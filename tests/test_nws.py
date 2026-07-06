from datetime import UTC, datetime, timedelta

import pytest

from stratus.collectors.nws import NwsForecastError, parse_nws_hourly_forecast
from stratus.collectors.open_meteo import StationTarget
from stratus.db.models import WeatherKind

STATION = StationTarget(1, "KLGA", 40.7769, -73.874, "America/New_York")


def _payload(hours: int = 48) -> dict:
    start = datetime(2026, 7, 7, tzinfo=UTC) + timedelta(hours=4)
    periods = []
    for index in range(hours):
        period_start = start + timedelta(hours=index)
        periods.append(
            {
                "startTime": period_start.isoformat(),
                "endTime": (period_start + timedelta(hours=1)).isoformat(),
                "temperature": 68 + index % 10,
                "temperatureUnit": "F",
            }
        )
    return {
        "properties": {
            "updateTime": "2026-07-06T18:35:26+00:00",
            "periods": periods,
        }
    }


def test_parse_nws_hourly_forecast_keeps_only_complete_local_days() -> None:
    values = parse_nws_hourly_forecast(
        _payload(),
        station=STATION,
        fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC),
    )

    assert len(values) == 4
    assert {value.valid_date.isoformat() for value in values} == {
        "2026-07-07",
        "2026-07-08",
    }
    high = next(
        value
        for value in values
        if value.valid_date.isoformat() == "2026-07-07" and value.kind is WeatherKind.HIGH
    )
    assert float(high.temp_c) == pytest.approx(25.0)
    assert high.model == "nws"
    assert high.run_time == datetime(2026, 7, 6, 18, 35, 26, tzinfo=UTC)


def test_parse_nws_hourly_forecast_rejects_partial_only_payload() -> None:
    with pytest.raises(NwsForecastError, match="fully covered"):
        parse_nws_hourly_forecast(
            _payload(hours=12),
            station=STATION,
            fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC),
        )
