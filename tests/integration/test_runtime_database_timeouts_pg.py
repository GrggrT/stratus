from __future__ import annotations

import asyncio
import os
from time import perf_counter

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, runtime_database_role_name
from stratus.db.session import create_engine

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def test_api_runtime_engine_enforces_postgres_time_and_pool_budgets(
    db_sessions: SessionFactory,
) -> None:
    owner_url = make_url(os.environ["STRATUS_TEST_DATABASE_URL"])
    password = "runtime_timeout_test_password_0000000046"
    async with db_sessions.begin() as owner:
        database_name = await owner.scalar(text("SELECT current_database()"))
        assert isinstance(database_name, str)
        app_role = runtime_database_role_name(database_name)
        await owner.execute(
            text(f'ALTER ROLE "{app_role}" LOGIN PASSWORD \'{password}\'')
        )

    runtime_url = owner_url.set(username=app_role, password=password).render_as_string(
        hide_password=False
    )
    api_settings = Settings(
        _env_file=None,
        database_url=runtime_url,
        stratus_database_application_name="stratus-api",
        stratus_database_statement_timeout_ms=2_500,
        stratus_database_lock_timeout_ms=1_000,
        # Keep the integration proof fast; Compose uses the same setting with
        # a five-second production value.
        stratus_database_idle_transaction_timeout_ms=500,
        stratus_database_pool_size=4,
        stratus_database_max_overflow=0,
        stratus_database_pool_timeout_seconds=0.25,
    )
    api_engine = create_engine(api_settings)
    worker_engine = create_engine(
        Settings(
            _env_file=None,
            database_url=runtime_url,
            stratus_database_application_name="stratus-worker-test",
        )
    )
    try:
        async with api_engine.connect() as connection:
            profile = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            current_setting('application_name'),
                            current_setting('statement_timeout'),
                            current_setting('lock_timeout'),
                            current_setting('idle_in_transaction_session_timeout')
                        """
                    )
                )
            ).one()
            assert profile == ("stratus-api", "2500ms", "1s", "500ms")

            started = perf_counter()
            with pytest.raises(
                DBAPIError,
                match="canceling statement due to statement timeout",
            ):
                await connection.execute(text("SELECT pg_sleep(3)"))
            assert perf_counter() - started < 3.0
            await connection.rollback()

        abandoned = await api_engine.connect()
        try:
            await abandoned.execute(text("SELECT 1"))
            await asyncio.sleep(1.0)
            with pytest.raises(DBAPIError):
                await abandoned.execute(text("SELECT 1"))
        finally:
            await abandoned.close()

        checked_out = [await api_engine.connect() for _ in range(4)]
        try:
            started = perf_counter()
            with pytest.raises(SQLAlchemyTimeoutError):
                await api_engine.connect()
            assert perf_counter() - started < 1.0
        finally:
            await asyncio.gather(*(connection.close() for connection in checked_out))

        async with worker_engine.connect() as connection:
            worker_profile = (
                await connection.execute(
                    text(
                        """
                        SELECT
                            current_setting('application_name'),
                            current_setting('statement_timeout'),
                            current_setting('lock_timeout'),
                            current_setting('idle_in_transaction_session_timeout')
                        """
                    )
                )
            ).one()
            assert worker_profile == ("stratus-worker-test", "0", "0", "0")
    finally:
        await api_engine.dispose()
        await worker_engine.dispose()
        async with db_sessions.begin() as owner:
            await owner.execute(
                text(f'ALTER ROLE "{app_role}" NOLOGIN PASSWORD NULL')
            )
