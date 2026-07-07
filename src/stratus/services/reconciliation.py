from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

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
