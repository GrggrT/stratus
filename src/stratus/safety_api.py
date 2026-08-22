from __future__ import annotations

import asyncio
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal, Protocol, cast

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stratus.config import (
    Settings,
    get_settings,
    safety_database_group_role_name,
    safety_database_role_name,
)
from stratus.db.models import (
    OperatorCommand,
    PaperCanarySafetyLease,
)
from stratus.db.session import create_session_factory
from stratus.repositories.emergency_stop import (
    EmergencyStopCommandStateError,
    EmergencyStopIdentityCollisionError,
)
from stratus.repositories.paper_canary_leases import (
    PaperCanaryLeaseError,
)
from stratus.services.safety_control import (
    PaperCanaryWatchdogStatus,
    acquire_paper_canary_lease_atomically,
    heartbeat_paper_canary_lease_atomically,
    release_paper_canary_lease_atomically,
    run_paper_canary_watchdog,
    stop_polymarket_atomically,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]

_CONTROL_PLANE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._~-]{43,256}$")
_CONTROL_PLANE_KEY_WEAK_PREFIXES = (
    "change-me",
    "changeme",
    "replace-me",
    "example",
    "placeholder",
    "test-secret",
)
_AUTH_FAILURE_WINDOW_SECONDS = 60.0
_AUTH_FAILURE_LIMIT = 5
_AUTH_FAILURE_MAX_SOURCES = 256
_READINESS_MAX_AGE_SECONDS = 6.0
_WATCHDOG_SHUTDOWN_TIMEOUT_SECONDS = 6.0
_WATCHDOG_CANCEL_DRAIN_SECONDS = 1.0
_ENGINE_DISPOSE_TIMEOUT_SECONDS = 1.0
_ENGINE_DISPOSE_CANCEL_DRAIN_SECONDS = 0.25
_POST_ACQUIRE_STOP_ACTOR = "safety-control"
_POST_ACQUIRE_STOP_REASON = (
    "PAPER canary acquisition lost watchdog readiness after commit."
)

_SAFETY_TABLE_PRIVILEGES: dict[str, frozenset[str]] = {
    "paper_canary_safety_leases": frozenset({"SELECT"}),
}
_SAFETY_SEQUENCE_PRIVILEGES: dict[str, frozenset[str]] = {}
_TABLE_PRIVILEGES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "TRUNCATE",
    "REFERENCES",
    "TRIGGER",
)
_SEQUENCE_PRIVILEGES = ("USAGE", "SELECT", "UPDATE")
_SAFETY_CONNECTION_BOUNDARY_SQL = "SELECT public.assert_stratus_safety_boundary(true)"


def _validated_control_plane_key(value: str | None, *, name: str) -> str:
    if value is None or not value:
        raise ValueError(f"{name} is required by stratus-safety-api")
    if value != value.strip() or _CONTROL_PLANE_KEY_PATTERN.fullmatch(value) is None:
        raise ValueError(
            f"{name} must contain 43 to 256 URL-safe random characters"
        )
    lowered = value.lower()
    if lowered.startswith(_CONTROL_PLANE_KEY_WEAK_PREFIXES):
        raise ValueError(f"{name} must not use a placeholder value")
    if len(set(value)) < 8:
        raise ValueError(f"{name} must not use a low-diversity repeated value")
    return value


class _AuthFailureLimiter:
    """Bound failed-auth state without ever blocking a valid emergency key."""

    def __init__(self) -> None:
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def record_failure(self, source: str) -> bool:
        now = time.monotonic()
        cutoff = now - _AUTH_FAILURE_WINDOW_SECONDS
        with self._lock:
            attempts = self._failures.get(source)
            if attempts is None:
                if len(self._failures) >= _AUTH_FAILURE_MAX_SOURCES:
                    self._failures.popitem(last=False)
                attempts = deque()
                self._failures[source] = attempts
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            attempts.append(now)
            self._failures.move_to_end(source)
            return len(attempts) > _AUTH_FAILURE_LIMIT

    def clear(self, source: str) -> None:
        with self._lock:
            self._failures.pop(source, None)
