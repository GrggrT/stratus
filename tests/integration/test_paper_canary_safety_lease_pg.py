from __future__ import annotations

import asyncio
import hashlib
import os
from datetime import datetime
from time import monotonic
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from stratus.config import Settings
from stratus.db.models import (
    ExecutionMode,
    OperatorCommand,
    PaperCanarySafetyLease,
    RuntimeControl,
    TradingVenue,
    VenueMode,
    VenueRuntimeControl,
)
from stratus.jobs.process_operator_commands import (
    _stop_venues,
    process_operator_commands,
)
from stratus.repositories.operator_commands import (
    claim_pending_commands,
    enqueue_command,
)
from stratus.repositories.paper_canary_leases import (
    PaperCanaryLeaseAuthenticationError,
    PaperCanaryLeaseCollisionError,
    PaperCanaryLeaseUnavailableError,
    acquire_paper_canary_lease,
)
from stratus.repositories.runtime_control import get_runtime_control, set_kill_switch
from stratus.repositories.safety_lock import PaperCanarySafetyLockBusy
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    list_venue_runtime_controls,
    set_venue_enabled,
)
from stratus.safety_api import create_app
from stratus.services.safety_control import (
    acquire_paper_canary_lease_atomically,
    heartbeat_paper_canary_lease_atomically,
    release_paper_canary_lease_atomically,
    run_paper_canary_watchdog_once,
    stop_polymarket_atomically,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NONCE = "canary-nonce-" + ("x" * 32)
RESUME_ACTOR = "release-owner"
RESUME_REASON = "Reviewed release canary may resume Polymarket PAPER."
SAFETY_TEST_API_KEY = "sA9f4.Zx7~Qm2-Rp8-Ln5.Tv3-Wc6-Yk1~Hu9.Ds4-Pq7"


def _settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=url,
        operator_api_key="operator-secret",
        safety_api_key=SAFETY_TEST_API_KEY,
        safety_canary_lease_ttl_seconds=15,
        safety_watchdog_poll_seconds=1,
    )


def _lease_args(suffix: str, *, ttl_seconds: int = 15) -> dict[str, object]:
    return {
        "lease_id": f"paper-lease-{suffix}",
        "run_id": f"paper-run-{suffix}",
        "acquire_request_id": f"paper-acquire-{suffix}",
        "resume_request_id": f"paper-resume-{suffix}",
        "direct_stop_request_id": f"paper-direct-{suffix}",
        "release_request_id": f"paper-release-{suffix}",
        "resume_actor": RESUME_ACTOR,
        "resume_reason": RESUME_REASON,
        "nonce": NONCE,
        "ttl_seconds": ttl_seconds,
    }


async def _set_fail_closed(sessions: SessionFactory) -> tuple[object, ...]:
    async with sessions.begin() as session:
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
        runtime.mode = ExecutionMode.PAPER
        runtime.kill_switch = True
        runtime.live_confirmation_until = None
        polymarket.enabled = True
        polymarket.mode = VenueMode.PAPER
        polymarket.kill_switch = True
        polymarket.live_confirmation_until = None
        binance.enabled = True
        binance.mode = VenueMode.BACKTEST
        binance.kill_switch = True
        binance.strategy_version = "sealed-binance-strategy"
        binance.risk_config_version = "sealed-binance-risk"
        await session.flush()
        await session.refresh(binance)
        return _binance_identity(binance)


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


async def _queue_reserved_resume(
    sessions: SessionFactory,
    *,
    request_id: str,
    actor: str = RESUME_ACTOR,
    reason: str = RESUME_REASON,
) -> None:
    async with sessions.begin() as session:
        await enqueue_command(
            session,
            request_id=request_id,
            command="resume",
            payload={"venue": "polymarket"},
            actor=actor,
            reason=reason,
        )


async def _assert_post_terminal_history_fence(
    sessions: SessionFactory,
) -> None:
    rejected_updates = (
        (
            "UPDATE runtime_control SET kill_switch=false WHERE id=1",
            "runtime kill-off lacks exact",
        ),
        (
            "UPDATE venue_runtime_control SET kill_switch=false "
            "WHERE venue='polymarket'",
            "Polymarket kill-off lacks exact",
        ),
        (
            "UPDATE runtime_control SET mode='live'::execution_mode WHERE id=1",
            "history forbids non-PAPER runtime",
        ),
        (
            "UPDATE runtime_control "
            "SET live_confirmation_until=clock_timestamp() + interval '5 minutes' "
            "WHERE id=1",
            "history forbids non-PAPER runtime",
        ),
        (
            "UPDATE venue_runtime_control SET mode='live'::venue_mode "
            "WHERE venue='polymarket'",
            "history forbids unsafe Polymarket",
        ),
        (
            "UPDATE venue_runtime_control "
            "SET live_confirmation_until=clock_timestamp() + interval '5 minutes' "
            "WHERE venue='polymarket'",
            "history forbids unsafe Polymarket",
        ),
    )
    for statement, error in rejected_updates:
        with pytest.raises(DBAPIError, match=error):
            async with sessions.begin() as session:
                await session.execute(text(statement))


async def test_lease_api_is_authenticated_db_clocked_and_never_echoes_nonce(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    app.state.watchdog_task = SimpleNamespace(done=lambda: False)
    app.state.watchdog_status.record_success(
        tick_started_monotonic=monotonic(),
        control_state_ready=True,
        database_boundary="test_session_unverified",
    )
    body = {
        key: value
        for key, value in _lease_args("api-0001").items()
        if key != "ttl_seconds"
    }
    async with db_sessions() as session:
        database_before = await session.scalar(select(func.clock_timestamp()))
    assert isinstance(database_before, datetime)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.post("/api/v1/canary-leases/acquire", json=body)
        wrong = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": "wrong"},
            json=body,
        )
        acquired = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
        heartbeat_before_watchdog = await client.post(
            "/api/v1/canary-leases/paper-lease-api-0001/heartbeat",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json={"run_id": "paper-run-api-0001", "nonce": NONCE},
        )

    async with db_sessions() as session:
        database_after = await session.scalar(select(func.clock_timestamp()))
        lease = await session.scalar(select(PaperCanarySafetyLease))
    assert isinstance(database_after, datetime)
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert acquired.status_code == 200
    assert heartbeat_before_watchdog.status_code == 409
    assert lease is not None
    assert database_before <= lease.acquired_at <= database_after
    assert lease.watchdog_at == lease.acquired_at
    assert lease.nonce_sha256 == hashlib.sha256(NONCE.encode()).hexdigest()
    assert NONCE not in acquired.text
    assert "watchdog_stop_request_id" not in acquired.text
    assert "direct_stop_request_id" not in acquired.text

    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        wrong_nonce = await client.post(
            "/api/v1/canary-leases/paper-lease-api-0001/heartbeat",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json={"run_id": "paper-run-api-0001", "nonce": "z" * 32},
        )
        heartbeat = await client.post(
            "/api/v1/canary-leases/paper-lease-api-0001/heartbeat",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json={"run_id": "paper-run-api-0001", "nonce": NONCE},
        )
    assert wrong_nonce.status_code == 409
    assert heartbeat.status_code == 200
    assert NONCE not in heartbeat.text


async def test_resume_is_exact_observed_and_heartbeat_is_fail_closed(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    args = _lease_args("resume-0001")
    acquired = await acquire_paper_canary_lease_atomically(roles.safety, **args)
    assert acquired.created is True

    with pytest.raises(
        DBAPIError,
        match="(not reserved by a live observed lease|reserved canary request identity)",
    ):
        await _queue_reserved_resume(
            roles.runtime,
            request_id="paper-resume-resume-0001",
        )

    assert await run_paper_canary_watchdog_once(roles.safety) is None
    with pytest.raises(DBAPIError, match="reserved canary request identity"):
        await _queue_reserved_resume(
            roles.runtime,
            request_id="paper-resume-resume-0001",
            actor="different-actor",
        )

    with pytest.raises(PaperCanaryLeaseAuthenticationError):
        await heartbeat_paper_canary_lease_atomically(
            roles.safety,
            lease_id="paper-lease-resume-0001",
            run_id="paper-run-resume-0001",
            nonce="z" * 32,
            ttl_seconds=15,
            watchdog_fresh_seconds=3,
        )
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "ALTER TABLE paper_canary_safety_leases "
                "DISABLE TRIGGER trg_paper_canary_lease_audit"
            )
        )
        await owner.execute(
            text(
                "UPDATE paper_canary_safety_leases "
                "SET acquired_at=clock_timestamp() - interval '10 seconds', "
                "heartbeat_at=clock_timestamp() - interval '10 seconds', "
                "watchdog_at=clock_timestamp() - interval '8 seconds' "
                "WHERE lease_id='paper-lease-resume-0001'"
            )
        )
        await owner.execute(
            text(
                "ALTER TABLE paper_canary_safety_leases "
                "ENABLE TRIGGER trg_paper_canary_lease_audit"
            )
        )
    with pytest.raises(PaperCanaryLeaseUnavailableError, match="fresh"):
        await heartbeat_paper_canary_lease_atomically(
            roles.safety,
            lease_id="paper-lease-resume-0001",
            run_id="paper-run-resume-0001",
            nonce=NONCE,
            ttl_seconds=15,
            watchdog_fresh_seconds=3,
        )
    assert await run_paper_canary_watchdog_once(roles.safety) is None

    heartbeat = await heartbeat_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-resume-0001",
        run_id="paper-run-resume-0001",
        nonce=NONCE,
        ttl_seconds=15,
        watchdog_fresh_seconds=3,
    )
    assert heartbeat.lease.expires_at > acquired.lease.expires_at

    retry = await acquire_paper_canary_lease_atomically(roles.safety, **args)
    assert retry.idempotent is True
    with pytest.raises(PaperCanaryLeaseCollisionError):
        await acquire_paper_canary_lease_atomically(
            roles.safety,
            **_lease_args("collision-0002"),
        )

    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-resume-0001",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert runtime is not None and runtime.kill_switch is False
    assert polymarket is not None and polymarket.kill_switch is False


async def test_watchdog_stops_expired_unsafe_lease_without_worker_or_collector(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    binance_before = await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("expiry-0001", ttl_seconds=1),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-expiry-0001",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)

    # No worker, collector, or operator command is run after this point.
    await asyncio.sleep(1.2)
    assert await run_paper_canary_watchdog_once(roles.safety) == (
        "paper-lease-expiry-0001"
    )

    async with db_sessions() as session:
        lease = await session.scalar(select(PaperCanarySafetyLease))
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        stop_command = await session.get(
            OperatorCommand,
            lease.stop_command_id if lease is not None else None,
        )
    assert lease is not None and lease.state == "expired_stopped"
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert binance is not None and _binance_identity(binance) == binance_before
    assert stop_command is not None
    assert stop_command.request_id == lease.watchdog_stop_request_id
    assert stop_command.actor == "stratus-safety-watchdog"
    assert stop_command.status == "succeeded"

    direct_body = {
        "request_id": lease.direct_stop_request_id,
        "command": "stop",
        "payload": {"venue": "polymarket"},
        "actor": "codex-paper-canary",
        "reason": "STRATUS PAPER canary terminal fail-closed stop.",
    }
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        direct = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=direct_body,
        )
        assert direct.status_code == 200, direct.text
        replay = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=direct_body,
        )
        assert replay.status_code == 200, replay.text
    async with db_sessions() as session:
        sealed_lease = await session.get(PaperCanarySafetyLease, lease.id)
    assert sealed_lease is not None
    assert sealed_lease.state == "expired_stopped"
    assert sealed_lease.stop_command_id == stop_command.id
    await _assert_post_terminal_history_fence(roles.runtime)


