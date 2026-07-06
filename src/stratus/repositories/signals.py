from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import Signal, SignalConfidence, SignalStatus, TradeSide


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
    return result.rowcount or 0

