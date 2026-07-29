from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    EvidenceContinuityLane as EvidenceContinuityLaneRecord,
)
from stratus.db.models import (
    ProbabilityPipelineReleaseEpoch,
)
from stratus.repositories.evidence_continuity import (
    current_evidence_continuity_lanes,
    list_probability_pipeline_release_epochs,
)
from stratus.services.evidence_continuity import (
    EvidenceBottleneck,
    EvidenceDueCounts,
    EvidenceLiveness,
    EvidenceVelocity,
    calculate_evidence_velocity,
    classify_evidence_bottleneck,
    classify_evidence_liveness,
    full_utc_collection_days,
)
from stratus.services.uptime import MaintenanceWindow

_DAY_GRACE = timedelta(minutes=30)
_DEFAULT_EXECUTION_DUE_AFTER = timedelta(minutes=30)
_CURRENT_SCHEDULER_LOOKBACK = timedelta(hours=1)
_REQUIRED_SCHEDULER_INTERVALS = {
    "execution": timedelta(minutes=1),
    "execution-cost-maintenance": timedelta(seconds=15),
    "settlement": timedelta(minutes=10),
}
_PROSPECTIVE_EVIDENCE_LANES = frozenset(
    {
        "predictive_validation",
        "selection_policy",
        "execution_evidence",
        "station_lifecycle",
        "official_resolution_truth",
    }
)


@dataclass(frozen=True, slots=True)
class EvidenceSkipReason:
    reason: str
    count: int


@dataclass(frozen=True, slots=True)
class EvidenceContinuityLane:
    cohort: str
    epoch_id: int | None
    scope: str
    opened_at: datetime | None
    probability_rows: int
    materialized_events: int
    coherent_events: int
    execution_intents: int
    filled_orders: int
    no_fill_orders: int
    open_orders: int
    cost_seals: int
    eligible_cost_seals: int
    required_marks: int
    observed_marks: int
    enrollments: int
    eligible_enrollments: int
    outcomes: int
    independent_blocks: int
    first_evidence_at: datetime | None
    last_evidence_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvidenceCollectionWindow:
    started_at: datetime | None
    ended_at: datetime | None
    latest_day_started_at: datetime | None
    latest_day_ended_at: datetime | None
    latest_day_grace_until: datetime | None
    latest_complete_day_ended_at: datetime | None
    grace_seconds: int
    candidate_full_days: int
    evaluated_days: int
    maintenance_excluded_days: int
    complete_days: int
    waiting_for_due_days: int
    due_pending_days: int
    collecting_on_schedule_days: int
    overdue_days: int
    stalled_days: int
    required_scheduler_jobs: tuple[str, ...]
    missing_scheduler_jobs: tuple[str, ...]
    due_counts: EvidenceDueCounts


@dataclass(frozen=True, slots=True)
class EvidenceContinuitySummary:
    generated_at: datetime
    state: str
    candidate_cohort: str
    candidate_image_digest: str
    active_cohort: str | None
    cohort_match: bool
    image_match: bool
    active_epoch: ProbabilityPipelineReleaseEpoch | None
    continuity_lanes: tuple[EvidenceContinuityLaneRecord, ...]
    epoch_count: int
    cohort_changes_30d: int
    bottleneck: EvidenceBottleneck
    liveness: EvidenceLiveness
    collection_window: EvidenceCollectionWindow
    velocity: EvidenceVelocity
    paper_stations: int
    shadow_stations: int
    open_positions: int
    open_exposure_usd: float
    bankroll_usd: float
    signal_rows_since_epoch: int
    pending_signals: int
    top_skip_reasons: tuple[EvidenceSkipReason, ...]
    lanes: tuple[EvidenceContinuityLane, ...]


def _integer(value: object) -> int:
    return cast(int, value or 0)


@dataclass(frozen=True, slots=True)
class _CollectionDay:
    started_at: datetime
    ended_at: datetime
    grace_until: datetime
    liveness: EvidenceLiveness
    due_counts: EvidenceDueCounts
    outcomes: int
    missing_scheduler_jobs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _SchedulerCoverageWindow:
    started_at: datetime
    ended_at: datetime
    current: bool
    missing_scheduler_jobs: tuple[str, ...]


def _merge_due_counts(rows: Iterable[EvidenceDueCounts]) -> EvidenceDueCounts:
    values = [row.values() for row in rows]
    if not values:
        return EvidenceDueCounts()
    totals = tuple(sum(items) for items in zip(*values, strict=True))
    return EvidenceDueCounts(*totals)