_SAFETY_CONNECTION_DIAGNOSTIC_SQL = """
WITH expected_names AS (
    SELECT
        ('stratus_safety_' || md5(current_database()))::name AS group_name,
        ('stratus_safety_app_' || md5(current_database()))::name AS app_name,
        format(
            'STRATUS managed safety group v1 database=%s',
            current_database()
        ) AS group_marker,
        format(
            'STRATUS managed safety login v1 database=%s',
            current_database()
        ) AS app_marker
),
expected_tables(
    relation_name,
    can_select,
    can_insert,
    can_update,
    can_delete,
    can_truncate,
    can_references,
    can_trigger
) AS (
    VALUES
        (
            'paper_canary_safety_leases',
            true, false, false, false, false, false, false
        )
),
expected_sequences(relation_name, can_usage, can_select, can_update) AS (
    SELECT
        unused.relation_name,
        unused.can_usage,
        unused.can_select,
        unused.can_update
    FROM (
        VALUES ('unused', false, false, false)
    ) AS unused(relation_name, can_usage, can_select, can_update)
    WHERE false
)
SELECT
    current_setting('server_version_num')::integer >= 160000
    AND session_user = names.app_name
    AND current_user = names.app_name
    AND current_setting('session_replication_role') = 'origin'
    AND (
        SELECT count(*) = 2
        FROM pg_roles role_row
        WHERE role_row.rolname IN (names.group_name, names.app_name)
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_roles role_row
        WHERE role_row.rolname IN (names.group_name, names.app_name)
          AND (
              role_row.rolsuper
              OR NOT role_row.rolinherit
              OR role_row.rolcreatedb
              OR role_row.rolcreaterole
              OR role_row.rolreplication
              OR role_row.rolbypassrls
              OR role_row.rolconfig IS NOT NULL
              OR (
                  role_row.rolname = names.group_name
                  AND (
                      role_row.rolcanlogin
                      OR shobj_description(role_row.oid, 'pg_authid')
                         IS DISTINCT FROM names.group_marker
                  )
              )
              OR (
                  role_row.rolname = names.app_name
                  AND (
                      NOT role_row.rolcanlogin
                      OR shobj_description(role_row.oid, 'pg_authid')
                         IS DISTINCT FROM names.app_marker
                  )
              )
          )
    )
    AND (
        SELECT count(*) = 1
        FROM pg_auth_members membership
        WHERE membership.roleid = names.group_name::regrole
          AND membership.member = names.app_name::regrole
          AND membership.grantor = 'stratus'::regrole
          AND NOT membership.admin_option
          AND membership.inherit_option
          AND NOT membership.set_option
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_auth_members membership
        WHERE (
            membership.member IN (
                names.group_name::regrole,
                names.app_name::regrole
            )
            OR membership.roleid IN (
                names.group_name::regrole,
                names.app_name::regrole
            )
        )
        AND NOT (
            membership.roleid = names.group_name::regrole
            AND membership.member = names.app_name::regrole
            AND membership.grantor = 'stratus'::regrole
            AND NOT membership.admin_option
            AND membership.inherit_option
            AND NOT membership.set_option
        )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_db_role_setting
        WHERE setrole IN (
            names.group_name::regrole,
            names.app_name::regrole
        )
    )
    AND NOT has_parameter_privilege(
        names.group_name,
        'session_replication_role',
        'SET'
    )
    AND NOT has_parameter_privilege(
        names.app_name,
        'session_replication_role',
        'SET'
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_shdepend
        WHERE refclassid = 'pg_authid'::regclass
          AND refobjid IN (
              names.group_name::regrole::oid,
              names.app_name::regrole::oid
          )
          AND deptype = 'o'
    )
    AND has_database_privilege(
        current_user,
        current_database(),
        'CONNECT'
    )
    AND NOT has_database_privilege(
        current_user,
        current_database(),
        'CREATE'
    )
    AND NOT has_database_privilege(
        current_user,
        current_database(),
        'TEMPORARY'
    )
    AND has_schema_privilege(current_user, 'public', 'USAGE')
    AND NOT has_schema_privilege(current_user, 'public', 'CREATE')
    AND NOT EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        LEFT JOIN expected_tables expected
          ON expected.relation_name = relation.relname
        WHERE namespace.nspname = 'public'
          AND relation.relkind IN ('r', 'p')
          AND (
              has_table_privilege(
                  current_user, relation.oid, 'SELECT'
              ) IS DISTINCT FROM coalesce(expected.can_select, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'INSERT'
              ) IS DISTINCT FROM coalesce(expected.can_insert, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'UPDATE'
              ) IS DISTINCT FROM coalesce(expected.can_update, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'DELETE'
              ) IS DISTINCT FROM coalesce(expected.can_delete, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'TRUNCATE'
              ) IS DISTINCT FROM coalesce(expected.can_truncate, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'REFERENCES'
              ) IS DISTINCT FROM coalesce(expected.can_references, false)
              OR has_table_privilege(
                  current_user, relation.oid, 'TRIGGER'
              ) IS DISTINCT FROM coalesce(expected.can_trigger, false)
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM expected_tables expected
        WHERE to_regclass('public.' || expected.relation_name) IS NULL
    )
    AND NOT EXISTS (
        SELECT 1
        FROM pg_class relation
        JOIN pg_namespace namespace
          ON namespace.oid = relation.relnamespace
        LEFT JOIN expected_sequences expected
          ON expected.relation_name = relation.relname
        WHERE namespace.nspname = 'public'
          AND relation.relkind = 'S'
          AND (
              has_sequence_privilege(
                  current_user, relation.oid, 'USAGE'
              ) IS DISTINCT FROM coalesce(expected.can_usage, false)
              OR has_sequence_privilege(
                  current_user, relation.oid, 'SELECT'
              ) IS DISTINCT FROM coalesce(expected.can_select, false)
              OR has_sequence_privilege(
                  current_user, relation.oid, 'UPDATE'
              ) IS DISTINCT FROM coalesce(expected.can_update, false)
          )
    )
    AND NOT EXISTS (
        SELECT 1
        FROM expected_sequences expected
        WHERE to_regclass('public.' || expected.relation_name) IS NULL
    )
    AND EXISTS (
        SELECT 1
        FROM pg_policy policy
        WHERE policy.polrelid =
              'public.paper_canary_safety_leases'::regclass
          AND policy.polname = 'paper_canary_safety_role_select'
          AND policy.polroles = ARRAY[names.group_name::regrole::oid]
          AND policy.polcmd = 'r'
          AND policy.polpermissive
          AND pg_get_expr(
              policy.polqual,
              policy.polrelid
          ) = 'true'
          AND policy.polwithcheck IS NULL
    )
FROM expected_names names
"""


class _SafetyBoundaryCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def fetchone(self) -> tuple[object, ...] | None: ...

    def close(self) -> None: ...


