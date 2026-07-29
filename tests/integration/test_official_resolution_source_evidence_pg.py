from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.services.evidence_release_manifest import ALEMBIC_HEAD
from stratus.services.official_resolution import (
    LicensedContractBinding,
    LicensedJsonFieldMapConfig,
    OfficialResolutionLicense,
    canonical_content_sha256,
    parser_config_sha256,
    seal_license_policy,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
TERMS_HASH = "b" * 64
SOURCE_FAMILY = "wunderground"
SOURCE_URL = "https://www.wunderground.com/history/daily/test/K039"
ENDPOINT_URL = "https://api.example.test/v1/official/K039"
TERMS_URL = "https://example.test/legal/official-resolution-v1"
ADAPTER_NAME = "wunderground-licensed-json-field-map"
ADAPTER_VERSION = "1"
MARKET_DATE = date(2026, 7, 15)
DEFINITION_HASH = "c078c2c72a0547907f7fc7cbe3f09bf656c6c560043c347f925528743c2340e4"


def _rules_raw(source_url: str) -> str:
    return (
        f"Resolution is based on {source_url} for "
        f"{MARKET_DATE.day} {MARKET_DATE:%b} '{MARKET_DATE:%y} using whole "
        "degrees Fahrenheit until all data for this date has been finalized."
    )


def _parser_config(station_code: str = "K039") -> LicensedJsonFieldMapConfig:
    return LicensedJsonFieldMapConfig(
        schema_id="licensed-daily-v1",
        series_mode="scalar",
        station_code_literal=station_code,
        source_unit_literal="F",
        decimal_places_literal=0,
        finalization_rule_literal="source_finalized",
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
        source_window_start_local=time(0),
        source_window_end_local=time(0),
        source_window_end_day_offset=1,
    )


async def _seed_scope(
    session: AsyncSession,
    *,
    suffix: str = "base",
) -> dict[str, Any]:
    station_code = f"K{suffix[-3:].upper():0>3}"[-4:]
    station_id = await session.scalar(
        text(
            """
            INSERT INTO stations (icao, name, city, lat, lon, tz, active, stage)
            VALUES (:station_code, :name, :name, 40, -73, 'UTC', true, 'paper')
            RETURNING id
            """
        ),
        {"station_code": station_code, "name": f"Official Evidence {suffix}"},
    )
    assert station_id is not None
    event_id = f"official-evidence-event-{suffix}"
    source_url = SOURCE_URL.replace("K039", station_code)
    endpoint_url = ENDPOINT_URL.replace("K039", station_code)
    rules_raw = _rules_raw(source_url)
    rules_hash = hashlib.sha256(rules_raw.encode("utf-8")).hexdigest()
    market_id = await session.scalar(
        text(
            """
            INSERT INTO markets (
                polymarket_id, event_id, condition_id, yes_token_id, no_token_id,
                station_id, market_date, kind, rules_raw, station_verified,
                status, created_at, resolves_at
            ) VALUES (
                :polymarket_id, :event_id, :condition_id, :yes_token_id, :no_token_id,
                :station_id, :market_date, 'high', :rules_raw, true,
                'active', :created_at, :resolves_at
            ) RETURNING id
            """
        ),
        {
            "polymarket_id": f"official-evidence-market-{suffix}",
            "event_id": event_id,
            "condition_id": f"0xofficial-evidence-{suffix}",
            "yes_token_id": f"official-evidence-yes-{suffix}",
            "no_token_id": f"official-evidence-no-{suffix}",
            "station_id": station_id,
            "market_date": MARKET_DATE,
            "rules_raw": rules_raw,
            "created_at": datetime(2026, 7, 14, tzinfo=UTC),
            "resolves_at": datetime(2026, 7, 16, tzinfo=UTC),
        },
    )
    assert market_id is not None
    contract_id = await session.scalar(
        text(
            """
            INSERT INTO resolution_contracts (
                event_id, rules_hash, source_family, source_url, station_code,
                local_timezone, statistic, source_unit, decimal_places,
                finalization_rule, license_status, storage_permitted, license_url,
                rules_raw
            ) VALUES (
                :event_id, :rules_hash, :source_family, :source_url, :station_code,
                'UTC', 'high', 'F', 0, 'source_finalized',
                'restricted_unlicensed', false,
                'https://www.wunderground.com/company/legal', :rules_raw
            ) RETURNING id
            """
        ),
        {
            "event_id": event_id,
            "rules_hash": rules_hash,
            "source_family": SOURCE_FAMILY,
            "source_url": source_url,
            "station_code": station_code,
            "rules_raw": rules_raw,
        },
    )
    assert contract_id is not None
    return {
        "station_id": int(station_id),
        "station_code": station_code,
        "market_id": int(market_id),
        "market_date": MARKET_DATE,
        "event_id": event_id,
        "contract_id": int(contract_id),
        "rules_hash": rules_hash,
        "source_family": SOURCE_FAMILY,
        "source_url": source_url,
        "endpoint_url": endpoint_url,
    }


def _license_values(
    scope: dict[str, Any],
    now: datetime,
    *,
    version: str = "license-fixture-v1",
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    config = _parser_config(str(scope["station_code"]))
    # Preserve microseconds to prove the database UTC ISO encoder is exactly
    # compatible with the service's canonical policy payload.
    valid_from = now - timedelta(days=1)
    expires_at = now + timedelta(days=30)
    draft = OfficialResolutionLicense(
        license_id=version,
        grantee="STRATUS integration fixture",
        source_family=str(scope["source_family"]),
        endpoint_url=str(scope["endpoint_url"]),
        adapter_name=ADAPTER_NAME,
        adapter_version=ADAPTER_VERSION,
        parser_config=config,
        parser_config_sha256=parser_config_sha256(config),
        contract_bindings=(
            LicensedContractBinding(
                event_id=str(scope["event_id"]),
                rules_hash=str(scope["rules_hash"]),
                source_url=str(scope["source_url"]),
                station_code=str(scope["station_code"]),
            ),
        ),
        terms_url=TERMS_URL,
        terms_sha256=TERMS_HASH,
        valid_from=valid_from,
        expires_at=expires_at,
        storage_permitted=True,
        commercial_use_permitted=True,
        max_response_age_seconds=3_600,
    )
    policy = seal_license_policy(draft)
    values: dict[str, object] = {
        "contract_id": scope["contract_id"],
        "attestation_version": version,
        "attestation_protocol": "official-resolution-license-v1",
        "grantee": policy.grantee,
        "source_family": scope["source_family"],
        "settlement_source_url": scope["source_url"],
        "endpoint_url": scope["endpoint_url"],
        "license_status": "approved",
        "commercial_use_permitted": True,
        "value_retention_permitted": True,
        "raw_response_retention_permitted": True,
        "license_url": TERMS_URL,
        "terms_sha256": TERMS_HASH,
        "reviewer": "integration-reviewer",
        "reviewed_at": valid_from - timedelta(days=1),
        "valid_from": valid_from,
        "expires_at": expires_at,
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "parser_config": json.dumps(config.canonical_payload()),
        "parser_config_hash": policy.parser_config_sha256,
        "max_response_age_seconds": policy.max_response_age_seconds,
        "policy_hash": policy.policy_sha256,
        "rationale": "External licence and retention review approved for this contract.",
        "record_hash": "0" * 64,
    }
    if overrides:
        values.update(overrides)
    return values


async def _insert_license(
    session: AsyncSession,
    values: dict[str, object],
) -> dict[str, Any]:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO official_resolution_license_attestations (
                    contract_id, attestation_version, attestation_protocol, grantee,
                    source_family, settlement_source_url, endpoint_url, license_status,
                    commercial_use_permitted, value_retention_permitted,
                    raw_response_retention_permitted, license_url, terms_sha256,
                    reviewer, reviewed_at, valid_from, expires_at, adapter_name,
                    adapter_version, parser_config, parser_config_hash,
                    max_response_age_seconds, policy_hash, rationale, record_hash
                ) VALUES (
                    :contract_id, :attestation_version, :attestation_protocol, :grantee,
                    :source_family, :settlement_source_url, :endpoint_url, :license_status,
                    :commercial_use_permitted, :value_retention_permitted,
                    :raw_response_retention_permitted, :license_url, :terms_sha256,
                    :reviewer, :reviewed_at, :valid_from, :expires_at, :adapter_name,
                    :adapter_version, CAST(:parser_config AS jsonb), :parser_config_hash,
                    :max_response_age_seconds, :policy_hash, :rationale, :record_hash
                )
                RETURNING id, attested_at, record_hash, policy_hash, parser_config_hash
                """
            ),
            values,
        )
    ).mappings().one()
    return dict(row)


def _response_values(
    scope: dict[str, Any],
    license_row: dict[str, Any],
    observed_at: datetime,
    *,
    acquisition_protocol: str = "authorized-artifact-v1",
    body: bytes = b'{"temperatureMax":"75","isFinal":true}',
    published_at: datetime | None = None,
) -> dict[str, object]:
    body_hash = hashlib.sha256(body).hexdigest()
    if acquisition_protocol == "authorized-artifact-v1":
        request_method = "IMPORT"
        http_status: int | None = None
        request_manifest: dict[str, object] = {
            "relative_path": (
                f"imports/{scope['source_family']}/{scope['contract_id']}/{body_hash}.bin"
            ),
            "contract_id": scope["contract_id"],
        }
    else:
        request_method = "GET"
        http_status = 200
        request_manifest = {
            "url": scope["endpoint_url"],
            "contract_id": scope["contract_id"],
        }
    manifest_hash = canonical_content_sha256(request_manifest)
    return {
        "license_attestation_id": license_row["id"],
        "contract_id": scope["contract_id"],
        "response_protocol": "official-resolution-source-v1",
        "acquisition_protocol": acquisition_protocol,
        "source_family": scope["source_family"],
        "settlement_source_url": scope["source_url"],
        "endpoint_url": scope["endpoint_url"],
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "parser_config_hash": license_row["parser_config_hash"],
        "license_policy_hash": license_row["policy_hash"],
        "request_method": request_method,
        "request_manifest": json.dumps(request_manifest),
        "request_sha256": manifest_hash,
        "request_started_at": observed_at,
        "response_received_at": observed_at,
        "retrieved_at": observed_at,
        "source_published_at": published_at or observed_at - timedelta(hours=1),
        "http_status": http_status,
        "http_date": None,
        "last_modified": None,
        "etag": None,
        "content_type": "application/json",
        "response_headers": "{}",
        "response_body": body,
        "response_sha256": body_hash,
        "response_bytes": len(body),
        "record_hash": "0" * 64,
    }


async def _insert_response(
    session: AsyncSession,
    values: dict[str, object],
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO official_resolution_source_responses (
                    license_attestation_id, contract_id, response_protocol,
                    acquisition_protocol, source_family, settlement_source_url,
                    endpoint_url, adapter_name, adapter_version, parser_config_hash,
                    license_policy_hash, request_method, request_manifest,
                    request_sha256, request_started_at, response_received_at,
                    retrieved_at, source_published_at, http_status, http_date,
                    last_modified, etag, content_type, response_headers, response_body,
                    response_sha256, response_bytes, record_hash
                ) VALUES (
                    :license_attestation_id, :contract_id, :response_protocol,
                    :acquisition_protocol, :source_family, :settlement_source_url,
                    :endpoint_url, :adapter_name, :adapter_version, :parser_config_hash,
                    :license_policy_hash, :request_method, CAST(:request_manifest AS jsonb),
                    :request_sha256, :request_started_at, :response_received_at,
                    :retrieved_at, :source_published_at, :http_status, :http_date,
                    :last_modified, :etag, :content_type, CAST(:response_headers AS jsonb),
                    :response_body, :response_sha256, :response_bytes, :record_hash
                )
                RETURNING id, first_seen_at, retrieved_at, source_published_at,
                          response_sha256, record_hash, license_attestation_id,
                          parser_config_hash
                """
            ),
            values,
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


def _exact_values(
    scope: dict[str, Any],
    response_row: dict[str, Any],
    *,
    source_value: str = "75",
    value_c: str = "23.8889",
) -> dict[str, object]:
    manifest = {
        "locator": "/temperatureMax",
        "response_sha256": response_row["response_sha256"],
    }
    return {
        "response_id": response_row["id"],
        "contract_id": scope["contract_id"],
        "station_id": scope["station_id"],
        "event_id": scope["event_id"],
        "rules_hash": scope["rules_hash"],
        "source_family": scope["source_family"],
        "settlement_source_url": scope["source_url"],
        "endpoint_url": scope["endpoint_url"],
        "station_code": scope["station_code"],
        "market_date": scope["market_date"],
        "kind": "high",
        "source_value": Decimal(source_value),
        "source_unit": "F",
        "value_c": Decimal(value_c),
        "decimal_places": 0,
        "finalization_rule": "source_finalized",
        "is_final": True,
        "source_published_at": response_row["source_published_at"],
        "adapter_name": ADAPTER_NAME,
        "adapter_version": ADAPTER_VERSION,
        "parser_config_hash": response_row["parser_config_hash"],
        "extraction_locator": "/temperatureMax",
        "extraction_manifest": json.dumps(manifest),
        "extraction_manifest_hash": canonical_content_sha256(manifest),
        "source_response_sha256": response_row["response_sha256"],
        "extraction_protocol": "official-resolution-extraction-v1",
        "record_hash": "0" * 64,
    }


async def _insert_exact(
    session: AsyncSession,
    values: dict[str, object],
) -> dict[str, Any] | None:
    row = (
        await session.execute(
            text(
                """
                INSERT INTO official_resolution_exact_actuals (
                    response_id, contract_id, station_id, event_id, rules_hash,
                    source_family, settlement_source_url, endpoint_url, station_code,
                    market_date, kind, source_value, source_unit, value_c, decimal_places,
                    finalization_rule, is_final, source_published_at, adapter_name,
                    adapter_version, parser_config_hash, extraction_locator,
                    extraction_manifest, extraction_manifest_hash,
                    source_response_sha256, extraction_protocol, record_hash
                ) VALUES (
                    :response_id, :contract_id, :station_id, :event_id, :rules_hash,
                    :source_family, :settlement_source_url, :endpoint_url, :station_code,
                    :market_date, CAST(:kind AS weather_kind), :source_value, :source_unit,
                    :value_c, :decimal_places, :finalization_rule, :is_final,
                    :source_published_at, :adapter_name, :adapter_version,
                    :parser_config_hash, :extraction_locator,
                    CAST(:extraction_manifest AS jsonb), :extraction_manifest_hash,
                    :source_response_sha256, :extraction_protocol, :record_hash
                )
                RETURNING id, response_id, contract_id, station_id, market_date, kind,
                          source_value, source_unit, value_c, is_final,
                          source_published_at, record_hash
                """
            ),
            values,
        )
    ).mappings().one_or_none()
    return dict(row) if row is not None else None


async def _seed_complete_chain(
    session: AsyncSession,
    *,
    suffix: str = "all",
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    scope = await _seed_scope(session, suffix=suffix)
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    license_row = await _insert_license(session, _license_values(scope, now))
    observed_at = await session.scalar(select(func.clock_timestamp()))
    assert observed_at is not None
    response_row = await _insert_response(
        session,
        _response_values(scope, license_row, observed_at),
    )
    assert response_row is not None
    exact_row = await _insert_exact(session, _exact_values(scope, response_row))
    assert exact_row is not None
    return scope, license_row, response_row, exact_row


async def test_runtime_is_frozen_and_schema_starts_prospectively(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions() as session:
        runtime = (
            await session.execute(
                text("SELECT * FROM official_resolution_evidence_runtime WHERE id = 1")
            )
        ).mappings().one()
        assert runtime["definition_version"] == "official-resolution-evidence-v1"
        assert runtime["definition_hash"] == DEFINITION_HASH
        assert runtime["license_protocol"] == "official-resolution-license-v1"
        assert runtime["response_protocol"] == "official-resolution-source-v1"
        assert runtime["extraction_protocol"] == "official-resolution-extraction-v1"
        for table_name in (
            "official_resolution_license_attestations",
            "official_resolution_source_responses",
            "official_resolution_exact_actuals",
        ):
            assert await session.scalar(text(f"SELECT count(*) FROM {table_name}")) == 0
        with pytest.raises(DBAPIError, match="evidence runtime is immutable"):
            await session.execute(
                text(
                    "UPDATE official_resolution_evidence_runtime "
                    "SET definition_hash = repeat('f', 64) WHERE id = 1"
                )
            )
        await session.rollback()


async def test_contract_scoped_external_policy_is_verified_and_append_only(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="lic")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None

        forged = _license_values(scope, now, version="forged")
        forged["policy_hash"] = "f" * 64
        with pytest.raises(DBAPIError, match="policy hash mismatch"):
            async with session.begin_nested():
                await _insert_license(session, forged)

        wrong_parser = _license_values(scope, now, version="parser")
        wrong_parser["parser_config_hash"] = "e" * 64
        with pytest.raises(DBAPIError, match="parser config hash mismatch"):
            async with session.begin_nested():
                await _insert_license(session, wrong_parser)

        values = _license_values(scope, now)
        row = await _insert_license(session, values)
        assert row["policy_hash"] == values["policy_hash"]
        assert row["record_hash"] != "0" * 64
        # The legacy discovery flag was deliberately false. The reviewed 0039
        # attestation, not that old coarse flag, is the new authority.
        assert await session.scalar(
            text("SELECT storage_permitted FROM resolution_contracts WHERE id = :id"),
            {"id": scope["contract_id"]},
        ) is False

        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE official_resolution_license_attestations "
                        "SET rationale = 'changed' WHERE id = :id"
                    ),
                    {"id": row["id"]},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "DELETE FROM official_resolution_license_attestations WHERE id = :id"
                    ),
                    {"id": row["id"]},
                )


