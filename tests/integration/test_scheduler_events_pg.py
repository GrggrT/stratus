from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from apscheduler.events import EVENT_JOB_MAX_INSTANCES, JobSubmissionEvent
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus import runtime
from stratus.db.models import JobRun, TelegramDelivery
from stratus.repositories.evidence_continuity_reporting import (
    _scheduler_coverage_windows,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def test_max_instances_is_idempotent_and_fails_continuity_uptime(
    db_sessions: SessionFactory,
) -> None:
    interval = timedelta(seconds=15)
    scheduled_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    event = JobSubmissionEvent(
        EVENT_JOB_MAX_INSTANCES,
        "execution-cost-maintenance",
        "default",
        [scheduled_at],
    )

    await runtime._persist_scheduler_event(db_sessions, event)
    await runtime._persist_scheduler_event(db_sessions, event)

    async with db_sessions() as session:
        runs = (
            await session.scalars(
                select(JobRun).where(
                    JobRun.job_id == "execution-cost-maintenance",
                    JobRun.scheduled_at == scheduled_at,
                )
            )
        ).all()
        deliveries = (
            await session.scalars(
                select(TelegramDelivery).where(
                    TelegramDelivery.event_key
                    == (f"job-max_instances:execution-cost-maintenance:{scheduled_at.isoformat()}")
                )
            )
        ).all()
        coverage_windows = await _scheduler_coverage_windows(
            session,
            historical_started_at=None,
            historical_ended_at=None,
            current_started_at=scheduled_at - interval * 3,
            now=scheduled_at,
        )

    assert len(runs) == 1
    assert runs[0].status == "max_instances"
    assert "was not executed" in (runs[0].error or "")
    assert len(deliveries) == 1
    assert len(coverage_windows) == 1
    assert coverage_windows[0].current is True
    assert coverage_windows[0].missing_scheduler_jobs == ("execution-cost-maintenance",)