async def test_expired_lease_fails_closed_on_execution_reads_without_watchdog(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("read-gate", ttl_seconds=1),
    )
    await run_paper_canary_watchdog_once(roles.safety)
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-read-gate",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    await asyncio.sleep(1.2)

    # The persisted bits are deliberately still open: no watchdog ran.
    async with db_sessions() as session:
        stored = await session.get(RuntimeControl, 1)
        stored_venue = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        effective = await get_runtime_control(session)
        effective_venue = await get_venue_runtime_control(
            session,
            TradingVenue.POLYMARKET,
        )
        listed_venues = await list_venue_runtime_controls(session)
    assert stored is not None and stored.kill_switch is False
    assert stored_venue is not None and stored_venue.kill_switch is False
    assert effective.kill_switch is True
    assert "expired" in (effective.halted_reason or "")
    assert effective_venue.kill_switch is True
    assert "expired" in (effective_venue.halted_reason or "")
    listed_polymarket = next(
        item
        for item in listed_venues
        if item.venue is TradingVenue.POLYMARKET
    )
    assert listed_polymarket.kill_switch is True
    assert "expired" in (listed_polymarket.halted_reason or "")


async def test_advisory_lock_serializes_empty_set_acquire_before_stop(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    acquired = asyncio.Event()
    may_commit = asyncio.Event()

    async def acquire_and_hold() -> None:
        async with roles.safety.begin() as session:
            await acquire_paper_canary_lease(
                session,
                **_lease_args("empty-race"),
            )
            acquired.set()
            await may_commit.wait()

    acquire_task = asyncio.create_task(acquire_and_hold())
    await asyncio.wait_for(acquired.wait(), timeout=3)
    stop_task = asyncio.create_task(
        stop_polymarket_atomically(
            roles.safety,
            request_id="paper-direct-empty-race",
            actor="integration-safety",
            reason="serialize acquire against emergency stop",
        )
    )
    await asyncio.sleep(0.1)
    assert stop_task.done() is False
    may_commit.set()
    await asyncio.wait_for(asyncio.gather(acquire_task, stop_task), timeout=8)

    async with db_sessions() as session:
        lease = await session.scalar(select(PaperCanarySafetyLease))
        runtime = await session.get(RuntimeControl, 1)
    assert lease is not None and lease.state == "revoked_stopped"
    assert runtime is not None and runtime.kill_switch is True


async def test_release_and_generic_stop_win_queued_resume_races_and_stop_ids_cannot_be_poisoned(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    binance_before = await _set_fail_closed(db_sessions)
    settings = _settings(migrated_database_url)

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("release-race"),
    )
    await run_paper_canary_watchdog_once(roles.safety)
    async with db_sessions() as session:
        release_lease = await session.scalar(select(PaperCanarySafetyLease))
    assert release_lease is not None
    for reserved_request_id in (
        release_lease.resume_request_id,
        release_lease.release_request_id,
        release_lease.watchdog_stop_request_id,
        release_lease.direct_stop_request_id,
    ):
        with pytest.raises(DBAPIError, match="reserved canary request identity"):
            async with roles.runtime.begin() as session:
                session.add(
                    OperatorCommand(
                        request_id=reserved_request_id,
                        command="run_job",
                        payload={"job": "prune-signals"},
                        actor="poison-attempt",
                        reason="late collision must fail",
                    )
                )
                await session.flush()

    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-release-race",
    )
    release_result, process_result = await asyncio.wait_for(
        asyncio.gather(
            release_paper_canary_lease_atomically(
                roles.safety,
                lease_id="paper-lease-release-race",
                run_id="paper-run-release-race",
                nonce=NONCE,
                release_request_id="paper-release-release-race",
            ),
            process_operator_commands(settings, roles.runtime),
        ),
        timeout=8,
    )
    assert release_result.lease.state == "released"
    assert release_result.lease.stop_command_id is not None
    assert process_result in {(0, 0), (0, 1), (1, 1)}

    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        release_command = await session.get(
            OperatorCommand,
            release_result.lease.stop_command_id,
        )
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert binance is not None and _binance_identity(binance) == binance_before
    assert release_command is not None
    assert release_command.request_id == "paper-release-release-race"
    assert release_command.status == "succeeded"
    async with db_sessions() as session:
        pending = int(
            await session.scalar(
                select(func.count(OperatorCommand.id)).where(
                    OperatorCommand.status == "pending"
                )
            )
            or 0
        )
    assert pending == 0

    release_retry = await release_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-release-race",
        run_id="paper-run-release-race",
        nonce=NONCE,
        release_request_id="paper-release-release-race",
    )
    assert release_retry.idempotent is True
    assert release_retry.lease.stop_command_id == release_result.lease.stop_command_id

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("stop-race"),
    )
    await run_paper_canary_watchdog_once(roles.safety)
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-stop-race",
    )
    # This is the same repository boundary used by Telegram /stop and health
    # fail-closed paths; it must route an active lease through atomic revoke.
    async with db_sessions.begin() as session:
        await set_kill_switch(session, True)
    assert await process_operator_commands(settings, roles.runtime) == (0, 0)
    async with db_sessions() as session:
        stop_lease = await session.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id == "paper-lease-stop-race"
            )
        )
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        resume_command = await session.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id == "paper-resume-stop-race"
            )
        )
    assert stop_lease is not None and stop_lease.state == "revoked_stopped"
    assert stop_lease.stop_command_id is not None
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert binance is not None and _binance_identity(binance) == binance_before
    assert resume_command is not None and resume_command.status == "rejected"
    assert resume_command.executed_at is not None


async def test_worker_stop_defers_to_same_id_safety_stop_without_deadlock(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    settings = _settings(migrated_database_url)
    binance_before = await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("stop-lock-order"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-stop-lock-order",
    )
    assert await process_operator_commands(settings, roles.runtime) == (1, 1)

    request_id = "paper-queued-safety-stop-lock-order"
    actor = "lock-order-integration"
    reason = "prove safety STOP cannot deadlock a claimed worker STOP"
    async with roles.runtime.begin() as session:
        _, created = await enqueue_command(
            session,
            request_id=request_id,
            command="stop",
            payload={"venue": "polymarket"},
            actor=actor,
            reason=reason,
        )
    assert created is True

    app = create_app(
        settings,
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    response_task: asyncio.Task[Response] | None = None
    worker_deferred = False
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        try:
            async with roles.runtime.begin() as session:
                claimed = await claim_pending_commands(session, limit=1)
                assert len(claimed) == 1
                assert claimed[0].request_id == request_id
                response_task = asyncio.create_task(
                    client.post(
                        "/api/v1/emergency-stop",
                        headers={"X-API-Key": SAFETY_TEST_API_KEY},
                        json={
                            "request_id": request_id,
                            "command": "stop",
                            "payload": {"venue": "polymarket"},
                            "actor": actor,
                            "reason": reason,
                        },
                    )
                )

                # The safety transaction must own the advisory lock and be
                # waiting for this claimed audit row before the worker tries
                # the inverse edge.  That makes the old deadlock deterministic.
                for _ in range(100):
                    async with db_sessions() as observer:
                        safety_lock_held = bool(
                            await observer.scalar(
                                text(
                                    "SELECT EXISTS ("
                                    "SELECT 1 FROM pg_locks "
                                    "WHERE locktype='advisory' AND granted "
                                    "AND classid=1398036030 "
                                    "AND objid=256578387 AND objsubid=1)"
                                )
                            )
                        )
                    if safety_lock_held:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError(
                        "safety STOP did not acquire the PAPER advisory lock"
                    )

                await _stop_venues(session, claimed[0])
                raise AssertionError("worker STOP unexpectedly waited or executed")
        except PaperCanarySafetyLockBusy:
            worker_deferred = True

        assert response_task is not None
        response = await asyncio.wait_for(response_task, timeout=8)

    assert worker_deferred is True
    assert response.status_code == 200
    assert response.json()["item"]["request_id"] == request_id
    assert response.json()["item"]["status"] == "succeeded"
    assert await process_operator_commands(settings, roles.runtime) == (0, 0)

    async with db_sessions() as session:
        command = await session.scalar(
            select(OperatorCommand).where(OperatorCommand.request_id == request_id)
        )
        lease = await session.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id == "paper-lease-stop-lock-order"
            )
        )
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await session.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
    assert command is not None and command.status == "succeeded"
    assert command.executed_at is not None
    assert lease is not None and lease.state == "revoked_stopped"
    assert lease.stop_command_id == command.id
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert binance is not None and _binance_identity(binance) == binance_before


async def test_database_fences_block_unleased_kill_off_and_nonpaper_transition(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("fence-0001"),
    )
    await run_paper_canary_watchdog_once(roles.safety)

    with pytest.raises(DBAPIError, match="kill-off lacks exact"):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE runtime_control SET kill_switch=false "
                    "WHERE id=1"
                )
            )
    with pytest.raises(DBAPIError, match="non-PAPER runtime"):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE runtime_control "
                    "SET mode='live'::execution_mode WHERE id=1"
                )
            )
    with pytest.raises(DBAPIError, match="unsafe Polymarket state"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    "UPDATE venue_runtime_control SET enabled=false "
                    "WHERE venue='polymarket'"
                )
            )

    # A direct emergency stop is always permitted and revokes the authority.
    await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-direct-fence-0001",
        actor="integration-safety",
        reason="close database fence test safely",
    )
    async with db_sessions() as session:
        lease = await session.scalar(select(PaperCanarySafetyLease))
    assert lease is not None and lease.state == "revoked_stopped"


