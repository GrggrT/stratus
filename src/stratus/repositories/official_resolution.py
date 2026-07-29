from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final, Literal, cast

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    OfficialResolutionEvidenceRuntime,
    OfficialResolutionExactActual,
    OfficialResolutionLicenseAttestation,
    OfficialResolutionSourceResponse,
    ResolutionContract,
    SettlementActual,
    Station,
)
from stratus.services.official_resolution import (
    OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
    OFFICIAL_RESOLUTION_PROTOCOL,
    AcquisitionProtocol,
    LicensedContractBinding,
    OfficialResolutionLicense,
    OfficialResolutionRecord,
    OfficialSourceResponse,
    canonical_content_sha256,
    license_policy_sha256,
    official_resolution_record_sha256,
    parser_config_sha256,
)

EVIDENCE_PROTOCOL = "official-resolution-evidence-v1"
EXTRACTION_PROTOCOL = "official-resolution-extraction-v1"
LICENSED_HTTP_ACQUISITION: Final[AcquisitionProtocol] = "licensed-http-v1"
AUTHORIZED_ARTIFACT_ACQUISITION: Final[AcquisitionProtocol] = "authorized-artifact-v1"
_ZERO_HASH = "0" * 64
_MAX_DRILLDOWN_ROWS = 200


class OfficialResolutionIntegrityError(RuntimeError):
    """An immutable official-resolution identity was replayed with new content."""


@dataclass(frozen=True, slots=True)
class LicenseAttestationInput:
    contract_id: int
    license_policy: OfficialResolutionLicense
    reviewer: str
    reviewed_at: datetime
    rationale: str


@dataclass(frozen=True, slots=True)
class OfficialSourceAcquisitionInput:
    acquisition_protocol: AcquisitionProtocol
    request_method: Literal["GET", "IMPORT"]
    request_manifest: Mapping[str, object]
    response_headers: Mapping[str, object]
    http_date: datetime | None = None
    last_modified: datetime | None = None
    etag: str | None = None


@dataclass(frozen=True, slots=True)
class OfficialEvidenceIngestResult:
    response: OfficialResolutionSourceResponse
    exact_actual: OfficialResolutionExactActual
    settlement_actual: SettlementActual


@dataclass(frozen=True, slots=True)
class OfficialResolutionEvidenceSummary:
    runtime_opened_at: datetime
    definition_version: str
    definition_hash: str
    license_protocol: str
    response_protocol: str
    extraction_protocol: str
    active_contracts: int
    licensed_contracts: int
    expired_contracts: int
    license_attestations: int
    source_responses: int
    exact_actuals: int
    projected_settlement_actuals: int
    raw_response_bytes: int
    latest_response_at: datetime | None
    latest_exact_actual_at: datetime | None


@dataclass(frozen=True, slots=True)
class OfficialResolutionContractEvidence:
    contract_id: int
    event_id: str
    rules_hash: str
    source_family: str
    settlement_source_url: str
    station_code: str
    kind: str
    retired_at: datetime | None
    license_attestation_id: int | None
    attestation_version: str | None
    license_status: str | None
    license_valid_from: datetime | None
    license_expires_at: datetime | None
    adapter_name: str | None
    adapter_version: str | None
    parser_config_hash: str | None
    license_policy_hash: str | None
    license_record_hash: str | None
    response_count: int
    latest_response_id: int | None
    acquisition_protocol: str | None
    request_method: str | None
    request_sha256: str | None
    response_sha256: str | None
    response_bytes: int | None
    response_first_seen_at: datetime | None
    source_published_at: datetime | None
    response_record_hash: str | None
    exact_actual_id: int | None
    station_id: int | None
    market_date: date | None
    source_value: Decimal | None
    source_unit: str | None
    value_c: Decimal | None
    exact_record_hash: str | None
    settlement_actual_id: int | None


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OfficialResolutionIntegrityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _one_contract_binding(
    policy: OfficialResolutionLicense,
    contract: ResolutionContract,
) -> LicensedContractBinding:
    if len(policy.contract_bindings) != 1:
        raise OfficialResolutionIntegrityError(
            "a contract-scoped attestation must contain exactly one contract binding"
        )
    binding = policy.contract_bindings[0]
    if (
        binding.event_id != contract.event_id
        or binding.rules_hash != contract.rules_hash
        or binding.source_url != contract.source_url
        or binding.station_code != contract.station_code
    ):
        raise OfficialResolutionIntegrityError(
            "license policy does not bind the exact resolution contract"
        )
    return binding


