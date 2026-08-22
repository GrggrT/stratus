import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from stratus import safety_api
from stratus.config import (
    Settings,
    runtime_database_role_name,
    safety_database_group_role_name,
    safety_database_role_name,
)
from stratus.safety_api import (
    SafetyDatabaseBoundaryError,
    _assert_safety_checkout,
    _assert_safety_connection,
    create_app,
)
from stratus.services import safety_control
from stratus.services.safety_control import (
    PaperCanaryWatchdogStatus,
    PaperCanaryWatchdogTickResult,
    refresh_paper_canary_watchdog_status_once,
)

_SAFETY_KEY = "safety-control-key-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_abc"
_OPERATOR_KEY = "operator-control-key-0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_xyz"


class _BoundaryCursor:
    def __init__(
        self,
        row: tuple[object, ...] | None,
        *,
        execute_error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.row = row
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.statement: str | None = None
        self.closed = False
        self.connection: _BoundaryConnection | None = None
        self.autocommit_at_execute: bool | None = None

    def execute(self, statement: str) -> None:
        self.statement = statement
        assert self.connection is not None
        self.autocommit_at_execute = self.connection.autocommit
        if self.execute_error is not None:
            raise self.execute_error

    def fetchone(self) -> tuple[object, ...] | None:
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.row

    def close(self) -> None:
        self.closed = True


class _BoundaryConnection:
    def __init__(
        self,
        row: tuple[object, ...] | None,
        *,
        autocommit: bool = False,
        execute_error: Exception | None = None,
        fetch_error: Exception | None = None,
        cursor_error: Exception | None = None,
    ) -> None:
        self.boundary_cursor = _BoundaryCursor(
            row,
            execute_error=execute_error,
            fetch_error=fetch_error,
        )
        self.boundary_cursor.connection = self
        self._autocommit = autocommit
        self.autocommit_history: list[bool] = []
        self.cursor_error = cursor_error

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self.autocommit_history.append(value)
        self._autocommit = value

    def cursor(self) -> _BoundaryCursor:
        if self.cursor_error is not None:
            raise self.cursor_error
        return self.boundary_cursor


class _BoundaryConnectionRecord:
    def __init__(self) -> None:
        self.invalidated_with: BaseException | None = None

    def invalidate(
        self,
        exception: BaseException | None = None,
        soft: bool = False,
    ) -> None:
        assert soft is False
        self.invalidated_with = exception


def test_safety_api_refuses_to_start_without_independent_safety_key() -> None:
    with pytest.raises(ValueError, match="SAFETY_API_KEY"):
        create_app(Settings(_env_file=None))
    with pytest.raises(ValueError, match="SAFETY_API_KEY"):
        create_app(Settings(_env_file=None, safety_api_key="   "))
    with pytest.raises(ValueError, match="43 to 256"):
        create_app(Settings(_env_file=None, safety_api_key="too-short"))
    with pytest.raises(ValueError, match="placeholder"):
        create_app(
            Settings(
                _env_file=None,
                safety_api_key="change-me-to-a-real-independent-random-key-000000000000",
            )
        )
    with pytest.raises(ValueError, match="low-diversity"):
        create_app(Settings(_env_file=None, safety_api_key="A" * 43))
    with pytest.raises(ValueError, match="must be distinct"):
        create_app(
            Settings(
                _env_file=None,
                safety_api_key=_SAFETY_KEY,
                operator_api_key=_SAFETY_KEY,
            )
        )


def test_safety_database_url_uses_only_the_dedicated_secret(tmp_path: Path) -> None:
    safety_password_file = tmp_path / "safety-password"
    safety_password_file.write_text(
        "independent-safety-password-0001",
        encoding="utf-8",
    )
    settings = Settings(
        _env_file=None,
        stratus_safety_password_file=safety_password_file,
        stratus_database_name="stratus_test",
    )

    resolved = settings.resolved_safety_database_url()

    assert safety_database_role_name("stratus_test") in resolved
    assert runtime_database_role_name("stratus_test") not in resolved
    assert "independent-safety-password-0001" in resolved


def test_every_physical_safety_connection_rechecks_migration_boundary() -> None:
    accepted = _BoundaryConnection((True,))
    _assert_safety_connection(accepted, object())

    assert accepted.boundary_cursor.statement == (
        "SELECT public.assert_stratus_safety_boundary(true)"
    )
    assert accepted.boundary_cursor.autocommit_at_execute is True
    assert accepted.boundary_cursor.closed is True
    assert accepted.autocommit is False
    assert accepted.autocommit_history == [True, False]

    drifted_reconnect = _BoundaryConnection((False,))
    with pytest.raises(RuntimeError, match="safety boundary"):
        _assert_safety_connection(drifted_reconnect, object())
    assert drifted_reconnect.boundary_cursor.autocommit_at_execute is True
    assert drifted_reconnect.boundary_cursor.closed is True
    assert drifted_reconnect.autocommit is False
    assert drifted_reconnect.autocommit_history == [True, False]

    missing_assertion = _BoundaryConnection(None)
    with pytest.raises(RuntimeError, match="safety boundary"):
        _assert_safety_connection(missing_assertion, object())
    assert missing_assertion.boundary_cursor.closed is True
    assert missing_assertion.autocommit is False
    assert missing_assertion.autocommit_history == [True, False]


def test_every_safety_checkout_rechecks_and_invalidates_drift() -> None:
    accepted = _BoundaryConnection((True,))
    accepted_record = _BoundaryConnectionRecord()
    _assert_safety_checkout(accepted, accepted_record, object())
    assert accepted_record.invalidated_with is None
    assert accepted.autocommit_history == [True, False]

    drifted = _BoundaryConnection((False,))
    drifted_record = _BoundaryConnectionRecord()
    with pytest.raises(SafetyDatabaseBoundaryError, match="safety boundary") as caught:
        _assert_safety_checkout(drifted, drifted_record, object())

    assert drifted.boundary_cursor.closed is True
    assert drifted.autocommit_history == [True, False]
    assert drifted_record.invalidated_with is caught.value


@pytest.mark.parametrize(
    "connection",
    [
        _BoundaryConnection(None, execute_error=RuntimeError("execute failed")),
        _BoundaryConnection(None, fetch_error=RuntimeError("fetch failed")),
        _BoundaryConnection(None, cursor_error=RuntimeError("cursor failed")),
    ],
)
def test_safety_boundary_restores_autocommit_on_driver_failure(
    connection: _BoundaryConnection,
) -> None:
    with pytest.raises(RuntimeError):
        _assert_safety_connection(connection, object())

    assert connection.autocommit is False
    assert connection.autocommit_history == [True, False]


def test_safety_checkout_normalizes_and_invalidates_driver_failure() -> None:
    driver_error = RuntimeError("driver leaked catalog assertion detail")
    connection = _BoundaryConnection(None, execute_error=driver_error)
    record = _BoundaryConnectionRecord()

    with pytest.raises(
        SafetyDatabaseBoundaryError,
        match="safety database boundary assertion failed",
    ) as caught:
        _assert_safety_checkout(connection, record, object())

    assert connection.boundary_cursor.closed is True
    assert connection.autocommit_history == [True, False]
    assert record.invalidated_with is driver_error
    assert caught.value.__cause__ is driver_error
    assert "catalog assertion detail" not in str(caught.value)


async def test_health_is_open_but_emergency_stop_requires_exact_authentication() -> None:
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    body = {
        "request_id": "safety-auth-0001",
        "command": "stop",
        "reason": "authentication contract test",
        "actor": "unit-test",
        "payload": {"venue": "polymarket"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        health = await client.get("/healthz")
        missing = await client.post("/api/v1/emergency-stop", json=body)
        wrong = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": "wrong"},
            json=body,
        )

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert missing.status_code == 401
    assert wrong.status_code == 401


async def test_session_factory_injection_requires_two_distinct_pools() -> None:
    settings = Settings(_env_file=None, safety_api_key=_SAFETY_KEY)
    lone_factory = async_sessionmaker()
    with pytest.raises(ValueError, match="must be injected together"):
        create_app(settings, session_factory=lone_factory)
    with pytest.raises(ValueError, match="must be injected together"):
        create_app(settings, emergency_session_factory=lone_factory)
    with pytest.raises(ValueError, match="must be distinct"):
        create_app(
            settings,
            session_factory=lone_factory,
            emergency_session_factory=lone_factory,
        )
    injected = create_app(
        settings,
        session_factory=async_sessionmaker(),
        emergency_session_factory=async_sessionmaker(),
    )
    assert injected.state.watchdog_required is True

    engine = create_async_engine(
        "postgresql+asyncpg://stratus:unused@127.0.0.1:1/stratus"
    )
    try:
        with pytest.raises(ValueError, match="must use distinct binds"):
            create_app(
                settings,
                session_factory=async_sessionmaker(engine),
                emergency_session_factory=async_sessionmaker(engine),
            )
    finally:
        await engine.dispose()


async def test_readyz_uses_only_fresh_watchdog_cache_without_database_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = async_sessionmaker()
    emergency_sessions = async_sessionmaker()
    app = create_app(
        Settings(
            _env_file=None,
            safety_api_key=_SAFETY_KEY,
            safety_watchdog_poll_seconds=3,
        ),
        session_factory=sessions,
        emergency_session_factory=emergency_sessions,
        _watchdog_required=False,
    )
    app.state.watchdog_status.record_success(
        tick_started_monotonic=safety_control.monotonic(),
        control_state_ready=True,
        database_boundary="test_session_unverified",
    )

    def reject_database_session(_request: object) -> None:
        raise AssertionError("/readyz must not borrow the safety database pool")

    monkeypatch.setattr("stratus.safety_api._sessions", reject_database_session)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        ready = await client.get("/readyz")

    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "reasons": [],
        "database_boundary": "test_session_unverified",
    }


async def test_readyz_cache_fails_closed_before_tick_and_after_tick_failure() -> None:
    app = create_app(
        Settings(_env_file=None, safety_api_key=_SAFETY_KEY),
        session_factory=async_sessionmaker(),
        emergency_session_factory=async_sessionmaker(),
        _watchdog_required=False,
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        before_tick = await client.get("/readyz")
        app.state.watchdog_status.record_success(
            tick_started_monotonic=safety_control.monotonic(),
            control_state_ready=False,
            database_boundary="test_session_unverified",
        )
        unsafe = await client.get("/readyz")
        app.state.watchdog_status.record_failure()
        failed = await client.get("/readyz")

    assert before_tick.status_code == 503
    assert "database_unavailable" in before_tick.json()["reasons"]
    assert "paper_canary_watchdog_stale" in before_tick.json()["reasons"]
    assert before_tick.json()["database_boundary"] == "failed"
    assert unsafe.status_code == 503
    assert unsafe.json()["reasons"] == ["database_control_state_unsafe"]
    assert unsafe.json()["database_boundary"] == "test_session_unverified"
    assert failed.status_code == 503
    assert "database_unavailable" in failed.json()["reasons"]
    assert "database_control_state_unsafe" in failed.json()["reasons"]
    assert failed.json()["database_boundary"] == "failed"


async def test_watchdog_tick_refreshes_and_invalidates_readiness_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status = PaperCanaryWatchdogStatus()
    sessions = async_sessionmaker()
    clock = [100.0]
    monkeypatch.setattr(safety_control, "monotonic", lambda: clock[0])

    async def successful_tick(_sessions: object) -> PaperCanaryWatchdogTickResult:
        clock[0] = 104.0
        return PaperCanaryWatchdogTickResult(
            terminalized_lease_id=None,
            control_state_ready=True,
        )

    monkeypatch.setattr(
        safety_control,
        "run_paper_canary_watchdog_tick",
        successful_tick,
    )
    await refresh_paper_canary_watchdog_status_once(
        sessions,
        status,
        database_boundary="exact",
    )
    fresh = status.snapshot()
    assert fresh.latest_tick_succeeded is True
    assert fresh.control_state_ready is True
    assert fresh.database_boundary == "exact"
    assert fresh.success_age_seconds == 4.0
    assert fresh.consecutive_failures == 0

    async def failed_tick(_sessions: object) -> PaperCanaryWatchdogTickResult:
        raise RuntimeError("synthetic watchdog database failure")

    monkeypatch.setattr(
        safety_control,
        "run_paper_canary_watchdog_tick",
        failed_tick,
    )
    with pytest.raises(RuntimeError, match="synthetic watchdog database failure"):
        await refresh_paper_canary_watchdog_status_once(sessions, status)

    failed = status.snapshot()
    assert failed.latest_tick_succeeded is False
    assert failed.database_boundary == "failed"
    assert failed.consecutive_failures == 1


async def test_readyz_and_acquire_clamp_cache_age_below_database_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            safety_api_key=_SAFETY_KEY,
            safety_watchdog_poll_seconds=5,
        ),
        session_factory=async_sessionmaker(),
        emergency_session_factory=async_sessionmaker(),
    )
    app.state.watchdog_required = True
    app.state.watchdog_task = SimpleNamespace(done=lambda: False)
    monkeypatch.setattr(safety_control, "monotonic", lambda: 106.01)
    app.state.watchdog_status.record_success(
        tick_started_monotonic=100.0,
        control_state_ready=True,
        database_boundary="test_session_unverified",
    )
    acquire_body = {
        "lease_id": "paper-lease-cache-age",
        "run_id": "paper-run-cache-age",
        "acquire_request_id": "paper-acquire-cache-age",
        "resume_request_id": "paper-resume-cache-age",
        "direct_stop_request_id": "paper-direct-cache-age",
        "release_request_id": "paper-release-cache-age",
        "resume_actor": "unit-test",
        "resume_reason": "reject a cache older than the database safety margin",
        "nonce": "cache-age-test-nonce-" + ("n" * 32),
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        ready = await client.get("/readyz")
        acquired = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": _SAFETY_KEY},
            json=acquire_body,
        )

    assert ready.status_code == 503
    assert "paper_canary_watchdog_stale" in ready.json()["reasons"]
    assert acquired.status_code == 503
    assert acquired.json()["detail"] == "PAPER canary watchdog is not fresh"