async def test_terminal_history_latches_fences_but_allows_safe_control_and_next_canary(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    settings = _settings(migrated_database_url)

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("history-first"),
    )
    await run_paper_canary_watchdog_once(roles.safety)
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-history-first",
    )
    assert await process_operator_commands(settings, roles.runtime) == (1, 1)
    first_release = await release_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-history-first",
        run_id="paper-run-history-first",
        nonce=NONCE,
        release_request_id="paper-release-history-first",
    )
    assert first_release.lease.state == "released"
    await _assert_post_terminal_history_fence(roles.runtime)

    # Fail-closed administrative actions remain available after the latch.
    async with db_sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.POLYMARKET, False)
    async with db_sessions() as session:
        disabled = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert disabled is not None and disabled.enabled is False
    async with db_sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.POLYMARKET, True)

    # Owner-only fault injection models a row drifted by the pre-fix fence.
    # The strengthened trigger must never prevent STOP from making that row
    # safer merely because its existing mode is already LIVE.
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
        await session.execute(
            text(
                "UPDATE runtime_control SET mode='live'::execution_mode, "
                "kill_switch=false, "
                "live_confirmation_until=clock_timestamp() + interval '5 minutes' "
                "WHERE id=1"
            )
        )
        await session.execute(
            text(
                "UPDATE venue_runtime_control SET mode='live'::venue_mode, "
                "kill_switch=false, "
                "live_confirmation_until=clock_timestamp() + interval '5 minutes' "
                "WHERE venue='polymarket'"
            )
        )
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

    safe_stop = await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-stop-after-history",
        actor="integration-safety",
        reason="prove fail-closed control remains available after terminal history",
    )
    assert safe_stop.command.status == "succeeded"
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE runtime_control SET mode='paper'::execution_mode "
                "WHERE id=1"
            )
        )
        await session.execute(
            text(
                "UPDATE venue_runtime_control SET mode='paper'::venue_mode "
                "WHERE venue='polymarket'"
            )
        )

    # History is a safety latch, not a one-shot canary: a fresh observed lease
    # and its exact reserved identity may resume PAPER again.
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("history-second"),
    )
    await run_paper_canary_watchdog_once(roles.safety)
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-history-second",
    )
    assert await process_operator_commands(settings, roles.runtime) == (1, 1)
    async with db_sessions() as session:
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert runtime is not None and runtime.kill_switch is False
    assert polymarket is not None and polymarket.kill_switch is False

    second_release = await release_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-history-second",
        run_id="paper-run-history-second",
        nonce=NONCE,
        release_request_id="paper-release-history-second",
    )
    assert second_release.lease.state == "released"


async def test_long_run_job_does_not_hold_the_canary_safety_lock(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    import stratus.jobs.process_operator_commands as command_job

    settings = _settings(migrated_database_url)
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("long-run-job"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-long-run-job",
    )
    assert await process_operator_commands(settings, roles.runtime) == (1, 1)
    async with db_sessions() as owner:
        lease_before = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id == "paper-lease-long-run-job"
            )
        )
        runtime_before = await owner.get(RuntimeControl, 1)
        polymarket_before = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert lease_before is not None and lease_before.state == "active"
    assert runtime_before is not None and runtime_before.kill_switch is False
    assert polymarket_before is not None
    assert polymarket_before.kill_switch is False

    started = asyncio.Event()
    finish = asyncio.Event()

    async def long_job(
        _settings: Settings,
        _sessions: SessionFactory,
    ) -> dict[str, bool]:
        started.set()
        await finish.wait()
        return {"completed": True}

    monkeypatch.setitem(
        command_job._MAINTENANCE_JOBS,
        "prune-signals",
        long_job,
    )
    async with roles.runtime.begin() as session:
        await enqueue_command(
            session,
            request_id="paper-long-run-job",
            command="run_job",
            payload={"job": "prune-signals"},
            actor="integration",
            reason="prove maintenance cannot block safety control",
        )
    processing = asyncio.create_task(
        process_operator_commands(
            settings,
            roles.runtime,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        assert processing.done() is False
        stop = await asyncio.wait_for(
            stop_polymarket_atomically(
                roles.safety,
                request_id="paper-direct-long-run-job",
                actor="integration-safety",
                reason="safety stop must bypass a long maintenance command",
            ),
            timeout=2,
        )
        assert stop.command.status == "succeeded"
        assert stop.command.request_id == "paper-direct-long-run-job"
        assert processing.done() is False

        async with db_sessions() as owner:
            lease_after = await owner.scalar(
                select(PaperCanarySafetyLease).where(
                    PaperCanarySafetyLease.lease_id == "paper-lease-long-run-job"
                )
            )
            runtime_after = await owner.get(RuntimeControl, 1)
            polymarket_after = await owner.get(
                VenueRuntimeControl,
                TradingVenue.POLYMARKET,
            )
        assert lease_after is not None
        assert lease_after.state == "revoked_stopped"
        assert lease_after.stop_command_id == stop.command.id
        assert runtime_after is not None and runtime_after.kill_switch is True
        assert polymarket_after is not None
        assert polymarket_after.kill_switch is True
    finally:
        finish.set()
    assert await asyncio.wait_for(processing, timeout=3) == (1, 1)


async def test_paper_only_live_rejection_does_not_delay_emergency_stop(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    import stratus.jobs.process_operator_commands as command_job

    settings = _settings(migrated_database_url).model_copy(
        update={
            "live_legal_approved": True,
            "live_tax_approved": True,
            "polygon_private_key": "0xtestkey",
        }
    )
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("heavy-live-gate"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-heavy-live-gate",
    )
    assert await process_operator_commands(settings, roles.runtime) == (1, 1)
    async with db_sessions() as owner:
        lease_before = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id == "paper-lease-heavy-live-gate"
            )
        )
        runtime_before = await owner.get(RuntimeControl, 1)
        polymarket_before = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert lease_before is not None and lease_before.state == "active"
    assert runtime_before is not None and runtime_before.kill_switch is False
    assert polymarket_before is not None
    assert polymarket_before.kill_switch is False

    live_gate_started = asyncio.Event()
    finish_live_gate = asyncio.Event()
    original_execute = command_job._execute

    async def heavy_live_gate(
        session: AsyncSession,
        command: OperatorCommand,
        command_settings: Settings,
        session_factory: SessionFactory,
    ) -> tuple[str, str]:
        live_gate_started.set()
        await finish_live_gate.wait()
        return await original_execute(
            session,
            command,
            command_settings,
            session_factory,
        )

    monkeypatch.setattr(command_job, "_execute", heavy_live_gate)
    async with roles.runtime.begin() as session:
        await enqueue_command(
            session,
            request_id="paper-heavy-live-gate",
            command="set_mode",
            payload={"mode": "live"},
            actor="integration",
            reason="prove the live gate cannot block safety control",
        )

    processing = asyncio.create_task(process_operator_commands(settings, roles.runtime))
    try:
        await asyncio.wait_for(live_gate_started.wait(), timeout=3)
        assert processing.done() is False
        started_at = asyncio.get_running_loop().time()
        stop = await asyncio.wait_for(
            stop_polymarket_atomically(
                roles.safety,
                request_id="paper-direct-heavy-live-gate",
                actor="integration-safety",
                reason="emergency stop must bypass a heavy live gate",
            ),
            timeout=2,
        )
        stop_elapsed = asyncio.get_running_loop().time() - started_at
        assert stop.command.status == "succeeded"
        assert stop.command.request_id == "paper-direct-heavy-live-gate"
        assert stop_elapsed < 2
        assert processing.done() is False
    finally:
        finish_live_gate.set()
    assert await asyncio.wait_for(processing, timeout=3) == (0, 1)

    async with db_sessions() as session:
        lease_after = await session.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id == "paper-lease-heavy-live-gate"
            )
        )
        runtime = await session.get(RuntimeControl, 1)
        polymarket = await session.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        command = await session.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id == "paper-heavy-live-gate"
            )
        )
    assert lease_after is not None
    assert lease_after.state == "revoked_stopped"
    assert lease_after.stop_command_id == stop.command.id
    assert runtime is not None
    assert runtime.mode is ExecutionMode.PAPER
    assert runtime.kill_switch is True
    assert runtime.live_confirmation_until is None
    assert polymarket is not None
    assert polymarket.mode is VenueMode.PAPER
    assert polymarket.kill_switch is True
    assert polymarket.live_confirmation_until is None
    assert command is not None and command.status == "rejected"
    assert "PAPER-only" in (command.detail or "")


