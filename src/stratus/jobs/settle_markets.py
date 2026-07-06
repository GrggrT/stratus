from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.gamma import GammaClient, parse_market_resolutions
from stratus.config import Settings, get_settings
from stratus.db.models import Market, MarketStatus
from stratus.db.session import session_factory_scope
from stratus.repositories.settlement import settle_paper_markets

logger = logging.getLogger(__name__)


async def settle_markets(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            candidates = (
                await session.execute(
                    select(Market.id, Market.polymarket_id, Market.event_id).where(
                        Market.status != MarketStatus.RESOLVED,
                        Market.resolves_at < now,
                    )
                )
            ).all()
        event_ids = list(dict.fromkeys(row.event_id for row in candidates))
        if not event_ids:
            logger.info("market settlement complete: candidates=0 resolved=0 positions=0")
            return 0, 0

        async with GammaClient(
            events_url=settings.gamma_events_url,
            weather_tag_id=settings.gamma_weather_tag_id,
        ) as client:
            events = await client.fetch_events_by_id(event_ids)
        details_by_external_id = {}
        for event in events:
            details_by_external_id.update(parse_market_resolutions(event))
        resolutions = {
            row.id: details_by_external_id[row.polymarket_id].outcome_yes
            for row in candidates
            if row.polymarket_id in details_by_external_id
        }
        closing_times = {
            row.id: details_by_external_id[row.polymarket_id].closed_at
            for row in candidates
            if row.polymarket_id in details_by_external_id
        }

        async with session_factory.begin() as session:
            resolved, positions, pnl = await settle_paper_markets(
                session,
                resolutions=resolutions,
                closing_times=closing_times,
                settled_at=now,
                initial_bankroll_usd=Decimal(str(settings.initial_bankroll_usd)),
            )
        logger.info(
            "market settlement complete: candidates=%d resolved=%d positions=%d pnl=%s",
            len(candidates),
            resolved,
            positions,
            pnl,
        )
        return resolved, positions


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(settle_markets(settings))


if __name__ == "__main__":
    main()
