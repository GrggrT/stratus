from decimal import Decimal

import pytest

from stratus.services.reconciliation import (
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