def _license_values(
    value: LicenseAttestationInput,
    contract: ResolutionContract,
) -> dict[str, object]:
    policy = value.license_policy
    _one_contract_binding(policy, contract)
    parser_payload = policy.parser_config.canonical_payload()
    parser_hash = parser_config_sha256(policy.parser_config)
    policy_hash = license_policy_sha256(policy)
    if not policy.policy_sha256 or policy.policy_sha256 != policy_hash:
        raise OfficialResolutionIntegrityError("license policy is not correctly sealed")
    if policy.parser_config_sha256 != parser_hash:
        raise OfficialResolutionIntegrityError("license parser configuration hash is invalid")
    if policy.source_family != contract.source_family:
        raise OfficialResolutionIntegrityError("license source family differs from contract")
    if not policy.storage_permitted or not policy.commercial_use_permitted:
        raise OfficialResolutionIntegrityError(
            "license must permit raw/value retention and commercial use"
        )
    reviewer = value.reviewer.strip()
    rationale = value.rationale.strip()
    if not reviewer or not rationale:
        raise OfficialResolutionIntegrityError("reviewer and rationale are required")
    return {
        "contract_id": value.contract_id,
        "attestation_version": policy.license_id,
        "attestation_protocol": OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
        "grantee": policy.grantee,
        "source_family": policy.source_family,
        "settlement_source_url": contract.source_url,
        "endpoint_url": policy.endpoint_url,
        "license_status": "approved",
        "commercial_use_permitted": True,
        "value_retention_permitted": True,
        "raw_response_retention_permitted": True,
        "license_url": policy.terms_url,
        "terms_sha256": policy.terms_sha256,
        "reviewer": reviewer,
        "reviewed_at": _utc(value.reviewed_at, field="reviewed_at"),
        "valid_from": _utc(policy.valid_from, field="license.valid_from"),
        "expires_at": _utc(policy.expires_at, field="license.expires_at"),
        "adapter_name": policy.adapter_name,
        "adapter_version": policy.adapter_version,
        "parser_config": parser_payload,
        "parser_config_hash": parser_hash,
        "max_response_age_seconds": policy.max_response_age_seconds,
        "policy_hash": policy_hash,
        "rationale": rationale,
        "record_hash": _ZERO_HASH,
    }


def _row_matches(row: object, values: Mapping[str, object], fields: tuple[str, ...]) -> bool:
    return all(getattr(row, field) == values[field] for field in fields)


_LICENSE_EQUALITY_FIELDS = (
    "contract_id",
    "attestation_version",
    "attestation_protocol",
    "grantee",
    "source_family",
    "settlement_source_url",
    "endpoint_url",
    "license_status",
    "commercial_use_permitted",
    "value_retention_permitted",
    "raw_response_retention_permitted",
    "license_url",
    "terms_sha256",
    "reviewer",
    "reviewed_at",
    "valid_from",
    "expires_at",
    "adapter_name",
    "adapter_version",
    "parser_config",
    "parser_config_hash",
    "max_response_age_seconds",
    "policy_hash",
    "rationale",
)


async def register_license_attestation(
    session: AsyncSession,
    value: LicenseAttestationInput,
) -> OfficialResolutionLicenseAttestation:
    """Register a reviewed contract-scoped policy without mutable upserts."""

    contract = await session.get(ResolutionContract, value.contract_id)
    if contract is None or contract.retired_at is not None:
        raise OfficialResolutionIntegrityError("active resolution contract does not exist")
    values = _license_values(value, contract)
    existing = await session.scalar(
        select(OfficialResolutionLicenseAttestation).where(
            OfficialResolutionLicenseAttestation.contract_id == value.contract_id,
            OfficialResolutionLicenseAttestation.attestation_version
            == value.license_policy.license_id,
        )
    )
    if existing is not None:
        if not _row_matches(existing, values, _LICENSE_EQUALITY_FIELDS):
            raise OfficialResolutionIntegrityError(
                "license attestation version conflicts with immutable stored content"
            )
        return existing

    attestation_id = (
        await session.execute(
            insert(OfficialResolutionLicenseAttestation)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_official_resolution_license_contract_version")
            .returning(OfficialResolutionLicenseAttestation.id)
        )
    ).scalar_one_or_none()
    stored = (
        await session.get(OfficialResolutionLicenseAttestation, attestation_id)
        if attestation_id is not None
        else await session.scalar(
            select(OfficialResolutionLicenseAttestation).where(
                OfficialResolutionLicenseAttestation.contract_id == value.contract_id,
                OfficialResolutionLicenseAttestation.attestation_version
                == value.license_policy.license_id,
            )
        )
    )
    if stored is None or not _row_matches(stored, values, _LICENSE_EQUALITY_FIELDS):
        raise OfficialResolutionIntegrityError(
            "license attestation retry differs from immutable stored content"
        )
    await session.refresh(stored)
    return stored