async def test_actual_runtime_and_safety_logins_enforce_lease_isolation_and_repeat_canary(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)

    # Runtime reaches the database fences, while safety has no direct table
    # authority at all. Both fail before execution can open.
    for statement in (
        "UPDATE runtime_control SET kill_switch=false WHERE id=1",
        "UPDATE venue_runtime_control SET kill_switch=false "
        "WHERE venue='polymarket'",
    ):
        with pytest.raises(DBAPIError, match="kill-off lacks exact"):
            async with roles.runtime.begin() as session:
                await session.execute(text(statement))
        with pytest.raises(DBAPIError, match="permission denied"):
            async with roles.safety.begin() as session:
                await session.execute(text(statement))

    # Every DB-owned stop is audited, including a stop with no active lease;
    # replaying the same exact identity is idempotent and a collision fails.
    async with roles.runtime.begin() as session:
        assert await set_kill_switch(
            session,
            True,
            request_id="paper-role-no-lease-stop",
            actor="runtime-role-test",
            reason="prove a no-lease runtime stop is audited",
        ) == 0
    async with roles.runtime.begin() as session:
        assert await set_kill_switch(
            session,
            True,
            request_id="paper-role-no-lease-stop",
            actor="runtime-role-test",
            reason="prove a no-lease runtime stop is audited",
        ) == 0
    with pytest.raises(DBAPIError, match="audit identity collision"):
        async with roles.runtime.begin() as session:
            await set_kill_switch(
                session,
                True,
                request_id="paper-role-no-lease-stop",
                actor="different-runtime-actor",
                reason="prove a no-lease runtime stop is audited",
            )
    async with db_sessions() as owner:
        no_lease_audits = int(
            await owner.scalar(
                select(func.count(OperatorCommand.id)).where(
                    OperatorCommand.request_id == "paper-role-no-lease-stop"
                )
            )
            or 0
        )
    assert no_lease_audits == 1

    # Runtime receives broad table ACLs only for the generic 0044 contract;
    # the exact RLS policy makes every lease row and mutation invisible.
    with pytest.raises(DBAPIError, match="row-level security"):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO paper_canary_safety_leases (
                        lease_id, run_id, acquire_request_id,
                        resume_request_id, resume_actor, resume_reason,
                        watchdog_stop_request_id, direct_stop_request_id,
                        release_request_id, nonce_sha256, expires_at
                    ) VALUES (
                        'paper-runtime-forge-lease',
                        'paper-runtime-forge-run',
                        'paper-runtime-forge-acquire',
                        'paper-runtime-forge-resume',
                        'runtime-forge',
                        'runtime must not create safety authority',
                        'paper-runtime-forge-watchdog',
                        'paper-runtime-forge-direct',
                        'paper-runtime-forge-release',
                        repeat('a', 64),
                        clock_timestamp() + interval '15 seconds'
                    )
                    """
                )
            )

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("role-first"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with roles.runtime.begin() as session:
        assert int(
            await session.scalar(
                select(func.count(PaperCanarySafetyLease.id))
            )
            or 0
        ) == 0
        updated = await session.execute(
            text(
                "UPDATE paper_canary_safety_leases "
                "SET expires_at=clock_timestamp() + interval '30 seconds'"
            )
        )
        deleted = await session.execute(
            text("DELETE FROM paper_canary_safety_leases")
        )
        assert updated.rowcount == 0
        assert deleted.rowcount == 0
    with pytest.raises(DBAPIError, match="permission denied"):
        async with roles.safety.begin() as session:
            await session.execute(
                text("DELETE FROM paper_canary_safety_leases")
            )

    async with db_sessions() as owner:
        first_lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-role-first"
            )
        )
    assert first_lease is not None
    # Caller-controlled GUCs are inert: runtime still cannot consume a hidden
    # reserved stop identity by guessing it.
    with pytest.raises(
        DBAPIError,
        match="reserved canary request identity",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "SELECT set_config("
                    "'stratus.paper_canary_stop_request_id', "
                    ":request_id, true)"
                ),
                {"request_id": first_lease.direct_stop_request_id},
            )
            session.add(
                OperatorCommand(
                    request_id=first_lease.direct_stop_request_id,
                    command="run_job",
                    payload={"job": "prune-signals"},
                    actor="runtime-guc-forge",
                    reason="a custom GUC must not be authority",
                )
            )
            await session.flush()

    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-role-first",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    async with roles.runtime.begin() as session:
        await enqueue_command(
            session,
            request_id="paper-role-active-stop",
            command="stop",
            payload={"venue": "polymarket"},
            actor="runtime-role-test",
            reason="prove active runtime stop links its exact audit identity",
        )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    async with db_sessions() as owner:
        first_lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-role-first"
            )
        )
        reserved_stop = await owner.get(
            OperatorCommand,
            first_lease.stop_command_id if first_lease is not None else None,
        )
    assert first_lease is not None
    assert first_lease.state == "revoked_stopped"
    assert reserved_stop is not None
    assert reserved_stop.request_id == "paper-role-active-stop"
    assert reserved_stop.status == "succeeded"

    # Terminal history does not prevent a separately authorized next PAPER
    # canary, and the runtime still learns no lease row identity.
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("role-second"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-role-second",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-direct-role-second",
        actor="safety-role-test",
        reason="close the repeated PAPER canary safely",
    )


async def test_actual_safety_login_is_primitive_only_and_cannot_forge_resume(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)

    # The safety login is not a second application login. It can read only its
    # lease records and cannot inspect or mutate application/control data.
    forbidden_statements = (
        "SELECT * FROM runtime_control",
        "UPDATE runtime_control SET kill_switch=true WHERE id=1",
        "SELECT * FROM venue_runtime_control",
        "UPDATE venue_runtime_control SET kill_switch=true "
        "WHERE venue='polymarket'",
        "SELECT * FROM signals",
        "UPDATE signals SET status='expired' WHERE false",
        "SELECT * FROM orders",
        "DELETE FROM orders WHERE false",
        "SELECT * FROM operator_commands",
        "INSERT INTO operator_commands("
        "request_id, command, payload, actor, reason"
        ") VALUES ("
        "'paper-safety-forbidden-command', 'stop', "
        "'{\"venue\":\"polymarket\"}'::jsonb, "
        "'safety-forge', 'safety must not write commands'"
        ")",
    )
    for statement in forbidden_statements:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with roles.safety.begin() as session:
                await session.execute(text(statement))

    # Only the six narrow safety primitives are executable; owner/runtime
    # helpers cannot be invoked by the independently compromised login.
    forbidden_functions = (
        "SELECT public.engage_paper_canary_runtime_stop("
        "'paper-forbidden-runtime-stop', 'safety-forge', "
        "'must not invoke runtime helper')",
        "SELECT public.resume_paper_canary_exact(1::bigint)",
        "SELECT public.paper_canary_stop_core("
        "'paper-forbidden-core-stop', 'safety-forge', "
        "'must not invoke owner core', 'revoked_stopped')",
    )
    for statement in forbidden_functions:
        with pytest.raises(DBAPIError, match="permission denied"):
            async with roles.safety.begin() as session:
                await session.execute(text(statement))

    # Defense in depth: even if an ACL drifts later, the command trigger
    # explicitly rejects the safety session user on both INSERT and UPDATE.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                f'GRANT SELECT, INSERT, UPDATE ON operator_commands '
                f'TO "{roles.safety_role}"'
            )
        )
        await owner.execute(
            text(
                f'GRANT USAGE, SELECT ON SEQUENCE operator_commands_id_seq '
                f'TO "{roles.safety_role}"'
            )
        )
        owner.add(
            OperatorCommand(
                request_id="paper-owner-command-for-safety-mutation",
                command="run_job",
                payload={"job": "prune-signals"},
                actor="owner-fixture",
                reason="prove immutable command identity rejects safety",
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="safety login cannot enqueue or mutate operator commands",
        ):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "INSERT INTO operator_commands("
                        "request_id, command, payload, actor, reason"
                        ") VALUES ("
                        "'paper-safety-forged-resume', 'resume', "
                        "'{\"venue\":\"polymarket\"}'::jsonb, "
                        "'safety-forge', 'must be rejected explicitly'"
                        ")"
                    )
                )
        with pytest.raises(
            DBAPIError,
            match="safety login cannot enqueue or mutate operator commands",
        ):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "UPDATE operator_commands "
                        "SET command='resume', "
                        "payload='{\"venue\":\"polymarket\"}'::jsonb "
                        "WHERE request_id="
                        "'paper-owner-command-for-safety-mutation'"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    f'REVOKE ALL PRIVILEGES ON operator_commands '
                    f'FROM "{roles.safety_role}"'
                )
            )
            await owner.execute(
                text(
                    f'REVOKE ALL PRIVILEGES ON SEQUENCE '
                    f'operator_commands_id_seq FROM "{roles.safety_role}"'
                )
            )

    async with roles.safety() as session:
        assert await session.scalar(
            text("SELECT public.assert_stratus_safety_boundary(true)")
        ) is True


async def test_actual_runtime_cannot_delete_or_rewrite_command_audit(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                """
                INSERT INTO operator_commands(
                    request_id, command, payload, actor, reason,
                    status, detail, executed_at
                ) VALUES (
                    'paper-terminal-audit-runtime-guard',
                    'stop',
                    '{"venue":"polymarket"}'::jsonb,
                    'migration-guard-test',
                    'prove terminal command evidence is durable',
                    'succeeded',
                    'terminal command evidence',
                    clock_timestamp()
                )
                """
            )
        )
        await owner.execute(
            text(
                """
                INSERT INTO operator_commands(
                    request_id, command, payload, actor, reason
                ) VALUES (
                    'paper-pending-audit-runtime-guard',
                    'run_job',
                    '{"job":"audit-health"}'::jsonb,
                    'migration-guard-test',
                    'prove pending command lifecycle is monotonic'
                )
                """
            )
        )

    with pytest.raises(
        DBAPIError,
        match="operator command audit rows cannot be deleted",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "DELETE FROM operator_commands "
                    "WHERE request_id="
                    "'paper-terminal-audit-runtime-guard'"
                )
            )

    with pytest.raises(
        DBAPIError,
        match="new runtime operator command must be pending",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO operator_commands(
                        request_id, command, payload, actor, reason,
                        status, detail, executed_at
                    ) VALUES (
                        'paper-forged-terminal-runtime-audit',
                        'stop', '{"venue":"polymarket"}'::jsonb,
                        'forged-runtime', 'must remain pending on insert',
                        'succeeded', 'forged terminal detail',
                        clock_timestamp()
                    )
                    """
                )
            )

    with pytest.raises(
        DBAPIError,
        match="operator command audit identity is immutable",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE operator_commands SET id=id+1000000 "
                    "WHERE request_id="
                    "'paper-pending-audit-runtime-guard'"
                )
            )

    async with roles.runtime.begin() as session:
        await session.execute(
            text(
                """
                INSERT INTO operator_commands(
                    request_id, command, payload, actor, reason, created_at
                ) VALUES (
                    'paper-runtime-canonical-created-at',
                    'run_job', '{"job":"audit-health"}'::jsonb,
                    'runtime-clock-forge',
                    'database trigger must own the audit timestamp',
                    TIMESTAMPTZ '2000-01-01 00:00:00+00'
                )
                """
            )
        )

    with pytest.raises(DBAPIError, match="ck_operator_commands_lifecycle"):
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    """
                    INSERT INTO operator_commands(
                        request_id, command, payload, actor, reason,
                        status, detail, executed_at
                    ) VALUES (
                        'paper-owner-invalid-terminal-shape',
                        'stop', '{"venue":"polymarket"}'::jsonb,
                        'owner-shape-test',
                        'constraint must reject incomplete terminal rows',
                        'succeeded', NULL, NULL
                    )
                    """
                )
            )

    with pytest.raises(
        DBAPIError,
        match="terminal operator command audit is immutable",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE operator_commands "
                    "SET status='pending', detail=NULL, executed_at=NULL "
                    "WHERE request_id="
                    "'paper-terminal-audit-runtime-guard'"
                )
            )

    with pytest.raises(
        DBAPIError,
        match="operator command lifecycle transition is invalid",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE operator_commands "
                    "SET detail='runtime tried to rewrite pending evidence' "
                    "WHERE request_id="
                    "'paper-pending-audit-runtime-guard'"
                )
            )

    async with db_sessions() as owner:
        terminal = (
            await owner.execute(
                text(
                    """
                    SELECT status, detail, executed_at IS NOT NULL
                    FROM operator_commands
                    WHERE request_id =
                        'paper-terminal-audit-runtime-guard'
                    """
                )
            )
        ).one()
        pending = (
            await owner.execute(
                text(
                    """
                    SELECT status, detail, executed_at
                    FROM operator_commands
                    WHERE request_id =
                        'paper-pending-audit-runtime-guard'
                    """
                )
            )
        ).one()
        canonical_created_at = await owner.scalar(
            text(
                "SELECT created_at FROM operator_commands "
                "WHERE request_id='paper-runtime-canonical-created-at'"
            )
        )
    assert terminal == ("succeeded", "terminal command evidence", True)
    assert pending == ("pending", None, None)
    assert canonical_created_at is not None
    assert canonical_created_at.year > 2000


async def test_paper_only_fence_is_latched_before_first_lease(
    db_sessions: SessionFactory,
    canary_role_sessions: SimpleNamespace,
) -> None:
    roles = canary_role_sessions
    async with db_sessions() as owner:
        assert int(
            await owner.scalar(
                select(func.count(PaperCanarySafetyLease.id))
            )
            or 0
        ) == 0

    forbidden_updates = (
        (
            "UPDATE runtime_control SET mode='live' WHERE id=1",
            "non-PAPER runtime state",
        ),
        (
            "UPDATE runtime_control "
            "SET live_confirmation_until=clock_timestamp()+interval '5 minutes' "
            "WHERE id=1",
            "non-PAPER runtime state",
        ),
        (
            "UPDATE venue_runtime_control SET mode='live' "
            "WHERE venue='polymarket'",
            "unsafe Polymarket state",
        ),
        (
            "UPDATE venue_runtime_control "
            "SET live_confirmation_until=clock_timestamp()+interval '5 minutes' "
            "WHERE venue='polymarket'",
            "unsafe Polymarket state",
        ),
    )
    for statement, error in forbidden_updates:
        with pytest.raises(DBAPIError, match=error):
            async with roles.runtime.begin() as session:
                await session.execute(text(statement))

    async with db_sessions() as owner:
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert runtime is not None
    assert runtime.mode is ExecutionMode.PAPER
    assert runtime.live_confirmation_until is None
    assert polymarket is not None
    assert polymarket.mode is VenueMode.PAPER
    assert polymarket.live_confirmation_until is None