async def test_http_and_artifact_acquisition_are_strict_and_raw_bytes_are_db_authority(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="raw")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        license_row = await _insert_license(session, _license_values(scope, now))
        observed_at = await session.scalar(select(func.clock_timestamp()))
        assert observed_at is not None

        bad_pair = _response_values(scope, license_row, observed_at)
        bad_pair["request_method"] = "GET"
        bad_pair["http_status"] = 200
        with pytest.raises(DBAPIError, match="protocol mismatch|response_acquisition"):
            async with session.begin_nested():
                await _insert_response(session, bad_pair)

        unsafe_path = _response_values(scope, license_row, observed_at)
        unsafe_manifest = {"relative_path": "../outside.bin"}
        unsafe_path["request_manifest"] = json.dumps(unsafe_manifest)
        unsafe_path["request_sha256"] = canonical_content_sha256(unsafe_manifest)
        with pytest.raises(DBAPIError, match="path is not normalized relative"):
            async with session.begin_nested():
                await _insert_response(session, unsafe_path)

        forged_body = _response_values(scope, license_row, observed_at)
        forged_body["response_sha256"] = "d" * 64
        with pytest.raises(DBAPIError, match="body hash mismatch"):
            async with session.begin_nested():
                await _insert_response(session, forged_body)

        adapter_mismatch = _response_values(scope, license_row, observed_at)
        adapter_mismatch["adapter_version"] = "2"
        with pytest.raises(DBAPIError, match="binding mismatch"):
            async with session.begin_nested():
                await _insert_response(session, adapter_mismatch)

        artifact_row = await _insert_response(
            session,
            _response_values(scope, license_row, observed_at),
        )
        assert artifact_row is not None
        assert artifact_row["record_hash"] != "0" * 64
        assert artifact_row["source_published_at"] <= artifact_row["retrieved_at"]
        assert artifact_row["retrieved_at"] <= artifact_row["first_seen_at"]
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE official_resolution_source_responses "
                        "SET content_type = 'text/plain' WHERE id = :id"
                    ),
                    {"id": artifact_row["id"]},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "DELETE FROM official_resolution_source_responses WHERE id = :id"
                    ),
                    {"id": artifact_row["id"]},
                )

    # A distinct contract proves that real HTTP evidence still has its own
    # non-forged method/status semantics.
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="web")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        license_row = await _insert_license(session, _license_values(scope, now))
        observed_at = await session.scalar(select(func.clock_timestamp()))
        assert observed_at is not None
        http_values = _response_values(
            scope,
            license_row,
            observed_at,
            acquisition_protocol="licensed-http-v1",
        )
        http_row = await _insert_response(session, http_values)
        assert http_row is not None
        later_at = await session.scalar(select(func.clock_timestamp()))
        assert later_at is not None
        http_published_at = http_values["source_published_at"]
        assert isinstance(http_published_at, datetime)
        later_http = _response_values(
            scope,
            license_row,
            later_at,
            acquisition_protocol="licensed-http-v1",
            published_at=http_published_at,
        )
        with pytest.raises(DBAPIError, match="response identity conflict"):
            async with session.begin_nested():
                await _insert_response(session, later_http)


