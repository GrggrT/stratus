from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import aliased
from sqlalchemy.sql import ClauseElement

from stratus.db.models import DailyActual
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible


def _compiled_sql(statement: ClauseElement) -> str:
    return str(
        statement.compile(
            compile_kwargs={"literal_binds": True},
        )
    )


def test_predicate_is_correlated_not_exists_with_exact_blocking_policy() -> None:
    statement = select(DailyActual.station_id).where(
        daily_actual_fallback_eligible(
            station_id=DailyActual.station_id,
            actual_date=DailyActual.date,
            kind=DailyActual.kind,
        )
    )

    sql = _compiled_sql(statement)

    assert "NOT (EXISTS (SELECT resolution_reconciliations.id" in sql
    assert "resolution_reconciliations.station_id = daily_actuals.station_id" in sql
    assert "resolution_reconciliations.market_date = daily_actuals.date" in sql
    assert "resolution_reconciliations.kind = daily_actuals.kind" in sql
    assert "'exact_source_mismatch'" in sql
    assert "'clear_mismatch'" in sql
    assert "'exact_truth_gap'" in sql
    assert "'truth_unavailable'" in sql
    assert "'boundary_sensitive'" not in sql
    assert "'match'" not in sql
    assert "settlement_actuals" not in sql
    assert "LEFT OUTER JOIN resolution_reconciliations" in sql
    assert "markets.status = 'resolved'" in sql
    assert "markets.resolved_outcome IS NOT NULL" in sql
    assert "resolution_reconciliations.id IS NULL" in sql


def test_predicate_composes_with_an_aliased_daily_actual() -> None:
    actual = aliased(DailyActual, name="candidate_actual")
    statement = select(actual.station_id).where(
        daily_actual_fallback_eligible(
            station_id=actual.station_id,
            actual_date=actual.date,
            kind=actual.kind,
        )
    )

    sql = _compiled_sql(statement)
    subquery = sql.split("EXISTS", maxsplit=1)[1]

    assert "candidate_actual.station_id" in subquery
    assert "candidate_actual.date" in subquery
    assert "candidate_actual.kind" in subquery
    assert "FROM resolution_reconciliations" in subquery
    assert "FROM resolution_reconciliations, daily_actuals" not in subquery