async def test_denied_exact_resume_commits_rejected_not_failed(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("denied-resume"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-denied-resume",
    )
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "UPDATE runtime_control "
                "SET halted_reason='integration denied resume' WHERE id=1"
            )
        )

    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (0, 1)
    async with db_sessions() as owner:
        command = await owner.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id
                == "paper-resume-denied-resume"
            )
        )
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert command is not None and command.status == "rejected"
    assert command.executed_at is not None
    assert "database safety authority" in (command.detail or "")
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True


async def test_actual_runtime_stop_requires_exact_effect_and_success_terminal_state(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    request_ids: list[str] = []

    # Pre-existing safe controls are not enough: each payload's exact rows must
    # be written in the same transaction that records STOP success.
    for suffix, payload in (
        ("binance", {"venue": "binance_spot"}),
        ("polymarket", {"venue": "polymarket"}),
        ("all", {"venue": "all"}),
    ):
        request_id = f"paper-runtime-forged-stop-{suffix}"
        request_ids.append(request_id)
        async with roles.runtime.begin() as runtime:
            await enqueue_command(
                runtime,
                request_id=request_id,
                command="stop",
                payload=payload,
                actor="runtime-terminal-forge-test",
                reason=f"prove stale safe controls cannot forge {suffix} STOP",
            )
        with pytest.raises(
            DBAPIError,
            match="runtime STOP succeeded before its exact database control effect",
        ):
            async with roles.runtime.begin() as runtime:
                await runtime.execute(
                    text(
                        "UPDATE operator_commands "
                        "SET status='succeeded', detail=:detail, "
                        "executed_at=clock_timestamp() "
                        "WHERE request_id=:request_id"
                    ),
                    {
                        "detail": f"forged {suffix} success without effect",
                        "request_id": request_id,
                    },
                )

    # A direct runtime transaction still cannot impersonate the owner
    # primitive by writing the two PAPER controls (or all three controls)
    # itself. current_user must be the primitive owner while session_user
    # remains the runtime login.
    for suffix in ("polymarket", "all"):
        request_id = f"paper-runtime-forged-stop-{suffix}"
        with pytest.raises(
            DBAPIError,
            match="runtime STOP succeeded before its exact database control effect",
        ):
            async with roles.runtime.begin() as runtime:
                await runtime.execute(
                    text(
                        "UPDATE runtime_control SET mode='paper'::execution_mode, "
                        "kill_switch=true, live_confirmation_until=NULL, "
                        "updated_at=clock_timestamp() WHERE id=1"
                    )
                )
                await runtime.execute(
                    text(
                        "UPDATE venue_runtime_control SET enabled=true, "
                        "mode='paper'::venue_mode, kill_switch=true, "
                        "live_confirmation_until=NULL, updated_at=clock_timestamp() "
                        "WHERE venue='polymarket'"
                    )
                )
                if suffix == "all":
                    await runtime.execute(
                        text(
                            "UPDATE venue_runtime_control SET enabled=true, "
                            "mode='backtest'::venue_mode, kill_switch=true, "
                            "live_confirmation_until=NULL, "
                            "updated_at=clock_timestamp() "
                            "WHERE venue='binance_spot'"
                        )
                    )
                await runtime.execute(
                    text(
                        "UPDATE operator_commands "
                        "SET status='succeeded', detail='forged direct effect', "
                        "executed_at=clock_timestamp() "
                        "WHERE request_id=:request_id"
                    ),
                    {"request_id": request_id},
                )

    # Even after exact controls are already safe, a pending STOP must never be
    # reclassified as rejected or failed by the runtime login.
    for forged_status in ("rejected", "failed"):
        request_id = f"paper-runtime-forged-stop-{forged_status}"
        request_ids.append(request_id)
        async with roles.runtime.begin() as runtime:
            await enqueue_command(
                runtime,
                request_id=request_id,
                command="stop",
                payload={"venue": "polymarket"},
                actor="runtime-terminal-forge-test",
                reason=f"prove runtime cannot forge {forged_status} STOP",
            )
        with pytest.raises(
            DBAPIError,
            match="runtime pending STOP may only transition to succeeded",
        ):
            async with roles.runtime.begin() as runtime:
                await runtime.execute(
                    text(
                        "UPDATE operator_commands "
                        "SET status=:status, detail=:detail, "
                        "executed_at=clock_timestamp() "
                        "WHERE request_id=:request_id"
                    ),
                    {
                        "status": forged_status,
                        "detail": f"forged {forged_status}",
                        "request_id": request_id,
                    },
                )

    async with db_sessions() as owner:
        pending = (
            await owner.execute(
                select(OperatorCommand.request_id, OperatorCommand.status)
                .where(OperatorCommand.request_id.in_(request_ids))
                .order_by(OperatorCommand.id)
            )
        ).all()
    assert pending == [(request_id, "pending") for request_id in request_ids]

    # The same login now consumes all three payload shapes. Polymarket and all
    # retain the owner primitive; Binance succeeds through the trigger only
    # after its control write has been flushed in the exact transaction.
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (5, 5)
    async with db_sessions() as owner:
        completed = (
            await owner.execute(
                select(
                    OperatorCommand.request_id,
                    OperatorCommand.status,
                    OperatorCommand.executed_at,
                )
                .where(OperatorCommand.request_id.in_(request_ids))
                .order_by(OperatorCommand.id)
            )
        ).all()
    assert [(row.request_id, row.status) for row in completed] == [
        (request_id, "succeeded") for request_id in request_ids
    ]
    assert all(row.executed_at is not None for row in completed)


async def test_queued_binance_stop_audits_success_and_progresses_queue(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    stop_request_id = "paper-queued-binance-stop"
    next_request_id = "paper-after-queued-binance-stop"

    async with roles.runtime.begin() as runtime:
        await enqueue_command(
            runtime,
            request_id=stop_request_id,
            command="stop",
            payload={"venue": "binance_spot"},
            actor="queued-binance-stop-test",
            reason="prove Binance STOP audit success and queue progression",
        )
        await enqueue_command(
            runtime,
            request_id=next_request_id,
            command="set_mode",
            payload={"venue": "binance_spot", "mode": "backtest"},
            actor="queued-binance-stop-test",
            reason="prove work after Binance STOP is not starved",
        )

    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (2, 2)

    async with db_sessions() as owner:
        audits = (
            await owner.execute(
                select(OperatorCommand)
                .where(
                    OperatorCommand.request_id.in_(
                        (stop_request_id, next_request_id)
                    )
                )
                .order_by(OperatorCommand.id)
            )
        ).scalars().all()
        binance = await owner.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )

    assert [audit.request_id for audit in audits] == [
        stop_request_id,
        next_request_id,
    ]
    assert [audit.status for audit in audits] == ["succeeded", "succeeded"]
    assert all(audit.executed_at is not None for audit in audits)
    assert "Kill switch ON for binance_spot" in (audits[0].detail or "")
    assert binance is not None
    assert binance.enabled is True
    assert binance.mode is VenueMode.BACKTEST
    assert binance.kill_switch is True
    assert binance.live_confirmation_until is None


@pytest.mark.parametrize("stop_path", ["runtime", "safety"])
async def test_stop_cleanup_lock_selection_and_pending_signal_admission_gates(
    stop_path: str,
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    actor = f"{stop_path}-skip-locked-test"
    reason = "prove locked low IDs do not starve later STOP cleanup rows"
    request_id = f"paper-{stop_path}-skip-locked-stop"

    async with db_sessions.begin() as owner:
        market_id = int(
            await owner.scalar(
                text(
                    """
                    INSERT INTO markets (
                        polymarket_id, event_id, market_date, kind, rules_raw,
                        resolves_at, status
                    ) VALUES (
                        :market_id, :market_id, CURRENT_DATE, 'high',
                        'pending Signal admission gate integration test',
                        clock_timestamp() + interval '1 day', 'active'
                    ) RETURNING id
                    """
                ),
                {"market_id": f"paper-{stop_path}-skip-locked-market"},
            )
        )
        await owner.execute(
            text(
                """
                INSERT INTO signals (
                    market_id, ts, side, model_prob, market_price, edge,
                    kelly_frac, size_usd, confidence, status
                )
                SELECT
                    :market_id,
                    TIMESTAMPTZ '2026-08-14 00:00:00+00'
                        + candidate * interval '1 microsecond',
                    'buy_yes', 0.6, 0.5, 0.1, 0.01, 20, 'B', 'pending'
                FROM generate_series(1, 513) AS candidate
                """
            ),
            {"market_id": market_id},
        )
        oldest_signal_id = int(
            await owner.scalar(
                text("SELECT min(id) FROM signals WHERE market_id=:market_id"),
                {"market_id": market_id},
            )
        )
        latest_signal_id = int(
            await owner.scalar(
                text("SELECT max(id) FROM signals WHERE market_id=:market_id"),
                {"market_id": market_id},
            )
        )

    async def invoke_stop(*, replay: bool = False) -> int:
        if stop_path == "runtime":
            async with roles.runtime.begin() as runtime:
                return await set_kill_switch(
                    runtime,
                    True,
                    request_id=request_id,
                    actor=actor,
                    reason=reason,
                )
        result = await stop_polymarket_atomically(
            roles.safety,
            request_id=request_id,
            actor=actor,
            reason=reason,
        )
        assert result.idempotent is replay
        return result.expired_signals

    blocker = db_sessions()
    blocker_transaction = await blocker.begin()
    try:
        await blocker.execute(
            text("SELECT id FROM signals WHERE id=:id FOR UPDATE"),
            {"id": oldest_signal_id},
        )
        # The batch cap is 512. With 513 pending rows and the oldest locked,
        # the later 512 rows must all be claimed; pre-limiting IDs returned 511.
        assert await invoke_stop() == 512
        async with db_sessions() as observer:
            statuses = (
                await observer.execute(
                    text(
                        "SELECT id, status FROM signals "
                        "WHERE id IN (:oldest, :latest) ORDER BY id"
                    ),
                    {"oldest": oldest_signal_id, "latest": latest_signal_id},
                )
            ).all()
            pending_count = int(
                await observer.scalar(
                    text(
                        "SELECT count(*) FROM signals "
                        "WHERE status='pending'::signal_status"
                    )
                )
                or 0
            )
        assert statuses == [
            (oldest_signal_id, "pending"),
            (latest_signal_id, "expired"),
        ]
        assert pending_count == 1
        async with roles.safety() as safety:
            assert await safety.scalar(
                text("SELECT safety_paper_canary_ready()")
            ) is False
        with pytest.raises(
            PaperCanaryLeaseUnavailableError,
            match="zero pending Signals",
        ):
            await acquire_paper_canary_lease_atomically(
                roles.safety,
                **_lease_args(f"{stop_path}-pending-acquire-block"),
            )
    finally:
        if blocker_transaction.is_active:
            await blocker_transaction.commit()
        await blocker.close()

    # Unlocking and replaying the exact succeeded STOP consumes the survivor
    # without changing its audit identity.
    assert await invoke_stop(replay=True) == 1
    async with db_sessions() as owner:
        assert int(
            await owner.scalar(
                text(
                    "SELECT count(*) FROM signals "
                    "WHERE status='pending'::signal_status"
                )
            )
            or 0
        ) == 0
        stop_audit_count = int(
            await owner.scalar(
                select(func.count(OperatorCommand.id)).where(
                    OperatorCommand.request_id == request_id
                )
            )
            or 0
        )
    assert stop_audit_count == 1

    lease_args = _lease_args(f"{stop_path}-pending-resume-block", ttl_seconds=30)
    await acquire_paper_canary_lease_atomically(roles.safety, **lease_args)
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                """
                INSERT INTO signals (
                    market_id, ts, side, model_prob, market_price, edge,
                    kelly_frac, size_usd, confidence, status
                ) VALUES (
                    :market_id, TIMESTAMPTZ '2026-08-14 00:00:01+00',
                    'buy_yes', 0.6, 0.5, 0.1, 0.01, 20, 'B', 'pending'
                )
                """
            ),
            {"market_id": market_id},
        )
    await _queue_reserved_resume(
        roles.runtime,
        request_id=str(lease_args["resume_request_id"]),
    )
    async with roles.safety() as safety:
        assert await safety.scalar(
            text("SELECT safety_paper_canary_ready()")
        ) is False
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (0, 1)
    async with db_sessions() as owner:
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        resume = await owner.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id == lease_args["resume_request_id"]
            )
        )
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert resume is not None and resume.status == "rejected"

    terminal = await stop_polymarket_atomically(
        roles.safety,
        request_id=f"paper-{stop_path}-pending-resume-cleanup",
        actor=actor,
        reason="close pending Signal resume-gate proof",
    )
    assert terminal.expired_signals == 1


