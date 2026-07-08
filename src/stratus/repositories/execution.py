from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    ExecutionMode,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    TradeSide,
)
from stratus.db.session import dml_rowcount

# Live order lifecycle. "submitting" is the durable pre-submit marker; a
# crash leaves it here and the next run escalates it rather than re-sending.
LIVE_STATUS_SUBMITTING = "submitting"
LIVE_STATUS_OPEN = "open"
LIVE_STATUS_FILLED = "filled"
LIVE_STATUS_SUBMIT_FAILED = "submit_failed"
LIVE_STATUS_UNCONFIRMED = "submit_unconfirmed"
LIVE_STATUS_CANCEL_REQUESTED = "cancel_requested"
LIVE_STATUS_CANCELED = "canceled"


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


@dataclass(frozen=True, slots=True)
class LiveOrderClaim:
    signal_id: int
    market_id: int
    side: TradeSide
    price: Decimal
    size_usd: Decimal


async def claim_live_order(session: AsyncSession, claim: LiveOrderClaim) -> int | None:
    """Insert the durable pre-submit marker; returns the order id if this call
    created it, or None if an order for the signal already exists.

    Committing this before the network call is what makes live placement
    crash-safe: a signal that already has an order row is never re-submitted.
    """
    statement = (
        insert(Order)
        .values(
            signal_id=claim.signal_id,
            mode=ExecutionMode.LIVE,
            clob_order_id=None,
            side=claim.side,
            price=claim.price,
            size_usd=claim.size_usd,
            filled_usd=Decimal(0),
            status=LIVE_STATUS_SUBMITTING,
        )
        .on_conflict_do_nothing(constraint="uq_orders_signal_mode")
        .returning(Order.id)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def record_live_placement(
    session: AsyncSession,
    *,
    order_id: int,
    clob_order_id: str,
    status: str,
    placed_at: datetime,
) -> None:
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        return
    order.clob_order_id = clob_order_id
    order.status = status
    order.filled_at = placed_at if status == LIVE_STATUS_FILLED else None
    signal = await session.get(Signal, order.signal_id, with_for_update=True)
    if signal is not None and signal.status is SignalStatus.PENDING:
        signal.status = SignalStatus.EXECUTED


async def mark_live_order(
    session: AsyncSession, order_id: int, *, status: str, clob_order_id: str | None = None
) -> None:
    order = await session.get(Order, order_id, with_for_update=True)
    if order is None:
        return
    order.status = status
    if clob_order_id is not None:
        order.clob_order_id = clob_order_id


async def stale_submitting_orders(session: AsyncSession) -> list[Order]:
    """Orders left in the pre-submit state by a crash: their real state on the
    exchange is unknown, so they must be escalated, never re-sent."""
    rows = (
        await session.execute(
            select(Order)
            .where(
                Order.mode == ExecutionMode.LIVE,
                Order.status == LIVE_STATUS_SUBMITTING,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    return list(rows)


async def claim_cancellable_live_orders(session: AsyncSession) -> list[Order]:
    rows = (
        await session.execute(
            select(Order)
            .where(
                Order.mode == ExecutionMode.LIVE,
                Order.status == LIVE_STATUS_CANCEL_REQUESTED,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    return list(rows)


async def open_live_orders(session: AsyncSession) -> list[tuple[Order, int]]:
    """Live orders still resting on the book, each paired with its market id
    (resolved through the signal). A matched order is marked `filled` in the
    same transaction as its final position write, so it drops out here and the
    poll set stays bounded to genuinely open orders."""
    rows = (
        await session.execute(
            select(Order, Signal.market_id)
            .join(Signal, Order.signal_id == Signal.id)
            .where(
                Order.mode == ExecutionMode.LIVE,
                Order.status == LIVE_STATUS_OPEN,
                Order.clob_order_id.is_not(None),
            )
            .with_for_update(of=Order, skip_locked=True)
        )
    ).all()
    return [(order, market_id) for order, market_id in rows]


@dataclass(frozen=True, slots=True)
class LiveFillValue:
    order_id: int
    market_id: int
    side: TradeSide
    size_shares: Decimal
    avg_price: Decimal
    cost_usd: Decimal


async def upsert_live_position(session: AsyncSession, fill: LiveFillValue) -> None:
    """Idempotently recompute the position for one live order from its
    cumulative fill state. Recomputing (not appending) makes repeated polls of
    the same order safe. A zero fill writes nothing."""
    if fill.size_shares <= 0:
        return
    position = await session.scalar(
        select(Position)
        .where(Position.order_id == fill.order_id)
        .with_for_update()
    )
    if position is None:
        session.add(
            Position(
                order_id=fill.order_id,
                market_id=fill.market_id,
                mode=ExecutionMode.LIVE,
                side=fill.side,
                avg_price=fill.avg_price,
                size_shares=fill.size_shares,
                size_usd=fill.cost_usd,
                status=PositionStatus.OPEN,
            )
        )
        return
    if position.status is PositionStatus.CLOSED:
        return
    position.avg_price = fill.avg_price
    position.size_shares = fill.size_shares
    position.size_usd = fill.cost_usd


async def record_live_bankroll(
    session: AsyncSession, *, balance: Decimal, now: datetime
) -> int:
    """Append a live bankroll snapshot only when the balance changed, so the
    per-poll cadence does not flood the log. Returns 1 if a row was written."""
    latest = await session.scalar(
        select(BankrollLog.balance_usd)
        .where(BankrollLog.mode == ExecutionMode.LIVE)
        .order_by(BankrollLog.ts.desc())
        .limit(1)
    )
    rounded = balance.quantize(Decimal("0.0001"))
    if latest is not None and Decimal(latest) == rounded:
        return 0
    session.add(
        BankrollLog(
            ts=now,
            mode=ExecutionMode.LIVE,
            balance_usd=rounded,
            event="live collateral snapshot",
        )
    )
    return 1


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
