from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    ExecutionMode,
    Market,
    MarketPrice,
    MarketStatus,
    Position,
    PositionStatus,
)
from stratus.services.settlement import closing_line_value, position_pnl


async def settle_paper_markets(
    session: AsyncSession,
    *,
    resolutions: dict[int, bool],
    closing_times: dict[int, datetime],
    settled_at: datetime,
    initial_bankroll_usd: Decimal,
) -> tuple[int, int, Decimal]:
    if not resolutions:
        return 0, 0, Decimal(0)
    markets = (
        await session.execute(
            select(Market)
            .where(Market.id.in_(resolutions))
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    newly_resolved = []
    for market in markets:
        if market.status is MarketStatus.RESOLVED:
            continue
        market.status = MarketStatus.RESOLVED
        market.resolved_outcome = resolutions[market.id]
        newly_resolved.append(market.id)
    if not newly_resolved:
        return 0, 0, Decimal(0)

    positions = (
        await session.execute(
            select(Position)
            .where(
                Position.market_id.in_(newly_resolved),
                Position.mode == ExecutionMode.PAPER,
                Position.status == PositionStatus.OPEN,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    total_pnl = Decimal(0)
    for position in positions:
        pnl = position_pnl(
            side=position.side,
            resolved_yes=resolutions[position.market_id],
            size_shares=position.size_shares,
            cost_usd=position.size_usd,
        )
        position.pnl_usd = pnl
        closed_at = closing_times.get(position.market_id, settled_at)
        clv_target = closed_at - timedelta(minutes=15)
        closing_price = await session.scalar(
            select(MarketPrice)
            .where(
                MarketPrice.market_id == position.market_id,
                MarketPrice.ts <= clv_target,
                MarketPrice.ts >= clv_target - timedelta(minutes=2),
            )
            .order_by(MarketPrice.ts.desc())
            .limit(1)
        )
        if position.clv is None and closing_price is not None:
            position.clv = closing_line_value(
                side=position.side,
                yes_midpoint=closing_price.midpoint,
                entry_price=position.avg_price,
            )
        position.status = PositionStatus.CLOSED
        position.closed_at = closed_at
        total_pnl += pnl

    if positions:
        latest_balance = await session.scalar(
            select(BankrollLog.balance_usd)
            .where(BankrollLog.mode == ExecutionMode.PAPER)
            .order_by(BankrollLog.ts.desc())
            .limit(1)
        )
        balance = Decimal(latest_balance or initial_bankroll_usd) + total_pnl
        session.add(
            BankrollLog(
                ts=settled_at,
                mode=ExecutionMode.PAPER,
                balance_usd=balance.quantize(Decimal("0.0001")),
                event=f"settled {len(positions)} paper position(s)",
            )
        )
    await session.flush()
    return len(newly_resolved), len(positions), total_pnl.quantize(Decimal("0.0001"))
