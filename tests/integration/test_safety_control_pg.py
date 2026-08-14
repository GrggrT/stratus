from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stratus.config import Settings
from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketStatus,
    OperatorCommand,
    Order,
    RuntimeControl,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TradeSide,
    TradingVenue,
    VenueMode,
    VenueRuntimeControl,
    WeatherKind,
)
from stratus.repositories.emergency_stop import execute_polymarket_emergency_stop
from stratus.safety_api import create_app
from stratus.services.safety_control import (
    acquire_paper_canary_lease_atomically,
    refresh_paper_canary_watchdog_status_once,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
SAFETY_TEST_API_KEY = "sA9f4.Zx7~Qm2-Rp8-Ln5.Tv3-Wc6-Yk1~Hu9.Ds4-Pq7"


def _settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=url,
        operator_api_key="operator-secret",
        safety_api_key=SAFETY_TEST_API_KEY,
    )


def _request(
    request_id: str = "safety-integration-0001",
    *,
    reason: str = "independent emergency stop integration test",
) -> dict[str, object]:
    return {
        "request_id": request_id,
        "command": "stop",
        "reason": reason,
        "actor": "integration-safety",
        "payload": {"venue": "polymarket"},
    }


async def _seed_execution_state(sessions: SessionFactory) -> tuple[int, int, datetime]:
    confirmation = datetime.now(UTC) + timedelta(minutes=4)
    async with sessions.begin() as session:
        # This fixture deliberately constructs the unsafe pre-stop state that
        # migration 0047 forbids runtime from creating. Only the migration
        # owner may bypass the fences, and only inside this rolled-forward test
        # transaction; the triggers are re-enabled before commit.
        await session.execute(
            text(
                "ALTER TABLE runtime_control DISABLE TRIGGER "
                "trg_paper_canary_runtime_fence"
            )
        )
        await session.execute(
            text(
                "ALTER TABLE venue_runtime_control DISABLE TRIGGER "
                "trg_paper_canary_venue_fence"
            )
        )
        runtime = await session.get(RuntimeControl, 1, with_for_update=True)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
            with_for_update=True,
        )
        binance = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
            with_for_update=True,
        )
        assert runtime is not None and polymarket is not None and binance is not None
        runtime.kill_switch = False
        runtime.live_confirmation_until = confirmation
        polymarket.enabled = True
        polymarket.mode = VenueMode.PAPER
        polymarket.kill_switch = False
        polymarket.live_confirmation_until = confirmation
        binance.enabled = True
        binance.mode = VenueMode.BACKTEST
        binance.kill_switch = True
        binance.live_confirmation_until = confirmation
        binance.strategy_version = "binance-test-strategy"
        binance.risk_config_version = "binance-test-risk"

        station = Station(
            icao="KSAF",
            name="Safety Control",
            city="Safety",
            lat=Decimal("40.000000"),
            lon=Decimal("-73.000000"),
            tz="UTC",
            active=True,
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="safety-control-market",
            event_id="safety-control-event",
            station_id=station.id,
            market_date=date(2026, 7, 30),
            kind=WeatherKind.HIGH,
            rules_raw="integration safety-control rules",
            status=MarketStatus.ACTIVE,
            resolves_at=datetime(2026, 7, 31, tzinfo=UTC),
        )
        session.add(market)
        await session.flush()
        signal = Signal(
            market_id=market.id,
            ts=datetime.now(UTC),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.650000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.150000"),
            kelly_frac=Decimal("0.050000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.A,
            status=SignalStatus.PENDING,
        )
        session.add(signal)
        await session.flush()
        live_order = Order(
            signal_id=signal.id,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_YES,
            price=Decimal("0.500000"),
            size_usd=Decimal("10.0000"),
            status="open",
        )
        paper_order = Order(
            signal_id=signal.id,
            mode=ExecutionMode.PAPER,
            side=TradeSide.BUY_YES,
            price=Decimal("0.500000"),
            size_usd=Decimal("10.0000"),
            status="open",
        )
        session.add_all((live_order, paper_order))
        await session.flush()
        await session.execute(
            text(
                "ALTER TABLE runtime_control ENABLE TRIGGER "
                "trg_paper_canary_runtime_fence"
            )
        )
        await session.execute(
            text(
                "ALTER TABLE venue_runtime_control ENABLE TRIGGER "
                "trg_paper_canary_venue_fence"
            )
        )
        return live_order.id, paper_order.id, confirmation


async def _seed_nonblocking_cleanup_rows(
    sessions: SessionFactory,
) -> tuple[int, int, int, int]:
    async with sessions.begin() as session:
        station = Station(
            icao="KCLN",
            name="Cleanup Lock Test",
            city="Cleanup",
            lat=Decimal("41.000000"),
            lon=Decimal("-72.000000"),
            tz="UTC",
            active=True,
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="safety-cleanup-lock-market",
            event_id="safety-cleanup-lock-event",
            station_id=station.id,
            market_date=date(2026, 8, 14),
            kind=WeatherKind.HIGH,
            rules_raw="nonblocking emergency cleanup integration test",
            status=MarketStatus.ACTIVE,
            resolves_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
        session.add(market)
        await session.flush()
        first_signal = Signal(
            market_id=market.id,
            ts=datetime(2026, 8, 14, 12, 0, tzinfo=UTC),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.650000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.150000"),
            kelly_frac=Decimal("0.050000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.A,
            status=SignalStatus.PENDING,
        )
        second_signal = Signal(
            market_id=market.id,
            ts=datetime(2026, 8, 14, 12, 0, 1, tzinfo=UTC),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.660000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.160000"),
            kelly_frac=Decimal("0.050000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.A,
            status=SignalStatus.PENDING,
        )
        session.add_all((first_signal, second_signal))
        await session.flush()
        first_order = Order(
            signal_id=first_signal.id,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_YES,
            price=Decimal("0.500000"),
            size_usd=Decimal("10.0000"),
            status="open",
        )
        second_order = Order(
            signal_id=second_signal.id,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_YES,
            price=Decimal("0.500000"),
            size_usd=Decimal("10.0000"),
            status="open",
        )
        session.add_all((first_order, second_order))
        await session.flush()
        return (
            first_signal.id,
            first_order.id,
            second_signal.id,
            second_order.id,
        )


def _binance_identity(row: VenueRuntimeControl) -> tuple[object, ...]:
    return (
        row.enabled,
        row.mode,
        row.kill_switch,
        row.halted_reason,
        row.live_confirmation_until,
        row.strategy_version,
        row.risk_config_version,
        row.last_reconciled_at,
        row.updated_at,
    )


async def test_emergency_stop_is_immediate_atomic_audited_and_idempotent(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    live_order_id, paper_order_id, confirmation = await _seed_execution_state(
        db_sessions
    )
    async with db_sessions() as session:
        binance_before = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        assert binance_before is not None
        exact_binance_before = _binance_identity(binance_before)

    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    await refresh_paper_canary_watchdog_status_once(
        roles.safety,
        app.state.watchdog_status,
        database_boundary="test_session_unverified",
    )
    headers = {"X-API-Key": SAFETY_TEST_API_KEY}
    body = _request()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        ready = await client.get("/readyz")
        first = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=body,
        )
        retry = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=body,
        )

    assert ready.status_code == 503
    assert "database_control_state_unsafe" in ready.json()["reasons"]
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["idempotent"] is False
    assert first.json()["expired_signals"] == 1
    assert first.json()["cancel_requested_live_orders"] == 1
    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["idempotent"] is True
    assert retry.json()["expired_signals"] == 0
    assert retry.json()["cancel_requested_live_orders"] == 0
    assert retry.json()["item"]["id"] == first.json()["item"]["id"]

    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance_after = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        live_order = await session.get(Order, live_order_id)
        paper_order = await session.get(Order, paper_order_id)
        signal = await session.scalar(select(Signal))
        commands = int(await session.scalar(select(func.count(OperatorCommand.id))) or 0)
        command = await session.scalar(select(OperatorCommand))

    assert runtime is not None
    assert runtime.kill_switch is True
    assert runtime.live_confirmation_until is None
    assert polymarket is not None
    assert polymarket.kill_switch is True
    assert polymarket.live_confirmation_until is None
    assert binance_after is not None
    assert _binance_identity(binance_after) == exact_binance_before
    assert binance_after.live_confirmation_until == confirmation
    assert live_order is not None and live_order.status == "cancel_requested"
    assert paper_order is not None and paper_order.status == "open"
    assert signal is not None
    assert signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "kill_switch"
    assert commands == 1
    assert command is not None
    assert command.request_id == body["request_id"]
    assert command.command == "stop"
    assert command.payload == {"venue": "polymarket"}
    assert command.actor == body["actor"]
    assert command.reason == body["reason"]
    assert command.status == "succeeded"
    assert command.executed_at is not None


async def test_emergency_stop_skips_locked_cleanup_then_exact_retry_converges(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-cleanup-locks",
        run_id="paper-run-cleanup-locks",
        acquire_request_id="paper-acquire-cleanup-locks",
        resume_request_id="paper-resume-cleanup-locks",
        direct_stop_request_id="paper-direct-cleanup-locks",
        release_request_id="paper-release-cleanup-locks",
        resume_actor="cleanup-lock-test",
        resume_reason="prove emergency STOP never waits on cleanup rows",
        nonce="cleanup-lock-nonce-" + ("x" * 32),
        ttl_seconds=15,
    )
    (
        locked_signal_id,
        unlocked_order_id,
        reverse_signal_id,
        locked_order_id,
    ) = await _seed_nonblocking_cleanup_rows(db_sessions)

    pause_key = 6004519027736253999
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                f"""
                CREATE FUNCTION public.test_pause_cleanup_signal_0047()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $test_pause$
                BEGIN
                    PERFORM pg_advisory_xact_lock({pause_key});
                    RETURN NEW;
                END
                $test_pause$
                """
            )
        )
        await owner.execute(
            text(
                f"""
                CREATE TRIGGER test_pause_cleanup_signal_0047
                BEFORE UPDATE OF status ON public.signals
                FOR EACH ROW
                WHEN (
                    OLD.id = {reverse_signal_id}
                    AND OLD.status = 'pending'::signal_status
                )
                EXECUTE FUNCTION public.test_pause_cleanup_signal_0047()
                """
            )
        )

    signal_locker = db_sessions()
    reverse_locker = db_sessions()
    pause_gate = db_sessions()
    signal_transaction = await signal_locker.begin()
    reverse_transaction = await reverse_locker.begin()
    gate_transaction = await pause_gate.begin()
    stop_task = None
    reverse_task = None
    body = _request(
        "paper-direct-cleanup-locks",
        reason="STOP commits while Signal and reverse Order locks are held",
    )
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    try:
        await pause_gate.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": pause_key},
        )
        await signal_locker.execute(
            text("SELECT id FROM signals WHERE id=:id FOR UPDATE"),
            {"id": locked_signal_id},
        )
        await reverse_locker.execute(
            text("SET LOCAL application_name='cleanup-order-signal-reverse'")
        )
        await reverse_locker.execute(
            text("SELECT id FROM orders WHERE id=:id FOR UPDATE"),
            {"id": locked_order_id},
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            stop_task = asyncio.create_task(
                client.post(
                    "/api/v1/emergency-stop",
                    headers={"X-API-Key": SAFETY_TEST_API_KEY},
                    json=body,
                )
            )
            for _ in range(100):
                async with db_sessions() as observer:
                    paused = int(
                        await observer.scalar(
                            text(
                                """
                                SELECT count(*)
                                FROM pg_stat_activity
                                WHERE state = 'active'
                                  AND wait_event_type = 'Lock'
                                  AND wait_event = 'advisory'
                                  AND query LIKE '%safety_paper_canary_stop%'
                                """
                            )
                        )
                        or 0
                    )
                if paused == 1:
                    break
                await asyncio.sleep(0.01)
            else:
                raise AssertionError(
                    "safety STOP did not reach the deterministic Signal pause"
                )

            # This transaction already owns Order B, then requests Signal B.
            # The STOP owns Signal B at the pause. Old Signal->Order cleanup
            # deadlocked here; SKIP LOCKED lets STOP commit before this waiter.
            reverse_task = asyncio.create_task(
                reverse_locker.execute(
                    text("SELECT id FROM signals WHERE id=:id FOR UPDATE"),
                    {"id": reverse_signal_id},
                )
            )
            await asyncio.sleep(0.05)
            assert not reverse_task.done()
            await gate_transaction.commit()
            first_response, _ = await asyncio.wait_for(
                asyncio.gather(stop_task, reverse_task),
                timeout=3,
            )

        assert first_response.status_code == 200
        assert first_response.json()["created"] is True
        assert first_response.json()["expired_signals"] == 1
        assert first_response.json()["cancel_requested_live_orders"] == 1
        await reverse_transaction.commit()
        await signal_transaction.commit()
    finally:
        for task in (stop_task, reverse_task):
            if task is not None and not task.done():
                task.cancel()
        if gate_transaction.is_active:
            await gate_transaction.rollback()
        if reverse_transaction.is_active:
            await reverse_transaction.rollback()
        if signal_transaction.is_active:
            await signal_transaction.rollback()
        await pause_gate.close()
        await reverse_locker.close()
        await signal_locker.close()
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "DROP TRIGGER IF EXISTS test_pause_cleanup_signal_0047 "
                    "ON public.signals"
                )
            )
            await owner.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "public.test_pause_cleanup_signal_0047()"
                )
            )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        replay = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["idempotent"] is True
    assert replay.json()["expired_signals"] == 1
    assert replay.json()["cancel_requested_live_orders"] == 1

    async with db_sessions() as owner:
        lease_state = await owner.scalar(
            text(
                "SELECT state FROM paper_canary_safety_leases "
                "WHERE lease_id='paper-lease-cleanup-locks'"
            )
        )
        pending_signals = int(
            await owner.scalar(
                select(func.count(Signal.id)).where(
                    Signal.status == SignalStatus.PENDING
                )
            )
            or 0
        )
        nonterminal_live_orders = int(
            await owner.scalar(
                select(func.count(Order.id)).where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status.in_(("open", "pending")),
                )
            )
            or 0
        )
        first_order = await owner.get(Order, unlocked_order_id)
        second_order = await owner.get(Order, locked_order_id)
    assert lease_state == "revoked_stopped"
    assert pending_signals == 0
    assert nonterminal_live_orders == 0
    assert first_order is not None and first_order.status == "cancel_requested"
    assert second_order is not None and second_order.status == "cancel_requested"