@pytest.mark.parametrize("stop_is_ambiguous", [False, True])
async def test_acquire_revalidates_cache_and_uses_reserved_emergency_stop_pool(
    monkeypatch: pytest.MonkeyPatch,
    stop_is_ambiguous: bool,
) -> None:
    control_sessions = async_sessionmaker()
    emergency_sessions = async_sessionmaker()
    app = create_app(
        Settings(_env_file=None, safety_api_key=_SAFETY_KEY),
        session_factory=control_sessions,
        emergency_session_factory=emergency_sessions,
    )
    app.state.watchdog_required = True
    app.state.watchdog_task = SimpleNamespace(done=lambda: False)
    app.state.watchdog_status.record_success(
        tick_started_monotonic=safety_control.monotonic(),
        control_state_ready=True,
        database_boundary="test_session_unverified",
    )
    calls: list[tuple[object, str, str, str]] = []

    async def acquire_then_lose_readiness(
        sessions: object,
        **_kwargs: object,
    ) -> SimpleNamespace:
        assert sessions is control_sessions
        app.state.watchdog_status.record_failure()
        return SimpleNamespace()

    async def reserved_stop(
        sessions: object,
        *,
        request_id: str,
        actor: str,
        reason: str,
        now: object = None,
    ) -> SimpleNamespace:
        assert now is None
        calls.append((sessions, request_id, actor, reason))
        if stop_is_ambiguous:
            raise RuntimeError("synthetic ambiguous STOP response")
        return SimpleNamespace()

    monkeypatch.setattr(
        safety_api,
        "acquire_paper_canary_lease_atomically",
        acquire_then_lose_readiness,
    )
    monkeypatch.setattr(safety_api, "stop_polymarket_atomically", reserved_stop)
    body = {
        "lease_id": "paper-lease-post-check",
        "run_id": "paper-run-post-check",
        "acquire_request_id": "paper-acquire-post-check",
        "resume_request_id": "paper-resume-post-check",
        "direct_stop_request_id": "paper-direct-post-check",
        "release_request_id": "paper-release-post-check",
        "resume_actor": "unit-test",
        "resume_reason": "revalidate watchdog cache after acquisition commits",
        "nonce": "post-check-test-nonce-" + ("n" * 32),
    }

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": _SAFETY_KEY},
            json=body,
        )

    assert response.status_code == 503
    assert "fail-closed STOP requested" in response.json()["detail"]
    assert len(calls) == 1
    assert calls[0][0] is emergency_sessions
    assert calls[0][1] == body["direct_stop_request_id"]


