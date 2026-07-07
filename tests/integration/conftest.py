"""Fixtures for integration tests against a real Postgres.

Activated only when STRATUS_TEST_DATABASE_URL is set, e.g.:

    docker compose up -d postgres
    docker compose exec -T postgres createdb -U stratus stratus_test
    STRATUS_TEST_DATABASE_URL=postgresql+asyncpg://stratus:stratus@localhost:5432/stratus_test \
        uv run pytest tests/integration

The schema is created by running the real Alembic migration chain, so these
tests also validate that the migrations apply cleanly and that every
ON CONFLICT constraint name the repositories target actually exists.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from stratus.config import get_settings

# Cleaned before every test. runtime_control is intentionally NOT truncated:
# migration 0009 seeds it and the fail-closed logic depends on that row.
_TRUNCATE_TABLES = (
    "orders",
    "positions",
    "signals",
    "model_probs",
    "market_prices",
    "markets",
    "forecasts",
    "historical_forecasts",
    "observations",
    "daily_actuals",
    "calibration",
    "stations",
    "bankroll_log",
    "telegram_deliveries",
    "job_runs",
    "validation_checkpoints",
    "health_audits",
)


@pytest.fixture(scope="session")
def migrated_database_url() -> Iterator[str]:
    url = os.environ["STRATUS_TEST_DATABASE_URL"]
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        command.upgrade(Config("alembic.ini"), "head")
        yield url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
async def db_sessions(
    migrated_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory.begin() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(_TRUNCATE_TABLES)
                + " RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield factory
    finally:
        await engine.dispose()
