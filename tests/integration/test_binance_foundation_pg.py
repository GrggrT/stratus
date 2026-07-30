from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    CryptoCandle,
    CryptoMarketSnapshot,
    CryptoSymbol,
    OperatorCommand,
    RuntimeControl,
    TradingVenue,
    VenueMode,
)
from stratus.jobs.process_operator_commands import process_operator_commands
from stratus.repositories.operator_commands import enqueue_command
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    halt_venue,
    list_venue_runtime_controls,
    set_venue_enabled,
    set_venue_kill_switch,
    set_venue_mode,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


async def test_seeded_venue_controls_are_safe(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions() as session:
        controls = await list_venue_runtime_controls(session)

    assert [control.venue for control in controls] == [
        TradingVenue.POLYMARKET,
        TradingVenue.BINANCE_SPOT,
    ]
    polymarket, binance = controls
    assert polymarket.enabled is True
    assert polymarket.mode is VenueMode.PAPER
    assert polymarket.kill_switch is False
    assert binance.enabled is False
    assert binance.mode is VenueMode.DISABLED
    assert binance.kill_switch is True
    assert binance.risk_config_version == "v1-conservative"


async def test_binance_control_changes_do_not_touch_polymarket(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        legacy_before = await session.get(RuntimeControl, 1)
        assert legacy_before is not None
        legacy_state = (legacy_before.mode, legacy_before.kill_switch)
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.SHADOW)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)

    async with db_sessions() as session:
        binance = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        polymarket = await get_venue_runtime_control(session, TradingVenue.POLYMARKET)
        legacy_after = await session.get(RuntimeControl, 1)

    assert (binance.enabled, binance.kill_switch, binance.mode) == (
        True,
        False,
        VenueMode.SHADOW,
    )
    assert (polymarket.enabled, polymarket.kill_switch, polymarket.mode) == (
        True,
        False,
        VenueMode.PAPER,
    )
    assert legacy_after is not None
    assert (legacy_after.mode, legacy_after.kill_switch) == legacy_state


async def test_halted_venue_cannot_be_resumed(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await halt_venue(session, TradingVenue.BINANCE_SPOT, "reconciliation mismatch")

    async with db_sessions.begin() as session:
        with pytest.raises(RuntimeError, match="reconciliation mismatch"):
            await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)


