from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings, get_settings, runtime_database_role_name
from stratus.db.models import RuntimeControl, StorageCapacitySample
from stratus.jobs.audit_storage_capacity import audit_storage_capacity
from stratus.jobs.execution_capacity import (
    acquire_execution_capacity_gate,
    engage_capacity_failure,
)
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.storage_capacity import (
    acquire_capacity_write_lock,
    read_capacity_measurement,
)
from stratus.services.storage_capacity import CAPACITY_POLICY_VERSION

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def test_explicit_probe_path_does_not_require_pg_read_all_settings(
    db_sessions: SessionFactory,
    tmp_path: Path,
) -> None:
    async with db_sessions.begin() as session:
        database_name = await session.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        app_role = runtime_database_role_name(database_name)
        await session.execute(text(f'SET LOCAL ROLE "{app_role}"'))

        with pytest.raises(DBAPIError, match="permission denied to examine"):
            async with session.begin_nested():
                await session.scalar(text("SELECT current_setting('data_directory')"))

        measurement = await read_capacity_measurement(
            session,
            captured_at=datetime.now(UTC),
            storage_path_override=str(tmp_path),
        )

    assert measurement.point.database_bytes > 0
    assert measurement.point.free_disk_bytes is not None
    assert measurement.point.free_disk_bytes > 0
    assert measurement.free_disk_path == str(tmp_path)


async def test_capacity_audit_persists_append_only_database_measurement(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> None:
    settings = get_settings().model_copy(
        update={
            "clob_ledger_capacity_path": str(tmp_path),
            "clob_ledger_capacity_min_free_bytes": 500_000_000,
        }
    )
    assessment = await audit_storage_capacity(settings, db_sessions)

    assert assessment.severity == "info"
    assert assessment.reason == "measurement_window_pending"
    async with db_sessions() as session:
        sample = await session.scalar(select(StorageCapacitySample))
        assert sample is not None
        assert sample.database_bytes > 0
        assert sample.clob_book_batches_bytes > 0
        assert sample.clob_market_snapshots_bytes > 0
        assert sample.free_disk_bytes is not None and sample.free_disk_bytes > 0
        assert sample.retention_policy_version == CAPACITY_POLICY_VERSION
        assert sample.details["automatic_deletion"] is False
        assert await session.scalar(select(func.count()).select_from(StorageCapacitySample)) == 1

    app = create_app(
        Settings(
            database_url=migrated_database_url,
            operator_api_key=None,
            clob_ledger_capacity_min_free_bytes=500_000_000,
        )
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/storage-capacity")
    assert response.status_code == 200
    payload = response.json()
    assert payload["reason"] == "measurement_window_pending"
    assert payload["measurement_complete"] is False
    assert payload["automatic_deletion"] is False
    assert payload["ledger_bytes"] > 0
    assert payload["sample_fresh"] is True
    assert payload["free_disk_bytes"] == sample.free_disk_bytes
    assert payload["minimum_free_disk_bytes"] == 500_000_000

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(
                text("UPDATE storage_capacity_samples SET database_bytes = database_bytes + 1")
            )


async def test_capacity_probe_failure_is_persisted_blocking_and_engages_kill_switch(
    db_sessions: SessionFactory,
    active_paper_canary: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert active_paper_canary is not None
    missing_path = tmp_path / "missing-capacity-probe"
    settings = get_settings().model_copy(update={"clob_ledger_capacity_path": str(missing_path)})
    async with db_sessions() as session:
        control = await session.get(RuntimeControl, 1)
        assert control is not None and control.kill_switch is False

    async def notification_failure(*_: object) -> bool:
        raise RuntimeError("telegram queue unavailable")

    monkeypatch.setattr(
        "stratus.jobs.audit_storage_capacity.enqueue_notification",
        notification_failure,
    )

    assessment = await audit_storage_capacity(settings, db_sessions)

    assert assessment.severity == "blocking"
    assert assessment.reason == "free_disk_measurement_unavailable"
    async with db_sessions.begin() as session:
        sample = await session.scalar(select(StorageCapacitySample))
        control = await session.get(RuntimeControl, 1)
        assert sample is not None
        assert sample.free_disk_bytes is None
        assert sample.details["free_disk_error"]
        assert control is not None and control.kill_switch is True


async def test_capacity_write_advisory_lock_serializes_collectors(
    db_sessions: SessionFactory,
) -> None:
    second_acquired = asyncio.Event()

    async def acquire_second() -> None:
        async with db_sessions.begin() as session:
            await acquire_capacity_write_lock(session)
            second_acquired.set()

    async with db_sessions.begin() as first_session:
        await acquire_capacity_write_lock(first_session)
        waiter = asyncio.create_task(acquire_second())
        await asyncio.sleep(0.05)
        assert second_acquired.is_set() is False

    # The lock hand-off is the subject of this test; Docker Desktop connection
    # startup can legitimately exceed two seconds on a healthy local host.
    await asyncio.wait_for(waiter, timeout=10)
    assert second_acquired.is_set() is True


async def test_capacity_kill_serializes_after_inflight_intent_and_blocks_next_execution(
    db_sessions: SessionFactory,
    active_paper_canary: object,
    tmp_path: Path,
) -> None:
    assert active_paper_canary is not None
    settings = get_settings().model_copy(
        update={
            "clob_ledger_capacity_path": str(tmp_path),
            "clob_ledger_capacity_min_free_bytes": 500_000_000,
        }
    )
    assessment = await audit_storage_capacity(settings, db_sessions)
    assert assessment.severity != "blocking"
    async with db_sessions() as session:
        assert (await get_runtime_control(session)).kill_switch is False
    intent_locked = asyncio.Event()
    release_intent = asyncio.Event()
    kill_committed = asyncio.Event()
    commit_order: list[str] = []

    async def execution_intent() -> None:
        async with db_sessions.begin() as session:
            gate = await acquire_execution_capacity_gate(
                session,
                settings,
                now=datetime.now(UTC),
            )
            assert gate.allowed is True
            assert (await get_runtime_control(session)).kill_switch is False
            intent_locked.set()
            await release_intent.wait()
        commit_order.append("intent")

    async def capacity_kill() -> None:
        await intent_locked.wait()
        async with db_sessions.begin() as session:
            await engage_capacity_failure(session, reason="concurrent_test")
        commit_order.append("kill")
        kill_committed.set()

    intent_task = asyncio.create_task(execution_intent())
    kill_task = asyncio.create_task(capacity_kill())
    # Opening a fresh asyncpg connection through Docker Desktop can exceed two
    # seconds on an otherwise healthy local host.  The assertion below tests
    # advisory-lock ordering, not connection-startup latency.
    await asyncio.wait_for(intent_locked.wait(), timeout=10)
    await asyncio.sleep(0.05)
    assert kill_committed.is_set() is False
    release_intent.set()
    await asyncio.gather(intent_task, kill_task)

    assert commit_order == ["intent", "kill"]
    async with db_sessions.begin() as session:
        gate = await acquire_execution_capacity_gate(
            session,
            settings,
            now=datetime.now(UTC),
        )
        control = await get_runtime_control(session)
        assert gate.allowed is True
        assert control.kill_switch is True