def _validate_acquisition(value: OfficialSourceAcquisitionInput) -> dict[str, object]:
    protocol = value.acquisition_protocol.strip()
    method = value.request_method.strip().upper()
    if (protocol, method) not in {
        (LICENSED_HTTP_ACQUISITION, "GET"),
        (AUTHORIZED_ARTIFACT_ACQUISITION, "IMPORT"),
    }:
        raise OfficialResolutionIntegrityError("unsupported acquisition protocol/method")
    manifest = dict(value.request_manifest)
    headers = dict(value.response_headers)
    if protocol == AUTHORIZED_ARTIFACT_ACQUISITION:
        relative_path = manifest.get("relative_path")
        actor = manifest.get("actor")
        reason = manifest.get("reason")
        if (
            not isinstance(relative_path, str)
            or not relative_path
            or relative_path != relative_path.strip()
            or relative_path.startswith("/")
            or ":" in relative_path
            or "\\" in relative_path
            or "//" in relative_path
            or any(part in {".", ".."} for part in relative_path.split("/"))
        ):
            raise OfficialResolutionIntegrityError(
                "authorized artifact import requires a normalized relative_path"
            )
        if (
            not isinstance(actor, str)
            or not actor.strip()
            or not isinstance(reason, str)
            or not reason.strip()
        ):
            raise OfficialResolutionIntegrityError(
                "authorized artifact import requires an actor and reason"
            )
        if (
            headers
            or value.http_date is not None
            or value.last_modified is not None
            or value.etag is not None
        ):
            raise OfficialResolutionIntegrityError(
                "authorized artifact import cannot fabricate HTTP metadata"
            )
    return {
        "acquisition_protocol": protocol,
        "request_method": method,
        "request_manifest": manifest,
        "request_sha256": canonical_content_sha256(manifest),
        "response_headers": headers,
        "http_date": (
            None if value.http_date is None else _utc(value.http_date, field="http_date")
        ),
        "last_modified": (
            None
            if value.last_modified is None
            else _utc(value.last_modified, field="last_modified")
        ),
        "etag": value.etag,
    }


def _extraction_manifest(record: OfficialResolutionRecord) -> dict[str, object]:
    # Deliberately excludes acquisition-clock fields and record_sha256. An operator
    # retry of the exact same authorized artifact may occur later, but must resolve
    # to the first immutable response/extraction chain.
    return {
        "canonical_content_sha256": record.canonical_content_sha256,
        "finalization_rule": record.finalization_rule,
        "finalization_witness_at": _utc(
            record.finalization_witness_at,
            field="record.finalization_witness_at",
        ).isoformat(),
        "license_policy_sha256": record.license_policy_sha256,
        "source_response_sha256": record.response_sha256,
        "source_token": record.source_token,
        "statistic": record.statistic,
        "target_date": record.target_date.isoformat(),
        "window_end_at": _utc(record.window_end_at, field="record.window_end_at").isoformat(),
        "window_start_at": _utc(
            record.window_start_at,
            field="record.window_start_at",
        ).isoformat(),
    }