async def test_artifact_later_and_concurrent_retries_are_idempotent(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="ret")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        license_row = await _insert_license(session, _license_values(scope, now))
        observed_at = await session.scalar(select(func.clock_timestamp()))
        assert observed_at is not None
        first_values = _response_values(scope, license_row, observed_at)
        first_row = await _insert_response(session, first_values)
        assert first_row is not None

        later_at = await session.scalar(select(func.clock_timestamp()))
        assert later_at is not None
        later_values = _response_values(
            scope,
            license_row,
            later_at,
            published_at=first_values["source_published_at"],  # type: ignore[arg-type]
        )
        assert later_values["retrieved_at"] != first_values["retrieved_at"]
        assert await _insert_response(session, later_values) is None
        assert await session.scalar(
            text(
                "SELECT count(*) FROM official_resolution_source_responses "
                "WHERE contract_id = :contract_id"
            ),
            {"contract_id": scope["contract_id"]},
        ) == 1

        conflict = dict(later_values)
        conflict_published_at = conflict["source_published_at"]
        assert isinstance(conflict_published_at, datetime)
        conflict["source_published_at"] = conflict_published_at + timedelta(minutes=1)
        with pytest.raises(DBAPIError, match="response identity conflict"):
            async with session.begin_nested():
                await _insert_response(session, conflict)

        body_conflict = dict(later_values)
        conflicting_body = b'{"temperatureMax":"74","isFinal":true}'
        body_conflict["response_body"] = conflicting_body
        body_conflict["response_sha256"] = hashlib.sha256(conflicting_body).hexdigest()
        body_conflict["response_bytes"] = len(conflicting_body)
        with pytest.raises(DBAPIError, match="response identity conflict"):
            async with session.begin_nested():
                await _insert_response(session, body_conflict)

    # Use a second scope so two genuinely concurrent transactions race on an
    # absent identity. The advisory lock must serialize them into insert+no-op.
    async with db_sessions.begin() as session:
        concurrent_scope = await _seed_scope(session, suffix="con")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        concurrent_license = await _insert_license(
            session,
            _license_values(concurrent_scope, now),
        )
        observed_at = await session.scalar(select(func.clock_timestamp()))
        assert observed_at is not None
    concurrent_values = _response_values(
        concurrent_scope,
        concurrent_license,
        observed_at,
    )

    async def insert_once() -> dict[str, Any] | None:
        async with db_sessions.begin() as session:
            return await _insert_response(session, concurrent_values)

    results = await asyncio.gather(insert_once(), insert_once())
    assert sum(item is not None for item in results) == 1
    async with db_sessions() as session:
        assert await session.scalar(
            text(
                "SELECT count(*) FROM official_resolution_source_responses "
                "WHERE contract_id = :contract_id"
            ),
            {"contract_id": concurrent_scope["contract_id"]},
        ) == 1


