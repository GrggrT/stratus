from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from stratus.config import Settings
from stratus.db.models import (
    ResolutionReconciliationClassification,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    WeatherKind,
)

STATION_RELIABILITY_PROTOCOL = "station-reliability-v1"

_CONFLICT_CLASSES = frozenset(
    {
        ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH,
        ResolutionReconciliationClassification.CLEAR_MISMATCH,
        ResolutionReconciliationClassification.EXACT_TRUTH_GAP,
    }
)
_CLASS_PRIORITY = {
    ResolutionReconciliationClassification.TRUTH_UNAVAILABLE: 0,
    ResolutionReconciliationClassification.MATCH: 1,
    ResolutionReconciliationClassification.BOUNDARY_SENSITIVE: 2,
    ResolutionReconciliationClassification.EXACT_TRUTH_GAP: 3,
    ResolutionReconciliationClassification.CLEAR_MISMATCH: 4,
    ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH: 5,
}


@dataclass(frozen=True, slots=True)
class StationReconciliationEvidence:
    station_id: int
    market_date: date
    kind: WeatherKind
    classification: ResolutionReconciliationClassification
    checked_at: datetime
    record_hash: str


@dataclass(frozen=True, slots=True)
class StationReliabilityUnit:
    station_id: int
    market_date: date
    kind: WeatherKind
    classification: ResolutionReconciliationClassification
    checked_at: datetime
    record_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StationReliabilityAssessment:
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
    observation_local_date: date
    observation_cooldown_until: datetime | None
    reasons: tuple[str, ...]


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def station_reliability_policy_hash(settings: Settings) -> str:
    """Hash only the settings that can change station selection semantics."""

    return _canonical_hash(
        {
            "protocol": STATION_RELIABILITY_PROTOCOL,
            "window_days": settings.station_reliability_window_days,
            "min_samples": settings.station_reliability_min_samples,
            "min_lower_bound": settings.station_reliability_min_lower_bound,
            "resolution_cooldown_hours": settings.station_resolution_cooldown_hours,
            "observation_stale_minutes": settings.station_observation_stale_minutes,
            "snapshot_max_age_minutes": (settings.station_reliability_snapshot_max_age_minutes),
        }
    )


def reduce_station_reconciliation_units(
    rows: Iterable[StationReconciliationEvidence],
) -> tuple[StationReliabilityUnit, ...]:
    """Collapse correlated event buckets to independent station/day/kind units."""

    grouped: dict[tuple[int, date, WeatherKind], list[StationReconciliationEvidence]] = {}
    for row in rows:
        grouped.setdefault((row.station_id, row.market_date, row.kind), []).append(row)

    units: list[StationReliabilityUnit] = []
    for (station_id, market_date, kind), evidence in sorted(
        grouped.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2].value),
    ):
        decisive = max(
            evidence,
            key=lambda item: (_CLASS_PRIORITY[item.classification], item.checked_at),
        )
        units.append(
            StationReliabilityUnit(
                station_id=station_id,
                market_date=market_date,
                kind=kind,
                classification=decisive.classification,
                checked_at=decisive.checked_at,
                record_hashes=tuple(sorted({item.record_hash for item in evidence})),
            )
        )
    return tuple(units)


def wilson_lower_bound(successes: int, total: int, *, z: float = 1.959963984540054) -> float | None:
    if total <= 0:
        return None
    if successes < 0 or successes > total:
        raise ValueError("successes must be within [0, total]")
    proportion = successes / total
    denominator = 1 + z * z / total
    centre = proportion + z * z / (2 * total)
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denominator)


def _conflict_anchor(
    market_date: date,
    *,
    station_timezone: str,
    discovered_at: datetime,
) -> datetime:
    local_day_end = datetime.combine(
        market_date + timedelta(days=1),
        time.min,
        tzinfo=ZoneInfo(station_timezone),
    )
    return max(local_day_end.astimezone(UTC), discovered_at.astimezone(UTC))


