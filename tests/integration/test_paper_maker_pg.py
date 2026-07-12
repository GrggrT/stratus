"""Maker-simulation persistence: resting orders, fills, TTL expiry."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
    TradeSide,
    WeatherKind,
)
from stratus.repositories.execution import (
    PAPER_STATUS_RESTING,
    PAPER_STATUS_UNFILLED,
    MakerFillValue,
    RestingPaperOrder,
    expire_pending_signals,
    expire_resting_paper_orders,
    insert_resting_paper_orders,
    persist_maker_fills,
    resting_paper_orders,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


async def _seed_signal(session: AsyncSession, *, suffix: str) -> Signal:
    station = Station(
        icao="KLGA", name="LaGuardia", city="New York City",
        lat=Decimal("40.77"), lon=Decimal("-73.87"), tz="America/New_York",
    )
    session.add(station)
    await session.flush()
    market = Market(
        polymarket_id=f"pm-maker-{suffix}", event_id="ev-1", station_id=station.id,
        market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
        resolves_at=NOW + timedelta(hours=6), status=MarketStatus.ACTIVE,
        station_verified=True,
    )
    session.add(market)
    await session.flush()
    signal = Signal(
        market_id=market.id, ts=NOW, side=TradeSide.BUY_YES,
        model_prob=Decimal("0.65"), market_price=Decimal("0.58"),
        edge=Decimal("0.07"), kelly_frac=Decimal("0.02"), size_usd=Decimal("22"),
        confidence=SignalConfidence.B, status=SignalStatus.PENDING,
    )
    session.add(signal)
    await session.flush()
    return signal


async def test_maker_roundtrip_posts_fills_and_aggregates_position(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        signal = await _seed_signal(session, suffix="1")
        signal_id, market_id = signal.id, signal.market_id
        posted = await insert_resting_paper_orders(
            session,
            [
                RestingPaperOrder(
                    signal_id=signal_id,
                    side=TradeSide.BUY_YES,
                    limit_price=Decimal("0.55"),
                    size_usd=Decimal("22"),
                )
            ],
        )
        assert posted == 1
        # A second post for the same signal is a no-op (uq_orders_signal_mode).
        assert (
            await insert_resting_paper_orders(
                session,
                [
                    RestingPaperOrder(
                        signal_id=signal_id,
                        side=TradeSide.BUY_YES,
                        limit_price=Decimal("0.55"),
                        size_usd=Decimal("22"),
                    )
                ],
            )
            == 0
        )

    async with db_sessions.begin() as session:
        resting = await resting_paper_orders(session)
        assert signal_id in resting
        order_id = resting[signal_id].id
        filled = await persist_maker_fills(
            session,
            [
                MakerFillValue(
                    order_id=order_id,
                    signal_id=signal_id,
                    market_id=market_id,
                    side=TradeSide.BUY_YES,
                    limit_price=Decimal("0.55"),
                    size_shares=Decimal("40"),
                    filled_usd=Decimal("22"),
                    fee_usd=Decimal("0"),
                    total_cost_usd=Decimal("22"),
                    filled_at=NOW,
                )
            ],
        )
        assert filled == 1

    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        assert order is not None
        assert order.status == "filled"
        assert order.filled_usd == Decimal("22")
        signal_row = await session.get(Signal, signal_id)
        assert signal_row is not None
        assert signal_row.status is SignalStatus.EXECUTED
        position = (
            await session.execute(
                select(Position).where(Position.market_id == market_id)
            )
        ).scalar_one()
        assert position.status is PositionStatus.OPEN
        assert position.avg_price == Decimal("0.55")
        assert position.size_shares == Decimal("40")
        assert position.size_usd == Decimal("22")


async def test_maker_ttl_expiry_marks_order_unfilled(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        signal = await _seed_signal(session, suffix="2")
        signal_id = signal.id
        await insert_resting_paper_orders(
            session,
            [
                RestingPaperOrder(
                    signal_id=signal_id,
                    side=TradeSide.BUY_YES,
                    limit_price=Decimal("0.55"),
                    size_usd=Decimal("22"),
                )
            ],
        )

    async with db_sessions.begin() as session:
        expired = await expire_pending_signals(
            session, [signal_id], reason="unfilled_maker"
        )
        expired_orders = await expire_resting_paper_orders(session, [signal_id])
        assert expired == 1
        assert expired_orders == 1

    async with db_sessions() as session:
        signal_row = await session.get(Signal, signal_id)
        assert signal_row is not None
        assert signal_row.status is SignalStatus.EXPIRED
        assert signal_row.skip_reason == "unfilled_maker"
        order = (
            await session.execute(
                select(Order).where(Order.signal_id == signal_id)
            )
        ).scalar_one()
        assert order.status == PAPER_STATUS_UNFILLED
        assert order.status != PAPER_STATUS_RESTING
