from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionOrderIntent,
    Market,
    MarketStatus,
    NotificationDeliveryRuntime,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TelegramDelivery,
)
from stratus.db.session import dml_rowcount
from stratus.repositories.analytics import (
    ActualOrderFill,
    actual_order_fill,
    execution_fill_aggregate_subquery,
)


@dataclass(frozen=True, slots=True)
class ClaimedDelivery:
    id: int
    payload: str
    attempts: int
    lease_until: datetime


@dataclass(frozen=True, slots=True)
class NotificationDeliverySummary:
    channel: str
    state: str
    total_count: int
    unacknowledged_count: int
    oldest_unacknowledged_at: datetime | None
    activated_at: datetime | None
    heartbeat_at: datetime | None
    heartbeat_stale: bool
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    pending_after_activation: int
    oldest_pending_after_activation_at: datetime | None
    backlog_overdue: bool
    dead_letter_count: int


TELEGRAM_CHANNEL = "telegram"
DELIVERY_HEARTBEAT_STALE_AFTER = timedelta(minutes=2)
DELIVERY_BACKLOG_STALE_AFTER = timedelta(minutes=2)
MAX_DELIVERY_ERROR_LENGTH = 1000
_NO_FILL_ORDER_STATUSES = frozenset(
    {"canceled", "cancelled", "unfilled_maker", "submit_failed", "rejected", "expired"}
)


async def has_delivery(session: AsyncSession, event_key: str) -> bool:
    row = await session.scalar(
        select(TelegramDelivery.id).where(TelegramDelivery.event_key == event_key)
    )
    return row is not None


async def enqueue_notification(
    session: AsyncSession, event_key: str, payload: str
) -> bool:
    result = await session.execute(
        insert(TelegramDelivery)
        .values(event_key=event_key, payload=payload)
        .on_conflict_do_nothing(index_elements=[TelegramDelivery.event_key])
    )
    return bool(dml_rowcount(result))


def _bucket(market: Market) -> str:
    low = "−∞" if market.bucket_low_c is None else f"{market.bucket_low_c:g}"
    high = "∞" if market.bucket_high_c is None else f"{market.bucket_high_c:g}"
    return f"[{low}, {high})°C"


def format_fill_notification_execution(
    *,
    requested_limit: Decimal,
    status: str,
    fill: ActualOrderFill | None,
    filled_notional_hint: Decimal | None = None,
) -> str:
    """Describe decision price and execution price without conflating them."""

    requested = f"requested limit {requested_limit:.3f}"
    if fill is None:
        known_zero_fill = (
            filled_notional_hint is not None
            and Decimal(filled_notional_hint).is_finite()
            and Decimal(filled_notional_hint) == 0
        )
        if status.strip().lower() in _NO_FILL_ORDER_STATUSES and known_zero_fill:
            return f"WARNING no_fill; {requested} ({status})"
        return f"CRITICAL execution_evidence_unavailable; {requested} ({status})"
    fee = "unknown" if fill.fee_usd is None else f"${fill.fee_usd:.4f}"
    return (
        f"${fill.filled_notional_usd:.2f} at fill VWAP {fill.entry_price:.3f}; "
        f"{requested}; fee {fee} ({status})"
    )


