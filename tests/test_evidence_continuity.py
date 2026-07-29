from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from stratus.repositories.evidence_continuity_reporting import (
    _operational_collection_opened_at,
    _prospective_evidence_opened_at,
)
from stratus.services.evidence_continuity import (
    ETA_INSUFFICIENT_FULL_DAYS,
    ETA_NO_OUTCOME_VELOCITY,
    EvidenceDueCounts,
    EvidenceLiveness,
    calculate_evidence_velocity,
    classify_evidence_bottleneck,
    classify_evidence_liveness,
    full_utc_collection_days,
)

NOW = datetime(2026, 7, 15, 12, tzinfo=UTC)


def test_cross_lane_evidence_window_uses_newest_material_boundary() -> None:
    oldest = NOW - timedelta(days=10)
    newest = NOW - timedelta(hours=1)
    lanes = [
        SimpleNamespace(lane_name="predictive_validation", boundary_at=oldest),
        SimpleNamespace(lane_name="selection_policy", boundary_at=newest),
        SimpleNamespace(lane_name="execution_evidence", boundary_at=newest),
        SimpleNamespace(lane_name="station_lifecycle", boundary_at=newest),
        SimpleNamespace(lane_name="official_resolution_truth", boundary_at=newest),
        SimpleNamespace(lane_name="operational_legacy", boundary_at=oldest),
    ]

    assert _prospective_evidence_opened_at(lanes) == newest  # type: ignore[arg-type]


def test_cross_lane_evidence_window_fails_closed_when_lane_is_missing() -> None:
    lanes = [
        SimpleNamespace(lane_name="predictive_validation", boundary_at=NOW),
        SimpleNamespace(lane_name="selection_policy", boundary_at=NOW),
        SimpleNamespace(lane_name="execution_evidence", boundary_at=NOW),
        SimpleNamespace(lane_name="station_lifecycle", boundary_at=NOW),
    ]

    assert _prospective_evidence_opened_at(lanes) is None  # type: ignore[arg-type]


def test_operational_fork_resets_collection_only_not_formal_boundary() -> None:
    formal_boundary = NOW - timedelta(days=10)
    operational_fork = NOW - timedelta(hours=1)
    lanes = [
        SimpleNamespace(lane_name=lane_name, boundary_at=formal_boundary)
        for lane_name in (
            "predictive_validation",
            "selection_policy",
            "execution_evidence",
            "station_lifecycle",
            "official_resolution_truth",
        )
    ]
    lanes.append(
        SimpleNamespace(
            lane_name="operational_legacy",
            boundary_at=operational_fork,
        )
    )

    assert _prospective_evidence_opened_at(lanes) == formal_boundary  # type: ignore[arg-type]
    assert _operational_collection_opened_at(lanes) == operational_fork  # type: ignore[arg-type]


def test_continued_operational_lane_preserves_newest_formal_boundary() -> None:
    oldest = NOW - timedelta(days=10)
    newest_formal = NOW - timedelta(days=2)
    lanes = [
        SimpleNamespace(lane_name="predictive_validation", boundary_at=oldest),
        SimpleNamespace(lane_name="selection_policy", boundary_at=newest_formal),
        SimpleNamespace(lane_name="execution_evidence", boundary_at=oldest),
        SimpleNamespace(lane_name="station_lifecycle", boundary_at=oldest),
        SimpleNamespace(lane_name="official_resolution_truth", boundary_at=oldest),
        SimpleNamespace(lane_name="operational_legacy", boundary_at=oldest),
    ]

    assert _prospective_evidence_opened_at(lanes) == newest_formal  # type: ignore[arg-type]
    assert _operational_collection_opened_at(lanes) == newest_formal  # type: ignore[arg-type]


def test_collection_window_fails_closed_when_operational_lane_is_missing() -> None:
    lanes = [
        SimpleNamespace(lane_name=lane_name, boundary_at=NOW)
        for lane_name in (
            "predictive_validation",
            "selection_policy",
            "execution_evidence",
            "station_lifecycle",
            "official_resolution_truth",
        )
    ]

    assert _prospective_evidence_opened_at(lanes) == NOW  # type: ignore[arg-type]
    assert _operational_collection_opened_at(lanes) is None  # type: ignore[arg-type]


def test_velocity_refuses_eta_before_seven_complete_days() -> None:
    result = calculate_evidence_velocity(
        complete_days=6,
        now=NOW,
        outcomes_in_complete_window=30,
        total_outcomes=32,
    )

    assert result.complete_days == 6
    assert result.outcomes_per_day is None
    assert result.eta_100_at is None
    assert result.eta_200_at is None
    assert result.eta_reason == ETA_INSUFFICIENT_FULL_DAYS


def test_velocity_uses_only_complete_window_and_reports_honest_eta() -> None:
    result = calculate_evidence_velocity(
        complete_days=10,
        now=NOW,
        outcomes_in_complete_window=20,
        total_outcomes=24,
    )

    assert result.complete_days == 10
    assert result.outcomes_per_day == 2.0
    assert result.eta_100_at == NOW + timedelta(days=38)
    assert result.eta_200_at == NOW + timedelta(days=88)
    assert result.eta_reason is None


def test_velocity_with_zero_growth_never_invents_eta() -> None:
    result = calculate_evidence_velocity(
        complete_days=8,
        now=NOW,
        outcomes_in_complete_window=0,
        total_outcomes=0,
    )

    assert result.outcomes_per_day == 0.0
    assert result.eta_100_at is None
    assert result.eta_reason == ETA_NO_OUTCOME_VELOCITY


