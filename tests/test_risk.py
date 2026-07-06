from datetime import UTC, datetime, timedelta

import pytest

from stratus.services.risk import assess_loss_limits


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
