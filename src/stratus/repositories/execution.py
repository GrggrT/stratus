from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    ExecutionMode,
    Market,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    TradeSide,
)
from stratus.db.session import dml_rowcount
from stratus.repositories.gamma_outcomes import lock_gamma_event
from stratus.repositories.validation_ledger import enroll_terminal_order

# Live order lifecycle. "submitting" is the durable pre-submit marker; a
# crash leaves it here and the next run escalates it rather than re-sending.
LIVE_STATUS_SUBMITTING = "submitting"
LIVE_STATUS_OPEN = "open"
LIVE_STATUS_FILLED = "filled"
LIVE_STATUS_PARTIALLY_FILLED = "partially_filled"
LIVE_STATUS_SUBMIT_FAILED = "submit_failed"
LIVE_STATUS_UNCONFIRMED = "submit_unconfirmed"
LIVE_STATUS_CANCEL_REQUESTED = "cancel_requested"
LIVE_STATUS_CANCELED = "canceled"

# Paper maker lifecycle: a resting simulated limit order waits for the market
# to trade through it; TTL expiry marks it unfilled instead of paying taker.
PAPER_STATUS_RESTING = "paper_resting"
PAPER_STATUS_PENDING = "paper_pending"
PAPER_STATUS_UNFILLED = "unfilled_maker"


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


async def expire_pending_signals(
    session: AsyncSession,
    signal_ids: list[int],
    *,
    reason: str = "paper_execution_timeout",
) -> int:
    if not signal_ids:
        return 0
    result = await session.execute(
        update(Signal)
        .where(Signal.id.in_(signal_ids), Signal.status == SignalStatus.PENDING)
        .values(status=SignalStatus.EXPIRED, skip_reason=reason)
    )
    return dml_rowcount(result)


@dataclass(frozen=True, slots=True)
class LiveOrderClaim:
    signal_id: int
    market_id: int
    side: TradeSide
    price: Decimal
    size_usd: Decimal
    token_id: str | None = None