async def test_any_live_order_history_blocks_ready_acquire_and_resume(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("live-history-gate"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-live-history-gate",
    )
    async with db_sessions.begin() as owner:
        market_id = int(
            (
                await owner.execute(
                    text(
                        """
                        INSERT INTO markets (
                            polymarket_id, event_id, station_id, market_date,
                            kind, rules_raw, resolves_at, status
                        ) VALUES (
                            'paper-live-history-gate',
                            'paper-live-history-gate',
                            (SELECT min(id) FROM stations),
                            CURRENT_DATE, 'high', 'integration rules',
                            clock_timestamp() + interval '1 day', 'active'
                        ) RETURNING id
                        """
                    )
                )
            ).scalar_one()
        )
        signal_id = int(
            (
                await owner.execute(
                    text(
                        """
                        INSERT INTO signals (
                            market_id, ts, side, model_prob, market_price,
                            edge, kelly_frac, size_usd, confidence, status
                        ) VALUES (
                            :market_id, clock_timestamp(), 'buy_yes', 0.6,
                            0.5, 0.1, 0.01, 20, 'B', 'pending'
                        ) RETURNING id
                        """
                    ),
                    {"market_id": market_id},
                )
            ).scalar_one()
        )
        order_id = int(
            (
                await owner.execute(
                    text(
                        """
                        INSERT INTO orders (
                            signal_id, mode, side, price, size_usd,
                            filled_usd, status
                        ) VALUES (
                            :signal_id, 'live', 'buy_yes', 0.53, 20, 0, 'open'
                        ) RETURNING id
                        """
                    ),
                    {"signal_id": signal_id},
                )
            ).scalar_one()
        )

    async with roles.safety() as safety:
        assert await safety.scalar(text("SELECT safety_paper_canary_ready()")) is False
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (0, 1)
    await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-stop-live-history-gate",
        actor="integration-safety",
        reason="Close denied LIVE-history resume lease.",
    )
    async with db_sessions.begin() as owner:
        await owner.execute(
            text("UPDATE orders SET status='canceled' WHERE id=:order_id"),
            {"order_id": order_id},
        )
    async with roles.safety() as safety:
        assert await safety.scalar(text("SELECT safety_paper_canary_ready()")) is False
    with pytest.raises(PaperCanaryLeaseUnavailableError, match="zero LIVE inventory"):
        await acquire_paper_canary_lease_atomically(
            roles.safety,
            **_lease_args("terminal-live-history-gate"),
        )


async def test_safety_api_stop_response_is_the_exact_lease_audit(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("api-stop-link"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-api-stop-link",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)
    async with db_sessions() as owner:
        runtime_before = await owner.get(RuntimeControl, 1)
        polymarket_before = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert runtime_before is not None and runtime_before.kill_switch is False
    assert polymarket_before is not None
    assert polymarket_before.kill_switch is False

    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    body = {
        "request_id": "paper-direct-api-stop-link",
        "command": "stop",
        "payload": {"venue": "polymarket"},
        "actor": "safety-api-link-test",
        "reason": "prove the API response and lease share one stop audit",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
        retry = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
    assert response.status_code == 200
    assert retry.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["idempotent"] is False
    assert retry.json()["created"] is False
    assert retry.json()["idempotent"] is True
    response_item = response.json()["item"]
    assert retry.json()["item"]["id"] == response_item["id"]
    async with db_sessions() as owner:
        lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-api-stop-link"
            )
        )
        command_count = int(
            await owner.scalar(
                select(func.count(OperatorCommand.id)).where(
                    OperatorCommand.request_id
                    == "paper-direct-api-stop-link"
                )
            )
            or 0
        )
    assert lease is not None and lease.state == "revoked_stopped"
    assert lease.stop_command_id == response_item["id"]
    assert lease.direct_stop_request_id == response_item["request_id"]
    assert response_item["request_id"] == "paper-direct-api-stop-link"
    assert response_item["status"] == "succeeded"
    assert command_count == 1


async def test_authenticated_emergency_stop_independently_revokes_active_lease(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    binance_before = await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("independent-stop"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-independent-stop",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)

    # Release and watchdog are still bound to their precommitted identities.
    for terminal_state in ("released", "expired_stopped"):
        with pytest.raises(DBAPIError, match="reserved lease identity"):
            async with db_sessions.begin() as owner:
                await owner.execute(
                    text(
                        "SELECT * FROM public.paper_canary_stop_core("
                        ":request_id, :actor, :reason, :terminal_state)"
                    ),
                    {
                        "request_id": f"paper-wrong-{terminal_state}",
                        "actor": "integration-owner-negative-test",
                        "reason": "prove release and watchdog remain exact",
                        "terminal_state": terminal_state,
                    },
                )

    async with db_sessions() as owner:
        lease_before = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-independent-stop"
            )
        )
        runtime_before = await owner.get(RuntimeControl, 1)
        polymarket_before = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert lease_before is not None and lease_before.state == "active"
    assert runtime_before is not None and runtime_before.kill_switch is False
    assert polymarket_before is not None
    assert polymarket_before.kill_switch is False

    independent_request_id = "paper-independent-emergency-stop"
    assert independent_request_id != lease_before.direct_stop_request_id
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    body = {
        "request_id": independent_request_id,
        "command": "stop",
        "payload": {"venue": "polymarket"},
        "actor": "independent-safety-operator",
        "reason": "revoke the active canary without collector state",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
        retry = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )

    assert response.status_code == 200
    assert retry.status_code == 200
    assert response.json()["created"] is True
    assert response.json()["idempotent"] is False
    assert retry.json()["created"] is False
    assert retry.json()["idempotent"] is True
    response_item = response.json()["item"]
    assert retry.json()["item"]["id"] == response_item["id"]

    async with db_sessions() as owner:
        lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-independent-stop"
            )
        )
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await owner.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        reserved_direct_count = int(
            await owner.scalar(
                select(func.count(OperatorCommand.id)).where(
                    OperatorCommand.request_id
                    == "paper-direct-independent-stop"
                )
            )
            or 0
        )
    assert lease is not None and lease.state == "revoked_stopped"
    assert lease.direct_stop_request_id == "paper-direct-independent-stop"
    assert lease.stop_command_id == response_item["id"]
    assert response_item["request_id"] == independent_request_id
    assert response_item["status"] == "succeeded"
    assert reserved_direct_count == 0
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True
    assert binance is not None and _binance_identity(binance) == binance_before


async def test_active_lease_rejects_succeeded_stop_audits_from_before_acquisition(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    headers = {"X-API-Key": SAFETY_TEST_API_KEY}
    stale_safety_body = {
        "request_id": "paper-stale-safety-stop",
        "command": "stop",
        "payload": {"venue": "polymarket"},
        "actor": "stale-safety-test",
        "reason": "create a succeeded audit before the lease exists",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=stale_safety_body,
        )
    assert created.status_code == 200

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("stale-safety-audit"),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        rejected = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json=stale_safety_body,
        )
    assert rejected.status_code == 409
    assert "predates the active canary lease" in rejected.json()["detail"]
    async with db_sessions() as owner:
        lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-stale-safety-audit"
            )
        )
        stale_command = await owner.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id == "paper-stale-safety-stop"
            )
        )
    assert lease is not None and lease.state == "active"
    assert lease.stop_command_id is None
    assert stale_command is not None and stale_command.executed_at is not None
    assert stale_command.created_at < lease.acquired_at
    assert stale_command.executed_at < lease.acquired_at
    await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-direct-stale-safety-audit",
        actor="stale-safety-test",
        reason="close the stale safety audit test lease",
    )

    async with roles.runtime.begin() as runtime:
        assert await set_kill_switch(
            runtime,
            True,
            request_id="paper-stale-runtime-stop",
            actor="stale-runtime-test",
            reason="create a runtime STOP audit before the next lease",
        ) == 0
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("stale-runtime-audit"),
    )
    with pytest.raises(DBAPIError, match="predates the active canary lease"):
        async with roles.runtime.begin() as runtime:
            await set_kill_switch(
                runtime,
                True,
                request_id="paper-stale-runtime-stop",
                actor="stale-runtime-test",
                reason="create a runtime STOP audit before the next lease",
            )
    async with db_sessions() as owner:
        lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-stale-runtime-audit"
            )
        )
    assert lease is not None and lease.state == "active"
    assert lease.stop_command_id is None
    await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-direct-stale-runtime-audit",
        actor="stale-runtime-test",
        reason="close the stale runtime audit test lease",
    )


