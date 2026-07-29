from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

ETA_INSUFFICIENT_FULL_DAYS = "insufficient_full_days_7_required"
ETA_NO_OUTCOME_VELOCITY = "no_outcomes_in_complete_days"


class EvidenceLiveness(StrEnum):
    WAITING_FOR_DUE = "waiting_for_due"
    DUE_PENDING = "due_pending"
    COLLECTING_ON_SCHEDULE = "collecting_on_schedule"
    OVERDUE = "overdue"
    STALLED = "stalled"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class EvidenceDueCounts:
    validation_waiting_for_due: int = 0
    validation_due_pending: int = 0
    validation_overdue: int = 0
    validation_classified: int = 0
    execution_waiting_for_due: int = 0
    execution_due_pending: int = 0
    execution_overdue: int = 0
    execution_classified: int = 0
    mark_waiting_for_due: int = 0
    mark_due_pending: int = 0
    mark_overdue: int = 0
    mark_classified: int = 0

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.values()):
            raise ValueError("evidence due counts must be non-negative")

    def values(self) -> tuple[int, ...]:
        return (
            self.validation_waiting_for_due,
            self.validation_due_pending,
            self.validation_overdue,
            self.validation_classified,
            self.execution_waiting_for_due,
            self.execution_due_pending,
            self.execution_overdue,
            self.execution_classified,
            self.mark_waiting_for_due,
            self.mark_due_pending,
            self.mark_overdue,
            self.mark_classified,
        )

    @property
    def waiting_for_due(self) -> int:
        return (
            self.validation_waiting_for_due
            + self.execution_waiting_for_due
            + self.mark_waiting_for_due
        )

    @property
    def due_pending(self) -> int:
        return (
            self.validation_due_pending
            + self.execution_due_pending
            + self.mark_due_pending
        )

    @property
    def overdue(self) -> int:
        return self.validation_overdue + self.execution_overdue + self.mark_overdue

    @property
    def classified(self) -> int:
        return (
            self.validation_classified
            + self.execution_classified
            + self.mark_classified
        )

    @property
    def total(self) -> int:
        return self.waiting_for_due + self.due_pending + self.overdue + self.classified


@dataclass(frozen=True, slots=True)
class EvidenceVelocity:
    complete_days: int
    outcomes_per_day: float | None
    eta_100_at: datetime | None
    eta_200_at: datetime | None
    eta_reason: str | None


@dataclass(frozen=True, slots=True)
class EvidenceBottleneck:
    code: str
    detail: str


def calculate_evidence_velocity(
    *,
    complete_days: int,
    now: datetime,
    outcomes_in_complete_window: int,
    total_outcomes: int,
) -> EvidenceVelocity:
    """Estimate sample velocity only after seven complete rolling days.

    Partial days are deliberately excluded.  Before seven complete days the
    rate and ETAs remain unavailable instead of extrapolating startup noise.
    """

    if complete_days < 0 or outcomes_in_complete_window < 0 or total_outcomes < 0:
        raise ValueError("outcome counts must be non-negative")
    if now.tzinfo is None:
        raise ValueError("evidence velocity timestamp must be timezone-aware")
    if complete_days < 7:
        return EvidenceVelocity(
            complete_days,
            None,
            None,
            None,
            ETA_INSUFFICIENT_FULL_DAYS,
        )
    rate = outcomes_in_complete_window / complete_days
    if rate <= 0:
        return EvidenceVelocity(
            complete_days,
            0.0,
            None,
            None,
            ETA_NO_OUTCOME_VELOCITY,
        )

    def eta(target: int) -> datetime:
        remaining = max(0, target - total_outcomes)
        return now + timedelta(days=math.ceil(remaining / rate))

    return EvidenceVelocity(
        complete_days,
        rate,
        eta(100),
        eta(200),
        None,
    )


def classify_evidence_liveness(
    *,
    grace_elapsed: bool,
    scheduler_covered: bool,
    due_counts: EvidenceDueCounts,
) -> EvidenceLiveness:
    """Classify a full collection day without treating future work as late.

    Scheduler coverage is evaluated only once the day-level grace has elapsed.
    A day is complete only when every obligation naturally due inside it has an
    immutable classification; future-due work is never pulled into an earlier
    denominator or called overdue early.
    """

    if due_counts.overdue:
        return EvidenceLiveness.OVERDUE
    if not grace_elapsed:
        if due_counts.due_pending:
            return (
                EvidenceLiveness.COLLECTING_ON_SCHEDULE
                if due_counts.classified
                else EvidenceLiveness.DUE_PENDING
            )
        return (
            EvidenceLiveness.COLLECTING_ON_SCHEDULE
            if due_counts.classified
            else EvidenceLiveness.WAITING_FOR_DUE
        )
    if not scheduler_covered:
        return EvidenceLiveness.STALLED
    if due_counts.due_pending:
        return EvidenceLiveness.DUE_PENDING
    if due_counts.waiting_for_due:
        return (
            EvidenceLiveness.COLLECTING_ON_SCHEDULE
            if due_counts.classified
            else EvidenceLiveness.WAITING_FOR_DUE
        )
    return EvidenceLiveness.COMPLETE


