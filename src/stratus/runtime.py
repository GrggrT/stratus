from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.session import create_engine, create_session_factory, verify_runtime_engine
from stratus.jobs.assess_station_reliability import assess_station_reliability_job
from stratus.jobs.audit_accuracy import audit_accuracy
from stratus.jobs.audit_gate import audit_gate
from stratus.jobs.audit_health import audit_health
from stratus.jobs.audit_storage_capacity import audit_storage_capacity
from stratus.jobs.collect_binance_market_data import collect_binance_market_data
from stratus.jobs.discover_markets import discover_markets
from stratus.jobs.evaluate_forecasts import evaluate_forecasts
from stratus.jobs.evaluate_promotion_trials import evaluate_promotion_trials
from stratus.jobs.execute_live_signals import cancel_live_orders, poll_live_fills
from stratus.jobs.execute_signals import execute_signals
from stratus.jobs.fetch_ensemble import fetch_ensemble
from stratus.jobs.fetch_forecasts import fetch_forecasts
from stratus.jobs.fetch_observations import fetch_observations
from stratus.jobs.fetch_prices import fetch_prices
from stratus.jobs.finalize_daily_actuals import finalize_daily_actuals
from stratus.jobs.fit_settlement_calibration import fit_settlement_calibration
from stratus.jobs.generate_censored_challenger import generate_censored_challenger
from stratus.jobs.generate_signals import generate_signals
from stratus.jobs.generate_weighted_ensemble import generate_weighted_ensemble
from stratus.jobs.maintain_execution_cost_evidence import (
    EXECUTION_COST_MAINTENANCE_INTERVAL_SECONDS,
    ExecutionCostMaintenanceRun,
    maintain_execution_cost_evidence,
)
from stratus.jobs.process_operator_commands import process_operator_commands
from stratus.jobs.prune_signals import prune_signals
from stratus.jobs.recalibrate_emos import recalibrate_emos
from stratus.jobs.recompute_probs import recompute_probs
from stratus.jobs.reconcile_resolutions import reconcile_resolutions
from stratus.jobs.settle_markets import settle_markets
from stratus.jobs.weekly_edge_report import weekly_edge_report
from stratus.repositories.job_runs import record_job_run
from stratus.repositories.telegram import enqueue_notification

logger = logging.getLogger(__name__)
_PROBABILITY_PIPELINE_LOCK = asyncio.Lock()
_STATION_RELIABILITY_LOCK = asyncio.Lock()
_EXECUTION_COST_MAINTENANCE_LOCK = asyncio.Lock()

SessionFactory = async_sessionmaker[AsyncSession]
type SchedulerEvent = JobExecutionEvent | JobSubmissionEvent
SCHEDULER_EVENT_MASK = (
    EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED | EVENT_JOB_MAX_INSTANCES
)


@dataclass(frozen=True, slots=True)
class SchedulerEventOutcome:
    job_id: str
    scheduled_at: datetime
    status: str
    error: str | None


def _scheduler_event_outcomes(event: SchedulerEvent) -> tuple[SchedulerEventOutcome, ...]:
    """Normalize execution and submission failures without inventing a run."""

    if isinstance(event, JobExecutionEvent):
        status = "success"
        if event.code == EVENT_JOB_ERROR:
            status = "error"
        elif event.code == EVENT_JOB_MISSED:
            status = "missed"
        elif event.code != EVENT_JOB_EXECUTED:
            return ()
        error = str(event.exception)[:4000] if event.exception is not None else None
        return (
            SchedulerEventOutcome(
                job_id=event.job_id,
                scheduled_at=event.scheduled_run_time,
                status=status,
                error=error,
            ),
        )

    if event.code != EVENT_JOB_MAX_INSTANCES:
        return ()
    error = (
        "APScheduler EVENT_JOB_MAX_INSTANCES: scheduled invocation was not executed"
    )
    return tuple(
        SchedulerEventOutcome(
            job_id=event.job_id,
            scheduled_at=scheduled_at,
            status="max_instances",
            error=error,
        )
        for scheduled_at in event.scheduled_run_times
    )


