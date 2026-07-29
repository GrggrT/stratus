from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.config import Settings
from stratus.db.models import (
    Market,
    Signal,
    Station,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    WeatherKind,
)
from stratus.services.station_reliability import (
    station_reliability_policy_hash,
    station_signal_gate_reasons,
)


class StationReliabilityIntegrityError(RuntimeError):
    """Station evidence or its immutable provenance failed attestation."""


@dataclass(frozen=True, slots=True)
class StationReliabilitySnapshotValue:
    station_id: int
    evaluated_at: datetime
    window_days: int
    comparable_units: int
    match_units: int
    conflict_units: int
    exact_source_mismatch_units: int
    clear_mismatch_units: int
    exact_truth_gap_units: int
    boundary_units: int
    unavailable_units: int
    reliability_score: Decimal | None
    reliability_lower_bound: Decimal | None
    status: StationReliabilityStatus
    latest_conflict_at: datetime | None
    latest_conflict_date: date | None
    latest_conflict_kind: WeatherKind | None
    resolution_cooldown_until: datetime | None
    latest_observation_at: datetime | None
    observation_local_date: date
    observation_cooldown_until: datetime | None
    reasons: tuple[str, ...]
    policy_hash: str
    source_hash: str


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _decimal(value: Decimal | None) -> str | None:
    return format(value.normalize(), "f") if value is not None else None


def station_reliability_record_hash(value: StationReliabilitySnapshotValue) -> str:
    payload = {
        "station_id": value.station_id,
        "evaluated_at": value.evaluated_at.isoformat(),
        "window_days": value.window_days,
        "comparable_units": value.comparable_units,
        "match_units": value.match_units,
        "conflict_units": value.conflict_units,
        "exact_source_mismatch_units": value.exact_source_mismatch_units,
        "clear_mismatch_units": value.clear_mismatch_units,
        "exact_truth_gap_units": value.exact_truth_gap_units,
        "boundary_units": value.boundary_units,
        "unavailable_units": value.unavailable_units,
        "reliability_score": _decimal(value.reliability_score),
        "reliability_lower_bound": _decimal(value.reliability_lower_bound),
        "status": value.status.value,
        "latest_conflict_at": (
            value.latest_conflict_at.isoformat() if value.latest_conflict_at is not None else None
        ),
        "latest_conflict_date": (
            value.latest_conflict_date.isoformat()
            if value.latest_conflict_date is not None
            else None
        ),
        "latest_conflict_kind": (
            value.latest_conflict_kind.value if value.latest_conflict_kind is not None else None
        ),
        "resolution_cooldown_until": (
            value.resolution_cooldown_until.isoformat()
            if value.resolution_cooldown_until is not None
            else None
        ),
        "latest_observation_at": (
            value.latest_observation_at.isoformat()
            if value.latest_observation_at is not None
            else None
        ),
        "observation_local_date": value.observation_local_date.isoformat(),
        "observation_cooldown_until": (
            value.observation_cooldown_until.isoformat()
            if value.observation_cooldown_until is not None
            else None
        ),
        "reasons": value.reasons,
        "policy_hash": value.policy_hash,
        "source_hash": value.source_hash,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def station_reliability_record_is_valid(row: StationReliabilitySnapshot) -> bool:
    value = StationReliabilitySnapshotValue(
        station_id=row.station_id,
        evaluated_at=row.evaluated_at,
        window_days=row.window_days,
        comparable_units=row.comparable_units,
        match_units=row.match_units,
        conflict_units=row.conflict_units,
        exact_source_mismatch_units=row.exact_source_mismatch_units,
        clear_mismatch_units=row.clear_mismatch_units,
        exact_truth_gap_units=row.exact_truth_gap_units,
        boundary_units=row.boundary_units,
        unavailable_units=row.unavailable_units,
        reliability_score=row.reliability_score,
        reliability_lower_bound=row.reliability_lower_bound,
        status=row.status,
        latest_conflict_at=row.latest_conflict_at,
        latest_conflict_date=row.latest_conflict_date,
        latest_conflict_kind=row.latest_conflict_kind,
        resolution_cooldown_until=row.resolution_cooldown_until,
        latest_observation_at=row.latest_observation_at,
        observation_local_date=row.observation_local_date,
        observation_cooldown_until=row.observation_cooldown_until,
        reasons=tuple(row.reasons),
        policy_hash=row.policy_hash,
        source_hash=row.source_hash,
    )
    return row.record_hash == station_reliability_record_hash(value)


async def insert_station_reliability_snapshot(
    session: AsyncSession,
    value: StationReliabilitySnapshotValue,
) -> StationReliabilitySnapshot:
    fields = asdict(value)
    fields["reasons"] = list(value.reasons)
    row = StationReliabilitySnapshot(
        **fields,
        record_hash=station_reliability_record_hash(value),
    )
    session.add(row)
    await session.flush()
    return row


async def latest_station_reliability_by_station(
    session: AsyncSession,
    station_ids: list[int] | tuple[int, ...],
) -> dict[int, StationReliabilitySnapshot]:
    if not station_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(StationReliabilitySnapshot)
                .where(StationReliabilitySnapshot.station_id.in_(station_ids))
                .distinct(StationReliabilitySnapshot.station_id)
                .order_by(
                    StationReliabilitySnapshot.station_id,
                    StationReliabilitySnapshot.evaluated_at.desc(),
                    StationReliabilitySnapshot.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.station_id: row for row in rows}


async def acquire_station_reliability_gate_lock(session: AsyncSession) -> None:
    """Serialize snapshot publication with order admission/fill rechecks."""

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('stratus:station-reliability-gate-v1', 0))"
        )
    )


