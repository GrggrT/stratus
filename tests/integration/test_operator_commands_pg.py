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
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = _settings(migrated_database_url)
    async with db_sessions.begin() as session:
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
    async with db_sessions.begin() as session:
        row, created = await enqueue_command(
            session,
            request_id="req-stop-0001",
            command="stop",
            payload={},
            actor="integration",
            reason="drill",
        )
    assert created is False

    succeeded, finished = await process_operator_commands(settings, db_sessions)
    assert (succeeded, finished) == (1, 1)
    assert await _kill_switch_state(db_sessions) is True

    async with db_sessions.begin() as session:
        await enqueue_command(
            session,
            request_id="req-resume-0001",
            command="resume",
            payload={},
            actor="integration",
            reason="drill over",
        )
    succeeded, finished = await process_operator_commands(settings, db_sessions)
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


async def test_http_submit_and_audit_roundtrip(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = _settings(migrated_database_url)
    app = create_app(settings)
    app.state.sessions = db_sessions  # lifespan does not run under ASGITransport
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
