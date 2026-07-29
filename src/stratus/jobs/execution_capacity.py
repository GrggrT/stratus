from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from stratus.config import Settings
from stratus.repositories.runtime_control import get_runtime_control, set_kill_switch
from stratus.repositories.storage_capacity import (
    acquire_capacity_write_lock,
    authoritative_capacity_check,
)
from stratus.services.storage_capacity import CapacityCollectionDecision

logger = logging.getLogger(__name__)


async def engage_capacity_failure(
    session: AsyncSession,
    *,
    reason: str,
) -> None:
    """Persist a fail-closed state after a capacity probe transaction failed."""

    await acquire_capacity_write_lock(session)
    control = await get_runtime_control(session)
    if not control.kill_switch:
        await set_kill_switch(
            session,
            True,
            expiration_reason=f"storage_capacity:{reason}",
        )


async def acquire_execution_capacity_gate(
    session: AsyncSession,
    settings: Settings,
    *,
    now: datetime,
) -> CapacityCollectionDecision:
    """Serialize execution with capacity kills and fail closed in-place.

    The transaction-scoped advisory lock is intentionally retained by the
    caller through the authoritative RuntimeControl read and the paper fill or
    live order-intent write. Therefore a capacity kill can commit either
    before execution observes it or after that execution transaction commits,
    but never between its final safety check and durable intent.
    """

    await acquire_capacity_write_lock(session)
    check = await authoritative_capacity_check(
        session,
        now=now,
        budget_bytes=settings.clob_ledger_capacity_budget_bytes,
        minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
        sample_ttl=timedelta(minutes=settings.clob_ledger_capacity_sample_ttl_minutes),
        storage_path_override=settings.clob_ledger_capacity_path,
    )
    if check.decision.allowed:
        return check.decision

    control = await get_runtime_control(session)
    if not control.kill_switch:
        await set_kill_switch(
            session,
            True,
            expiration_reason=f"storage_capacity:{check.decision.reason}",
        )
    logger.error(
        "execution blocked by authoritative capacity gate: reason=%s sample_age_seconds=%s",
        check.decision.reason,
        check.decision.sample_age_seconds,
    )
    return check.decision
