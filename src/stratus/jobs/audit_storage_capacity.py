from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.runtime_control import set_kill_switch
from stratus.repositories.storage_capacity import (
    acquire_capacity_write_lock,
    capacity_baseline,
    read_capacity_measurement,
    sample_capacity_point,
    store_capacity_sample,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.services.storage_capacity import (
    CAPACITY_POLICY_VERSION,
    CapacityAssessment,
    assess_clob_ledger_capacity,
)

logger = logging.getLogger(__name__)


async def audit_storage_capacity(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> CapacityAssessment:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=settings.clob_ledger_capacity_min_window_hours)
    async with session_factory_scope(settings, session_factory) as sessions:
        notification: tuple[str, str] | None = None
        async with sessions.begin() as session:
            await acquire_capacity_write_lock(session)
            measurement = await read_capacity_measurement(
                session,
                captured_at=now,
                storage_path_override=settings.clob_ledger_capacity_path,
            )
            baseline = await capacity_baseline(session, at_or_before=cutoff)
            assessment = assess_clob_ledger_capacity(
                measurement.point,
                sample_capacity_point(baseline) if baseline is not None else None,
                budget_bytes=settings.clob_ledger_capacity_budget_bytes,
                warning_fraction=settings.clob_ledger_capacity_warning_fraction,
                projection_days=settings.clob_ledger_capacity_projection_days,
                minimum_window_hours=settings.clob_ledger_capacity_min_window_hours,
                minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
            )
            details = asdict(assessment)
            details.update(
                {
                    "automatic_deletion": False,
                    "retention_rule": (
                        "retain immutable evidence until an attested archive and "
                        "referential-integrity proof exist"
                    ),
                    "free_disk_path": measurement.free_disk_path,
                    "free_disk_error": measurement.free_disk_error,
                }
            )
            await store_capacity_sample(
                session,
                measurement,
                policy_version=CAPACITY_POLICY_VERSION,
                details=details,
            )
            if assessment.severity == "blocking":
                await set_kill_switch(
                    session,
                    True,
                    expiration_reason=f"storage_capacity:{assessment.reason}",
                )
            if assessment.severity in {"warning", "blocking"}:
                bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
                notification = (
                    f"storage-capacity:{assessment.severity}:{bucket}",
                    (
                        f"STRATUS storage {assessment.severity}: {assessment.reason}\n"
                        f"CLOB ledger {assessment.ledger_bytes:,} / "
                        f"{assessment.budget_bytes:,} bytes; free disk "
                        f"{assessment.free_disk_bytes!s} / reserve "
                        f"{assessment.minimum_free_disk_bytes:,} bytes; "
                        "automatic deletion disabled"
                    ),
                )
        # Capacity state and the kill switch are safety-critical. Telegram is
        # best-effort and must never roll their transaction back.
        if notification is not None:
            try:
                async with sessions.begin() as session:
                    await enqueue_notification(session, *notification)
            except Exception:  # noqa: BLE001 - safety state is already durable
                logger.exception("storage capacity notification could not be queued")
    logger.info(
        "storage capacity audit: severity=%s reason=%s ledger_bytes=%d growth_day=%s",
        assessment.severity,
        assessment.reason,
        assessment.ledger_bytes,
        assessment.growth_bytes_per_day,
    )
    return assessment


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_storage_capacity(settings))


if __name__ == "__main__":
    main()
