from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    DailyActual,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ResolutionContract,
    ResolutionReconciliation,
    SettlementActual,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.resolution_reconciliation import (
    ResolutionReconciliationValue,
    upsert_resolution_reconciliation,
)
from stratus.services.reconciliation import assess_resolution

logger = logging.getLogger(__name__)
RECONCILIATION_PROTOCOL = "resolution-reconciliation-v1"
DEFAULT_BATCH_SIZE = 1000
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _metar_value_hash(actual: DailyActual) -> str:
    if actual.record_hash is not None:
        if _SHA256.fullmatch(actual.record_hash) is None:
            raise ValueError("daily actual record hash is not canonical")
        return actual.record_hash
    if actual.first_seen_at is not None:
        return _canonical_hash(
            {
                "protocol": "daily-actual-db-attested-v2",
                "station_id": actual.station_id,
                "date": actual.date.isoformat(),
                "kind": actual.kind.value,
                "temp_c": format(actual.temp_c, "f"),
                "first_seen_at": actual.first_seen_at.astimezone(UTC).isoformat(),
            }
        )
    return _canonical_hash(
        {
            "protocol": "daily-actual-v1",
            "station_id": actual.station_id,
            "date": actual.date.isoformat(),
            "kind": actual.kind.value,
            "temp_c": format(actual.temp_c, "f"),
        }
    )


def _exact_value_hash(actual: SettlementActual) -> str:
    return _canonical_hash(
        {
            "protocol": "settlement-actual-v1",
            "settlement_actual_id": actual.id,
            "contract_id": actual.contract_id,
            "station_id": actual.station_id,
            "date": actual.date.isoformat(),
            "kind": actual.kind.value,
            "source_value": format(actual.source_value, "f"),
            "source_unit": actual.source_unit,
            "value_c": format(actual.value_c, "f"),
            "is_final": actual.is_final,
            "source_response_hash": actual.source_response_hash,
        }
    )