async def test_readyz_and_queued_control_work_cannot_starve_reserved_stop_pool(
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    source_engine = roles.safety.kw["bind"]
    control_engine = create_async_engine(
        source_engine.url,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=10.0,
    )
    emergency_engine = create_async_engine(
        source_engine.url,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
    control_sessions = async_sessionmaker(control_engine, expire_on_commit=False)
    emergency_sessions = async_sessionmaker(
        emergency_engine,
        expire_on_commit=False,
    )
    queued_checkout: asyncio.Task[int] | None = None
    try:
        app = create_app(
            _settings(migrated_database_url),
            session_factory=control_sessions,
            emergency_session_factory=emergency_sessions,
        )
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                async with asyncio.timeout(2.0):
                    for _ in range(200):
                        ready = await client.get("/readyz")
                        if ready.status_code == 200:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        pytest.fail("safety watchdog did not publish readiness")
                assert app.state.watchdog_status.snapshot().control_state_ready is True

                async with control_sessions.begin() as held_session:
                    assert await held_session.scalar(text("SELECT 1")) == 1

                    async def wait_for_control_pool() -> int:
                        async with control_sessions.begin() as queued_session:
                            return int(await queued_session.scalar(text("SELECT 1")))

                    queued_checkout = asyncio.create_task(wait_for_control_pool())
                    await asyncio.sleep(0)
                    assert not queued_checkout.done()
                    responses = await asyncio.wait_for(
                        asyncio.gather(*(client.get("/readyz") for _ in range(20))),
                        timeout=2.0,
                    )
                    stopped = await asyncio.wait_for(
                        client.post(
                            "/api/v1/emergency-stop",
                            headers={"X-API-Key": SAFETY_TEST_API_KEY},
                            json=_request("safety-ready-pool-0001"),
                        ),
                        timeout=6.0,
                    )
                    assert not queued_checkout.done()

        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["status"] == "ready" for response in responses)
        assert stopped.status_code == 200
        assert await queued_checkout == 1
    finally:
        if queued_checkout is not None and not queued_checkout.done():
            queued_checkout.cancel()
        await control_engine.dispose()
        await emergency_engine.dispose()


async def test_idempotent_retry_converges_back_to_safe_state(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    headers = {"X-API-Key": SAFETY_TEST_API_KEY}
    body = _request("safety-convergent-0001")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=body,
        )
        assert first.status_code == 200
        original_executed_at = first.json()["item"]["executed_at"]

        confirmation = datetime.now(UTC) + timedelta(minutes=3)
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    "ALTER TABLE runtime_control DISABLE TRIGGER "
                    "trg_paper_canary_runtime_fence"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE venue_runtime_control DISABLE TRIGGER "
                    "trg_paper_canary_venue_fence"
                )
            )
            runtime = await session.get(RuntimeControl, 1, with_for_update=True)
            polymarket = await session.get(
                VenueRuntimeControl,
                TradingVenue.POLYMARKET,
                with_for_update=True,
            )
            assert runtime is not None and polymarket is not None
            runtime.kill_switch = False
            runtime.live_confirmation_until = confirmation
            polymarket.kill_switch = False
            polymarket.live_confirmation_until = confirmation
            await session.flush()
            await session.execute(
                text(
                    "ALTER TABLE runtime_control ENABLE TRIGGER "
                    "trg_paper_canary_runtime_fence"
                )
            )
            await session.execute(
                text(
                    "ALTER TABLE venue_runtime_control ENABLE TRIGGER "
                    "trg_paper_canary_venue_fence"
                )
            )

        retry = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=body,
        )

    assert retry.status_code == 200
    assert retry.json()["created"] is False
    assert retry.json()["idempotent"] is True
    assert retry.json()["item"]["executed_at"] == original_executed_at
    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        command_count = int(
            await session.scalar(select(func.count(OperatorCommand.id))) or 0
        )
    assert runtime is not None
    assert runtime.kill_switch is True
    assert runtime.live_confirmation_until is None
    assert polymarket is not None
    assert polymarket.kill_switch is True
    assert polymarket.live_confirmation_until is None
    assert command_count == 1


