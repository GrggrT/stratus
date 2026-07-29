from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy_promotions import (
    PromotionProtocolRun,
    run_accuracy_promotion_protocol,
)
from stratus.services.pipeline_identity import probability_pipeline_fingerprint

logger = logging.getLogger(__name__)


async def evaluate_promotion_trials(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> PromotionProtocolRun:
    """Register/evaluate the immutable accuracy-promotion family once.

    The repository owns the PostgreSQL advisory lock and the entire atomic
    registration/evidence/look/activation transaction.  The job deliberately
    supplies one wall-clock value so every decision made in a retry has a
    coherent audit timestamp.
    """

    settings = settings or get_settings()
    evaluated_at = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            result = await run_accuracy_promotion_protocol(
                session,
                evaluated_at=evaluated_at,
                pipeline_fingerprint=probability_pipeline_fingerprint(settings),
            )
    logger.info(
        "accuracy promotion protocol: family=%s trials=%d registered=%d "
        "members=%d looks=%d activations=%d",
        result.family_id,
        result.trials,
        result.registered_trials,
        result.recorded_members,
        result.recorded_looks,
        result.recorded_activations,
    )
    return result


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(evaluate_promotion_trials(settings))


if __name__ == "__main__":
    main()