async def test_reserved_direct_stop_after_distinct_runtime_winner_gets_own_audit(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("runtime-wins-direct"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with roles.runtime.begin() as runtime:
        await enqueue_command(
            runtime,
            request_id="paper-runtime-winner-stop",
            command="stop",
            payload={"venue": "polymarket"},
            actor="runtime-winner-test",
            reason="runtime STOP wins before collector direct STOP",
        )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)

    async with db_sessions() as owner:
        stopped_lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-runtime-wins-direct"
            )
        )
        runtime_stop = await owner.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id == "paper-runtime-winner-stop"
            )
        )
    assert stopped_lease is not None
    assert stopped_lease.state == "revoked_stopped"
    assert runtime_stop is not None and runtime_stop.status == "succeeded"
    assert stopped_lease.stop_command_id == runtime_stop.id
    assert runtime_stop.created_at >= stopped_lease.acquired_at
    assert runtime_stop.executed_at is not None
    assert runtime_stop.executed_at >= stopped_lease.acquired_at

    app = create_app(
        _settings(migrated_database_url),
        session_factory=roles.safety,
        emergency_session_factory=roles.safety_emergency,
    )
    body = {
        "request_id": "paper-direct-runtime-wins-direct",
        "command": "stop",
        "payload": {"venue": "polymarket"},
        "actor": "collector-direct-stop-test",
        "reason": "record exact collector STOP after runtime won the race",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
        replay = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": SAFETY_TEST_API_KEY},
            json=body,
        )
    assert created.status_code == 200
    assert created.json()["created"] is True
    assert created.json()["idempotent"] is False
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["idempotent"] is True
    assert replay.json()["item"]["id"] == created.json()["item"]["id"]

    async with db_sessions() as owner:
        lease_after = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-runtime-wins-direct"
            )
        )
        direct_stop = await owner.scalar(
            select(OperatorCommand).where(
                OperatorCommand.request_id
                == "paper-direct-runtime-wins-direct"
            )
        )
    assert lease_after is not None and direct_stop is not None
    assert lease_after.stop_command_id == runtime_stop.id
    assert direct_stop.id != runtime_stop.id
    assert direct_stop.status == "succeeded"
    assert direct_stop.created_at >= lease_after.acquired_at
    assert direct_stop.executed_at is not None
    assert direct_stop.executed_at >= lease_after.acquired_at


async def test_acquire_nowait_conflicts_with_uncommitted_operator_identity(
    db_sessions: SessionFactory,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    args = _lease_args("operator-table-race")
    blocker = roles.runtime()
    transaction = await blocker.begin()
    try:
        blocker.add(
            OperatorCommand(
                request_id="paper-direct-operator-table-race",
                command="run_job",
                payload={"job": "prune-signals"},
                actor="operator-table-race",
                reason="hold an uncommitted conflicting request identity",
            )
        )
        await blocker.flush()
        with pytest.raises(
            PaperCanaryLeaseUnavailableError,
            match="could not obtain lock on relation",
        ):
            await acquire_paper_canary_lease_atomically(
                roles.safety,
                **args,
            )
        await transaction.commit()
    finally:
        if transaction.is_active:
            await transaction.rollback()
        await blocker.close()

    with pytest.raises(
        PaperCanaryLeaseCollisionError,
        match="reserved PAPER canary request identity already exists",
    ):
        await acquire_paper_canary_lease_atomically(
            roles.safety,
            **args,
        )
    async with db_sessions() as owner:
        assert int(
            await owner.scalar(
                select(func.count(PaperCanarySafetyLease.id))
            )
            or 0
        ) == 0


async def test_terminal_lease_request_ids_remain_reserved_across_columns(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("historical-released"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    released = await release_paper_canary_lease_atomically(
        roles.safety,
        lease_id="paper-lease-historical-released",
        run_id="paper-run-historical-released",
        nonce=NONCE,
        release_request_id="paper-release-historical-released",
    )
    assert released.lease.state == "released"

    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("historical-expired-safe", ttl_seconds=1),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await asyncio.sleep(1.2)
    assert await run_paper_canary_watchdog_once(roles.safety) == (
        "paper-lease-historical-expired-safe"
    )

    async with db_sessions() as owner:
        historical_leases = list(
            (
                await owner.scalars(
                    select(PaperCanarySafetyLease).where(
                        PaperCanarySafetyLease.lease_id.in_(
                            (
                                "paper-lease-historical-released",
                                "paper-lease-historical-expired-safe",
                            )
                        )
                    )
                )
            ).all()
        )
    states = {lease.lease_id: lease.state for lease in historical_leases}
    linked_stop_ids = {
        lease.lease_id: lease.stop_command_id for lease in historical_leases
    }
    assert states == {
        "paper-lease-historical-released": "released",
        "paper-lease-historical-expired-safe": "expired_safe",
    }
    reserved_ids = {
        request_id
        for lease in historical_leases
        for request_id in (
            lease.resume_request_id,
            lease.watchdog_stop_request_id,
            lease.direct_stop_request_id,
        )
    }
    assert len(reserved_ids) == 6
    historical_direct_ids = {
        lease.direct_stop_request_id for lease in historical_leases
    }

    for request_id in reserved_ids:
        with pytest.raises(DBAPIError, match="reserved canary request identity"):
            async with roles.runtime.begin() as session:
                await enqueue_command(
                    session,
                    request_id=request_id,
                    command="run_job",
                    payload={"job": "prune-signals"},
                    actor="historical-reuse-test",
                    reason="historical request identities must never be reused",
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
        for request_id in reserved_ids - historical_direct_ids:
            response = await client.post(
                "/api/v1/emergency-stop",
                headers={"X-API-Key": SAFETY_TEST_API_KEY},
                json={
                    "request_id": request_id,
                    "command": "stop",
                    "payload": {"venue": "polymarket"},
                    "actor": "historical-reuse-test",
                    "reason": "reserved lease identity is not a fresh stop",
                },
            )
            assert response.status_code == 409
            assert "audit identity collision" in response.json()["detail"]

        for request_id in historical_direct_ids:
            exact_body = {
                "request_id": request_id,
                "command": "stop",
                "payload": {"venue": "polymarket"},
                "actor": "codex-paper-canary",
                "reason": "STRATUS PAPER canary terminal fail-closed stop.",
            }
            response = await client.post(
                "/api/v1/emergency-stop",
                headers={"X-API-Key": SAFETY_TEST_API_KEY},
                json=exact_body,
            )
            assert response.status_code == 200, (request_id, response.text)
            assert response.json()["item"]["request_id"] == request_id
            assert response.json()["item"]["status"] == "succeeded"
            replay = await client.post(
                "/api/v1/emergency-stop",
                headers={"X-API-Key": SAFETY_TEST_API_KEY},
                json=exact_body,
            )
            assert replay.status_code == 200
            assert replay.json()["item"]["request_id"] == request_id

            mismatched = await client.post(
                "/api/v1/emergency-stop",
                headers={"X-API-Key": SAFETY_TEST_API_KEY},
                json={
                    **exact_body,
                    "reason": "reserved lease identity is not reusable",
                },
            )
            assert mismatched.status_code == 409

    async with db_sessions() as owner:
        terminal_leases = list(
            (
                await owner.scalars(
                    select(PaperCanarySafetyLease).where(
                        PaperCanarySafetyLease.lease_id.in_(
                            tuple(linked_stop_ids)
                        )
                    )
                )
            ).all()
        )
    assert {
        lease.lease_id: lease.stop_command_id for lease in terminal_leases
    } == linked_stop_ids

    cross_column_args = _lease_args("cross-column-history")
    cross_column_args["resume_request_id"] = (
        "paper-direct-historical-released"
    )
    with pytest.raises(PaperCanaryLeaseCollisionError, match="identity already exists"):
        await acquire_paper_canary_lease_atomically(
            roles.safety,
            **cross_column_args,
        )


async def test_connection_boundaries_detect_rls_and_trigger_drift(
    db_sessions: SessionFactory,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                f"CREATE POLICY paper_canary_runtime_drift "
                "ON paper_canary_safety_leases FOR SELECT "
                f'TO "{roles.runtime_role}" USING (true)'
            )
        )
    try:
        with pytest.raises(DBAPIError, match="RLS boundary drifted"):
            async with roles.runtime.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_runtime_boundary(true)"
                    )
                )
        with pytest.raises(DBAPIError, match="RLS boundary drifted"):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "DROP POLICY paper_canary_runtime_drift "
                    "ON paper_canary_safety_leases"
                )
            )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "ALTER TABLE paper_canary_safety_leases "
                "FORCE ROW LEVEL SECURITY"
            )
        )
    try:
        for sessions in (roles.runtime, roles.safety):
            with pytest.raises(DBAPIError, match="RLS boundary drifted"):
                async with sessions.begin() as session:
                    boundary = (
                        "assert_stratus_runtime_boundary"
                        if sessions is roles.runtime
                        else "assert_stratus_safety_boundary"
                    )
                    await session.execute(
                        text(f"SELECT public.{boundary}(true)")
                    )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "ALTER TABLE paper_canary_safety_leases "
                    "NO FORCE ROW LEVEL SECURITY"
                )
            )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "ALTER TABLE runtime_control "
                "DISABLE TRIGGER trg_paper_canary_runtime_fence"
            )
        )
    try:
        with pytest.raises(DBAPIError, match="trigger boundary drifted"):
            async with roles.runtime.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_runtime_boundary(true)"
                    )
                )
        with pytest.raises(DBAPIError, match="trigger boundary drifted"):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "ALTER TABLE runtime_control "
                    "ENABLE TRIGGER trg_paper_canary_runtime_fence"
                )
            )

    # An enabled trigger with the right name/function but a narrower event
    # mask is still boundary drift: UPDATE-only reopens DELETE+INSERT
    # singleton replacement.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "DROP TRIGGER trg_paper_canary_runtime_fence "
                "ON runtime_control"
            )
        )
        await owner.execute(
            text(
                "CREATE TRIGGER trg_paper_canary_runtime_fence "
                "BEFORE UPDATE ON runtime_control FOR EACH ROW "
                "EXECUTE FUNCTION enforce_paper_canary_runtime_fence()"
            )
        )
    try:
        with pytest.raises(DBAPIError, match="trigger boundary drifted"):
            async with roles.runtime.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_runtime_boundary(true)"
                    )
                )
        with pytest.raises(DBAPIError, match="trigger boundary drifted"):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "DROP TRIGGER trg_paper_canary_runtime_fence "
                    "ON runtime_control"
                )
            )
            await owner.execute(
                text(
                    "CREATE TRIGGER trg_paper_canary_runtime_fence "
                    "BEFORE INSERT OR UPDATE OR DELETE "
                    "ON runtime_control FOR EACH ROW "
                    "EXECUTE FUNCTION enforce_paper_canary_runtime_fence()"
                )
            )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "GRANT EXECUTE ON FUNCTION "
                "resume_paper_canary_exact(bigint) "
                f'TO "{roles.safety_role}"'
            )
        )
    try:
        with pytest.raises(DBAPIError, match="function boundary drifted"):
            async with roles.runtime.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_runtime_boundary(true)"
                    )
                )
        with pytest.raises(DBAPIError, match="function ACL drifted"):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "REVOKE EXECUTE ON FUNCTION "
                    "resume_paper_canary_exact(bigint) "
                    f'FROM "{roles.safety_role}"'
                )
            )

    # Column grants are distinct from table ACLs in PostgreSQL and must not
    # become an invisible safety-login read path.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "GRANT SELECT(id) ON runtime_control "
                f'TO "{roles.safety_role}"'
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="direct or column table ACL drifted",
        ):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "REVOKE SELECT(id) ON runtime_control "
                    f'FROM "{roles.safety_role}"'
                )
            )

    # PUBLIC still has schema USAGE in a stock database. The exact boundary
    # must nevertheless detect removal of the independently auditable grant.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "REVOKE USAGE ON SCHEMA public "
                f'FROM "{roles.safety_group_role}"'
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="direct database or schema ACL drifted",
        ):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "GRANT USAGE ON SCHEMA public "
                    f'TO "{roles.safety_group_role}"'
                )
            )

    # A second BEFORE trigger can run after the named fence and rewrite NEW.
    # Exact trigger-set cardinality closes that owner-drift bypass.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "CREATE TRIGGER zz_paper_canary_drift "
                "BEFORE INSERT OR UPDATE ON operator_commands "
                "FOR EACH ROW EXECUTE FUNCTION "
                "enforce_paper_canary_request_reservation()"
            )
        )
    try:
        for sessions in (roles.runtime, roles.safety):
            with pytest.raises(
                DBAPIError,
                match="protected trigger set drifted",
            ):
                async with sessions.begin() as session:
                    boundary = (
                        "assert_stratus_runtime_boundary"
                        if sessions is roles.runtime
                        else "assert_stratus_safety_boundary"
                    )
                    await session.execute(
                        text(f"SELECT public.{boundary}(true)")
                    )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "DROP TRIGGER zz_paper_canary_drift "
                    "ON operator_commands"
                )
            )

    # Grant options are authority too; effective-privilege booleans alone do
    # not distinguish them from the intended non-delegable EXECUTE grant.
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "GRANT EXECUTE ON FUNCTION "
                "safety_paper_canary_ready() "
                f'TO "{roles.safety_role}" WITH GRANT OPTION'
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="exact function ACL drifted",
        ):
            async with roles.safety.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_safety_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "REVOKE EXECUTE ON FUNCTION "
                    "safety_paper_canary_ready() "
                    f'FROM "{roles.safety_role}"'
                )
            )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "GRANT EXECUTE ON FUNCTION "
                "paper_canary_execution_authorized() "
                f'TO "{roles.runtime_role}" WITH GRANT OPTION'
            )
        )
    try:
        with pytest.raises(
            DBAPIError,
            match="exact runtime function ACL drifted",
        ):
            async with roles.runtime.begin() as session:
                await session.execute(
                    text(
                        "SELECT public."
                        "assert_stratus_runtime_boundary(true)"
                    )
                )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "REVOKE EXECUTE ON FUNCTION "
                    "paper_canary_execution_authorized() "
                    f'FROM "{roles.runtime_role}"'
                )
            )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(
                "ALTER FUNCTION safety_paper_canary_acquire("
                "text,text,text,text,text,text,text,text,text,integer) "
                "SET session_replication_role TO replica"
            )
        )
    try:
        for sessions in (roles.runtime, roles.safety):
            with pytest.raises(
                DBAPIError,
                match="authority function hardening drifted",
            ):
                async with sessions.begin() as session:
                    boundary = (
                        "assert_stratus_runtime_boundary"
                        if sessions is roles.runtime
                        else "assert_stratus_safety_boundary"
                    )
                    await session.execute(
                        text(f"SELECT public.{boundary}(true)")
                    )
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "ALTER FUNCTION safety_paper_canary_acquire("
                    "text,text,text,text,text,text,text,text,text,integer) "
                    "RESET session_replication_role"
                )
            )

    async with roles.runtime() as session:
        assert await session.scalar(
            text("SELECT public.assert_stratus_runtime_boundary(true)")
        ) is True
    async with roles.safety() as session:
        assert await session.scalar(
            text("SELECT public.assert_stratus_safety_boundary(true)")
        ) is True


