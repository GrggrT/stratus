from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, cast

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import ForecastIngest, ForecastSourceResponse
from stratus.services.forecast_provenance import build_forecast_manifest

FORECAST_INGEST_PROTOCOL = "prospective-http-v1"
HISTORICAL_BACKFILL_PROTOCOL = "historical-backfill-v1"
_SUPPORTED_PROTOCOLS = frozenset(
    {FORECAST_INGEST_PROTOCOL, HISTORICAL_BACKFILL_PROTOCOL}
)


class SourceResponseEvidenceLike(Protocol):
    @property
    def request_started_at(self) -> datetime: ...

    @property
    def response_received_at(self) -> datetime: ...

    @property
    def source_url(self) -> str: ...

    @property
    def http_date(self) -> datetime | None: ...

    @property
    def etag(self) -> str | None: ...

    @property
    def response_body(self) -> bytes: ...

    @property
    def request_method(self) -> str: ...


@dataclass(frozen=True, slots=True)
class ForecastIngestResult:
    ingest_id: int
    first_seen_at: datetime
    normalized_manifest_sha256: str
    record_hash: str


class ForecastProvenanceIntegrityError(RuntimeError):
    pass


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastProvenanceIntegrityError(
            "forecast provenance timestamps must be timezone-aware"
        )
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _request_hash(*, request_method: str, source_url: str) -> str:
    return canonical_json_sha256(
        {"request_method": request_method, "source_url": source_url}
    )


def _evidence_values(
    evidence: SourceResponseEvidenceLike,
) -> dict[str, object]:
    method = evidence.request_method.strip().upper()
    source_url = evidence.source_url.strip()
    if not method or not source_url:
        raise ForecastProvenanceIntegrityError(
            "forecast source request method and URL are required"
        )
    body = evidence.response_body
    if not isinstance(body, bytes) or not body:
        raise ForecastProvenanceIntegrityError(
            "forecast source response body must be non-empty bytes"
        )
    if evidence.response_received_at < evidence.request_started_at:
        raise ForecastProvenanceIntegrityError(
            "forecast response cannot precede its request"
        )
    request_sha256 = _request_hash(request_method=method, source_url=source_url)
    response_sha256 = hashlib.sha256(body).hexdigest()
    values: dict[str, object] = {
        "etag": evidence.etag,
        "http_date": _utc_text(evidence.http_date) if evidence.http_date is not None else None,
        "request_method": method,
        "request_sha256": request_sha256,
        "request_started_at": _utc_text(evidence.request_started_at),
        "response_bytes": len(body),
        "response_received_at": _utc_text(evidence.response_received_at),
        "response_sha256": response_sha256,
        "source_url": source_url,
    }
    return values


def _ingest_record_values(
    *,
    run_id: int | None,
    station_id: int,
    request_model: str,
    collector: str,
    collection_protocol: str,
    pipeline_fingerprint: str,
    upstream_init_time: datetime | None,
    upstream_init_verified: bool,
    availability_semantics: str,
    normalized_manifest_sha256: str,
    expected_response_count: int,
    response_set_sha256: str,
) -> dict[str, object]:
    return {
        "availability_semantics": availability_semantics,
        "collection_protocol": collection_protocol,
        "collector": collector,
        "expected_response_count": expected_response_count,
        "normalized_manifest_sha256": normalized_manifest_sha256,
        "pipeline_fingerprint": pipeline_fingerprint,
        "request_model": request_model,
        "response_set_sha256": response_set_sha256,
        "run_id": run_id,
        "station_id": station_id,
        "upstream_init_time": (
            _utc_text(upstream_init_time) if upstream_init_time is not None else None
        ),
        "upstream_init_verified": upstream_init_verified,
    }


