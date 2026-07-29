from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI
from starlette.requests import Request

from stratus import operator_api
from stratus.api import create_app
from stratus.config import Settings
from stratus.operator_api import (
    EvidenceContinuityResponse,
    EvidenceContinuityResponseCache,
    EvidenceReleaseActivationRequest,
)


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeSessions:
    def __call__(self) -> _FakeSessionContext:
        return _FakeSessionContext()

    def begin(self) -> _FakeSessionContext:
        return _FakeSessionContext()


def _request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def _response(generated_at: datetime) -> EvidenceContinuityResponse:
    return cast(
        EvidenceContinuityResponse,
        SimpleNamespace(generated_at=generated_at),
    )


async def test_evidence_continuity_read_gate_is_non_authoritative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ReadStopped(RuntimeError):
        pass

    authoritative_values: list[bool] = []

    async def fake_gate(
        _session: object,
        _settings: Settings,
        *,
        authoritative: bool = True,
    ) -> None:
        authoritative_values.append(authoritative)
        raise ReadStopped

    monkeypatch.setattr(operator_api, "probability_release_gate", fake_gate)
    app = create_app(Settings())
    app.state.sessions = _FakeSessions()

    with pytest.raises(ReadStopped):
        await operator_api._uncached_evidence_continuity_response(_request(app))

    assert authoritative_values == [False]


async def test_response_cache_coalesces_concurrent_reads_and_preserves_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    first_at = datetime(2026, 7, 15, 12, tzinfo=UTC)
    second_at = datetime(2026, 7, 15, 12, 1, tzinfo=UTC)
    responses = (_response(first_at), _response(second_at))
    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    cache = EvidenceContinuityResponseCache(
        ttl_seconds=30.0,
        clock=lambda: now[0],
    )
    app = create_app(Settings())
    app.state.evidence_continuity_response_cache = cache
    request = _request(app)

    async def factory(_request: Request) -> EvidenceContinuityResponse:
        nonlocal calls
        index = calls
        calls += 1
        entered.set()
        await release.wait()
        return responses[index]

    monkeypatch.setattr(
        operator_api,
        "_uncached_evidence_continuity_response",
        factory,
    )
    tasks = [
        asyncio.create_task(operator_api.evidence_continuity(request))
        for _ in range(12)
    ]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(result is responses[0] for result in results)
    assert all(result.generated_at == first_at for result in results)

    now[0] = 129.9
    cached = await operator_api.evidence_continuity(request)
    assert cached is responses[0]
    assert cached.generated_at == first_at
    assert calls == 1

    now[0] = 130.1
    refreshed = await operator_api.evidence_continuity(request)
    assert refreshed is responses[1]
    assert refreshed.generated_at == second_at
    assert calls == 2


async def test_successful_activation_invalidates_the_app_response_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(Settings(release_image_digest="1" * 64))
    app.state.sessions = _FakeSessions()
    request = _request(app)
    cache = cast(
        EvidenceContinuityResponseCache,
        app.state.evidence_continuity_response_cache,
    )
    old_response = _response(datetime(2026, 7, 15, 12, tzinfo=UTC))
    new_response = _response(datetime(2026, 7, 15, 12, 1, tzinfo=UTC))

    async def old_factory() -> EvidenceContinuityResponse:
        return old_response

    assert await cache.get_or_create(old_factory) is old_response

    candidate = SimpleNamespace(manifest_hash="candidate-manifest")
    plan = object()
    epoch = SimpleNamespace(
        id=1,
        opened_at=datetime(2026, 7, 15, 12, 0, 30, tzinfo=UTC),
        pipeline_version="stratus-accuracy-v4-20260713",
        material_fingerprint="a" * 16,
        activation_manifest_hash="b" * 64,
        cohort="stratus-accuracy-v4-20260713+" + "a" * 16 + "+promotions-" + "b" * 16,
        image_digest="1" * 64,
        plan_id=1,
        previous_epoch_id=None,
        approval_reason="Activate the reviewed release manifest now",
        plan_hash="c" * 64,
        record_hash="d" * 64,
    )

    async def fake_release_plan(*_args: object, **_kwargs: object) -> Any:
        return plan, None, None, candidate

    async def fake_append(*_args: object, **_kwargs: object) -> Any:
        return epoch

    monkeypatch.setattr(operator_api, "_evidence_release_plan", fake_release_plan)
    monkeypatch.setattr(
        operator_api,
        "append_probability_pipeline_release_epoch",
        fake_append,
    )
    payload = EvidenceReleaseActivationRequest(
        reviewed_by="cache-test",
        approval_reason="Activate the reviewed release manifest now",
        expected_plan_hash="c" * 64,
    )

    result = await operator_api.activate_evidence_continuity_release(request, payload)
    assert result.activated.id == 1

    refresh_calls = 0

    async def new_factory(_request: Request) -> EvidenceContinuityResponse:
        nonlocal refresh_calls
        refresh_calls += 1
        return new_response

    monkeypatch.setattr(
        operator_api,
        "_uncached_evidence_continuity_response",
        new_factory,
    )
    assert await operator_api.evidence_continuity(request) is new_response
    assert refresh_calls == 1