class _SafetyBoundaryConnection(Protocol):
    @property
    def autocommit(self) -> bool: ...

    @autocommit.setter
    def autocommit(self, value: bool) -> None: ...

    def cursor(self) -> _SafetyBoundaryCursor: ...


class _SafetyBoundaryConnectionRecord(Protocol):
    def invalidate(
        self,
        exception: BaseException | None = None,
        soft: bool = False,
    ) -> None: ...


class SafetyDatabaseBoundaryError(RuntimeError):
    """The safety process encountered a drifted database authority boundary."""


class SafetyControlShutdownError(RuntimeError):
    """The safety process could not finish a bounded, reusable shutdown."""

    def __init__(
        self,
        message: str,
        *,
        failures: tuple[BaseException, ...] = (),
    ) -> None:
        super().__init__(message)
        self.failures = failures


class SafetyWatchdogShutdownError(SafetyControlShutdownError):
    """The watchdog remained alive after its bounded cancellation drain."""


def _assert_safety_connection(
    dbapi_connection: object,
    _connection_record: object,
) -> None:
    connection = cast(_SafetyBoundaryConnection, dbapi_connection)
    original_autocommit = connection.autocommit
    cursor: _SafetyBoundaryCursor | None = None
    try:
        # Keep the per-checkout proof transaction-neutral.  The pool checkout
        # event precedes SQLAlchemy's borrower-specific transaction setup, so a
        # normal SELECT here would otherwise open a transaction before either
        # the safety session or an AUTOCOMMIT read facade receives the handle.
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(_SAFETY_CONNECTION_BOUNDARY_SQL)
        row = cursor.fetchone()
        if row is None or len(row) != 1 or row[0] is not True:
            raise SafetyDatabaseBoundaryError(
                "database rejected the STRATUS safety boundary"
            )
    finally:
        try:
            if cursor is not None:
                cursor.close()
        finally:
            connection.autocommit = original_autocommit


def _assert_safety_checkout(
    dbapi_connection: object,
    connection_record: object,
    _connection_proxy: object,
) -> None:
    """Re-prove and invalidate a pooled connection before every borrower."""
    try:
        _assert_safety_connection(dbapi_connection, connection_record)
    except Exception as exc:
        cast(_SafetyBoundaryConnectionRecord, connection_record).invalidate(exc)
        if isinstance(exc, SafetyDatabaseBoundaryError):
            raise
        raise SafetyDatabaseBoundaryError(
            "safety database boundary assertion failed"
        ) from exc


class EmergencyStopPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    venue: Literal["polymarket"]


class EmergencyStopRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    command: Literal["stop"]
    reason: str = Field(min_length=4, max_length=2000)
    actor: str = Field(min_length=1, max_length=64)
    payload: EmergencyStopPayload

    @field_validator("actor", "reason")
    @classmethod
    def require_nonblank_identity_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("audit identity fields must not be blank")
        return value


class EmergencyStopCommandItem(BaseModel):
    id: int
    request_id: str
    command: str
    payload: dict[str, str]
    actor: str
    reason: str
    status: str
    detail: str | None
    created_at: datetime
    executed_at: datetime | None


class EmergencyStopResponse(BaseModel):
    created: bool
    idempotent: bool
    expired_signals: int = Field(
        description="Pending signals claimed and expired by this attempt."
    )
    cancel_requested_live_orders: int = Field(
        description=(
            "Nonterminal LIVE orders claimed for cancellation by this attempt."
        )
    )
    rejected_pending_resume: int
    item: EmergencyStopCommandItem


class PaperCanaryLeaseAcquireRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lease_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    run_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    acquire_request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    resume_request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    direct_stop_request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    release_request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    resume_actor: str = Field(min_length=1, max_length=64)
    resume_reason: str = Field(min_length=4, max_length=2000)
    nonce: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{32,128}$",
    )

    @field_validator("resume_actor", "resume_reason")
    @classmethod
    def require_nonblank_resume_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resume identity fields must not be blank")
        return value


class PaperCanaryLeaseHeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    nonce: str = Field(
        min_length=32,
        max_length=128,
        pattern=r"^[A-Za-z0-9._~-]{32,128}$",
    )


class PaperCanaryLeaseReleaseRequest(PaperCanaryLeaseHeartbeatRequest):
    release_request_id: str = Field(
        min_length=8,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$",
    )
    successful: Literal[True]


class PaperCanaryLeaseItem(BaseModel):
    id: int
    lease_id: str
    run_id: str
    acquire_request_id: str
    resume_request_id: str
    resume_actor: str
    resume_reason: str
    release_request_id: str
    nonce_sha256: str
    state: str
    acquired_at: datetime
    heartbeat_at: datetime
    watchdog_at: datetime
    expires_at: datetime
    released_at: datetime | None
    stop_command_id: int | None


class PaperCanaryLeaseResponse(BaseModel):
    created: bool
    idempotent: bool
    item: PaperCanaryLeaseItem


def _sessions(request: Request) -> SessionFactory:
    sessions = getattr(request.app.state, "sessions", None)
    if sessions is None:
        raise HTTPException(
            status_code=503,
            detail="safety-control database session is not initialized",
        )
    return cast(SessionFactory, sessions)


def _emergency_sessions(request: Request) -> SessionFactory:
    sessions = getattr(request.app.state, "emergency_sessions", None)
    if sessions is None:
        raise HTTPException(
            status_code=503,
            detail="emergency STOP database session is not initialized",
        )
    return cast(SessionFactory, sessions)


