from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    DailyActual,
    ObservationRecoveryArtifact,
    ObservationRecoveryPoint,
    Station,
    WeatherKind,
)
from stratus.repositories.forecast_provenance import canonical_json_sha256

IEM_ASOS_RECOVERY_PROTOCOL = "iem-asos-recovery-v1"
IEM_CSV_EVIDENCE_PARSER_VERSION = "iem-csv-evidence-v2"
DAILY_EXTREMES_COMPLETENESS_VERSION = "daily-extremes-v1"
IEM_RETENTION_POLICY_VERSION = "iem-retention-review-v1"
IEM_RETENTION_POLICY_URL = "https://mesonet.agron.iastate.edu/disclaimer.php"
IEM_RETENTION_POLICY_STATEMENT = (
    b"iem-disclaimer-public-domain-lawful-use-reviewed-2026-07-17-v1|"
    b"https://mesonet.agron.iastate.edu/disclaimer.php|"
    b"raw-response-retention=true|attribution=Iowa Environmental Mesonet"
)
IEM_RETENTION_POLICY_SHA256 = hashlib.sha256(
    IEM_RETENTION_POLICY_STATEMENT
).hexdigest()
_MIN_SAMPLES = 20
_BOUNDARY_TOLERANCE = timedelta(minutes=90)
_MAX_GAP = timedelta(hours=2)
_TEMP_QUANTUM = Decimal("0.01")


class ObservationRecoveryIntegrityError(RuntimeError):
    """Recovery evidence violates the immutable repository contract."""


@dataclass(frozen=True, slots=True)
class ObservationRecoveryEvidence:
    station_id: int
    local_date: date
    timezone: str
    utc_start: datetime
    utc_end: datetime
    network: str
    source_station_id: str
    source_name: str
    source_protocol: str
    source_url: str
    request_manifest: dict[str, object]
    request_started_at: datetime
    response_received_at: datetime
    response_body: bytes
    retention_policy_url: str
    retention_policy_reviewed_at: datetime
    retention_policy_version: str
    retention_policy_statement: bytes
    storage_permitted: bool
    http_status: int = 200
    content_type: str = "text/csv"
    parser_version: str = IEM_CSV_EVIDENCE_PARSER_VERSION
    completeness_version: str = DAILY_EXTREMES_COMPLETENESS_VERSION
    minimum_samples: int = _MIN_SAMPLES
    boundary_tolerance_seconds: int = int(_BOUNDARY_TOLERANCE.total_seconds())
    max_permitted_gap_seconds: int = int(_MAX_GAP.total_seconds())
    request_method: str = "GET"
    http_date: datetime | None = None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class ObservationRecoveryPointValue:
    obs_time: datetime
    temp_c: Decimal
    raw_report: str
    source_row_bytes: bytes


