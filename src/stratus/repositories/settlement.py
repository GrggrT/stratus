from __future__ import annotations

from dataclasses import dataclass
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
    Station,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.services.settlement import closing_line_value, position_pnl


@dataclass(frozen=True, slots=True)
class SettlementResult:
    resolved: int
    paper_positions: int
    live_positions: int
    paper_pnl: Decimal
    live_pnl: Decimal


async def _close_position(
    session: AsyncSession,
    position: Position,
    *,
    resolved_yes: bool,
    closed_at: datetime,
) -> Decimal:
    pnl = position_pnl(
        side=position.side,
        resolved_yes=resolved_yes,
        size_shares=position.size_shares,
        cost_usd=position.size_usd,
    )
    position.pnl_usd = pnl
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
    return pnl


async def settle_resolved_markets(
    session: AsyncSession,
    *,
    resolutions: dict[int, bool],
    closing_times: dict[int, datetime],
    settled_at: datetime,
    initial_bankroll_usd: Decimal,
) -> SettlementResult:
    """Resolve markets and close their open positions in both modes.

    Paper settlement synthesises a bankroll balance from realised PnL. Live
    settlement records PnL only: the real USDC balance does not change until
    the operator redeems winning tokens on-chain, so a redemption reminder is
    enqueued instead of a synthetic balance.
    """
    if not resolutions:
        return SettlementResult(0, 0, 0, Decimal(0), Decimal(0))
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
        return SettlementResult(0, 0, 0, Decimal(0), Decimal(0))

    positions = (
        await session.execute(
            select(Position)
            .where(
                Position.market_id.in_(newly_resolved),
                Position.status == PositionStatus.OPEN,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    paper_pnl = Decimal(0)
    live_pnl = Decimal(0)
    paper_count = 0
    live_count = 0
    live_winners: list[Position] = []
    for position in positions:
        resolved_yes = resolutions[position.market_id]
        closed_at = closing_times.get(position.market_id, settled_at)
        pnl = await _close_position(
            session, position, resolved_yes=resolved_yes, closed_at=closed_at
        )
        if position.mode is ExecutionMode.LIVE:
            live_pnl += pnl
            live_count += 1
            won = (position.side.value == "buy_yes") == resolved_yes
            if won and position.size_shares > 0:
                live_winners.append(position)
        else:
            paper_pnl += pnl
            paper_count += 1

    if paper_count:
        latest = await session.scalar(
            select(BankrollLog.balance_usd)
            .where(BankrollLog.mode == ExecutionMode.PAPER)
            .order_by(BankrollLog.ts.desc())
            .limit(1)
        )
        balance = Decimal(latest or initial_bankroll_usd) + paper_pnl
        session.add(
            BankrollLog(
                ts=settled_at,
                mode=ExecutionMode.PAPER,
                balance_usd=balance.quantize(Decimal("0.0001")),
                event=f"settled {paper_count} paper position(s)",
            )
        )

    if live_winners:
        await _enqueue_redemption_reminder(session, live_winners, settled_at)

    await session.flush()
    return SettlementResult(
        resolved=len(newly_resolved),
        paper_positions=paper_count,
        live_positions=live_count,
        paper_pnl=paper_pnl.quantize(Decimal("0.0001")),
        live_pnl=live_pnl.quantize(Decimal("0.0001")),
    )


async def _enqueue_redemption_reminder(
    session: AsyncSession, winners: list[Position], settled_at: datetime
) -> None:
    market_ids = {position.market_id for position in winners}
    rows = (
        await session.execute(
            select(Market.id, Station.icao)
            .join(Station, Station.id == Market.station_id)
            .where(Market.id.in_(market_ids))
        )
    ).all()
    stations: dict[int, str] = {market_id: icao for market_id, icao in rows}
    lines = [
        f"{stations.get(position.market_id, position.market_id)}: "
        f"{position.size_shares:.2f} winning shares"
        for position in winners
    ]
    await enqueue_notification(
        session,
        f"live-redeem:{settled_at:%Y%m%dT%H%M}",
        "ACTION REQUIRED: redeem winning Polymarket positions manually via the "
        "UI. STRATUS does not redeem on-chain.\n" + "\n".join(lines),
    )
