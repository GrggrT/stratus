from __future__ import annotations

import asyncio
import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from stratus import operator_api
from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import AccuracyPrediction, ModelSkillReport
from stratus.operator_api import (
    AccuracyOverviewResponse,
    AccuracyOverviewResponseCache,
)


def _request(app: FastAPI) -> Request:
    return Request({"type": "http", "app": app})


def _response(generated_at: datetime) -> AccuracyOverviewResponse:
    return cast(
        AccuracyOverviewResponse,
        SimpleNamespace(generated_at=generated_at),
    )


async def test_accuracy_overview_cache_coalesces_and_expires_at_five_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    first = _response(datetime(2026, 7, 30, 12, tzinfo=UTC))
    second = _response(datetime(2026, 7, 30, 12, 5, tzinfo=UTC))
    responses = (first, second)
    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()
    app = create_app(Settings())
    app.state.accuracy_overview_response_cache = AccuracyOverviewResponseCache(
        clock=lambda: clock[0]
    )
    request = _request(app)

    async def factory(_request: Request) -> AccuracyOverviewResponse:
        nonlocal calls
        response = responses[calls]
        calls += 1
        entered.set()
        await release.wait()
        return response

    monkeypatch.setattr(
        operator_api,
        "_uncached_accuracy_overview_response",
        factory,
    )
    tasks = [asyncio.create_task(operator_api.accuracy_overview(request)) for _ in range(12)]
    await entered.wait()
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(*tasks)

    assert calls == 1
    assert all(result is first for result in results)

    clock[0] = 399.9
    assert await operator_api.accuracy_overview(request) is first
    assert calls == 1

    clock[0] = 400.1
    assert await operator_api.accuracy_overview(request) is second
    assert calls == 2


async def test_accuracy_overview_timeout_is_single_flight_and_backed_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [100.0]
    calls = 0
    app = create_app(Settings())
    app.state.accuracy_overview_response_cache = AccuracyOverviewResponseCache(
        clock=lambda: clock[0],
        failure_backoff_seconds=5.0,
    )
    request = _request(app)

    async def slow_factory(_request: Request) -> AccuracyOverviewResponse:
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        raise AssertionError("timeout must cancel the cold computation")

    monkeypatch.setattr(
        operator_api,
        "_uncached_accuracy_overview_response",
        slow_factory,
    )
    monkeypatch.setattr(
        operator_api,
        "_ACCURACY_OVERVIEW_TIMEOUT_SECONDS",
        0.001,
    )

    failures = await asyncio.gather(
        *(operator_api.accuracy_overview(request) for _ in range(12)),
        return_exceptions=True,
    )

    assert calls == 1
    assert all(
        isinstance(error, HTTPException) and error.status_code == 503
        for error in failures
    )

    with pytest.raises(HTTPException) as backoff_error:
        await operator_api.accuracy_overview(request)
    assert backoff_error.value.status_code == 503
    assert calls == 1

    clock[0] = 105.1
    with pytest.raises(HTTPException) as retry_error:
        await operator_api.accuracy_overview(request)
    assert retry_error.value.status_code == 503
    assert calls == 2


async def test_accuracy_overview_follower_cancellation_does_not_cancel_shared_task() -> None:
    cache = AccuracyOverviewResponseCache()
    response = _response(datetime(2026, 7, 30, 12, tzinfo=UTC))
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def factory() -> AccuracyOverviewResponse:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return response

    leader = asyncio.create_task(cache.get_or_create(factory))
    await entered.wait()
    follower = asyncio.create_task(cache.get_or_create(factory))
    await asyncio.sleep(0)

    follower.cancel()
    with pytest.raises(asyncio.CancelledError):
        await follower
    assert not leader.done()

    release.set()
    assert await leader is response
    assert calls == 1
    assert await cache.get_or_create(factory) is response
    assert calls == 1


async def test_latest_expert_batches_use_one_sql_round_trip() -> None:
    rows = [
        SimpleNamespace(expert="censored_gaussian"),
        SimpleNamespace(expert="regime_emos"),
    ]

    class ScalarRows:
        def all(self) -> list[Any]:
            return rows

    class Result:
        def scalars(self) -> ScalarRows:
            return ScalarRows()

    class Session:
        def __init__(self) -> None:
            self.statements: list[Any] = []

        async def execute(self, statement: Any) -> Result:
            self.statements.append(statement)
            return Result()

    session = Session()
    batches = await operator_api._latest_accuracy_prediction_batches(cast(Any, session))

    assert len(session.statements) == 1
    assert batches["censored_gaussian"] == [rows[0]]
    assert batches["regime_emos"] == [rows[1]]
    assert batches["qrf"] == []
    source = inspect.getsource(operator_api._latest_accuracy_prediction_batches)
    assert source.count("AccuracyPrediction.evidence_eligible.is_(True)") == 2


def test_accuracy_overview_cold_path_keeps_expensive_aggregates_single_pass() -> None:
    source = inspect.getsource(operator_api._uncached_accuracy_overview_response)

    assert source.count("select(ModelSkillReport)") == 1
    assert source.count(".select_from(attested_evaluations)") == 1
    assert source.count("_latest_accuracy_prediction_batches(session)") == 1


def test_accuracy_overview_cache_rejects_nonpositive_ttl() -> None:
    with pytest.raises(ValueError, match="TTL must be positive"):
        AccuracyOverviewResponseCache(ttl_seconds=0)

    with pytest.raises(ValueError, match="failure backoff must be positive"):
        AccuracyOverviewResponseCache(failure_backoff_seconds=0)


def test_accuracy_overview_total_budget_fits_the_cockpit_http_budget() -> None:
    assert 0 < operator_api._ACCURACY_OVERVIEW_TIMEOUT_SECONDS < 3


def test_accuracy_overview_index_metadata_matches_migration() -> None:
    prediction_indexes = {index.name: index for index in AccuracyPrediction.__table__.indexes}
    skill_indexes = {index.name: index for index in ModelSkillReport.__table__.indexes}

    assert [
        column.name
        for column in prediction_indexes["ix_accuracy_predictions_eligible_expert_ts"].columns
    ] == ["expert", "ts"]
    assert [
        column.name
        for column in prediction_indexes[
            "ix_accuracy_predictions_eligible_expert_version_market_ts"
        ].columns
    ] == ["expert", "expert_version", "market_id", "ts"]
    assert [column.name for column in skill_indexes["ix_model_skill_window_evaluated"].columns] == [
        "window_days",
        "evaluated_at",
    ]
    assert all(
        str(prediction_indexes[name].dialect_options["postgresql"]["where"])
        == "evidence_eligible IS TRUE"
        for name in (
            "ix_accuracy_predictions_eligible_expert_ts",
            "ix_accuracy_predictions_eligible_expert_version_market_ts",
        )
    )
