from __future__ import annotations

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

from stratus.config import Settings, runtime_database_role_name


class RuntimeDatabaseBoundaryError(RuntimeError):
    """The process attempted to use a non-runtime or drifted database role."""


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


def create_engine(settings: Settings) -> AsyncEngine:
    database_url = settings.resolved_database_url()
    parsed_url = make_url(database_url)
    database_name = parsed_url.database
    if database_name is None or parsed_url.username != runtime_database_role_name(
        database_name
    ):
        raise RuntimeDatabaseBoundaryError(
            "runtime entrypoints require the database-scoped stratus_app role"
        )
    engine = create_async_engine(database_url, pool_pre_ping=True)
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
