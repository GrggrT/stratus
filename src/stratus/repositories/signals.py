from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import Order, Signal, SignalConfidence, SignalStatus, TradeSide
from stratus.db.session import dml_rowcount


@dataclass(frozen=True, slots=True)
class SignalValue:
    market_id: int
    ts: datetime
    side: TradeSide
    model_prob: Decimal
    market_price: Decimal
    edge: Decimal
    kelly_frac: Decimal
    size_usd: Decimal
    confidence: SignalConfidence
    status: SignalStatus
    skip_reason: str | None


async def insert_signals(session: AsyncSession, values: list[SignalValue]) -> int:
    if not values:
        return 0
    statement = insert(Signal).values([asdict(value) for value in values])
    statement = statement.on_conflict_do_nothing(constraint="uq_signals_market_time")
    result = await session.execute(statement)
    return dml_rowcount(result)


async def prune_signals(session: AsyncSession, *, older_than: datetime) -> int:
    """Delete old skipped/expired signals that never produced an order.

    Signal generation writes roughly one (mostly SKIPPED) row per market per
    minute; without retention the table grows unbounded. Executed signals and
    anything referenced by an order are never touched.
    """
    has_order = exists(select(Order.id).where(Order.signal_id == Signal.id))
    result = await session.execute(
        delete(Signal).where(
            Signal.status.in_([SignalStatus.SKIPPED, SignalStatus.EXPIRED]),
            Signal.ts < older_than,
            ~has_order,
        )
    )
    return dml_rowcount(result)

