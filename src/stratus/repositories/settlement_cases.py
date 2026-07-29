from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    SettlementCase,
    SettlementCaseClassification,
)


class SettlementCaseIntegrityError(RuntimeError):
    """A settlement lifecycle update conflicts with terminal evidence."""


@dataclass(frozen=True, slots=True)
class SettlementCaseObservation:
    event_id: str
    classification: SettlementCaseClassification
    reason_code: str
    event_resolves_at: datetime
    observed_at: datetime
    source_url: str | None = None
    source_response_sha256: str | None = None
    resolution_event_id: int | None = None
    resolution_vector_hash: str | None = None
    last_error: str | None = None
    details: dict[str, Any] | None = None
    resolved_at: datetime | None = None


def settlement_case_record_hash(value: SettlementCaseObservation) -> str:
    payload = {
        "event_id": value.event_id,
        "classification": value.classification.value,
        "reason_code": value.reason_code,
        "event_resolves_at": value.event_resolves_at.isoformat(),
        "source_url": value.source_url,
        "source_response_sha256": value.source_response_sha256,
        "resolution_event_id": value.resolution_event_id,
        "resolution_vector_hash": value.resolution_vector_hash,
        "last_error": value.last_error,
        "details": value.details or {},
        "resolved_at": value.resolved_at.isoformat() if value.resolved_at else None,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def settlement_case_record_is_valid(row: SettlementCase) -> bool:
    value = SettlementCaseObservation(
        event_id=row.event_id,
        classification=row.classification,
        reason_code=row.reason_code,
        event_resolves_at=row.event_resolves_at,
        observed_at=row.last_checked_at,
        source_url=row.source_url,
        source_response_sha256=row.source_response_sha256,
        resolution_event_id=row.resolution_event_id,
        resolution_vector_hash=row.resolution_vector_hash,
        last_error=row.last_error,
        details=row.details,
        resolved_at=row.resolved_at,
    )
    return row.record_hash == settlement_case_record_hash(value)


async def upsert_settlement_case(
    session: AsyncSession,
    observation: SettlementCaseObservation,
) -> SettlementCase:
    if observation.classification is SettlementCaseClassification.RESOLVED:
        if (
            observation.resolution_event_id is None
            or observation.resolution_vector_hash is None
            or observation.resolved_at is None
        ):
            raise SettlementCaseIntegrityError(
                "resolved settlement case requires immutable outcome evidence"
            )
    elif (
        observation.resolved_at is not None
        or observation.resolution_event_id is not None
        or observation.resolution_vector_hash is not None
    ):
        raise SettlementCaseIntegrityError(
            "non-terminal settlement case cannot carry terminal evidence"
        )

    row = await session.scalar(
        select(SettlementCase)
        .where(SettlementCase.event_id == observation.event_id)
        .with_for_update()
    )
    record_hash = settlement_case_record_hash(observation)
    details = observation.details or {}
    if row is None:
        row = SettlementCase(
            event_id=observation.event_id,
            classification=observation.classification,
            previous_classification=None,
            reason_code=observation.reason_code,
            event_resolves_at=observation.event_resolves_at,
            first_detected_at=observation.observed_at,
            last_checked_at=observation.observed_at,
            classification_changed_at=observation.observed_at,
            attempt_count=1,
            transition_count=1,
            source_url=observation.source_url,
            source_response_sha256=observation.source_response_sha256,
            resolution_event_id=observation.resolution_event_id,
            resolution_vector_hash=observation.resolution_vector_hash,
            last_error=observation.last_error,
            details=details,
            resolved_at=observation.resolved_at,
            record_hash=record_hash,
        )
        session.add(row)
        await session.flush()
        return row

    if not settlement_case_record_is_valid(row):
        raise SettlementCaseIntegrityError(
            f"settlement case {observation.event_id} has an invalid record hash"
        )
    if row.classification is SettlementCaseClassification.RESOLVED:
        if observation.classification is not SettlementCaseClassification.RESOLVED:
            raise SettlementCaseIntegrityError(
                f"resolved settlement case {observation.event_id} cannot regress"
            )
        if (
            row.resolution_event_id != observation.resolution_event_id
            or row.resolution_vector_hash != observation.resolution_vector_hash
        ):
            raise SettlementCaseIntegrityError(
                f"resolved settlement case {observation.event_id} conflicts with evidence"
            )

    changed = row.classification is not observation.classification
    if changed:
        row.previous_classification = row.classification
        row.classification = observation.classification
        row.classification_changed_at = observation.observed_at
        row.transition_count += 1
    row.reason_code = observation.reason_code
    row.event_resolves_at = observation.event_resolves_at
    row.last_checked_at = observation.observed_at
    row.attempt_count += 1
    row.source_url = observation.source_url
    row.source_response_sha256 = observation.source_response_sha256
    row.resolution_event_id = observation.resolution_event_id
    row.resolution_vector_hash = observation.resolution_vector_hash
    row.last_error = observation.last_error
    row.details = details
    row.resolved_at = observation.resolved_at
    row.record_hash = record_hash
    await session.flush()
    return row