async def test_exact_actual_and_settlement_projection_are_immutable_and_exact(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="ext")
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        license_row = await _insert_license(session, _license_values(scope, now))
        observed_at = await session.scalar(select(func.clock_timestamp()))
        assert observed_at is not None
        response_row = await _insert_response(
            session,
            _response_values(scope, license_row, observed_at),
        )
        assert response_row is not None

        wrong_conversion = _exact_values(scope, response_row, value_c="24.0000")
        with pytest.raises(DBAPIError, match="value conversion mismatch"):
            async with session.begin_nested():
                await _insert_exact(session, wrong_conversion)

        exact_values = _exact_values(scope, response_row)
        exact_row = await _insert_exact(session, exact_values)
        assert exact_row is not None
        assert exact_row["record_hash"] != "0" * 64
        assert await _insert_exact(session, exact_values) is None

        conflict = _exact_values(
            scope,
            response_row,
            source_value="74",
            value_c="23.3333",
        )
        with pytest.raises(DBAPIError, match="conflicts with sealed contract truth"):
            async with session.begin_nested():
                await _insert_exact(session, conflict)

        provenance = {
            "evidence_protocol": "official-resolution-evidence-v1",
            "official_exact_actual_id": exact_row["id"],
            "official_exact_record_hash": exact_row["record_hash"],
            "official_source_response_id": response_row["id"],
            "official_source_response_record_hash": response_row["record_hash"],
            "official_license_attestation_id": license_row["id"],
        }
        projection_values = {
            "contract_id": scope["contract_id"],
            "official_exact_actual_id": exact_row["id"],
            "station_id": scope["station_id"],
            "date": scope["market_date"],
            "kind": "high",
            "source_value": exact_row["source_value"],
            "source_unit": exact_row["source_unit"],
            "value_c": exact_row["value_c"],
            "is_final": exact_row["is_final"],
            "source_published_at": exact_row["source_published_at"],
            "retrieved_at": response_row["retrieved_at"],
            "source_response_hash": response_row["response_sha256"],
            "provenance": json.dumps(provenance),
        }
        missing_evidence = dict(projection_values)
        missing_evidence["official_exact_actual_id"] = None
        with pytest.raises(DBAPIError, match="requires official exact evidence"):
            async with session.begin_nested():
                await _insert_projection(session, missing_evidence)

        wrong_projection = dict(projection_values)
        wrong_projection["value_c"] = Decimal("23.9000")
        with pytest.raises(DBAPIError, match="does not match official exact evidence"):
            async with session.begin_nested():
                await _insert_projection(session, wrong_projection)

        projection_id = await _insert_projection(session, projection_values)
        assert projection_id is not None
        with pytest.raises(
            DBAPIError,
            match="projection is immutable after 0039|current evidence projection",
        ):
            async with session.begin_nested():
                await session.execute(
                    text("UPDATE settlement_actuals SET is_final = false WHERE id = :id"),
                    {"id": projection_id},
                )
        with pytest.raises(
            DBAPIError,
            match="projection is immutable after 0039|current evidence projection",
        ):
            async with session.begin_nested():
                await session.execute(
                    text("DELETE FROM settlement_actuals WHERE id = :id"),
                    {"id": projection_id},
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        "UPDATE official_resolution_exact_actuals "
                        "SET extraction_locator = '/changed' WHERE id = :id"
                    ),
                    {"id": exact_row["id"]},
                )