async def enqueue_recent_notifications(
    session: AsyncSession, since: datetime
) -> int:
    values = []
    signal_rows = (
        await session.execute(
            select(Signal, Market, Station)
            .join(Market, Signal.market_id == Market.id)
            .join(Station, Market.station_id == Station.id)
            .where(
                Signal.confidence.in_([SignalConfidence.A, SignalConfidence.B]),
                Signal.status.in_([SignalStatus.PENDING, SignalStatus.EXECUTED]),
                Signal.ts >= since,
            )
        )
    ).all()
    for signal, market, station in signal_rows:
        values.append(
            {
                "event_key": f"signal:{signal.id}",
                "payload": (
                    f"Signal {signal.confidence.value} · {signal.side.value}\n"
                    f"{station.icao} {market.market_date} {_bucket(market)}\n"
                    f"Entry {signal.market_price:.3f}, model {signal.model_prob:.3f}, "
                    f"edge {signal.edge:+.3f}, size ${signal.size_usd:.2f}\n"
                    f"Status: {signal.status.value}"
                ),
            }
        )
    fill_aggregate = execution_fill_aggregate_subquery()
    order_rows = (
        await session.execute(
            select(
                Order,
                Signal,
                Market,
                Station,
                ExecutionOrderIntent.id,
                fill_aggregate.c.vwap_fill_price,
                fill_aggregate.c.filled_notional_usd,
                fill_aggregate.c.total_fee_usd,
                fill_aggregate.c.total_shares,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .join(Station, Market.station_id == Station.id)
            .outerjoin(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
            .outerjoin(fill_aggregate, fill_aggregate.c.order_id == Order.id)
            .where(Order.filled_at >= since)
        )
    ).all()
    for (
        order,
        _signal,
        market,
        station,
        execution_intent_id,
        leg_vwap,
        leg_notional,
        leg_fee,
        leg_shares,
    ) in order_rows:
        fill = actual_order_fill(
            order_price=order.price,
            order_filled_usd=order.filled_usd,
            order_fee_usd=order.fee_usd,
            execution_intent_id=execution_intent_id,
            leg_vwap_fill_price=leg_vwap,
            leg_filled_notional_usd=leg_notional,
            leg_fee_usd=leg_fee,
            leg_total_shares=leg_shares,
        )
        values.append(
            {
                "event_key": f"fill:{order.id}",
                "payload": (
                    f"Fill · {order.mode.value} {order.side.value}\n"
                    f"{station.icao} {market.market_date} {_bucket(market)}\n"
                    + format_fill_notification_execution(
                        requested_limit=Decimal(order.price),
                        status=order.status,
                        fill=fill,
                        filled_notional_hint=Decimal(order.filled_usd),
                    )
                ),
            }
        )
    position_rows = (
        await session.execute(
            select(Position, Market, Station)
            .join(Market, Position.market_id == Market.id)
            .join(Station, Market.station_id == Station.id)
            .where(
                Position.status == PositionStatus.CLOSED,
                Position.closed_at >= since,
            )
        )
    ).all()
    for position, market, station in position_rows:
        clv = "n/a" if position.clv is None else f"{position.clv:+.3f}"
        values.append(
            {
                "event_key": f"resolution:{position.id}",
                "payload": (
                    f"Resolution · {position.side.value}\n"
                    f"{station.icao} {market.market_date} {_bucket(market)}\n"
                    f"P&L ${position.pnl_usd or 0:+.2f}; CLV {clv}"
                ),
            }
        )
    verification_rows = (
        await session.execute(
            select(Market).where(
                Market.status == MarketStatus.BLOCKED,
                Market.station_verified.is_(False),
                Market.created_at >= since,
            )
        )
    ).scalars().all()
    for market in verification_rows:
        values.append(
            {
                "event_key": f"verify:{market.id}",
                "payload": (
                    f"Station verification required\n"
                    f"Market {market.id}, external {market.polymarket_id}\n"
                    f"Reason: {market.station_verification_reason or 'unknown'}\n"
                    f"Use /verify {market.id} after checking rules."
                ),
            }
        )
    if not values:
        return 0
    result = await session.execute(
        insert(TelegramDelivery)
        .values(values)
        .on_conflict_do_nothing(index_elements=[TelegramDelivery.event_key])
    )
    return dml_rowcount(result)


async def activate_notification_channel(
    session: AsyncSession,
    channel: str = TELEGRAM_CHANNEL,
) -> NotificationDeliveryRuntime:
    """Explicitly activate an external channel at the PostgreSQL clock.

    ``ON CONFLICT DO NOTHING`` makes concurrent consumer starts idempotent and,
    critically, preserves the first activation watermark.  No migration or
    read path calls this function.
    """

    await session.execute(
        insert(NotificationDeliveryRuntime)
        .values(channel=channel, activated_at=func.clock_timestamp())
        .on_conflict_do_nothing(index_elements=[NotificationDeliveryRuntime.channel])
    )
    runtime = await session.get(NotificationDeliveryRuntime, channel)
    if runtime is None:  # pragma: no cover - defensive against database corruption
        raise RuntimeError(f"notification channel activation failed: {channel}")
    return runtime


async def record_notification_heartbeat(
    session: AsyncSession,
    channel: str = TELEGRAM_CHANNEL,
) -> bool:
    """Record consumer liveness without implicitly activating a channel."""

    result = await session.execute(
        update(NotificationDeliveryRuntime)
        .where(NotificationDeliveryRuntime.channel == channel)
        .values(heartbeat_at=func.clock_timestamp())
    )
    return bool(dml_rowcount(result))


async def claim_deliveries(
    session: AsyncSession,
    *,
    channel: str = TELEGRAM_CHANNEL,
    limit: int = 20,
    lease_seconds: int = 60,
) -> list[ClaimedDelivery]:
    """Atomically lease due post-activation rows with ``SKIP LOCKED``.

    Attempts advance at claim time.  A transaction rollback rolls the attempt
    and lease back together, so a returned item always has durable ownership.
    The delivery guarantee is at-least-once: a consumer crash after an external
    send but before acknowledgement can cause a retry after lease expiry.
    """

    if limit <= 0:
        return []
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    rows = (
        await session.execute(
            text(
                """
                WITH current_clock AS MATERIALIZED (
                    SELECT clock_timestamp() AS now
                ), runtime AS MATERIALIZED (
                    SELECT activated_at
                    FROM notification_delivery_runtime
                    WHERE channel = :channel
                ), candidates AS (
                    SELECT delivery.id
                    FROM telegram_deliveries AS delivery
                    CROSS JOIN runtime
                    CROSS JOIN current_clock
                    WHERE delivery.created_at >= runtime.activated_at
                      AND delivery.sent_at IS NULL
                      AND delivery.dead_lettered_at IS NULL
                      AND (
                          delivery.next_attempt_at IS NULL
                          OR delivery.next_attempt_at <= current_clock.now
                      )
                      AND (
                          delivery.lease_until IS NULL
                          OR delivery.lease_until < current_clock.now
                      )
                    ORDER BY delivery.id
                    FOR UPDATE OF delivery SKIP LOCKED
                    LIMIT :limit
                )
                UPDATE telegram_deliveries AS delivery
                SET attempts = delivery.attempts + 1,
                    last_attempt_at = current_clock.now,
                    next_attempt_at = NULL,
                    lease_until = current_clock.now
                        + make_interval(secs => CAST(:lease_seconds AS double precision))
                FROM candidates, current_clock
                WHERE delivery.id = candidates.id
                RETURNING delivery.id, delivery.payload, delivery.attempts,
                          delivery.lease_until
                """
            ),
            {
                "channel": channel,
                "limit": min(limit, 100),
                "lease_seconds": lease_seconds,
            },
        )
    ).mappings()
    return [
        ClaimedDelivery(
            id=int(row["id"]),
            payload=str(row["payload"]),
            attempts=int(row["attempts"]),
            lease_until=row["lease_until"],
        )
        for row in rows
    ]


async def mark_delivery_sent(
    session: AsyncSession,
    delivery_id: int,
    claimed_lease_until: datetime,
    *,
    channel: str = TELEGRAM_CHANNEL,
) -> bool:
    """Confirm an external send and clear in-flight delivery state."""

    predicate = [
        TelegramDelivery.id == delivery_id,
        TelegramDelivery.sent_at.is_(None),
        TelegramDelivery.dead_lettered_at.is_(None),
        TelegramDelivery.lease_until == claimed_lease_until,
    ]
    result = await session.execute(
        update(TelegramDelivery)
        .where(*predicate)
        .values(
            sent_at=func.clock_timestamp(),
            lease_until=None,
            next_attempt_at=None,
            error=None,
        )
    )
    if not dml_rowcount(result):
        return False
    await session.execute(
        update(NotificationDeliveryRuntime)
        .where(NotificationDeliveryRuntime.channel == channel)
        .values(
            heartbeat_at=func.clock_timestamp(),
            last_success_at=func.clock_timestamp(),
            last_error=None,
        )
    )
    return True


async def mark_delivery_failed(
    session: AsyncSession,
    delivery_id: int,
    error: str,
    claimed_lease_until: datetime,
    *,
    channel: str = TELEGRAM_CHANNEL,
) -> bool:
    """Record a failed claimed attempt, retry schedule, or dead letter.

    Attempts are incremented by :func:`claim_deliveries`; this transition never
    increments them a second time.  The exact retry schedule is 10s, 30s, 2m,
    and 10m.  Failure of attempt five or later is terminal and explicit.
    """

    parameters: dict[str, object] = {
        "delivery_id": delivery_id,
        "error": error[:MAX_DELIVERY_ERROR_LENGTH],
        "claimed_lease_until": claimed_lease_until,
    }
    row = (
        await session.execute(
            text(
                """
                WITH current_clock AS MATERIALIZED (
                    SELECT clock_timestamp() AS now
                )
                UPDATE telegram_deliveries
                SET lease_until = NULL,
                    error = :error,
                    next_attempt_at = CASE attempts
                        WHEN 1 THEN current_clock.now + INTERVAL '10 seconds'
                        WHEN 2 THEN current_clock.now + INTERVAL '30 seconds'
                        WHEN 3 THEN current_clock.now + INTERVAL '2 minutes'
                        WHEN 4 THEN current_clock.now + INTERVAL '10 minutes'
                        ELSE NULL
                    END,
                    dead_lettered_at = CASE
                        WHEN attempts >= 5 THEN current_clock.now
                        ELSE NULL
                    END
                FROM current_clock
                WHERE telegram_deliveries.id = :delivery_id
                  AND sent_at IS NULL
                  AND dead_lettered_at IS NULL
                  AND last_attempt_at IS NOT NULL
                  AND lease_until = :claimed_lease_until
                RETURNING telegram_deliveries.id
                """
            ),
            parameters,
        )
    ).first()
    if row is None:
        return False
    await session.execute(
        update(NotificationDeliveryRuntime)
        .where(NotificationDeliveryRuntime.channel == channel)
        .values(
            heartbeat_at=func.clock_timestamp(),
            last_failure_at=func.clock_timestamp(),
            last_error=error[:MAX_DELIVERY_ERROR_LENGTH],
        )
    )
    return True


async def list_delivery_alerts(
    session: AsyncSession,
    *,
    unacknowledged_only: bool = True,
    limit: int = 100,
) -> list[TelegramDelivery]:
    query = select(TelegramDelivery).order_by(TelegramDelivery.id.desc())
    if unacknowledged_only:
        query = query.where(TelegramDelivery.acknowledged_at.is_(None))
    return list((await session.scalars(query.limit(max(1, min(limit, 500))))).all())


async def acknowledge_delivery(session: AsyncSession, delivery_id: int) -> bool:
    """Idempotently acknowledge one local inbox item using the DB clock."""

    row = (
        await session.execute(
            update(TelegramDelivery)
            .where(TelegramDelivery.id == delivery_id)
            .values(
                acknowledged_at=func.coalesce(
                    TelegramDelivery.acknowledged_at,
                    func.clock_timestamp(),
                )
            )
            .returning(TelegramDelivery.id)
        )
    ).first()
    return row is not None


async def acknowledge_deliveries_through(
    session: AsyncSession,
    through_id: int,
) -> int:
    """Acknowledge only rows at or below a caller-provided stable boundary."""

    result = await session.execute(
        update(TelegramDelivery)
        .where(
            TelegramDelivery.id <= through_id,
            TelegramDelivery.acknowledged_at.is_(None),
        )
        .values(acknowledged_at=func.clock_timestamp())
    )
    return dml_rowcount(result)


async def notification_delivery_summary(
    session: AsyncSession,
    *,
    channel: str = TELEGRAM_CHANNEL,
) -> NotificationDeliverySummary:
    """Return local-inbox and optional-channel state from one DB clock."""

    runtime = await session.get(NotificationDeliveryRuntime, channel)
    total_count, unacknowledged_count, oldest_unacknowledged_at = (
        await session.execute(
            select(
                func.count(TelegramDelivery.id),
                func.count(TelegramDelivery.id).filter(
                    TelegramDelivery.acknowledged_at.is_(None)
                ),
                func.min(TelegramDelivery.created_at).filter(
                    TelegramDelivery.acknowledged_at.is_(None)
                ),
            )
        )
    ).one()
    pending_after_activation = 0
    oldest_pending_after_activation_at: datetime | None = None
    dead_letter_count = 0
    backlog_overdue = False
    heartbeat_stale = False
    state = "not_activated"
    if runtime is not None:
        (
            pending_after_activation,
            oldest_pending_after_activation_at,
            dead_letter_count,
            database_now,
        ) = (
            await session.execute(
                select(
                    func.count(TelegramDelivery.id).filter(
                        TelegramDelivery.created_at >= runtime.activated_at,
                        TelegramDelivery.sent_at.is_(None),
                        TelegramDelivery.dead_lettered_at.is_(None),
                    ),
                    func.min(TelegramDelivery.created_at).filter(
                        TelegramDelivery.created_at >= runtime.activated_at,
                        TelegramDelivery.sent_at.is_(None),
                        TelegramDelivery.dead_lettered_at.is_(None),
                    ),
                    func.count(TelegramDelivery.id).filter(
                        TelegramDelivery.created_at >= runtime.activated_at,
                        TelegramDelivery.dead_lettered_at.is_not(None),
                    ),
                    func.clock_timestamp(),
                )
            )
        ).one()
        failure_is_current = runtime.last_failure_at is not None and (
            runtime.last_success_at is None
            or runtime.last_failure_at > runtime.last_success_at
        )
        backlog_overdue = (
            oldest_pending_after_activation_at is not None
            and database_now - oldest_pending_after_activation_at
            > DELIVERY_BACKLOG_STALE_AFTER
        )
        heartbeat_stale = (
            runtime.heartbeat_at is None
            or database_now - runtime.heartbeat_at > DELIVERY_HEARTBEAT_STALE_AFTER
        )
        if dead_letter_count or backlog_overdue or failure_is_current:
            state = "degraded"
        elif heartbeat_stale:
            state = "stale"
        else:
            state = "healthy"

    return NotificationDeliverySummary(
        channel=channel,
        state=state,
        total_count=int(total_count),
        unacknowledged_count=int(unacknowledged_count),
        oldest_unacknowledged_at=oldest_unacknowledged_at,
        activated_at=runtime.activated_at if runtime is not None else None,
        heartbeat_at=runtime.heartbeat_at if runtime is not None else None,
        heartbeat_stale=heartbeat_stale,
        last_success_at=runtime.last_success_at if runtime is not None else None,
        last_failure_at=runtime.last_failure_at if runtime is not None else None,
        last_error=runtime.last_error if runtime is not None else None,
        pending_after_activation=int(pending_after_activation),
        oldest_pending_after_activation_at=oldest_pending_after_activation_at,
        backlog_overdue=backlog_overdue,
        dead_letter_count=int(dead_letter_count),
    )