@dataclass(frozen=True, slots=True)
class ObservationRecoveryResult:
    artifact_id: int
    record_hash: str
    first_seen_at: datetime
    validation_status: str
    validation_code: str
    materialized: bool
    idempotent: bool


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationRecoveryIntegrityError(
            "observation recovery timestamps must be timezone-aware"
        )
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _temp(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ObservationRecoveryIntegrityError(
            "observation recovery temperatures must be finite"
        )
    return value.quantize(_TEMP_QUANTUM)


def _temp_text(value: Decimal) -> str:
    return format(_temp(value), ".2f")


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _normalised_point_payload(
    point: ObservationRecoveryPointValue,
) -> dict[str, object]:
    return {
        "obs_time": _utc_text(point.obs_time),
        "raw_report": point.raw_report,
        "temp_c": _temp_text(point.temp_c),
    }


def _point_set_values(
    points: tuple[ObservationRecoveryPointValue, ...],
) -> list[dict[str, object]]:
    return [
        {
            "obs_time": _utc_text(point.obs_time),
            "raw_report": point.raw_report,
            "sequence_no": sequence_no,
            "normalized_point_sha256": hashlib.sha256(
                _canonical_json_bytes(_normalised_point_payload(point))
            ).hexdigest(),
            "source_row_sha256": hashlib.sha256(point.source_row_bytes).hexdigest(),
            "temp_c": _temp_text(point.temp_c),
        }
        for sequence_no, point in enumerate(points)
    ]


def _artifact_record_values(
    *,
    evidence: ObservationRecoveryEvidence,
    request_method: str,
    request_manifest_sha256: str,
    response_sha256: str,
    retention_policy_sha256: str,
    point_set_sha256: str,
    points: tuple[ObservationRecoveryPointValue, ...],
    first_observation_at: datetime,
    last_observation_at: datetime,
    max_gap_seconds: int,
    validation_status: str,
    validation_code: str,
    validation_details: dict[str, object],
) -> dict[str, object]:
    return {
        "boundary_tolerance_seconds": evidence.boundary_tolerance_seconds,
        "completeness_version": evidence.completeness_version,
        "content_type": evidence.content_type,
        "etag": evidence.etag,
        "first_observation_at": _utc_text(first_observation_at),
        "http_date": _utc_text(evidence.http_date) if evidence.http_date else None,
        "http_status": evidence.http_status,
        "last_observation_at": _utc_text(last_observation_at),
        "local_date": evidence.local_date.isoformat(),
        "max_gap_seconds": max_gap_seconds,
        "max_permitted_gap_seconds": evidence.max_permitted_gap_seconds,
        "minimum_samples": evidence.minimum_samples,
        "network": evidence.network,
        "point_count": len(points),
        "point_set_sha256": point_set_sha256,
        "parser_version": evidence.parser_version,
        "request_method": request_method,
        "request_manifest_sha256": request_manifest_sha256,
        "request_started_at": _utc_text(evidence.request_started_at),
        "response_bytes": len(evidence.response_body),
        "response_received_at": _utc_text(evidence.response_received_at),
        "response_sha256": response_sha256,
        "sample_n": len(points),
        "source_name": evidence.source_name,
        "source_protocol": evidence.source_protocol,
        "source_station_id": evidence.source_station_id,
        "source_url": evidence.source_url,
        "station_id": evidence.station_id,
        "storage_permitted": evidence.storage_permitted,
        "retention_policy_reviewed_at": _utc_text(
            evidence.retention_policy_reviewed_at
        ),
        "retention_policy_sha256": retention_policy_sha256,
        "retention_policy_url": evidence.retention_policy_url,
        "retention_policy_version": evidence.retention_policy_version,
        "timezone": evidence.timezone,
        "utc_end": _utc_text(evidence.utc_end),
        "utc_start": _utc_text(evidence.utc_start),
        "validation_code": validation_code,
        "validation_details": validation_details,
        "validation_status": validation_status,
    }


def _daily_actual_record_hash(
    *,
    artifact_record_hash: str,
    evidence: ObservationRecoveryEvidence,
    first_seen_at: datetime,
    kind: WeatherKind,
    temp_c: Decimal,
    sample_n: int,
    first_observation_at: datetime,
    last_observation_at: datetime,
    max_gap_seconds: int,
) -> str:
    return canonical_json_sha256(
        {
            "artifact_record_hash": artifact_record_hash,
            "date": evidence.local_date.isoformat(),
            "first_observation_at": _utc_text(first_observation_at),
            "first_seen_at": _utc_text(first_seen_at),
            "kind": kind.value,
            "last_observation_at": _utc_text(last_observation_at),
            "max_gap_seconds": max_gap_seconds,
            "sample_n": sample_n,
            "source_protocol": evidence.source_protocol,
            "station_id": evidence.station_id,
            "temp_c": _temp_text(temp_c),
        }
    )


def _normalise_evidence(
    evidence: ObservationRecoveryEvidence,
) -> tuple[str, str, str, str]:
    source_name = evidence.source_name.strip()
    source_protocol = evidence.source_protocol.strip()
    source_url = evidence.source_url.strip()
    retention_policy_url = evidence.retention_policy_url.strip()
    retention_policy_version = evidence.retention_policy_version.strip()
    parser_version = evidence.parser_version.strip()
    completeness_version = evidence.completeness_version.strip()
    timezone = evidence.timezone.strip()
    network = evidence.network.strip()
    source_station_id = evidence.source_station_id.strip().upper()
    request_method = evidence.request_method.strip().upper()
    content_type = evidence.content_type.strip().lower()
    if evidence.station_id <= 0:
        raise ObservationRecoveryIntegrityError("station id must be positive")
    if not all(
        (
            source_name,
            source_protocol,
            source_url,
            retention_policy_url,
            retention_policy_version,
            parser_version,
            completeness_version,
            timezone,
            network,
            source_station_id,
            request_method,
            content_type,
        )
    ):
        raise ObservationRecoveryIntegrityError(
            "observation recovery source identity is incomplete"
        )
    if (
        not evidence.response_body
        or not isinstance(evidence.response_body, bytes)
        or not evidence.retention_policy_statement
        or not isinstance(evidence.retention_policy_statement, bytes)
    ):
        raise ObservationRecoveryIntegrityError(
            "response bytes and reviewed retention-policy statement are required"
        )
    if (
        not evidence.storage_permitted
        or source_protocol != IEM_ASOS_RECOVERY_PROTOCOL
        or parser_version != IEM_CSV_EVIDENCE_PARSER_VERSION
        or completeness_version != DAILY_EXTREMES_COMPLETENESS_VERSION
        or retention_policy_version != IEM_RETENTION_POLICY_VERSION
        or retention_policy_url != IEM_RETENTION_POLICY_URL
        or evidence.retention_policy_statement != IEM_RETENTION_POLICY_STATEMENT
    ):
        raise ObservationRecoveryIntegrityError(
            "recovery source authorization policy is not the reviewed immutable policy"
        )
    for value in (
        evidence.request_started_at,
        evidence.response_received_at,
        evidence.retention_policy_reviewed_at,
        evidence.utc_start,
        evidence.utc_end,
    ):
        _utc_text(value)
    if evidence.http_date is not None:
        _utc_text(evidence.http_date)
    if evidence.response_received_at < evidence.request_started_at:
        raise ObservationRecoveryIntegrityError(
            "response cannot precede its request"
        )
    if not 200 <= evidence.http_status < 300:
        raise ObservationRecoveryIntegrityError(
            "recovery evidence requires a successful HTTP response"
        )
    if evidence.utc_start >= evidence.utc_end:
        raise ObservationRecoveryIntegrityError("recovery UTC bounds are invalid")
    if evidence.response_received_at < evidence.utc_end:
        raise ObservationRecoveryIntegrityError(
            "recovery response must be received after the local day closed"
        )
    if (
        evidence.minimum_samples < 1
        or evidence.boundary_tolerance_seconds < 0
        or evidence.max_permitted_gap_seconds < 1
    ):
        raise ObservationRecoveryIntegrityError(
            "recovery completeness thresholds are invalid"
        )
    manifest = evidence.request_manifest
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "stratus.iem-request.v1":
        raise ObservationRecoveryIntegrityError("IEM request manifest schema is invalid")
    if (
        manifest.get("station_id") != evidence.station_id
        or manifest.get("source_url") != source_url
        or manifest.get("network") != network
        or str(manifest.get("source_station_id", "")).upper() != source_station_id
        or not isinstance(manifest.get("params"), list)
    ):
        raise ObservationRecoveryIntegrityError(
            "IEM request manifest disagrees with recovery identity"
        )
    try:
        request_start = date.fromisoformat(str(manifest["start"]))
        request_end = date.fromisoformat(str(manifest["end"]))
    except (KeyError, ValueError) as exc:
        raise ObservationRecoveryIntegrityError(
            "IEM request manifest date bounds are invalid"
        ) from exc
    last_utc_date = (evidence.utc_end.astimezone(UTC) - timedelta(microseconds=1)).date()
    if (
        request_start > evidence.utc_start.astimezone(UTC).date()
        or request_end <= last_utc_date
    ):
        raise ObservationRecoveryIntegrityError(
            "IEM request manifest does not envelope the exact local-day UTC bounds"
        )
    return source_name, source_protocol, source_url, request_method


def _normalise_points(
    points: list[ObservationRecoveryPointValue],
    *,
    local_date: date,
    timezone: ZoneInfo,
) -> tuple[ObservationRecoveryPointValue, ...]:
    if not points:
        raise ObservationRecoveryIntegrityError(
            "observation recovery requires at least one parsed point"
        )
    normalised: list[ObservationRecoveryPointValue] = []
    for point in points:
        _utc_text(point.obs_time)
        raw_report = point.raw_report.strip()
        if (
            not raw_report
            or not point.source_row_bytes
            or not isinstance(point.source_row_bytes, bytes)
            or raw_report.encode("utf-8") not in point.source_row_bytes
        ):
            raise ObservationRecoveryIntegrityError(
                "each exact CSV row must contain its parsed raw report"
            )
        if point.obs_time.astimezone(timezone).date() != local_date:
            raise ObservationRecoveryIntegrityError(
                "recovery point is outside the requested station-local date"
            )
        normalised.append(
            ObservationRecoveryPointValue(
                obs_time=point.obs_time,
                temp_c=_temp(point.temp_c),
                raw_report=raw_report,
                source_row_bytes=point.source_row_bytes,
            )
        )
    normalised.sort(key=lambda point: point.obs_time)
    if len({point.obs_time for point in normalised}) != len(normalised):
        raise ObservationRecoveryIntegrityError(
            "recovery points must be unique by observation timestamp"
        )
    return tuple(normalised)


def _coverage(
    points: tuple[ObservationRecoveryPointValue, ...],
    *,
    local_date: date,
    timezone: ZoneInfo,
    minimum_samples: int,
    boundary_tolerance_seconds: int,
    max_permitted_gap_seconds: int,
) -> tuple[datetime, datetime, int, bool, dict[str, object]]:
    first = points[0].obs_time
    last = points[-1].obs_time
    day_start = datetime.combine(local_date, time.min, tzinfo=timezone)
    day_end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=timezone)
    first_gap = first.astimezone(UTC) - day_start.astimezone(UTC)
    last_gap = day_end.astimezone(UTC) - last.astimezone(UTC)
    gaps = [
        later.obs_time.astimezone(UTC) - earlier.obs_time.astimezone(UTC)
        for earlier, later in zip(points, points[1:], strict=False)
    ]
    max_gap = max(gaps, default=timedelta(0))
    boundary_tolerance = timedelta(seconds=boundary_tolerance_seconds)
    max_permitted_gap = timedelta(seconds=max_permitted_gap_seconds)
    complete = (
        len(points) >= minimum_samples
        and timedelta(0) <= first_gap <= boundary_tolerance
        and timedelta(0) <= last_gap <= boundary_tolerance
        and max_gap <= max_permitted_gap
    )
    max_gap_seconds = int(max_gap.total_seconds())
    details: dict[str, object] = {
        "boundary_tolerance_seconds": boundary_tolerance_seconds,
        "complete": complete,
        "first_boundary_gap_seconds": int(first_gap.total_seconds()),
        "last_boundary_gap_seconds": int(last_gap.total_seconds()),
        "max_gap_seconds": max_gap_seconds,
        "max_permitted_gap_seconds": max_permitted_gap_seconds,
        "minimum_samples": minimum_samples,
        "sample_n": len(points),
    }
    return first, last, max_gap_seconds, complete, details


