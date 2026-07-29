from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.execution_cost_ledger import (
    ObligationFinalizationRun,
)
from stratus.repositories.execution_cost_ledger import (
    finalize_execution_cost_obligations as finalize_due_obligations,
)

logger = logging.getLogger(__name__)


async def finalize_execution_cost_obligations(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ObligationFinalizationRun:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            result = await finalize_due_obligations(session)
    logger.info(
        "execution obligations: claimed=%d sealed=%d deferred=%d",
        result.obligations_claimed,
        result.obligations_sealed,
        result.obligations_deferred,
    )
    return result


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(finalize_execution_cost_obligations(settings))


if __name__ == "__main__":
    main()
