from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from itertools import pairwise

COLLECTOR_INTERVALS = {
    "forecasts": timedelta(hours=1),
    "ensemble": timedelta(hours=6),
    "observations": timedelta(minutes=20),
    "market-discovery": timedelta(minutes=30),
    "prices-and-signals": timedelta(minutes=1),
}
MAX_PLANNED_MAINTENANCE = timedelta(hours=2)


class CollectorSLOState(StrEnum):
    STARTUP_GRACE = "startup_grace"
    COLLECTING = "collecting"
    PLANNED_MAINTENANCE = "planned_maintenance"
    PASSING = "passing"
    FAILING = "failing"


@dataclass(frozen=True, slots=True)
class MaintenanceWindow:
    starts_at: datetime
    ends_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if (
            self.starts_at.tzinfo is None
            or self.starts_at.utcoffset() is None
            or self.ends_at.tzinfo is None
            or self.ends_at.utcoffset() is None
        ):
            raise ValueError("maintenance window timestamps must be timezone-aware")
        if self.ends_at <= self.starts_at:
            raise ValueError("maintenance window must have positive duration")
        if self.ends_at - self.starts_at > MAX_PLANNED_MAINTENANCE:
            raise ValueError("planned maintenance cannot exceed two hours")
        if not self.reason.strip():
            raise ValueError("maintenance reason is required")


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
    state: CollectorSLOState = CollectorSLOState.STARTUP_GRACE
    startup_grace_remaining: timedelta = timedelta(0)
    excluded_maintenance: timedelta = timedelta(0)
    maintenance_reasons: tuple[str, ...] = ()


def _maintenance_intervals(
    windows: Iterable[MaintenanceWindow],
    *,
    period_start: datetime,
    period_end: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    clipped = sorted(
        (
            max(window.starts_at, period_start),
            min(window.ends_at, period_end),
        )
        for window in windows
        if window.ends_at > period_start and window.starts_at < period_end
    )
    merged: list[tuple[datetime, datetime]] = []
    for starts_at, ends_at in clipped:
        if starts_at >= ends_at:
            continue
        if merged and starts_at <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], ends_at))
        else:
            merged.append((starts_at, ends_at))
    total_excluded = sum(
        (ends_at - starts_at for starts_at, ends_at in merged),
        start=timedelta(0),
    )
    if total_excluded > MAX_PLANNED_MAINTENANCE:
        raise ValueError("planned maintenance exclusion cannot exceed two hours per SLO window")
    return tuple(merged)


def _excluded_duration(
    starts_at: datetime,
    ends_at: datetime,
    intervals: tuple[tuple[datetime, datetime], ...],
) -> timedelta:
    return sum(
        (
            max(
                timedelta(0),
                min(ends_at, maintenance_end) - max(starts_at, maintenance_start),
            )
            for maintenance_start, maintenance_end in intervals
        ),
        start=timedelta(0),
    )


def _inside_maintenance(
    value: datetime,
    intervals: tuple[tuple[datetime, datetime], ...],
) -> bool:
    return any(starts_at <= value < ends_at for starts_at, ends_at in intervals)


def calculate_collector_uptime(
    outcomes: list[JobOutcome],
    *,
    now: datetime,
    monitoring_started: datetime | None,
    window: timedelta = timedelta(hours=48),
    intervals: dict[str, timedelta] | None = None,
    maintenance_windows: Iterable[MaintenanceWindow] = (),
    startup_grace: timedelta = timedelta(hours=6),
) -> CollectorUptime:
    intervals = intervals or COLLECTOR_INTERVALS
    if window.total_seconds() <= 0:
        raise ValueError("window must be positive")
    if startup_grace < timedelta(0):
        raise ValueError("startup_grace must not be negative")
    effective_startup_grace = min(startup_grace, window)
    if not intervals or any(interval.total_seconds() <= 0 for interval in intervals.values()):
        raise ValueError("collector intervals must be positive")
    if monitoring_started is None:
        return CollectorUptime(
            False,
            False,
            timedelta(0),
            (),
            startup_grace_remaining=effective_startup_grace,
        )

    coverage = min(max(now - monitoring_started, timedelta(0)), window)
    mature = now - monitoring_started >= window
    period_start = now - coverage
    planned_windows = tuple(maintenance_windows)
    maintenance_intervals = _maintenance_intervals(
        planned_windows,
        period_start=period_start,
        period_end=now,
    )
    excluded_maintenance = _excluded_duration(period_start, now, maintenance_intervals)
    evaluated_coverage = max(timedelta(0), coverage - excluded_maintenance)
    metrics = []
    for job_id, interval in intervals.items():
        expected = (
            max(1, int(evaluated_coverage / interval))
            if evaluated_coverage > timedelta(0)
            else 0
        )
        successful_times = sorted(
            outcome.scheduled_at
            for outcome in outcomes
            if outcome.job_id == job_id
            and outcome.status == "success"
            and period_start < outcome.scheduled_at <= now
            and not _inside_maintenance(outcome.scheduled_at, maintenance_intervals)
        )
        points = [period_start, *successful_times, now]
        max_gap = max(
            (
                later
                - earlier
                - _excluded_duration(earlier, later, maintenance_intervals)
                for earlier, later in pairwise(points)
            ),
            default=evaluated_coverage,
        )
        uptime = min(1.0, len(successful_times) / expected) if expected else 1.0
        passed = expected == 0 or (uptime >= 0.99 and max_gap <= interval * 2.5)
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
    metrics_passed = all(metric.passed for metric in metrics)
    passed = mature and metrics_passed
    if not mature:
        if coverage < effective_startup_grace:
            state = CollectorSLOState.STARTUP_GRACE
        else:
            state = (
                CollectorSLOState.COLLECTING
                if metrics_passed
                else CollectorSLOState.FAILING
            )
    elif not metrics_passed:
        state = CollectorSLOState.FAILING
    elif excluded_maintenance > timedelta(0):
        state = CollectorSLOState.PLANNED_MAINTENANCE
    else:
        state = CollectorSLOState.PASSING
    return CollectorUptime(
        mature=mature,
        passed=passed,
        coverage=coverage,
        metrics=tuple(metrics),
        state=state,
        startup_grace_remaining=(
            max(timedelta(0), effective_startup_grace - coverage)
            if state is CollectorSLOState.STARTUP_GRACE
            else timedelta(0)
        ),
        excluded_maintenance=excluded_maintenance,
        maintenance_reasons=tuple(
            sorted(
                {
                    window.reason.strip()
                    for window in planned_windows
                    if window.ends_at > period_start and window.starts_at < now
                }
            )
        ),
    )
