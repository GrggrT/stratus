from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.telegram import enqueue_notification
from stratus.services.telegram_reports import edge_report_text

logger = logging.getLogger(__name__)


async def weekly_edge_report(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Enqueue the weekly anti-luck digest: gate checklist with numbers.
    Idempotent per ISO week via the notification event key."""
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            text = await edge_report_text(session, settings.initial_bankroll_usd)
            await enqueue_notification(session, f"edge-report:{now:%G-W%V}", text)
    logger.info("weekly edge report enqueued")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(weekly_edge_report(settings))


if __name__ == "__main__":
    main()
