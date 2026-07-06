from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from stratus.collectors.open_meteo import (
    OpenMeteoClient,
    OpenMeteoError,
    StationTarget,
    parse_daily_extremes,
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
