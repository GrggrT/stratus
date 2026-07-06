from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


def period_starts(now: datetime, timezone: str = "UTC") -> tuple[datetime, datetime]:
    """UTC instants where the accounting day and ISO week begin in `timezone`.

    The accounting timezone decides when the daily loss limit resets; with the
    default UTC a bad New York evening splits across two "days". Set it to the
    venue timezone (America/New_York) to align limits with market days.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    local_now = now.astimezone(ZoneInfo(timezone))
    local_day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_week_start = local_day_start - timedelta(days=local_day_start.weekday())
    return local_day_start.astimezone(UTC), local_week_start.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class LossLimitStatus:
    day_start: datetime
    week_start: datetime
    daily_pnl: float
    weekly_pnl: float
    daily_start_bankroll: float
    weekly_start_bankroll: float
    daily_breached: bool
    weekly_breached: bool


def assess_loss_limits(
    realized: list[tuple[datetime, float]],
    *,
    now: datetime,
    daily_start_bankroll: float,
    weekly_start_bankroll: float,
    timezone: str = "UTC",
) -> LossLimitStatus:
    if daily_start_bankroll <= 0 or weekly_start_bankroll <= 0:
        raise ValueError("period starting bankrolls must be positive")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    day_start, week_start = period_starts(now, timezone)
    daily_pnl = sum(pnl for closed_at, pnl in realized if closed_at >= day_start)
    weekly_pnl = sum(pnl for closed_at, pnl in realized if closed_at >= week_start)
    return LossLimitStatus(
        day_start=day_start,
        week_start=week_start,
        daily_pnl=daily_pnl,
        weekly_pnl=weekly_pnl,
        daily_start_bankroll=daily_start_bankroll,
        weekly_start_bankroll=weekly_start_bankroll,
        daily_breached=daily_pnl <= -0.06 * daily_start_bankroll,
        weekly_breached=weekly_pnl <= -0.12 * weekly_start_bankroll,
    )
