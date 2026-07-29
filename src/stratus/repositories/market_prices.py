from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.clob import PriceSnapshot
from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    Market,
    MarketPrice,
    MarketStatus,
    Station,
    StationStage,
)


@dataclass(frozen=True, slots=True)
class PriceTarget:
    market_id: int
    event_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str

    @property
    def token_id(self) -> str:
        """Legacy YES-token alias for callers not yet migrated to paired books."""

        return self.yes_token_id


@dataclass(frozen=True, slots=True)
class EventPriceCohort:
    event_id: str
    targets: tuple[PriceTarget, ...]

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(
            token_id
            for target in self.targets
            for token_id in (target.yes_token_id, target.no_token_id)
        )


def group_price_targets_by_event(targets: list[PriceTarget]) -> tuple[EventPriceCohort, ...]:
    grouped: dict[str, list[PriceTarget]] = {}
    market_ids: set[int] = set()
    for target in targets:
        if target.market_id in market_ids:
            raise ValueError(f"duplicate price target market {target.market_id}")
        market_ids.add(target.market_id)
        if (
            not target.event_id
            or not target.condition_id
            or not target.yes_token_id
            or not target.no_token_id
            or target.yes_token_id == target.no_token_id
        ):
            raise ValueError(f"invalid paired price target market {target.market_id}")
        grouped.setdefault(target.event_id, []).append(target)

    cohorts: list[EventPriceCohort] = []
    all_tokens: set[str] = set()
    for event_id in sorted(grouped):
        event_targets = tuple(sorted(grouped[event_id], key=lambda item: item.market_id))
        tokens = [
            token_id
            for target in event_targets
            for token_id in (target.yes_token_id, target.no_token_id)
        ]
        if len(set(tokens)) != len(tokens):
            raise ValueError(f"event {event_id} reuses a CLOB token identity")
        duplicate_across_events = all_tokens.intersection(tokens)
        if duplicate_across_events:
            raise ValueError(
                f"CLOB tokens occur in multiple events: {sorted(duplicate_across_events)}"
            )
        all_tokens.update(tokens)
        cohorts.append(EventPriceCohort(event_id=event_id, targets=event_targets))
    return tuple(cohorts)


async def markets_due_for_price_fetch(session: AsyncSession, now: datetime) -> list[PriceTarget]:
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
    due_shadow = or_(
        Market.last_price_fetch_at.is_(None),
        Market.last_price_fetch_at <= now - timedelta(hours=1),
    )
    trading_stage = Station.stage.in_((StationStage.PAPER, StationStage.APPROVED))
    due_events_statement = (
        select(Market.event_id)
        .distinct()
        .join(Station, Market.station_id == Station.id)
        .where(
            Market.status == MarketStatus.ACTIVE,
            Market.resolves_at > now,
            Market.station_verified.is_(True),
            Market.condition_id.is_not(None),
            Market.yes_token_id.is_not(None),
            Market.no_token_id.is_not(None),
            Market.yes_token_id != Market.no_token_id,
            Station.active.is_(True),
            or_(
                and_(trading_stage, or_(due_near, due_far)),
                and_(Station.stage == StationStage.SHADOW, due_shadow),
            ),
        )
    )
    due_event_ids = tuple((await session.scalars(due_events_statement)).all())
    if not due_event_ids:
        return []

    # A single due bucket makes the complete future persisted event universe
    # due.  A later formal benchmark can therefore prove that all unresolved
    # sibling markets came from whole exchange responses rather than
    # independently timed requests.
    siblings_statement = (
        select(
            Market.id,
            Market.event_id,
            Market.condition_id,
            Market.yes_token_id,
            Market.no_token_id,
        )
        .where(
            Market.event_id.in_(due_event_ids),
            Market.resolves_at > now,
            Market.condition_id.is_not(None),
            Market.yes_token_id.is_not(None),
            Market.no_token_id.is_not(None),
            Market.yes_token_id != Market.no_token_id,
        )
        .order_by(Market.event_id, Market.id)
    )
    rows = (await session.execute(siblings_statement)).all()
    return [
        PriceTarget(
            market_id=row.id,
            event_id=row.event_id,
            condition_id=row.condition_id,
            yes_token_id=row.yes_token_id,
            no_token_id=row.no_token_id,
        )
        for row in rows
    ]


async def mark_prices_fetched(
    session: AsyncSession, market_ids: list[int], fetched_at: datetime
) -> None:
    if market_ids:
        await session.execute(
            update(Market).where(Market.id.in_(market_ids)).values(last_price_fetch_at=fetched_at)
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


async def project_eligible_clob_prices(
    session: AsyncSession,
    batch: ClobBookBatch,
    snapshots: list[ClobMarketSnapshot] | tuple[ClobMarketSnapshot, ...],
) -> int:
    """Write the legacy operational view from eligible dual-token evidence.

    This mutable compatibility table is intentionally not formal evidence.
    Formal consumers must retain ``clob_market_snapshots.id`` provenance.
    """

    rows: list[dict[str, object]] = []
    for snapshot in snapshots:
        if not snapshot.eligible:
            continue
        if snapshot.batch_id != batch.id or any(
            value is None
            for value in (
                snapshot.binary_yes_probability,
                snapshot.yes_best_bid,
                snapshot.yes_best_ask,
                snapshot.yes_bid_depth_usd,
                snapshot.yes_ask_depth_usd,
            )
        ):
            raise ValueError("eligible CLOB snapshot has incomplete operational projection")
        rows.append(
            {
                "market_id": snapshot.market_id,
                "ts": batch.first_seen_at,
                "midpoint": snapshot.binary_yes_probability,
                "best_bid": snapshot.yes_best_bid,
                "best_ask": snapshot.yes_best_ask,
                "bid_depth_usd": snapshot.yes_bid_depth_usd,
                "ask_depth_usd": snapshot.yes_ask_depth_usd,
            }
        )
    if not rows:
        return 0
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
