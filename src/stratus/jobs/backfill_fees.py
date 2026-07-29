from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionCostRuntime,
    ExecutionMode,
    ExecutionOrderIntent,
    Market,
    Order,
    Position,
    Signal,
)
from stratus.db.session import session_factory_scope
from stratus.services.execution import taker_fee

logger = logging.getLogger(__name__)

_FILLED_ORDER_STATUSES = ("filled", "partially_filled")


def _legacy_fee_backfill_orders_statement() -> Select[tuple[Order, Market]]:
    """Select only pre-ledger paper orders whose price represented execution.

    Modern orders retain the requested limit in ``Order.price`` and bind their
    actual fill facts to an ``ExecutionOrderIntent`` plus atomic legs. They
    must never be repaired using the old filled_usd / Order.price arithmetic.
    """

    opened_at = (
        select(ExecutionCostRuntime.opened_at)
        .where(ExecutionCostRuntime.id == 1)
        .scalar_subquery()
    )
    return (
        select(Order, Market)
        .join(Signal, Order.signal_id == Signal.id)
        .join(Market, Signal.market_id == Market.id)
        .outerjoin(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
        .where(
            Order.mode == ExecutionMode.PAPER,
            Order.fee_usd.is_(None),
            Order.filled_at.is_not(None),
            Order.filled_at < opened_at,
            ExecutionOrderIntent.id.is_(None),
        )
        .with_for_update(of=Order, skip_locked=True)
    )


def _legacy_fee_backfill_positions_statement() -> Select[tuple[Position, Market]]:
    """Select only paper aggregates with positive pre-ledger order evidence.

    Paper positions are aggregated by market/side/token and therefore cannot
    point at every contributing order.  Absence of ``Position.order_id`` is
    not proof that the row is legacy.  Admit a row only when a matching filled
    order predates the execution-cost boundary, and reject the whole aggregate
    if any matching order is post-boundary or has an immutable intent.
    """

    opened_at = (
        select(ExecutionCostRuntime.opened_at)
        .where(ExecutionCostRuntime.id == 1)
        .scalar_subquery()
    )

    def matching_orders() -> Select[tuple[int]]:
        return (
            select(Order.id)
            .join(Signal, Signal.id == Order.signal_id)
            .where(
                Order.mode == ExecutionMode.PAPER,
                Signal.market_id == Position.market_id,
                Order.side == Position.side,
                Order.token_id.is_not_distinct_from(Position.token_id),
                Order.status.in_(_FILLED_ORDER_STATUSES),
                Order.filled_usd > 0,
                Order.filled_at.is_not(None),
            )
            .correlate(Position)
        )

    # ``filled_at`` is the execution-protocol boundary fact. An order may be
    # created before a rollout and fill afterwards; classifying it by
    # ``created_at`` would incorrectly authorize a legacy reconstruction.
    legacy_fill_exists = matching_orders().where(Order.filled_at < opened_at).exists()
    post_boundary_fill_exists = matching_orders().where(Order.filled_at >= opened_at).exists()
    modern_intent_exists = (
        select(ExecutionOrderIntent.id)
        .join(Order, Order.id == ExecutionOrderIntent.order_id)
        .join(Signal, Signal.id == Order.signal_id)
        .where(
            ExecutionOrderIntent.mode == ExecutionMode.PAPER,
            Signal.market_id == Position.market_id,
            ExecutionOrderIntent.side == Position.side,
            ExecutionOrderIntent.token_id.is_not_distinct_from(Position.token_id),
        )
        .correlate(Position)
        .exists()
    )
    return (
        select(Position, Market)
        .join(Market, Position.market_id == Market.id)
        .where(
            Position.mode == ExecutionMode.PAPER,
            Position.fee_usd.is_(None),
            legacy_fill_exists,
            ~post_boundary_fill_exists,
            ~modern_intent_exists,
        )
        .with_for_update(of=Position, skip_locked=True)
    )


async def backfill_fees(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            order_rows = (
                await session.execute(
                    _legacy_fee_backfill_orders_statement()
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
                    _legacy_fee_backfill_positions_statement()
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