def signal_station_reliability_gate_reasons(
    *,
    signal: Signal,
    market: Market,
    station: Station,
    attested_snapshot: StationReliabilitySnapshot | None,
    current_snapshot: StationReliabilitySnapshot | None,
    settings: Settings,
    now: datetime,
    require_reliable: bool,
) -> tuple[str, ...]:
    """Re-attest decision-time and current station evidence before an order.

    The signal FK makes the original decision reproducible.  The current
    snapshot closes the generation/execution race when a conflict or stale
    observation is learned after the signal was accepted.
    """

    if signal.station_reliability_snapshot_id is None:
        return ("station_reliability_provenance_missing",)
    if attested_snapshot is None:
        return ("station_reliability_provenance_unavailable",)
    if (
        attested_snapshot.id != signal.station_reliability_snapshot_id
        or attested_snapshot.station_id != station.id
        or market.station_id != station.id
    ):
        return ("station_reliability_provenance_mismatch",)
    if not station_reliability_record_is_valid(attested_snapshot):
        return ("station_reliability_evidence_invalid",)
    if current_snapshot is None:
        return ("station_reliability_snapshot_missing",)
    if current_snapshot.station_id != station.id:
        return ("station_reliability_snapshot_station_mismatch",)
    if not station_reliability_record_is_valid(current_snapshot):
        return ("station_reliability_evidence_invalid",)

    expected_policy_hash = station_reliability_policy_hash(settings)
    max_age = timedelta(minutes=settings.station_reliability_snapshot_max_age_minutes)
    observation_stale_after = timedelta(
        minutes=settings.station_observation_stale_minutes
    )
    decision_time = max(signal.ts, attested_snapshot.evaluated_at)
    reasons = [
        *station_signal_gate_reasons(
            attested_snapshot,
            market_date=market.market_date,
            now=decision_time,
            expected_policy_hash=expected_policy_hash,
            max_age=max_age,
            observation_stale_after=observation_stale_after,
            station_timezone=station.tz,
            require_reliable=require_reliable,
        ),
        *station_signal_gate_reasons(
            current_snapshot,
            market_date=market.market_date,
            now=now,
            expected_policy_hash=expected_policy_hash,
            max_age=max_age,
            observation_stale_after=observation_stale_after,
            station_timezone=station.tz,
            require_reliable=require_reliable,
        ),
    ]
    return tuple(dict.fromkeys(reasons))


__all__ = [
    "StationReliabilitySnapshotValue",
    "StationReliabilityIntegrityError",
    "acquire_station_reliability_gate_lock",
    "insert_station_reliability_snapshot",
    "latest_station_reliability_by_station",
    "signal_station_reliability_gate_reasons",
    "station_reliability_record_hash",
    "station_reliability_record_is_valid",
]