def _validate_evidence_chain(
    *,
    contract: ResolutionContract,
    station: Station,
    attestation: OfficialResolutionLicenseAttestation,
    response: OfficialSourceResponse,
    record: OfficialResolutionRecord,
    acquisition: OfficialSourceAcquisitionInput,
) -> None:
    if hashlib.sha256(response.body).hexdigest() != response.body_sha256:
        raise OfficialResolutionIntegrityError("raw response hash differs from its bytes")
    media_type = response.media_type.split(";", 1)[0].strip().lower()
    if media_type not in {
        "application/json",
        "application/problem+json",
    } and not media_type.endswith("+json"):
        raise OfficialResolutionIntegrityError("official response is not a JSON artifact")
    max_body_bytes = attestation.parser_config.get("max_body_bytes")
    if (
        not isinstance(max_body_bytes, int)
        or isinstance(max_body_bytes, bool)
        or len(response.body) > max_body_bytes
    ):
        raise OfficialResolutionIntegrityError("official response exceeds its licensed bound")
    try:
        decoded = response.body.decode("utf-8", errors="strict")

        def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise OfficialResolutionIntegrityError(
                        f"official response contains duplicate JSON key {key!r}"
                    )
                result[key] = value
            return result

        artifact = json.loads(decoded, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialResolutionIntegrityError("official response is not valid UTF-8 JSON") from exc
    if canonical_content_sha256(artifact) != record.canonical_content_sha256:
        raise OfficialResolutionIntegrityError(
            "official response canonical-content hash differs from extraction"
        )
    requested_at = _utc(response.requested_at, field="response.requested_at")
    received_at = _utc(response.received_at, field="response.received_at")
    retrieved_at = _utc(record.retrieved_at, field="record.retrieved_at")
    source_published_at = _utc(
        record.source_published_at,
        field="record.source_published_at",
    )
    protocol = acquisition.acquisition_protocol.strip()
    method = acquisition.request_method.strip().upper()
    if (
        response.acquisition_protocol != protocol
        or record.acquisition_protocol != protocol
        or response.request_method != method
    ):
        raise OfficialResolutionIntegrityError(
            "acquisition metadata contradicts the captured response"
        )
    if protocol == LICENSED_HTTP_ACQUISITION:
        transport_valid = (
            response.request_url == attestation.endpoint_url
            and response.tls_verified is True
            and response.status_code == 200
        )
    elif protocol == AUTHORIZED_ARTIFACT_ACQUISITION:
        transport_valid = (
            response.request_url is None
            and response.tls_verified is None
            and response.status_code is None
        )
    else:
        transport_valid = False
    if (
        not transport_valid
        or not response.body
        or requested_at > received_at
        or received_at > retrieved_at
        or source_published_at > received_at
    ):
        raise OfficialResolutionIntegrityError("captured response envelope is invalid")
    if (
        attestation.reviewed_at > requested_at
        or attestation.attested_at > requested_at
        or attestation.valid_from > requested_at
        or attestation.expires_at <= retrieved_at
        or (retrieved_at - received_at).total_seconds() > attestation.max_response_age_seconds
    ):
        raise OfficialResolutionIntegrityError("license was not valid for the acquisition")
    if official_resolution_record_sha256(record) != record.record_sha256:
        raise OfficialResolutionIntegrityError("official resolution record seal is invalid")
    if (
        attestation.contract_id != contract.id
        or record.license_id != attestation.attestation_version
        or record.license_policy_sha256 != attestation.policy_hash
        or record.parser_config_sha256 != attestation.parser_config_hash
        or record.adapter_name != attestation.adapter_name
        or record.adapter_version != attestation.adapter_version
    ):
        raise OfficialResolutionIntegrityError("record differs from licensed adapter authority")
    if (
        record.protocol != OFFICIAL_RESOLUTION_PROTOCOL
        or record.event_id != contract.event_id
        or record.rules_hash != contract.rules_hash
        or record.source_family != contract.source_family
        or record.source_url != contract.source_url
        or record.station_code != contract.station_code
        or record.statistic != contract.statistic
        or record.source_unit != contract.source_unit
        or record.decimal_places != contract.decimal_places
        or record.finalization_rule != contract.finalization_rule
        or station.icao != contract.station_code
    ):
        raise OfficialResolutionIntegrityError("record differs from exact resolution contract")
    if (
        record.endpoint_url != attestation.endpoint_url
        or record.response_sha256 != response.body_sha256
        or _utc(record.requested_at, field="record.requested_at") != requested_at
        or retrieved_at != received_at
    ):
        raise OfficialResolutionIntegrityError("record differs from captured response envelope")
    if (
        response.source_published_at is not None
        and _utc(
            response.source_published_at,
            field="response.source_published_at",
        )
        != source_published_at
    ):
        raise OfficialResolutionIntegrityError(
            "response publication metadata differs from extracted record"
        )
    if response.finalization_witness_at is not None and _utc(
        response.finalization_witness_at,
        field="response.finalization_witness_at",
    ) != _utc(
        record.finalization_witness_at,
        field="record.finalization_witness_at",
    ):
        raise OfficialResolutionIntegrityError(
            "response finalization witness differs from extracted record"
        )


_RESPONSE_EQUALITY_FIELDS = (
    "license_attestation_id",
    "contract_id",
    "response_protocol",
    "acquisition_protocol",
    "source_family",
    "settlement_source_url",
    "endpoint_url",
    "adapter_name",
    "adapter_version",
    "parser_config_hash",
    "license_policy_hash",
    "request_method",
    "request_manifest",
    "request_sha256",
    "request_started_at",
    "response_received_at",
    "retrieved_at",
    "source_published_at",
    "http_status",
    "http_date",
    "last_modified",
    "etag",
    "content_type",
    "response_headers",
    "response_body",
    "response_sha256",
    "response_bytes",
)
_IMPORT_RETRY_IGNORED_RESPONSE_FIELDS = frozenset(
    {"request_started_at", "response_received_at", "retrieved_at", "http_date"}
)


def _response_matches_retry(
    row: OfficialResolutionSourceResponse,
    values: Mapping[str, object],
) -> bool:
    ignored = (
        _IMPORT_RETRY_IGNORED_RESPONSE_FIELDS
        if values["acquisition_protocol"] == AUTHORIZED_ARTIFACT_ACQUISITION
        else frozenset()
    )
    fields = tuple(field for field in _RESPONSE_EQUALITY_FIELDS if field not in ignored)
    return _row_matches(row, values, fields)


def _response_values(
    *,
    contract_id: int,
    attestation: OfficialResolutionLicenseAttestation,
    response: OfficialSourceResponse,
    record: OfficialResolutionRecord,
    acquisition: OfficialSourceAcquisitionInput,
) -> dict[str, object]:
    acquired = _validate_acquisition(acquisition)
    return {
        "license_attestation_id": attestation.id,
        "contract_id": contract_id,
        "response_protocol": OFFICIAL_RESOLUTION_PROTOCOL,
        "acquisition_protocol": acquired["acquisition_protocol"],
        "source_family": record.source_family,
        "settlement_source_url": record.source_url,
        "endpoint_url": record.endpoint_url,
        "adapter_name": record.adapter_name,
        "adapter_version": record.adapter_version,
        "parser_config_hash": record.parser_config_sha256,
        "license_policy_hash": record.license_policy_sha256,
        "request_method": acquired["request_method"],
        "request_manifest": acquired["request_manifest"],
        "request_sha256": acquired["request_sha256"],
        "request_started_at": _utc(response.requested_at, field="response.requested_at"),
        "response_received_at": _utc(response.received_at, field="response.received_at"),
        "retrieved_at": _utc(record.retrieved_at, field="record.retrieved_at"),
        "source_published_at": _utc(
            record.source_published_at,
            field="record.source_published_at",
        ),
        "http_status": (
            200 if acquired["acquisition_protocol"] == LICENSED_HTTP_ACQUISITION else None
        ),
        "http_date": acquired["http_date"],
        "last_modified": acquired["last_modified"],
        "etag": acquired["etag"],
        "content_type": response.media_type,
        "response_headers": acquired["response_headers"],
        "response_body": response.body,
        "response_sha256": response.body_sha256,
        "response_bytes": len(response.body),
        "record_hash": _ZERO_HASH,
    }


_EXACT_EQUALITY_FIELDS = (
    "response_id",
    "contract_id",
    "station_id",
    "event_id",
    "rules_hash",
    "source_family",
    "settlement_source_url",
    "endpoint_url",
    "station_code",
    "market_date",
    "kind",
    "source_value",
    "source_unit",
    "value_c",
    "decimal_places",
    "finalization_rule",
    "is_final",
    "source_published_at",
    "adapter_name",
    "adapter_version",
    "parser_config_hash",
    "extraction_locator",
    "extraction_manifest",
    "extraction_manifest_hash",
    "source_response_sha256",
    "extraction_protocol",
)


def _exact_values(
    *,
    response_id: int,
    contract_id: int,
    station_id: int,
    record: OfficialResolutionRecord,
) -> dict[str, object]:
    manifest = _extraction_manifest(record)
    return {
        "response_id": response_id,
        "contract_id": contract_id,
        "station_id": station_id,
        "event_id": record.event_id,
        "rules_hash": record.rules_hash,
        "source_family": record.source_family,
        "settlement_source_url": record.source_url,
        "endpoint_url": record.endpoint_url,
        "station_code": record.station_code,
        "market_date": record.target_date,
        "kind": record.statistic,
        "source_value": record.source_value,
        "source_unit": record.source_unit,
        "value_c": record.value_c,
        "decimal_places": record.decimal_places,
        "finalization_rule": record.finalization_rule,
        "is_final": True,
        "source_published_at": _utc(
            record.source_published_at,
            field="record.source_published_at",
        ),
        "adapter_name": record.adapter_name,
        "adapter_version": record.adapter_version,
        "parser_config_hash": record.parser_config_sha256,
        "extraction_locator": (
            f"{record.adapter_name}:{record.statistic}:{record.target_date.isoformat()}"
        ),
        "extraction_manifest": manifest,
        "extraction_manifest_hash": canonical_content_sha256(manifest),
        "source_response_sha256": record.response_sha256,
        "extraction_protocol": EXTRACTION_PROTOCOL,
        "record_hash": _ZERO_HASH,
    }


async def _persist_response(
    session: AsyncSession,
    values: dict[str, object],
) -> OfficialResolutionSourceResponse:
    existing = await session.scalar(
        select(OfficialResolutionSourceResponse).where(
            OfficialResolutionSourceResponse.contract_id == values["contract_id"],
            or_(
                OfficialResolutionSourceResponse.request_sha256 == values["request_sha256"],
                OfficialResolutionSourceResponse.response_sha256 == values["response_sha256"],
            ),
        )
    )
    if existing is not None:
        if not _response_matches_retry(existing, values):
            raise OfficialResolutionIntegrityError(
                "source response identity conflicts with immutable stored evidence"
            )
        return existing
    response_id = (
        await session.execute(
            insert(OfficialResolutionSourceResponse)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_official_resolution_response_contract_request")
            .returning(OfficialResolutionSourceResponse.id)
        )
    ).scalar_one_or_none()
    stored = (
        await session.get(OfficialResolutionSourceResponse, response_id)
        if response_id is not None
        else await session.scalar(
            select(OfficialResolutionSourceResponse).where(
                OfficialResolutionSourceResponse.contract_id == values["contract_id"],
                or_(
                    OfficialResolutionSourceResponse.request_sha256 == values["request_sha256"],
                    OfficialResolutionSourceResponse.response_sha256 == values["response_sha256"],
                ),
            )
        )
    )
    if stored is None or not _response_matches_retry(stored, values):
        raise OfficialResolutionIntegrityError(
            "source response retry differs from immutable stored evidence"
        )
    await session.refresh(stored)
    return stored


async def _persist_exact(
    session: AsyncSession,
    values: dict[str, object],
) -> OfficialResolutionExactActual:
    existing = await session.scalar(
        select(OfficialResolutionExactActual).where(
            OfficialResolutionExactActual.contract_id == values["contract_id"]
        )
    )
    if existing is not None:
        if not _row_matches(existing, values, _EXACT_EQUALITY_FIELDS):
            raise OfficialResolutionIntegrityError(
                "contract already has a different immutable exact actual"
            )
        return existing
    exact_id = (
        await session.execute(
            insert(OfficialResolutionExactActual)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_official_resolution_exact_actual_contract")
            .returning(OfficialResolutionExactActual.id)
        )
    ).scalar_one_or_none()
    stored = (
        await session.get(OfficialResolutionExactActual, exact_id)
        if exact_id is not None
        else await session.scalar(
            select(OfficialResolutionExactActual).where(
                OfficialResolutionExactActual.contract_id == values["contract_id"]
            )
        )
    )
    if stored is None or not _row_matches(stored, values, _EXACT_EQUALITY_FIELDS):
        raise OfficialResolutionIntegrityError(
            "exact actual retry differs from immutable stored evidence"
        )
    await session.refresh(stored)
    return stored


def _projection_values(
    exact: OfficialResolutionExactActual,
    response: OfficialResolutionSourceResponse,
) -> dict[str, object]:
    return {
        "contract_id": exact.contract_id,
        "official_exact_actual_id": exact.id,
        "station_id": exact.station_id,
        "date": exact.market_date,
        "kind": exact.kind,
        "source_value": exact.source_value,
        "source_unit": exact.source_unit,
        "value_c": exact.value_c,
        "is_final": exact.is_final,
        "source_published_at": exact.source_published_at,
        "retrieved_at": response.retrieved_at,
        "source_response_hash": response.response_sha256,
        "provenance": {
            "evidence_protocol": EVIDENCE_PROTOCOL,
            "official_exact_actual_id": exact.id,
            "official_exact_record_hash": exact.record_hash,
            "official_source_response_id": response.id,
            "official_source_response_record_hash": response.record_hash,
            "official_license_attestation_id": response.license_attestation_id,
        },
    }


_PROJECTION_EQUALITY_FIELDS = (
    "contract_id",
    "official_exact_actual_id",
    "station_id",
    "date",
    "kind",
    "source_value",
    "source_unit",
    "value_c",
    "is_final",
    "source_published_at",
    "retrieved_at",
    "source_response_hash",
    "provenance",
)


async def _persist_projection(
    session: AsyncSession,
    values: dict[str, object],
) -> SettlementActual:
    existing = await session.scalar(
        select(SettlementActual).where(SettlementActual.contract_id == values["contract_id"])
    )
    if existing is not None:
        if not _row_matches(existing, values, _PROJECTION_EQUALITY_FIELDS):
            raise OfficialResolutionIntegrityError(
                "settlement projection conflicts with immutable exact evidence"
            )
        return existing
    actual_id = (
        await session.execute(
            insert(SettlementActual)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_settlement_actual_contract")
            .returning(SettlementActual.id)
        )
    ).scalar_one_or_none()
    stored = (
        await session.get(SettlementActual, actual_id)
        if actual_id is not None
        else await session.scalar(
            select(SettlementActual).where(SettlementActual.contract_id == values["contract_id"])
        )
    )
    if stored is None or not _row_matches(stored, values, _PROJECTION_EQUALITY_FIELDS):
        raise OfficialResolutionIntegrityError(
            "settlement projection retry differs from immutable exact evidence"
        )
    await session.refresh(stored)
    return stored


async def persist_official_resolution_evidence(
    session: AsyncSession,
    *,
    license_attestation_id: int,
    contract_id: int,
    station_id: int,
    response: OfficialSourceResponse,
    record: OfficialResolutionRecord,
    acquisition: OfficialSourceAcquisitionInput,
) -> OfficialEvidenceIngestResult:
    """Atomically seal source bytes, exact truth, and its settlement projection."""

    async with session.begin_nested():
        contract = await session.get(ResolutionContract, contract_id)
        station = await session.get(Station, station_id)
        attestation = await session.get(
            OfficialResolutionLicenseAttestation,
            license_attestation_id,
        )
        if contract is None or contract.retired_at is not None:
            raise OfficialResolutionIntegrityError("active resolution contract does not exist")
        if station is None:
            raise OfficialResolutionIntegrityError("resolution station does not exist")
        if attestation is None:
            raise OfficialResolutionIntegrityError("license attestation does not exist")
        _validate_evidence_chain(
            contract=contract,
            station=station,
            attestation=attestation,
            response=response,
            record=record,
            acquisition=acquisition,
        )
        stored_response = await _persist_response(
            session,
            _response_values(
                contract_id=contract_id,
                attestation=attestation,
                response=response,
                record=record,
                acquisition=acquisition,
            ),
        )
        stored_exact = await _persist_exact(
            session,
            _exact_values(
                response_id=stored_response.id,
                contract_id=contract_id,
                station_id=station_id,
                record=record,
            ),
        )
        stored_projection = await _persist_projection(
            session,
            _projection_values(stored_exact, stored_response),
        )
    return OfficialEvidenceIngestResult(
        response=stored_response,
        exact_actual=stored_exact,
        settlement_actual=stored_projection,
    )


async def official_resolution_evidence_summary(
    session: AsyncSession,
) -> OfficialResolutionEvidenceSummary:
    runtime = await session.get(OfficialResolutionEvidenceRuntime, 1)
    if runtime is None:
        raise OfficialResolutionIntegrityError("official-resolution runtime is unavailable")
    active_contracts = int(
        await session.scalar(
            select(func.count(ResolutionContract.id)).where(ResolutionContract.retired_at.is_(None))
        )
        or 0
    )
    license_attestations = int(
        await session.scalar(select(func.count(OfficialResolutionLicenseAttestation.id))) or 0
    )
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise OfficialResolutionIntegrityError("database clock is unavailable")
    all_licensed_contracts = int(
        await session.scalar(
            select(func.count(func.distinct(OfficialResolutionLicenseAttestation.contract_id)))
            .join(
                ResolutionContract,
                ResolutionContract.id == OfficialResolutionLicenseAttestation.contract_id,
            )
            .where(ResolutionContract.retired_at.is_(None))
        )
        or 0
    )
    licensed_contracts = int(
        await session.scalar(
            select(func.count(func.distinct(OfficialResolutionLicenseAttestation.contract_id)))
            .join(
                ResolutionContract,
                ResolutionContract.id == OfficialResolutionLicenseAttestation.contract_id,
            )
            .where(
                ResolutionContract.retired_at.is_(None),
                OfficialResolutionLicenseAttestation.license_status == "approved",
                OfficialResolutionLicenseAttestation.valid_from <= now,
                OfficialResolutionLicenseAttestation.expires_at > now,
            )
        )
        or 0
    )
    source_responses = int(
        await session.scalar(select(func.count(OfficialResolutionSourceResponse.id))) or 0
    )
    exact_actuals = int(
        await session.scalar(select(func.count(OfficialResolutionExactActual.id))) or 0
    )
    projected = int(
        await session.scalar(
            select(func.count(SettlementActual.id)).where(
                SettlementActual.official_exact_actual_id.is_not(None)
            )
        )
        or 0
    )
    total_bytes = int(
        await session.scalar(
            select(func.coalesce(func.sum(OfficialResolutionSourceResponse.response_bytes), 0))
        )
        or 0
    )
    latest_response = await session.scalar(
        select(func.max(OfficialResolutionSourceResponse.first_seen_at))
    )
    latest_exact = await session.scalar(
        select(func.max(OfficialResolutionExactActual.extracted_at))
    )
    return OfficialResolutionEvidenceSummary(
        runtime_opened_at=runtime.opened_at,
        definition_version=runtime.definition_version,
        definition_hash=runtime.definition_hash,
        license_protocol=runtime.license_protocol,
        response_protocol=runtime.response_protocol,
        extraction_protocol=runtime.extraction_protocol,
        active_contracts=active_contracts,
        licensed_contracts=licensed_contracts,
        expired_contracts=max(all_licensed_contracts - licensed_contracts, 0),
        license_attestations=license_attestations,
        source_responses=source_responses,
        exact_actuals=exact_actuals,
        projected_settlement_actuals=projected,
        raw_response_bytes=total_bytes,
        latest_response_at=latest_response,
        latest_exact_actual_at=latest_exact,
    )


async def list_official_resolution_contract_evidence(
    session: AsyncSession,
    *,
    limit: int = 100,
    offset: int = 0,
    contract_id: int | None = None,
) -> tuple[OfficialResolutionContractEvidence, ...]:
    """Return bounded evidence metadata; raw response bytes are never selected."""

    if not 1 <= limit <= _MAX_DRILLDOWN_ROWS or offset < 0:
        raise OfficialResolutionIntegrityError(
            f"limit must be in [1, {_MAX_DRILLDOWN_ROWS}] and offset non-negative"
        )
    contract_query = select(ResolutionContract)
    if contract_id is not None:
        contract_query = contract_query.where(ResolutionContract.id == contract_id)
    contracts = tuple(
        (
            await session.scalars(
                contract_query.order_by(ResolutionContract.id.desc()).offset(offset).limit(limit)
            )
        ).all()
    )
    if not contracts:
        return ()
    contract_ids = tuple(contract.id for contract in contracts)
    licenses = tuple(
        (
            await session.scalars(
                select(OfficialResolutionLicenseAttestation)
                .where(OfficialResolutionLicenseAttestation.contract_id.in_(contract_ids))
                .order_by(
                    OfficialResolutionLicenseAttestation.contract_id,
                    OfficialResolutionLicenseAttestation.attested_at.desc(),
                    OfficialResolutionLicenseAttestation.id.desc(),
                )
            )
        ).all()
    )
    responses = tuple(
        (
            await session.execute(
                select(
                    OfficialResolutionSourceResponse.id,
                    OfficialResolutionSourceResponse.contract_id,
                    OfficialResolutionSourceResponse.acquisition_protocol,
                    OfficialResolutionSourceResponse.request_method,
                    OfficialResolutionSourceResponse.request_sha256,
                    OfficialResolutionSourceResponse.response_sha256,
                    OfficialResolutionSourceResponse.response_bytes,
                    OfficialResolutionSourceResponse.first_seen_at,
                    OfficialResolutionSourceResponse.source_published_at,
                    OfficialResolutionSourceResponse.record_hash,
                )
                .where(OfficialResolutionSourceResponse.contract_id.in_(contract_ids))
                .order_by(
                    OfficialResolutionSourceResponse.contract_id,
                    OfficialResolutionSourceResponse.first_seen_at.desc(),
                    OfficialResolutionSourceResponse.id.desc(),
                )
            )
        )
        .mappings()
        .all()
    )
    exacts = tuple(
        (
            await session.scalars(
                select(OfficialResolutionExactActual).where(
                    OfficialResolutionExactActual.contract_id.in_(contract_ids)
                )
            )
        ).all()
    )
    projections = tuple(
        (
            await session.scalars(
                select(SettlementActual).where(SettlementActual.contract_id.in_(contract_ids))
            )
        ).all()
    )
    latest_license: dict[int, OfficialResolutionLicenseAttestation] = {}
    latest_response: dict[int, Mapping[str, object]] = {}
    response_counts = Counter(int(response["contract_id"]) for response in responses)
    for license_row in licenses:
        latest_license.setdefault(license_row.contract_id, license_row)
    for response_row in responses:
        latest_response.setdefault(
            int(response_row["contract_id"]),
            cast(Mapping[str, object], dict(response_row)),
        )
    exact_by_contract = {row.contract_id: row for row in exacts}
    projection_by_contract = {row.contract_id: row for row in projections}

    result: list[OfficialResolutionContractEvidence] = []
    for contract in contracts:
        selected_license = latest_license.get(contract.id)
        selected_response = latest_response.get(contract.id)
        exact = exact_by_contract.get(contract.id)
        projection = projection_by_contract.get(contract.id)
        result.append(
            OfficialResolutionContractEvidence(
                contract_id=contract.id,
                event_id=contract.event_id,
                rules_hash=contract.rules_hash,
                source_family=contract.source_family,
                settlement_source_url=contract.source_url,
                station_code=contract.station_code,
                kind=contract.statistic,
                retired_at=contract.retired_at,
                license_attestation_id=(None if selected_license is None else selected_license.id),
                attestation_version=(
                    None if selected_license is None else selected_license.attestation_version
                ),
                license_status=(
                    None if selected_license is None else selected_license.license_status
                ),
                license_valid_from=(
                    None if selected_license is None else selected_license.valid_from
                ),
                license_expires_at=(
                    None if selected_license is None else selected_license.expires_at
                ),
                adapter_name=(None if selected_license is None else selected_license.adapter_name),
                adapter_version=(
                    None if selected_license is None else selected_license.adapter_version
                ),
                parser_config_hash=(
                    None if selected_license is None else selected_license.parser_config_hash
                ),
                license_policy_hash=(
                    None if selected_license is None else selected_license.policy_hash
                ),
                license_record_hash=(
                    None if selected_license is None else selected_license.record_hash
                ),
                response_count=response_counts[contract.id],
                latest_response_id=(
                    None if selected_response is None else cast(int, selected_response["id"])
                ),
                acquisition_protocol=(
                    None
                    if selected_response is None
                    else cast(str, selected_response["acquisition_protocol"])
                ),
                request_method=(
                    None
                    if selected_response is None
                    else cast(str, selected_response["request_method"])
                ),
                request_sha256=(
                    None
                    if selected_response is None
                    else cast(str, selected_response["request_sha256"])
                ),
                response_sha256=(
                    None
                    if selected_response is None
                    else cast(str, selected_response["response_sha256"])
                ),
                response_bytes=(
                    None
                    if selected_response is None
                    else cast(int, selected_response["response_bytes"])
                ),
                response_first_seen_at=(
                    None
                    if selected_response is None
                    else cast(datetime, selected_response["first_seen_at"])
                ),
                source_published_at=(
                    None
                    if selected_response is None
                    else cast(datetime, selected_response["source_published_at"])
                ),
                response_record_hash=(
                    None
                    if selected_response is None
                    else cast(str, selected_response["record_hash"])
                ),
                exact_actual_id=None if exact is None else exact.id,
                station_id=None if exact is None else exact.station_id,
                market_date=None if exact is None else exact.market_date,
                source_value=None if exact is None else exact.source_value,
                source_unit=None if exact is None else exact.source_unit,
                value_c=None if exact is None else exact.value_c,
                exact_record_hash=None if exact is None else exact.record_hash,
                settlement_actual_id=None if projection is None else projection.id,
            )
        )
    return tuple(result)


__all__ = [
    "AUTHORIZED_ARTIFACT_ACQUISITION",
    "EVIDENCE_PROTOCOL",
    "EXTRACTION_PROTOCOL",
    "LICENSED_HTTP_ACQUISITION",
    "LicenseAttestationInput",
    "OfficialEvidenceIngestResult",
    "OfficialResolutionContractEvidence",
    "OfficialResolutionEvidenceSummary",
    "OfficialResolutionIntegrityError",
    "OfficialSourceAcquisitionInput",
    "list_official_resolution_contract_evidence",
    "official_resolution_evidence_summary",
    "persist_official_resolution_evidence",
    "register_license_attestation",
]