async def _insert_projection(
    session: AsyncSession,
    values: dict[str, object],
) -> int | None:
    result = await session.scalar(
        text(
            """
            INSERT INTO settlement_actuals (
                contract_id, official_exact_actual_id, station_id, date, kind,
                source_value, source_unit, value_c, is_final, source_published_at,
                retrieved_at, source_response_hash, provenance
            ) VALUES (
                :contract_id, :official_exact_actual_id, :station_id, :date,
                CAST(:kind AS weather_kind), :source_value, :source_unit, :value_c,
                :is_final, :source_published_at, :retrieved_at,
                :source_response_hash, CAST(:provenance AS jsonb)
            ) RETURNING id
            """
        ),
        values,
    )
    return int(result) if result is not None else None


async def test_migration_zero_backfills_legacy_settlement_actuals(
    db_sessions: SessionFactory,
) -> None:
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260715_0038")
    async with db_sessions.begin() as session:
        scope = await _seed_scope(session, suffix="old")
        await session.execute(
            text(
                """
                INSERT INTO settlement_actuals (
                    contract_id, station_id, date, kind, source_value, source_unit,
                    value_c, is_final, source_published_at, retrieved_at,
                    source_response_hash, provenance
                ) VALUES (
                    :contract_id, :station_id, :date, 'high', 75, 'F', 23.8889,
                    true, :published_at, :retrieved_at, :source_hash, '{}'::jsonb
                )
                """
            ),
            {
                "contract_id": scope["contract_id"],
                "station_id": scope["station_id"],
                "date": scope["market_date"],
                "published_at": datetime(2026, 7, 15, 1, tzinfo=UTC),
                "retrieved_at": datetime(2026, 7, 15, 2, tzinfo=UTC),
                "source_hash": "c" * 64,
            },
        )
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db_sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT official_exact_actual_id FROM settlement_actuals "
                    "WHERE contract_id = :contract_id"
                ),
                {"contract_id": scope["contract_id"]},
            )
        ).one()
        assert row.official_exact_actual_id is None
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD
        )


async def test_downgrade_is_blocked_after_primary_evidence_exists(
    db_sessions: SessionFactory,
) -> None:
    config = Config("alembic.ini")
    # Exercise the older 0039 guard without asking 0044 to discard an already
    # used immutable contract boundary.  The fixture is empty here, so the
    # 0044 downgrade is allowed; head is restored even when the assertion fails.
    await asyncio.to_thread(command.downgrade, config, "20260717_0043")
    try:
        async with db_sessions.begin() as session:
            scope = await _seed_scope(session, suffix="dwn")
            now = await session.scalar(select(func.clock_timestamp()))
            assert now is not None
            await _insert_license(session, _license_values(scope, now))

        with pytest.raises(Exception, match="refusing 0039 downgrade"):
            await asyncio.to_thread(command.downgrade, config, "20260715_0038")
        async with db_sessions() as session:
            assert await session.scalar(
                text("SELECT version_num FROM alembic_version")
            ) == ("20260717_0043")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