def assess_station_reliability(
    units: Iterable[StationReliabilityUnit],
    *,
    now: datetime,
    station_timezone: str,
    latest_observation_at: datetime | None,
    min_samples: int,
    min_lower_bound: float,
    resolution_cooldown: timedelta,
    observation_stale_after: timedelta,
    previous_snapshot: StationReliabilitySnapshot | None = None,
) -> StationReliabilityAssessment:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if latest_observation_at is not None and (
        latest_observation_at.tzinfo is None or latest_observation_at.utcoffset() is None
    ):
        raise ValueError("latest_observation_at must be timezone-aware")

    unit_rows = tuple(units)
    counts = {
        classification: sum(item.classification is classification for item in unit_rows)
        for classification in ResolutionReconciliationClassification
    }
    matches = counts[ResolutionReconciliationClassification.MATCH]
    exact_source_mismatches = counts[ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH]
    clear_mismatches = counts[ResolutionReconciliationClassification.CLEAR_MISMATCH]
    exact_truth_gaps = counts[ResolutionReconciliationClassification.EXACT_TRUTH_GAP]
    conflicts = exact_source_mismatches + clear_mismatches + exact_truth_gaps
    comparable = matches + conflicts
    boundaries = counts[ResolutionReconciliationClassification.BOUNDARY_SENSITIVE]
    unavailable = counts[ResolutionReconciliationClassification.TRUTH_UNAVAILABLE]

    score = matches / comparable if comparable else None
    lower = wilson_lower_bound(matches, comparable)

    conflict_units = [item for item in unit_rows if item.classification in _CONFLICT_CLASSES]
    latest_conflict = max(
        conflict_units,
        key=lambda item: (item.market_date, item.kind.value),
        default=None,
    )
    latest_conflict_at: datetime | None = None
    if latest_conflict is not None:
        same_attested_conflict = bool(
            previous_snapshot is not None
            and previous_snapshot.latest_conflict_at is not None
            and previous_snapshot.latest_conflict_date == latest_conflict.market_date
            and previous_snapshot.latest_conflict_kind == latest_conflict.kind
            and previous_snapshot.conflict_units >= conflicts
        )
        # checked_at is a polling heartbeat on the reconciliation projection.
        # Preserve the first attested anchor while the conflict set is stable;
        # only a newly learned conflict starts a fresh cooldown.
        latest_conflict_at = (
            previous_snapshot.latest_conflict_at
            if same_attested_conflict and previous_snapshot is not None
            else _conflict_anchor(
                latest_conflict.market_date,
                station_timezone=station_timezone,
                discovered_at=min(latest_conflict.checked_at, now),
            )
        )
    resolution_cooldown_until = (
        latest_conflict_at + resolution_cooldown if latest_conflict_at is not None else None
    )
    resolution_blocked = bool(
        resolution_cooldown_until is not None and resolution_cooldown_until > now
    )

    observation_local_date = now.astimezone(ZoneInfo(station_timezone)).date()
    observation_reason: str | None = None
    if latest_observation_at is None:
        observation_reason = "station_observation_missing"
    elif latest_observation_at > now + timedelta(minutes=5):
        observation_reason = "station_observation_clock_skew"
    elif latest_observation_at.astimezone(ZoneInfo(station_timezone)).date() != (
        observation_local_date
    ):
        observation_reason = "station_observation_previous_local_date"
    elif now - latest_observation_at > observation_stale_after:
        observation_reason = "station_observation_stale"
    observation_cooldown_until = (
        now + observation_stale_after if observation_reason is not None else None
    )

    reasons: list[str] = []
    if resolution_blocked:
        reasons.append("station_resolution_conflict")
    if observation_reason is not None:
        reasons.append(observation_reason)
    if comparable >= min_samples and lower is not None and lower < min_lower_bound:
        reasons.append("station_reliability_below_threshold")
    elif comparable < min_samples:
        reasons.append("station_reliability_collecting")

    if resolution_blocked or observation_reason is not None:
        status = StationReliabilityStatus.BLOCKED
    elif comparable < min_samples:
        status = StationReliabilityStatus.COLLECTING
    elif lower is not None and lower < min_lower_bound:
        status = StationReliabilityStatus.WATCH
    else:
        status = StationReliabilityStatus.RELIABLE

    return StationReliabilityAssessment(
        comparable_units=comparable,
        match_units=matches,
        conflict_units=conflicts,
        exact_source_mismatch_units=exact_source_mismatches,
        clear_mismatch_units=clear_mismatches,
        exact_truth_gap_units=exact_truth_gaps,
        boundary_units=boundaries,
        unavailable_units=unavailable,
        reliability_score=(
            Decimal(str(score)).quantize(Decimal("0.000001")) if score is not None else None
        ),
        reliability_lower_bound=(
            Decimal(str(lower)).quantize(Decimal("0.000001")) if lower is not None else None
        ),
        status=status,
        latest_conflict_at=latest_conflict_at,
        latest_conflict_date=(latest_conflict.market_date if latest_conflict else None),
        latest_conflict_kind=(latest_conflict.kind if latest_conflict else None),
        resolution_cooldown_until=resolution_cooldown_until,
        observation_local_date=observation_local_date,
        observation_cooldown_until=observation_cooldown_until,
        reasons=tuple(reasons),
    )


