from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.signals import prune_signals as prune_signal_rows

logger = logging.getLogger(__name__)


async def prune_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    older_than = datetime.now(UTC) - timedelta(days=settings.signal_retention_days)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            deleted = await prune_signal_rows(session, older_than=older_than)
    logger.info(
        "signal retention complete: deleted=%d older_than=%s",
        deleted,
        older_than.isoformat(),
    )
    return deleted


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(prune_signals(settings))


if __name__ == "__main__":
    main()
