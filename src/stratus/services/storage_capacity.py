from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

CAPACITY_POLICY_VERSION = "immutable-evidence-preserve-v1"


@dataclass(frozen=True)
class CapacityPoint:
    captured_at: datetime
    database_bytes: int
    clob_book_batches_bytes: int
    clob_market_snapshots_bytes: int
    free_disk_bytes: int | None

    @property
    def clob_ledger_bytes(self) -> int:
        return self.clob_book_batches_bytes + self.clob_market_snapshots_bytes


@dataclass(frozen=True)
class CapacityAssessment:
    severity: str
    reason: str
    observed_hours: float | None
    ledger_bytes: int
    growth_bytes_per_day: float | None
    projected_ledger_bytes: float | None
    days_to_budget: float | None
    budget_bytes: int
    free_disk_bytes: int | None
    minimum_free_disk_bytes: int
    policy_version: str = CAPACITY_POLICY_VERSION


@dataclass(frozen=True)
class CapacityCollectionDecision:
    allowed: bool
    reason: str
    sample_age_seconds: float | None


def assess_capacity_collection_gate(
    current: CapacityPoint | None,
    *,
    now: datetime,
    budget_bytes: int,
    minimum_free_bytes: int,
    sample_ttl: timedelta,
    pending_bytes: int = 0,
) -> CapacityCollectionDecision:
    """Decide whether another immutable CLOB write is safe.

    Collection is deliberately fail-closed. A capacity sample is useful only
    while it is fresh, and both the configured ledger budget and the actual
    filesystem reserve must be available before another exchange response is
    persisted.
    """

    if pending_bytes < 0:
        raise ValueError("pending_bytes cannot be negative")
    if current is None:
        return CapacityCollectionDecision(False, "capacity_sample_missing", None)
    if (
        current.captured_at.tzinfo is None
        or current.captured_at.utcoffset() is None
        or now.tzinfo is None
        or now.utcoffset() is None
    ):
        return CapacityCollectionDecision(False, "capacity_sample_time_invalid", None)
    age_seconds = (now - current.captured_at).total_seconds()
    if age_seconds < 0:
        return CapacityCollectionDecision(False, "capacity_sample_from_future", age_seconds)
    if age_seconds > sample_ttl.total_seconds():
        return CapacityCollectionDecision(False, "capacity_sample_stale", age_seconds)
    projected_ledger_bytes = current.clob_ledger_bytes + pending_bytes
    if projected_ledger_bytes >= budget_bytes:
        return CapacityCollectionDecision(
            False,
            (
                "capacity_budget_exhausted"
                if pending_bytes == 0
                else "capacity_write_would_exhaust_budget"
            ),
            age_seconds,
        )
    if current.free_disk_bytes is None:
        return CapacityCollectionDecision(False, "free_disk_measurement_unavailable", age_seconds)
    projected_free_disk_bytes = current.free_disk_bytes - pending_bytes
    if projected_free_disk_bytes <= minimum_free_bytes:
        return CapacityCollectionDecision(
            False,
            (
                "free_disk_reserve_breached"
                if pending_bytes == 0
                else "free_disk_write_would_breach_reserve"
            ),
            age_seconds,
        )
    return CapacityCollectionDecision(True, "capacity_gate_passed", age_seconds)


def assess_clob_ledger_capacity(
    current: CapacityPoint,
    baseline: CapacityPoint | None,
    *,
    budget_bytes: int,
    warning_fraction: float,
    projection_days: int,
    minimum_window_hours: int,
    minimum_free_bytes: int,
) -> CapacityAssessment:
    """Assess immutable evidence growth without deleting formal provenance.

    A measurement shorter than ``minimum_window_hours`` is deliberately not
    extrapolated: intraday collection is bursty and an early estimate could
    either hide a capacity risk or trigger unnecessary retention work.
    """

    ledger_bytes = current.clob_ledger_bytes
    if ledger_bytes >= budget_bytes:
        return CapacityAssessment(
            severity="blocking",
            reason="capacity_budget_exhausted",
            observed_hours=None,
            ledger_bytes=ledger_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=0.0,
            budget_bytes=budget_bytes,
            free_disk_bytes=current.free_disk_bytes,
            minimum_free_disk_bytes=minimum_free_bytes,
        )

    if current.free_disk_bytes is None:
        return CapacityAssessment(
            severity="blocking",
            reason="free_disk_measurement_unavailable",
            observed_hours=None,
            ledger_bytes=ledger_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=None,
            budget_bytes=budget_bytes,
            free_disk_bytes=None,
            minimum_free_disk_bytes=minimum_free_bytes,
        )
    if current.free_disk_bytes <= minimum_free_bytes:
        return CapacityAssessment(
            severity="blocking",
            reason="free_disk_reserve_breached",
            observed_hours=None,
            ledger_bytes=ledger_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=None,
            budget_bytes=budget_bytes,
            free_disk_bytes=current.free_disk_bytes,
            minimum_free_disk_bytes=minimum_free_bytes,
        )

    free_disk_warning = current.free_disk_bytes <= minimum_free_bytes * 2

    if baseline is None:
        return CapacityAssessment(
            severity=(
                "warning"
                if free_disk_warning or ledger_bytes >= budget_bytes * warning_fraction
                else "info"
            ),
            reason=(
                "free_disk_reserve_at_risk" if free_disk_warning else "measurement_window_pending"
            ),
            observed_hours=None,
            ledger_bytes=ledger_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=None,
            budget_bytes=budget_bytes,
            free_disk_bytes=current.free_disk_bytes,
            minimum_free_disk_bytes=minimum_free_bytes,
        )

    observed_seconds = (current.captured_at - baseline.captured_at).total_seconds()
    observed_hours = observed_seconds / 3600.0
    if observed_hours < minimum_window_hours or observed_seconds <= 0:
        return CapacityAssessment(
            severity=(
                "warning"
                if free_disk_warning or ledger_bytes >= budget_bytes * warning_fraction
                else "info"
            ),
            reason=(
                "free_disk_reserve_at_risk" if free_disk_warning else "measurement_window_pending"
            ),
            observed_hours=max(0.0, observed_hours),
            ledger_bytes=ledger_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=None,
            budget_bytes=budget_bytes,
            free_disk_bytes=current.free_disk_bytes,
            minimum_free_disk_bytes=minimum_free_bytes,
        )

    growth_bytes = max(0, ledger_bytes - baseline.clob_ledger_bytes)
    growth_per_day = growth_bytes * 86_400.0 / observed_seconds
    projected = ledger_bytes + growth_per_day * projection_days
    remaining = max(0.0, budget_bytes - ledger_bytes)
    days_to_budget = remaining / growth_per_day if growth_per_day > 0 else None

    warning = (
        free_disk_warning
        or ledger_bytes >= budget_bytes * warning_fraction
        or projected >= budget_bytes
    )
    return CapacityAssessment(
        severity="warning" if warning else "info",
        reason=(
            "free_disk_reserve_at_risk"
            if free_disk_warning
            else "capacity_projection_at_risk"
            if warning
            else "capacity_within_budget"
        ),
        observed_hours=observed_hours,
        ledger_bytes=ledger_bytes,
        growth_bytes_per_day=growth_per_day,
        projected_ledger_bytes=projected,
        days_to_budget=days_to_budget,
        budget_bytes=budget_bytes,
        free_disk_bytes=current.free_disk_bytes,
        minimum_free_disk_bytes=minimum_free_bytes,
    )
