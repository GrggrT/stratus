import asyncio
from datetime import UTC, datetime, timedelta

from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MAX_INSTANCES,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
    JobSubmissionEvent,
)
from apscheduler.triggers.cron import CronTrigger

from stratus import runtime
from stratus.config import Settings


def test_scheduler_allows_short_operational_misfires() -> None:
    scheduler = runtime.build_scheduler(Settings())

    assert scheduler.get_jobs()
    assert all(job.misfire_grace_time == 30 for job in scheduler.get_jobs())
    assert scheduler.get_job("shadow-probabilities") is not None
    assert scheduler.get_job("accuracy-promotions") is not None
    maintenance = scheduler.get_job("execution-cost-maintenance")
    assert maintenance is not None
    assert maintenance.func is runtime.execution_cost_maintenance_cycle
    assert maintenance.trigger.interval == timedelta(seconds=15)
    assert scheduler.get_job("execution-markouts") is None
    assert scheduler.get_job("execution-cost-finalization") is None
    assert scheduler.get_job("observations").func is runtime.observation_reliability_cycle
    assert scheduler.get_job("prices-and-signals").func is runtime.trading_cycle
    assert (
        scheduler.get_job("resolution-reconciliation").func
        is runtime.settlement_reconciliation_cycle
    )
    assert scheduler.get_job("station-reliability") is None
    assert scheduler.get_job("execution") is None
    assert isinstance(scheduler.get_job("prices-and-signals").trigger, CronTrigger)
    assert str(scheduler.get_job("prices-and-signals").trigger.fields[7]) == "30"
    assert runtime.SCHEDULER_EVENT_MASK & EVENT_JOB_MAX_INSTANCES


def test_scheduler_event_outcomes_include_max_instance_skips() -> None:
    first = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    second = first + timedelta(seconds=15)
    skipped = JobSubmissionEvent(
        EVENT_JOB_MAX_INSTANCES,
        "execution-cost-maintenance",
        "default",
        [first, second],
    )

    outcomes = runtime._scheduler_event_outcomes(skipped)

    assert [(item.scheduled_at, item.status) for item in outcomes] == [
        (first, "max_instances"),
        (second, "max_instances"),
    ]
    assert all("was not executed" in (item.error or "") for item in outcomes)


def test_scheduler_execution_event_outcomes_preserve_failure_semantics() -> None:
    scheduled_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    success = JobExecutionEvent(
        EVENT_JOB_EXECUTED,
        "prices",
        "default",
        scheduled_at,
    )
    failed = JobExecutionEvent(
        EVENT_JOB_ERROR,
        "prices",
        "default",
        scheduled_at,
        exception=RuntimeError("boom"),
    )
    missed = JobExecutionEvent(
        EVENT_JOB_MISSED,
        "prices",
        "default",
        scheduled_at,
    )

    assert runtime._scheduler_event_outcomes(success)[0].status == "success"
    assert runtime._scheduler_event_outcomes(failed)[0].status == "error"
    assert runtime._scheduler_event_outcomes(failed)[0].error == "boom"
    assert runtime._scheduler_event_outcomes(missed)[0].status == "missed"


async def test_scheduler_max_instance_event_is_persisted_and_alerted(
    monkeypatch,
) -> None:
    first = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    second = first + timedelta(seconds=15)
    recorded: list[dict[str, object]] = []
    notifications: list[tuple[str, str]] = []

    class Transaction:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
            return None

    class Sessions:
        def begin(self) -> Transaction:
            return Transaction()

    async def fake_record(_session, **values) -> None:
        recorded.append(values)

    async def fake_notify(_session, key: str, message: str) -> None:
        notifications.append((key, message))

    monkeypatch.setattr(runtime, "record_job_run", fake_record)
    monkeypatch.setattr(runtime, "enqueue_notification", fake_notify)

    await runtime._persist_scheduler_event(
        Sessions(),  # type: ignore[arg-type]
        JobSubmissionEvent(
            EVENT_JOB_MAX_INSTANCES,
            "execution-cost-maintenance",
            "default",
            [first, second],
        ),
    )

    assert [item["scheduled_at"] for item in recorded] == [first, second]
    assert {item["status"] for item in recorded} == {"max_instances"}
    assert len(notifications) == 2
    assert all("max_instances" in key for key, _message in notifications)


