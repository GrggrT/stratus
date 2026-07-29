from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.execution_cost_ledger import (
    MarkoutSealRun,
    ObligationFinalizationRun,
    execution_cost_clock_timestamp,
    finalize_execution_cost_obligations,
    seal_due_execution_markouts,
)

logger = logging.getLogger(__name__)

EXECUTION_COST_MAINTENANCE_INTERVAL_SECONDS = 15


@dataclass(frozen=True, slots=True)
class ExecutionCostMaintenanceRun:
    observed_at: datetime
    markouts: MarkoutSealRun
    finalization: ObligationFinalizationRun


async def maintain_execution_cost_evidence(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ExecutionCostMaintenanceRun:
    """Collect due marks, commit them, then finalize terminal obligations.

    Both phases share one database-witness clock but intentionally use separate
    transactions.  A failure in finalization therefore cannot roll back valid
    mark evidence collected by the first phase.
    """

    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            observed_at = await execution_cost_clock_timestamp(session)
            markouts = await seal_due_execution_markouts(
                session,
                now=observed_at,
            )
        async with sessions.begin() as session:
            finalization = await finalize_execution_cost_obligations(
                session,
                now=observed_at,
            )
    logger.info(
        (
            "execution cost maintenance: at=%s mark_claimed=%d observed=%d "
            "missing=%d deferred=%d finalize_claimed=%d sealed=%d deferred=%d"
        ),
        observed_at.isoformat(),
        markouts.obligations_claimed,
        markouts.marks_observed,
        markouts.marks_missing,
        markouts.obligations_deferred,
        finalization.obligations_claimed,
        finalization.obligations_sealed,
        finalization.obligations_deferred,
    )
    return ExecutionCostMaintenanceRun(
        observed_at=observed_at,
        markouts=markouts,
        finalization=finalization,
    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(maintain_execution_cost_evidence(settings))


if __name__ == "__main__":
    main()