async def claim_live_order(session: AsyncSession, claim: LiveOrderClaim) -> int | None:
    """Insert the durable pre-submit marker; returns the order id if this call
    created it, or None if an order for the signal already exists.

    Committing this before the network call is what makes live placement
    crash-safe: a signal that already has an order row is never re-submitted.
    """
    signal = await session.scalar(
        select(Signal)
        .where(
            Signal.id == claim.signal_id,
            Signal.status == SignalStatus.PENDING,
        )
        .with_for_update(skip_locked=True)
    )
    if signal is None:
        return None
    statement = (
        insert(Order)
        .values(
            signal_id=claim.signal_id,
            mode=ExecutionMode.LIVE,
            clob_order_id=None,
            token_id=claim.token_id,
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
    # Load cumulative fill provenance before changing the order to a terminal
    # state.  Dual-ledger terminal rows are immutable: an autoflush between
    # setting ``status`` and copying the fill totals would otherwise emit two
    # UPDATEs and correctly be rejected by the database on the second one.
    position = None
    filled_at = order.filled_at
    if status in {LIVE_STATUS_FILLED, LIVE_STATUS_CANCELED}:
        position = await session.scalar(
            select(Position).where(Position.order_id == order.id)
        )
        if position is not None and position.size_usd > 0 and filled_at is None:
            filled_at = await session.scalar(select(func.clock_timestamp()))
    effective_status = (
        LIVE_STATUS_PARTIALLY_FILLED
        if status == LIVE_STATUS_CANCELED
        and position is not None
        and position.size_usd > 0
        else status
    )
    order.status = effective_status
    if clob_order_id is not None:
        order.clob_order_id = clob_order_id
    if (
        effective_status in {LIVE_STATUS_FILLED, LIVE_STATUS_PARTIALLY_FILLED}
        and position is not None
        and position.size_usd > 0
    ):
        order.filled_usd = position.size_usd
        order.fee_usd = position.fee_usd
        order.filled_at = filled_at
        await session.flush()
        await enroll_terminal_order(session, order.id)


async def stale_submitting_orders(session: AsyncSession) -> list[Order]:
    """Orders left in the pre-submit state by a crash: their real state on the
    exchange is unknown, so they must be escalated, never re-sent."""
    rows = (
        (
            await session.execute(
                select(Order)
                .where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status == LIVE_STATUS_SUBMITTING,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def claim_cancellable_live_orders(session: AsyncSession) -> list[Order]:
    rows = (
        (
            await session.execute(
                select(Order)
                .where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status == LIVE_STATUS_CANCEL_REQUESTED,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@dataclass(frozen=True, slots=True)
class OpenLiveOrder:
    order_id: int
    clob_order_id: str
    market_id: int
    side: TradeSide
    token_id: str | None


@dataclass(frozen=True, slots=True)
class InFlightLiveExposure:
    market_id: int
    station_id: int | None
    market_date: date
    size_usd: Decimal


async def in_flight_live_exposure(session: AsyncSession) -> list[InFlightLiveExposure]:
    """Live orders that are placed but do not yet have an open position — the
    window between placement and the first fill poll. Signal generation must
    count this committed capital, or a second order could breach the exposure
    caps on the same market/station-day. Paper has no such window."""
    from stratus.db.models import Market

    rows = (
        await session.execute(
            select(Order.size_usd, Market.id, Market.station_id, Market.market_date)
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .outerjoin(
                Position,
                and_(
                    Position.order_id == Order.id,
                    Position.status == PositionStatus.OPEN,
                ),
            )
            .where(
                Order.mode == ExecutionMode.LIVE,
                Order.status.in_([LIVE_STATUS_SUBMITTING, LIVE_STATUS_OPEN]),
                Position.id.is_(None),
            )
        )
    ).all()
    return [
        InFlightLiveExposure(
            market_id=market_id,
            station_id=station_id,
            market_date=market_date,
            size_usd=size_usd,
        )
        for size_usd, market_id, station_id, market_date in rows
    ]


async def open_live_orders(session: AsyncSession) -> list[OpenLiveOrder]:
    """Live orders still resting on the book, each with the market id and the
    traded token. A matched order is marked `filled` in the same transaction
    as its final position write, so it drops out here and the poll set stays
    bounded to genuinely open orders."""
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
    result = []
    for order, market_id in rows:
        assert order.clob_order_id is not None
        result.append(
            OpenLiveOrder(
                order_id=order.id,
                clob_order_id=order.clob_order_id,
                market_id=market_id,
                side=order.side,
                token_id=order.token_id,
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class LiveFillValue:
    order_id: int
    market_id: int
    side: TradeSide
    size_shares: Decimal
    avg_price: Decimal
    cost_usd: Decimal
    fee_usd: Decimal | None = None


async def upsert_live_position(session: AsyncSession, fill: LiveFillValue) -> None:
    """Idempotently recompute the position for one live order from its
    cumulative fill state. Recomputing (not appending) makes repeated polls of
    the same order safe. A zero fill writes nothing."""
    if fill.size_shares <= 0:
        return
    provenance = (
        await session.execute(
            select(Order.token_id, Signal.benchmark_protocol, Market.event_id)
            .join(Signal, Signal.id == Order.signal_id)
            .join(Market, Market.id == Signal.market_id)
            .where(Order.id == fill.order_id)
        )
    ).one_or_none()
    if provenance is None:
        return
    token_id, benchmark_protocol, event_id = provenance
    # Settlement takes this event lock before any Market/Position row lock.
    # Match that order so a fill arriving at the resolution boundary cannot
    # deadlock against outcome projection or be committed without enrollment.
    await lock_gamma_event(session, event_id)
    position = await session.scalar(
        select(Position).where(Position.order_id == fill.order_id).with_for_update()
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
                fee_usd=fill.fee_usd,
                status=PositionStatus.OPEN,
                token_id=token_id,
                benchmark_protocol=benchmark_protocol,
            )
        )
        return
    if position.status is PositionStatus.CLOSED:
        return
    position.avg_price = fill.avg_price
    position.size_shares = fill.size_shares
    position.size_usd = fill.cost_usd
    if fill.fee_usd is not None:
        position.fee_usd = fill.fee_usd


async def record_live_bankroll(session: AsyncSession, *, balance: Decimal, now: datetime) -> int:
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


async def _aggregate_paper_position(
    session: AsyncSession,
    *,
    market_id: int,
    side: TradeSide,
    avg_price: Decimal,
    size_shares: Decimal,
    total_cost_usd: Decimal,
    fee_usd: Decimal,
    benchmark_protocol: str,
    token_id: str | None,
) -> None:
    """Fold one paper fill into the open (market, side) position, keeping a
    single aggregated row per market/side instead of one row per top-up."""
    event_id = await session.scalar(select(Market.event_id).where(Market.id == market_id))
    if event_id is None:
        raise RuntimeError(f"paper fill market {market_id} has no event identity")
    # Keep lock ordering identical to settlement: event advisory lock first,
    # then Position.  The enrollment hook safely reacquires the same xact lock.
    await lock_gamma_event(session, event_id)
    position = await session.scalar(
        select(Position)
        .where(
            Position.market_id == market_id,
            Position.mode == ExecutionMode.PAPER,
            Position.side == side,
            Position.status == PositionStatus.OPEN,
            Position.benchmark_protocol == benchmark_protocol,
            Position.token_id.is_not_distinct_from(token_id),
        )
        .order_by(Position.id)
        .with_for_update()
    )
    if position is None:
        session.add(
            Position(
                market_id=market_id,
                mode=ExecutionMode.PAPER,
                side=side,
                avg_price=avg_price,
                size_shares=size_shares,
                size_usd=total_cost_usd,
                fee_usd=fee_usd,
                status=PositionStatus.OPEN,
                benchmark_protocol=benchmark_protocol,
                token_id=token_id,
            )
        )
        return
    combined_shares = position.size_shares + size_shares
    if combined_shares > 0:
        position.avg_price = (
            (position.avg_price * position.size_shares + avg_price * size_shares) / combined_shares
        ).quantize(Decimal("0.000001"))
    position.size_shares = combined_shares.quantize(Decimal("0.000001"))
    position.size_usd = (position.size_usd + total_cost_usd).quantize(Decimal("0.0001"))
    position.fee_usd = ((position.fee_usd or Decimal(0)) + fee_usd).quantize(Decimal("0.00001"))


async def persist_paper_executions(session: AsyncSession, values: list[PaperExecutionValue]) -> int:
    written = 0
    for value in values:
        signal = await session.scalar(
            select(Signal).where(Signal.id == value.signal_id).with_for_update(skip_locked=True)
        )
        if signal is None or signal.status is not SignalStatus.PENDING:
            continue
        order_status = (
            "filled"
            if value.total_cost_usd >= value.requested_usd - Decimal("0.0001")
            else "partially_filled"
        )
        order = Order(
            signal_id=value.signal_id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{value.signal_id}",
            token_id=signal.token_id,
            side=value.side,
            price=value.avg_price,
            size_usd=value.requested_usd,
            filled_usd=value.filled_usd,
            fee_usd=value.fee_usd,
            status=order_status,
            filled_at=value.filled_at,
        )
        session.add(order)
        await _aggregate_paper_position(
            session,
            market_id=value.market_id,
            side=value.side,
            avg_price=value.avg_price,
            size_shares=value.size_shares,
            total_cost_usd=value.total_cost_usd,
            fee_usd=value.fee_usd,
            benchmark_protocol=signal.benchmark_protocol,
            token_id=signal.token_id,
        )
        signal.status = SignalStatus.EXECUTED
        await session.flush()
        await enroll_terminal_order(session, order.id)
        written += 1
    await session.flush()
    return written


@dataclass(frozen=True, slots=True)
class RestingPaperOrder:
    signal_id: int
    side: TradeSide
    limit_price: Decimal
    size_usd: Decimal
    token_id: str | None = None


async def insert_resting_paper_orders(
    session: AsyncSession, values: list[RestingPaperOrder]
) -> int:
    """Post simulated maker limits. Idempotent per signal thanks to
    uq_orders_signal_mode — a signal already holding a paper order (resting
    or filled) is never double-posted."""
    written = 0
    for value in values:
        statement = (
            insert(Order)
            .values(
                signal_id=value.signal_id,
                mode=ExecutionMode.PAPER,
                clob_order_id=f"paper-{value.signal_id}",
                token_id=value.token_id,
                side=value.side,
                price=value.limit_price,
                size_usd=value.size_usd,
                filled_usd=Decimal(0),
                status=PAPER_STATUS_RESTING,
            )
            .on_conflict_do_nothing(constraint="uq_orders_signal_mode")
            .returning(Order.id)
        )
        if (await session.execute(statement)).scalar_one_or_none() is not None:
            written += 1
    return written


async def resting_paper_orders(session: AsyncSession) -> dict[int, Order]:
    """Resting maker orders keyed by signal id, locked for this cycle."""
    rows = (
        (
            await session.execute(
                select(Order)
                .where(
                    Order.mode == ExecutionMode.PAPER,
                    Order.status == PAPER_STATUS_RESTING,
                )
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    return {order.signal_id: order for order in rows}


@dataclass(frozen=True, slots=True)
class MakerFillValue:
    order_id: int
    signal_id: int
    market_id: int
    side: TradeSide
    limit_price: Decimal
    size_shares: Decimal
    filled_usd: Decimal
    fee_usd: Decimal
    total_cost_usd: Decimal
    filled_at: datetime


async def persist_maker_fills(session: AsyncSession, fills: list[MakerFillValue]) -> int:
    written = 0
    for fill in fills:
        order = await session.get(Order, fill.order_id, with_for_update=True)
        if order is None or order.status != PAPER_STATUS_RESTING:
            continue
        signal = await session.scalar(
            select(Signal).where(Signal.id == fill.signal_id).with_for_update(skip_locked=True)
        )
        if signal is None or signal.status is not SignalStatus.PENDING:
            continue
        order.status = LIVE_STATUS_FILLED
        order.filled_usd = fill.filled_usd
        order.fee_usd = fill.fee_usd
        order.filled_at = fill.filled_at
        await _aggregate_paper_position(
            session,
            market_id=fill.market_id,
            side=fill.side,
            avg_price=fill.limit_price,
            size_shares=fill.size_shares,
            total_cost_usd=fill.total_cost_usd,
            fee_usd=fill.fee_usd,
            benchmark_protocol=signal.benchmark_protocol,
            token_id=signal.token_id,
        )
        signal.status = SignalStatus.EXECUTED
        await session.flush()
        await enroll_terminal_order(session, order.id)
        written += 1
    await session.flush()
    return written


async def expire_resting_paper_orders(session: AsyncSession, signal_ids: list[int]) -> int:
    """TTL expiry for maker orders whose signals just expired. The unfilled
    rate is itself a quality metric — it prices how often our passive quote
    was never reached."""
    if not signal_ids:
        return 0
    result = await session.execute(
        update(Order)
        .where(
            Order.signal_id.in_(signal_ids),
            Order.mode == ExecutionMode.PAPER,
            Order.status == PAPER_STATUS_RESTING,
        )
        .values(status=PAPER_STATUS_UNFILLED)
    )
    return dml_rowcount(result)
