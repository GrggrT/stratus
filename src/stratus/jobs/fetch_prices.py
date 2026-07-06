from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient, ClobError, parse_price_snapshot
from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.market_prices import (
    mark_prices_fetched,
    markets_due_for_price_fetch,
    upsert_market_prices,
)

logger = logging.getLogger(__name__)


async def fetch_prices(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            targets = await markets_due_for_price_fetch(session, datetime.now(UTC))
        if not targets:
            logger.info("price collection complete: due=0 written=0 skipped=0")
            return 0, 0

        market_by_token = {target.token_id: target.market_id for target in targets}
        collected_at = datetime.now(UTC)
        async with ClobClient(
            books_url=settings.clob_books_url,
            batch_size=settings.clob_batch_size,
        ) as client:
            books = await client.fetch_books(list(market_by_token))

        snapshots = []
        skipped = 0
        seen_tokens: set[str] = set()
        for book in books:
            try:
                snapshot = parse_price_snapshot(book, collected_at=collected_at)
            except ClobError as exc:
                logger.warning("invalid CLOB book skipped: %s", exc)
                skipped += 1
                continue
            token_id = str(book.get("asset_id") or "")
            seen_tokens.add(token_id)
            market_id = market_by_token.get(token_id)
            if market_id is None or snapshot is None:
                skipped += 1
                continue
            snapshots.append((market_id, snapshot))
        skipped += len(set(market_by_token) - seen_tokens)

        async with session_factory.begin() as session:
            written = await upsert_market_prices(session, snapshots)
            await mark_prices_fetched(
                session, [target.market_id for target in targets], collected_at
            )
        logger.info(
            "price collection complete: due=%d books=%d written=%d skipped=%d",
            len(targets),
            len(books),
            written,
            skipped,
        )
        return written, skipped


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_prices(settings))


if __name__ == "__main__":
    main()