async def _idempotent_result(
    session: AsyncSession,
    *,
    artifact: ObservationRecoveryArtifact,
    expected_point_hashes: list[str],
) -> ObservationRecoveryResult:
    stored_point_hashes = list(
        (
            await session.scalars(
                select(ObservationRecoveryPoint.record_hash)
                .where(ObservationRecoveryPoint.artifact_id == artifact.id)
                .order_by(ObservationRecoveryPoint.sequence_no)
            )
        ).all()
    )
    if stored_point_hashes != expected_point_hashes:
        raise ObservationRecoveryIntegrityError(
            "stored idempotent artifact has a conflicting parsed point set"
        )
    actuals = list(
        (
            await session.scalars(
                select(DailyActual).where(
                    DailyActual.recovery_artifact_id == artifact.id
                )
            )
        ).all()
    )
    expected_actual_count = 2 if artifact.validation_status == "accepted" else 0
    if len(actuals) != expected_actual_count:
        raise ObservationRecoveryIntegrityError(
            "stored recovery artifact has an incomplete daily-actual projection"
        )
    return ObservationRecoveryResult(
        artifact_id=artifact.id,
        record_hash=artifact.record_hash,
        first_seen_at=artifact.first_seen_at,
        validation_status=artifact.validation_status,
        validation_code=artifact.validation_code,
        materialized=artifact.validation_status == "accepted",
        idempotent=True,
    )


