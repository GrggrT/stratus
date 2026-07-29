from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketStatus,
    ModelProb,
    Order,
    SelectionPolicyDecision,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TradeSide,
    WeatherKind,
)
from stratus.repositories.selection_policy import (
    capture_selection_policy_decisions,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
PIPELINE = "stratus-accuracy-v4-test+0123456789abcdef"


async def test_selection_decisions_are_immutable_and_pre_resolution(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
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
            polymarket_id="selection-ledger-1",
            event_id="selection-event-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=2),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        signal = Signal(
            market_id=market.id,
            ts=NOW - timedelta(minutes=10),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.70"),
            market_price=Decimal("0.50"),
            edge=Decimal("0.20"),
            kelly_frac=Decimal("0.02"),
            size_usd=Decimal("20"),
            confidence=SignalConfidence.A,
            status=SignalStatus.EXECUTED,
        )
        session.add(signal)
        session.add(
            ModelProb(
                market_id=market.id,
                ts=signal.ts,
                prob=Decimal("0.70"),
                sigma=Decimal("1.0"),
                horizon_h=Decimal("2.0"),
                components={"pipeline": {"cohort": PIPELINE}},
            )
        )
        await session.flush()
        order = Order(
            signal_id=signal.id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{signal.id}",
            side=TradeSide.BUY_YES,
            price=Decimal("0.50"),
            size_usd=Decimal("20"),
            filled_usd=Decimal("20"),
            fee_usd=Decimal("0"),
            status="filled",
            filled_at=NOW - timedelta(minutes=1),
        )
        session.add(order)
        await session.flush()

        assert await capture_selection_policy_decisions(session, now=NOW) == 6
        assert await capture_selection_policy_decisions(session, now=NOW) == 0
        order_id = order.id

    async with db_sessions() as session:
        rows = (
            await session.execute(
                select(SelectionPolicyDecision).order_by(
                    SelectionPolicyDecision.policy_name
                )
            )
        ).scalars().all()
        assert len(rows) == 6
        assert all(row.pipeline_version == PIPELINE for row in rows)
        assert {row.order_id for row in rows} == {order_id}

    async with db_sessions.begin() as session:
        stored_market = await session.scalar(
            select(Market).where(Market.polymarket_id == "selection-ledger-1")
        )
        assert stored_market is not None
        stored_market.status = MarketStatus.RESOLVED
        stored_market.resolved_outcome = True
        assert (
            await capture_selection_policy_decisions(
                session,
                now=NOW + timedelta(minutes=1),
            )
            == 0
        )
        stored = (
            await session.execute(select(SelectionPolicyDecision))
        ).scalars().all()
        assert len(stored) == 6
