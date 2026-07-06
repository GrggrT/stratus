from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import JobRun
from stratus.services.uptime import CollectorUptime, JobOutcome, calculate_collector_uptime


async def record_job_run(
    session: AsyncSession,
    *,
    job_id: str,
    scheduled_at: datetime,
    finished_at: datetime,
    status: str,
    error: str | None,
) -> None:
    statement = insert(JobRun).values(
        job_id=job_id,
        scheduled_at=scheduled_at,
        finished_at=finished_at,
        status=status,
        error=error,
    )
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_job_runs_schedule",
            set_={
                "finished_at": statement.excluded.finished_at,
                "status": statement.excluded.status,
                "error": statement.excluded.error,
            },
        )
    )


async def collector_uptime(
    session: AsyncSession,
    *,
    now: datetime,
    window: timedelta = timedelta(hours=48),
) -> CollectorUptime:
    monitoring_started = await session.scalar(select(func.min(JobRun.scheduled_at)))
    rows = (
        await session.execute(
            select(JobRun.job_id, JobRun.scheduled_at, JobRun.status).where(
                JobRun.scheduled_at > now - window
            )
        )
    ).all()
    return calculate_collector_uptime(
        [JobOutcome(job_id, scheduled_at, status) for job_id, scheduled_at, status in rows],
        now=now,
        monitoring_started=monitoring_started,
        window=window,
    )