def station_signal_gate_reasons(
    snapshot: StationReliabilitySnapshot | None,
    *,
    market_date: date,
    now: datetime,
    expected_policy_hash: str,
    max_age: timedelta,
    observation_stale_after: timedelta,
    station_timezone: str,
    require_reliable: bool = False,
) -> tuple[str, ...]:
    """Return stable fail-closed reasons for one station-market decision."""

    if snapshot is None:
        return ("station_reliability_snapshot_missing",)
    if snapshot.policy_hash != expected_policy_hash:
        return ("station_reliability_policy_mismatch",)
    if snapshot.evaluated_at > now + timedelta(minutes=5):
        return ("station_reliability_snapshot_clock_skew",)
    if now - snapshot.evaluated_at > max_age:
        return ("station_reliability_snapshot_stale",)

    current_local_date = now.astimezone(ZoneInfo(station_timezone)).date()
    if snapshot.observation_local_date != current_local_date:
        return ("station_reliability_snapshot_local_date_stale",)

    reasons: list[str] = []
    if snapshot.resolution_cooldown_until is not None and snapshot.resolution_cooldown_until > now:
        reasons.append("station_resolution_conflict")
    if "station_reliability_below_threshold" in snapshot.reasons:
        reasons.append("station_reliability_below_threshold")
    if require_reliable and snapshot.status is not StationReliabilityStatus.RELIABLE:
        if snapshot.status is StationReliabilityStatus.COLLECTING:
            reasons.append("station_reliability_collecting")
        elif not reasons:
            reasons.append("station_reliability_not_reliable")
    if market_date == current_local_date:
        observation_reason: str | None = None
        if snapshot.latest_observation_at is None:
            observation_reason = "station_observation_missing"
        elif snapshot.latest_observation_at > now + timedelta(minutes=5):
            observation_reason = "station_observation_clock_skew"
        elif snapshot.latest_observation_at.astimezone(ZoneInfo(station_timezone)).date() != (
            current_local_date
        ):
            observation_reason = "station_observation_previous_local_date"
        elif now - snapshot.latest_observation_at > observation_stale_after:
            observation_reason = "station_observation_stale"
        elif (
            snapshot.observation_cooldown_until is not None
            and snapshot.observation_cooldown_until > now
        ):
            observation_reason = next(
                (
                    reason
                    for reason in snapshot.reasons
                    if reason
                    in {
                        "station_observation_missing",
                        "station_observation_stale",
                        "station_observation_clock_skew",
                        "station_observation_previous_local_date",
                    }
                ),
                "station_observation_stale",
            )
        if observation_reason is not None:
            reasons.append(observation_reason)
    return tuple(dict.fromkeys(reasons))


def station_reliability_source_hash(
    units: Iterable[StationReliabilityUnit],
    *,
    latest_observation_id: int | None,
    latest_observation_at: datetime | None,
    latest_observation_payload_hash: str | None,
) -> str:
    return _canonical_hash(
        {
            "protocol": STATION_RELIABILITY_PROTOCOL,
            "units": [
                {
                    "station_id": item.station_id,
                    "market_date": item.market_date.isoformat(),
                    "kind": item.kind.value,
                    "classification": item.classification.value,
                    "record_hashes": item.record_hashes,
                }
                for item in units
            ],
            "observation": {
                "id": latest_observation_id,
                "at": latest_observation_at.isoformat()
                if latest_observation_at is not None
                else None,
                "payload_hash": latest_observation_payload_hash,
            },
        }
    )


__all__ = [
    "STATION_RELIABILITY_PROTOCOL",
    "StationReconciliationEvidence",
    "StationReliabilityAssessment",
    "StationReliabilityUnit",
    "assess_station_reliability",
    "reduce_station_reconciliation_units",
    "station_reliability_policy_hash",
    "station_reliability_source_hash",
    "station_signal_gate_reasons",
    "wilson_lower_bound",
]