def test_settlement_and_reconciliation_crons_do_not_share_a_due_minute() -> None:
    scheduler = runtime.build_scheduler(Settings())
    settlement = scheduler.get_job("settlement")
    reconciliation = scheduler.get_job("resolution-reconciliation")

    assert isinstance(settlement.trigger, CronTrigger)
    assert isinstance(reconciliation.trigger, CronTrigger)

    def due_minutes(trigger: CronTrigger, *, count: int) -> set[datetime]:
        cursor = datetime(2026, 7, 17, tzinfo=UTC)
        previous: datetime | None = None
        values: set[datetime] = set()
        for _ in range(count):
            next_fire = trigger.get_next_fire_time(previous, cursor)
            assert next_fire is not None
            values.add(next_fire.astimezone(UTC).replace(second=0, microsecond=0))
            previous = next_fire
            cursor = next_fire
        return values

    settlement_due = due_minutes(settlement.trigger, count=120)
    reconciliation_due = due_minutes(reconciliation.trigger, count=24)

    assert settlement_due & reconciliation_due == set()
    assert {value.minute for value in settlement_due} == {10, 20, 30, 40, 50}
    assert {value.minute for value in reconciliation_due} == {0}


async def test_observation_reliability_cycle_waits_for_observation_commit(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime, "_STATION_RELIABILITY_LOCK", asyncio.Lock())
    fetch_started = asyncio.Event()
    release_fetch = asyncio.Event()
    events: list[str] = []
    child_jobs: list[tuple[str, bool]] = []

    async def fake_fetch(_settings: Settings, _sessions=None) -> int:
        events.append("observations-start")
        fetch_started.set()
        await release_fetch.wait()
        events.append("observations-committed")
        return 7

    async def fake_assess(_settings: Settings, _sessions=None) -> int:
        events.append("station-reliability")
        return 3

    async def invoke_child(
        job_id: str,
        function,
        settings: Settings,
        sessions,
        *,
        fatal: bool = False,
    ):
        child_jobs.append((job_id, fatal))
        return await function(settings, sessions)

    monkeypatch.setattr(runtime, "fetch_observations", fake_fetch)
    monkeypatch.setattr(runtime, "assess_station_reliability_job", fake_assess)
    monkeypatch.setattr(runtime, "_run_startup_job", invoke_child)

    task = asyncio.create_task(runtime.observation_reliability_cycle(Settings()))
    await fetch_started.wait()
    await asyncio.sleep(0)

    assert events == ["observations-start"]
    assert child_jobs == []

    release_fetch.set()
    result = await task

    assert result == (7, 3)
    assert events == [
        "observations-start",
        "observations-committed",
        "station-reliability",
    ]
    assert child_jobs == [("station-reliability", True)]


async def test_trading_cycle_waits_for_price_signal_pipeline_before_execution(
    monkeypatch,
) -> None:
    price_started = asyncio.Event()
    release_price = asyncio.Event()
    events: list[str] = []
    child_jobs: list[tuple[str, bool]] = []

    async def fake_price(_settings: Settings, _sessions=None) -> None:
        events.append("prices-start")
        price_started.set()
        await release_price.wait()
        events.append("signals-committed")

    async def fake_execute(
        _settings: Settings,
        _sessions=None,
    ) -> tuple[int, int, int]:
        events.append("execution")
        return 5, 1, 0

    async def invoke_child(
        job_id: str,
        function,
        settings: Settings,
        sessions,
        *,
        fatal: bool = False,
    ):
        child_jobs.append((job_id, fatal))
        return await function(settings, sessions)

    monkeypatch.setattr(runtime, "price_cycle", fake_price)
    monkeypatch.setattr(runtime, "execute_signals", fake_execute)
    monkeypatch.setattr(runtime, "_run_startup_job", invoke_child)

    task = asyncio.create_task(runtime.trading_cycle(Settings()))
    await price_started.wait()
    await asyncio.sleep(0)

    assert events == ["prices-start"]
    assert child_jobs == []

    release_price.set()
    result = await task

    assert result == (None, (5, 1, 0))
    assert events == ["prices-start", "signals-committed", "execution"]
    assert child_jobs == [("execution", True)]