def require_safety_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = cast(Settings, request.app.state.settings).safety_api_key
    if expected is None:
        raise HTTPException(status_code=503, detail="safety-control is not configured")
    source = request.client.host if request.client is not None else "unknown"
    limiter = cast(_AuthFailureLimiter, request.app.state.auth_failure_limiter)
    if x_api_key is not None and secrets.compare_digest(x_api_key, expected):
        # A valid key is never locked out by failures from the same address;
        # emergency STOP must remain available during a local brute-force/DoS.
        limiter.clear(source)
        return
    throttled = limiter.record_failure(source)
    if throttled:
        raise HTTPException(
            status_code=429,
            detail="too many invalid API key attempts",
            headers={"Retry-After": str(int(_AUTH_FAILURE_WINDOW_SECONDS))},
        )
    raise HTTPException(status_code=401, detail="invalid or missing API key")


def create_safety_engine(settings: Settings) -> AsyncEngine:
    """Create a single-purpose engine that cannot accept the runtime login."""
    database_url = settings.resolved_safety_database_url()
    parsed_url = make_url(database_url)
    database_name = parsed_url.database
    if database_name is None or parsed_url.username != safety_database_role_name(database_name):
        raise RuntimeError("safety-control requires the database-scoped stratus_safety_app role")
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=1,
        max_overflow=0,
        pool_timeout=settings.stratus_database_pool_timeout_seconds,
        connect_args={
            "server_settings": {
                "application_name": settings.stratus_database_application_name,
                "statement_timeout": (f"{settings.stratus_database_statement_timeout_ms}ms"),
                "lock_timeout": f"{settings.stratus_database_lock_timeout_ms}ms",
                "idle_in_transaction_session_timeout": (
                    f"{settings.stratus_database_idle_transaction_timeout_ms}ms"
                ),
            }
        },
    )
    event.listen(engine.sync_engine, "connect", _assert_safety_connection)
    event.listen(engine.sync_engine, "checkout", _assert_safety_checkout)
    return engine


def _session_factory_enforces_safety_boundary(
    session_factory: SessionFactory,
) -> bool:
    bind = session_factory.kw.get("bind")
    return isinstance(bind, AsyncEngine) and event.contains(
        bind.sync_engine,
        "checkout",
        _assert_safety_checkout,
    )


def _validate_injected_session_factories(
    session_factory: SessionFactory | None,
    emergency_session_factory: SessionFactory | None,
) -> None:
    """Keep test injection from weakening production control-plane topology."""
    if (session_factory is None) != (emergency_session_factory is None):
        raise ValueError(
            "control and emergency session factories must be injected together"
        )
    if session_factory is None or emergency_session_factory is None:
        return
    if session_factory is emergency_session_factory:
        raise ValueError("control and emergency session factories must be distinct")
    control_bind = session_factory.kw.get("bind")
    emergency_bind = emergency_session_factory.kw.get("bind")
    if control_bind is not None and control_bind is emergency_bind:
        raise ValueError("control and emergency session factories must use distinct binds")


def _watchdog_maximum_age(settings: Settings) -> float:
    return min(
        settings.safety_watchdog_poll_seconds * 2 + 1,
        _READINESS_MAX_AGE_SECONDS,
    )


def _watchdog_admission_is_fresh(app: FastAPI, settings: Settings) -> bool:
    if not app.state.watchdog_required:
        return True
    watchdog_task = getattr(app.state, "watchdog_task", None)
    watchdog_status = getattr(app.state, "watchdog_status", None)
    if watchdog_task is None or watchdog_task.done() or watchdog_status is None:
        return False
    snapshot = cast(PaperCanaryWatchdogStatus, watchdog_status).snapshot()
    return (
        snapshot.latest_tick_succeeded
        and snapshot.control_state_ready is True
        and snapshot.success_age_seconds is not None
        and snapshot.success_age_seconds <= _watchdog_maximum_age(settings)
        and snapshot.consecutive_failures < 2
    )


async def _shutdown_watchdog_task(
    task: asyncio.Task[None] | None,
    stop_event: asyncio.Event,
) -> None:
    """Stop the watchdog without allowing shutdown to wait without a bound."""
    stop_event.set()
    if task is None:
        return
    try:
        done, _ = await asyncio.wait(
            {task},
            timeout=_WATCHDOG_SHUTDOWN_TIMEOUT_SECONDS,
        )
    except BaseException:
        task.cancel()
        try:
            await asyncio.wait(
                {task},
                timeout=_WATCHDOG_CANCEL_DRAIN_SECONDS,
            )
        except BaseException:
            pass
        if not task.done():
            logger.critical(
                "safety watchdog remained alive while lifespan shutdown was cancelled"
            )
            task.add_done_callback(_consume_background_task_result)
        raise
    if not done:
        logger.error("safety watchdog did not stop before the shutdown deadline")
        task.cancel()
        try:
            await asyncio.wait(
                {task},
                timeout=_WATCHDOG_CANCEL_DRAIN_SECONDS,
            )
        except asyncio.CancelledError:
            pass
        if not task.done():
            task.add_done_callback(_consume_background_task_result)
            raise SafetyWatchdogShutdownError(
                "safety watchdog remained alive after bounded cancellation"
            )
    if task.done():
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("safety watchdog exited during shutdown: %s", exc)