async def test_lifespan_resets_cache_cancels_watchdog_and_disposes_both_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeEngine:
        def __init__(self) -> None:
            self.disposed = False

        async def dispose(self) -> None:
            self.disposed = True

    engines: list[FakeEngine] = []
    verified: list[FakeEngine] = []
    cancelled = 0

    def create_engine(_settings: Settings) -> FakeEngine:
        engine = FakeEngine()
        engines.append(engine)
        return engine

    async def verify_engine(engine: FakeEngine, _settings: Settings) -> None:
        verified.append(engine)

    def create_sessions(_engine: FakeEngine) -> async_sessionmaker:
        return async_sessionmaker()

    async def hanging_watchdog(*_args: object, **_kwargs: object) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        finally:
            cancelled += 1

    monkeypatch.setattr(safety_api, "create_safety_engine", create_engine)
    monkeypatch.setattr(safety_api, "verify_safety_engine", verify_engine)
    monkeypatch.setattr(safety_api, "create_session_factory", create_sessions)
    monkeypatch.setattr(safety_api, "run_paper_canary_watchdog", hanging_watchdog)
    monkeypatch.setattr(safety_api, "_WATCHDOG_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(safety_api, "_WATCHDOG_CANCEL_DRAIN_SECONDS", 0.1)
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    lifespan_statuses: list[PaperCanaryWatchdogStatus] = []

    async with app.router.lifespan_context(app):
        control_sessions = app.state.sessions
        emergency_sessions = app.state.emergency_sessions
        assert control_sessions is not emergency_sessions
        assert app.state.watchdog_status.snapshot().latest_tick_succeeded is False
        lifespan_statuses.append(app.state.watchdog_status)
        app.state.watchdog_status.record_success(
            tick_started_monotonic=safety_control.monotonic(),
            control_state_ready=True,
            database_boundary="exact",
        )
        await asyncio.sleep(0)

    assert app.state.sessions is None
    assert app.state.emergency_sessions is None
    assert app.state.watchdog_status is None

    async with app.router.lifespan_context(app):
        assert app.state.watchdog_status.snapshot().latest_tick_succeeded is False
        lifespan_statuses.append(app.state.watchdog_status)
        await asyncio.sleep(0)

    assert len(engines) == 4
    assert verified == engines
    assert all(engine.disposed for engine in engines)
    assert cancelled == 2
    assert lifespan_statuses[0] is not lifespan_statuses[1]


async def test_lifespan_blocks_reuse_after_watchdog_misses_cancel_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_orphan = asyncio.Event()
    watchdog_started = asyncio.Event()

    async def stubborn_watchdog(*_args: object, **_kwargs: object) -> None:
        watchdog_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release_orphan.wait()

    monkeypatch.setattr(safety_api, "run_paper_canary_watchdog", stubborn_watchdog)
    monkeypatch.setattr(safety_api, "_WATCHDOG_SHUTDOWN_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(safety_api, "_WATCHDOG_CANCEL_DRAIN_SECONDS", 0.01)
    app = create_app(
        Settings(_env_file=None, safety_api_key=_SAFETY_KEY),
        session_factory=async_sessionmaker(),
        emergency_session_factory=async_sessionmaker(),
    )

    with pytest.raises(
        safety_api.SafetyControlShutdownError,
        match="teardown did not complete cleanly",
    ):
        async with app.router.lifespan_context(app):
            await watchdog_started.wait()

    assert app.state.watchdog_status is None
    with pytest.raises(
        safety_api.SafetyControlShutdownError,
        match="lifespan reuse is blocked",
    ):
        async with app.router.lifespan_context(app):
            pass

    release_orphan.set()
    await asyncio.sleep(0)


async def test_lifespan_bounds_disposal_and_attempts_both_engines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispose_attempts: list[int] = []
    emergency_cancelled = False

    class FakeEngine:
        def __init__(self, identity: int) -> None:
            self.identity = identity

        async def dispose(self) -> None:
            nonlocal emergency_cancelled
            dispose_attempts.append(self.identity)
            if self.identity == 2:
                try:
                    await asyncio.Event().wait()
                finally:
                    emergency_cancelled = True

    engines: list[FakeEngine] = []

    def create_engine(_settings: Settings) -> FakeEngine:
        engine = FakeEngine(len(engines) + 1)
        engines.append(engine)
        return engine

    async def verify_engine(_engine: FakeEngine, _settings: Settings) -> None:
        return None

    def create_sessions(_engine: FakeEngine) -> async_sessionmaker:
        return async_sessionmaker()

    async def cooperative_watchdog(
        _settings: object,
        _sessions: object,
        stop_event: asyncio.Event,
        _status: object,
        **_kwargs: object,
    ) -> None:
        await stop_event.wait()

    monkeypatch.setattr(safety_api, "create_safety_engine", create_engine)
    monkeypatch.setattr(safety_api, "verify_safety_engine", verify_engine)
    monkeypatch.setattr(safety_api, "create_session_factory", create_sessions)
    monkeypatch.setattr(safety_api, "run_paper_canary_watchdog", cooperative_watchdog)
    monkeypatch.setattr(safety_api, "_ENGINE_DISPOSE_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(safety_api, "_ENGINE_DISPOSE_CANCEL_DRAIN_SECONDS", 0.1)
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))

    with pytest.raises(
        safety_api.SafetyControlShutdownError,
        match="teardown did not complete cleanly",
    ) as caught:
        async with app.router.lifespan_context(app):
            await asyncio.sleep(0)

    # Disposal is emergency first, then control. The timed-out first disposal
    # cannot prevent the independent second attempt.
    assert dispose_attempts == [2, 1]
    assert emergency_cancelled is True
    assert caught.value.failures
    assert app.state.sessions is None
    assert app.state.emergency_sessions is None


async def test_auth_failures_are_throttled_without_locking_out_valid_stop_key() -> None:
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    body = {
        "request_id": "safety-auth-throttle",
        "command": "stop",
        "reason": "authentication throttle contract test",
        "actor": "unit-test",
        "payload": {"venue": "polymarket"},
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        failures = [
            await client.post(
                "/api/v1/emergency-stop",
                headers={"X-API-Key": f"wrong-{attempt}"},
                json=body,
            )
            for attempt in range(6)
        ]
        # A correct emergency key must bypass/clear source throttling; the
        # endpoint then reaches the intentionally absent test DB session.
        valid = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": _SAFETY_KEY},
            json=body,
        )
        after_clear = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": "wrong-after-clear"},
            json=body,
        )

    assert [response.status_code for response in failures[:5]] == [401] * 5
    assert failures[5].status_code == 429
    assert failures[5].headers["retry-after"] == "60"
    assert valid.status_code == 503
    assert after_clear.status_code == 401


