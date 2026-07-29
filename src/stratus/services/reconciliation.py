from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

from stratus.db.models import ResolutionReconciliationClassification

# A mismatch this far from the nearest bucket boundary cannot be explained by
# rounding differences between METAR readings and the official climate report;
# it means the observation pipeline disagrees with the resolution source.
CLEAR_MISMATCH_MARGIN_C = Decimal("0.25")


@dataclass(frozen=True, slots=True)
class ResolutionMismatch:
    market_id: int
    temp_c: Decimal
    margin_c: Decimal
    observed_yes: bool
    resolved_yes: bool


@dataclass(frozen=True, slots=True)
class ResolutionAssessment:
    classification: ResolutionReconciliationClassification
    metar_implied_yes: bool | None
    exact_implied_yes: bool | None
    boundary_margin_c: Decimal | None
    exact_truth_gap_c: Decimal | None


def bucket_contains(
    temp_c: Decimal, low_c: Decimal | None, high_c: Decimal | None
) -> bool:
    """Same [low, high) semantics the probability pipeline uses."""
    if low_c is None and high_c is None:
        raise ValueError("bucket must have at least one boundary")
    return (low_c is None or temp_c >= low_c) and (high_c is None or temp_c < high_c)


def boundary_margin(
    temp_c: Decimal, low_c: Decimal | None, high_c: Decimal | None
) -> Decimal:
    """Distance from the temperature to the nearest defined bucket boundary."""
    distances = [abs(temp_c - bound) for bound in (low_c, high_c) if bound is not None]
    if not distances:
        raise ValueError("bucket must have at least one boundary")
    return min(distances)


def find_resolution_mismatches(
    rows: Iterable[tuple[int, Decimal, Decimal | None, Decimal | None, bool]],
) -> list[ResolutionMismatch]:
    """Compare METAR-derived daily actuals against actual market resolutions.

    Each row is (market_id, actual_temp_c, bucket_low_c, bucket_high_c,
    resolved_yes). A mismatch means the market resolved differently from what
    our own observations imply — the key epistemic risk of calibrating on
    METAR data while Polymarket resolves on official climate reports.
    """
    mismatches = []
    for market_id, temp_c, low_c, high_c, resolved_yes in rows:
        if low_c is None and high_c is None:
            continue
        observed_yes = bucket_contains(temp_c, low_c, high_c)
        if observed_yes != resolved_yes:
            mismatches.append(
                ResolutionMismatch(
                    market_id=market_id,
                    temp_c=temp_c,
                    margin_c=boundary_margin(temp_c, low_c, high_c),
                    observed_yes=observed_yes,
                    resolved_yes=resolved_yes,
                )
            )
    return mismatches


def clear_mismatches(
    mismatches: Iterable[ResolutionMismatch],
    threshold_c: Decimal = CLEAR_MISMATCH_MARGIN_C,
) -> list[ResolutionMismatch]:
    """Mismatches too far from a boundary to be rounding noise."""
    return [item for item in mismatches if item.margin_c >= threshold_c]


def assess_resolution(
    *,
    resolved_yes: bool,
    low_c: Decimal | None,
    high_c: Decimal | None,
    metar_temp_c: Decimal | None,
    exact_temp_c: Decimal | None,
    threshold_c: Decimal = CLEAR_MISMATCH_MARGIN_C,
) -> ResolutionAssessment:
    """Classify one attested outcome against the two available truth layers.

    Exact licensed truth has precedence over METAR-derived truth.  A venue
    outcome that contradicts the exact source is always its own fail-closed
    class.  Otherwise an exact-vs-METAR temperature gap is retained even when
    both temperatures happen to fall in the same bucket.
    """

    if low_c is None and high_c is None:
        return ResolutionAssessment(
            classification=ResolutionReconciliationClassification.TRUTH_UNAVAILABLE,
            metar_implied_yes=None,
            exact_implied_yes=None,
            boundary_margin_c=None,
            exact_truth_gap_c=None,
        )

    metar_implied = (
        bucket_contains(metar_temp_c, low_c, high_c)
        if metar_temp_c is not None
        else None
    )
    exact_implied = (
        bucket_contains(exact_temp_c, low_c, high_c)
        if exact_temp_c is not None
        else None
    )
    margin = (
        boundary_margin(metar_temp_c, low_c, high_c)
        if metar_temp_c is not None
        else None
    )
    truth_gap = (
        abs(exact_temp_c - metar_temp_c)
        if exact_temp_c is not None and metar_temp_c is not None
        else None
    )

    if exact_implied is not None and exact_implied != resolved_yes:
        classification = ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH
    elif metar_implied is None:
        classification = ResolutionReconciliationClassification.TRUTH_UNAVAILABLE
    elif truth_gap is not None and truth_gap >= threshold_c:
        classification = ResolutionReconciliationClassification.EXACT_TRUTH_GAP
    elif metar_implied != resolved_yes:
        classification = (
            ResolutionReconciliationClassification.CLEAR_MISMATCH
            if margin is not None and margin >= threshold_c
            else ResolutionReconciliationClassification.BOUNDARY_SENSITIVE
        )
    else:
        classification = ResolutionReconciliationClassification.MATCH

    return ResolutionAssessment(
        classification=classification,
        metar_implied_yes=metar_implied,
        exact_implied_yes=exact_implied,
        boundary_margin_c=margin,
        exact_truth_gap_c=truth_gap,
    )
