from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.services.event_probability_coherence import (
    EventBucketProbability,
    EventCoherenceReason,
    assess_event_probability_coherence,
    assess_event_probability_materialization,
    event_probability_distribution_sha256,
)

NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)
MARKET_DATE = date(2026, 7, 16)
CoherenceMutation = Callable[[EventBucketProbability], EventBucketProbability]


def _row(
    market_id: int,
    *,
    low: str | None,
    high: str | None,
    probability: str | None,
) -> EventBucketProbability:
    return EventBucketProbability(
        market_id=market_id,
        event_id="weather-event",
        station_id=17,
        market_date=MARKET_DATE,
        kind="high",
        resolves_at=NOW + timedelta(days=2),
        resolution_source="https://example.test/final-observation",
        bucket_low_c=Decimal(low) if low is not None else None,
        bucket_high_c=Decimal(high) if high is not None else None,
        eligible=True,
        probability=Decimal(probability) if probability is not None else None,
        probability_ts=NOW,
        horizon_h=Decimal("36"),
        pipeline_cohort="accuracy-v4+fingerprint",
        anchor_snapshot_id=1000 + market_id,
        anchor_market_id=market_id,
        anchor_batch_id=700,
        anchor_eligible=True,
    )


def _complete_rows(
    probabilities: tuple[str | None, str | None, str | None] = (
        "0.200000",
        "0.300000",
        "0.500000",
    ),
) -> list[EventBucketProbability]:
    return [
        _row(1, low=None, high="20", probability=probabilities[0]),
        _row(2, low="20", high="21", probability=probabilities[1]),
        _row(3, low="21", high=None, probability=probabilities[2]),
    ]


def test_complete_coherent_partition_passes_without_synthesizing_buckets() -> None:
    result = assess_event_probability_coherence(_complete_rows())

    assert result.passed
    assert result.reason is None
    assert not result.normalized
    assert result.probabilities == (
        (1, Decimal("0.200000")),
        (2, Decimal("0.300000")),
        (3, Decimal("0.500000")),
    )
    assert result.probability_total_before == Decimal("1.000000")
    assert result.metadata()["pipeline_cohort"] == "accuracy-v4+fingerprint"


def test_accuracy_materialization_is_independent_from_trade_context() -> None:
    rows = [
        replace(
            row,
            resolution_source=None,
            eligible=False,
            anchor_snapshot_id=None,
            anchor_market_id=None,
            anchor_batch_id=None,
            anchor_eligible=False,
        )
        for row in _complete_rows()
    ]

    materialization = assess_event_probability_materialization(rows)
    trade = assess_event_probability_coherence(rows)

    assert materialization.passed
    assert sum(materialization.probability_by_market.values(), Decimal("0")) == 1
    assert trade.reason is EventCoherenceReason.EVENT_SEMANTICS_UNVERIFIABLE


def test_small_mass_drift_is_normalized_to_an_exact_six_decimal_partition() -> None:
    result = assess_event_probability_coherence(
        _complete_rows(("0.200200", "0.299800", "0.499500"))
    )

    assert result.passed
    assert result.normalized
    assert result.probability_total_before == Decimal("0.999500")
    assert sum(result.probability_by_market.values(), start=Decimal("0")) == 1
    assert all(
        probability.as_tuple().exponent == -6
        for probability in result.probability_by_market.values()
    )
    assert set(result.probability_by_market) == {1, 2, 3}


def test_residual_tie_and_distribution_hash_are_deterministic() -> None:
    forward = assess_event_probability_coherence(
        _complete_rows(("0.333334", "0.333334", "0.333334"))
    )
    reverse = assess_event_probability_coherence(
        list(reversed(_complete_rows(("0.333334", "0.333334", "0.333334"))))
    )

    expected = {
        1: Decimal("0.333334"),
        2: Decimal("0.333333"),
        3: Decimal("0.333333"),
    }
    assert forward.probability_by_market == reverse.probability_by_market == expected
    assert event_probability_distribution_sha256(
        forward.probability_by_market
    ) == event_probability_distribution_sha256(reverse.probability_by_market)


def test_mass_tolerance_boundary_is_inclusive() -> None:
    result = assess_event_probability_coherence(
        _complete_rows(("0.200000", "0.300000", "0.499000"))
    )

    assert result.passed
    assert result.normalized
    assert result.mass_deviation == Decimal("0.001000")