async def test_queued_binance_resume_and_mode_drift_are_rejected_unchanged(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    binance_before = await _set_fail_closed(db_sessions)
    for statement in (
        "UPDATE venue_runtime_control SET enabled=false "
        "WHERE venue='binance_spot'",
        "UPDATE venue_runtime_control SET mode='shadow'::venue_mode "
        "WHERE venue='binance_spot'",
        "UPDATE venue_runtime_control SET kill_switch=false "
        "WHERE venue='binance_spot'",
    ):
        with pytest.raises(DBAPIError, match="Binance Spot is sealed"):
            async with canary_role_sessions.runtime.begin() as session:
                await session.execute(text(statement))
    async with db_sessions.begin() as owner:
        owner.add_all(
            [
                OperatorCommand(
                    request_id="paper-queued-binance-resume",
                    command="resume",
                    payload={"venue": "binance_spot"},
                    actor="owner-injected",
                    reason="processor must reject stale Binance resume",
                ),
                OperatorCommand(
                    request_id="paper-queued-binance-shadow",
                    command="set_mode",
                    payload={
                        "venue": "binance_spot",
                        "mode": "shadow",
                    },
                    actor="owner-injected",
                    reason="processor must preserve sealed backtest mode",
                ),
            ]
        )
        await owner.flush()

    assert await process_operator_commands(
        _settings(migrated_database_url),
        canary_role_sessions.runtime,
    ) == (0, 2)
    async with db_sessions() as owner:
        binance = await owner.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
        statuses = (
            await owner.execute(
                select(OperatorCommand.status).where(
                    OperatorCommand.request_id.in_(
                        (
                            "paper-queued-binance-resume",
                            "paper-queued-binance-shadow",
                        )
                    )
                )
            )
        ).scalars().all()
    assert binance is not None
    assert _binance_identity(binance) == binance_before
    assert statuses == ["rejected", "rejected"]


async def test_actual_runtime_cannot_replace_singleton_control_rows(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    binance_before = await _set_fail_closed(db_sessions)
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        **_lease_args("replacement-fence"),
    )
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    await _queue_reserved_resume(
        roles.runtime,
        request_id="paper-resume-replacement-fence",
    )
    assert await process_operator_commands(
        _settings(migrated_database_url),
        roles.runtime,
    ) == (1, 1)

    # ORM deletion must not let the runtime login remove either singleton
    # while a live observed lease authorizes PAPER execution.
    with pytest.raises(
        DBAPIError,
        match="runtime control deletion is owner-only",
    ):
        async with roles.runtime.begin() as session:
            runtime = await session.get(RuntimeControl, 1)
            assert runtime is not None
            await session.delete(runtime)
            await session.flush()
    for venue in (
        TradingVenue.POLYMARKET,
        TradingVenue.BINANCE_SPOT,
    ):
        with pytest.raises(
            DBAPIError,
            match="venue runtime control deletion is owner-only",
        ):
            async with roles.runtime.begin() as session:
                control = await session.get(VenueRuntimeControl, venue)
                assert control is not None
                await session.delete(control)
                await session.flush()

    # Core INSERT is rejected by the trigger before uniqueness or check
    # constraints. This closes the second half of DELETE+INSERT replacement,
    # including a forged global/Polymarket open state and unsafe Binance state.
    with pytest.raises(
        DBAPIError,
        match="runtime control insertion is owner-only",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                insert(RuntimeControl).values(
                    id=2,
                    kill_switch=False,
                    mode=ExecutionMode.PAPER,
                )
            )
    with pytest.raises(
        DBAPIError,
        match="runtime control singleton identity is immutable",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text("UPDATE runtime_control SET id=2 WHERE id=1")
            )
    with pytest.raises(DBAPIError, match="ck_runtime_control_singleton_id"):
        async with db_sessions.begin() as owner:
            await owner.execute(
                insert(RuntimeControl).values(
                    id=2,
                    kill_switch=True,
                    mode=ExecutionMode.PAPER,
                )
            )
    for values in (
        {
            "venue": TradingVenue.POLYMARKET,
            "enabled": True,
            "mode": VenueMode.PAPER,
            "kill_switch": False,
            "risk_config_version": "replacement-test",
        },
        {
            "venue": TradingVenue.BINANCE_SPOT,
            "enabled": True,
            "mode": VenueMode.SHADOW,
            "kill_switch": False,
            "risk_config_version": "replacement-test",
        },
    ):
        with pytest.raises(
            DBAPIError,
            match="venue runtime control insertion is owner-only",
        ):
            async with roles.runtime.begin() as session:
                await session.execute(
                    insert(VenueRuntimeControl).values(**values)
                )
    with pytest.raises(
        DBAPIError,
        match="venue runtime control identity is immutable",
    ):
        async with roles.runtime.begin() as session:
            await session.execute(
                text(
                    "UPDATE venue_runtime_control "
                    "SET venue='binance_spot'::trading_venue "
                    "WHERE venue='polymarket'::trading_venue"
                )
            )

    # Exact resume remained effective throughout, and the same DB-owned STOP
    # path must still close both controls and terminalize the active lease.
    async with db_sessions() as owner:
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
        binance = await owner.get(
            VenueRuntimeControl,
            TradingVenue.BINANCE_SPOT,
        )
    assert runtime is not None and runtime.kill_switch is False
    assert polymarket is not None and polymarket.kill_switch is False
    assert binance is not None
    assert _binance_identity(binance) == binance_before

    stop = await stop_polymarket_atomically(
        roles.safety,
        request_id="paper-direct-replacement-fence",
        actor="replacement-fence-test",
        reason="prove replacement fences preserve atomic emergency stop",
    )
    assert stop.command.status == "succeeded"
    async with db_sessions() as owner:
        lease = await owner.scalar(
            select(PaperCanarySafetyLease).where(
                PaperCanarySafetyLease.lease_id
                == "paper-lease-replacement-fence"
            )
        )
        runtime = await owner.get(RuntimeControl, 1)
        polymarket = await owner.get(
            VenueRuntimeControl,
            TradingVenue.POLYMARKET,
        )
    assert lease is not None
    assert lease.state == "revoked_stopped"
    assert lease.stop_command_id == stop.command.id
    assert runtime is not None and runtime.kill_switch is True
    assert polymarket is not None and polymarket.kill_switch is True

    # Owner-only migration/repair remains possible. Exercise the same
    # replacement shape atomically and preserve every stored column.
    async with db_sessions.begin() as owner:
        replaced_runtime = await owner.scalar(
            text(
                """
                WITH deleted AS (
                    DELETE FROM runtime_control
                    WHERE id = 1
                    RETURNING *
                )
                INSERT INTO runtime_control (
                    id, kill_switch, mode, live_confirmation_until,
                    halted_reason, updated_at
                )
                SELECT
                    id, kill_switch, mode, live_confirmation_until,
                    halted_reason, updated_at
                FROM deleted
                RETURNING id
                """
            )
        )
        replaced_venues = (
            await owner.execute(
                text(
                    """
                    WITH deleted AS (
                        DELETE FROM venue_runtime_control
                        RETURNING *
                    )
                    INSERT INTO venue_runtime_control (
                        venue, enabled, mode, kill_switch, halted_reason,
                        live_confirmation_until, strategy_version,
                        risk_config_version, last_reconciled_at, updated_at
                    )
                    SELECT
                        venue, enabled, mode, kill_switch, halted_reason,
                        live_confirmation_until, strategy_version,
                        risk_config_version, last_reconciled_at, updated_at
                    FROM deleted
                    RETURNING venue::text
                    """
                )
            )
        ).scalars().all()
    assert replaced_runtime == 1
    assert set(replaced_venues) == {"polymarket", "binance_spot"}