async def test_emergency_stop_schema_rejects_any_other_operation_before_database() -> None:
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    headers = {"X-API-Key": _SAFETY_KEY}
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        resume = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json={
                "request_id": "safety-schema-0001",
                "command": "resume",
                "reason": "must be refused",
                "actor": "unit-test",
                "payload": {"venue": "polymarket"},
            },
        )
        binance = await client.post(
            "/api/v1/emergency-stop",
            headers=headers,
            json={
                "request_id": "safety-schema-0002",
                "command": "stop",
                "reason": "must be refused",
                "actor": "unit-test",
                "payload": {"venue": "binance_spot"},
            },
        )

    assert resume.status_code == 422
    assert binance.status_code == 422


async def test_emergency_stop_schema_rejects_blank_audit_identity() -> None:
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/v1/emergency-stop",
            headers={"X-API-Key": _SAFETY_KEY},
            json={
                "request_id": "safety-schema-blank",
                "command": "stop",
                "reason": "    ",
                "actor": "unit-test",
                "payload": {"venue": "polymarket"},
            },
        )

    assert response.status_code == 422


async def test_canary_acquire_requires_reserved_direct_stop_identity() -> None:
    app = create_app(Settings(_env_file=None, safety_api_key=_SAFETY_KEY))
    body = {
        "lease_id": "paper-lease-schema",
        "run_id": "paper-run-schema",
        "acquire_request_id": "paper-acquire-schema",
        "resume_request_id": "paper-resume-schema",
        "release_request_id": "paper-release-schema",
        "resume_actor": "unit-test",
        "resume_reason": "prove direct STOP identity is mandatory",
        "nonce": "schema-test-nonce-" + ("n" * 32),
    }
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        missing = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": _SAFETY_KEY},
            json=body,
        )
        valid = await client.post(
            "/api/v1/canary-leases/acquire",
            headers={"X-API-Key": _SAFETY_KEY},
            json={
                **body,
                "direct_stop_request_id": "paper-direct-schema",
            },
        )

    assert missing.status_code == 422
    assert valid.status_code == 503


