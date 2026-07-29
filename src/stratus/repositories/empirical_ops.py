from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ResolutionReconciliation,
    SettlementCase,
    SettlementCaseClassification,
)

RECONCILIATION_STALE_AFTER_SECONDS = 2 * 60 * 60
SETTLEMENT_STALE_AFTER_SECONDS = 30 * 60


@dataclass(frozen=True, slots=True)
class ResolutionReconciliationSummary:
    """Unfiltered evidence coverage and freshness for durable reconciliations."""

    total: int
    metar_available: int
    metar_consistent: int
    exact_available: int
    exact_source_hash_available: int
    exact_coverage: float
    latest_checked_at: datetime | None
    oldest_checked_at: datetime | None
    stale_records: int
    fresh: bool
    stale_after_seconds: int = RECONCILIATION_STALE_AFTER_SECONDS


@dataclass(frozen=True, slots=True)
class SettlementCasesSummary:
    """Unfiltered lifecycle and poll freshness for durable settlement cases."""

    total: int
    open_total: int
    oldest_open_at: datetime | None
    latest_checked_at: datetime | None
    stale_open_cases: int
    fresh: bool
    stale_after_seconds: int = SETTLEMENT_STALE_AFTER_SECONDS


async def resolution_reconciliation_summary(
    session: AsyncSession,
    *,
    now: datetime,
) -> ResolutionReconciliationSummary:
    """Summarize every reconciliation row, independent of API item filters."""

    stale_before = now - timedelta(seconds=RECONCILIATION_STALE_AFTER_SECONDS)
    (
        total_value,
        metar_available_value,
        metar_consistent_value,
        exact_available_value,
        exact_source_hash_available_value,
        latest_checked_at,
        oldest_checked_at,
        stale_records_value,
    ) = (
        await session.execute(
            select(
                func.count(ResolutionReconciliation.id),
                func.count(ResolutionReconciliation.id).filter(
                    ResolutionReconciliation.metar_temp_c.is_not(None)
                ),
                func.count(ResolutionReconciliation.id).filter(
                    and_(
                        ResolutionReconciliation.metar_temp_c.is_not(None),
                        ResolutionReconciliation.metar_implied_yes.is_not(None),
                        ResolutionReconciliation.metar_implied_yes
                        == ResolutionReconciliation.resolved_yes,
                    )
                ),
                func.count(ResolutionReconciliation.id).filter(
                    ResolutionReconciliation.settlement_actual_id.is_not(None)
                ),
                func.count(ResolutionReconciliation.id).filter(
                    and_(
                        ResolutionReconciliation.settlement_actual_id.is_not(None),
                        ResolutionReconciliation.exact_source_hash.is_not(None),
                    )
                ),
                func.max(ResolutionReconciliation.checked_at),
                func.min(ResolutionReconciliation.checked_at),
                func.count(ResolutionReconciliation.id).filter(
                    ResolutionReconciliation.checked_at < stale_before
                ),
            )
        )
    ).one()
    total = int(total_value or 0)
    metar_available = int(metar_available_value or 0)
    metar_consistent = int(metar_consistent_value or 0)
    exact_available = int(exact_available_value or 0)
    exact_source_hash_available = int(exact_source_hash_available_value or 0)
    stale_records = int(stale_records_value or 0)
    exact_coverage = (
        min(1.0, max(0.0, exact_source_hash_available / total)) if total else 0.0
    )
    return ResolutionReconciliationSummary(
        total=total,
        metar_available=metar_available,
        metar_consistent=metar_consistent,
        exact_available=exact_available,
        exact_source_hash_available=exact_source_hash_available,
        exact_coverage=exact_coverage,
        latest_checked_at=latest_checked_at,
        oldest_checked_at=oldest_checked_at,
        stale_records=stale_records,
        # No records means there is no positive evidence of reconciliation freshness.
        fresh=total > 0 and stale_records == 0,
    )


async def settlement_cases_summary(
    session: AsyncSession,
    *,
    now: datetime,
) -> SettlementCasesSummary:
    """Summarize every case while applying freshness only to non-terminal cases."""

    stale_before = now - timedelta(seconds=SETTLEMENT_STALE_AFTER_SECONDS)
    open_condition = SettlementCase.classification != SettlementCaseClassification.RESOLVED
    (
        total_value,
        open_total_value,
        oldest_open_at,
        latest_checked_at,
        stale_open_cases_value,
    ) = (
        await session.execute(
            select(
                func.count(SettlementCase.id),
                func.count(SettlementCase.id).filter(open_condition),
                func.min(SettlementCase.first_detected_at).filter(open_condition),
                func.max(SettlementCase.last_checked_at),
                func.count(SettlementCase.id).filter(
                    and_(
                        open_condition,
                        SettlementCase.last_checked_at < stale_before,
                    )
                ),
            )
        )
    ).one()
    total = int(total_value or 0)
    open_total = int(open_total_value or 0)
    stale_open_cases = int(stale_open_cases_value or 0)
    return SettlementCasesSummary(
        total=total,
        open_total=open_total,
        oldest_open_at=oldest_open_at,
        latest_checked_at=latest_checked_at,
        stale_open_cases=stale_open_cases,
        # A terminal-only or empty case table has no pending poll obligation.
        fresh=stale_open_cases == 0,
    )
