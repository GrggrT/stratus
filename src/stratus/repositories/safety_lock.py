from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# Stable, process-independent lock namespace for all Polymarket PAPER canary
# authority transitions. A transaction-scoped PostgreSQL advisory lock also
# serializes the important "no active row exists" case that row locks cannot.
PAPER_CANARY_SAFETY_LOCK_KEY = 6004519027736253267


class PaperCanarySafetyLockBusy(RuntimeError):
    """The safety domain is busy; leave the queued command pending."""


async def lock_paper_canary_safety_domain(session: AsyncSession) -> None:
    await session.execute(
        select(func.pg_advisory_xact_lock(PAPER_CANARY_SAFETY_LOCK_KEY))
    )


async def try_lock_paper_canary_safety_domain(session: AsyncSession) -> bool:
    acquired = await session.scalar(
        select(func.pg_try_advisory_xact_lock(PAPER_CANARY_SAFETY_LOCK_KEY))
    )
    return bool(acquired)