def test_compose_safety_control_is_loopback_bounded_and_independent() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    service = compose.split("\n  safety-control:", 1)[1].split("\n  backup:", 1)[0]

    assert "image: ${STRATUS_IMAGE:-stratus:local}" in service
    assert 'command: ["stratus-safety-api"]' in service
    assert '"127.0.0.1:${STRATUS_SAFETY_API_PORT:-8001}:8001"' in service
    assert 'STRATUS_DATABASE_POOL_SIZE: "1"' in service
    assert 'STRATUS_DATABASE_MAX_OVERFLOW: "0"' in service
    assert "mem_limit:" in service
    assert "healthcheck:" in service
    assert "http://127.0.0.1:8001/readyz" in service
    assert "http://127.0.0.1:8001/healthz" not in service
    assert "restart: unless-stopped" in service
    assert "provision-safety-role:" in service
    assert 'entrypoint: ["/usr/local/bin/stratus-runtime-entrypoint"]' in service
    assert "SAFETY_API_KEY: ${SAFETY_API_KEY:-}" in service
    assert "OPERATOR_API_KEY:" not in service
    assert "STRATUS_SAFETY_PASSWORD_FILE:" in service
    assert "source: stratus-safety-password" in service
    assert "STRATUS_RUNTIME_PASSWORD_FILE:" not in service
    assert "source: stratus-runtime-password" not in service
    assert "worker:" not in service
    assert "api:" not in service
    assert "validate-safety-credentials:" in service
    assert "networks:\n      - safety-plane" in service


