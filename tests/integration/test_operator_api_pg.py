"""Operator monitoring endpoints whose SQL joins need a real Postgres."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    ExecutionMode,
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

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 7, 12, tzinfo=UTC)


async def test_orders_endpoint_joins_order_signal_market_station(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA", name="LaGuardia", city="New York City",
            lat=Decimal("40.77"), lon=Decimal("-73.87"), tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-orders-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6), status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        signal = Signal(
            market_id=market.id, ts=NOW, side=TradeSide.BUY_YES,
            model_prob=Decimal("0.6"), market_price=Decimal("0.52"), edge=Decimal("0.08"),
            kelly_frac=Decimal("0.01"), size_usd=Decimal("20"),
            confidence=SignalConfidence.B, status=SignalStatus.EXECUTED,
        )
        session.add(signal)
        await session.flush()
        session.add(
            Order(
                signal_id=signal.id, mode=ExecutionMode.LIVE, clob_order_id="0xlive",
                side=TradeSide.BUY_YES, price=Decimal("0.53"), size_usd=Decimal("20"),
                filled_usd=Decimal("20"), fee_usd=Decimal("0.04"), status="filled",
                filled_at=NOW,
            )
        )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/orders")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["mode"] == "live"
    assert item["clob_order_id"] == "0xlive"
    assert item["station_icao"] == "KLGA"
    assert item["polymarket_id"] == "pm-orders-1"


async def test_positions_endpoint_reports_mark_to_market(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    from stratus.db.models import MarketPrice

    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA", name="LaGuardia", city="New York City",
            lat=Decimal("40.77"), lon=Decimal("-73.87"), tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-mtm-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6), status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        # Open BUY_NO position: 40 shares at 0.50 = $20 cost.
        session.add(
            Position(
                market_id=market.id, mode=ExecutionMode.PAPER, side=TradeSide.BUY_NO,
                avg_price=Decimal("0.5"), size_shares=Decimal("40"),
                size_usd=Decimal("20"), status=PositionStatus.OPEN,
            )
        )
        # Latest YES midpoint 0.40 -> NO side mid 0.60 -> mark $24, +$4.
        session.add(
            MarketPrice(
                market_id=market.id, ts=NOW, midpoint=Decimal("0.400000"),
                best_bid=Decimal("0.39"), best_ask=Decimal("0.41"),
                bid_depth_usd=Decimal("100"), ask_depth_usd=Decimal("100"),
            )
        )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/positions")

    item = response.json()["items"][0]
    assert item["mark_price"] == 0.6
    assert item["unrealized_pnl_usd"] == 4.0
    assert item["market_date"] == str(NOW.date())
    assert item["kind"] == "high"


async def test_equity_endpoint_filters_by_mode(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA", name="LaGuardia", city="New York City",
            lat=Decimal("40.77"), lon=Decimal("-73.87"), tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-eqm-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW, status=MarketStatus.RESOLVED, station_verified=True,
        )
        session.add(market)
        await session.flush()
        for mode, pnl in ((ExecutionMode.PAPER, "12.5"), (ExecutionMode.LIVE, "-3.0")):
            session.add(
                Position(
                    market_id=market.id, mode=mode, side=TradeSide.BUY_YES,
                    avg_price=Decimal("0.5"), size_shares=Decimal("40"),
                    size_usd=Decimal("20"), status=PositionStatus.CLOSED,
                    pnl_usd=Decimal(pnl), closed_at=NOW + timedelta(hours=1),
                )
            )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        paper = await client.get("/api/v1/equity?mode=paper")
        live = await client.get("/api/v1/equity?mode=live")
        bad = await client.get("/api/v1/equity?mode=yolo")

    assert [p["cumulative_pnl_usd"] for p in paper.json()["points"]] == [12.5]
    assert [p["cumulative_pnl_usd"] for p in live.json()["points"]] == [-3.0]
    assert bad.status_code == 422


async def test_live_equity_endpoint_returns_cumulative_series(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA", name="LaGuardia", city="New York City",
            lat=Decimal("40.77"), lon=Decimal("-73.87"), tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-eq-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW, status=MarketStatus.RESOLVED, station_verified=True,
        )
        session.add(market)
        await session.flush()
        for offset, pnl in ((1, "20.0"), (2, "-5.0")):
            session.add(
                Position(
                    market_id=market.id, mode=ExecutionMode.LIVE, side=TradeSide.BUY_YES,
                    avg_price=Decimal("0.5"), size_shares=Decimal("40"), size_usd=Decimal("20"),
                    status=PositionStatus.CLOSED, pnl_usd=Decimal(pnl),
                    closed_at=NOW + timedelta(hours=offset),
                )
            )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/live-equity")

    assert response.status_code == 200
    points = response.json()["points"]
    assert [p["cumulative_pnl_usd"] for p in points] == [20.0, 15.0]
