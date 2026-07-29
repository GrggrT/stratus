from __future__ import annotations

import hashlib
import json
import os
from dataclasses import fields, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import (
    OfficialResolutionExactActual,
    OfficialResolutionSourceResponse,
    SettlementActual,
)
from stratus.repositories.official_resolution import (
    AUTHORIZED_ARTIFACT_ACQUISITION,
    LicenseAttestationInput,
    OfficialResolutionIntegrityError,
    OfficialSourceAcquisitionInput,
    list_official_resolution_contract_evidence,
    official_resolution_evidence_summary,
    persist_official_resolution_evidence,
    register_license_attestation,
)
from stratus.services.official_resolution import (
    LICENSED_JSON_ADAPTER_NAME,
    LICENSED_JSON_ADAPTER_VERSION,
    LicensedContractBinding,
    LicensedJsonFieldMapConfig,
    OfficialResolutionLicense,
    OfficialResolutionRecord,
    OfficialSourceResponse,
    canonical_content_sha256,
    official_resolution_record_sha256,
    parser_config_sha256,
    seal_license_policy,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
TERMS_HASH = "b" * 64
SOURCE_URL = "https://history.licensed.example/station/KAAA"
TARGET_DATE = date(2026, 7, 19)
RULES_RAW = (
    f"Resolution is based on {SOURCE_URL} for "
    f"{TARGET_DATE.day} {TARGET_DATE:%b} '{TARGET_DATE:%y} using whole degrees "
    "Celsius until data for this date has been published."
)
RULES_HASH = hashlib.sha256(RULES_RAW.encode("utf-8")).hexdigest()
SOURCE_FAMILY = "national"
ENDPOINT_URL = "https://licensed.example/v1/official/KAAA"
RAW_SENTINEL = "official-resolution-raw-body-sentinel"


def _config() -> LicensedJsonFieldMapConfig:
    return LicensedJsonFieldMapConfig(
        schema_id="licensed-daily-v1",
        series_mode="scalar",
        schema_path=("schema",),
        event_id_path=("contract", "event_id"),
        rules_hash_path=("contract", "rules_hash"),
        source_family_path=("contract", "source_family"),
        source_url_path=("contract", "source_url"),
        station_path=("contract", "station"),
        date_path=("contract", "date"),
        high_path=("summary", "high"),
        low_path=("summary", "low"),
        unit_path=("contract", "unit"),
        decimal_places_path=("contract", "decimal_places"),
        finalization_rule_path=("contract", "finalization_rule"),
        window_start_at_path=("contract", "window_start_at"),
        window_end_at_path=("contract", "window_end_at"),
        finalization_witness_at_path=("contract", "finalization_witness_at"),
        published_at_path=("contract", "published_at"),
        station_code_literal="KAAA",
        source_unit_literal="C",
        decimal_places_literal=0,
        finalization_rule_literal="source_published",
        source_window_start_local=time(0),
        source_window_end_local=time(0),
        source_window_end_day_offset=1,
    )


async def _seed_scope(
    session: AsyncSession,
) -> tuple[int, int, date, datetime]:
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    target_date = TARGET_DATE
    station_id = await session.scalar(
        text(
            """
            INSERT INTO stations (icao, name, city, lat, lon, tz, active, stage)
            VALUES ('KAAA', 'Licensed station', 'Fixture', 40, -73, 'UTC', true, 'paper')
            RETURNING id
            """
        )
    )
    assert station_id is not None
    await session.execute(
        text(
            """
            INSERT INTO markets (
                polymarket_id, event_id, condition_id, yes_token_id, no_token_id,
                station_id, market_date, kind, rules_raw, station_verified,
                status, created_at, resolves_at
            ) VALUES (
                'official-market-1', 'official-event-1', '0xofficial-1',
                'official-yes-1', 'official-no-1', :station_id, :market_date,
                'high', :rules_raw, true, 'active',
                :created_at, :resolves_at
            )
            """
        ),
        {
            "station_id": int(station_id),
            "market_date": target_date,
            "rules_raw": RULES_RAW,
            "created_at": now - timedelta(days=2),
            "resolves_at": now - timedelta(minutes=5),
        },
    )
    contract_id = await session.scalar(
        text(
            """
            INSERT INTO resolution_contracts (
                event_id, rules_hash, source_family, source_url, station_code,
                local_timezone, statistic, source_unit, decimal_places,
                finalization_rule, license_status, storage_permitted,
                license_url, rules_raw
            ) VALUES (
                'official-event-1', :rules_hash, :source_family, :source_url,
                'KAAA', 'UTC', 'high', 'C', 0, 'source_published',
                'unreviewed', false, NULL, :rules_raw
            ) RETURNING id
            """
        ),
        {
            "rules_hash": RULES_HASH,
            "source_family": SOURCE_FAMILY,
            "source_url": SOURCE_URL,
            "rules_raw": RULES_RAW,
        },
    )
    assert contract_id is not None
    return int(station_id), int(contract_id), target_date, now


def _policy(*, now: datetime, grantee: str = "STRATUS test fixture") -> OfficialResolutionLicense:
    config = _config()
    draft = OfficialResolutionLicense(
        license_id="license-fixture-v1",
        grantee=grantee,
        source_family=SOURCE_FAMILY,
        endpoint_url=ENDPOINT_URL,
        adapter_name=LICENSED_JSON_ADAPTER_NAME,
        adapter_version=LICENSED_JSON_ADAPTER_VERSION,
        parser_config=config,
        parser_config_sha256=parser_config_sha256(config),
        contract_bindings=(
            LicensedContractBinding(
                event_id="official-event-1",
                rules_hash=RULES_HASH,
                source_url=SOURCE_URL,
                station_code="KAAA",
            ),
        ),
        terms_url="https://licensed.example/terms/v1",
        terms_sha256=TERMS_HASH,
        valid_from=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        storage_permitted=True,
        commercial_use_permitted=True,
        max_response_age_seconds=3_600,
    )
    return seal_license_policy(draft)


def _body(target_date: date, *, token: str = "31") -> bytes:
    return json.dumps(
        {
            "schema": "licensed-daily-v1",
            "sentinel": RAW_SENTINEL,
            "date": target_date.isoformat(),
            "temperatureMax": token,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _response(
    *,
    body: bytes,
    captured_at: datetime,
) -> OfficialSourceResponse:
    return OfficialSourceResponse(
        acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
        request_url=None,
        requested_at=captured_at,
        received_at=captured_at,
        request_method="IMPORT",
        status_code=None,
        media_type="application/json",
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
        tls_verified=None,
        source_published_at=captured_at - timedelta(minutes=1),
        finalization_witness_at=captured_at - timedelta(minutes=2),
    )


def _record(
    *,
    policy: OfficialResolutionLicense,
    response: OfficialSourceResponse,
    target_date: date,
    token: str = "31",
) -> OfficialResolutionRecord:
    source_value = Decimal(token)
    source_published_at = response.source_published_at
    witness = response.finalization_witness_at
    assert source_published_at is not None
    assert witness is not None
    draft = OfficialResolutionRecord(
        protocol="official-resolution-source-v1",
        acquisition_protocol=response.acquisition_protocol,
        adapter_name=policy.adapter_name,
        adapter_version=policy.adapter_version,
        license_id=policy.license_id,
        license_policy_sha256=policy.policy_sha256,
        parser_config_sha256=policy.parser_config_sha256,
        event_id="official-event-1",
        rules_hash=RULES_HASH,
        source_family=SOURCE_FAMILY,
        source_url=SOURCE_URL,
        endpoint_url=ENDPOINT_URL,
        station_code="KAAA",
        target_date=target_date,
        statistic="high",
        source_token=token,
        source_value=source_value,
        source_unit="C",
        value_c=source_value.quantize(Decimal("0.0001")),
        decimal_places=0,
        finalization_rule="source_published",
        window_start_at=datetime.combine(target_date, time(0), tzinfo=UTC),
        window_end_at=datetime.combine(target_date + timedelta(days=1), time(0), tzinfo=UTC),
        finalization_witness_at=witness,
        source_published_at=source_published_at,
        requested_at=response.requested_at,
        retrieved_at=response.received_at,
        response_sha256=response.body_sha256,
        canonical_content_sha256=canonical_content_sha256(
            json.loads(response.body.decode("utf-8"))
        ),
        record_sha256="",
    )
    return replace(draft, record_sha256=official_resolution_record_sha256(draft))


def _acquisition(target_date: date, *, suffix: str = "") -> OfficialSourceAcquisitionInput:
    filename = f"{target_date.isoformat()}{suffix}.json"
    return OfficialSourceAcquisitionInput(
        acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
        request_method="IMPORT",
        request_manifest={
            "relative_path": f"licensed/KAAA/{filename}",
            "actor": "integration-test",
            "reason": "licensed official resolution import",
        },
        response_headers={},
    )


@pytest.mark.asyncio
async def test_official_resolution_chain_retry_conflict_and_safe_reporting(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        station_id, contract_id, target_date, seeded_at = await _seed_scope(session)
        policy = _policy(now=seeded_at)
        attestation_input = LicenseAttestationInput(
            contract_id=contract_id,
            license_policy=policy,
            reviewer="integration reviewer",
            reviewed_at=seeded_at - timedelta(minutes=1),
            rationale="written license and retention review",
        )
        attestation = await register_license_attestation(session, attestation_input)
        same_attestation = await register_license_attestation(session, attestation_input)
        assert same_attestation.id == attestation.id
        conflicting_policy = _policy(now=seeded_at, grantee="different grantee")
        with pytest.raises(OfficialResolutionIntegrityError, match="conflicts"):
            await register_license_attestation(
                session,
                replace(attestation_input, license_policy=conflicting_policy),
            )

        captured_at = await session.scalar(select(func.clock_timestamp()))
        assert captured_at is not None
        body = _body(target_date)
        response = _response(body=body, captured_at=captured_at)
        record = _record(
            policy=policy,
            response=response,
            target_date=target_date,
        )
        acquisition = _acquisition(target_date)
        result = await persist_official_resolution_evidence(
            session,
            license_attestation_id=attestation.id,
            contract_id=contract_id,
            station_id=station_id,
            response=response,
            record=record,
            acquisition=acquisition,
        )
        retry = await persist_official_resolution_evidence(
            session,
            license_attestation_id=attestation.id,
            contract_id=contract_id,
            station_id=station_id,
            response=response,
            record=record,
            acquisition=acquisition,
        )
        assert (
            retry.response.id,
            retry.exact_actual.id,
            retry.settlement_actual.id,
        ) == (
            result.response.id,
            result.exact_actual.id,
            result.settlement_actual.id,
        )

        # An offline retry gets new observation clocks but keeps the same path,
        # bytes, source publication, and extraction. It resolves to first-seen IDs.
        later_response = replace(
            response,
            requested_at=response.requested_at + timedelta(microseconds=1),
            received_at=response.received_at + timedelta(microseconds=1),
        )
        later_record = _record(
            policy=policy,
            response=later_response,
            target_date=target_date,
        )
        later_retry = await persist_official_resolution_evidence(
            session,
            license_attestation_id=attestation.id,
            contract_id=contract_id,
            station_id=station_id,
            response=later_response,
            record=later_record,
            acquisition=acquisition,
        )
        assert later_retry.response.id == result.response.id
        assert later_retry.exact_actual.id == result.exact_actual.id
        assert later_retry.settlement_actual.id == result.settlement_actual.id
        assert later_retry.response.request_started_at == response.requested_at

        conflicting_body = _body(target_date, token="32")
        conflicting_response = _response(body=conflicting_body, captured_at=captured_at)
        conflicting_record = _record(
            policy=policy,
            response=conflicting_response,
            target_date=target_date,
            token="32",
        )
        with pytest.raises(OfficialResolutionIntegrityError, match="source response identity"):
            await persist_official_resolution_evidence(
                session,
                license_attestation_id=attestation.id,
                contract_id=contract_id,
                station_id=station_id,
                response=conflicting_response,
                record=conflicting_record,
                acquisition=acquisition,
            )

        # A new artifact identity can insert its response, but a contradictory
        # exact value rolls back that insert together with the failed chain.
        with pytest.raises(OfficialResolutionIntegrityError, match="different immutable"):
            await persist_official_resolution_evidence(
                session,
                license_attestation_id=attestation.id,
                contract_id=contract_id,
                station_id=station_id,
                response=conflicting_response,
                record=conflicting_record,
                acquisition=_acquisition(target_date, suffix="-different"),
            )
        assert await session.scalar(select(func.count(OfficialResolutionSourceResponse.id))) == 1
        assert await session.scalar(select(func.count(OfficialResolutionExactActual.id))) == 1

        stored_projection = await session.get(SettlementActual, result.settlement_actual.id)
        assert stored_projection is not None
        assert stored_projection.official_exact_actual_id == result.exact_actual.id
        assert stored_projection.source_value == result.exact_actual.source_value
        assert stored_projection.source_unit == result.exact_actual.source_unit
        assert stored_projection.value_c == result.exact_actual.value_c
        assert stored_projection.source_response_hash == result.response.response_sha256
        assert stored_projection.retrieved_at == result.response.retrieved_at

        summary = await official_resolution_evidence_summary(session)
        assert summary.definition_version == "official-resolution-evidence-v1"
        assert summary.license_protocol == "official-resolution-license-v1"
        assert summary.response_protocol == "official-resolution-source-v1"
        assert summary.extraction_protocol == "official-resolution-extraction-v1"
        assert summary.active_contracts == 1
        assert summary.licensed_contracts == 1
        assert summary.expired_contracts == 0
        assert summary.license_attestations == 1
        assert summary.source_responses == 1
        assert summary.exact_actuals == 1
        assert summary.projected_settlement_actuals == 1
        assert summary.raw_response_bytes == len(body)

        drilldown = await list_official_resolution_contract_evidence(
            session,
            contract_id=contract_id,
            limit=1,
        )
        assert len(drilldown) == 1
        item = drilldown[0]
        assert item.latest_response_id == result.response.id
        assert item.response_bytes == len(body)
        assert item.response_sha256 == hashlib.sha256(body).hexdigest()
        assert item.exact_actual_id == result.exact_actual.id
        assert item.settlement_actual_id == result.settlement_actual.id
        field_names = {field.name for field in fields(type(item))}
        assert "response_body" not in field_names
        assert RAW_SENTINEL not in repr(item)
