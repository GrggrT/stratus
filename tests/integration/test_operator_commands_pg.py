"""End-to-end operator command queue against a real Postgres:

enqueue (idempotent), poll, execute, audit — including the full HTTP path
through the FastAPI app with the API key enforced.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    Market,
    MarketStatus,
    OperatorCommand,
    RuntimeControl,
    WeatherKind,
)
from stratus.jobs.process_operator_commands import process_operator_commands
from stratus.repositories.operator_commands import enqueue_command
from stratus.services.safety_control import (
    acquire_paper_canary_lease_atomically,
    run_paper_canary_watchdog_once,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _settings(url: str) -> Settings:
    return Settings(database_url=url, operator_api_key="secret-key")


async def _kill_switch_state(sessions: SessionFactory) -> bool:
    async with sessions() as session:
        control = await session.scalar(
            select(RuntimeControl).where(RuntimeControl.id == 1)
        )
        assert control is not None
        return control.kill_switch


async def test_stop_resume_cycle_through_the_queue(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    canary_role_sessions: object,
) -> None:
    roles = canary_role_sessions
    settings = _settings(migrated_database_url)
    async with roles.runtime.begin() as session:
        _, created = await enqueue_command(
            session,
            request_id="req-stop-0001",
            command="stop",
            payload={},
            actor="integration",
            reason="drill",
        )
    assert created is True

    # Retried request with the same id must not enqueue twice.
    async with roles.runtime.begin() as session:
        row, created = await enqueue_command(
            session,
            request_id="req-stop-0001",
            command="stop",
            payload={},
            actor="integration",
            reason="drill",
        )
    assert created is False

    succeeded, finished = await process_operator_commands(
        settings,
        roles.runtime,
    )
    assert (succeeded, finished) == (1, 1)
    assert await _kill_switch_state(db_sessions) is True

    nonce = "n" * 32
    await acquire_paper_canary_lease_atomically(
        roles.safety,
        lease_id="lease-resume-0001",
        run_id="run-resume-00001",
        acquire_request_id="req-acquire-0001",
        resume_request_id="req-resume-0001",
        direct_stop_request_id="req-direct-stop-0001",
        release_request_id="req-release-0001",
        resume_actor="integration",
        resume_reason="drill over",
        nonce=nonce,
        ttl_seconds=15,
    )
    # Acquisition deliberately sets watchdog_at == acquired_at. The separate
    # control-plane pass must observe it before the reserved resume may queue.
    assert await run_paper_canary_watchdog_once(roles.safety) is None
    async with roles.runtime.begin() as session:
        await enqueue_command(
            session,
            request_id="req-resume-0001",
            command="resume",
            payload={"venue": "polymarket"},
            actor="integration",
            reason="drill over",
        )
    succeeded, finished = await process_operator_commands(
        settings,
        roles.runtime,
    )
    assert (succeeded, finished) == (1, 1)
    assert await _kill_switch_state(db_sessions) is False

    async with db_sessions() as session:
        rows = (
            (await session.execute(select(OperatorCommand).order_by(OperatorCommand.id)))
            .scalars()
            .all()
        )
    assert [row.status for row in rows] == ["succeeded", "succeeded"]
    assert all(row.executed_at is not None for row in rows)


async def test_verify_market_command_unblocks_the_market(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = _settings(migrated_database_url)
    async with db_sessions.begin() as session:
        station_market = Market(
            polymarket_id="pm-verify-1",
            event_id="ev-1",
            market_date=datetime(2026, 7, 7, tzinfo=UTC).date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=datetime(2026, 7, 8, 4, tzinfo=UTC),
            status=MarketStatus.BLOCKED,
            station_verified=False,
            station_verification_reason="station_unknown",
        )
        session.add(station_market)
        await session.flush()
        market_id = station_market.id
        # No station: the command must be rejected, not crash the queue.
        await enqueue_command(
            session,
            request_id="req-verify-0001",
            command="verify_market",
            payload={"market_id": market_id},
            actor="integration",
            reason="operator approval",
        )

    succeeded, finished = await process_operator_commands(settings, db_sessions)
    assert (succeeded, finished) == (0, 1)

    async with db_sessions() as session:
        command = (
            await session.execute(select(OperatorCommand))
        ).scalar_one()
    assert command.status == "rejected"
    assert "station parser" in (command.detail or "")


async def test_set_mode_paper_switches_and_live_is_gated(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    from stratus.db.models import ExecutionMode, RuntimeControl
    from stratus.jobs.process_operator_commands import process_operator_commands

    settings = _settings(migrated_database_url)

    async def enqueue(request_id: str, mode: str) -> None:
        async with db_sessions.begin() as session:
            await enqueue_command(
                session,
                request_id=request_id,
                command="set_mode",
                payload={"mode": mode},
                actor="integration",
                reason="mode test",
            )

    # Paper switch always succeeds.
    await enqueue("req-mode-0001", "paper")
    assert await process_operator_commands(settings, db_sessions) == (1, 1)

    # Live without the operator flags is rejected outright.
    await enqueue("req-mode-0002", "live")
    succeeded, finished = await process_operator_commands(settings, db_sessions)
    assert (succeeded, finished) == (0, 1)
    async with db_sessions() as session:
        rejected = (
            await session.execute(
                select(OperatorCommand).where(
                    OperatorCommand.request_id == "req-mode-0002"
                )
            )
        ).scalar_one()
    assert rejected.status == "rejected"
    assert "PAPER-only" in (rejected.detail or "")

    # Even with operator flags and a wallet, the release boundary remains
    # authoritative: no confirmation window may open and LIVE is unreachable.
    gated = Settings(
        database_url=migrated_database_url,
        operator_api_key="secret-key",
        live_legal_approved=True,
        live_tax_approved=True,
        polygon_private_key="0xtestkey",
    )

    await enqueue("req-mode-0003", "live")
    assert await process_operator_commands(gated, db_sessions) == (0, 1)
    async with db_sessions() as session:
        control = await session.get(RuntimeControl, 1)
        blocked = (
            await session.execute(
                select(OperatorCommand).where(
                    OperatorCommand.request_id == "req-mode-0003"
                )
            )
        ).scalar_one()
    assert control is not None and control.mode is ExecutionMode.PAPER
    assert control.live_confirmation_until is None
    assert blocked.status == "rejected"
    assert "paper-only" in (blocked.detail or "").lower()


async def test_http_submit_and_audit_roundtrip(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = _settings(migrated_database_url)
    app = create_app(settings)
    app.state.sessions = db_sessions  # lifespan does not run under ASGITransport
    app.state.read_sessions = app.state.sessions
    headers = {"X-API-Key": "secret-key"}
    body = {
        "request_id": "req-http-00001",
        "command": "run_job",
        "reason": "manual retention run",
        "payload": {"job": "prune-signals"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        accepted = await client.post("/api/v1/commands", headers=headers, json=body)
        duplicate = await client.post("/api/v1/commands", headers=headers, json=body)

        assert accepted.status_code == 202
        assert accepted.json()["created"] is True
        assert duplicate.json()["created"] is False
        assert duplicate.json()["item"]["id"] == accepted.json()["item"]["id"]

        succeeded, finished = await process_operator_commands(settings, db_sessions)
        assert (succeeded, finished) == (1, 1)

        audit = await client.get("/api/v1/commands", headers=headers)

    items = audit.json()["items"]
    assert len(items) == 1
    assert items[0]["status"] == "succeeded"
    assert "prune-signals completed" in items[0]["detail"]
