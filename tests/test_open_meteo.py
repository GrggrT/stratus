import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256

import httpx
import pytest

from stratus.collectors.open_meteo import (
    OpenMeteoClient,
    OpenMeteoError,
    StationTarget,
    parse_daily_extremes,
    parse_daily_trajectories,
)
from stratus.db.models import WeatherKind

RUN_TIME = datetime(2026, 7, 5, 18, tzinfo=UTC)
FETCHED_AT = datetime(2026, 7, 5, 18, 3, tzinfo=UTC)


def test_parse_daily_extremes_uses_local_calendar_dates() -> None:
    payload = {
        "hourly": {
            "time": [
                "2026-07-05T00:00",
                "2026-07-05T12:00",
                "2026-07-05T23:00",
                "2026-07-06T00:00",
                "2026-07-06T12:00",
            ],
            "temperature_2m": [14.1, 27.4, 18.3, 16.0, 25.2],
        }
    }

    values = parse_daily_extremes(
        payload,
        station_id=7,
        model="ecmwf",
        run_time=RUN_TIME,
        fetched_at=FETCHED_AT,
    )

    assert [(value.kind, value.temp_c) for value in values] == [
        (WeatherKind.HIGH, Decimal("27.4")),
        (WeatherKind.LOW, Decimal("14.1")),
        (WeatherKind.HIGH, Decimal("25.2")),
        (WeatherKind.LOW, Decimal("16.0")),
    ]


def test_parse_daily_extremes_skips_model_horizon_null_tail() -> None:
    payload = {
        "hourly": {
            "time": [
                "2026-07-05T00:00",
                "2026-07-05T12:00",
                "2026-07-06T00:00",
                "2026-07-06T12:00",
            ],
            "temperature_2m": [10.0, 20.0, 11.0, None],
        }
    }

    values = parse_daily_extremes(
        payload,
        station_id=7,
        model="icon",
        run_time=RUN_TIME,
        fetched_at=FETCHED_AT,
    )

    assert len(values) == 2
    assert {value.valid_date.isoformat() for value in values} == {"2026-07-05"}


def test_parse_daily_trajectories_keeps_ordered_complete_local_day() -> None:
    times = [f"2026-07-05T{hour:02d}:00" for hour in range(24)]
    payload = {
        "hourly": {
            "time": times + ["2026-07-06T00:00"],
            "temperature_2m": [10 + hour / 2 for hour in range(24)] + [None],
        }
    }

    values = parse_daily_trajectories(
        payload,
        station_id=7,
        model="ecmwf",
        run_time=RUN_TIME,
        fetched_at=FETCHED_AT,
    )

    assert len(values) == 1
    assert values[0].valid_date.isoformat() == "2026-07-05"
    assert len(values[0].points) == 24
    assert values[0].points[0] == ("2026-07-05T00:00", Decimal("10.0"))
    assert values[0].points[-1] == ("2026-07-05T23:00", Decimal("21.5"))


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"hourly": {"time": [], "temperature_2m": []}},
        {"hourly": {"time": ["2026-07-05T00:00"], "temperature_2m": []}},
        {"hourly": {"time": ["2026-07-05T00:00"], "temperature_2m": [None]}},
    ],
)
def test_parse_daily_extremes_rejects_incomplete_payload(payload: dict) -> None:
    with pytest.raises(OpenMeteoError):
        parse_daily_extremes(
            payload,
            station_id=7,
            model="ecmwf",
            run_time=RUN_TIME,
            fetched_at=FETCHED_AT,
        )


async def test_client_sends_station_timezone_and_native_model_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["models"] == "ecmwf_ifs025"
        assert request.url.params["timezone"] == "Europe/London"
        assert request.url.params["hourly"] == "temperature_2m"
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T00:00", "2026-07-05T12:00"],
                    "temperature_2m": [10.0, 20.0],
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    station = StationTarget(id=1, icao="EGLL", lat=51.47, lon=-0.4543, tz="Europe/London")
    async with http_client:
        async with OpenMeteoClient(
            base_url="https://example.test/v1/forecast",
            forecast_days=10,
            concurrency=1,
            http_client=http_client,
        ) as client:
            values = await client.fetch(
                station, "ecmwf", run_time=RUN_TIME, fetched_at=FETCHED_AT
            )

    assert len(values) == 2


async def test_single_runs_request_includes_exact_initialization() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["run"] == "2026-07-05T18:00"
        assert request.url.params["models"] == "ncep_hrrr_conus"
        return httpx.Response(
            200,
            json={
                "hourly": {
                    "time": ["2026-07-05T00:00", "2026-07-05T12:00"],
                    "temperature_2m": [20.0, 30.0],
                }
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    station = StationTarget(id=1, icao="KLGA", lat=40.77, lon=-73.87, tz="America/New_York")
    async with http_client:
        async with OpenMeteoClient(
            base_url="https://single-runs-api.open-meteo.com/v1/forecast",
            forecast_days=10,
            concurrency=1,
            http_client=http_client,
        ) as client:
            values = await client.fetch(
                station,
                "hrrr",
                run_time=RUN_TIME,
                fetched_at=FETCHED_AT,
                exact_run=True,
            )

    assert len(values) == 2


async def test_fetch_bundle_preserves_exact_http_response_evidence() -> None:
    times = [f"2026-07-05T{hour:02d}:00" for hour in range(24)]
    raw_body = json.dumps(
        {
            "hourly": {
                "time": times,
                "temperature_2m": list(range(24)),
            }
        },
        separators=(",", ":"),
    ).encode()
    requested_url = ""

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_url
        requested_url = str(request.url)
        return httpx.Response(
            200,
            content=raw_body,
            headers={
                "Date": "Sun, 05 Jul 2026 18:03:05 GMT",
                "ETag": '"forecast-v7"',
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    station = StationTarget(id=1, icao="EGLL", lat=51.47, lon=-0.4543, tz="Europe/London")
    async with http_client:
        async with OpenMeteoClient(
            base_url="https://example.test/v1/forecast",
            forecast_days=10,
            concurrency=1,
            http_client=http_client,
        ) as client:
            bundle = await client.fetch_bundle(
                station, "ecmwf", run_time=RUN_TIME, fetched_at=FETCHED_AT
            )

    assert len(bundle.forecasts) == 2
    assert len(bundle.trajectories) == 1
    assert len(bundle.evidence) == 1
    evidence = bundle.evidence[0]
    assert evidence.request_method == "GET"
    assert evidence.source_url == requested_url
    assert evidence.response_body == raw_body
    assert evidence.response_sha256 == sha256(raw_body).hexdigest()
    assert evidence.response_bytes == len(raw_body)
    assert evidence.http_date == datetime(2026, 7, 5, 18, 3, 5, tzinfo=UTC)
    assert evidence.etag == '"forecast-v7"'
    assert evidence.request_started_at.tzinfo is UTC
    assert evidence.response_received_at.tzinfo is UTC
    assert evidence.request_started_at <= evidence.response_received_at
