from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ResolutionReconciliation,
    ResolutionReconciliationClassification,
    WeatherKind,
)


class ResolutionReconciliationIntegrityError(RuntimeError):
    """A durable reconciliation conflicts with its attested venue outcome."""


@dataclass(frozen=True, slots=True)
class ResolutionReconciliationValue:
    market_id: int
    event_id: str
    station_id: int | None
    market_date: date
    kind: WeatherKind
    resolution_event_id: int
    settlement_actual_id: int | None
    classification: ResolutionReconciliationClassification
    resolved_yes: bool
    metar_temp_c: Decimal | None
    metar_implied_yes: bool | None
    exact_temp_c: Decimal | None
    exact_implied_yes: bool | None
    boundary_margin_c: Decimal | None
    exact_truth_gap_c: Decimal | None
    rules_hash: str
    resolution_vector_hash: str
    metar_value_hash: str | None
    exact_value_hash: str | None
    exact_source_hash: str | None
    checked_at: datetime
    provenance: dict[str, Any]


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decimal(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def resolution_reconciliation_record_hash(
    value: ResolutionReconciliationValue,
) -> str:
    payload = {
        "market_id": value.market_id,
        "event_id": value.event_id,
        "station_id": value.station_id,
        "market_date": value.market_date.isoformat(),
        "kind": value.kind.value,
        "resolution_event_id": value.resolution_event_id,
        "settlement_actual_id": value.settlement_actual_id,
        "classification": value.classification.value,
        "resolved_yes": value.resolved_yes,
        "metar_temp_c": _decimal(value.metar_temp_c),
        "metar_implied_yes": value.metar_implied_yes,
        "exact_temp_c": _decimal(value.exact_temp_c),
        "exact_implied_yes": value.exact_implied_yes,
        "boundary_margin_c": _decimal(value.boundary_margin_c),
        "exact_truth_gap_c": _decimal(value.exact_truth_gap_c),
        "rules_hash": value.rules_hash,
        "resolution_vector_hash": value.resolution_vector_hash,
        "metar_value_hash": value.metar_value_hash,
        "exact_value_hash": value.exact_value_hash,
        "exact_source_hash": value.exact_source_hash,
        "provenance": value.provenance,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def resolution_reconciliation_record_is_valid(
    row: ResolutionReconciliation,
) -> bool:
    value = ResolutionReconciliationValue(
        market_id=row.market_id,
        event_id=row.event_id,
        station_id=row.station_id,
        market_date=row.market_date,
        kind=row.kind,
        resolution_event_id=row.resolution_event_id,
        settlement_actual_id=row.settlement_actual_id,
        classification=row.classification,
        resolved_yes=row.resolved_yes,
        metar_temp_c=row.metar_temp_c,
        metar_implied_yes=row.metar_implied_yes,
        exact_temp_c=row.exact_temp_c,
        exact_implied_yes=row.exact_implied_yes,
        boundary_margin_c=row.boundary_margin_c,
        exact_truth_gap_c=row.exact_truth_gap_c,
        rules_hash=row.rules_hash,
        resolution_vector_hash=row.resolution_vector_hash,
        metar_value_hash=row.metar_value_hash,
        exact_value_hash=row.exact_value_hash,
        exact_source_hash=row.exact_source_hash,
        checked_at=row.checked_at,
        provenance=row.provenance,
    )
    return row.record_hash == resolution_reconciliation_record_hash(value)


async def upsert_resolution_reconciliation(
    session: AsyncSession,
    value: ResolutionReconciliationValue,
) -> ResolutionReconciliation:
    row = await session.scalar(
        select(ResolutionReconciliation)
        .where(ResolutionReconciliation.market_id == value.market_id)
        .with_for_update()
    )
    record_hash = resolution_reconciliation_record_hash(value)
    if row is None:
        row = ResolutionReconciliation(
            **asdict(value),
            record_hash=record_hash,
        )
        session.add(row)
        await session.flush()
        return row

    if not resolution_reconciliation_record_is_valid(row):
        raise ResolutionReconciliationIntegrityError(
            f"market {value.market_id} has an invalid reconciliation record hash"
        )
    if (
        row.event_id != value.event_id
        or row.resolution_event_id != value.resolution_event_id
        or row.resolution_vector_hash != value.resolution_vector_hash
        or row.resolved_yes != value.resolved_yes
    ):
        raise ResolutionReconciliationIntegrityError(
            f"market {value.market_id} conflicts with its attested resolution"
        )

    for field in fields(value):
        setattr(row, field.name, getattr(value, field.name))
    row.record_hash = record_hash
    await session.flush()
    return row
