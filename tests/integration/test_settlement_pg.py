"""Settlement across both execution modes against a real Postgres."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import (
    BankrollLog,
    ExecutionMode,
    Market,
    MarketPrice,
    MarketStatus,
    Position,
    PositionStatus,
    Station,
    TelegramDelivery,
    TradeSide,
    WeatherKind,
)
from stratus.repositories.settlement import settle_resolved_markets

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 6, 18, tzinfo=UTC)


async def _market_with_position(
    sessions: SessionFactory, *, mode: ExecutionMode, side: TradeSide
) -> int:
    async with sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id=f"pm-{mode.value}-{side.value}",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW - timedelta(hours=1),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        session.add(
            Position(
                market_id=market.id,
                mode=mode,
                side=side,
                avg_price=Decimal("0.500000"),
                size_shares=Decimal("40.000000"),
                size_usd=Decimal("20.0000"),
                status=PositionStatus.OPEN,
            )
        )
        return market.id


async def test_paper_settlement_writes_a_bankroll_row(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    market_id = await _market_with_position(
        db_sessions, mode=ExecutionMode.PAPER, side=TradeSide.BUY_YES
    )
    async with db_sessions.begin() as session:
        result = await settle_resolved_markets(
            session,
            resolutions={market_id: True},  # YES wins
            closing_times={market_id: NOW},
            settled_at=NOW,
            initial_bankroll_usd=Decimal("1080"),
        )

    assert (result.resolved, result.paper_positions, result.live_positions) == (1, 1, 0)
    # 40 shares pay $40, cost $20 -> +$20 PnL.
    assert result.paper_pnl == Decimal("20.0000")
    async with db_sessions() as session:
        bankroll = (
            await session.execute(
                select(BankrollLog).where(BankrollLog.mode == ExecutionMode.PAPER)
            )
        ).scalar_one()
        position = (await session.execute(select(Position))).scalar_one()
    assert bankroll.balance_usd == Decimal("1100.0000")
    assert position.status is PositionStatus.CLOSED
    assert position.pnl_usd == Decimal("20.0000")


async def test_settlement_uses_latest_pre_cutoff_price_for_clv(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    market_id = await _market_with_position(
        db_sessions, mode=ExecutionMode.PAPER, side=TradeSide.BUY_NO
    )
    async with db_sessions.begin() as session:
        session.add_all(
            [
                MarketPrice(
                    market_id=market_id,
                    ts=NOW - timedelta(minutes=25),
                    best_bid=Decimal("0.290000"),
                    best_ask=Decimal("0.310000"),
                    midpoint=Decimal("0.300000"),
                    bid_depth_usd=Decimal("100.0000"),
                    ask_depth_usd=Decimal("100.0000"),
                ),
                MarketPrice(
                    market_id=market_id,
                    ts=NOW - timedelta(minutes=5),
                    best_bid=Decimal("0.010000"),
                    best_ask=Decimal("0.020000"),
                    midpoint=Decimal("0.015000"),
                    bid_depth_usd=Decimal("100.0000"),
                    ask_depth_usd=Decimal("100.0000"),
                ),
            ]
        )
        await settle_resolved_markets(
            session,
            resolutions={market_id: False},  # NO wins
            closing_times={market_id: NOW},
            settled_at=NOW,
            initial_bankroll_usd=Decimal("1080"),
        )

    async with db_sessions() as session:
        position = (await session.execute(select(Position))).scalar_one()

    # BUY_NO side-adjusts the YES midpoint: 1 - 0.30 - 0.50 entry.
    # The later post-cutoff price must not be used.
    assert position.clv == Decimal("0.200000")


async def test_live_settlement_records_pnl_and_a_redemption_reminder(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    market_id = await _market_with_position(
        db_sessions, mode=ExecutionMode.LIVE, side=TradeSide.BUY_YES
    )
    async with db_sessions.begin() as session:
        result = await settle_resolved_markets(
            session,
            resolutions={market_id: True},  # YES wins -> redeemable
            closing_times={market_id: NOW},
            settled_at=NOW,
            initial_bankroll_usd=Decimal("1080"),
        )

    assert (result.live_positions, result.paper_positions) == (1, 0)
    assert result.live_pnl == Decimal("20.0000")
    async with db_sessions() as session:
        # No synthetic live bankroll row: the real balance changes on redeem.
        live_bankroll = (
            await session.execute(
                select(BankrollLog).where(BankrollLog.mode == ExecutionMode.LIVE)
            )
        ).first()
        reminders = (
            (await session.execute(select(TelegramDelivery))).scalars().all()
        )
        position = (await session.execute(select(Position))).scalar_one()
    assert live_bankroll is None
    assert position.status is PositionStatus.CLOSED
    assert any("redeem" in r.payload.lower() for r in reminders)


async def test_losing_live_position_settles_without_a_reminder(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    market_id = await _market_with_position(
        db_sessions, mode=ExecutionMode.LIVE, side=TradeSide.BUY_YES
    )
    async with db_sessions.begin() as session:
        result = await settle_resolved_markets(
            session,
            resolutions={market_id: False},  # YES loses -> nothing to redeem
            closing_times={market_id: NOW},
            settled_at=NOW,
            initial_bankroll_usd=Decimal("1080"),
        )

    assert result.live_pnl == Decimal("-20.0000")
    async with db_sessions() as session:
        reminders = (
            (await session.execute(select(TelegramDelivery))).scalars().all()
        )
    assert reminders == []
