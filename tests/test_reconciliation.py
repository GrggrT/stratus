from decimal import Decimal

import pytest

from stratus.db.models import ResolutionReconciliationClassification
from stratus.services.reconciliation import (
    assess_resolution,
    boundary_margin,
    bucket_contains,
    clear_mismatches,
    find_resolution_mismatches,
)


def test_bucket_contains_uses_half_open_interval() -> None:
    low, high = Decimal("28.5"), Decimal("29.5")
    assert bucket_contains(Decimal("28.5"), low, high) is True
    assert bucket_contains(Decimal("29.5"), low, high) is False
    assert bucket_contains(Decimal("30"), Decimal("29.5"), None) is True
    assert bucket_contains(Decimal("28"), None, Decimal("28.5")) is True
    with pytest.raises(ValueError):
        bucket_contains(Decimal("20"), None, None)


def test_boundary_margin_is_distance_to_nearest_bound() -> None:
    assert boundary_margin(
        Decimal("29.0"), Decimal("28.5"), Decimal("29.5")
    ) == Decimal("0.5")
    assert boundary_margin(Decimal("31"), Decimal("29.5"), None) == Decimal("1.5")


def test_find_resolution_mismatches_flags_disagreements_only() -> None:
    rows = [
        # Agrees: temp inside bucket, resolved YES.
        (1, Decimal("29.0"), Decimal("28.5"), Decimal("29.5"), True),
        # Agrees: temp outside bucket, resolved NO.
        (2, Decimal("27.0"), Decimal("28.5"), Decimal("29.5"), False),
        # Clear mismatch: temp well inside bucket but market resolved NO.
        (3, Decimal("29.0"), Decimal("28.5"), Decimal("29.5"), False),
        # Borderline mismatch: 0.1°C from the boundary — rounding territory.
        (4, Decimal("28.4"), Decimal("28.5"), Decimal("29.5"), True),
        # No boundaries at all: skipped.
        (5, Decimal("20"), None, None, True),
    ]

    mismatches = find_resolution_mismatches(rows)

    assert [item.market_id for item in mismatches] == [3, 4]
    assert mismatches[0].margin_c == Decimal("0.5")
    assert mismatches[1].margin_c == Decimal("0.1")

    clear = clear_mismatches(mismatches)
    assert [item.market_id for item in clear] == [3]


def test_assessment_prioritizes_exact_source_conflict() -> None:
    result = assess_resolution(
        resolved_yes=True,
        low_c=Decimal("20"),
        high_c=Decimal("21"),
        metar_temp_c=Decimal("20.5"),
        exact_temp_c=Decimal("19.9"),
    )

    assert (
        result.classification
        is ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH
    )
    assert result.metar_implied_yes is True
    assert result.exact_implied_yes is False
    assert result.exact_truth_gap_c == Decimal("0.6")


def test_assessment_retains_exact_truth_gap_inside_same_bucket() -> None:
    result = assess_resolution(
        resolved_yes=True,
        low_c=Decimal("20"),
        high_c=Decimal("22"),
        metar_temp_c=Decimal("20.4"),
        exact_temp_c=Decimal("20.8"),
    )

    assert (
        result.classification
        is ResolutionReconciliationClassification.EXACT_TRUTH_GAP
    )
    assert result.exact_truth_gap_c == Decimal("0.4")


def test_assessment_separates_boundary_and_clear_metar_mismatch() -> None:
    boundary = assess_resolution(
        resolved_yes=True,
        low_c=Decimal("20"),
        high_c=Decimal("21"),
        metar_temp_c=Decimal("19.9"),
        exact_temp_c=None,
    )
    clear = assess_resolution(
        resolved_yes=True,
        low_c=Decimal("20"),
        high_c=Decimal("21"),
        metar_temp_c=Decimal("19.0"),
        exact_temp_c=None,
    )

    assert (
        boundary.classification
        is ResolutionReconciliationClassification.BOUNDARY_SENSITIVE
    )
    assert clear.classification is ResolutionReconciliationClassification.CLEAR_MISMATCH


def test_assessment_is_explicit_when_training_truth_is_missing() -> None:
    result = assess_resolution(
        resolved_yes=True,
        low_c=Decimal("20"),
        high_c=Decimal("21"),
        metar_temp_c=None,
        exact_temp_c=Decimal("20.5"),
    )

    assert (
        result.classification
        is ResolutionReconciliationClassification.TRUTH_UNAVAILABLE
    )
