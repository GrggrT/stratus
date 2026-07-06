from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy import CursorResult, Result
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from stratus.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def dml_rowcount(result: Result[Any]) -> int:
    """Affected-row count of an INSERT/UPDATE/DELETE execution.

    SQLAlchemy types execute() as returning Result, but DML statements always
    produce a CursorResult which carries rowcount.
    """
    return cast(CursorResult[Any], result).rowcount or 0


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


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
        yield create_session_factory(engine)
    finally:
        await engine.dispose()
