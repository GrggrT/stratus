from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import create_engine, create_session_factory
from stratus.jobs.audit_gate import audit_gate
from stratus.jobs.audit_health import audit_health
from stratus.jobs.discover_markets import discover_markets
from stratus.jobs.execute_signals import execute_signals
from stratus.jobs.fetch_ensemble import fetch_ensemble
from stratus.jobs.fetch_forecasts import fetch_forecasts
from stratus.jobs.fetch_observations import fetch_observations
from stratus.jobs.fetch_prices import fetch_prices
from stratus.jobs.finalize_daily_actuals import finalize_daily_actuals
from stratus.jobs.generate_signals import generate_signals
from stratus.jobs.process_operator_commands import process_operator_commands
from stratus.jobs.prune_signals import prune_signals
from stratus.jobs.recalibrate_emos import recalibrate_emos
from stratus.jobs.recompute_probs import recompute_probs
from stratus.jobs.settle_markets import settle_markets
from stratus.repositories.job_runs import record_job_run
from stratus.repositories.telegram import enqueue_notification

logger = logging.getLogger(__name__)
_PROBABILITY_PIPELINE_LOCK = asyncio.Lock()

SessionFactory = async_sessionmaker[AsyncSession]


async def _probability_pipeline(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    async with _PROBABILITY_PIPELINE_LOCK:
        written, _ = await recompute_probs(settings, sessions)
        if written:
            await generate_signals(settings, sessions)


async def forecast_cycle(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    if await fetch_forecasts(settings, sessions):
        await _probability_pipeline(settings, sessions)


async def ensemble_cycle(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    if await fetch_ensemble(settings, sessions):
        await _probability_pipeline(settings, sessions)


async def price_cycle(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    written, _ = await fetch_prices(settings, sessions)
    if written:
        await _probability_pipeline(settings, sessions)


async def startup_refresh(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    await discover_markets(settings, sessions)
    forecasts, ensemble, _observations = await asyncio.gather(
        fetch_forecasts(settings, sessions),
        fetch_ensemble(settings, sessions),
        fetch_observations(settings, sessions),
    )
    await finalize_daily_actuals(settings, sessions)
    prices, _ = await fetch_prices(settings, sessions)
    if forecasts or ensemble or prices:
        await _probability_pipeline(settings, sessions)
    await execute_signals(settings, sessions)
    await settle_markets(settings, sessions)


async def safe_startup_refresh(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    """Run the eager refresh without letting source-data gaps kill the scheduler."""
    try:
        await startup_refresh(settings, sessions)
    except Exception:
        logger.exception(
            "startup refresh failed; scheduled jobs and operator commands remain active"
        )


def build_scheduler(
    settings: Settings, sessions: SessionFactory | None = None
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    common = {"args": [settings, sessions], "coalesce": True, "max_instances": 1}
    jobs: list[
        tuple[Callable[..., Awaitable[object]], object, str]
    ] = [
        (forecast_cycle, IntervalTrigger(hours=1), "forecasts"),
        (ensemble_cycle, IntervalTrigger(hours=6), "ensemble"),
        (fetch_observations, IntervalTrigger(minutes=20), "observations"),
        (finalize_daily_actuals, IntervalTrigger(hours=1), "daily-actuals"),
        (discover_markets, IntervalTrigger(minutes=30), "market-discovery"),
        (price_cycle, IntervalTrigger(minutes=1), "prices-and-signals"),
        (execute_signals, IntervalTrigger(minutes=1), "paper-execution"),
        (settle_markets, IntervalTrigger(minutes=10), "settlement"),
        (audit_health, IntervalTrigger(minutes=10), "data-quality"),
        (
            recalibrate_emos,
            CronTrigger(day_of_week="sun", hour=4, minute=0, timezone=UTC),
            "emos-calibration",
        ),
        (
            audit_gate,
            CronTrigger(hour=8, minute=0, timezone=ZoneInfo("Europe/Berlin")),
            "validation-gate",
        ),
        (
            prune_signals,
            CronTrigger(hour=3, minute=30, timezone=UTC),
            "signal-retention",
        ),
        (
            process_operator_commands,
            IntervalTrigger(seconds=20),
            "operator-commands",
        ),
    ]
    for function, trigger, job_id in jobs:
        scheduler.add_job(function, trigger, id=job_id, **common)
    return scheduler


async def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    # One engine for the whole scheduler process: jobs share its pool instead
    # of building and disposing their own engine on every invocation.
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    scheduler = build_scheduler(settings, sessions)

    def request_stop() -> None:
        stopped.set()

    async def persist_job_event(event: JobExecutionEvent) -> None:
        status = "success"
        if event.code == EVENT_JOB_ERROR:
            status = "error"
        elif event.code == EVENT_JOB_MISSED:
            status = "missed"
        # The command poller fires every 20s; recording each success would
        # flood job_runs. The queue itself is the audit trail; failures are
        # still persisted and alerted below.
        if event.job_id == "operator-commands" and status == "success":
            return
        error = str(event.exception)[:4000] if event.exception is not None else None
        try:
            async with sessions.begin() as session:
                await record_job_run(
                    session,
                    job_id=event.job_id,
                    scheduled_at=event.scheduled_run_time,
                    finished_at=datetime.now(UTC),
                    status=status,
                    error=error,
                )
                if status != "success":
                    await enqueue_notification(
                        session,
                        f"job-{status}:{event.job_id}:{event.scheduled_run_time.isoformat()}",
                        f"STRATUS job {status}: {event.job_id}\n{error or 'No details'}",
                    )
        except Exception:
            logger.exception("failed to persist scheduler event for %s", event.job_id)
        if status != "success":
            logger.error("scheduled job %s %s: %s", event.job_id, status, error)

    # asyncio holds tasks by weak reference only; keep strong references so
    # listener tasks cannot be garbage-collected mid-flight.
    listener_tasks: set[asyncio.Task[None]] = set()

    def handle_job_event(event: JobExecutionEvent) -> None:
        task = loop.create_task(persist_job_event(event))
        listener_tasks.add(task)
        task.add_done_callback(listener_tasks.discard)

    scheduler.add_listener(
        handle_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
    )
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            pass

    scheduler.start()
    logger.info("STRATUS scheduler started with %d jobs", len(scheduler.get_jobs()))
    try:
        await safe_startup_refresh(settings, sessions)
        await stopped.wait()
    finally:
        scheduler.shutdown(wait=False)
        if listener_tasks:
            await asyncio.gather(*listener_tasks, return_exceptions=True)
        await engine.dispose()
        logger.info("STRATUS scheduler stopped at %s", datetime.now(UTC).isoformat())


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
