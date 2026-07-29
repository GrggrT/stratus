from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from stratus.db.models import DailyActual
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)

AS_OF = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def test_canonical_exact_truth_is_one_agreed_row_per_cell() -> None:
    canonical = canonical_settlement_actuals(as_of=AS_OF)
    sql = str(
        select(canonical).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "GROUP BY eligible_settlement_actuals.station_id" in sql
    assert "min(eligible_settlement_actuals.value_c)" in sql
    assert "max(eligible_settlement_actuals.value_c)" in sql
    assert "HAVING min(eligible_settlement_actuals.value_c) = max(" in sql
    assert "official_resolution_exact_actuals.extracted_at" in sql
    assert "settlement_actuals.retrieved_at" in sql


def test_exact_candidate_exists_correlates_to_outer_weather_cell() -> None:
    statement = select(DailyActual.station_id).where(
        eligible_settlement_actual_exists(
            station_id=DailyActual.station_id,
            actual_date=DailyActual.date,
            kind=DailyActual.kind,
            as_of=AS_OF,
        )
    )
    sql = str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "settlement_actuals.station_id = daily_actuals.station_id" in sql
    assert "settlement_actuals.date = daily_actuals.date" in sql
    assert "settlement_actuals.kind = daily_actuals.kind" in sql
    assert "settlement_actuals.is_final IS true" in sql