async def record_observation_recovery(
    session: AsyncSession,
    *,
    evidence: ObservationRecoveryEvidence,
    points: list[ObservationRecoveryPointValue],
) -> ObservationRecoveryResult:
    """Insert-or-compare one immutable missed-day recovery.

    A complete first revision atomically inserts HIGH and LOW daily actuals.
    It never writes the live ``observations`` table and never updates an
    existing daily actual.  Exact retries return the existing artifact;
    divergent revisions after an accepted artifact are retained as
    quarantined evidence without changing truth used by calibration.
    """

    source_name, source_protocol, source_url, request_method = _normalise_evidence(
        evidence
    )
    evidence = replace(
        evidence,
        source_name=source_name,
        source_protocol=source_protocol,
        source_url=source_url,
        timezone=evidence.timezone.strip(),
        network=evidence.network.strip(),
        source_station_id=evidence.source_station_id.strip().upper(),
        parser_version=evidence.parser_version.strip(),
        completeness_version=evidence.completeness_version.strip(),
        retention_policy_url=evidence.retention_policy_url.strip(),
        retention_policy_version=evidence.retention_policy_version.strip(),
        content_type=evidence.content_type.strip().lower(),
        request_method=request_method,
    )
    station_row = (
        await session.execute(
            select(
                Station.tz,
                Station.icao,
                Station.iem_network,
                Station.iem_station_id,
            ).where(Station.id == evidence.station_id)
        )
    ).one_or_none()
    if station_row is None:
        raise ObservationRecoveryIntegrityError("recovery station does not exist")
    station_timezone_name = cast(str, station_row.tz)
    try:
        station_timezone = ZoneInfo(station_timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ObservationRecoveryIntegrityError(
            "recovery station timezone is invalid"
        ) from exc
    expected_utc_start = datetime.combine(
        evidence.local_date, time.min, tzinfo=station_timezone
    ).astimezone(UTC)
    expected_utc_end = datetime.combine(
        evidence.local_date + timedelta(days=1), time.min, tzinfo=station_timezone
    ).astimezone(UTC)
    manifest = evidence.request_manifest
    if (
        evidence.timezone != station_timezone_name
        or evidence.utc_start.astimezone(UTC) != expected_utc_start
        or evidence.utc_end.astimezone(UTC) != expected_utc_end
        or evidence.network != station_row.iem_network
        or evidence.source_station_id != str(station_row.iem_station_id).upper()
        or str(manifest.get("icao", "")).upper() != str(station_row.icao).upper()
    ):
        raise ObservationRecoveryIntegrityError(
            "recovery station mapping or exact local-day bounds disagree with registry"
        )
    normalised_points = _normalise_points(
        points,
        local_date=evidence.local_date,
        timezone=station_timezone,
    )
    first_observation_at, last_observation_at, max_gap_seconds, complete, details = (
        _coverage(
            normalised_points,
            local_date=evidence.local_date,
            timezone=station_timezone,
            minimum_samples=evidence.minimum_samples,
            boundary_tolerance_seconds=evidence.boundary_tolerance_seconds,
            max_permitted_gap_seconds=evidence.max_permitted_gap_seconds,
        )
    )
    for point in normalised_points:
        if point.source_row_bytes not in evidence.response_body:
            raise ObservationRecoveryIntegrityError(
                "exact selected CSV row is absent from the retained response body"
            )
    request_manifest_sha256 = canonical_json_sha256(manifest)
    response_sha256 = hashlib.sha256(evidence.response_body).hexdigest()
    retention_policy_sha256 = hashlib.sha256(
        evidence.retention_policy_statement
    ).hexdigest()
    point_set_values = _point_set_values(normalised_points)
    point_set_sha256 = canonical_json_sha256(point_set_values)

    # One station/day lock makes conflict classification deterministic and
    # prevents two concurrent first revisions from both materializing truth.
    lock_key = f"observation-recovery:{evidence.station_id}:{evidence.local_date.isoformat()}"
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": lock_key},
    )

    same_source = await session.scalar(
        select(ObservationRecoveryArtifact).where(
            ObservationRecoveryArtifact.station_id == evidence.station_id,
            ObservationRecoveryArtifact.local_date == evidence.local_date,
            ObservationRecoveryArtifact.source_protocol == source_protocol,
            ObservationRecoveryArtifact.request_manifest_sha256
            == request_manifest_sha256,
            ObservationRecoveryArtifact.response_sha256 == response_sha256,
        )
    )
    if same_source is not None:
        expected_point_hashes = [
            canonical_json_sha256(
                {
                    "artifact_record_hash": same_source.record_hash,
                    **point_values,
                }
            )
            for point_values in point_set_values
        ]
        return await _idempotent_result(
            session,
            artifact=same_source,
            expected_point_hashes=expected_point_hashes,
        )

    natural_status = "accepted" if complete else "quarantined"
    natural_code = (
        "complete"
        if natural_status == "accepted"
        else "incomplete_coverage"
    )
    natural_values = _artifact_record_values(
        evidence=evidence,
        request_method=request_method,
        request_manifest_sha256=request_manifest_sha256,
        response_sha256=response_sha256,
        retention_policy_sha256=retention_policy_sha256,
        point_set_sha256=point_set_sha256,
        points=normalised_points,
        first_observation_at=first_observation_at,
        last_observation_at=last_observation_at,
        max_gap_seconds=max_gap_seconds,
        validation_status=natural_status,
        validation_code=natural_code,
        validation_details=details,
    )
    natural_record_hash = canonical_json_sha256(natural_values)
    exact = await session.scalar(
        select(ObservationRecoveryArtifact).where(
            ObservationRecoveryArtifact.record_hash == natural_record_hash
        )
    )
    if exact is not None:
        expected_point_hashes = [
            canonical_json_sha256(
                {
                    "artifact_record_hash": natural_record_hash,
                    **point_values,
                }
            )
            for point_values in point_set_values
        ]
        return await _idempotent_result(
            session,
            artifact=exact,
            expected_point_hashes=expected_point_hashes,
        )

    existing_actual_count = cast(
        int,
        await session.scalar(
            select(func.count()).select_from(DailyActual).where(
                DailyActual.station_id == evidence.station_id,
                DailyActual.date == evidence.local_date,
            )
        ),
    )
    accepted = await session.scalar(
        select(ObservationRecoveryArtifact)
        .where(
            ObservationRecoveryArtifact.station_id == evidence.station_id,
            ObservationRecoveryArtifact.local_date == evidence.local_date,
            ObservationRecoveryArtifact.source_protocol == source_protocol,
            ObservationRecoveryArtifact.validation_status == "accepted",
        )
        .order_by(ObservationRecoveryArtifact.id)
        .limit(1)
    )
    validation_status = natural_status
    validation_code = natural_code
    validation_details = dict(details)
    if natural_status == "accepted" and accepted is not None:
        validation_status = "quarantined"
        validation_code = "conflicting_revision"
        validation_details["accepted_artifact_record_hash"] = accepted.record_hash
    elif natural_status == "accepted" and existing_actual_count:
        validation_status = "quarantined"
        validation_code = "existing_daily_actuals"
        validation_details["existing_daily_actual_count"] = existing_actual_count

    artifact_values = _artifact_record_values(
        evidence=evidence,
        request_method=request_method,
        request_manifest_sha256=request_manifest_sha256,
        response_sha256=response_sha256,
        retention_policy_sha256=retention_policy_sha256,
        point_set_sha256=point_set_sha256,
        points=normalised_points,
        first_observation_at=first_observation_at,
        last_observation_at=last_observation_at,
        max_gap_seconds=max_gap_seconds,
        validation_status=validation_status,
        validation_code=validation_code,
        validation_details=validation_details,
    )
    record_hash = canonical_json_sha256(artifact_values)
    exact = await session.scalar(
        select(ObservationRecoveryArtifact).where(
            ObservationRecoveryArtifact.record_hash == record_hash
        )
    )
    if exact is not None:
        expected_point_hashes = [
            canonical_json_sha256(
                {"artifact_record_hash": record_hash, **point_values}
            )
            for point_values in point_set_values
        ]
        return await _idempotent_result(
            session,
            artifact=exact,
            expected_point_hashes=expected_point_hashes,
        )

    database_values = dict(artifact_values)
    database_values.update(
        {
            "first_observation_at": first_observation_at,
            "http_date": evidence.http_date,
            "last_observation_at": last_observation_at,
            "local_date": evidence.local_date,
            "timezone": evidence.timezone,
            "utc_start": evidence.utc_start,
            "utc_end": evidence.utc_end,
            "network": evidence.network,
            "source_station_id": evidence.source_station_id,
            "request_started_at": evidence.request_started_at,
            "request_manifest": manifest,
            "response_body": evidence.response_body,
            "response_received_at": evidence.response_received_at,
            "source_name": source_name,
            "source_protocol": source_protocol,
            "parser_version": evidence.parser_version,
            "completeness_version": evidence.completeness_version,
            "source_url": source_url,
            "retention_policy_reviewed_at": evidence.retention_policy_reviewed_at,
            "retention_policy_statement": evidence.retention_policy_statement,
            "retention_policy_url": evidence.retention_policy_url,
            "retention_policy_version": evidence.retention_policy_version,
            "record_hash": record_hash,
        }
    )
    inserted = (
        await session.execute(
            insert(ObservationRecoveryArtifact)
            .values(**database_values)
            .returning(
                ObservationRecoveryArtifact.id,
                ObservationRecoveryArtifact.first_seen_at,
            )
        )
    ).one()
    artifact_id = cast(int, inserted.id)
    first_seen_at = cast(datetime, inserted.first_seen_at)

    point_rows: list[dict[str, object]] = []
    for point_values, point in zip(
        point_set_values, normalised_points, strict=True
    ):
        point_rows.append(
            {
                "artifact_id": artifact_id,
                "sequence_no": point_values["sequence_no"],
                "obs_time": point.obs_time,
                "temp_c": point.temp_c,
                "raw_report": point.raw_report,
                "source_row_bytes": point.source_row_bytes,
                "source_row_sha256": point_values["source_row_sha256"],
                "normalized_point_bytes": _canonical_json_bytes(
                    _normalised_point_payload(point)
                ),
                "normalized_point_sha256": point_values[
                    "normalized_point_sha256"
                ],
                "record_hash": canonical_json_sha256(
                    {"artifact_record_hash": record_hash, **point_values}
                ),
            }
        )
    await session.execute(insert(ObservationRecoveryPoint).values(point_rows))

    if validation_status == "accepted":
        extremes = {
            WeatherKind.HIGH: max(point.temp_c for point in normalised_points),
            WeatherKind.LOW: min(point.temp_c for point in normalised_points),
        }
        actual_rows: list[dict[str, object]] = []
        for kind, temp_c in extremes.items():
            actual_rows.append(
                {
                    "station_id": evidence.station_id,
                    "date": evidence.local_date,
                    "kind": kind,
                    "temp_c": temp_c,
                    "recovery_artifact_id": artifact_id,
                    "source_protocol": source_protocol,
                    "first_seen_at": first_seen_at,
                    "sample_n": len(normalised_points),
                    "first_observation_at": first_observation_at,
                    "last_observation_at": last_observation_at,
                    "max_gap_seconds": max_gap_seconds,
                    "record_hash": _daily_actual_record_hash(
                        artifact_record_hash=record_hash,
                        evidence=evidence,
                        first_seen_at=first_seen_at,
                        kind=kind,
                        temp_c=temp_c,
                        sample_n=len(normalised_points),
                        first_observation_at=first_observation_at,
                        last_observation_at=last_observation_at,
                        max_gap_seconds=max_gap_seconds,
                    ),
                }
            )
        # One multi-row INSERT plus the deferred database pair constraint makes
        # a partial HIGH-only/LOW-only recovery impossible.
        await session.execute(insert(DailyActual).values(actual_rows))

    return ObservationRecoveryResult(
        artifact_id=artifact_id,
        record_hash=record_hash,
        first_seen_at=first_seen_at,
        validation_status=validation_status,
        validation_code=validation_code,
        materialized=validation_status == "accepted",
        idempotent=False,
    )


__all__ = [
    "DAILY_EXTREMES_COMPLETENESS_VERSION",
    "IEM_ASOS_RECOVERY_PROTOCOL",
    "IEM_CSV_EVIDENCE_PARSER_VERSION",
    "IEM_RETENTION_POLICY_STATEMENT",
    "IEM_RETENTION_POLICY_SHA256",
    "IEM_RETENTION_POLICY_URL",
    "IEM_RETENTION_POLICY_VERSION",
    "ObservationRecoveryEvidence",
    "ObservationRecoveryIntegrityError",
    "ObservationRecoveryPointValue",
    "ObservationRecoveryResult",
    "record_observation_recovery",
]