def test_compose_safety_credentials_never_reach_runtime_services() -> None:
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    service_names = (
        "migrate",
        "worker",
        "binance-market-stream",
        "safety-control",
        "telegram",
        "provision-runtime-role",
        "provision-safety-role",
    )
    boundaries = [
        (match.start(), match.group(1)) for match in re.finditer(r"(?m)^  ([a-z0-9-]+):$", compose)
    ]
    service_blocks: dict[str, str] = {}
    for index, (start, name) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(compose)
        service_blocks[name] = compose[start:end]

    for name in service_names:
        block = service_blocks[name]
        assert "OPERATOR_API_KEY:" not in block
    for name in ("worker", "binance-market-stream", "api", "telegram"):
        block = service_blocks[name]
        assert "SAFETY_API_KEY" not in block
        assert "STRATUS_SAFETY_PASSWORD_FILE" not in block
        assert "stratus-safety-password" not in block
        assert "safety-plane" not in block

    worker = service_blocks["worker"]
    assert "safety-control:" in worker
    assert "condition: service_healthy" in worker

    postgres = service_blocks["postgres"]
    assert "networks:" in postgres
    assert "- default" in postgres
    assert "- safety-plane" in postgres

    provisioner = service_blocks["provision-safety-role"]
    assert "PGUSER: stratus" in provisioner
    assert "source: stratus-safety-password" in provisioner
    assert "source: stratus-runtime-password" in provisioner
    assert "STRATUS_SAFETY_PASSWORD must differ from STRATUS_RUNTIME_PASSWORD" in provisioner
    assert "STRATUS_SAFETY_PASSWORD must differ from POSTGRES_PASSWORD" in provisioner
    assert "STRATUS_RUNTIME_PASSWORD must differ from POSTGRES_PASSWORD" in provisioner
    assert "migrate:" in provisioner
    assert "stratus_safety_app_" in provisioner
    assert "stratus_runtime_" in provisioner
    assert "assert_stratus_safety_boundary(false)" in provisioner
    assert "assert_stratus_safety_boundary(true)" in provisioner
    assert "CREATE ROLE" not in provisioner
    assert "GRANT SELECT" not in provisioner
    assert "REVOKE ALL PRIVILEGES" not in provisioner

    validator = service_blocks["validate-safety-credentials"]
    assert "SAFETY_API_KEY: ${SAFETY_API_KEY:-}" in validator
    assert "OPERATOR_API_KEY: ${OPERATOR_API_KEY:-}" in validator
    assert "OPERATOR_API_KEY is required when safety-control is staged" in validator
    assert "SAFETY_API_KEY must be distinct from OPERATOR_API_KEY" in validator
    assert "43 to 256 URL-safe random characters" in validator
    assert "must not use a placeholder value" in validator
    assert "must not use a low-diversity repeated value" in validator
    assert "POSTGRES_PASSWORD" not in validator
    assert "stratus-runtime-password" not in validator
    assert "stratus-safety-password" not in validator

    api = service_blocks["api"]
    assert "OPERATOR_API_KEY: ${OPERATOR_API_KEY:-}" in api


