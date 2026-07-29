from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import Market, Order, Signal, SignalConfidence, SignalStatus, TradeSide
from stratus.db.session import dml_rowcount
from stratus.services.clob_evidence import LEGACY_MARKET_PROTOCOL


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
    clob_snapshot_id: int | None = None
    station_reliability_snapshot_id: int | None = None
    token_id: str | None = None
    benchmark_protocol: str = LEGACY_MARKET_PROTOCOL


async def insert_signals(
    session: AsyncSession,
    values: list[SignalValue],
    *,
    skipped_heartbeat: timedelta | None = None,
) -> int:
    if not values:
        return 0
    if skipped_heartbeat is not None:
        market_ids = {value.market_id for value in values}
        latest_rows = (
            (
                await session.execute(
                    select(Signal)
                    .where(Signal.market_id.in_(market_ids))
                    .distinct(Signal.market_id)
                    .order_by(Signal.market_id, Signal.ts.desc())
                )
            )
            .scalars()
            .all()
        )
        latest_by_market = {row.market_id: row for row in latest_rows}
        values = [
            value
            for value in values
            if not _is_repeated_skip(
                value,
                latest_by_market.get(value.market_id),
                skipped_heartbeat,
            )
        ]
        if not values:
            return 0
    statement = insert(Signal).values([asdict(value) for value in values])
    statement = statement.on_conflict_do_nothing(constraint="uq_signals_market_time")
    result = await session.execute(statement)
    return dml_rowcount(result)


def _is_repeated_skip(
    value: SignalValue,
    latest: Signal | None,
    heartbeat: timedelta,
) -> bool:
    return bool(
        value.status is SignalStatus.SKIPPED
        and latest is not None
        and latest.status is SignalStatus.SKIPPED
        and latest.side is value.side
        and latest.skip_reason == value.skip_reason
        and value.ts < latest.ts + heartbeat
    )


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


async def expire_unclaimed_pending_signals_for_events(
    session: AsyncSession,
    event_ids: set[str] | frozenset[str],
    *,
    reason: str,
) -> int:
    """Revoke only pending decisions that have no durable execution claim.

    Existing Order/Intent rows remain owned by their execution TTL.  The
    second NOT EXISTS check closes the race with an execution claim attempted
    after the candidate query but before the update.
    """

    if not event_ids:
        return 0
    has_order = exists(select(Order.id).where(Order.signal_id == Signal.id))
    signal_ids = list(
        (
            await session.scalars(
                select(Signal.id)
                .join(Market, Market.id == Signal.market_id)
                .where(
                    Market.event_id.in_(event_ids),
                    Signal.status == SignalStatus.PENDING,
                    ~has_order,
                )
                .with_for_update(of=Signal, skip_locked=True)
            )
        ).all()
    )
    if not signal_ids:
        return 0
    result = await session.execute(
        update(Signal)
        .where(
            Signal.id.in_(signal_ids),
            Signal.status == SignalStatus.PENDING,
            ~has_order,
        )
        .values(status=SignalStatus.EXPIRED, skip_reason=reason)
    )
    return dml_rowcount(result)
