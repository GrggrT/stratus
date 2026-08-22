from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app as create_runtime_app
from stratus.config import (
    Settings,
    runtime_database_role_name,
    safety_database_role_name,
)
from stratus.db.session import (
    RuntimeDatabaseBoundaryError,
    create_engine,
    create_read_session_factory,
)
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


async def test_runtime_checkout_boundary_is_transaction_neutral(
    db_sessions: SessionFactory,
    checkout_boundary_engines: object,
) -> None:
    boundary = checkout_boundary_engines
    application_name = "stratus-checkout-autocommit-test"
    settings = boundary.runtime_settings.model_copy(
        update={"stratus_database_application_name": application_name}
    )
    checkout_entered = threading.Event()
    checkout_release = threading.Event()
    thread_errors: list[BaseException] = []
    warm_backend_pid: list[int] = []

    def hold_after_boundary(
        _dbapi_connection: object,
        _connection_record: object,
        _connection_proxy: object,
    ) -> None:
        checkout_entered.set()
        if not checkout_release.wait(timeout=10):
            raise TimeoutError("test checkout hold was not released")

    async def borrow_read_session() -> None:
        engine = create_engine(settings, pool_size=1, max_overflow=0, read_only=True)
        read_sessions = create_read_session_factory(engine, max_concurrent=1)
        try:
            async with read_sessions() as session:
                backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(backend_pid, int)
            warm_backend_pid.append(backend_pid)

            # Attach after the first borrow/check-in so the hold observes the
            # same warm connection immediately after its next boundary proof.
            event.listen(engine.sync_engine, "checkout", hold_after_boundary)
            async with read_sessions() as session:
                assert await session.scalar(text("SELECT pg_backend_pid()")) == backend_pid
        finally:
            await engine.dispose()

    def run_borrower() -> None:
        try:
            asyncio.run(borrow_read_session())
        except BaseException as exc:  # pragma: no cover - re-raised in test thread
            thread_errors.append(exc)

    borrower = threading.Thread(target=run_borrower, daemon=True)
    borrower.start()
    try:
        assert await asyncio.to_thread(checkout_entered.wait, 10)
        async with db_sessions() as observer:
            rows = (
                await observer.execute(
                    text(
                        "SELECT pid, state FROM pg_stat_activity "
                        "WHERE datname=current_database() "
                        "AND usename=:runtime_role "
                        "AND application_name=:application_name"
                    ),
                    {
                        "runtime_role": boundary.runtime_role,
                        "application_name": application_name,
                    },
                )
            ).all()
        assert warm_backend_pid
        assert rows == [(warm_backend_pid[0], "idle")]
    finally:
        checkout_release.set()
        await asyncio.to_thread(borrower.join, 10)

    assert not borrower.is_alive()
    if thread_errors:
        raise thread_errors[0]


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