def test_safety_role_names_and_provenance_match_the_migration() -> None:
    migration = Path("migrations/versions/20260730_0047_paper_canary_safety_lease.py").read_text(
        encoding="utf-8"
    )
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    safety_api = Path("src/stratus/safety_api.py").read_text(encoding="utf-8")

    assert safety_database_group_role_name("stratus").startswith("stratus_safety_")
    assert safety_database_role_name("stratus").startswith("stratus_safety_app_")
    for role_contract in (
        "'stratus_safety_' || md5(current_database())",
        "'stratus_safety_app_' || md5(current_database())",
    ):
        assert role_contract in migration
        assert role_contract in compose
        assert role_contract in safety_api
    for marker_contract in (
        "STRATUS managed safety group v1 database=%s",
        "STRATUS managed safety login v1 database=%s",
    ):
        assert marker_contract in migration
        assert marker_contract in safety_api
    assert "CREATE FUNCTION public.assert_stratus_safety_boundary(" in migration
    assert "SELECT public.assert_stratus_safety_boundary(false)" in migration
    for trigger_contract in (
        "trg_paper_canary_lease_audit",
        "trg_paper_canary_runtime_fence",
        "trg_paper_canary_venue_fence",
        "trg_paper_canary_request_reservation",
    ):
        assert trigger_contract in migration


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is unavailable")
def test_compose_rejects_shared_operator_and_safety_api_keys() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "POSTGRES_PASSWORD": "compose-test-owner-password-0001",
            "OPERATOR_API_KEY": "",
            "SAFETY_API_KEY": _SAFETY_KEY,
        }
    )
    missing_operator = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "validate-safety-credentials",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert missing_operator.returncode != 0
    assert "OPERATOR_API_KEY is required when safety-control is staged" in missing_operator.stderr

    environment["OPERATOR_API_KEY"] = _OPERATOR_KEY
    environment["SAFETY_API_KEY"] = ""
    missing_safety = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "validate-safety-credentials",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert missing_safety.returncode != 0
    assert "SAFETY_API_KEY is required by safety-control" in missing_safety.stderr

    for weak_key, expected_error in (
        ("too-short", "43 to 256 URL-safe random characters"),
        (
            "change-me-to-an-independent-random-key-00000000000000",
            "must not use a placeholder value",
        ),
        ("A" * 43, "must not use a low-diversity repeated value"),
    ):
        environment["SAFETY_API_KEY"] = weak_key
        weak = subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "--no-deps",
                "validate-safety-credentials",
            ],
            cwd=Path.cwd(),
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
        assert weak.returncode != 0
        assert expected_error in weak.stderr

    environment["OPERATOR_API_KEY"] = _OPERATOR_KEY
    environment["SAFETY_API_KEY"] = _OPERATOR_KEY
    rejected = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "validate-safety-credentials",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert rejected.returncode != 0
    assert "SAFETY_API_KEY must be distinct from OPERATOR_API_KEY" in rejected.stderr

    environment["SAFETY_API_KEY"] = _SAFETY_KEY
    accepted = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "--no-deps",
            "validate-safety-credentials",
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=60,
    )
    assert accepted.returncode == 0, accepted.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is unavailable")
