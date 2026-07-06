from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import pairwise

COLLECTOR_INTERVALS = {
    "forecasts": timedelta(hours=1),
    "ensemble": timedelta(hours=6),
    "observations": timedelta(minutes=20),
    "market-discovery": timedelta(minutes=30),
    "prices-and-signals": timedelta(minutes=1),
}


@dataclass(frozen=True, slots=True)
class JobOutcome:
    job_id: str
    scheduled_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class CollectorMetric:
    job_id: str
    expected: int
    successful: int
    uptime: float
    max_gap: timedelta
    passed: bool


@dataclass(frozen=True, slots=True)
class CollectorUptime:
    mature: bool
    passed: bool
    coverage: timedelta
    metrics: tuple[CollectorMetric, ...]


def calculate_collector_uptime(
    outcomes: list[JobOutcome],
    *,
    now: datetime,
    monitoring_started: datetime | None,
    window: timedelta = timedelta(hours=48),
    intervals: dict[str, timedelta] | None = None,
) -> CollectorUptime:
    intervals = intervals or COLLECTOR_INTERVALS
    if window.total_seconds() <= 0:
        raise ValueError("window must be positive")
    if not intervals or any(interval.total_seconds() <= 0 for interval in intervals.values()):
        raise ValueError("collector intervals must be positive")
    if monitoring_started is None:
        return CollectorUptime(False, False, timedelta(0), ())

    coverage = min(max(now - monitoring_started, timedelta(0)), window)
    mature = now - monitoring_started >= window
    period_start = now - coverage
    metrics = []
    for job_id, interval in intervals.items():
        expected = max(1, int(coverage / interval))
        successful_times = sorted(
            outcome.scheduled_at
            for outcome in outcomes
            if outcome.job_id == job_id
            and outcome.status == "success"
            and period_start < outcome.scheduled_at <= now
        )
        points = [period_start, *successful_times, now]
        max_gap = max(
            (later - earlier for earlier, later in pairwise(points)),
            default=coverage,
        )
        uptime = min(1.0, len(successful_times) / expected)
        passed = uptime >= 0.99 and max_gap <= interval * 2.5
        metrics.append(
            CollectorMetric(
                job_id=job_id,
                expected=expected,
                successful=len(successful_times),
                uptime=uptime,
                max_gap=max_gap,
                passed=passed,
            )
        )
    return CollectorUptime(
        mature=mature,
        passed=mature and all(metric.passed for metric in metrics),
        coverage=coverage,
        metrics=tuple(metrics),
    )
