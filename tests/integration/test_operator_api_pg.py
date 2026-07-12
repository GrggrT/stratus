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
    MarketPrice,
    MarketStatus,
    ModelProb,
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


async def test_market_drilldown_endpoints_return_series_and_fail_soft_orderbook(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="LFPB", name="Paris Le Bourget", city="Paris",
            lat=Decimal("48.96"), lon=Decimal("2.44"), tz="Europe/Paris",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-drill-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6), status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        session.add_all([
            MarketPrice(
                market_id=market.id, ts=NOW - timedelta(minutes=2),
                midpoint=Decimal("0.410000"), best_bid=Decimal("0.40"),
                best_ask=Decimal("0.42"), bid_depth_usd=Decimal("12.5"),
                ask_depth_usd=Decimal("18.0"),
            ),
            MarketPrice(
                market_id=market.id, ts=NOW - timedelta(minutes=1),
                midpoint=Decimal("0.430000"), best_bid=Decimal("0.42"),
                best_ask=Decimal("0.44"), bid_depth_usd=Decimal("14.5"),
                ask_depth_usd=Decimal("20.0"),
            ),
            ModelProb(
                market_id=market.id, ts=NOW - timedelta(minutes=1),
                prob=Decimal("0.610000"), sigma=Decimal("1.2500"),
                horizon_h=Decimal("12.00"), components={},
            ),
            Signal(
                market_id=market.id, ts=NOW, side=TradeSide.BUY_YES,
                model_prob=Decimal("0.62"), market_price=Decimal("0.43"),
                edge=Decimal("0.19"), kelly_frac=Decimal("0.05"),
                size_usd=Decimal("25"), confidence=SignalConfidence.A,
                status=SignalStatus.PENDING,
            ),
        ])

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        prices = await client.get(f"/api/v1/market-timeseries?market_id={market.id}")
        probability = await client.get(
            f"/api/v1/probability-timeseries?market_id={market.id}"
        )
        book = await client.get(f"/api/v1/orderbook?market_id={market.id}")

    assert prices.status_code == 200
    assert [p["midpoint"] for p in prices.json()["points"]] == [0.41, 0.43]
    assert probability.status_code == 200
    probability_points = probability.json()["points"]
    assert {p["source"] for p in probability_points} == {"model", "signal"}
    assert probability_points[-1]["edge"] == 0.19
    assert book.status_code == 200
    assert book.json()["source_status"] == "unavailable"
    assert "token id" in book.json()["error"]


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


async def test_performance_summary_endpoint_supports_paper_robust_metrics(
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
            polymarket_id="pm-paper-summary", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW, status=MarketStatus.RESOLVED, station_verified=True,
        )
        session.add(market)
        await session.flush()
        for pnl, cost in (("100.0", "50.0"), ("-10.0", "25.0"), ("-10.0", "25.0")):
            session.add(
                Position(
                    market_id=market.id, mode=ExecutionMode.PAPER,
                    side=TradeSide.BUY_YES, avg_price=Decimal("0.5"),
                    size_shares=Decimal("50"), size_usd=Decimal(cost),
                    status=PositionStatus.CLOSED, pnl_usd=Decimal(pnl),
                    closed_at=NOW,
                )
            )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/performance-summary?mode=paper")

    assert response.status_code == 200
    body = response.json()
    assert body["total_pnl_usd"] == 80.0
    assert body["roi"] == 0.8
    assert body["robust_pnl_usd"] == -20.0
    assert body["robust_roi"] == -0.4