@pytest.mark.parametrize(
    ("grace_elapsed", "scheduler_covered", "due_counts", "expected"),
    [
        (False, False, EvidenceDueCounts(), EvidenceLiveness.WAITING_FOR_DUE),
        (
            False,
            True,
            EvidenceDueCounts(validation_due_pending=1),
            EvidenceLiveness.DUE_PENDING,
        ),
        (
            False,
            True,
            EvidenceDueCounts(
                validation_due_pending=1,
                validation_classified=1,
            ),
            EvidenceLiveness.COLLECTING_ON_SCHEDULE,
        ),
        (True, False, EvidenceDueCounts(), EvidenceLiveness.STALLED),
        (
            True,
            False,
            EvidenceDueCounts(validation_overdue=1),
            EvidenceLiveness.OVERDUE,
        ),
        (
            True,
            True,
            EvidenceDueCounts(validation_overdue=1),
            EvidenceLiveness.OVERDUE,
        ),
        (
            True,
            True,
            EvidenceDueCounts(execution_due_pending=1),
            EvidenceLiveness.DUE_PENDING,
        ),
        (
            True,
            True,
            EvidenceDueCounts(mark_waiting_for_due=1),
            EvidenceLiveness.WAITING_FOR_DUE,
        ),
        (
            True,
            True,
            EvidenceDueCounts(
                validation_classified=1,
                mark_waiting_for_due=1,
            ),
            EvidenceLiveness.COLLECTING_ON_SCHEDULE,
        ),
        (
            True,
            True,
            EvidenceDueCounts(
                validation_classified=1,
                execution_classified=1,
                mark_classified=2,
            ),
            EvidenceLiveness.COMPLETE,
        ),
    ],
)
def test_due_aware_liveness_classification(
    grace_elapsed: bool,
    scheduler_covered: bool,
    due_counts: EvidenceDueCounts,
    expected: EvidenceLiveness,
) -> None:
    assert (
        classify_evidence_liveness(
            grace_elapsed=grace_elapsed,
            scheduler_covered=scheduler_covered,
            due_counts=due_counts,
        )
        is expected
    )


def test_due_counts_reject_negative_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        EvidenceDueCounts(validation_overdue=-1)


def test_full_utc_days_exclude_partial_activation_and_current_days() -> None:
    windows = full_utc_collection_days(
        opened_at=datetime(2026, 7, 7, 12, tzinfo=UTC),
        now=datetime(2026, 7, 15, 18, tzinfo=UTC),
    )

    assert len(windows) == 7
    assert windows[0] == (
        datetime(2026, 7, 8, tzinfo=UTC),
        datetime(2026, 7, 9, tzinfo=UTC),
    )
    assert windows[-1][1] == datetime(2026, 7, 15, tzinfo=UTC)


def test_midnight_activation_keeps_first_full_utc_day() -> None:
    windows = full_utc_collection_days(
        opened_at=datetime(2026, 7, 8, tzinfo=UTC),
        now=datetime(2026, 7, 10, 1, tzinfo=UTC),
    )

    assert [start.date().isoformat() for start, _end in windows] == [
        "2026-07-08",
        "2026-07-09",
    ]


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"active_epoch": False}, "release_activation_required"),
        ({"cohort_match": False}, "cohort_change_blocked"),
        ({"image_match": False}, "release_image_mismatch"),
        ({"probability_rows": 0}, "probability_publication_empty"),
        ({"execution_intents": 0}, "awaiting_executable_signal"),
        (
            {"execution_intents": 0, "open_exposure_usd": 101.0},
            "risk_capacity_occupied",
        ),
        ({"open_orders": 1}, "maker_orders_resting"),
        ({"filled_orders": 0, "no_fill_orders": 2}, "maker_no_fills"),
        ({"cost_seals": 0}, "execution_seal_pending"),
        ({"eligible_cost_seals": 0}, "execution_evidence_ineligible"),
        ({"eligible_enrollments": 0}, "validation_enrollment_gap"),
        ({"outcomes": 0}, "awaiting_resolution"),
        ({"independent_blocks": 0}, "correlated_orders_consolidated"),
    ],
)
def test_bottleneck_names_first_honest_funnel_break(
    overrides: dict[str, object],
    expected: str,
) -> None:
    values: dict[str, object] = {
        "active_epoch": True,
        "cohort_match": True,
        "image_match": True,
        "probability_rows": 10,
        "execution_intents": 2,
        "filled_orders": 1,
        "no_fill_orders": 1,
        "open_orders": 0,
        "cost_seals": 2,
        "eligible_cost_seals": 1,
        "eligible_enrollments": 1,
        "outcomes": 1,
        "independent_blocks": 1,
        "open_exposure_usd": 0.0,
        "bankroll_usd": 100.0,
    }
    values.update(overrides)

    result = classify_evidence_bottleneck(**values)  # type: ignore[arg-type]

    assert result.code == expected


def test_bottleneck_reports_advancing_frozen_cohort() -> None:
    result = classify_evidence_bottleneck(
        active_epoch=True,
        cohort_match=True,
        image_match=True,
        probability_rows=100,
        execution_intents=20,
        filled_orders=10,
        no_fill_orders=10,
        open_orders=0,
        cost_seals=20,
        eligible_cost_seals=10,
        eligible_enrollments=10,
        outcomes=10,
        independent_blocks=10,
        open_exposure_usd=20.0,
        bankroll_usd=100.0,
    )

    assert result.code == "collecting_independent_blocks"