async def reconcile_resolutions(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Persist the oldest unchecked/least-recently-checked resolution evidence."""

    settings = settings or get_settings()
    checked_at = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            rows = (
                await session.execute(
                    select(
                        Market,
                        GammaOutcomeEvent,
                        ResolutionReconciliation.checked_at,
                    )
                    .join(
                        GammaOutcomeEvent,
                        GammaOutcomeEvent.id == Market.resolution_event_id,
                    )
                    .outerjoin(
                        ResolutionReconciliation,
                        ResolutionReconciliation.market_id == Market.id,
                    )
                    .where(
                        Market.status == MarketStatus.RESOLVED,
                        Market.resolved_outcome.is_not(None),
                        Market.resolution_event_id.is_not(None),
                    )
                    .order_by(
                        ResolutionReconciliation.checked_at.asc().nullsfirst(),
                        Market.id,
                    )
                    .limit(batch_size)
                )
            ).all()
            if not rows:
                logger.info("resolution reconciliation complete: markets=0")
                return 0

            markets = [market for market, _outcome, _last_checked in rows]
            station_keys = {
                (market.station_id, market.market_date, market.kind)
                for market in markets
                if market.station_id is not None
            }
            event_ids = {market.event_id for market in markets}

            daily_actuals: dict[tuple[int, date, WeatherKind], DailyActual] = {}
            if station_keys:
                actual_rows = (
                    await session.execute(
                        select(DailyActual).where(
                            tuple_(
                                DailyActual.station_id,
                                DailyActual.date,
                                DailyActual.kind,
                            ).in_(station_keys)
                        )
                    )
                ).scalars()
                daily_actuals = {
                    (actual.station_id, actual.date, actual.kind): actual
                    for actual in actual_rows
                }

            contracts = (
                (
                    await session.execute(
                        select(ResolutionContract).where(
                            ResolutionContract.event_id.in_(event_ids)
                        )
                    )
                )
                .scalars()
                .all()
            )
            contracts_by_identity = {
                (contract.event_id, contract.rules_hash): contract
                for contract in contracts
            }
            contract_ids = {contract.id for contract in contracts}
            settlement_actuals: dict[int, SettlementActual] = {}
            if contract_ids:
                exact_rows = (
                    await session.execute(
                        select(SettlementActual).where(
                            SettlementActual.contract_id.in_(contract_ids),
                            SettlementActual.is_final.is_(True),
                        )
                    )
                ).scalars()
                settlement_actuals = {
                    actual.contract_id: actual for actual in exact_rows
                }

            written = 0
            for market, outcome_event, _last_checked in rows:
                rules_hash = hashlib.sha256(
                    market.rules_raw.encode("utf-8")
                ).hexdigest()
                contract = contracts_by_identity.get((market.event_id, rules_hash))
                exact = (
                    settlement_actuals.get(contract.id) if contract is not None else None
                )
                metar = (
                    daily_actuals.get(
                        (market.station_id, market.market_date, market.kind)
                    )
                    if market.station_id is not None
                    else None
                )
                assessment = assess_resolution(
                    resolved_yes=bool(market.resolved_outcome),
                    low_c=market.bucket_low_c,
                    high_c=market.bucket_high_c,
                    metar_temp_c=metar.temp_c if metar is not None else None,
                    exact_temp_c=exact.value_c if exact is not None else None,
                )
                exact_source_hash = (
                    exact.source_response_hash
                    if exact is not None
                    and exact.source_response_hash is not None
                    and _SHA256.fullmatch(exact.source_response_hash)
                    else None
                )
                provenance: dict[str, Any] = {
                    "protocol": RECONCILIATION_PROTOCOL,
                    "gamma_outcome_protocol": outcome_event.evidence_protocol,
                    "contract_id": contract.id if contract is not None else None,
                    "contract_rules_match": contract is not None,
                    "exact_truth_available": exact is not None,
                    "exact_source_hash_valid": (
                        exact is None
                        or exact.source_response_hash is None
                        or exact_source_hash is not None
                    ),
                    "metar_truth_available": metar is not None,
                    "metar_source_protocol": (
                        metar.source_protocol if metar is not None else None
                    ),
                    "metar_recovery_artifact_id": (
                        metar.recovery_artifact_id if metar is not None else None
                    ),
                    "metar_first_seen_at": (
                        metar.first_seen_at.astimezone(UTC).isoformat()
                        if metar is not None and metar.first_seen_at is not None
                        else None
                    ),
                    "metar_daily_actual_record_hash": (
                        metar.record_hash if metar is not None else None
                    ),
                }
                value = ResolutionReconciliationValue(
                    market_id=market.id,
                    event_id=market.event_id,
                    station_id=market.station_id,
                    market_date=market.market_date,
                    kind=market.kind,
                    resolution_event_id=outcome_event.id,
                    settlement_actual_id=exact.id if exact is not None else None,
                    classification=assessment.classification,
                    resolved_yes=bool(market.resolved_outcome),
                    metar_temp_c=metar.temp_c if metar is not None else None,
                    metar_implied_yes=assessment.metar_implied_yes,
                    exact_temp_c=exact.value_c if exact is not None else None,
                    exact_implied_yes=assessment.exact_implied_yes,
                    boundary_margin_c=assessment.boundary_margin_c,
                    exact_truth_gap_c=assessment.exact_truth_gap_c,
                    rules_hash=rules_hash,
                    resolution_vector_hash=outcome_event.outcome_vector_hash,
                    metar_value_hash=(
                        _metar_value_hash(metar)
                        if metar is not None
                        else None
                    ),
                    exact_value_hash=(
                        _exact_value_hash(exact) if exact is not None else None
                    ),
                    exact_source_hash=exact_source_hash,
                    checked_at=checked_at,
                    provenance=provenance,
                )
                await upsert_resolution_reconciliation(session, value)
                written += 1

        logger.info("resolution reconciliation complete: markets=%d", written)
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(reconcile_resolutions(settings))


if __name__ == "__main__":
    main()
