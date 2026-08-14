from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app as create_runtime_app
from stratus.config import (
    Settings,
    runtime_database_role_name,
    safety_database_role_name,
)
from stratus.db.session import RuntimeDatabaseBoundaryError, create_engine
from stratus.safety_api import (
    SafetyDatabaseBoundaryError,
    create_safety_engine,
    verify_safety_engine,
)
from stratus.safety_api import (
    create_app as create_safety_app,
)
from stratus.services.safety_control import (
    refresh_paper_canary_watchdog_status_once,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
@pytest.fixture
async def checkout_boundary_engines(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> object:
    owner_url = make_url(migrated_database_url)
    database_name = owner_url.database
    assert database_name is not None
    runtime_role = runtime_database_role_name(database_name)
    safety_role = safety_database_role_name(database_name)
    runtime_password = "runtime-checkout-boundary-password-0047"
    safety_password = "safety-checkout-boundary-password-0047"

    async with db_sessions.begin() as owner:
        await owner.execute(
            text(f"ALTER ROLE \"{runtime_role}\" LOGIN PASSWORD '{runtime_password}'")
        )
        await owner.execute(
            text(f"ALTER ROLE \"{safety_role}\" LOGIN PASSWORD '{safety_password}'")
        )

    runtime_url = owner_url.set(
        username=runtime_role,
        password=runtime_password,
    ).render_as_string(hide_password=False)
    runtime_settings = Settings(
        _env_file=None,
        database_url=runtime_url,
        stratus_database_pool_size=2,
        stratus_database_max_overflow=0,
    )
    safety_password_file = tmp_path / "safety-password"
    safety_password_file.write_text(safety_password, encoding="utf-8")
    safety_settings = Settings(
        _env_file=None,
        stratus_safety_password_file=safety_password_file,
        stratus_database_host=owner_url.host or "127.0.0.1",
        stratus_database_port=owner_url.port or 5432,
        stratus_database_name=database_name,
        stratus_database_application_name="stratus-safety-checkout-test",
        stratus_database_pool_size=1,
        stratus_database_max_overflow=0,
        safety_api_key="safety-checkout-api-key-0047-xxxxxxxxxxxxxxxx",
        operator_api_key="operator-checkout-api-key-0047-yyyyyyyyyyyyyy",
    )
    runtime_engine = create_engine(
        runtime_settings,
        pool_size=1,
        max_overflow=0,
    )
    safety_engine = create_safety_engine(safety_settings)
    safety_emergency_engine = create_safety_engine(safety_settings)
    try:
        async with runtime_engine.connect() as connection:
            runtime_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        async with safety_engine.connect() as connection:
            safety_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(runtime_pid, int)
        assert isinstance(safety_pid, int)
        yield SimpleNamespace(
            runtime_engine=runtime_engine,
            safety_engine=safety_engine,
            safety_emergency_engine=safety_emergency_engine,
            safety_settings=safety_settings,
            runtime_settings=runtime_settings,
            runtime_role=runtime_role,
            safety_role=safety_role,
            runtime_password=runtime_password,
            safety_password=safety_password,
            runtime_pid=runtime_pid,
            safety_pid=safety_pid,
        )
    finally:
        await runtime_engine.dispose()
        await safety_engine.dispose()
        await safety_emergency_engine.dispose()
        async with db_sessions.begin() as owner:
            await owner.execute(text(f'ALTER ROLE "{runtime_role}" NOLOGIN PASSWORD NULL'))
            await owner.execute(text(f'ALTER ROLE "{safety_role}" NOLOGIN PASSWORD NULL'))


async def test_runtime_warm_pool_rejects_disabled_trigger_on_next_checkout(
    db_sessions: SessionFactory,
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    async with db_sessions.begin() as owner:
        await owner.execute(
            text("ALTER TABLE runtime_control DISABLE TRIGGER trg_paper_canary_runtime_fence")
        )
    try:
        with pytest.raises(
            RuntimeDatabaseBoundaryError,
            match="runtime database boundary assertion failed",
        ) as caught:
            async with boundary.runtime_engine.connect():
                pytest.fail("drifted warm runtime connection must not be borrowed")
        assert type(caught.value) is RuntimeDatabaseBoundaryError
        cause = caught.value.__cause__
        assert cause is not None
        cause_text = str(cause)
        assert "trigger boundary drifted" in cause_text
        assert boundary.runtime_password not in cause_text
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text("ALTER TABLE runtime_control ENABLE TRIGGER trg_paper_canary_runtime_fence")
            )

    async with boundary.runtime_engine.connect() as connection:
        replacement_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
    assert replacement_pid != boundary.runtime_pid


async def test_runtime_http_sanitizes_warm_checkout_boundary_failure(
    db_sessions: SessionFactory,
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    app = create_runtime_app(boundary.runtime_settings)

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            async with db_sessions.begin() as owner:
                await owner.execute(
                    text(
                        "ALTER TABLE runtime_control DISABLE TRIGGER trg_paper_canary_runtime_fence"
                    )
                )
            try:
                rejected = await client.get("/api/v1/overview")
            finally:
                async with db_sessions.begin() as owner:
                    await owner.execute(
                        text(
                            "ALTER TABLE runtime_control "
                            "ENABLE TRIGGER trg_paper_canary_runtime_fence"
                        )
                    )

    assert rejected.status_code == 503
    assert rejected.json() == {
        "detail": "runtime database boundary rejected",
        "retryable": False,
    }
    assert "trigger" not in rejected.text.lower()


async def test_safety_engine_diagnostic_accepts_exact_select_only_boundary(
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    await verify_safety_engine(
        boundary.safety_engine,
        boundary.safety_settings,
    )


async def test_safety_warm_pool_and_readiness_reject_disabled_rls(
    db_sessions: SessionFactory,
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    safety_sessions = async_sessionmaker(
        boundary.safety_engine,
        expire_on_commit=False,
    )
    safety_emergency_sessions = async_sessionmaker(
        boundary.safety_emergency_engine,
        expire_on_commit=False,
    )
    app = create_safety_app(
        boundary.safety_settings,
        session_factory=safety_sessions,
        emergency_session_factory=safety_emergency_sessions,
        _watchdog_required=False,
    )
    await refresh_paper_canary_watchdog_status_once(
        safety_sessions,
        app.state.watchdog_status,
    )

    async with db_sessions.begin() as owner:
        await owner.execute(
            text("ALTER TABLE paper_canary_safety_leases DISABLE ROW LEVEL SECURITY")
        )
    try:
        with pytest.raises(
            SafetyDatabaseBoundaryError,
            match="safety database boundary assertion failed",
        ) as caught:
            await refresh_paper_canary_watchdog_status_once(
                safety_sessions,
                app.state.watchdog_status,
            )
        assert type(caught.value) is SafetyDatabaseBoundaryError
        cause = caught.value.__cause__
        assert cause is not None
        cause_text = str(cause)
        assert "RLS boundary drifted" in cause_text
        assert boundary.safety_password not in cause_text
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            rejected = await client.get("/readyz")
        assert rejected.status_code == 503
        assert rejected.json()["database_boundary"] == "failed"
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text("ALTER TABLE paper_canary_safety_leases ENABLE ROW LEVEL SECURITY")
            )

    await refresh_paper_canary_watchdog_status_once(
        safety_sessions,
        app.state.watchdog_status,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        recovered = await client.get("/readyz")
    assert recovered.status_code == 200
    assert recovered.json()["database_boundary"] == "exact"
    async with boundary.safety_engine.connect() as connection:
        replacement_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
    assert replacement_pid != boundary.safety_pid


async def test_safety_warm_pool_rejects_acl_drift_on_next_checkout(
    db_sessions: SessionFactory,
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    async with db_sessions.begin() as owner:
        await owner.execute(
            text(f'GRANT DELETE ON TABLE paper_canary_safety_leases TO "{boundary.safety_role}"')
        )
    try:
        with pytest.raises(
            SafetyDatabaseBoundaryError,
            match="safety database boundary assertion failed",
        ) as caught:
            async with boundary.safety_engine.connect():
                pytest.fail("ACL-drifted warm safety connection must not be borrowed")
        assert type(caught.value) is SafetyDatabaseBoundaryError
        cause = caught.value.__cause__
        assert cause is not None
        cause_text = str(cause)
        assert "table ACL drifted" in cause_text
        assert boundary.safety_password not in cause_text
    finally:
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(
                    "REVOKE DELETE ON TABLE paper_canary_safety_leases "
                    f'FROM "{boundary.safety_role}"'
                )
            )

    async with boundary.safety_engine.connect() as connection:
        replacement_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
    assert replacement_pid != boundary.safety_pid
