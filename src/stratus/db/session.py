from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Protocol, cast

from sqlalchemy import CursorResult, Result, event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.sql import visitors
from sqlalchemy.sql.dml import UpdateBase
from sqlalchemy.sql.elements import TextClause

from stratus.config import Settings, runtime_database_role_name


class RuntimeDatabaseBoundaryError(RuntimeError):
    """The process attempted to use a non-runtime or drifted database role."""


class RuntimeReadCapacityError(RuntimeError):
    """The bounded runtime read lane could not admit another database session."""


class RuntimeReadOnlyError(RuntimeError):
    """A statement attempted to mutate data through the API read lane."""


class _ReadOnlySyncSession(Session):
    """Synchronous implementation behind guarded AsyncSession reads."""


_LEADING_SQL_KEYWORD = re.compile(
    r"^\s*(?:(?:--[^\r\n]*(?:\r?\n|$))|(?:/\*.*?\*/))*\s*([a-z]+)",
    re.IGNORECASE | re.DOTALL,
)
_WITH_DML_KEYWORD = re.compile(
    r"\b(?:insert|update|delete|merge)\b",
    re.IGNORECASE,
)


def _text_statement_is_read_only(statement: TextClause) -> bool:
    match = _LEADING_SQL_KEYWORD.match(statement.text)
    if match is None:
        return False
    keyword = match.group(1).upper()
    if keyword == "SELECT":
        return True
    return keyword == "WITH" and _WITH_DML_KEYWORD.search(statement.text) is None


@event.listens_for(_ReadOnlySyncSession, "do_orm_execute")
def _reject_read_lane_mutation(execute_state: ORMExecuteState) -> None:
    statement = execute_state.statement
    if (
        execute_state.is_insert
        or execute_state.is_update
        or execute_state.is_delete
        or bool(getattr(statement, "is_dml", False))
        or any(
            isinstance(element, UpdateBase)
            for element in visitors.iterate(cast(Any, statement))
        )
        or (
            isinstance(statement, TextClause)
            and not _text_statement_is_read_only(statement)
        )
    ):
        raise RuntimeReadOnlyError("runtime read sessions reject mutating statements")


@event.listens_for(_ReadOnlySyncSession, "before_flush")
def _reject_read_lane_flush(
    _session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    raise RuntimeReadOnlyError("runtime read sessions reject ORM flushes")


class _BoundaryCursor(Protocol):
    def execute(self, statement: str) -> object: ...

    def fetchone(self) -> Sequence[object] | None: ...

    def close(self) -> None: ...


class _BoundaryConnection(Protocol):
    def cursor(self) -> _BoundaryCursor: ...


def _assert_runtime_connection(
    dbapi_connection: object,
    _connection_record: object,
) -> None:
    cursor = cast(_BoundaryConnection, dbapi_connection).cursor()
    try:
        cursor.execute("SELECT public.assert_stratus_runtime_boundary(true)")
        row = cursor.fetchone()
        if row is None or len(row) != 1 or row[0] is not True:
            raise RuntimeDatabaseBoundaryError(
                "database rejected the STRATUS runtime boundary"
            )
    finally:
        cursor.close()


def _runtime_server_settings(
    settings: Settings,
    *,
    read_only: bool = False,
) -> dict[str, str]:
    """Return fail-closed limits installed on every runtime DB connection.

    These are asyncpg ``server_settings``, not database- or role-level ALTERs.
    Consequently Alembic owner connections, pg_dump, and restore tooling retain
    the unrestricted budgets required for maintenance work.
    """
    server_settings = {
        "application_name": settings.stratus_database_application_name,
        "statement_timeout": f"{settings.stratus_database_statement_timeout_ms}ms",
        "lock_timeout": f"{settings.stratus_database_lock_timeout_ms}ms",
        "idle_in_transaction_session_timeout": (
            f"{settings.stratus_database_idle_transaction_timeout_ms}ms"
        ),
    }
    if read_only:
        server_settings["default_transaction_read_only"] = "on"
    return server_settings


def create_engine(
    settings: Settings,
    *,
    pool_size: int | None = None,
    max_overflow: int | None = None,
    read_only: bool = False,
) -> AsyncEngine:
    database_url = settings.resolved_database_url()
    parsed_url = make_url(database_url)
    database_name = parsed_url.database
    if database_name is None or parsed_url.username != runtime_database_role_name(
        database_name
    ):
        raise RuntimeDatabaseBoundaryError(
            "runtime entrypoints require the database-scoped stratus_app role"
        )
    resolved_pool_size = (
        settings.stratus_database_pool_size if pool_size is None else pool_size
    )
    resolved_max_overflow = (
        settings.stratus_database_max_overflow
        if max_overflow is None
        else max_overflow
    )
    if resolved_pool_size < 1 or resolved_max_overflow < 0:
        raise ValueError("runtime engine pool bounds must be non-negative and nonzero")
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=resolved_pool_size,
        max_overflow=resolved_max_overflow,
        pool_timeout=settings.stratus_database_pool_timeout_seconds,
        connect_args={
            "server_settings": _runtime_server_settings(
                settings,
                read_only=read_only,
            )
        },
    )
    event.listen(engine.sync_engine, "connect", _assert_runtime_connection)
    return engine


