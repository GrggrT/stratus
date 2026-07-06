from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    ExecutionMode,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
)
from stratus.db.session import dml_rowcount
from stratus.repositories.runtime_control import set_kill_switch
from stratus.repositories.telegram import enqueue_notification
from stratus.services.risk import LossLimitStatus, assess_loss_limits, period_starts


async def _starting_bankroll(
    session: AsyncSession,
    mode: ExecutionMode,
    period_start: datetime,
    initial_bankroll_usd: float,
) -> float:
    balance = await session.scalar(
        select(BankrollLog.balance_usd)
        .where(BankrollLog.mode == mode, BankrollLog.ts < period_start)
        .order_by(BankrollLog.ts.desc())
        .limit(1)
    )
    return float(balance) if balance is not None else initial_bankroll_usd


async def get_loss_limit_status(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
    initial_bankroll_usd: float,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> LossLimitStatus:
    now = now or datetime.now(UTC)
    day_start, week_start = period_starts(now, timezone)
    daily_start_bankroll = await _starting_bankroll(
        session, mode, day_start, initial_bankroll_usd
    )
    weekly_start_bankroll = await _starting_bankroll(
        session, mode, week_start, initial_bankroll_usd
    )
    rows = (
        await session.execute(
            select(Position.closed_at, Position.pnl_usd).where(
                Position.mode == mode,
                Position.status == PositionStatus.CLOSED,
                Position.closed_at >= week_start,
            )
        )
    ).all()
    realized = [
        (closed_at, float(pnl or 0))
        for closed_at, pnl in rows
        if closed_at is not None
    ]
    return assess_loss_limits(
        realized,
        now=now,
        daily_start_bankroll=daily_start_bankroll,
        weekly_start_bankroll=weekly_start_bankroll,
        timezone=timezone,
    )


async def enforce_loss_limits(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
    initial_bankroll_usd: float,
    timezone: str = "UTC",
    now: datetime | None = None,
) -> tuple[LossLimitStatus, int]:
    status = await get_loss_limit_status(
        session,
        mode=mode,
        initial_bankroll_usd=initial_bankroll_usd,
        timezone=timezone,
        now=now,
    )
    expired = 0
    if status.weekly_breached:
        expired = await set_kill_switch(
            session, True, expiration_reason="weekly_loss_limit"
        )
        await enqueue_notification(
            session,
            f"risk:weekly:{mode.value}:{status.week_start.date()}",
            f"STRATUS WEEKLY LOSS LIMIT — trading stopped\n"
            f"P&L ${status.weekly_pnl:+.2f}; "
            f"limit ${-0.12 * status.weekly_start_bankroll:.2f}",
        )
    elif status.daily_breached:
        result = await session.execute(
            update(Signal)
            .where(Signal.status == SignalStatus.PENDING)
            .values(status=SignalStatus.EXPIRED, skip_reason="daily_loss_limit")
        )
        expired = dml_rowcount(result)
        await enqueue_notification(
            session,
            f"risk:daily:{mode.value}:{status.day_start.date()}",
            f"STRATUS daily loss limit — new trades blocked until 00:00 UTC\n"
            f"P&L ${status.daily_pnl:+.2f}; "
            f"limit ${-0.06 * status.daily_start_bankroll:.2f}",
        )
    return status, expired