def _consume_background_task_result(task: asyncio.Task[None]) -> None:
    """Retrieve a late task result after a bounded shutdown has abandoned it."""
    try:
        task.result()
    except BaseException:
        pass


async def _dispose_engine_bounded(engine: AsyncEngine, *, label: str) -> None:
    """Dispose one engine without allowing a driver to hang process shutdown."""
    dispose_task = asyncio.create_task(
        engine.dispose(),
        name=f"dispose-safety-{label}-engine",
    )
    try:
        done, _ = await asyncio.wait(
            {dispose_task},
            timeout=_ENGINE_DISPOSE_TIMEOUT_SECONDS,
        )
    except BaseException:
        dispose_task.cancel()
        try:
            await asyncio.wait(
                {dispose_task},
                timeout=_ENGINE_DISPOSE_CANCEL_DRAIN_SECONDS,
            )
        except BaseException:
            pass
        if not dispose_task.done():
            dispose_task.add_done_callback(_consume_background_task_result)
        raise
    if not done:
        dispose_task.cancel()
        try:
            await asyncio.wait(
                {dispose_task},
                timeout=_ENGINE_DISPOSE_CANCEL_DRAIN_SECONDS,
            )
        except asyncio.CancelledError:
            pass
        if not dispose_task.done():
            dispose_task.add_done_callback(_consume_background_task_result)
        raise SafetyControlShutdownError(
            f"{label} safety database engine disposal exceeded its deadline"
        )
    dispose_task.result()


async def _dispose_engines_bounded(
    engines: tuple[tuple[str, AsyncEngine | None], ...],
) -> None:
    """Attempt every managed disposal and aggregate failures after all attempts."""
    failures: list[BaseException] = []
    cancellation: asyncio.CancelledError | None = None
    for label, engine in engines:
        if engine is None:
            continue
        try:
            await _dispose_engine_bounded(engine, label=label)
        except asyncio.CancelledError as exc:
            logger.error("%s safety database engine disposal was cancelled", label)
            failures.append(exc)
            cancellation = cancellation or exc
        except BaseException as exc:
            logger.error(
                "%s safety database engine disposal failed: %s",
                label,
                exc,
            )
            failures.append(exc)
    if cancellation is not None:
        raise cancellation
    if failures:
        raise SafetyControlShutdownError(
            "one or more safety database engines failed bounded disposal",
            failures=tuple(failures),
        ) from failures[0]


