from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.clob import PriceSnapshot
from stratus.db.models import Market, MarketPrice, MarketStatus


@dataclass(frozen=True, slots=True)
class PriceTarget:
    market_id: int
    token_id: str


async def markets_due_for_price_fetch(
    session: AsyncSession, now: datetime
) -> list[PriceTarget]:
    near_resolution = Market.resolves_at <= now + timedelta(hours=6)
    due_near = and_(
        near_resolution,
        or_(
            Market.last_price_fetch_at.is_(None),
            Market.last_price_fetch_at <= now - timedelta(minutes=1),
        ),
    )
    due_far = and_(
        ~near_resolution,
        or_(
            Market.last_price_fetch_at.is_(None),
            Market.last_price_fetch_at <= now - timedelta(minutes=10),
        ),
    )
    statement = select(Market.id, Market.yes_token_id).where(
        Market.status == MarketStatus.ACTIVE,
        Market.station_verified.is_(True),
        Market.yes_token_id.is_not(None),
        or_(due_near, due_far),
    )
    rows = (await session.execute(statement)).all()
    return [PriceTarget(market_id=row.id, token_id=row.yes_token_id) for row in rows]


async def mark_prices_fetched(
    session: AsyncSession, market_ids: list[int], fetched_at: datetime
) -> None:
    if market_ids:
        await session.execute(
            update(Market)
            .where(Market.id.in_(market_ids))
            .values(last_price_fetch_at=fetched_at)
        )


async def upsert_market_prices(
    session: AsyncSession,
    snapshots_by_market: list[tuple[int, PriceSnapshot]],
) -> int:
    if not snapshots_by_market:
        return 0
    rows = [
        {
            "market_id": market_id,
            "ts": snapshot.ts,
            "midpoint": snapshot.midpoint,
            "best_bid": snapshot.best_bid,
            "best_ask": snapshot.best_ask,
            "bid_depth_usd": snapshot.bid_depth_usd,
            "ask_depth_usd": snapshot.ask_depth_usd,
        }
        for market_id, snapshot in snapshots_by_market
    ]
    statement = insert(MarketPrice).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_market_prices_market_time",
        set_={
            "midpoint": statement.excluded.midpoint,
            "best_bid": statement.excluded.best_bid,
            "best_ask": statement.excluded.best_ask,
            "bid_depth_usd": statement.excluded.bid_depth_usd,
            "ask_depth_usd": statement.excluded.ask_depth_usd,
        },
    )
    await session.execute(statement)
    return len(rows)