async def test_settlement_reconciliation_cycle_waits_for_settlement_projection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime, "_STATION_RELIABILITY_LOCK", asyncio.Lock())
    settlement_started = asyncio.Event()
    release_settlement = asyncio.Event()
    events: list[str] = []
    child_jobs: list[tuple[str, bool]] = []

    async def fake_settle(_settings: Settings, _sessions=None) -> int:
        events.append("settlement-start")
        settlement_started.set()
        await release_settlement.wait()
        events.append("settlement-committed")
        return 4

    async def fake_reconcile(_settings: Settings, _sessions=None) -> int:
        events.append("reconciliation")
        return 5

    async def fake_assess(_settings: Settings, _sessions=None) -> int:
        events.append("station-reliability")
        return 6

    async def invoke_child(
        job_id: str,
        function,
        settings: Settings,
        sessions,
        *,
        fatal: bool = False,
    ):
        child_jobs.append((job_id, fatal))
        return await function(settings, sessions)

    monkeypatch.setattr(runtime, "settle_markets", fake_settle)
    monkeypatch.setattr(runtime, "reconcile_resolutions", fake_reconcile)
    monkeypatch.setattr(runtime, "assess_station_reliability_job", fake_assess)
    monkeypatch.setattr(runtime, "_run_startup_job", invoke_child)

    task = asyncio.create_task(runtime.settlement_reconciliation_cycle(Settings()))
    await settlement_started.wait()
    await asyncio.sleep(0)

    assert events == ["settlement-start"]
    assert child_jobs == [("settlement", True)]

    release_settlement.set()
    result = await task

    assert result == (4, (5, 6))
    assert events == [
        "settlement-start",
        "settlement-committed",
        "reconciliation",
        "station-reliability",
    ]
    assert child_jobs == [("settlement", True)]


