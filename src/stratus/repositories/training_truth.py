from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.sql.elements import ColumnElement, SQLColumnExpression
from sqlalchemy.sql.selectable import Subquery

from stratus.db.models import (
    OfficialResolutionExactActual,
    SettlementActual,
    WeatherKind,
)


def _eligible_settlement_actual_condition(*, as_of: datetime) -> ColumnElement[bool]:
    return and_(
        SettlementActual.is_final.is_(True),
        or_(
            and_(
                SettlementActual.official_exact_actual_id.is_(None),
                SettlementActual.retrieved_at <= as_of,
            ),
            SettlementActual.official_exact_actual_id.in_(
                select(OfficialResolutionExactActual.id).where(
                    OfficialResolutionExactActual.extracted_at <= as_of
                )
            ),
        ),
    )


def eligible_settlement_actual_exists(
    *,
    station_id: SQLColumnExpression[int],
    actual_date: SQLColumnExpression[date],
    kind: SQLColumnExpression[WeatherKind],
    as_of: datetime,
) -> ColumnElement[bool]:
    """Return whether any point-in-time exact candidate owns the cell.

    Consumers use this predicate to prevent a conflicting exact-source cell
    (which the canonical projection intentionally omits) from falling through
    to a lower-authority METAR value.
    """

    return (
        select(SettlementActual.id)
        .where(
            SettlementActual.station_id == station_id,
            SettlementActual.date == actual_date,
            SettlementActual.kind == kind,
            _eligible_settlement_actual_condition(as_of=as_of),
        )
        .correlate_except(SettlementActual)
        .exists()
    )


def canonical_settlement_actuals(*, as_of: datetime) -> Subquery:
    """Return at most one agreed, point-in-time exact value per weather cell.

    Several contracts or events may legally resolve from the same
    station/date/kind value.  Joining the projection directly would duplicate
    forecast rows and silently overweight that cell.  This projection collapses
    identical values and excludes a cell entirely when its eligible exact
    records disagree.  Legacy settlement rows are visible from ``retrieved_at``;
    licensed exact rows are visible from their immutable extraction timestamp.
    """

    eligible = (
        select(
            SettlementActual.station_id.label("station_id"),
            SettlementActual.date.label("date"),
            SettlementActual.kind.label("kind"),
            SettlementActual.value_c.label("value_c"),
        )
        .where(
            _eligible_settlement_actual_condition(as_of=as_of),
        )
        .subquery("eligible_settlement_actuals")
    )
    minimum = func.min(eligible.c.value_c)
    maximum = func.max(eligible.c.value_c)
    return (
        select(
            eligible.c.station_id,
            eligible.c.date,
            eligible.c.kind,
            minimum.label("value_c"),
        )
        .group_by(
            eligible.c.station_id,
            eligible.c.date,
            eligible.c.kind,
        )
        .having(minimum == maximum)
        .subquery("canonical_settlement_actuals")
    )


__all__ = [
    "canonical_settlement_actuals",
    "eligible_settlement_actual_exists",
]