async def test_crypto_market_data_round_trips(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        symbol = CryptoSymbol(
            venue=TradingVenue.BINANCE_SPOT,
            symbol="BTCUSDT",
            base_asset="BTC",
            quote_asset="USDT",
            status="TRADING",
            enabled=True,
            price_tick=Decimal("0.010000000000000000"),
            quantity_step=Decimal("0.000010000000000000"),
            minimum_quantity=Decimal("0.000010000000000000"),
            minimum_notional=Decimal("5.000000000000000000"),
            supports_oco=True,
            supports_oto=True,
            filters={"PRICE_FILTER": {"tickSize": "0.01"}},
            filter_hash="a" * 64,
            fetched_at=NOW,
        )
        session.add(symbol)
        await session.flush()
        session.add(
            CryptoCandle(
                symbol_id=symbol.id,
                interval="1m",
                open_time=NOW,
                close_time=NOW + timedelta(minutes=1) - timedelta(milliseconds=1),
                open_price=Decimal("100000.000000000000000000"),
                high_price=Decimal("100100.000000000000000000"),
                low_price=Decimal("99900.000000000000000000"),
                close_price=Decimal("100050.000000000000000000"),
                base_volume=Decimal("1.250000000000000000"),
                quote_volume=Decimal("125000.000000000000000000"),
                trade_count=100,
                taker_buy_base_volume=Decimal("0.700000000000000000"),
                taker_buy_quote_volume=Decimal("70000.000000000000000000"),
                closed=True,
            )
        )
        session.add(
            CryptoMarketSnapshot(
                symbol_id=symbol.id,
                snapshot_type="book_ticker",
                update_id=42,
                event_time=NOW,
                received_at=NOW + timedelta(milliseconds=20),
                best_bid=Decimal("100049.990000000000000000"),
                best_bid_quantity=Decimal("0.500000000000000000"),
                best_ask=Decimal("100050.000000000000000000"),
                best_ask_quantity=Decimal("0.400000000000000000"),
                spread_bps=Decimal("0.00099950"),
                depth_valid=False,
            )
        )

    async with db_sessions() as session:
        stored_symbol = (await session.execute(select(CryptoSymbol))).scalar_one()
        candle = (await session.execute(select(CryptoCandle))).scalar_one()
        snapshot = (await session.execute(select(CryptoMarketSnapshot))).scalar_one()

    assert stored_symbol.symbol == "BTCUSDT"
    assert candle.close_price == Decimal("100050.000000000000000000")
    assert candle.closed is True
    assert snapshot.update_id == 42
    assert snapshot.best_ask == Decimal("100050.000000000000000000")


async def test_venues_endpoint_exposes_isolated_controls(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/venues")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["venue"] for item in items] == ["polymarket", "binance_spot"]
    assert items[1]["mode"] == "disabled"
    assert items[1]["kill_switch"] is True


async def test_binance_operator_commands_are_scoped_and_live_locked(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key="secret-key",
        binance_enabled=True,
    )

    async def run_command(
        request_id: str, command: str, payload: dict[str, str]
    ) -> tuple[int, int]:
        async with db_sessions.begin() as session:
            await enqueue_command(
                session,
                request_id=request_id,
                command=command,
                payload=payload,
                actor="integration",
                reason="Binance venue isolation test",
            )
        return await process_operator_commands(settings, db_sessions)

    assert await run_command(
        "req-binance-mode-shadow", "set_mode", {"venue": "binance_spot", "mode": "shadow"}
    ) == (1, 1)
    async with db_sessions() as session:
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
    assert (control.enabled, control.mode, control.kill_switch) == (
        True,
        VenueMode.SHADOW,
        True,
    )

    assert await run_command("req-binance-resume", "resume", {"venue": "binance_spot"}) == (1, 1)
    assert await run_command("req-binance-stop", "stop", {"venue": "binance_spot"}) == (1, 1)

    async with db_sessions() as session:
        legacy = await session.get(RuntimeControl, 1)
        binance = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
    assert legacy is not None
    assert legacy.kill_switch is False
    assert binance.kill_switch is True

    assert await run_command(
        "req-binance-mode-live", "set_mode", {"venue": "binance_spot", "mode": "live"}
    ) == (0, 1)
    async with db_sessions() as session:
        binance = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        rejected = await session.scalar(
            select(OperatorCommand).where(OperatorCommand.request_id == "req-binance-mode-live")
        )
    assert binance.mode is VenueMode.SHADOW
    assert rejected is not None
    assert rejected.status == "rejected"
    assert "locked" in (rejected.detail or "")


async def test_global_stop_and_resume_cover_both_venues(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = Settings(database_url=migrated_database_url, operator_api_key="secret-key")
    async with db_sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.SHADOW)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)
        await enqueue_command(
            session,
            request_id="req-global-stop",
            command="stop",
            payload={"venue": "all"},
            actor="integration",
            reason="global stop drill",
        )

    assert await process_operator_commands(settings, db_sessions) == (1, 1)
    async with db_sessions() as session:
        legacy = await session.get(RuntimeControl, 1)
        controls = await list_venue_runtime_controls(session)
    assert legacy is not None
    assert legacy.kill_switch is True
    assert all(control.kill_switch for control in controls)

    async with db_sessions.begin() as session:
        await enqueue_command(
            session,
            request_id="req-global-resume",
            command="resume",
            payload={"venue": "all"},
            actor="integration",
            reason="global stop drill complete",
        )

    assert await process_operator_commands(settings, db_sessions) == (1, 1)
    async with db_sessions() as session:
        legacy = await session.get(RuntimeControl, 1)
        controls = await list_venue_runtime_controls(session)
    assert legacy is not None
    assert legacy.kill_switch is False
    assert all(not control.kill_switch for control in controls)