@pytest.mark.parametrize(
    ("rows", "reason"),
    [
        (
            [
                *_complete_rows()[:2],
                replace(_complete_rows()[2], bucket_high_c=Decimal("22")),
            ],
            EventCoherenceReason.PARTITION_NOT_EXHAUSTIVE,
        ),
        (
            [
                replace(_complete_rows()[0], bucket_high_c=Decimal("19.5")),
                *_complete_rows()[1:],
            ],
            EventCoherenceReason.PARTITION_GAP,
        ),
        (
            [
                replace(_complete_rows()[0], bucket_high_c=Decimal("20.5")),
                *_complete_rows()[1:],
            ],
            EventCoherenceReason.PARTITION_OVERLAP,
        ),
        (
            [*_complete_rows()[:2], replace(_complete_rows()[2], kind="low")],
            EventCoherenceReason.EVENT_SEMANTICS_MISMATCH,
        ),
        (
            [*_complete_rows()[:2], replace(_complete_rows()[2], eligible=False)],
            EventCoherenceReason.BUCKET_INELIGIBLE,
        ),
    ],
)
def test_unproven_bucket_semantics_fail_closed(
    rows: list[EventBucketProbability],
    reason: EventCoherenceReason,
) -> None:
    result = assess_event_probability_coherence(rows)

    assert not result.passed
    assert result.reason is reason
    assert result.probabilities == ()


def test_non_finite_bucket_boundary_fails_closed_instead_of_raising() -> None:
    rows = _complete_rows()
    rows[1] = replace(rows[1], bucket_low_c=Decimal("NaN"))

    result = assess_event_probability_coherence(rows)

    assert result.reason is EventCoherenceReason.BUCKET_BOUNDS_INVALID
    assert result.detail == "non-finite bucket boundary"
    assert result.probabilities == ()


def test_missing_probability_fails_without_imputing_the_bucket() -> None:
    result = assess_event_probability_coherence(
        _complete_rows(("0.200000", None, "0.500000"))
    )

    assert result.reason is EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE
    assert result.detail == "2"
    assert result.probability_count == 2
    assert result.probabilities == ()


_COHERENCE_MUTATIONS: tuple[
    tuple[CoherenceMutation, EventCoherenceReason], ...
] = (
    (
        lambda row: replace(row, probability_ts=NOW + timedelta(seconds=1)),
        EventCoherenceReason.PROBABILITY_TIME_MISMATCH,
    ),
    (
        lambda row: replace(row, probability_ts=NOW.replace(tzinfo=None)),
        EventCoherenceReason.PROBABILITY_TIME_MISMATCH,
    ),
    (
        lambda row: replace(row, horizon_h=Decimal("35")),
        EventCoherenceReason.PROBABILITY_HORIZON_MISMATCH,
    ),
    (
        lambda row: replace(row, pipeline_cohort="other-cohort"),
        EventCoherenceReason.PROBABILITY_COHORT_MISMATCH,
    ),
    (
        lambda row: replace(row, pipeline_cohort=""),
        EventCoherenceReason.PROBABILITY_COHORT_MISMATCH,
    ),
    (
        lambda row: replace(row, anchor_batch_id=701),
        EventCoherenceReason.PROBABILITY_ANCHOR_BATCH_MISMATCH,
    ),
    (
        lambda row: replace(row, anchor_market_id=999),
        EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE,
    ),
    (
        lambda row: replace(row, anchor_snapshot_id=1001),
        EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE,
    ),
)


@pytest.mark.parametrize(("mutation", "reason"), _COHERENCE_MUTATIONS)
def test_probability_evidence_must_be_one_coherent_snapshot(
    mutation: CoherenceMutation,
    reason: EventCoherenceReason,
) -> None:
    rows = _complete_rows()
    rows[1] = mutation(rows[1])

    result = assess_event_probability_coherence(rows)

    assert result.reason is reason
    assert result.probabilities == ()


@pytest.mark.parametrize(
    "mass_tolerance",
    [Decimal("NaN"), Decimal("Infinity"), Decimal("-0.000001"), Decimal("0.010001")],
)
def test_invalid_mass_tolerance_is_rejected(mass_tolerance: Decimal) -> None:
    with pytest.raises(ValueError, match="mass_tolerance"):
        assess_event_probability_coherence(
            _complete_rows(),
            mass_tolerance=mass_tolerance,
        )


def test_material_mass_failure_exposes_json_safe_metadata() -> None:
    result = assess_event_probability_coherence(
        _complete_rows(("0.100000", "0.200000", "0.500000"))
    )

    assert result.metadata() == {
        "event_id": "weather-event",
        "reason": "event_probability_mass_out_of_tolerance",
        "detail": None,
        "bucket_count": 3,
        "probability_count": 3,
        "probability_total_before": "0.800000",
        "mass_deviation": "0.200000",
        "normalized": False,
        "normalization_scale": None,
        "probability_ts": NOW.isoformat(),
        "pipeline_cohort": "accuracy-v4+fingerprint",
        "anchor_batch_id": 700,
    }


def test_material_probability_mass_error_is_never_normalized() -> None:
    result = assess_event_probability_coherence(
        _complete_rows(("0.100000", "0.200000", "0.500000"))
    )

    assert result.reason is EventCoherenceReason.PROBABILITY_MASS_OUT_OF_TOLERANCE
    assert result.probability_total_before == Decimal("0.800000")
    assert result.mass_deviation == Decimal("0.200000")
    assert not result.normalized
    assert result.probabilities == ()
