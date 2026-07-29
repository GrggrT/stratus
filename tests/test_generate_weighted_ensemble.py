from __future__ import annotations

from datetime import UTC, date, datetime

from stratus.jobs.generate_weighted_ensemble import (
    _baseline_total_is_valid,
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