async def _persist_scheduler_event(
    sessions: SessionFactory,
    event: SchedulerEvent,
) -> None:
    outcomes = tuple(
        outcome
        for outcome in _scheduler_event_outcomes(event)
        if not (
            outcome.job_id == "operator-commands" and outcome.status == "success"
        )
    )
    if not outcomes:
        return
    finished_at = datetime.now(UTC)
    try:
        async with sessions.begin() as session:
            for outcome in outcomes:
                await record_job_run(
                    session,
                    job_id=outcome.job_id,
                    scheduled_at=outcome.scheduled_at,
                    finished_at=finished_at,
                    status=outcome.status,
                    error=outcome.error,
                )
                if outcome.status != "success":
                    await enqueue_notification(
                        session,
                        (
                            f"job-{outcome.status}:{outcome.job_id}:"
                            f"{outcome.scheduled_at.isoformat()}"
                        ),
                        (
                            f"STRATUS job {outcome.status}: {outcome.job_id}\n"
                            f"{outcome.error or 'No details'}"
                        ),
                    )
    except Exception:
        logger.exception("failed to persist scheduler event for %s", event.job_id)
        return
    for outcome in outcomes:
        if outcome.status != "success":
            logger.error(
                "scheduled job %s %s: %s",
                outcome.job_id,
                outcome.status,
                outcome.error,
            )


async def _probability_pipeline_unlocked(
    settings: Settings, sessions: SessionFactory | None = None
) -> None:
    written, _ = await recompute_probs(settings, sessions)
    if written:
        await generate_signals(settings, sessions)


async def _probability_pipeline(settings: Settings, sessions: SessionFactory | None = None) -> None:
    async with _PROBABILITY_PIPELINE_LOCK:
        await _probability_pipeline_unlocked(settings, sessions)


async def forecast_cycle(settings: Settings, sessions: SessionFactory | None = None) -> None:
    if await fetch_forecasts(settings, sessions):
        await _probability_pipeline(settings, sessions)


async def ensemble_cycle(settings: Settings, sessions: SessionFactory | None = None) -> None:
    if await fetch_ensemble(settings, sessions):
        await _probability_pipeline(settings, sessions)


async def price_cycle(settings: Settings, sessions: SessionFactory | None = None) -> None:
    # Price evidence and the probability cohort derived from it form one
    # in-process critical section.  In particular, startup refresh and the
    # scheduled minute job must not interleave a newer CLOB snapshot between
    # recompute_probs' calculation_time and its provenance lookup.
    async with _PROBABILITY_PIPELINE_LOCK:
        written, _ = await fetch_prices(settings, sessions)
        if written:
            await _probability_pipeline_unlocked(settings, sessions)


async def station_reliability_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> int:
    """Refresh observation-sensitive station gates without overlapping reconciliation."""

    async with _STATION_RELIABILITY_LOCK:
        return await assess_station_reliability_job(settings, sessions)


async def execution_cost_maintenance_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> ExecutionCostMaintenanceRun:
    """Serialize eager and scheduled evidence maintenance within this worker."""

    async with _EXECUTION_COST_MAINTENANCE_LOCK:
        return await maintain_execution_cost_evidence(settings, sessions)


async def resolution_reconciliation_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> tuple[int, int]:
    """Reconcile venue outcomes and immediately publish their station gate projection."""

    async with _STATION_RELIABILITY_LOCK:
        return await _resolution_reconciliation_unlocked(settings, sessions)


