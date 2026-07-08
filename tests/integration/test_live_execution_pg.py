"""Live executor placement machinery against a real Postgres, with a fake
trader (no wallet, no network): the crash-safety and idempotency guarantees
that the mock-free unit tests cannot express."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob_trader import ClobTradeError, PlacedOrder
from stratus.collectors.geoblock import GeoblockStatus
from stratus.config import Settings
from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketStatus,
    Order,
    RuntimeControl,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TradeSide,
    WeatherKind,
)
from stratus.jobs.execute_live_signals import cancel_live_orders, execute_live_signals

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime.now(UTC)
NOT_BLOCKED = GeoblockStatus(blocked=False, country="AT", detail="ok")


def _settings(url: str) -> Settings:
    return Settings(
        database_url=url,
        live_legal_approved=True,
        live_tax_approved=True,
        polygon_private_key="0xtestkey",
        live_max_order_usd=25.0,
    )


class FakeTrader:
    def __init__(
        self,
        *,
        result: PlacedOrder | None = None,
        place_error: Exception | None = None,
    ) -> None:
        self.result = result or PlacedOrder(order_id="0xdead", status="live")
        self.place_error = place_error
        self.placed: list[dict[str, object]] = []
        self.canceled: list[str] = []

    async def tick_size(self, token_id: str) -> Decimal:
        return Decimal("0.01")

    async def neg_risk(self, token_id: str) -> bool:
        return False

    async def place_gtc_buy(self, **kwargs: object) -> PlacedOrder:
        if self.place_error is not None:
            raise self.place_error
        self.placed.append(kwargs)
        return self.result

    async def cancel(self, order_ids: list[str]) -> None:
        self.canceled.extend(order_ids)


async def _set_runtime(
    sessions: SessionFactory, *, mode: ExecutionMode, kill: bool = False
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(RuntimeControl)
            .where(RuntimeControl.id == 1)
            .values(mode=mode, kill_switch=kill, halted_reason=None)
        )


async def _pending_signal(sessions: SessionFactory) -> int:
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
            polymarket_id="pm-live-1",
            event_id="ev-1",
            yes_token_id="yes-token",
            no_token_id="no-token",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        signal = Signal(
            market_id=market.id,
            ts=NOW,
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.600000"),
            market_price=Decimal("0.520000"),
            edge=Decimal("0.080000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("20.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.PENDING,
        )
        session.add(signal)
        await session.flush()
        return signal.id


async def test_places_a_live_order_and_marks_the_signal_executed(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _pending_signal(db_sessions)
    trader = FakeTrader(result=PlacedOrder(order_id="0xabc", status="live"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (1, 0, 0)
    assert len(trader.placed) == 1
    # Price snapped down to the tick below the +0.01 cap (0.53).
    assert trader.placed[0]["price"] == Decimal("0.53")

    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        signal = await session.get(Signal, signal_id)
    assert order.mode is ExecutionMode.LIVE
    assert order.clob_order_id == "0xabc"
    assert order.status == "open"
    assert signal is not None and signal.status is SignalStatus.EXECUTED


async def test_readiness_gate_blocks_when_geoblocked(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader()

    result = await execute_live_signals(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
        geoblock=GeoblockStatus(blocked=True, country="US", detail="ok"),
    )

    assert result == (0, 0, 0)
    assert trader.placed == []
    async with db_sessions() as session:
        assert (await session.execute(select(Order))).first() is None


async def test_unknown_outcome_halts_trading(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(place_error=TimeoutError("network timeout"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 1)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
    # The order MAY be live: recorded as unconfirmed, kill switch engaged.
    assert order.status == "submit_unconfirmed"
    assert control is not None and control.kill_switch is True


async def test_explicit_rejection_does_not_halt(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(place_error=ClobTradeError("not enough balance"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 1, 0)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
    assert order.status == "submit_failed"
    assert control is not None and control.kill_switch is False


async def test_orphaned_submitting_order_is_escalated_not_resent(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _pending_signal(db_sessions)
    # Simulate a crash between claim-commit and submit.
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.LIVE,
                clob_order_id=None,
                side=TradeSide.BUY_YES,
                price=Decimal("0.530000"),
                size_usd=Decimal("20.0000"),
                filled_usd=Decimal("0"),
                status="submitting",
            )
        )
    trader = FakeTrader()

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 1)
    assert trader.placed == []  # never re-sent
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
    assert order.status == "submit_unconfirmed"
    assert control is not None and control.kill_switch is True


async def test_cancel_requested_live_orders_are_canceled(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.LIVE,
                clob_order_id="0xlive",
                side=TradeSide.BUY_YES,
                price=Decimal("0.530000"),
                size_usd=Decimal("20.0000"),
                filled_usd=Decimal("0"),
                status="cancel_requested",
            )
        )
    trader = FakeTrader()

    canceled = await cancel_live_orders(
        _settings(migrated_database_url), db_sessions, trader=trader
    )

    assert canceled == 1
    assert trader.canceled == ["0xlive"]
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
    assert order.status == "canceled"