async def verify_safety_engine(engine: AsyncEngine, settings: Settings) -> None:
    """Prove the dedicated role identity and exact least-privilege ACL."""
    expected_database = settings.stratus_database_name
    expected_app = safety_database_role_name(expected_database)
    expected_group = safety_database_group_role_name(expected_database)
    expected_markers = {
        expected_group: (f"STRATUS managed safety group v1 database={expected_database}"),
        expected_app: (f"STRATUS managed safety login v1 database={expected_database}"),
    }
    async with engine.connect() as connection:
        diagnostic = await connection.scalar(text(_SAFETY_CONNECTION_DIAGNOSTIC_SQL))
        if diagnostic is not True:
            raise RuntimeError("safety-control database boundary drifted")
        identity = (
            await connection.execute(
                text(
                    "SELECT current_user, session_user, current_database(), "
                    "current_setting('session_replication_role')"
                )
            )
        ).one()
        if tuple(identity) != (
            expected_app,
            expected_app,
            expected_database,
            "origin",
        ):
            raise RuntimeError("safety-control database identity is invalid")

        role_rows = (
            await connection.execute(
                text(
                    "SELECT rolname, rolcanlogin, rolsuper, rolinherit, "
                    "rolcreatedb, rolcreaterole, rolreplication, rolbypassrls, "
                    "rolconfig, shobj_description(oid, 'pg_authid') "
                    "FROM pg_roles WHERE rolname IN (:app_role, :group_role)"
                ),
                {"app_role": expected_app, "group_role": expected_group},
            )
        ).all()
        roles = {str(row[0]): row for row in role_rows}
        if set(roles) != {expected_app, expected_group}:
            raise RuntimeError("safety-control database roles are missing")
        for role_name, row in roles.items():
            should_login = role_name == expected_app
            if (
                bool(row[1]) is not should_login
                or bool(row[2])
                or not bool(row[3])
                or bool(row[4])
                or bool(row[5])
                or bool(row[6])
                or bool(row[7])
                or row[8] is not None
                or row[9] != expected_markers[role_name]
            ):
                raise RuntimeError("safety-control database role boundary drifted")

        memberships = (
            await connection.execute(
                text(
                    "SELECT parent.rolname, membership.admin_option, "
                    "membership.inherit_option, membership.set_option, "
                    "grantor.rolname "
                    "FROM pg_auth_members membership "
                    "JOIN pg_roles parent ON parent.oid = membership.roleid "
                    "JOIN pg_roles grantor ON grantor.oid = membership.grantor "
                    "WHERE membership.member = current_user::regrole"
                )
            )
        ).all()
        if len(memberships) != 1 or tuple(memberships[0]) != (
            expected_group,
            False,
            True,
            False,
            "stratus",
        ):
            raise RuntimeError("safety-control role membership boundary drifted")

        hardening = (
            await connection.execute(
                text(
                    "SELECT "
                    "NOT EXISTS ("
                    "  SELECT 1 FROM pg_db_role_setting "
                    "  WHERE setrole IN ("
                    "    CAST(:app_role AS regrole), CAST(:group_role AS regrole)"
                    "  )"
                    "), "
                    "NOT has_parameter_privilege("
                    "  :app_role, 'session_replication_role', 'SET'"
                    "), "
                    "NOT has_parameter_privilege("
                    "  :group_role, 'session_replication_role', 'SET'"
                    "), "
                    "NOT EXISTS ("
                    "  SELECT 1 FROM pg_shdepend "
                    "  WHERE refclassid = 'pg_authid'::regclass "
                    "    AND refobjid IN ("
                    "      CAST(:app_role AS regrole)::oid, "
                    "      CAST(:group_role AS regrole)::oid"
                    "    ) AND deptype = 'o'"
                    ")"
                ),
                {"app_role": expected_app, "group_role": expected_group},
            )
        ).one()
        if tuple(hardening) != (True, True, True, True):
            raise RuntimeError("safety-control role hardening boundary drifted")

        database_acl = (
            await connection.execute(
                text(
                    "SELECT "
                    "has_database_privilege(current_user, current_database(), 'CONNECT'), "
                    "has_database_privilege(current_user, current_database(), 'CREATE'), "
                    "has_database_privilege(current_user, current_database(), 'TEMPORARY'), "
                    "has_schema_privilege(current_user, 'public', 'USAGE'), "
                    "has_schema_privilege(current_user, 'public', 'CREATE')"
                )
            )
        ).one()
        if tuple(database_acl) != (True, False, False, True, False):
            raise RuntimeError("safety-control database or schema ACL drifted")

        table_rows = (
            await connection.execute(
                text(
                    "SELECT relation.relname, "
                    + ", ".join(
                        f"has_table_privilege(current_user, relation.oid, '{privilege}')"
                        for privilege in _TABLE_PRIVILEGES
                    )
                    + " FROM pg_class relation "
                    "JOIN pg_namespace namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND relation.relkind IN ('r', 'p')"
                )
            )
        ).all()
        observed_tables = {str(row[0]) for row in table_rows}
        if not set(_SAFETY_TABLE_PRIVILEGES).issubset(observed_tables):
            raise RuntimeError("safety-control required tables are missing")
        for row in table_rows:
            table_name = str(row[0])
            observed = {
                privilege
                for privilege, granted in zip(
                    _TABLE_PRIVILEGES,
                    row[1:],
                    strict=True,
                )
                if granted
            }
            if observed != _SAFETY_TABLE_PRIVILEGES.get(
                table_name,
                frozenset(),
            ):
                raise RuntimeError("safety-control table ACL drifted")

        sequence_rows = (
            await connection.execute(
                text(
                    "SELECT relation.relname, "
                    + ", ".join(
                        f"has_sequence_privilege(current_user, relation.oid, '{privilege}')"
                        for privilege in _SEQUENCE_PRIVILEGES
                    )
                    + " FROM pg_class relation "
                    "JOIN pg_namespace namespace "
                    "ON namespace.oid = relation.relnamespace "
                    "WHERE namespace.nspname = 'public' "
                    "AND relation.relkind = 'S'"
                )
            )
        ).all()
        observed_sequences = {str(row[0]) for row in sequence_rows}
        if not set(_SAFETY_SEQUENCE_PRIVILEGES).issubset(observed_sequences):
            raise RuntimeError("safety-control required sequences are missing")
        for row in sequence_rows:
            sequence_name = str(row[0])
            observed = {
                privilege
                for privilege, granted in zip(
                    _SEQUENCE_PRIVILEGES,
                    row[1:],
                    strict=True,
                )
                if granted
            }
            if observed != _SAFETY_SEQUENCE_PRIVILEGES.get(
                sequence_name,
                frozenset(),
            ):
                raise RuntimeError("safety-control sequence ACL drifted")


def _command_item(command: OperatorCommand) -> EmergencyStopCommandItem:
    return EmergencyStopCommandItem(
        id=command.id,
        request_id=command.request_id,
        command=command.command,
        payload=cast(dict[str, str], command.payload),
        actor=command.actor,
        reason=command.reason,
        status=command.status,
        detail=command.detail,
        created_at=command.created_at,
        executed_at=command.executed_at,
    )


def _lease_item(lease: PaperCanarySafetyLease) -> PaperCanaryLeaseItem:
    return PaperCanaryLeaseItem(
        id=lease.id,
        lease_id=lease.lease_id,
        run_id=lease.run_id,
        acquire_request_id=lease.acquire_request_id,
        resume_request_id=lease.resume_request_id,
        resume_actor=lease.resume_actor,
        resume_reason=lease.resume_reason,
        release_request_id=lease.release_request_id,
        nonce_sha256=lease.nonce_sha256,
        state=lease.state,
        acquired_at=lease.acquired_at,
        heartbeat_at=lease.heartbeat_at,
        watchdog_at=lease.watchdog_at,
        expires_at=lease.expires_at,
        released_at=lease.released_at,
        stop_command_id=lease.stop_command_id,
    )


