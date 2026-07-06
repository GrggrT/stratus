from datetime import UTC, datetime, timedelta

import pytest

from stratus.services.risk import assess_loss_limits, period_starts


def test_period_starts_follow_the_accounting_timezone() -> None:
    # 01:00 UTC on Wed Jul 8 is still Tue Jul 7 evening in New York (EDT),
    # so the accounting day starts at Jul 7 00:00 EDT = Jul 7 04:00 UTC.
    now = datetime(2026, 7, 8, 1, tzinfo=UTC)

    utc_day, utc_week = period_starts(now, "UTC")
    ny_day, ny_week = period_starts(now, "America/New_York")

    assert utc_day == datetime(2026, 7, 8, tzinfo=UTC)
    assert utc_week == datetime(2026, 7, 6, tzinfo=UTC)
    assert ny_day == datetime(2026, 7, 7, 4, tzinfo=UTC)
    assert ny_week == datetime(2026, 7, 6, 4, tzinfo=UTC)


def test_loss_limits_respect_the_accounting_timezone() -> None:
    now = datetime(2026, 7, 8, 1, tzinfo=UTC)
    late_evening_loss = [(datetime(2026, 7, 8, 0, 30, tzinfo=UTC), -61.0)]

    utc_result = assess_loss_limits(
        late_evening_loss,
        now=now,
        daily_start_bankroll=1_000.0,
        weekly_start_bankroll=1_000.0,
    )
    ny_result = assess_loss_limits(
        late_evening_loss,
        now=now,
        daily_start_bankroll=1_000.0,
        weekly_start_bankroll=1_000.0,
        timezone="America/New_York",
    )

    assert utc_result.daily_breached is True
    assert ny_result.daily_breached is True
    assert ny_result.day_start < utc_result.day_start


def test_loss_limits_use_period_starting_bankrolls() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    result = assess_loss_limits(
        [
            (now - timedelta(days=2), -70.0),
            (now - timedelta(hours=2), -61.0),
        ],
        now=now,
        daily_start_bankroll=1_000.0,
        weekly_start_bankroll=1_100.0,
    )

    assert result.daily_pnl == pytest.approx(-61.0)
    assert result.weekly_pnl == pytest.approx(-131.0)
    assert result.daily_breached is True
    assert result.weekly_breached is False


def test_weekly_limit_trips_at_exact_boundary() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    result = assess_loss_limits(
        [(now, -120.0)],
        now=now,
        daily_start_bankroll=3_000.0,
        weekly_start_bankroll=1_000.0,
    )

    assert result.daily_breached is False
    assert result.weekly_breached is True


def test_loss_limits_require_aware_time_and_positive_bankroll() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_loss_limits(
            [],
            now=datetime(2026, 7, 8),
            daily_start_bankroll=1_000.0,
            weekly_start_bankroll=1_000.0,
        )
    with pytest.raises(ValueError, match="positive"):
        assess_loss_limits(
            [],
            now=datetime(2026, 7, 8, tzinfo=UTC),
            daily_start_bankroll=0,
            weekly_start_bankroll=1_000.0,
        )
