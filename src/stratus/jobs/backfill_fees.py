from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import ExecutionMode, Market, Order, Position, Signal
from stratus.db.session import session_factory_scope
from stratus.services.execution import taker_fee

logger = logging.getLogger(__name__)


async def backfill_fees(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            order_rows = (
                await session.execute(
                    select(Order, Market)
                    .join(Signal, Order.signal_id == Signal.id)
                    .join(Market, Signal.market_id == Market.id)
                    .where(
                        Order.mode == ExecutionMode.PAPER,
                        Order.fee_usd.is_(None),
                    )
                    .with_for_update(of=Order, skip_locked=True)
                )
            ).all()
            for order, market in order_rows:
                rate = market.fee_rate if market.fees_enabled else Decimal(0)
                shares = order.filled_usd / order.price
                raw_fee = taker_fee(shares, order.price, rate)
                total = order.filled_usd + raw_fee
                scale = min(Decimal(1), order.size_usd / total) if total else Decimal(1)
                order.filled_usd = (order.filled_usd * scale).quantize(
                    Decimal("0.0001")
                )
                order.fee_usd = (raw_fee * scale).quantize(Decimal("0.00001"))

            position_rows = (
                await session.execute(
                    select(Position, Market)
                    .join(Market, Position.market_id == Market.id)
                    .where(
                        Position.mode == ExecutionMode.PAPER,
                        Position.fee_usd.is_(None),
                    )
                    .with_for_update(of=Position, skip_locked=True)
                )
            ).all()
            for position, market in position_rows:
                rate = market.fee_rate if market.fees_enabled else Decimal(0)
                raw_fee = taker_fee(
                    position.size_shares, position.avg_price, rate
                )
                total = position.size_usd + raw_fee
                scale = min(Decimal(1), position.size_usd / total) if total else Decimal(1)
                position.size_shares = (position.size_shares * scale).quantize(
                    Decimal("0.000001")
                )
                position.fee_usd = (raw_fee * scale).quantize(Decimal("0.00001"))
        logger.info(
            "paper fee backfill complete: orders=%d positions=%d",
            len(order_rows),
            len(position_rows),
        )
        return len(order_rows), len(position_rows)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(backfill_fees(settings))


if __name__ == "__main__":
    main()
