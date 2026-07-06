from datetime import UTC, datetime
from decimal import Decimal

from stratus.collectors.open_meteo_previous_runs import parse_previous_runs
from stratus.db.models import WeatherKind


def test_previous_runs_aggregate_fixed_lead_by_local_day() -> None:
    payload = {
        "hourly": {
            "time": ["2026-07-01T00:00", "2026-07-01T12:00"],
            "temperature_2m_previous_day1": [10.0, 20.0],
            "temperature_2m_previous_day2": [9.0, 19.0],
        }
    }
    values = parse_previous_runs(
        payload,
        station_id=1,
        model="gfs",
        horizon_days=(1, 2),
        fetched_at=datetime(2026, 7, 6, tzinfo=UTC),
    )
    assert len(values) == 4
    assert {(value.horizon_h, value.kind, value.temp_c) for value in values} == {
        (24, WeatherKind.HIGH, Decimal("20.0")),
        (24, WeatherKind.LOW, Decimal("10.0")),
        (48, WeatherKind.HIGH, Decimal("19.0")),
        (48, WeatherKind.LOW, Decimal("9.0")),
    }