async def test_trading_waits_for_settlement_reconciliation_projection(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_STATION_RELIABILITY_LOCK", asyncio.Lock())
    settlement_started = asyncio.Event()
    release_settlement = asyncio.Event()
    events: list[str] = []

    async def fake_settle(_settings: Settings, _sessions=None) -> int:
        events.append("settlement")
        settlement_started.set()
        await release_settlement.wait()
        return 1

    async def fake_reconcile(_settings: Settings, _sessions=None) -> int:
        events.append("reconciliation")
        return 2

    async def fake_assess(_settings: Settings, _sessions=None) -> int:
        events.append("station-reliability")
        return 3

    async def fake_prices(_settings: Settings, _sessions=None) -> None:
        events.append("prices-and-signals")

    async def fake_execute(_settings: Settings, _sessions=None) -> int:
        events.append("execution")
        return 4

    async def invoke_child(job_id, func, settings, sessions, *, fatal=False):
        assert fatal
        return await func(settings, sessions)

    monkeypatch.setattr(runtime, "settle_markets", fake_settle)
    monkeypatch.setattr(runtime, "reconcile_resolutions", fake_reconcile)
    monkeypatch.setattr(runtime, "assess_station_reliability_job", fake_assess)
    monkeypatch.setattr(runtime, "price_cycle", fake_prices)
    monkeypatch.setattr(runtime, "execute_signals", fake_execute)
    monkeypatch.setattr(runtime, "_run_startup_job", invoke_child)

    settlement_task = asyncio.create_task(
        runtime.settlement_reconciliation_cycle(Settings())
    )
    await settlement_started.wait()
    trading_task = asyncio.create_task(runtime.trading_cycle(Settings()))
    await asyncio.sleep(0)

    assert events == ["settlement"]

    release_settlement.set()
    await settlement_task
    await trading_task

    assert events == [
        "settlement",
        "reconciliation",
        "station-reliability",
        "prices-and-signals",
        "execution",
    ]


async def test_resolution_reconciliation_cycle_projects_reliability_after_reconcile(
    monkeypatch,
) -> None:
    monkeypatch.setattr(runtime, "_STATION_RELIABILITY_LOCK", asyncio.Lock())
    events: list[str] = []

    async def fake_reconcile(_settings: Settings, _sessions=None) -> int:
        events.append("reconcile")
        return 7

    async def fake_assess(_settings: Settings, _sessions=None) -> int:
        events.append("assess")
        return 3

    monkeypatch.setattr(runtime, "reconcile_resolutions", fake_reconcile)
    monkeypatch.setattr(runtime, "assess_station_reliability_job", fake_assess)

    result = await runtime.resolution_reconciliation_cycle(Settings())

    assert result == (7, 3)
    assert events == ["reconcile", "assess"]


async def test_shadow_probability_cycle_explicitly_includes_shadow(monkeypatch) -> None:
    calls: list[bool] = []

    async def fake_recompute(
        _settings: Settings,
        _sessions=None,
        *,
        include_shadow: bool = False,
        seal_accuracy_evidence: bool = False,
    ) -> tuple[int, int]:
        calls.append(include_shadow and seal_accuracy_evidence)
        return 7, 0

    monkeypatch.setattr(runtime, "recompute_probs", fake_recompute)

    written = await runtime.shadow_probability_cycle(Settings())

    assert written == 7
    assert calls == [True]


async def test_probability_pipeline_is_serialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_PROBABILITY_PIPELINE_LOCK", asyncio.Lock())
    active = 0
    max_active = 0
    events: list[str] = []

    async def fake_recompute(_settings: Settings, _sessions=None) -> tuple[int, int]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append("recompute")
        await asyncio.sleep(0.01)
        return 1, 0

    async def fake_generate(_settings: Settings, _sessions=None) -> tuple[int, int]:
        nonlocal active
        events.append("signals")
        active -= 1
        return 0, 0

    monkeypatch.setattr(runtime, "recompute_probs", fake_recompute)
    monkeypatch.setattr(runtime, "generate_signals", fake_generate)

    await asyncio.gather(
        runtime._probability_pipeline(Settings()),
        runtime._probability_pipeline(Settings()),
    )

    assert max_active == 1
    assert events == ["recompute", "signals", "recompute", "signals"]


async def test_execution_cost_maintenance_is_serialized(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_EXECUTION_COST_MAINTENANCE_LOCK", asyncio.Lock())
    active = 0
    max_active = 0
    events: list[str] = []

    async def fake_maintenance(_settings: Settings, _sessions=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append("start")
        await asyncio.sleep(0.01)
        events.append("finish")
        active -= 1
        return object()

    monkeypatch.setattr(runtime, "maintain_execution_cost_evidence", fake_maintenance)

    await asyncio.gather(
        runtime.execution_cost_maintenance_cycle(Settings()),
        runtime.execution_cost_maintenance_cycle(Settings()),
    )

    assert max_active == 1
    assert events == ["start", "finish", "start", "finish"]


async def test_price_cycle_serializes_fetch_and_probability_pipeline(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "_PROBABILITY_PIPELINE_LOCK", asyncio.Lock())
    first_fetch_started = asyncio.Event()
    release_first_fetch = asyncio.Event()
    events: list[str] = []
    fetch_count = 0

    async def fake_fetch(_settings: Settings, _sessions=None) -> tuple[int, int]:
        nonlocal fetch_count
        fetch_count += 1
        events.append(f"fetch-{fetch_count}")
        if fetch_count == 1:
            first_fetch_started.set()
            await release_first_fetch.wait()
        return 1, 0

    async def fake_recompute(_settings: Settings, _sessions=None) -> tuple[int, int]:
        events.append("recompute")
        return 1, 0

    async def fake_generate(_settings: Settings, _sessions=None) -> tuple[int, int]:
        events.append("signals")
        return 0, 0

    monkeypatch.setattr(runtime, "fetch_prices", fake_fetch)
    monkeypatch.setattr(runtime, "recompute_probs", fake_recompute)
    monkeypatch.setattr(runtime, "generate_signals", fake_generate)

    first = asyncio.create_task(runtime.price_cycle(Settings()))
    await first_fetch_started.wait()
    second = asyncio.create_task(runtime.price_cycle(Settings()))
    await asyncio.sleep(0)

    # The second call cannot even fetch while the first cohort is unfinished.
    assert events == ["fetch-1"]
    release_first_fetch.set()
    await asyncio.gather(first, second)

    assert events == [
        "fetch-1",
        "recompute",
        "signals",
        "fetch-2",
        "recompute",
        "signals",
    ]


async def test_startup_refresh_failure_does_not_escape(monkeypatch) -> None:
    async def fail_refresh(_settings: Settings, _sessions=None) -> None:
        raise RuntimeError("incomplete source data")

    monkeypatch.setattr(runtime, "startup_refresh", fail_refresh)

    await runtime.safe_startup_refresh(Settings())


async def test_startup_measures_capacity_before_first_price_cycle(monkeypatch) -> None:
    order: list[str] = []
    fatal_jobs: dict[str, bool] = {}

    async def record_startup_job(
        job_id: str,
        _function,
        _settings: Settings,
        _sessions,
        *,
        fatal: bool = False,
    ) -> None:
        order.append(job_id)
        fatal_jobs[job_id] = fatal

    monkeypatch.setattr(runtime, "_run_startup_job", record_startup_job)

    await runtime.startup_refresh(Settings())

    assert order[0] == "storage-capacity"
    assert order.index("storage-capacity") < order.index("prices-and-signals")
    assert order.index("storage-capacity") < order.index("execution")
    assert order.index("settlement") < order.index("resolution-reconciliation")
    assert order.index("resolution-reconciliation") < order.index(
        "station-reliability-pretrade"
    )
    assert order.index("station-reliability-pretrade") < order.index(
        "prices-and-signals"
    )
    assert order.index("station-reliability-pretrade") < order.index("execution")
    assert fatal_jobs["settlement"]
    assert fatal_jobs["resolution-reconciliation"]
    assert fatal_jobs["station-reliability-pretrade"]
    assert order.count("settlement") == 1
    assert order.count("resolution-reconciliation") == 1
    assert order.index("execution") < order.index("execution-cost-maintenance")
    assert "execution-markouts" not in order
    assert "execution-cost-finalization" not in order