async def test_edge_report_returns_decomposition_and_gate_checklist(
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
            polymarket_id="pm-edge-1", event_id="ev-1", station_id=station.id,
            market_date=NOW.date(), kind=WeatherKind.HIGH, rules_raw="rules",
            resolves_at=NOW, status=MarketStatus.RESOLVED, station_verified=True,
            resolved_outcome=True,
        )
        session.add(market)
        await session.flush()
        # A resolved winning paper position with a recorded closing line, plus
        # its filled order and signal so the edge report can decompose it.
        position = Position(
            market_id=market.id, mode=ExecutionMode.PAPER, side=TradeSide.BUY_YES,
            avg_price=Decimal("0.5"), size_shares=Decimal("40"),
            size_usd=Decimal("20"), status=PositionStatus.CLOSED,
            pnl_usd=Decimal("20"), clv=Decimal("0.04"), fee_usd=Decimal("0.04"),
            closed_at=NOW,
        )
        session.add(position)
        signal = Signal(
            market_id=market.id, ts=NOW - timedelta(minutes=5), side=TradeSide.BUY_YES,
            model_prob=Decimal("0.7"), market_price=Decimal("0.5"),
            edge=Decimal("0.2"), kelly_frac=Decimal("0.02"), size_usd=Decimal("20"),
            confidence=SignalConfidence.A, status=SignalStatus.EXECUTED,
        )
        session.add(signal)
        await session.flush()
        session.add(
            MarketPrice(
                market_id=market.id, ts=NOW - timedelta(minutes=6),
                best_bid=Decimal("0.590000"), best_ask=Decimal("0.610000"),
                midpoint=Decimal("0.600000"), bid_depth_usd=Decimal("100"),
                ask_depth_usd=Decimal("100"),
            )
        )
        session.add(
            ModelProb(
                market_id=market.id, ts=signal.ts, prob=Decimal("0.700000"),
                sigma=Decimal("1.0000"), horizon_h=Decimal("0.08"),
                components={"shrinkage": {"p_model_raw": 0.8}},
            )
        )
        session.add(
            Order(
                signal_id=signal.id, mode=ExecutionMode.PAPER,
                clob_order_id=f"paper-{signal.id}", side=TradeSide.BUY_YES,
                price=Decimal("0.5"), size_usd=Decimal("20"), filled_usd=Decimal("20"),
                fee_usd=Decimal("0.04"), status="filled", filled_at=NOW,
            )
        )

    app = create_app(Settings(database_url=migrated_database_url))
    app.state.sessions = db_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/edge-report?mode=paper")

    assert response.status_code == 200
    body = response.json()
    assert body["n_resolved_trades"] == 1
    # skill = 40 * 0.04 - 0.04 = 1.56; luck = pnl - skill = 18.44.
    assert body["skill_pnl_usd"] == pytest.approx(1.56)
    assert body["luck_pnl_usd"] == pytest.approx(18.44)
    # The full formal checklist is present and, on a one-trade sample, blocked.
    assert body["gate_passed"] is False
    names = {check["name"] for check in body["gate_checks"]}
    assert names == {
        "stations_calibrated",
        "resolved_positions_200",
        "clv_samples_100",
        "avg_clv_positive",
        "clv_beat_rate_52",
        "roi_ci_excludes_zero",
        "skill_pnl_significant",
        "brier_model_beats_market",
    }
    assert any(not check["passed"] for check in body["gate_checks"])

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        brier_response = await client.get("/api/v1/brier-report?mode=paper")

    assert brier_response.status_code == 200
    brier = brier_response.json()
    assert brier["n_resolved_trades"] == 1
    assert brier["brier_model"] == pytest.approx(0.09)
    assert brier["brier_market"] == pytest.approx(0.16)
    assert brier["brier_raw_model"] == pytest.approx(0.04)
    station = next(
        item for item in brier["slices"]
        if item["dimension"] == "station" and item["value"] == "KLGA"
    )
    assert station["count"] == 1
    assert station["model_minus_market"] == pytest.approx(-0.07)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        clv_response = await client.get("/api/v1/clv-report?mode=paper")

    assert clv_response.status_code == 200
    clv = clv_response.json()
    assert clv["n_closed_positions"] == 1
    assert clv["avg_clv"] == pytest.approx(0.04)
    assert clv["clv_beat_rate"] == pytest.approx(1.0)
    dimensions = {item["dimension"] for item in clv["slices"]}
    assert dimensions == {
        "station", "kind", "horizon", "confidence", "side", "price_band"
    }
    clv_station = next(
        item for item in clv["slices"]
        if item["dimension"] == "station" and item["value"] == "KLGA"
    )
    assert clv_station["count"] == 1
    assert clv_station["avg_clv"] == pytest.approx(0.04)
    assert clv_station["avg_entry_price"] == pytest.approx(0.5)
    # Confidence and horizon come from the representative executed signal.
    confidence = next(item for item in clv["slices"] if item["dimension"] == "confidence")
    assert confidence["value"] == "A"
    horizon = next(item for item in clv["slices"] if item["dimension"] == "horizon")
    assert horizon["value"] == "0-6h"
    band = next(item for item in clv["slices"] if item["dimension"] == "price_band")
    assert band["value"] == "0.50-0.75"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        brier_drilldown_response = await client.get(
            "/api/v1/root-cause-drilldown"
            "?mode=paper&metric=brier&dimension=station&value=KLGA"
        )

    assert brier_drilldown_response.status_code == 200
    brier_drilldown = brier_drilldown_response.json()
    assert brier_drilldown["metric"] == "brier"
    assert brier_drilldown["dimension"] == "station"
    assert brier_drilldown["value"] == "KLGA"
    assert brier_drilldown["count"] == 1
    brier_item = brier_drilldown["items"][0]
    assert brier_item["record_type"] == "order"
    assert brier_item["market_id"] == market.id
    assert brier_item["station_icao"] == "KLGA"
    assert brier_item["model_probability"] == pytest.approx(0.7)
    assert brier_item["market_probability"] == pytest.approx(0.6)
    assert brier_item["raw_model_probability"] == pytest.approx(0.8)
    assert brier_item["brier_delta"] == pytest.approx(-0.07)
    assert brier_item["won"] is True

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        clv_drilldown_response = await client.get(
            "/api/v1/root-cause-drilldown"
            "?mode=paper&metric=clv&dimension=price_band&value=0.50-0.75"
        )

    assert clv_drilldown_response.status_code == 200
    clv_drilldown = clv_drilldown_response.json()
    assert clv_drilldown["metric"] == "clv"
    assert clv_drilldown["dimension"] == "price_band"
    assert clv_drilldown["value"] == "0.50-0.75"
    assert clv_drilldown["count"] == 1
    clv_item = clv_drilldown["items"][0]
    assert clv_item["record_type"] == "position"
    assert clv_item["market_id"] == market.id
    assert clv_item["position_id"] == position.id
    assert clv_item["clv"] == pytest.approx(0.04)
    assert clv_item["closing_line_price"] == pytest.approx(0.54)
    assert clv_item["confidence"] == "A"
    assert clv_item["horizon_bucket"] == "0-6h"

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        actions_response = await client.get("/api/v1/root-cause-actions?mode=paper")

    assert actions_response.status_code == 200
    actions = actions_response.json()
    assert actions["mode"] == "paper"
    action_keys = {
        (item["metric"], item["dimension"], item["value"])
        for item in actions["items"]
    }
    assert ("brier", "station", "KLGA") in action_keys
    assert ("clv", "price_band", "0.50-0.75") in action_keys
    brier_action = next(
        item for item in actions["items"]
        if item["metric"] == "brier"
        and item["dimension"] == "station"
        and item["value"] == "KLGA"
    )
    assert brier_action["action"] == "Watch"
    assert brier_action["severity"] == "warning"
    assert brier_action["count"] == 1