def full_utc_collection_days(
    *,
    opened_at: datetime | None,
    now: datetime,
) -> tuple[tuple[datetime, datetime], ...]:
    """Return only closed UTC calendar days wholly after activation.

    The partial activation day and current UTC day are never candidates.  Due
    classification, scheduler coverage, grace, and maintenance are additional
    repository-side requirements before a candidate may be called complete.
    """

    if opened_at is None:
        return ()
    if opened_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("collection-day timestamps must be timezone-aware")
    opened = opened_at.astimezone(UTC)
    current = now.astimezone(UTC)
    if current < opened:
        raise ValueError("evidence epoch cannot open in the future")
    opened_midnight = opened.replace(hour=0, minute=0, second=0, microsecond=0)
    first_start = (
        opened_midnight
        if opened == opened_midnight
        else opened_midnight + timedelta(days=1)
    )
    current_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    windows: list[tuple[datetime, datetime]] = []
    start = first_start
    while start < current_start:
        end = start + timedelta(days=1)
        windows.append((start, end))
        start = end
    return tuple(windows)


def classify_evidence_bottleneck(
    *,
    active_epoch: bool,
    cohort_match: bool,
    image_match: bool,
    probability_rows: int,
    execution_intents: int,
    filled_orders: int,
    no_fill_orders: int,
    open_orders: int,
    cost_seals: int,
    eligible_cost_seals: int,
    eligible_enrollments: int,
    outcomes: int,
    independent_blocks: int,
    open_exposure_usd: float,
    bankroll_usd: float,
) -> EvidenceBottleneck:
    """Name the first honest break in the prospective evidence funnel."""

    counts = (
        probability_rows,
        execution_intents,
        filled_orders,
        no_fill_orders,
        open_orders,
        cost_seals,
        eligible_cost_seals,
        eligible_enrollments,
        outcomes,
        independent_blocks,
    )
    if any(value < 0 for value in counts):
        raise ValueError("evidence funnel counts must be non-negative")
    if active_epoch is False:
        return EvidenceBottleneck(
            "release_activation_required",
            "No reviewed probability release epoch is active; execution is fail-closed.",
        )
    if cohort_match is False:
        return EvidenceBottleneck(
            "cohort_change_blocked",
            "Running probability identity differs from the reviewed epoch; "
            "no new intent is allowed.",
        )
    if image_match is False:
        return EvidenceBottleneck(
            "release_image_mismatch",
            "Running image digest differs from the reviewed epoch; no new intent is allowed.",
        )
    if probability_rows == 0:
        return EvidenceBottleneck(
            "probability_publication_empty",
            "The active epoch has not published an anchored probability row yet.",
        )
    if execution_intents == 0:
        if bankroll_usd > 0 and open_exposure_usd >= bankroll_usd:
            return EvidenceBottleneck(
                "risk_capacity_occupied",
                "Existing open exposure is at least the paper bankroll; risk gates are "
                "correctly suppressing new intents.",
            )
        return EvidenceBottleneck(
            "awaiting_executable_signal",
            "Probabilities are publishing, but no signal has cleared every edge, "
            "liquidity, and risk gate.",
        )
    if open_orders > 0:
        return EvidenceBottleneck(
            "maker_orders_resting",
            "At least one immutable maker intent is resting inside its execution window.",
        )
    if filled_orders == 0 and no_fill_orders > 0:
        return EvidenceBottleneck(
            "maker_no_fills",
            "All completed attempts expired without a genuine market trade-through; "
            "no fill is fabricated.",
        )
    if cost_seals < execution_intents:
        return EvidenceBottleneck(
            "execution_seal_pending",
            "A terminal execution attempt is still waiting for its immutable cost seal.",
        )
    if filled_orders > 0 and eligible_cost_seals == 0:
        return EvidenceBottleneck(
            "execution_evidence_ineligible",
            "Fills exist, but none yet satisfy the frozen execution-evidence definition.",
        )
    if eligible_enrollments < filled_orders:
        return EvidenceBottleneck(
            "validation_enrollment_gap",
            "A cohort fill is not represented by an eligible prospective enrollment.",
        )
    if outcomes < eligible_enrollments:
        return EvidenceBottleneck(
            "awaiting_resolution",
            "Eligible fills are enrolled and are waiting for immutable Gamma outcomes.",
        )
    if independent_blocks < outcomes:
        return EvidenceBottleneck(
            "correlated_orders_consolidated",
            "Resolved orders are accumulating, but correlated buckets count as fewer "
            "independent blocks.",
        )
    return EvidenceBottleneck(
        "collecting_independent_blocks",
        "The frozen cohort is advancing through genuine independent resolved blocks.",
    )


__all__ = [
    "ETA_INSUFFICIENT_FULL_DAYS",
    "ETA_NO_OUTCOME_VELOCITY",
    "EvidenceBottleneck",
    "EvidenceDueCounts",
    "EvidenceLiveness",
    "EvidenceVelocity",
    "calculate_evidence_velocity",
    "classify_evidence_bottleneck",
    "classify_evidence_liveness",
    "full_utc_collection_days",
]