async def _resolution_reconciliation_unlocked(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> tuple[int, int]:
    reconciled = await reconcile_resolutions(settings, sessions)
    assessed = await assess_station_reliability_job(settings, sessions)
    return reconciled, assessed


async def shadow_probability_cycle(
    settings: Settings, sessions: SessionFactory | None = None
) -> int:
    """Publish an explicit all-station baseline for prospective challengers."""

    async with _PROBABILITY_PIPELINE_LOCK:
        written, _ = await recompute_probs(
            settings,
            sessions,
            include_shadow=True,
            seal_accuracy_evidence=True,
        )
    return written


async def _record_startup_job_run(
    sessions: SessionFactory | None,
    *,
    job_id: str,
    scheduled_at: datetime,
    status: str,
    error: str | None,
) -> None:
    if sessions is None:
        return
    try:
        async with sessions.begin() as session:
            await record_job_run(
                session,
                job_id=job_id,
                scheduled_at=scheduled_at,
                finished_at=datetime.now(UTC),
                status=status,
                error=error,
            )
    except Exception:
        logger.exception("failed to persist startup job event for %s", job_id)


async def _run_startup_job(
    job_id: str,
    function: Callable[[Settings, SessionFactory | None], Awaitable[object]],
    settings: Settings,
    sessions: SessionFactory | None,
    *,
    fatal: bool = False,
) -> object:
    scheduled_at = datetime.now(UTC)
    try:
        result = await function(settings, sessions)
    except Exception as exc:
        error = str(exc)[:4000]
        await _record_startup_job_run(
            sessions,
            job_id=job_id,
            scheduled_at=scheduled_at,
            status="error",
            error=error,
        )
        if fatal:
            raise
        logger.warning("startup job %s failed; continuing: %s", job_id, error)
        return None
    await _record_startup_job_run(
        sessions,
        job_id=job_id,
        scheduled_at=scheduled_at,
        status="success",
        error=None,
    )
    return result


async def observation_reliability_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> tuple[object, object]:
    """Commit fresh observations before publishing their station-gate projection.

    APScheduler dispatches independently due jobs concurrently, so registering
    observation collection and station assessment at the same interval does not
    establish ordering. Keep both phases under the station lock and retain a
    distinct ``station-reliability`` job-run record for operator liveness.
    """

    async with _STATION_RELIABILITY_LOCK:
        observations = await fetch_observations(settings, sessions)
        assessed = await _run_startup_job(
            "station-reliability",
            assess_station_reliability_job,
            settings,
            sessions,
            fatal=True,
        )
    return observations, assessed


async def trading_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> tuple[None, object]:
    """Publish and execute one cohort against a serial station-gate snapshot."""

    async with _STATION_RELIABILITY_LOCK:
        await price_cycle(settings, sessions)
        execution = await _run_startup_job(
            "execution",
            execute_signals,
            settings,
            sessions,
            fatal=True,
        )
    return None, execution


async def settlement_reconciliation_cycle(
    settings: Settings,
    sessions: SessionFactory | None = None,
) -> tuple[object, tuple[int, int]]:
    """Refresh venue winners before reconciliation and station-gate projection."""

    async with _STATION_RELIABILITY_LOCK:
        settlement = await _run_startup_job(
            "settlement",
            settle_markets,
            settings,
            sessions,
            fatal=True,
        )
        reconciliation = await _resolution_reconciliation_unlocked(settings, sessions)
    return settlement, reconciliation


async def startup_refresh(settings: Settings, sessions: SessionFactory | None = None) -> None:
    # Capacity is a precondition for immutable CLOB writes, not an after-the-
    # fact metric. If this probe fails, fetch_prices also fails closed on the
    # missing/stale sample and no price evidence is appended.
    await _run_startup_job("storage-capacity", audit_storage_capacity, settings, sessions)
    await _run_startup_job("market-discovery", discover_markets, settings, sessions)
    await asyncio.gather(
        _run_startup_job("forecasts", forecast_cycle, settings, sessions),
        _run_startup_job("ensemble", ensemble_cycle, settings, sessions),
        _run_startup_job("observations", fetch_observations, settings, sessions),
    )
    await _run_startup_job("daily-actuals", finalize_daily_actuals, settings, sessions)
    # A newly discovered resolution conflict must be projected into station
    # selection evidence before the first signal or order can be created.
    await _run_startup_job(
        "settlement",
        settle_markets,
        settings,
        sessions,
        fatal=True,
    )
    await _run_startup_job(
        "resolution-reconciliation",
        reconcile_resolutions,
        settings,
        sessions,
        fatal=True,
    )
    await _run_startup_job(
        "station-reliability-pretrade",
        station_reliability_cycle,
        settings,
        sessions,
        fatal=True,
    )
    await _run_startup_job("prices-and-signals", price_cycle, settings, sessions)
    await _run_startup_job("binance-market-data", collect_binance_market_data, settings, sessions)
    await _run_startup_job("execution", execute_signals, settings, sessions)
    await _run_startup_job(
        "execution-cost-maintenance",
        execution_cost_maintenance_cycle,
        settings,
        sessions,
    )
    await _run_startup_job(
        "shadow-probabilities",
        shadow_probability_cycle,
        settings,
        sessions,
    )
    await _run_startup_job(
        "censored-challenger",
        generate_censored_challenger,
        settings,
        sessions,
    )
    await _run_startup_job(
        "weighted-ensemble",
        generate_weighted_ensemble,
        settings,
        sessions,
    )


async def safe_startup_refresh(settings: Settings, sessions: SessionFactory | None = None) -> None:
    """Run the eager refresh without letting source-data gaps kill the scheduler."""
    try:
        await startup_refresh(settings, sessions)
    except Exception:
        logger.exception(
            "startup refresh failed; scheduled jobs and operator commands remain active"
        )


def build_scheduler(settings: Settings, sessions: SessionFactory | None = None) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=UTC)
    common = {
        "args": [settings, sessions],
        "coalesce": True,
        "max_instances": 1,
        # Accuracy audits and model fits can briefly saturate a local machine.
        # Run a slightly late operational job instead of silently dropping it.
        "misfire_grace_time": 30,
    }
    jobs: list[tuple[Callable[..., Awaitable[object]], object, str]] = [
        (forecast_cycle, IntervalTrigger(hours=1), "forecasts"),
        (ensemble_cycle, IntervalTrigger(hours=6), "ensemble"),
        (observation_reliability_cycle, IntervalTrigger(minutes=20), "observations"),
        (finalize_daily_actuals, IntervalTrigger(hours=1), "daily-actuals"),
        (discover_markets, IntervalTrigger(minutes=30), "market-discovery"),
        (
            trading_cycle,
            CronTrigger(second=30, timezone=UTC),
            "prices-and-signals",
        ),
        (
            collect_binance_market_data,
            IntervalTrigger(minutes=1),
            "binance-market-data",
        ),
        (
            execution_cost_maintenance_cycle,
            IntervalTrigger(seconds=EXECUTION_COST_MAINTENANCE_INTERVAL_SECONDS),
            "execution-cost-maintenance",
        ),
        (poll_live_fills, IntervalTrigger(minutes=1), "live-fills"),
        (cancel_live_orders, IntervalTrigger(minutes=1), "live-cancellations"),
        (
            settle_markets,
            CronTrigger(minute="10,20,30,40,50", second=0, timezone=UTC),
            "settlement",
        ),
        (
            settlement_reconciliation_cycle,
            CronTrigger(minute=0, second=0, timezone=UTC),
            "resolution-reconciliation",
        ),
        (
            audit_storage_capacity,
            IntervalTrigger(hours=1),
            "storage-capacity",
        ),
        (evaluate_forecasts, IntervalTrigger(hours=1), "forecast-evaluation"),
        (
            audit_accuracy,
            CronTrigger(hour=5, minute=0, timezone=UTC),
            "accuracy-audit",
        ),
        (
            fit_settlement_calibration,
            CronTrigger(hour=5, minute=15, timezone=UTC),
            "settlement-calibration",
        ),
        (
            shadow_probability_cycle,
            # Five pre-registered snapshots cover the main horizon buckets;
            # 05:18 additionally provides a fresh baseline for the 05:20/05:22
            # challenger fits.  Minute price refreshes never write evidence.
            CronTrigger(hour="0,5,6,12,18", minute=18, timezone=UTC),
            "shadow-probabilities",
        ),
        (
            generate_censored_challenger,
            CronTrigger(hour=5, minute=20, timezone=UTC),
            "censored-challenger",
        ),
        (
            generate_weighted_ensemble,
            CronTrigger(hour=5, minute=22, timezone=UTC),
            "weighted-ensemble",
        ),
        (
            evaluate_promotion_trials,
            # Registration occurs only after the daily 05:18 sealed snapshot
            # and both 05:20/05:22 challenger generators.  A once-daily fixed
            # look is sufficient because evidence blocks are station-days;
            # running it more often would add no information and would invite
            # accidental optional-stopping semantics.
            CronTrigger(hour=5, minute=25, timezone=UTC),
            "accuracy-promotions",
        ),
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
            weekly_edge_report,
            CronTrigger(day_of_week="mon", hour=8, minute=30, timezone=ZoneInfo("Europe/Berlin")),
            "edge-report",
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
    await verify_runtime_engine(engine)
    sessions = create_session_factory(engine)
    scheduler = build_scheduler(settings, sessions)

    def request_stop() -> None:
        stopped.set()

    # asyncio holds tasks by weak reference only; keep strong references so
    # listener tasks cannot be garbage-collected mid-flight.
    listener_tasks: set[asyncio.Task[None]] = set()

    def handle_job_event(event: SchedulerEvent) -> None:
        task = loop.create_task(_persist_scheduler_event(sessions, event))
        listener_tasks.add(task)
        task.add_done_callback(listener_tasks.discard)

    scheduler.add_listener(handle_job_event, SCHEDULER_EVENT_MASK)
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            pass

    try:
        await safe_startup_refresh(settings, sessions)
        scheduler.start()
        logger.info("STRATUS scheduler started with %d jobs", len(scheduler.get_jobs()))
        await stopped.wait()
    finally:
        if scheduler.running:
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
