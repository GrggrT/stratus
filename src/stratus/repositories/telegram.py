from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    Market,
    MarketStatus,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TelegramDelivery,
)


@dataclass(frozen=True, slots=True)
class PendingDelivery:
    id: int
    payload: str


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
    return bool(result.rowcount)


def _bucket(market: Market) -> str:
    low = "−∞" if market.bucket_low_c is None else f"{market.bucket_low_c:g}"
    high = "∞" if market.bucket_high_c is None else f"{market.bucket_high_c:g}"
    return f"[{low}, {high})°C"


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
    order_rows = (
        await session.execute(
            select(Order, Signal, Market, Station)
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .join(Station, Market.station_id == Station.id)
            .where(Order.filled_at >= since)
        )
    ).all()
    for order, _signal, market, station in order_rows:
        values.append(
            {
                "event_key": f"fill:{order.id}",
                "payload": (
                    f"Fill · {order.mode.value} {order.side.value}\n"
                    f"{station.icao} {market.market_date} {_bucket(market)}\n"
                    f"${order.filled_usd:.2f} at {order.price:.3f}; "
                    f"fee ${order.fee_usd or 0:.4f} ({order.status})"
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
    return result.rowcount or 0


async def pending_deliveries(
    session: AsyncSession, limit: int = 20
) -> list[PendingDelivery]:
    rows = (
        await session.execute(
            select(TelegramDelivery)
            .where(TelegramDelivery.sent_at.is_(None), TelegramDelivery.attempts < 5)
            .order_by(TelegramDelivery.id)
            .limit(limit)
        )
    ).scalars().all()
    return [PendingDelivery(row.id, row.payload) for row in rows]


async def mark_delivery_sent(
    session: AsyncSession, delivery_id: int, sent_at: datetime
) -> None:
    await session.execute(
        update(TelegramDelivery)
        .where(TelegramDelivery.id == delivery_id)
        .values(sent_at=sent_at, error=None)
    )


async def mark_delivery_failed(
    session: AsyncSession, delivery_id: int, error: str
) -> None:
    await session.execute(
        update(TelegramDelivery)
        .where(TelegramDelivery.id == delivery_id)
        .values(
            attempts=TelegramDelivery.attempts + 1,
            error=error[:1000],
        )
    )