async def record_forecast_ingest(
    session: AsyncSession,
    *,
    run_id: int | None,
    station_id: int,
    request_model: str,
    collector: str,
    pipeline_fingerprint: str,
    availability_semantics: str,
    normalized_manifest: Mapping[str, object],
    responses: Sequence[SourceResponseEvidenceLike],
    upstream_init_time: datetime | None = None,
    upstream_init_verified: bool = False,
    collection_protocol: str = FORECAST_INGEST_PROTOCOL,
) -> ForecastIngestResult:
    """Atomically seal one parsed forecast request and its exact HTTP bytes.

    ``first_seen_at`` is assigned by a PostgreSQL BEFORE INSERT trigger.  The
    returned timestamp is therefore the database's point-in-time availability
    witness and must be copied unchanged to every materialized forecast row.
    """

    if collection_protocol not in _SUPPORTED_PROTOCOLS:
        raise ForecastProvenanceIntegrityError("unsupported forecast collection protocol")
    if station_id <= 0 or run_id is not None and run_id <= 0:
        raise ForecastProvenanceIntegrityError("forecast ingest identifiers must be positive")
    request_model = request_model.strip()
    collector = collector.strip()
    pipeline_fingerprint = pipeline_fingerprint.strip()
    availability_semantics = availability_semantics.strip()
    if not all(
        (request_model, collector, pipeline_fingerprint, availability_semantics)
    ):
        raise ForecastProvenanceIntegrityError("forecast ingest identity is incomplete")
    if upstream_init_verified and (upstream_init_time is None or run_id is None):
        raise ForecastProvenanceIntegrityError(
            "verified upstream initialization requires a timestamp and run id"
        )
    if len(responses) < 1 or len(responses) > 32:
        raise ForecastProvenanceIntegrityError(
            "forecast ingest must contain between 1 and 32 source responses"
        )

    manifest = dict(normalized_manifest)
    if (
        manifest.get("schema") != "forecast-normalized-manifest-v1"
        or manifest.get("station_id") != station_id
    ):
        raise ForecastProvenanceIntegrityError(
            "forecast manifest schema/station disagrees with ingest"
        )
    normalized_manifest_sha256 = canonical_json_sha256(manifest)

    response_payloads: list[tuple[SourceResponseEvidenceLike, dict[str, object], str]] = []
    for evidence in responses:
        evidence_values = _evidence_values(evidence)
        evidence_hash = canonical_json_sha256(evidence_values)
        response_payloads.append((evidence, evidence_values, evidence_hash))
    response_set_sha256 = canonical_json_sha256(
        [item[2] for item in response_payloads]
    )
    ingest_values = _ingest_record_values(
        run_id=run_id,
        station_id=station_id,
        request_model=request_model,
        collector=collector,
        collection_protocol=collection_protocol,
        pipeline_fingerprint=pipeline_fingerprint,
        upstream_init_time=upstream_init_time,
        upstream_init_verified=upstream_init_verified,
        availability_semantics=availability_semantics,
        normalized_manifest_sha256=normalized_manifest_sha256,
        expected_response_count=len(response_payloads),
        response_set_sha256=response_set_sha256,
    )
    record_hash = canonical_json_sha256(ingest_values)
    database_ingest_values = dict(ingest_values)
    database_ingest_values["upstream_init_time"] = upstream_init_time
    inserted = (
        await session.execute(
            insert(ForecastIngest)
            .values(
                **database_ingest_values,
                normalized_manifest=manifest,
                record_hash=record_hash,
            )
            .returning(ForecastIngest.id, ForecastIngest.first_seen_at)
        )
    ).one()
    ingest_id = cast(int, inserted.id)
    first_seen_at = cast(datetime, inserted.first_seen_at)

    response_rows: list[dict[str, object]] = []
    for sequence_no, (evidence, evidence_values, evidence_hash) in enumerate(
        response_payloads
    ):
        response_rows.append(
            {
                "ingest_id": ingest_id,
                "sequence_no": sequence_no,
                "request_method": evidence_values["request_method"],
                "request_started_at": evidence.request_started_at,
                "response_received_at": evidence.response_received_at,
                "source_url": evidence_values["source_url"],
                "request_sha256": evidence_values["request_sha256"],
                "http_date": evidence.http_date,
                "etag": evidence.etag,
                "response_body": evidence.response_body,
                "response_sha256": evidence_values["response_sha256"],
                "response_bytes": evidence_values["response_bytes"],
                "evidence_hash": evidence_hash,
                "record_hash": canonical_json_sha256(
                    {
                        "evidence_hash": evidence_hash,
                        "ingest_record_hash": record_hash,
                    }
                ),
            }
        )
    await session.execute(insert(ForecastSourceResponse).values(response_rows))
    return ForecastIngestResult(
        ingest_id=ingest_id,
        first_seen_at=first_seen_at,
        normalized_manifest_sha256=normalized_manifest_sha256,
        record_hash=record_hash,
    )


__all__ = [
    "FORECAST_INGEST_PROTOCOL",
    "HISTORICAL_BACKFILL_PROTOCOL",
    "ForecastIngestResult",
    "ForecastProvenanceIntegrityError",
    "SourceResponseEvidenceLike",
    "build_forecast_manifest",
    "canonical_json_sha256",
    "record_forecast_ingest",
]
