from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stratus.jobs.generate_weighted_ensemble import (
    _baseline_total_is_valid,
    _quantize_probability_partition,
)
from stratus.jobs.generate_weighted_ensemble import (
    _historical_training_statement as weighted_training_statement,
)
from stratus.jobs.recalibrate_emos import (
    _historical_training_statement as recalibration_training_statement,
)


def test_weighted_baseline_partition_tolerance_is_fail_closed() -> None:
    assert _baseline_total_is_valid(1.0)
    assert _baseline_total_is_valid(1.001515)
    assert _baseline_total_is_valid(0.99)
    assert not _baseline_total_is_valid(0.95)
    assert not _baseline_total_is_valid(1.02)
    assert not _baseline_total_is_valid(float("nan"))


def test_probability_partition_quantization_is_exact_and_order_independent() -> None:
    values = [
        (30, Decimal("0.333333")),
        (10, Decimal("0.333333")),
        (20, Decimal("0.333333")),
    ]

    forward = _quantize_probability_partition(values)
    reverse = _quantize_probability_partition(list(reversed(values)))

    assert forward == reverse == {
        10: Decimal("0.333334"),
        20: Decimal("0.333333"),
        30: Decimal("0.333333"),
    }
    assert sum(forward.values(), start=Decimal("0")) == Decimal("1.000000")


def test_probability_partition_quantization_preserves_edges_and_ordering() -> None:
    quantized = _quantize_probability_partition(
        [
            (3, Decimal("0")),
            (2, Decimal("0.1999993")),
            (4, Decimal("0.4000003")),
            (1, Decimal("0.4000004")),
        ]
    )

    assert sum(quantized.values(), start=Decimal("0")) == Decimal("1.000000")
    assert quantized[3] == Decimal("0.000000")
    assert quantized[1] >= quantized[4] >= quantized[2] >= quantized[3]
    assert all(Decimal("0") <= value <= Decimal("1") for value in quantized.values())


@pytest.mark.parametrize(
    "values",
    (
        [],
        [(1, Decimal("-0.1")), (2, Decimal("1.1"))],
        [(1, Decimal("NaN")), (2, Decimal("1"))],
        [(1, Decimal("0")), (2, Decimal("0"))],
        [(1, Decimal("0.5")), (1, Decimal("0.5"))],
    ),
)
def test_probability_partition_quantization_rejects_invalid_vectors(
    values: list[tuple[int, Decimal]],
) -> None:
    with pytest.raises((ValueError, ArithmeticError)):
        _quantize_probability_partition(values)


def test_training_statements_canonicalize_exact_cells_and_block_conflict_fallback() -> None:
    cutoff = datetime(2026, 7, 20, tzinfo=UTC)
    statements = (
        recalibration_training_statement(earliest_date=date(2026, 1, 1), truth_cutoff=cutoff),
        weighted_training_statement(earliest_date=date(2026, 1, 1), truth_cutoff=cutoff),
    )

    for statement in statements:
        sql = str(statement.compile(compile_kwargs={"literal_binds": True})).lower()
        assert "canonical_settlement_actuals" in sql
        assert "group by eligible_settlement_actuals.station_id" in sql
        assert "having min(eligible_settlement_actuals.value_c) = max(" in sql
        assert "not (exists (select settlement_actuals.id" in sql