def _utc_day(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("evidence due timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _prospective_evidence_opened_at(
    lanes: Iterable[EvidenceContinuityLaneRecord],
) -> datetime | None:
    """Return the newest boundary shared by the cross-lane evidence sample.

    A report that combines probabilities, selection, station authority,
    execution, and official outcomes cannot retain rows from before any one of
    those lanes forked.  Using only the predictive boundary relabelled old
    execution attempts as current whenever predictive validation continued.
    Missing lane state is fail-closed: the release gate reports the underlying
    incompleteness and the collection window remains unopened.
    """

    boundaries = {
        lane.lane_name: lane.boundary_at
        for lane in lanes
        if lane.lane_name in _PROSPECTIVE_EVIDENCE_LANES
    }
    if set(boundaries) != _PROSPECTIVE_EVIDENCE_LANES:
        return None
    return max(boundaries.values())


def _operational_collection_opened_at(
    lanes: Iterable[EvidenceContinuityLaneRecord],
) -> datetime | None:
    """Return the boundary for scheduler liveness and collection ETA only.

    Formal evidence denominators remain anchored to the five prospective
    lanes.  A fork of ``operational_legacy`` may reset forward-looking
    scheduler coverage, but must not relabel or discard formal evidence.
    Missing either side is fail-closed and leaves the collection window
    unopened.
    """

    materialized_lanes = tuple(lanes)
    prospective_opened_at = _prospective_evidence_opened_at(materialized_lanes)
    operational_opened_at = next(
        (
            lane.boundary_at
            for lane in materialized_lanes
            if lane.lane_name == "operational_legacy"
        ),
        None,
    )
    if prospective_opened_at is None or operational_opened_at is None:
        return None
    return max(prospective_opened_at, operational_opened_at)


async def _due_counts_by_day(
    session: AsyncSession,
    *,
    cohort: str,
    evidence_opened_at: datetime,
    started_at: datetime,
    ended_at: datetime,
    now: datetime,
    execution_due_after: timedelta,
) -> tuple[dict[datetime, EvidenceDueCounts], dict[datetime, int]]:
    """Classify obligations by their natural due time in a full UTC day."""

    pending_after = now - _DAY_GRACE
    validation_rows = (
        await session.execute(
            text(
                """
                WITH selected AS (
                    SELECT m.resolves_at AS due_at, o.id AS outcome_id
                    FROM prospective_validation_enrollments e
                    JOIN markets m ON m.id = e.market_id
                    LEFT JOIN prospective_validation_outcomes o
                      ON o.enrollment_id = e.id
                    WHERE e.pipeline_cohort = :cohort
                      AND e.eligible
                      AND e.enrolled_at >= :evidence_opened_at
                      AND m.resolves_at >= :started_at
                      AND m.resolves_at < :ended_at
                )
                SELECT date_trunc('day', due_at AT TIME ZONE 'UTC')
                           AT TIME ZONE 'UTC' AS day,
                       count(*)::bigint AS total,
                       count(*) FILTER (WHERE outcome_id IS NOT NULL)::bigint AS classified,
                       count(*) FILTER (
                           WHERE outcome_id IS NULL AND due_at > :now
                       )::bigint AS waiting,
                       count(*) FILTER (
                           WHERE outcome_id IS NULL
                             AND due_at <= :now AND due_at > :pending_after
                       )::bigint AS pending,
                       count(*) FILTER (
                           WHERE outcome_id IS NULL AND due_at <= :pending_after
                       )::bigint AS overdue,
                       count(outcome_id)::bigint AS outcomes
                FROM selected
                GROUP BY date_trunc('day', due_at AT TIME ZONE 'UTC')
                             AT TIME ZONE 'UTC'
                """
            ),
            {
                "cohort": cohort,
                "evidence_opened_at": evidence_opened_at,
                "started_at": started_at,
                "ended_at": ended_at,
                "now": now,
                "pending_after": pending_after,
            },
        )
    ).all()
    execution_rows = (
        await session.execute(
            text(
                """
                WITH projected AS (
                    SELECT i.intent_at,
                           obligation.state,
                           coalesce(
                               final_mark.due_at,
                               obligation.created_at,
                               i.intent_at
                                   + CAST(:execution_due_seconds AS integer)
                                     * interval '1 second'
                           ) AS due_at
                    FROM execution_order_intents i
                    LEFT JOIN execution_cost_obligations obligation
                      ON obligation.intent_id = i.id
                    LEFT JOIN LATERAL (
                        SELECT max(
                            leg.fill_effective_at
                                + (horizon.seconds + runtime.mark_grace_seconds)
                                  * interval '1 second'
                        ) AS due_at
                        FROM execution_fill_legs leg
                        CROSS JOIN execution_cost_runtime runtime
                        CROSS JOIN unnest(runtime.mark_horizons_seconds)
                                   AS horizon(seconds)
                        WHERE runtime.id = 1
                          AND leg.intent_id = i.id
                          AND leg.fill_effective_at IS NOT NULL
                    ) final_mark ON true
                    WHERE i.pipeline_cohort = :cohort
                      AND i.intent_at >= :evidence_opened_at
                ), selected AS (
                    SELECT *,
                           date_trunc('day', due_at AT TIME ZONE 'UTC')
                               AT TIME ZONE 'UTC' AS due_day
                    FROM projected
                    WHERE due_at >= :started_at AND due_at < :ended_at
                )
                SELECT due_day AS day,
                       count(*)::bigint AS total,
                       count(*) FILTER (WHERE state = 'sealed')::bigint AS classified,
                       count(*) FILTER (
                           WHERE state IS DISTINCT FROM 'sealed' AND due_at > :now
                       )::bigint AS waiting,
                       count(*) FILTER (
                           WHERE state IS DISTINCT FROM 'sealed'
                             AND due_at <= :now AND due_at > :pending_after
                       )::bigint AS pending,
                       count(*) FILTER (
                           WHERE state IS DISTINCT FROM 'sealed'
                             AND due_at <= :pending_after
                       )::bigint AS overdue
                FROM selected
                GROUP BY due_day
                """
            ),
            {
                "cohort": cohort,
                "evidence_opened_at": evidence_opened_at,
                "started_at": started_at,
                "ended_at": ended_at,
                "now": now,
                "pending_after": pending_after,
                "execution_due_seconds": int(execution_due_after.total_seconds()),
            },
        )
    ).all()
    mark_rows = (
        await session.execute(
            text(
                """
                WITH projected AS (
                    SELECT i.intent_at,
                           leg.id AS fill_leg_id,
                           horizon.seconds AS horizon_seconds,
                           leg.fill_effective_at
                               + (horizon.seconds + runtime.mark_grace_seconds)
                                 * interval '1 second' AS due_at
                    FROM execution_order_intents i
                    JOIN execution_fill_legs leg ON leg.intent_id = i.id
                    CROSS JOIN execution_cost_runtime runtime
                    CROSS JOIN unnest(runtime.mark_horizons_seconds)
                               AS horizon(seconds)
                    WHERE runtime.id = 1
                      AND i.pipeline_cohort = :cohort
                      AND i.intent_at >= :evidence_opened_at
                      AND leg.fill_effective_at IS NOT NULL
                ), expected AS (
                    SELECT *,
                           date_trunc('day', due_at AT TIME ZONE 'UTC')
                               AT TIME ZONE 'UTC' AS due_day
                    FROM projected
                    WHERE due_at >= :started_at AND due_at < :ended_at
                )
                SELECT expected.due_day AS day,
                       count(*)::bigint AS total,
                       count(mark.id)::bigint AS classified,
                       count(*) FILTER (
                           WHERE mark.id IS NULL AND expected.due_at > :now
                       )::bigint AS waiting,
                       count(*) FILTER (
                           WHERE mark.id IS NULL
                             AND expected.due_at <= :now
                             AND expected.due_at > :pending_after
                       )::bigint AS pending,
                       count(*) FILTER (
                           WHERE mark.id IS NULL
                             AND expected.due_at <= :pending_after
                       )::bigint AS overdue
                FROM expected
                LEFT JOIN execution_postfill_marks mark
                  ON mark.fill_leg_id = expected.fill_leg_id
                 AND mark.horizon_seconds = expected.horizon_seconds
                GROUP BY expected.due_day
                """
            ),
            {
                "cohort": cohort,
                "evidence_opened_at": evidence_opened_at,
                "started_at": started_at,
                "ended_at": ended_at,
                "now": now,
                "pending_after": pending_after,
            },
        )
    ).all()

    components: dict[datetime, dict[str, tuple[int, int, int, int]]] = {}
    outcomes: dict[datetime, int] = {}
    for day, _total, classified, waiting, pending, overdue, outcome_count in validation_rows:
        key = _utc_day(day)
        components.setdefault(key, {})["validation"] = (
            _integer(waiting),
            _integer(pending),
            _integer(overdue),
            _integer(classified),
        )
        outcomes[key] = _integer(outcome_count)
    for day, _total, classified, waiting, pending, overdue in execution_rows:
        components.setdefault(_utc_day(day), {})["execution"] = (
            _integer(waiting),
            _integer(pending),
            _integer(overdue),
            _integer(classified),
        )
    for day, _total, classified, waiting, pending, overdue in mark_rows:
        components.setdefault(_utc_day(day), {})["mark"] = (
            _integer(waiting),
            _integer(pending),
            _integer(overdue),
            _integer(classified),
        )
    result: dict[datetime, EvidenceDueCounts] = {}
    empty = (0, 0, 0, 0)
    for day, categories in components.items():
        validation = categories.get("validation", empty)
        execution = categories.get("execution", empty)
        mark = categories.get("mark", empty)
        result[day] = EvidenceDueCounts(*validation, *execution, *mark)
    return result, outcomes


async def _scheduler_coverage_windows(
    session: AsyncSession,
    *,
    historical_started_at: datetime | None,
    historical_ended_at: datetime | None,
    current_started_at: datetime | None,
    now: datetime,
) -> tuple[_SchedulerCoverageWindow, ...]:
    """Aggregate scheduler coverage in PostgreSQL without materializing every run.

    Closed UTC days retain the preregistered 99% uptime and 2.5x maximum-gap
    requirements.  The bounded current window is freshness-only until it has a
    full hour of coverage: a missing/max-instances composite is therefore
    visible after 2.5 cadences rather than remaining invisible until tomorrow.
    """

    required_values = ", ".join(
        f"('{job_id}', {int(interval.total_seconds())})"
        for job_id, interval in _REQUIRED_SCHEDULER_INTERVALS.items()
    )
    rows = (
        await session.execute(
            text(
                f"""
                WITH required(job_id, interval_seconds) AS (
                    VALUES {required_values}
                ), windows AS (
                    SELECT tick AS started_at,
                           tick + interval '1 day' AS ended_at,
                           false AS is_current
                    FROM generate_series(
                        CAST(:historical_started_at AS timestamptz),
                        CAST(:historical_ended_at AS timestamptz) - interval '1 day',
                        interval '1 day'
                    ) AS tick
                    WHERE CAST(:historical_started_at AS timestamptz)
                              < CAST(:historical_ended_at AS timestamptz)

                    UNION ALL

                    SELECT CAST(:current_started_at AS timestamptz),
                           CAST(:now AS timestamptz),
                           true
                    WHERE CAST(:current_started_at AS timestamptz) IS NOT NULL
                      AND CAST(:current_started_at AS timestamptz)
                              < CAST(:now AS timestamptz)
                ), successful AS (
                    SELECT coverage_window.started_at,
                           coverage_window.ended_at,
                           coverage_window.is_current,
                           required.job_id,
                           required.interval_seconds,
                           run.scheduled_at,
                           lag(run.scheduled_at) OVER (
                               PARTITION BY coverage_window.started_at,
                                            coverage_window.ended_at,
                                            required.job_id
                               ORDER BY run.scheduled_at
                           ) AS previous_at
                    FROM windows coverage_window
                    CROSS JOIN required
                    JOIN job_runs run
                      ON run.job_id = required.job_id
                     AND run.status = 'success'
                     AND run.scheduled_at > coverage_window.started_at
                     AND run.scheduled_at <= coverage_window.ended_at
                ), successful_stats AS (
                    SELECT started_at,
                           ended_at,
                           is_current,
                           job_id,
                           interval_seconds,
                           count(*)::bigint AS successful,
                           min(scheduled_at) AS first_success_at,
                           max(scheduled_at) AS last_success_at,
                           coalesce(
                               max(
                                   extract(
                                       epoch FROM scheduled_at - previous_at
                                   )
                               ) FILTER (WHERE previous_at IS NOT NULL),
                               0
                           ) AS max_internal_gap_seconds
                    FROM successful
                    GROUP BY started_at,
                             ended_at,
                             is_current,
                             job_id,
                             interval_seconds
                ), metrics AS (
                    SELECT coverage_window.started_at,
                           coverage_window.ended_at,
                           coverage_window.is_current,
                           required.job_id,
                           required.interval_seconds,
                           coalesce(stats.successful, 0)::bigint AS successful,
                           greatest(
                               1,
                               floor(
                                   extract(
                                       epoch FROM
                                           coverage_window.ended_at
                                               - coverage_window.started_at
                                   ) / required.interval_seconds
                               )
                           )::bigint AS expected,
                           CASE
                               WHEN coalesce(stats.successful, 0) = 0
                               THEN extract(
                                   epoch FROM
                                       coverage_window.ended_at
                                           - coverage_window.started_at
                               )
                               ELSE greatest(
                                   extract(
                                       epoch FROM
                                           stats.first_success_at
                                               - coverage_window.started_at
                                   ),
                                   extract(
                                       epoch FROM
                                           coverage_window.ended_at
                                               - stats.last_success_at
                                   ),
                                   stats.max_internal_gap_seconds
                               )
                           END AS max_gap_seconds
                    FROM windows coverage_window
                    CROSS JOIN required
                    LEFT JOIN successful_stats stats
                      ON stats.started_at = coverage_window.started_at
                     AND stats.ended_at = coverage_window.ended_at
                     AND stats.is_current = coverage_window.is_current
                     AND stats.job_id = required.job_id
                ), classified AS (
                    SELECT *,
                           CASE
                               WHEN is_current
                                AND extract(
                                    epoch FROM ended_at - started_at
                                ) < interval_seconds * 2.5
                               THEN true
                               WHEN max_gap_seconds > interval_seconds * 2.5
                               THEN false
                               WHEN is_current
                               THEN true
                               ELSE successful * 100 >= expected * 99
                           END AS passed
                    FROM metrics
                )
                SELECT started_at,
                       ended_at,
                       is_current,
                       coalesce(
                           array_agg(job_id ORDER BY job_id)
                               FILTER (WHERE NOT passed),
                           ARRAY[]::text[]
                       ) AS missing_scheduler_jobs
                FROM classified
                GROUP BY started_at, ended_at, is_current
                ORDER BY started_at, is_current
                """
            ),
            {
                "historical_started_at": historical_started_at,
                "historical_ended_at": historical_ended_at,
                "current_started_at": current_started_at,
                "now": now,
            },
        )
    ).all()
    return tuple(
        _SchedulerCoverageWindow(
            started_at=_utc_day(started_at),
            ended_at=_utc_day(ended_at),
            current=bool(current),
            missing_scheduler_jobs=tuple(str(job_id) for job_id in missing_jobs),
        )
        for started_at, ended_at, current, missing_jobs in rows
    )


def _current_scheduler_started_at(
    *,
    opened_at: datetime,
    now: datetime,
    maintenance_windows: tuple[MaintenanceWindow, ...],
) -> datetime | None:
    current_day = _utc_day(now).replace(hour=0, minute=0, second=0, microsecond=0)
    started_at = max(
        _utc_day(opened_at),
        current_day,
        _utc_day(now) - _CURRENT_SCHEDULER_LOOKBACK,
    )
    for window in maintenance_windows:
        if window.starts_at <= now < window.ends_at:
            return None
        if started_at < window.ends_at <= now:
            started_at = window.ends_at
    return started_at if started_at < now else None


def _overlaps_maintenance(
    started_at: datetime,
    ended_at: datetime,
    maintenance_windows: tuple[MaintenanceWindow, ...],
) -> bool:
    return any(
        window.starts_at < ended_at and window.ends_at > started_at
        for window in maintenance_windows
    )


def _summary_liveness(days: tuple[_CollectionDay, ...]) -> EvidenceLiveness:
    if not days:
        return EvidenceLiveness.WAITING_FOR_DUE
    states = {day.liveness for day in days}
    for state in (
        EvidenceLiveness.OVERDUE,
        EvidenceLiveness.STALLED,
        EvidenceLiveness.DUE_PENDING,
        EvidenceLiveness.COLLECTING_ON_SCHEDULE,
        EvidenceLiveness.WAITING_FOR_DUE,
    ):
        if state in states:
            return state
    return EvidenceLiveness.COMPLETE


async def _collection_window(
    session: AsyncSession,
    *,
    cohort: str,
    opened_at: datetime | None,
    now: datetime,
    maintenance_windows: tuple[MaintenanceWindow, ...],
    execution_due_after: timedelta,
) -> tuple[EvidenceCollectionWindow, EvidenceLiveness, int]:
    candidates = full_utc_collection_days(opened_at=opened_at, now=now)
    if opened_at is None:
        empty = EvidenceCollectionWindow(
            started_at=None,
            ended_at=None,
            latest_day_started_at=None,
            latest_day_ended_at=None,
            latest_day_grace_until=None,
            latest_complete_day_ended_at=None,
            grace_seconds=int(_DAY_GRACE.total_seconds()),
            candidate_full_days=0,
            evaluated_days=0,
            maintenance_excluded_days=0,
            complete_days=0,
            waiting_for_due_days=0,
            due_pending_days=0,
            collecting_on_schedule_days=0,
            overdue_days=0,
            stalled_days=0,
            required_scheduler_jobs=tuple(_REQUIRED_SCHEDULER_INTERVALS),
            missing_scheduler_jobs=(),
            due_counts=EvidenceDueCounts(),
        )
        return empty, EvidenceLiveness.WAITING_FOR_DUE, 0

    current_day_started_at = _utc_day(now).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    current_day_ended_at = current_day_started_at + timedelta(days=1)
    started_at = candidates[0][0] if candidates else None
    ended_at = candidates[-1][1] if candidates else None
    due_started_at = started_at or current_day_started_at
    due_by_day, outcomes_by_day = await _due_counts_by_day(
        session,
        cohort=cohort,
        evidence_opened_at=opened_at,
        started_at=due_started_at,
        ended_at=current_day_ended_at,
        now=now,
        execution_due_after=execution_due_after,
    )
    current_scheduler_started_at = _current_scheduler_started_at(
        opened_at=opened_at,
        now=now,
        maintenance_windows=maintenance_windows,
    )
    scheduler_windows = await _scheduler_coverage_windows(
        session,
        historical_started_at=started_at,
        historical_ended_at=ended_at,
        current_started_at=current_scheduler_started_at,
        now=now,
    )
    historical_scheduler = {
        window.started_at: window for window in scheduler_windows if not window.current
    }
    current_scheduler = next(
        (window for window in scheduler_windows if window.current),
        None,
    )
    current_missing_jobs = (
        current_scheduler.missing_scheduler_jobs
        if current_scheduler is not None
        else ()
    )
    excluded = 0
    assessments: list[_CollectionDay] = []
    for day_start, day_end in candidates:
        if _overlaps_maintenance(day_start, day_end, maintenance_windows):
            excluded += 1
            continue
        scheduler = historical_scheduler.get(day_start)
        missing_jobs = (
            scheduler.missing_scheduler_jobs
            if scheduler is not None
            else tuple(_REQUIRED_SCHEDULER_INTERVALS)
        )
        due_counts = due_by_day.get(day_start, EvidenceDueCounts())
        liveness = classify_evidence_liveness(
            grace_elapsed=now >= day_end + _DAY_GRACE,
            scheduler_covered=not missing_jobs,
            due_counts=due_counts,
        )
        assessments.append(
            _CollectionDay(
                started_at=day_start,
                ended_at=day_end,
                grace_until=day_end + _DAY_GRACE,
                liveness=liveness,
                due_counts=due_counts,
                outcomes=outcomes_by_day.get(day_start, 0),
                missing_scheduler_jobs=missing_jobs,
            )
        )

    days = tuple(assessments)
    complete = tuple(
        day for day in days if day.liveness is EvidenceLiveness.COMPLETE
    )
    latest = days[-1] if days else None
    state_counts = {
        state: sum(day.liveness is state for day in days) for state in EvidenceLiveness
    }
    current_due_counts = due_by_day.get(current_day_started_at, EvidenceDueCounts())
    aggregate_due = _merge_due_counts(
        (*tuple(day.due_counts for day in days), current_due_counts)
    )
    current_liveness = classify_evidence_liveness(
        grace_elapsed=False,
        scheduler_covered=not current_missing_jobs,
        due_counts=current_due_counts,
    )
    if current_missing_jobs and current_liveness is not EvidenceLiveness.OVERDUE:
        current_liveness = EvidenceLiveness.STALLED
    liveness_days = days
    if current_due_counts.total or current_missing_jobs or not days:
        liveness_days = (
            *days,
            _CollectionDay(
                started_at=current_day_started_at,
                ended_at=current_day_ended_at,
                grace_until=current_day_ended_at + _DAY_GRACE,
                liveness=current_liveness,
                due_counts=current_due_counts,
                outcomes=outcomes_by_day.get(current_day_started_at, 0),
                missing_scheduler_jobs=current_missing_jobs,
            ),
        )
    all_missing_jobs = {
        job_id for day in days for job_id in day.missing_scheduler_jobs
    }
    all_missing_jobs.update(current_missing_jobs)
    window = EvidenceCollectionWindow(
        started_at=started_at,
        ended_at=ended_at,
        latest_day_started_at=latest.started_at if latest is not None else None,
        latest_day_ended_at=latest.ended_at if latest is not None else None,
        latest_day_grace_until=latest.grace_until if latest is not None else None,
        latest_complete_day_ended_at=(
            max(day.ended_at for day in complete) if complete else None
        ),
        grace_seconds=int(_DAY_GRACE.total_seconds()),
        candidate_full_days=len(candidates),
        evaluated_days=len(days),
        maintenance_excluded_days=excluded,
        complete_days=state_counts[EvidenceLiveness.COMPLETE],
        waiting_for_due_days=state_counts[EvidenceLiveness.WAITING_FOR_DUE],
        due_pending_days=state_counts[EvidenceLiveness.DUE_PENDING],
        collecting_on_schedule_days=state_counts[
            EvidenceLiveness.COLLECTING_ON_SCHEDULE
        ],
        overdue_days=state_counts[EvidenceLiveness.OVERDUE],
        stalled_days=state_counts[EvidenceLiveness.STALLED],
        required_scheduler_jobs=tuple(_REQUIRED_SCHEDULER_INTERVALS),
        missing_scheduler_jobs=tuple(sorted(all_missing_jobs)),
        due_counts=aggregate_due,
    )
    outcomes_in_complete_days = sum(day.outcomes for day in complete)
    return window, _summary_liveness(liveness_days), outcomes_in_complete_days


async def _probability_counts(
    session: AsyncSession,
    *,
    active_cohort: str | None,
    active_opened_at: datetime | None,
) -> dict[str, tuple[int, int, int]]:
    rows = (
        await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT latest_probability.components,
                           m.event_id
                    FROM markets m
                    CROSS JOIN LATERAL (
                        SELECT mp.components
                        FROM model_probs mp
                        WHERE mp.market_id = m.id
                          AND (
                              CAST(:active_cohort AS text) IS NULL
                              OR mp.components->'pipeline'->>'cohort'
                                 <> CAST(:active_cohort AS text)
                              OR mp.ts >= CAST(:active_opened_at AS timestamptz)
                          )
                        ORDER BY mp.ts DESC
                        LIMIT 1
                    ) latest_probability
                    WHERE m.status = 'active'::market_status
                )
                SELECT components->'pipeline'->>'cohort' AS cohort,
                       count(*)::bigint AS probability_rows,
                       count(DISTINCT event_id) FILTER (
                           WHERE components->'event_coherence'->>'materialized' = 'true'
                       )::bigint AS materialized_events,
                       count(DISTINCT event_id) FILTER (
                           WHERE components->'event_coherence'->>'passed' = 'true'
                       )::bigint AS coherent_events
                FROM latest
                WHERE components->'pipeline'->>'cohort' IS NOT NULL
                GROUP BY components->'pipeline'->>'cohort'
                """
            ),
            {
                "active_cohort": active_cohort,
                "active_opened_at": active_opened_at,
            },
        )
    ).all()
    return {
        str(cohort): (_integer(probabilities), _integer(materialized), _integer(coherent))
        for cohort, probabilities, materialized, coherent in rows
    }


async def _evidence_cohorts(session: AsyncSession) -> tuple[str, ...]:
    rows = (
        await session.execute(
            text(
                """
                SELECT pipeline_cohort AS cohort FROM execution_order_intents
                UNION
                SELECT pipeline_cohort AS cohort
                FROM prospective_validation_enrollments
                WHERE pipeline_cohort IS NOT NULL
                """
            )
        )
    ).scalars()
    return tuple(sorted(str(value) for value in rows if value))


async def _lane_counts(
    session: AsyncSession,
    *,
    cohort: str,
    epoch: ProbabilityPipelineReleaseEpoch | None,
    evidence_opened_at: datetime | None,
    probability_counts: dict[str, tuple[int, int, int]],
) -> EvidenceContinuityLane:
    execution = (
        await session.execute(
            text(
                """
                SELECT count(*)::bigint AS intents,
                       count(*) FILTER (WHERE EXISTS (
                           SELECT 1 FROM execution_fill_legs fl
                           WHERE fl.intent_id = i.id
                       ))::bigint AS fills,
                       count(*) FILTER (
                           WHERE o.status IN ('canceled', 'unfilled_maker', 'submit_failed',
                                              'rejected', 'expired')
                             AND NOT EXISTS (
                                 SELECT 1 FROM execution_fill_legs fl
                                 WHERE fl.intent_id = i.id
                             )
                       )::bigint AS no_fills,
                       count(*) FILTER (
                           WHERE o.status IN ('paper_resting', 'paper_pending', 'submitting',
                                              'open', 'submit_unconfirmed',
                                              'cancel_requested')
                       )::bigint AS open_orders,
                       count(cs.id)::bigint AS seals,
                       count(cs.id) FILTER (WHERE cs.eligible)::bigint AS eligible_seals,
                       min(i.intent_at) AS first_at,
                       max(greatest(i.intent_at, coalesce(cs.sealed_at, i.intent_at))) AS last_at
                FROM execution_order_intents i
                JOIN orders o ON o.id = i.order_id
                LEFT JOIN execution_cost_seals cs ON cs.intent_id = i.id
                WHERE i.pipeline_cohort = :cohort
                  AND (CAST(:opened_at AS timestamptz) IS NULL
                       OR i.intent_at >= CAST(:opened_at AS timestamptz))
                """
            ),
            {"cohort": cohort, "opened_at": evidence_opened_at},
        )
    ).one()
    marks = (
        await session.execute(
            text(
                """
                SELECT count(pm.id)::bigint AS required_marks,
                       count(pm.id) FILTER (WHERE pm.status = 'observed')::bigint AS observed_marks
                FROM execution_order_intents i
                JOIN execution_fill_legs fl ON fl.intent_id = i.id
                JOIN execution_postfill_marks pm ON pm.fill_leg_id = fl.id
                WHERE i.pipeline_cohort = :cohort
                  AND (CAST(:opened_at AS timestamptz) IS NULL
                       OR i.intent_at >= CAST(:opened_at AS timestamptz))
                """
            ),
            {"cohort": cohort, "opened_at": evidence_opened_at},
        )
    ).one()
    validation = (
        await session.execute(
            text(
                """
                WITH selected AS (
                    SELECT e.*, o.id AS outcome_id, o.recognized_at
                    FROM prospective_validation_enrollments e
                    LEFT JOIN prospective_validation_outcomes o ON o.enrollment_id = e.id
                    WHERE e.pipeline_cohort = :cohort
                      AND (CAST(:opened_at AS timestamptz) IS NULL
                           OR e.enrolled_at >= CAST(:opened_at AS timestamptz))
                ), complete_blocks AS (
                    SELECT station_icao, market_date, kind
                    FROM selected
                    WHERE eligible
                    GROUP BY station_icao, market_date, kind
                    HAVING bool_and(outcome_id IS NOT NULL)
                )
                SELECT count(*)::bigint AS enrollments,
                       count(*) FILTER (WHERE eligible)::bigint AS eligible_enrollments,
                       count(outcome_id)::bigint AS outcomes,
                       (SELECT count(*) FROM complete_blocks)::bigint AS independent_blocks,
                       min(enrolled_at) AS first_at,
                       max(greatest(enrolled_at, coalesce(recognized_at, enrolled_at))) AS last_at
                FROM selected
                """
            ),
            {"cohort": cohort, "opened_at": evidence_opened_at},
        )
    ).one()
    first_candidates = [
        value for value in (execution.first_at, validation.first_at) if value is not None
    ]
    last_candidates = [
        value for value in (execution.last_at, validation.last_at) if value is not None
    ]
    probabilities, materialized, coherent = probability_counts.get(cohort, (0, 0, 0))
    return EvidenceContinuityLane(
        cohort=cohort,
        epoch_id=epoch.id if epoch is not None else None,
        scope="current" if epoch is not None else "audit_only",
        opened_at=evidence_opened_at,
        probability_rows=probabilities,
        materialized_events=materialized,
        coherent_events=coherent,
        execution_intents=_integer(execution.intents),
        filled_orders=_integer(execution.fills),
        no_fill_orders=_integer(execution.no_fills),
        open_orders=_integer(execution.open_orders),
        cost_seals=_integer(execution.seals),
        eligible_cost_seals=_integer(execution.eligible_seals),
        required_marks=_integer(marks.required_marks),
        observed_marks=_integer(marks.observed_marks),
        enrollments=_integer(validation.enrollments),
        eligible_enrollments=_integer(validation.eligible_enrollments),
        outcomes=_integer(validation.outcomes),
        independent_blocks=_integer(validation.independent_blocks),
        first_evidence_at=min(first_candidates) if first_candidates else None,
        last_evidence_at=max(last_candidates) if last_candidates else None,
    )


async def evidence_continuity_summary(
    session: AsyncSession,
    *,
    candidate_cohort: str,
    candidate_image_digest: str,
    now: datetime,
    maintenance_windows: Iterable[MaintenanceWindow] = (),
    execution_due_after: timedelta = _DEFAULT_EXECUTION_DUE_AFTER,
) -> EvidenceContinuitySummary:
    """Build parallel, non-convertible evidence lanes for release cohorts."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("evidence continuity timestamp must be timezone-aware")
    if execution_due_after <= timedelta(0):
        raise ValueError("execution due interval must be positive")
    planned_maintenance = tuple(maintenance_windows)

    epochs = await list_probability_pipeline_release_epochs(session, limit=1_000)
    active_epoch = epochs[0] if epochs else None
    active_cohort = active_epoch.cohort if active_epoch is not None else None
    continuity_lanes = await current_evidence_continuity_lanes(session)
    active_formal_evidence_opened_at = _prospective_evidence_opened_at(
        continuity_lanes
    )
    active_collection_opened_at = _operational_collection_opened_at(continuity_lanes)
    cohort_match = active_cohort == candidate_cohort
    image_match = (
        active_epoch is not None and active_epoch.image_digest == candidate_image_digest
    )
    probability_counts = await _probability_counts(
        session,
        active_cohort=active_cohort,
        active_opened_at=active_formal_evidence_opened_at,
    )
    cohort_values = set(await _evidence_cohorts(session))
    cohort_values.add(candidate_cohort)
    if active_cohort is not None:
        cohort_values.add(active_cohort)
    lanes = [
        await _lane_counts(
            session,
            cohort=cohort,
            epoch=active_epoch if cohort == active_cohort else None,
            evidence_opened_at=(
                active_formal_evidence_opened_at if cohort == active_cohort else None
            ),
            probability_counts=probability_counts,
        )
        for cohort in cohort_values
    ]
    lanes.sort(
        key=lambda lane: (
            lane.scope != "current",
            -(lane.last_evidence_at.timestamp() if lane.last_evidence_at is not None else 0),
            lane.cohort,
        )
    )
    current_lane = next((lane for lane in lanes if lane.cohort == candidate_cohort), lanes[0])

    operational = (
        await session.execute(
            text(
                """
                SELECT (SELECT count(*) FROM stations
                        WHERE active AND stage = 'paper'::station_stage)::bigint AS paper_stations,
                       (SELECT count(*) FROM stations
                        WHERE active AND stage = 'shadow'::station_stage)::bigint
                           AS shadow_stations,
                       (SELECT count(*) FROM positions
                        WHERE mode = 'paper'::execution_mode
                          AND status = 'open'::position_status)::bigint AS open_positions,
                       (SELECT coalesce(sum(size_usd), 0) FROM positions
                        WHERE mode = 'paper'::execution_mode
                          AND status = 'open'::position_status) AS open_exposure,
                       (SELECT balance_usd FROM bankroll_log
                        WHERE mode = 'paper'::execution_mode
                        ORDER BY ts DESC LIMIT 1) AS bankroll
                """
            )
        )
    ).one()
    bankroll = float(operational.bankroll or 0)
    open_exposure = float(operational.open_exposure or 0)
    signal_boundary = active_formal_evidence_opened_at or now
    signal_counts = (
        await session.execute(
            text(
                """
                SELECT count(*)::bigint AS rows,
                       count(*) FILTER (WHERE status = 'pending'::signal_status)::bigint AS pending
                FROM signals
                WHERE ts >= :boundary
                """
            ),
            {"boundary": signal_boundary},
        )
    ).one()
    skip_rows = (
        await session.execute(
            text(
                """
                WITH reasons AS (
                    SELECT regexp_split_to_table(skip_reason, ';') AS reason
                    FROM signals
                    WHERE ts >= :boundary
                      AND skip_reason IS NOT NULL
                      AND status IN ('skipped'::signal_status, 'expired'::signal_status)
                )
                SELECT reason, count(*)::bigint
                FROM reasons
                GROUP BY reason
                ORDER BY count(*) DESC, reason
                LIMIT 8
                """
            ),
            {"boundary": signal_boundary},
        )
    ).all()

    collection_window, liveness, outcomes_in_window = await _collection_window(
        session,
        cohort=candidate_cohort,
        opened_at=(
            active_collection_opened_at
            if active_collection_opened_at is not None and cohort_match and image_match
            else None
        ),
        now=now,
        maintenance_windows=planned_maintenance,
        execution_due_after=execution_due_after,
    )
    velocity = calculate_evidence_velocity(
        complete_days=collection_window.complete_days,
        now=now,
        outcomes_in_complete_window=outcomes_in_window,
        total_outcomes=current_lane.outcomes,
    )
    bottleneck = classify_evidence_bottleneck(
        active_epoch=active_epoch is not None,
        cohort_match=cohort_match,
        image_match=image_match,
        probability_rows=current_lane.probability_rows,
        execution_intents=current_lane.execution_intents,
        filled_orders=current_lane.filled_orders,
        no_fill_orders=current_lane.no_fill_orders,
        open_orders=current_lane.open_orders,
        cost_seals=current_lane.cost_seals,
        eligible_cost_seals=current_lane.eligible_cost_seals,
        eligible_enrollments=current_lane.eligible_enrollments,
        outcomes=current_lane.outcomes,
        independent_blocks=current_lane.independent_blocks,
        open_exposure_usd=open_exposure,
        bankroll_usd=bankroll,
    )
    chronological_epochs = tuple(reversed(epochs))
    changes_30d = sum(
        row.opened_at >= now - timedelta(days=30)
        and (index == 0 or row.cohort != chronological_epochs[index - 1].cohort)
        for index, row in enumerate(chronological_epochs)
    )
    if active_epoch is None:
        state = "activation_required"
    elif not cohort_match:
        state = "cohort_change_blocked"
    elif not image_match:
        state = "release_image_blocked"
    else:
        state = "collecting"
    return EvidenceContinuitySummary(
        generated_at=now,
        state=state,
        candidate_cohort=candidate_cohort,
        candidate_image_digest=candidate_image_digest,
        active_cohort=active_cohort,
        cohort_match=cohort_match,
        image_match=image_match,
        active_epoch=active_epoch,
        continuity_lanes=continuity_lanes,
        epoch_count=len(epochs),
        cohort_changes_30d=changes_30d,
        bottleneck=bottleneck,
        liveness=liveness,
        collection_window=collection_window,
        velocity=velocity,
        paper_stations=_integer(operational.paper_stations),
        shadow_stations=_integer(operational.shadow_stations),
        open_positions=_integer(operational.open_positions),
        open_exposure_usd=open_exposure,
        bankroll_usd=bankroll,
        signal_rows_since_epoch=_integer(signal_counts.rows),
        pending_signals=_integer(signal_counts.pending),
        top_skip_reasons=tuple(
            EvidenceSkipReason(reason=str(reason), count=_integer(count))
            for reason, count in skip_rows
        ),
        lanes=tuple(lanes),
    )


__all__ = [
    "EvidenceCollectionWindow",
    "EvidenceContinuityLane",
    "EvidenceContinuitySummary",
    "EvidenceSkipReason",
    "evidence_continuity_summary",
]
