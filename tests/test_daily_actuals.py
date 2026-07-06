from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.services.daily_actuals import (
    IncompleteObservationDayError,
    ObservationPoint,
    compute_daily_extremes,
    utc_bounds_for_local_date,
)


def test_compute_complete_local_day() -> None:
    target_date = date(2026, 7, 4)
    start, _ = utc_bounds_for_local_date(target_date, "Europe/London")
    points = [
        ObservationPoint(
            obs_time=start + timedelta(minutes=30, hours=hour),
            temp_c=Decimal(hour),
        )
        for hour in range(24)
    ]
    result = compute_daily_extremes(
        points, target_date=target_date, timezone="Europe/London"
    )
    assert result.sample_n == 24
    assert result.low_c == Decimal(0)
    assert result.high_c == Decimal(23)


def test_local_date_bounds_respect_timezone() -> None:
    start, end = utc_bounds_for_local_date(date(2026, 7, 4), "America/New_York")
    assert start == datetime(2026, 7, 4, 4, tzinfo=UTC)
    assert end == datetime(2026, 7, 5, 4, tzinfo=UTC)


def test_incomplete_day_is_not_finalized() -> None:
    with pytest.raises(IncompleteObservationDayError, match="only 1 observations"):
        compute_daily_extremes(
            [
                ObservationPoint(
                    obs_time=datetime(2026, 7, 4, 12, tzinfo=UTC),
                    temp_c=Decimal("20"),
                )
            ],
            target_date=date(2026, 7, 4),
            timezone="UTC",
        )

