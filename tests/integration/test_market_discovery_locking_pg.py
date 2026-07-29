from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import Market, MarketStatus, Station, StationStage, WeatherKind
from stratus.jobs.fetch_prices import PackedEventBatch, lock_packed_gamma_events
from stratus.repositories.market_prices import (
    EventPriceCohort,
    PriceTarget,
    mark_prices_fetched,
)
from stratus.repositories.markets import DiscoveredMarket, upsert_market

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


@pytest.mark.asyncio
async def test_price_event_lock_prevents_discovery_deadlock(
    db_sessions: SessionFactory,
) -> None:
    """Price writes must wait nowhere after discovery owns the event lock.

    The production deadlock was:

    * discovery: Gamma event advisory lock -> waits for market row;
    * price cycle: market row -> waits for Gamma event advisory lock.

    The fixed price path takes the event lock before touching the market.  A
    concurrent discovery therefore waits at the event boundary, leaving the
    price transaction free to update and commit before discovery continues.
    """

    suffix = uuid4().hex
    now = datetime.now(UTC)
    event_id = f"discovery-lock-{suffix}"
    async with db_sessions.begin() as session:
        station = Station(
            icao=f"D{suffix[:3].upper()}",
            name="Discovery lock test station",
            city="Lock City",
            lat=Decimal("50.000000"),
            lon=Decimal("8.000000"),
            tz="Europe/Berlin",
            stage=StationStage.PAPER,
            active=True,
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id=f"poly-{suffix}",
            event_id=event_id,
            condition_id=f"condition-{suffix}",
            yes_token_id=f"yes-{suffix}",
            no_token_id=f"no-{suffix}",
            station_id=station.id,
            market_date=now.date(),
            kind=WeatherKind.HIGH,
            bucket_low_c=None,
            bucket_high_c=Decimal("20"),
            resolution_source="https://example.invalid/lock-source",
            rules_raw="lock-order test rules",
            station_verified=True,
            status=MarketStatus.ACTIVE,
            created_at=now - timedelta(hours=2),
            resolves_at=now + timedelta(hours=6),
            fees_enabled=False,
            fee_rate=Decimal("0"),
            fee_exponent=0,
            fee_taker_only=True,
        )
        session.add(market)
        await session.flush()
        market_id = market.id
        station_id = station.id

    target = PriceTarget(
        market_id=market_id,
        event_id=event_id,
        condition_id=f"condition-{suffix}",
        yes_token_id=f"yes-{suffix}",
        no_token_id=f"no-{suffix}",
    )
    packed = PackedEventBatch(cohorts=(EventPriceCohort(event_id=event_id, targets=(target,)),))
    discovered = DiscoveredMarket(
        polymarket_id=f"poly-{suffix}",
        event_id=event_id,
        condition_id=f"condition-{suffix}",
        yes_token_id=f"yes-{suffix}",
        no_token_id=f"no-{suffix}",
        station_id=station_id,
        market_date=now.date(),
        kind=WeatherKind.HIGH,
        bucket_low_c=None,
        bucket_high_c=Decimal("20"),
        resolution_source="https://example.invalid/lock-source",
        rules_raw="lock-order test rules",
        station_verified=True,
        station_verification_reason=None,
        created_at=now - timedelta(hours=2),
        resolves_at=now + timedelta(hours=6),
        fees_enabled=False,
        fee_rate=Decimal("0"),
        fee_exponent=0,
        fee_taker_only=True,
    )

    price_has_event_lock = asyncio.Event()
    allow_price_update = asyncio.Event()
    discovery_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def price_transaction() -> None:
        async with db_sessions.begin() as session:
            await lock_packed_gamma_events(session, packed)
            price_has_event_lock.set()
            await allow_price_update.wait()
            await mark_prices_fetched(session, [market_id], now)

    async def discovery_transaction() -> None:
        await price_has_event_lock.wait()
        async with db_sessions.begin() as session:
            pid = int(await session.scalar(text("SELECT pg_backend_pid()")))
            discovery_pid.set_result(pid)
            await upsert_market(session, discovered)

    price_task = asyncio.create_task(price_transaction())
    discovery_task = asyncio.create_task(discovery_transaction())
    pid = await asyncio.wait_for(discovery_pid, timeout=5)

    waiting_on_advisory = False
    for _ in range(100):
        async with db_sessions() as observer:
            wait_state = (
                await observer.execute(
                    text(
                        "SELECT wait_event_type, wait_event FROM pg_stat_activity WHERE pid = :pid"
                    ),
                    {"pid": pid},
                )
            ).one_or_none()
        if wait_state == ("Lock", "advisory"):
            waiting_on_advisory = True
            break
        await asyncio.sleep(0.01)

    assert waiting_on_advisory
    assert not discovery_task.done()

    allow_price_update.set()
    await asyncio.wait_for(asyncio.gather(price_task, discovery_task), timeout=5)

    async with db_sessions() as session:
        stored = await session.scalar(select(Market).where(Market.id == market_id))
        assert stored is not None
        assert stored.last_price_fetch_at == now
        assert stored.event_id == event_id
        assert stored.station_verified is True
