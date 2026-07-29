from __future__ import annotations

from datetime import date

from sqlalchemy import and_, not_, select
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression

from stratus.db.models import (
    Market,
    MarketStatus,
    ResolutionReconciliation,
    ResolutionReconciliationClassification,
    WeatherKind,
)

_BLOCKING_RECONCILIATION_CLASSIFICATIONS = (
    ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH,
    ResolutionReconciliationClassification.CLEAR_MISMATCH,
    ResolutionReconciliationClassification.EXACT_TRUTH_GAP,
    ResolutionReconciliationClassification.TRUTH_UNAVAILABLE,
)


def daily_actual_fallback_eligible(
    *,
    station_id: SQLColumnExpression[int],
    actual_date: SQLColumnExpression[date],
    kind: SQLColumnExpression[WeatherKind],
) -> ColumnElement[bool]:
    """Return a fail-closed predicate for selecting METAR-derived truth.

    The caller supplies the station/date/kind expressions from its outer
    statement.  SQLAlchemy correlates those expressions while keeping the
    reconciliation table local to the ``NOT EXISTS`` subquery, so the result
    composes with ORM entities as well as aliases.

    Boundary-sensitive evidence remains eligible: it is review-worthy but is
    not proof that the DailyActual is false.  A resolved market cell is denied
    until every attested market has a reconciliation projection, so a delayed
    projector cannot silently admit an unchecked label.  Cells with no resolved
    venue market remain eligible for legacy climatology.  Whether an exact
    SettlementActual supersedes the fallback is deliberately left to the
    consumer.
    """

    blocking_reconciliation_exists = (
        select(ResolutionReconciliation.id)
        .where(
            ResolutionReconciliation.station_id == station_id,
            ResolutionReconciliation.market_date == actual_date,
            ResolutionReconciliation.kind == kind,
            ResolutionReconciliation.classification.in_(
                _BLOCKING_RECONCILIATION_CLASSIFICATIONS
            ),
        )
        .correlate_except(ResolutionReconciliation)
        .exists()
    )
    unresolved_projection_exists = (
        select(Market.id)
        .outerjoin(
            ResolutionReconciliation,
            ResolutionReconciliation.market_id == Market.id,
        )
        .where(
            Market.station_id == station_id,
            Market.market_date == actual_date,
            Market.kind == kind,
            Market.status == MarketStatus.RESOLVED,
            Market.resolved_outcome.is_not(None),
            ResolutionReconciliation.id.is_(None),
        )
        .correlate_except(Market, ResolutionReconciliation)
        .exists()
    )
    return and_(
        not_(blocking_reconciliation_exists),
        not_(unresolved_projection_exists),
    )


__all__ = ["daily_actual_fallback_eligible"]
