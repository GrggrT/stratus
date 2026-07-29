from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.execution_cost_ledger import (
    MarkoutSealRun,
    seal_due_execution_markouts,
)

logger = logging.getLogger(__name__)


async def seal_execution_markouts(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> MarkoutSealRun:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            result = await seal_due_execution_markouts(session)
    logger.info(
        "execution markouts: obligations=%d observed=%d missing=%d deferred=%d",
        result.obligations_claimed,
        result.marks_observed,
        result.marks_missing,
        result.obligations_deferred,
    )
    return result


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(seal_execution_markouts(settings))


if __name__ == "__main__":
    main()
