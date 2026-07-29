from datetime import UTC, datetime
from hashlib import sha256

import httpx
import pytest

from stratus.collectors.open_meteo import OpenMeteoError, StationTarget
from stratus.collectors.open_meteo_ensemble import (
    ENSEMBLE_MODELS,
    EnsembleModel,
    OpenMeteoEnsembleClient,
    parse_ensemble_daily_extremes,
)

RUN_TIME = datetime(2026, 7, 5, 18, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 5, 18, 3, tzinfo=UTC)


def test_parse_members_normalizes_control_to_m01(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ENSEMBLE_MODELS,
        "test_ens",
        EnsembleModel(api_id="test", member_prefix="test", member_count=3),
    )
    payload = {
        "hourly": {
            "time": ["2026-07-05T00:00", "2026-07-05T12:00"],
            "temperature_2m_member02": [12, 22],
            "temperature_2m": [10, 20],
            "temperature_2m_member01": [11, 21],
        }
    }
    values = parse_ensemble_daily_extremes(
        payload,
        station_id=1,
        model="test_ens",
        run_time=RUN_TIME,
        fetched_at=FETCHED_AT,
    )
    assert {value.model for value in values} == {"test_m01", "test_m02", "test_m03"}
    assert len(values) == 6


def test_member_count_change_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ENSEMBLE_MODELS,
        "test_ens",
        EnsembleModel(api_id="test", member_prefix="test", member_count=3),
    )
    with pytest.raises(OpenMeteoError, match="returned 1 members; expected 3"):
        parse_ensemble_daily_extremes(
            {
                "hourly": {
                    "time": ["2026-07-05T00:00"],
                    "temperature_2m": [10],
                }
            },
            station_id=1,
            model="test_ens",
            run_time=RUN_TIME,
            fetched_at=FETCHED_AT,
        )


async def test_client_uses_ensemble_model_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        ENSEMBLE_MODELS,
        "test_ens",
        EnsembleModel(api_id="upstream_test", member_prefix="test", member_count=1),
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["models"] == "upstream_test"
        assert request.url.params["timezone"] == "Europe/London"
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T00:00", "2026-07-05T12:00"],
                    "temperature_2m": [10, 20],
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    station = StationTarget(id=1, icao="EGLC", lat=51.505, lon=0.055, tz="Europe/London")
    async with http_client:
        async with OpenMeteoEnsembleClient(
            base_url="https://example.test/v1/ensemble",
            forecast_days=10,
            concurrency=1,
            http_client=http_client,
        ) as client:
            values = await client.fetch(
                station, "test_ens", run_time=RUN_TIME, fetched_at=FETCHED_AT
            )
    assert len(values) == 2


async def test_fetch_bundle_includes_exact_ensemble_response_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        ENSEMBLE_MODELS,
        "test_ens",
        EnsembleModel(api_id="upstream_test", member_prefix="test", member_count=1),
    )
    raw_body = (
        b'{"hourly":{"time":["2026-07-05T00:00","2026-07-05T12:00"],'
        b'"temperature_2m":[10,20]}}'
    )
    requested_url = ""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_url
        requested_url = str(request.url)
        return httpx.Response(200, content=raw_body, headers={"ETag": "ensemble-1"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    station = StationTarget(id=1, icao="EGLC", lat=51.505, lon=0.055, tz="Europe/London")
    async with http_client:
        async with OpenMeteoEnsembleClient(
            base_url="https://example.test/v1/ensemble",
            forecast_days=10,
            concurrency=1,
            http_client=http_client,
        ) as client:
            bundle = await client.fetch_bundle(
                station, "test_ens", run_time=RUN_TIME, fetched_at=FETCHED_AT
            )

    assert len(bundle.forecasts) == 2
    assert bundle.trajectories == ()
    assert len(bundle.evidence) == 1
    evidence = bundle.evidence[0]
    assert evidence.source_url == requested_url
    assert evidence.response_body == raw_body
    assert evidence.response_sha256 == sha256(raw_body).hexdigest()
    assert evidence.response_bytes == len(raw_body)
    assert evidence.http_date is None
    assert evidence.etag == "ensemble-1"