def test_compose_rejects_any_equal_database_credentials(tmp_path: Path) -> None:
    owner_password = "pairwise-owner-password-000000000001"
    runtime_password = "pairwise-runtime-password-0000000001"
    safety_password = "pairwise-safety-password-00000000001"
    runtime_file = tmp_path / "runtime-password"
    safety_file = tmp_path / "safety-password"

    def run_provisioner(
        service: str,
        *,
        owner: str,
        runtime: str,
        safety: str,
    ) -> subprocess.CompletedProcess[str]:
        runtime_file.write_text(runtime, encoding="utf-8")
        safety_file.write_text(safety, encoding="utf-8")
        environment = os.environ.copy()
        environment.update(
            {
                "POSTGRES_PASSWORD": owner,
                "STRATUS_RUNTIME_PASSWORD_FILE": str(runtime_file),
                "STRATUS_SAFETY_PASSWORD_FILE": str(safety_file),
            }
        )
        return subprocess.run(
            [
                "docker",
                "compose",
                "run",
                "--rm",
                "--no-deps",
                service,
            ],
            cwd=Path.cwd(),
            env=environment,
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )

    runtime_owner_equal = run_provisioner(
        "provision-runtime-role",
        owner=owner_password,
        runtime=owner_password,
        safety=safety_password,
    )
    assert runtime_owner_equal.returncode != 0
    assert (
        "STRATUS_RUNTIME_PASSWORD must differ from POSTGRES_PASSWORD"
        in runtime_owner_equal.stderr
    )

    safety_owner_equal = run_provisioner(
        "provision-safety-role",
        owner=owner_password,
        runtime=runtime_password,
        safety=owner_password,
    )
    assert safety_owner_equal.returncode != 0
    assert (
        "STRATUS_SAFETY_PASSWORD must differ from POSTGRES_PASSWORD"
        in safety_owner_equal.stderr
    )

    runtime_owner_equal_via_safety = run_provisioner(
        "provision-safety-role",
        owner=owner_password,
        runtime=owner_password,
        safety=safety_password,
    )
    assert runtime_owner_equal_via_safety.returncode != 0
    assert (
        "STRATUS_RUNTIME_PASSWORD must differ from POSTGRES_PASSWORD"
        in runtime_owner_equal_via_safety.stderr
    )

    safety_runtime_equal = run_provisioner(
        "provision-safety-role",
        owner=owner_password,
        runtime=runtime_password,
        safety=runtime_password,
    )
    assert safety_runtime_equal.returncode != 0
    assert (
        "STRATUS_SAFETY_PASSWORD must differ from STRATUS_RUNTIME_PASSWORD"
        in safety_runtime_equal.stderr
    )