async def test_late_failure_rolls_back_audit_controls_signals_and_orders(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    live_order_id, _, confirmation = await _seed_execution_state(db_sessions)
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                """
                CREATE FUNCTION public.test_fail_safety_stop_final_audit()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    IF NEW.request_id = 'safety-rollback-0001'
                       AND NEW.status = 'succeeded' THEN
                        RAISE EXCEPTION
                            'synthetic final audit update failure';
                    END IF;
                    RETURN NEW;
                END;
                $$
                """
            )
        )
        await owner.execute(
            text(
                """
                CREATE TRIGGER test_fail_safety_stop_final_audit
                BEFORE UPDATE ON operator_commands
                FOR EACH ROW
                EXECUTE FUNCTION public.test_fail_safety_stop_final_audit()
                """
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="synthetic final audit update failure",
        ):
            async with roles.safety.begin() as session:
                await execute_polymarket_emergency_stop(
                    session,
                    request_id="safety-rollback-0001",
                    actor="integration-safety",
                    reason="prove all-or-nothing stop transaction",
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "DROP TRIGGER IF EXISTS test_fail_safety_stop_final_audit "
                    "ON operator_commands"
                )
            )
            await owner.execute(
                text(
                    "DROP FUNCTION IF EXISTS "
                    "public.test_fail_safety_stop_final_audit()"
                )
            )

    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        signal = await session.scalar(select(Signal))
        live_order = await session.get(Order, live_order_id)
        command_count = int(
            await session.scalar(select(func.count(OperatorCommand.id))) or 0
        )
    assert runtime is not None
    assert runtime.kill_switch is False
    assert runtime.live_confirmation_until == confirmation
    assert polymarket is not None
    assert polymarket.kill_switch is False
    assert polymarket.live_confirmation_until == confirmation
    assert signal is not None and signal.status is SignalStatus.PENDING
    assert live_order is not None and live_order.status == "open"
    assert command_count == 0


async def test_authentication_fails_before_any_audit_or_state_change(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    body = _request("safety-auth-pg-0001")
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.post("/api/v1/emergency-stop", json=body)
        wrong = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": "wrong"},
            json=body,
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    async with db_sessions() as session:
        command_count = int(
            await session.scalar(select(func.count(OperatorCommand.id))) or 0
        )
        runtime = await session.get(RuntimeControl, 1)
    assert command_count == 0
    assert runtime is not None and runtime.kill_switch is True


async def test_request_id_identity_collision_is_rejected_without_mutation(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    request_id = "safety-collision-0001"
    async with db_sessions.begin() as session:
        session.add(
            OperatorCommand(
                request_id=request_id,
                command="stop",
                payload={"venue": "polymarket"},
                actor="integration-safety",
                reason="different immutable reason",
            )
        )

    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=_request(request_id),
        )

    assert response.status_code == 409
    assert "audit identity collision" in response.json()["detail"]
    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        command = await session.scalar(select(OperatorCommand))
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert command is not None
    assert command.status == "pending"
    assert command.reason == "different immutable reason"
