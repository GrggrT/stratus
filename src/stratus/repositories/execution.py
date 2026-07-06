from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionMode,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    TradeSide,
)
from stratus.db.session import dml_rowcount


@dataclass(frozen=True, slots=True)
class PaperExecutionValue:
    signal_id: int
    market_id: int
    side: TradeSide
    requested_usd: Decimal
    filled_usd: Decimal
    avg_price: Decimal
    size_shares: Decimal
    fee_usd: Decimal
    total_cost_usd: Decimal
    filled_at: datetime


async def expire_pending_signals(session: AsyncSession, signal_ids: list[int]) -> int:
    if not signal_ids:
        return 0
    result = await session.execute(
        update(Signal)
        .where(Signal.id.in_(signal_ids), Signal.status == SignalStatus.PENDING)
        .values(status=SignalStatus.EXPIRED, skip_reason="paper_execution_timeout")
    )
    return dml_rowcount(result)


async def persist_paper_executions(
    session: AsyncSession, values: list[PaperExecutionValue]
) -> int:
    written = 0
    for value in values:
        signal = await session.scalar(
            select(Signal)
            .where(Signal.id == value.signal_id)
            .with_for_update(skip_locked=True)
        )
        if signal is None or signal.status is not SignalStatus.PENDING:
            continue
        order_status = (
            "filled"
            if value.total_cost_usd >= value.requested_usd - Decimal("0.0001")
            else "partially_filled"
        )
        session.add(
            Order(
                signal_id=value.signal_id,
                mode=ExecutionMode.PAPER,
                clob_order_id=f"paper-{value.signal_id}",
                side=value.side,
                price=value.avg_price,
                size_usd=value.requested_usd,
                filled_usd=value.filled_usd,
                fee_usd=value.fee_usd,
                status=order_status,
                filled_at=value.filled_at,
            )
        )
        session.add(
            Position(
                market_id=value.market_id,
                mode=ExecutionMode.PAPER,
                side=value.side,
                avg_price=value.avg_price,
                size_shares=value.size_shares,
                size_usd=value.total_cost_usd,
                fee_usd=value.fee_usd,
                status=PositionStatus.OPEN,
            )
        )
        signal.status = SignalStatus.EXECUTED
        written += 1
    await session.flush()
    return written