def dml_rowcount(result: Result[Any]) -> int:
    """Affected-row count of an INSERT/UPDATE/DELETE execution.

    SQLAlchemy types execute() as returning Result, but DML statements always
    produce a CursorResult which carries rowcount.
    """
    return cast(CursorResult[Any], result).rowcount or 0


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


class BoundedReadSessionFactory:
    """Session factory that reserves runtime-pool capacity for control writes."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_concurrent: int,
        queue_timeout_seconds: float,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("read-session concurrency must be positive")
        if queue_timeout_seconds <= 0:
            raise ValueError("read-session queue timeout must be positive")
        self._session_factory = session_factory
        self._gate = asyncio.Semaphore(max_concurrent)
        self._queue_timeout_seconds = queue_timeout_seconds

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[AsyncSession]:
        try:
            async with asyncio.timeout(self._queue_timeout_seconds):
                await self._gate.acquire()
        except TimeoutError as exc:
            raise RuntimeReadCapacityError(
                "runtime read-session capacity is temporarily saturated"
            ) from exc
        try:
            async with self._session_factory() as session:
                yield session
        finally:
            self._gate.release()


def create_read_session_factory(
    engine: AsyncEngine,
    *,
    max_concurrent: int = 3,
    queue_timeout_seconds: float = 2.5,
) -> BoundedReadSessionFactory:
    """Return API read sessions that never leave PostgreSQL transactions open.

    SQLAlchemy starts a transaction for the first SELECT by default. Multi-query
    monitoring responses then appear as ``idle in transaction`` while Python
    formats intermediate results, holding both a database snapshot and one of
    the deliberately small runtime-pool slots. AUTOCOMMIT keeps each SELECT
    statement-scoped while preserving the same READ COMMITTED visibility
    semantics between statements.

    The returned engine facade shares the original pool. Callers must dispose
    only the original engine.
    """
    read_engine = engine.execution_options(isolation_level="AUTOCOMMIT")
    return BoundedReadSessionFactory(
        async_sessionmaker(
            read_engine,
            expire_on_commit=False,
            sync_session_class=_ReadOnlySyncSession,
        ),
        max_concurrent=max_concurrent,
        queue_timeout_seconds=queue_timeout_seconds,
    )


async def verify_runtime_engine(engine: AsyncEngine) -> None:
    """Force the per-connection boundary before a runtime starts serving work."""
    async with engine.connect():
        pass


@asynccontextmanager
async def session_factory_scope(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield the given factory, or a temporary one backed by a fresh engine.

    Jobs accept an optional factory so the scheduler can share one engine and
    connection pool across all jobs, while CLI entry points keep the simple
    engine-per-invocation behaviour.
    """
    if session_factory is not None:
        yield session_factory
        return
    engine = create_engine(settings)
    try:
        await verify_runtime_engine(engine)
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