def create_app(
    settings: Settings | None = None,
    *,
    session_factory: SessionFactory | None = None,
    emergency_session_factory: SessionFactory | None = None,
    _watchdog_required: bool = True,
) -> FastAPI:
    settings = settings or get_settings()
    _validate_injected_session_factories(
        session_factory,
        emergency_session_factory,
    )
    safety_api_key = _validated_control_plane_key(
        settings.safety_api_key,
        name="SAFETY_API_KEY",
    )
    if settings.operator_api_key is not None and secrets.compare_digest(
        safety_api_key,
        settings.operator_api_key,
    ):
        raise ValueError("SAFETY_API_KEY must be distinct from OPERATOR_API_KEY")

    watchdog_required = _watchdog_required
    boundary_assertion_required = session_factory is None or (
        session_factory is not None
        and _session_factory_enforces_safety_boundary(session_factory)
    )
    lifespan_reuse_blocked = False

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal lifespan_reuse_blocked
        if lifespan_reuse_blocked:
            raise SafetyControlShutdownError(
                "safety-control lifespan reuse is blocked after failed teardown"
            )
        sessions = session_factory
        emergency_sessions = emergency_session_factory
        managed_control_engine: AsyncEngine | None = None
        managed_emergency_engine: AsyncEngine | None = None
        watchdog_stop = asyncio.Event()
        watchdog_task: asyncio.Task[None] | None = None
        watchdog_status = PaperCanaryWatchdogStatus()
        app.state.watchdog_status = watchdog_status
        try:
            if sessions is None:
                managed_control_engine = create_safety_engine(settings)
                await verify_safety_engine(managed_control_engine, settings)
                sessions = create_session_factory(managed_control_engine)
            if emergency_sessions is None:
                managed_emergency_engine = create_safety_engine(settings)
                await verify_safety_engine(managed_emergency_engine, settings)
                emergency_sessions = create_session_factory(managed_emergency_engine)
            app.state.sessions = sessions
            app.state.emergency_sessions = emergency_sessions
            app.state.watchdog_status = watchdog_status
            if watchdog_required:
                watchdog_task = asyncio.create_task(
                    run_paper_canary_watchdog(
                        settings,
                        sessions,
                        watchdog_stop,
                        watchdog_status,
                        database_boundary=(
                            "exact"
                            if boundary_assertion_required
                            else "test_session_unverified"
                        ),
                    ),
                    name="paper-canary-safety-watchdog",
                )
            app.state.watchdog_task = watchdog_task
            yield
        finally:
            cleanup_failures: list[BaseException] = []
            try:
                await _shutdown_watchdog_task(watchdog_task, watchdog_stop)
            except BaseException as exc:
                logger.critical("safety watchdog teardown failed: %s", exc)
                cleanup_failures.append(exc)
            finally:
                app.state.watchdog_task = None
                app.state.watchdog_status = None
                app.state.sessions = None
                app.state.emergency_sessions = None
                try:
                    await _dispose_engines_bounded(
                        (
                            ("emergency", managed_emergency_engine),
                            ("control", managed_control_engine),
                        )
                    )
                except BaseException as exc:
                    cleanup_failures.append(exc)
            if cleanup_failures:
                lifespan_reuse_blocked = True
                cancellation = next(
                    (
                        failure
                        for failure in cleanup_failures
                        if isinstance(failure, asyncio.CancelledError)
                    ),
                    None,
                )
                if cancellation is not None:
                    raise cancellation
                raise SafetyControlShutdownError(
                    "safety-control teardown did not complete cleanly",
                    failures=tuple(cleanup_failures),
                ) from cleanup_failures[0]

    app = FastAPI(
        title="STRATUS safety control",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.auth_failure_limiter = _AuthFailureLimiter()
    app.state.settings = settings
    app.state.sessions = session_factory
    app.state.emergency_sessions = emergency_session_factory
    app.state.watchdog_required = watchdog_required
    app.state.boundary_assertion_required = boundary_assertion_required
    app.state.watchdog_task = None
    # This pre-lifespan instance exists only for explicit in-process tests.
    # Every real lifespan replaces it with a private status object and clears it
    # on exit, so an abandoned prior task cannot publish into a new lifecycle.
    app.state.watchdog_status = PaperCanaryWatchdogStatus()

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def database_timeout(
        _request: Request,
        _error: SQLAlchemyTimeoutError,
    ) -> JSONResponse:
        logger.error("safety-control database timeout")
        return JSONResponse(
            status_code=503,
            content={"detail": "safety-control database timeout", "retryable": True},
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(DBAPIError)
    async def database_operation_failure(
        _request: Request,
        _error: DBAPIError,
    ) -> JSONResponse:
        logger.exception("safety-control database operation failed")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "safety-control database operation failed",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(SafetyDatabaseBoundaryError)
    async def database_boundary_failure(
        _request: Request,
        _error: SafetyDatabaseBoundaryError,
    ) -> JSONResponse:
        logger.error("safety-control database boundary rejected a checkout")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "safety-control database boundary rejected",
                "retryable": False,
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Report the last watchdog DB tick without borrowing the control pool."""
        reasons: list[str] = []
        watchdog_task = getattr(app.state, "watchdog_task", None)
        if app.state.watchdog_required and (watchdog_task is None or watchdog_task.done()):
            reasons.append("paper_canary_watchdog_not_running")
        cached_status = getattr(app.state, "watchdog_status", None)
        if cached_status is None:
            boundary_status = "failed"
            reasons.append("database_unavailable")
        else:
            snapshot = cast(PaperCanaryWatchdogStatus, cached_status).snapshot()
            boundary_status = snapshot.database_boundary
            if not snapshot.latest_tick_succeeded:
                reasons.append("database_unavailable")
            if snapshot.control_state_ready is False:
                reasons.append("database_control_state_unsafe")
            maximum_age = _watchdog_maximum_age(settings)
            if (
                snapshot.success_age_seconds is None
                or snapshot.success_age_seconds > maximum_age
            ):
                reasons.append("paper_canary_watchdog_stale")
            if snapshot.consecutive_failures >= 2:
                reasons.append("paper_canary_watchdog_repeated_failures")
        return JSONResponse(
            status_code=200 if not reasons else 503,
            content={
                "status": "ready" if not reasons else "not_ready",
                "reasons": reasons,
                "database_boundary": boundary_status,
            },
        )

    @app.post(
        "/api/v1/emergency-stop",
        response_model=EmergencyStopResponse,
        dependencies=[Depends(require_safety_api_key)],
    )
    async def emergency_stop(
        request: Request,
        body: EmergencyStopRequest,
    ) -> EmergencyStopResponse:
        try:
            result = await stop_polymarket_atomically(
                _emergency_sessions(request),
                request_id=body.request_id,
                actor=body.actor,
                reason=body.reason,
            )
            item = _command_item(result.command)
        except (
            EmergencyStopIdentityCollisionError,
            EmergencyStopCommandStateError,
        ) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return EmergencyStopResponse(
            created=result.created,
            idempotent=result.idempotent,
            expired_signals=result.expired_signals,
            cancel_requested_live_orders=result.cancel_requested_live_orders,
            rejected_pending_resume=result.rejected_pending_resume,
            item=item,
        )

    @app.post(
        "/api/v1/canary-leases/acquire",
        response_model=PaperCanaryLeaseResponse,
        dependencies=[Depends(require_safety_api_key)],
    )
    async def acquire_canary_lease(
        request: Request,
        body: PaperCanaryLeaseAcquireRequest,
    ) -> PaperCanaryLeaseResponse:
        if not _watchdog_admission_is_fresh(app, settings):
            raise HTTPException(
                status_code=503,
                detail="PAPER canary watchdog is not fresh",
            )
        try:
            result = await acquire_paper_canary_lease_atomically(
                _sessions(request),
                lease_id=body.lease_id,
                run_id=body.run_id,
                acquire_request_id=body.acquire_request_id,
                resume_request_id=body.resume_request_id,
                direct_stop_request_id=body.direct_stop_request_id,
                release_request_id=body.release_request_id,
                resume_actor=body.resume_actor,
                resume_reason=body.resume_reason,
                nonce=body.nonce,
                ttl_seconds=settings.safety_canary_lease_ttl_seconds,
            )
        except PaperCanaryLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not _watchdog_admission_is_fresh(app, settings):
            try:
                await stop_polymarket_atomically(
                    _emergency_sessions(request),
                    request_id=body.direct_stop_request_id,
                    actor=_POST_ACQUIRE_STOP_ACTOR,
                    reason=_POST_ACQUIRE_STOP_REASON,
                )
                logger.critical(
                    "PAPER canary acquisition lost watchdog readiness; "
                    "reserved emergency STOP committed: lease_id=%s",
                    body.lease_id,
                )
            except Exception:
                # Acquisition itself cannot resume execution: PostgreSQL still
                # requires a later watchdog observation. Any STOP ambiguity is
                # therefore reported unavailable while DB fences stay authoritative.
                logger.exception(
                    "PAPER canary post-acquire reserved STOP was ambiguous: "
                    "lease_id=%s",
                    body.lease_id,
                )
            raise HTTPException(
                status_code=503,
                detail=(
                    "PAPER canary watchdog lost freshness after acquisition; "
                    "fail-closed STOP requested"
                ),
            )
        return PaperCanaryLeaseResponse(
            created=result.created,
            idempotent=result.idempotent,
            item=_lease_item(result.lease),
        )

    @app.post(
        "/api/v1/canary-leases/{lease_id}/heartbeat",
        response_model=PaperCanaryLeaseResponse,
        dependencies=[Depends(require_safety_api_key)],
    )
    async def heartbeat_canary_lease(
        request: Request,
        lease_id: str,
        body: PaperCanaryLeaseHeartbeatRequest,
    ) -> PaperCanaryLeaseResponse:
        try:
            result = await heartbeat_paper_canary_lease_atomically(
                _sessions(request),
                lease_id=lease_id,
                run_id=body.run_id,
                nonce=body.nonce,
                ttl_seconds=settings.safety_canary_lease_ttl_seconds,
                watchdog_fresh_seconds=max(
                    _watchdog_maximum_age(settings),
                    3,
                ),
            )
        except PaperCanaryLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return PaperCanaryLeaseResponse(
            created=False,
            idempotent=True,
            item=_lease_item(result.lease),
        )

    @app.post(
        "/api/v1/canary-leases/{lease_id}/release",
        response_model=PaperCanaryLeaseResponse,
        dependencies=[Depends(require_safety_api_key)],
    )
    async def release_canary_lease(
        request: Request,
        lease_id: str,
        body: PaperCanaryLeaseReleaseRequest,
    ) -> PaperCanaryLeaseResponse:
        try:
            result = await release_paper_canary_lease_atomically(
                _sessions(request),
                lease_id=lease_id,
                run_id=body.run_id,
                nonce=body.nonce,
                release_request_id=body.release_request_id,
            )
        except PaperCanaryLeaseError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return PaperCanaryLeaseResponse(
            created=result.created,
            idempotent=result.idempotent,
            item=_lease_item(result.lease),
        )

    return app


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.safety_api_host,
        port=settings.safety_api_port,
    )


if __name__ == "__main__":
    main()
