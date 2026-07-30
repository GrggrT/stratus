from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from dataclasses import asdict, replace
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.collectors.gamma import (
    ExpectedGammaMarket,
    GammaEventSnapshot,
    parse_complete_event_resolution,
)
from stratus.config import Settings
from stratus.db.models import (
    DailyActual,
    EvidenceVersionHistory,
    ExecutionMode,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ModelProb,
    Order,
    ProspectiveValidationEnrollment,
    ProspectiveValidationOutcome,
    ProspectiveValidationRuntime,
    ResolutionContract,
    ResolutionReconciliation,
    ResolutionReconciliationClassification,
    SettlementActual,
    SettlementCase,
    SettlementCaseClassification,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    StorageCapacitySample,
    TradeSide,
    WeatherKind,
)
from stratus.jobs.audit_accuracy import _daily_actual_truth_statement
from stratus.jobs.recompute_probs import _daily_actual_training_statement
from stratus.jobs.reconcile_resolutions import reconcile_resolutions
from stratus.repositories.accuracy import (
    ForecastEvaluationIntegrityError,
    ForecastEvaluationValue,
    upsert_forecast_evaluations,
)
from stratus.repositories.clob_snapshots import persist_clob_batch
from stratus.repositories.execution import PaperExecutionValue, persist_paper_executions
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeEventValue,
    GammaOutcomeIntegrityError,
    build_gamma_outcome_event_value,
    gamma_outcome_event_record_hash,
    gamma_outcome_event_record_is_valid,
    gamma_outcome_map,
    lock_gamma_event,
    upsert_gamma_outcome_event,
)
from stratus.repositories.market_prices import PriceTarget
from stratus.repositories.official_resolution import (
    AUTHORIZED_ARTIFACT_ACQUISITION,
    LicenseAttestationInput,
    OfficialSourceAcquisitionInput,
    persist_official_resolution_evidence,
    register_license_attestation,
)
from stratus.repositories.resolution_reconciliation import (
    resolution_reconciliation_record_is_valid,
)
from stratus.repositories.settlement_cases import (
    SettlementCaseIntegrityError,
    SettlementCaseObservation,
    settlement_case_record_is_valid,
    upsert_settlement_case,
)
from stratus.repositories.validation_ledger import (
    VALIDATION_DEFINITION_HASH,
    enroll_terminal_order,
    load_prospective_validation_dataset,
    seal_prospective_validation_outcomes,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
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
RESOLVED_AT = datetime(2026, 7, 5, 23, 43, 34, tzinfo=UTC)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _persist_attested_official_actual(
    session: AsyncSession,
    *,
    contract: ResolutionContract,
    station: Station,
    market: Market,
    source_value: Decimal,
) -> SettlementActual:
    """Create exact truth through the post-0039 immutable evidence chain."""

    assert contract.id is not None
    assert station.id is not None
    seeded_at = await session.scalar(select(func.clock_timestamp()))
    assert seeded_at is not None
    statistic: Literal["high", "low"] = "high" if market.kind == WeatherKind.HIGH else "low"
    parser_config = LicensedJsonFieldMapConfig(
        schema_id="gamma-ledger-official-v1",
        series_mode="scalar",
        schema_path=None,
        event_id_path=None,
        rules_hash_path=None,
        source_family_path=None,
        source_url_path=None,
        station_path=None,
        date_path=("date",),
        high_path=("high",),
        low_path=("low",),
        unit_path=None,
        decimal_places_path=None,
        finalization_rule_path=None,
        window_start_at_path=None,
        window_end_at_path=None,
        finalization_witness_at_path=None,
        published_at_path=None,
        station_code_literal=station.icao,
        source_unit_literal="C",
        decimal_places_literal=1,
        finalization_rule_literal="source_published",
        source_window_start_local=time(0),
        source_window_end_local=time(0),
        source_window_end_day_offset=1,
    )
    policy = seal_license_policy(
        OfficialResolutionLicense(
            license_id=f"gamma-ledger-license-{contract.id}",
            grantee="STRATUS integration test",
            source_family=contract.source_family,
            endpoint_url=f"https://example.test/api/final/{contract.id}",
            adapter_name=LICENSED_JSON_ADAPTER_NAME,
            adapter_version=LICENSED_JSON_ADAPTER_VERSION,
            parser_config=parser_config,
            parser_config_sha256=parser_config_sha256(parser_config),
            contract_bindings=(
                LicensedContractBinding(
                    event_id=contract.event_id,
                    rules_hash=contract.rules_hash,
                    source_url=contract.source_url,
                    station_code=contract.station_code,
                ),
            ),
            terms_url="https://example.test/terms/official-resolution-v1",
            terms_sha256="c" * 64,
            valid_from=seeded_at - timedelta(days=1),
            expires_at=seeded_at + timedelta(days=30),
            storage_permitted=True,
            commercial_use_permitted=True,
            max_response_age_seconds=3_600,
        )
    )
    attestation = await register_license_attestation(
        session,
        LicenseAttestationInput(
            contract_id=contract.id,
            license_policy=policy,
            reviewer="gamma ledger integration reviewer",
            reviewed_at=seeded_at - timedelta(seconds=1),
            rationale="fixture exercises the reviewed official-resolution path",
        ),
    )
    captured_at = await session.scalar(select(func.clock_timestamp()))
    assert captured_at is not None
    source_published_at = captured_at - timedelta(minutes=1)
    finalization_witness_at = captured_at - timedelta(minutes=2)
    token = format(source_value, "f")
    body = json.dumps(
        {
            "date": market.market_date.isoformat(),
            "high": token,
            "low": token,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    body_sha256 = hashlib.sha256(body).hexdigest()
    response = OfficialSourceResponse(
        acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
        request_url=None,
        requested_at=captured_at,
        received_at=captured_at,
        request_method="IMPORT",
        status_code=None,
        media_type="application/json",
        body=body,
        body_sha256=body_sha256,
        tls_verified=None,
        source_published_at=source_published_at,
        finalization_witness_at=finalization_witness_at,
    )
    local_timezone = ZoneInfo(station.tz)
    window_start_at = datetime.combine(
        market.market_date,
        time(0),
        tzinfo=local_timezone,
    ).astimezone(UTC)
    window_end_at = datetime.combine(
        market.market_date + timedelta(days=1),
        time(0),
        tzinfo=local_timezone,
    ).astimezone(UTC)
    value_c = source_value.quantize(Decimal("0.0001"))
    draft = OfficialResolutionRecord(
        protocol="official-resolution-source-v1",
        acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
        adapter_name=policy.adapter_name,
        adapter_version=policy.adapter_version,
        license_id=policy.license_id,
        license_policy_sha256=policy.policy_sha256,
        parser_config_sha256=policy.parser_config_sha256,
        event_id=contract.event_id,
        rules_hash=contract.rules_hash,
        source_family=contract.source_family,
        source_url=contract.source_url,
        endpoint_url=policy.endpoint_url,
        station_code=station.icao,
        target_date=market.market_date,
        statistic=statistic,
        source_token=token,
        source_value=source_value,
        source_unit="C",
        value_c=value_c,
        decimal_places=1,
        finalization_rule="source_published",
        window_start_at=window_start_at,
        window_end_at=window_end_at,
        finalization_witness_at=finalization_witness_at,
        source_published_at=source_published_at,
        requested_at=captured_at,
        retrieved_at=captured_at,
        response_sha256=body_sha256,
        canonical_content_sha256=canonical_content_sha256(json.loads(body)),
        record_sha256="",
    )
    record = replace(draft, record_sha256=official_resolution_record_sha256(draft))
    result = await persist_official_resolution_evidence(
        session,
        license_attestation_id=attestation.id,
        contract_id=contract.id,
        station_id=station.id,
        response=response,
        record=record,
        acquisition=OfficialSourceAcquisitionInput(
            acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
            request_method="IMPORT",
            request_manifest={
                "relative_path": (f"integration-test/{contract.id}/{body_sha256}.json"),
                "actor": "gamma-ledger-integration-test",
                "reason": "exercise immutable official-resolution evidence",
            },
            response_headers={},
        ),
    )
    return result.settlement_actual


def _raw_market(
    market: Market,
    *,
    yes_wins: bool,
    resolved_at: datetime = RESOLVED_AT,
) -> dict[str, object]:
    return {
        "id": market.polymarket_id,
        "closed": True,
        "umaResolutionStatus": "resolved",
        "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["1", "0"]' if yes_wins else '["0", "1"]',
        "clobTokenIds": json.dumps([market.yes_token_id, market.no_token_id]),
        "conditionId": market.condition_id,
        "questionID": f"question-{market.polymarket_id}",
        "resolvedBy": "0xresolution-oracle",
    }


def _validation_clob_batch(markets: list[Market]) -> tuple[ClobBooksBatch, list[PriceTarget]]:
    received_at = datetime.now(UTC) - timedelta(seconds=2)
    source_at = received_at - timedelta(milliseconds=100)
    targets = [
        PriceTarget(
            market_id=market.id,
            event_id=market.event_id,
            condition_id=str(market.condition_id),
            yes_token_id=str(market.yes_token_id),
            no_token_id=str(market.no_token_id),
        )
        for market in markets
    ]
    books: list[dict[str, object]] = []
    for target in targets:
        for yes in (True, False):
            books.append(
                {
                    "market": target.condition_id,
                    "asset_id": target.yes_token_id if yes else target.no_token_id,
                    "timestamp": str(int(source_at.timestamp() * 1000)),
                    "hash": f"validation-{'yes' if yes else 'no'}-{target.market_id}",
                    "tick_size": "0.01",
                    "neg_risk": False,
                    "bids": [{"price": "0.50" if yes else "0.48", "size": "100"}],
                    "asks": [{"price": "0.52" if yes else "0.50", "size": "100"}],
                }
            )
    body = json.dumps(books, separators=(",", ":")).encode()
    requested = tuple(sorted(str(book["asset_id"]) for book in books))
    by_token = {str(book["asset_id"]): book for book in books}
    return (
        ClobBooksBatch(
            source_url="https://clob.polymarket.com/books",
            requested_token_ids=requested,
            request_started_at=received_at - timedelta(milliseconds=200),
            response_received_at=received_at,
            response_body=body,
            response_sha256=hashlib.sha256(body).hexdigest(),
            response_bytes=len(body),
            response_headers=(),
            snapshots=tuple(parse_strict_book_snapshot(by_token[token]) for token in requested),
        ),
        targets,
    )


async def _seed_value(
    db_sessions: SessionFactory,
    *,
    market_count: int = 2,
    resolved_at: datetime = RESOLVED_AT,
) -> tuple[GammaOutcomeEventValue, list[Market]]:
    suffix = uuid4().hex
    event_id = f"gamma-ledger-{suffix}"
    async with db_sessions.begin() as session:
        station = Station(
            icao=f"T{suffix[:3].upper()}",
            name="Gamma ledger test station",
            city="Test City",
            lat=Decimal("50.000000"),
            lon=Decimal("8.000000"),
            tz="Europe/Berlin",
        )
        session.add(station)
        await session.flush()
        markets: list[Market] = []
        for index in range(market_count):
            external_id = f"{suffix}-{index}"
            source_url = f"https://example.test/final/{external_id}"
            rules_raw = (
                f"Resolution is based on {source_url} for "
                f"{resolved_at.date().day} {resolved_at.date():%b} "
                f"'{resolved_at.date():%y} using Celsius to one decimal place "
                "until data for this date has been published."
            )
            market = Market(
                polymarket_id=external_id,
                event_id=event_id,
                condition_id=f"condition-{external_id}",
                yes_token_id=f"yes-token-{external_id}",
                no_token_id=f"no-token-{external_id}",
                station_id=station.id,
                market_date=resolved_at.date(),
                kind=WeatherKind.HIGH,
                bucket_low_c=None if index == 0 else Decimal(str(20 + index)),
                bucket_high_c=(Decimal(str(20 + index)) if index < market_count - 1 else None),
                rules_raw=rules_raw,
                resolves_at=resolved_at - timedelta(hours=1),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
            session.add(market)
            markets.append(market)
        await session.flush()

    payload: dict[str, Any] = {
        "id": event_id,
        "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": (resolved_at + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
        "markets": [
            _raw_market(market, yes_wins=index == 0, resolved_at=resolved_at)
            for index, market in enumerate(markets)
        ],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    snapshot = GammaEventSnapshot(
        event_id=event_id,
        payload=payload,
        source_url=f"https://gamma-api.polymarket.com/events/{event_id}",
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        fetched_at=datetime.now(UTC),
        http_date=resolved_at + timedelta(minutes=3),
        last_modified=resolved_at + timedelta(minutes=2),
        etag='"gamma-test-revision"',
    )
    expected = [
        ExpectedGammaMarket(
            market_id=market.id,
            polymarket_id=market.polymarket_id,
            condition_id=market.condition_id,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            rules_hash=hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest(),
        )
        for market in markets
    ]
    resolution = parse_complete_event_resolution(
        payload,
        expected_event_id=event_id,
        expected_markets=expected,
    )
    return build_gamma_outcome_event_value(snapshot, resolution, markets), markets


async def _link_projection(
    session: AsyncSession,
    event: GammaOutcomeEvent,
    markets: list[Market],
) -> None:
    outcomes = gamma_outcome_map(event)
    for market in markets:
        stored_market = await session.get(Market, market.id)
        assert stored_market is not None
        stored_market.status = MarketStatus.RESOLVED
        stored_market.resolved_outcome = outcomes[market.id]
        stored_market.resolution_event_id = event.id
    await session.flush()


def _rehash_vector(
    value: GammaOutcomeEventValue,
    vector: list[dict[str, Any]],
) -> GammaOutcomeEventValue:
    universe = [
        {
            "market_id": item["market_id"],
            "polymarket_id": item["polymarket_id"],
            "condition_id": item["condition_id"],
            "yes_token_id": item["yes_token_id"],
            "no_token_id": item["no_token_id"],
        }
        for item in vector
    ]
    rules_manifest = [
        {"market_id": item["market_id"], "rules_hash": item["rules_hash"]} for item in vector
    ]
    return replace(
        value,
        market_count=len(vector),
        market_universe_hash=_canonical_hash(universe),
        rules_manifest_hash=_canonical_hash(rules_manifest),
        outcome_vector_hash=_canonical_hash(vector),
        outcome_vector=vector,
    )


async def _seed_forecast_evaluation_value(
    db_sessions: SessionFactory,
) -> tuple[ForecastEvaluationValue, int]:
    outcome_value, markets = await _seed_value(db_sessions)
    async with db_sessions.begin() as session:
        event = await upsert_gamma_outcome_event(session, outcome_value)
        outcomes = gamma_outcome_map(event)
        for market in markets:
            stored_market = await session.get(Market, market.id)
            assert stored_market is not None
            stored_market.status = MarketStatus.RESOLVED
            stored_market.resolved_outcome = outcomes[market.id]
            stored_market.resolution_event_id = event.id
        target = markets[0]
        decision_at = RESOLVED_AT - timedelta(hours=6)
        probability = Decimal("0.700000")
        model_prob = ModelProb(
            market_id=target.id,
            ts=decision_at,
            prob=probability,
            sigma=Decimal("1.5000"),
            horizon_h=Decimal("6"),
            components={"pipeline": {"version": "gamma-ledger-test-v1"}},
        )
        session.add(model_prob)
        await session.flush()
        value = ForecastEvaluationValue(
            market_id=target.id,
            model_prob_id=model_prob.id,
            decision_at=decision_at,
            independent_sample_key=f"gamma-ledger:{outcome_value.event_id}",
            model_probability=probability,
            horizon_market_probability=None,
            market_snapshot_at=None,
            executable_bid=None,
            executable_ask=None,
            pre_resolution_close=None,
            close_snapshot_at=None,
            event_probability_total=None,
            event_overround=None,
            resolved_outcome=True,
            resolution_event_id=event.id,
            resolution_vector_hash=event.outcome_vector_hash,
            model_brier=Decimal("0.09000000"),
            market_brier=None,
            evaluated_at=datetime.now(UTC),
            details={"resolution_evidence_protocol": event.evidence_protocol},
        )
        return value, event.id


async def test_first_seen_is_server_attested_and_replay_retains_first_witness(
    db_sessions: SessionFactory,
) -> None:
    value, markets = await _seed_value(db_sessions)
    attempted_backdate = datetime(2000, 1, 1, tzinfo=UTC)
    insert_started_at = datetime.now(UTC)
    async with db_sessions.begin() as session:
        direct = GammaOutcomeEvent(
            **asdict(value),
            first_seen_at=attempted_backdate,
            record_hash=gamma_outcome_event_record_hash(value),
        )
        session.add(direct)
        await session.flush()
        await session.refresh(direct)
        await _link_projection(session, direct, markets)
        direct_id = direct.id
        first_seen_at = direct.first_seen_at
    assert first_seen_at >= insert_started_at

    async with db_sessions.begin() as session:
        retry = await upsert_gamma_outcome_event(session, value)
        assert retry.id == direct_id
        assert retry.first_seen_at == first_seen_at
        assert gamma_outcome_event_record_is_valid(retry)
        expected_map = {
            int(item["market_id"]): bool(item["outcome_yes"]) for item in value.outcome_vector
        }
        assert gamma_outcome_map(retry) == expected_map

    later_body = value.source_response_body + b"\n"
    changed_http_witness = replace(
        value,
        source_etag='"later-gamma-revision"',
        source_response_body=later_body,
        source_response_sha256=hashlib.sha256(later_body).hexdigest(),
        source_response_bytes=len(later_body),
    )
    async with db_sessions.begin() as session:
        retained = await upsert_gamma_outcome_event(session, changed_http_witness)
        assert retained.id == direct_id
        assert retained.first_seen_at == first_seen_at
        assert retained.source_etag == value.source_etag
        assert retained.source_response_sha256 == value.source_response_sha256
        assert retained.source_response_bytes == value.source_response_bytes
        assert retained.source_response_body == value.source_response_body

    async with db_sessions() as session:
        persisted = await session.scalar(
            select(GammaOutcomeEvent).where(GammaOutcomeEvent.event_id == value.event_id)
        )
        assert persisted is not None

    fake_record = GammaOutcomeEvent(
        **asdict(value),
        first_seen_at=first_seen_at,
        record_hash="0" * 64,
    )
    assert gamma_outcome_event_record_is_valid(fake_record) is False
    with pytest.raises(GammaOutcomeIntegrityError, match="invalid record hash"):
        gamma_outcome_map(fake_record)


async def test_conflicting_canonical_winner_vector_is_rejected(
    db_sessions: SessionFactory,
) -> None:
    value, markets = await _seed_value(db_sessions)
    async with db_sessions.begin() as session:
        stored = await upsert_gamma_outcome_event(session, value)
        await _link_projection(session, stored, markets)
        original_map = gamma_outcome_map(stored)

    conflicting_vector = copy.deepcopy(value.outcome_vector)
    for item in conflicting_vector:
        item["outcome_yes"] = not item["outcome_yes"]
        item["yes_price"], item["no_price"] = item["no_price"], item["yes_price"]
    conflict = _rehash_vector(value, conflicting_vector)
    async with db_sessions() as session:
        with pytest.raises(
            GammaOutcomeIntegrityError,
            match="conflicting immutable Gamma outcome",
        ):
            await upsert_gamma_outcome_event(session, conflict)
        await session.rollback()

    async with db_sessions() as session:
        retained = await session.scalar(
            select(GammaOutcomeEvent).where(GammaOutcomeEvent.event_id == value.event_id)
        )
        assert retained is not None
        assert gamma_outcome_map(retained) == original_map


async def test_database_rejects_source_body_hash_mismatch(
    db_sessions: SessionFactory,
) -> None:
    value, _ = await _seed_value(db_sessions)
    tampered_body = value.source_response_body + b" "
    tampered = replace(
        value,
        source_response_body=tampered_body,
        source_response_bytes=len(tampered_body),
        # Keep the witnessed digest unchanged: the database must recompute it.
    )
    direct = GammaOutcomeEvent(
        **asdict(tampered),
        record_hash=gamma_outcome_event_record_hash(tampered),
    )
    async with db_sessions() as session:
        session.add(direct)
        with pytest.raises(DBAPIError, match="source bytes do not match"):
            await session.flush()
        await session.rollback()


async def test_database_rejects_committed_orphan_outcome_ledger(
    db_sessions: SessionFactory,
) -> None:
    value, _ = await _seed_value(db_sessions)
    with pytest.raises(DBAPIError, match="complete atomic market projection"):
        async with db_sessions.begin() as session:
            await upsert_gamma_outcome_event(session, value)


@pytest.mark.parametrize("operation", ["identity_update", "delete"])
async def test_database_seals_unlinked_market_once_ledger_exists(
    db_sessions: SessionFactory,
    operation: str,
) -> None:
    value, markets = await _seed_value(db_sessions)
    async with db_sessions() as session:
        await upsert_gamma_outcome_event(session, value)
        statement = (
            update(Market)
            .where(Market.id == markets[0].id)
            .values(rules_raw="mutated after ledger insert")
            if operation == "identity_update"
            else delete(Market).where(Market.id == markets[0].id)
        )
        with pytest.raises(DBAPIError, match="sealed by Gamma outcome evidence"):
            await session.execute(statement)
        await session.rollback()


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_database_rejects_outcome_ledger_mutation(
    db_sessions: SessionFactory,
    operation: str,
) -> None:
    value, markets = await _seed_value(db_sessions)
    async with db_sessions.begin() as session:
        stored = await upsert_gamma_outcome_event(session, value)
        await _link_projection(session, stored, markets)
        stored_id = stored.id

    statement = (
        update(GammaOutcomeEvent)
        .where(GammaOutcomeEvent.id == stored_id)
        .values(source_etag='"rewritten"')
        if operation == "update"
        else delete(GammaOutcomeEvent).where(GammaOutcomeEvent.id == stored_id)
    )
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="append-only evidence ledger"):
            await session.execute(statement)
        await session.rollback()

    async with db_sessions() as session:
        retained = await session.get(GammaOutcomeEvent, stored_id)
        assert retained is not None
        assert gamma_outcome_event_record_is_valid(retained)


@pytest.mark.parametrize("tamper", ["unknown_market", "rules", "incomplete"])
async def test_database_rejects_direct_invalid_event_projection(
    db_sessions: SessionFactory,
    tamper: str,
) -> None:
    value, _ = await _seed_value(
        db_sessions,
        market_count=3 if tamper == "incomplete" else 2,
    )
    vector = copy.deepcopy(value.outcome_vector)
    if tamper == "unknown_market":
        vector[0]["market_id"] = int(vector[0]["market_id"]) + 1_000_000
    elif tamper == "rules":
        vector[0]["rules_hash"] = "f" * 64
    else:
        vector.pop()
    tampered = _rehash_vector(value, vector)
    direct = GammaOutcomeEvent(
        **asdict(tampered),
        record_hash=gamma_outcome_event_record_hash(tampered),
    )

    async with db_sessions() as session:
        session.add(direct)
        with pytest.raises(
            DBAPIError,
            match="persisted market identity/rules|complete persisted market universe",
        ):
            await session.flush()
        await session.rollback()

    async with db_sessions() as session:
        assert (
            await session.scalar(
                select(GammaOutcomeEvent).where(GammaOutcomeEvent.event_id == value.event_id)
            )
            is None
        )


async def test_matching_legacy_forecast_evaluation_is_attested_once(
    db_sessions: SessionFactory,
) -> None:
    value, event_id = await _seed_forecast_evaluation_value(db_sessions)
    async with db_sessions.begin() as session:
        # Pre-ledger shape: predictive identity is valid, but mutable legacy
        # outcome and score are deliberately wrong.
        session.add(
            ForecastEvaluation(
                market_id=value.market_id,
                model_prob_id=value.model_prob_id,
                decision_at=value.decision_at,
                independent_sample_key=value.independent_sample_key,
                model_probability=value.model_probability,
                horizon_market_probability=None,
                market_snapshot_at=None,
                executable_bid=None,
                executable_ask=None,
                pre_resolution_close=None,
                close_snapshot_at=None,
                event_probability_total=None,
                event_overround=None,
                resolved_outcome=False,
                resolution_event_id=None,
                resolution_vector_hash=None,
                model_brier=Decimal("0.49000000"),
                market_brier=None,
                evaluated_at=value.evaluated_at,
                details={"legacy": True},
            )
        )

    async with db_sessions.begin() as session:
        assert await upsert_forecast_evaluations(session, [value]) == 1

    async with db_sessions() as session:
        stored = await session.scalar(
            select(ForecastEvaluation).where(
                ForecastEvaluation.model_prob_id == value.model_prob_id
            )
        )
        assert stored is not None
        assert stored.resolution_event_id == event_id
        assert stored.resolution_vector_hash == value.resolution_vector_hash
        assert stored.resolved_outcome is True
        assert stored.model_brier == Decimal("0.09000000")
        assert stored.details == value.details


async def test_clear_mismatch_quarantines_continuous_truth_without_rewriting_gamma(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    value, event_id = await _seed_forecast_evaluation_value(db_sessions)
    async with db_sessions.begin() as session:
        market = await session.get(Market, value.market_id)
        assert market is not None
        assert market.station_id is not None
        station_id = market.station_id
        market_date = market.market_date
        kind = market.kind
        # The first bucket is the witnessed Gamma winner (< 21 C).  This
        # fallback instead implies the opposite outcome for both buckets.
        session.add(
            DailyActual(
                station_id=station_id,
                date=market_date,
                kind=kind,
                temp_c=Decimal("25.0000"),
            )
        )
        assert await upsert_forecast_evaluations(session, [value]) == 1

    assert (
        await reconcile_resolutions(
            Settings(database_url=migrated_database_url, operator_api_key=None),
            db_sessions,
        )
        == 2
    )

    async with db_sessions() as session:
        cutoff = await session.scalar(select(func.clock_timestamp()))
        assert cutoff is not None
        recompute_actuals = list(
            (
                await session.scalars(
                    _daily_actual_training_statement(
                        station_ids={station_id},
                        earliest_date=market_date,
                        truth_cutoff=cutoff,
                    )
                )
            ).all()
        )
        audit_actuals = list(
            (await session.execute(_daily_actual_truth_statement(as_of=cutoff))).all()
        )
        reconciliations = list(
            (
                await session.scalars(
                    select(ResolutionReconciliation).order_by(
                        ResolutionReconciliation.market_id
                    )
                )
            ).all()
        )
        outcome = await session.get(GammaOutcomeEvent, event_id)
        evaluation = await session.scalar(
            select(ForecastEvaluation).where(
                ForecastEvaluation.model_prob_id == value.model_prob_id
            )
        )

    assert recompute_actuals == []
    assert audit_actuals == []
    assert len(reconciliations) == 2
    assert all(
        row.classification is ResolutionReconciliationClassification.CLEAR_MISMATCH
        for row in reconciliations
    )
    assert outcome is not None
    assert gamma_outcome_map(outcome)[value.market_id] is True
    assert evaluation is not None
    assert evaluation.resolution_event_id == event_id
    assert evaluation.resolved_outcome is True
    assert evaluation.model_brier == Decimal("0.09000000")


async def test_conflicting_legacy_forecast_identity_is_not_rewritten(
    db_sessions: SessionFactory,
) -> None:
    value, _ = await _seed_forecast_evaluation_value(db_sessions)
    legacy_probability = Decimal("0.600000")
    async with db_sessions.begin() as session:
        session.add(
            ForecastEvaluation(
                market_id=value.market_id,
                model_prob_id=value.model_prob_id,
                decision_at=value.decision_at,
                independent_sample_key=value.independent_sample_key,
                model_probability=legacy_probability,
                horizon_market_probability=None,
                market_snapshot_at=None,
                executable_bid=None,
                executable_ask=None,
                pre_resolution_close=None,
                close_snapshot_at=None,
                event_probability_total=None,
                event_overround=None,
                resolved_outcome=False,
                resolution_event_id=None,
                resolution_vector_hash=None,
                model_brier=Decimal("0.36000000"),
                market_brier=None,
                evaluated_at=value.evaluated_at,
                details={"legacy": True},
            )
        )

    async with db_sessions() as session:
        with pytest.raises(
            ForecastEvaluationIntegrityError,
            match="legacy forecast evaluation identity conflict",
        ):
            await upsert_forecast_evaluations(session, [value])
        await session.rollback()

    async with db_sessions() as session:
        stored = await session.scalar(
            select(ForecastEvaluation).where(
                ForecastEvaluation.model_prob_id == value.model_prob_id
            )
        )
        assert stored is not None
        assert stored.model_probability == legacy_probability
        assert stored.resolution_event_id is None
        assert stored.resolution_vector_hash is None
        assert stored.details == {"legacy": True}


async def test_attested_forecast_evaluation_conflict_is_not_rewritten(
    db_sessions: SessionFactory,
) -> None:
    value, _ = await _seed_forecast_evaluation_value(db_sessions)
    async with db_sessions.begin() as session:
        assert await upsert_forecast_evaluations(session, [value]) == 1

    conflict = replace(value, independent_sample_key="conflicting-sample-key")
    async with db_sessions() as session:
        with pytest.raises(
            ForecastEvaluationIntegrityError,
            match="forecast evaluation truth conflict",
        ):
            await upsert_forecast_evaluations(session, [conflict])
        await session.rollback()

    async with db_sessions() as session:
        stored = await session.scalar(
            select(ForecastEvaluation).where(
                ForecastEvaluation.model_prob_id == value.model_prob_id
            )
        )
        assert stored is not None
        assert stored.independent_sample_key == value.independent_sample_key
        assert stored.resolution_event_id == value.resolution_event_id
        assert stored.resolution_vector_hash == value.resolution_vector_hash


async def test_durable_reconciliation_and_settlement_lifecycle_are_attested_and_queryable(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    outcome_value, markets = await _seed_value(db_sessions)
    pending_at = RESOLVED_AT + timedelta(hours=2)
    async with db_sessions.begin() as session:
        pending = await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome_value.event_id,
                classification=SettlementCaseClassification.VENUE_PENDING,
                reason_code="gamma_resolution_pending",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at,
                source_url=outcome_value.source_url,
                source_response_sha256=outcome_value.source_response_sha256,
                last_error="not all markets are final",
                details={"underlying_classification": "venue_pending"},
            ),
        )
        assert settlement_case_record_is_valid(pending)

    async with db_sessions.begin() as session:
        outcome = await upsert_gamma_outcome_event(session, outcome_value)
        await _link_projection(session, outcome, markets)
        station_id = markets[0].station_id
        assert station_id is not None
        session.add(
            DailyActual(
                station_id=station_id,
                date=markets[0].market_date,
                kind=markets[0].kind,
                temp_c=Decimal("19.5000"),
            )
        )
        resolved_case = await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome.event_id,
                classification=SettlementCaseClassification.RESOLVED,
                reason_code="gamma_outcome_attested",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at + timedelta(minutes=10),
                source_url=outcome.source_url,
                source_response_sha256=outcome.source_response_sha256,
                resolution_event_id=outcome.id,
                resolution_vector_hash=outcome.outcome_vector_hash,
                details={"underlying_classification": "resolved"},
                resolved_at=outcome.fully_resolved_at,
            ),
        )
        assert resolved_case.previous_classification is SettlementCaseClassification.VENUE_PENDING
        assert resolved_case.attempt_count == 2
        assert resolved_case.transition_count == 2
        assert settlement_case_record_is_valid(resolved_case)

    written = await reconcile_resolutions(
        Settings(database_url=migrated_database_url, operator_api_key=None), db_sessions
    )
    assert written == 2

    async with db_sessions() as session:
        reconciliations = list(
            (
                await session.scalars(
                    select(ResolutionReconciliation).order_by(ResolutionReconciliation.market_id)
                )
            ).all()
        )
        case = await session.scalar(
            select(SettlementCase).where(SettlementCase.event_id == outcome_value.event_id)
        )
    assert len(reconciliations) == 2
    assert all(
        row.classification is ResolutionReconciliationClassification.MATCH
        for row in reconciliations
    )
    assert all(resolution_reconciliation_record_is_valid(row) for row in reconciliations)
    assert case is not None
    assert case.classification is SettlementCaseClassification.RESOLVED

    api_now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        await session.execute(
            update(ResolutionReconciliation)
            .where(ResolutionReconciliation.id == reconciliations[0].id)
            .values(checked_at=api_now - timedelta(hours=3))
        )
        await session.execute(
            update(ResolutionReconciliation)
            .where(ResolutionReconciliation.id == reconciliations[1].id)
            .values(checked_at=api_now - timedelta(minutes=5))
        )
        await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=f"{outcome_value.event_id}-stale-open",
                classification=SettlementCaseClassification.VENUE_PENDING,
                reason_code="gamma_resolution_pending",
                event_resolves_at=api_now - timedelta(hours=4),
                observed_at=api_now - timedelta(hours=1),
                last_error="winner vector incomplete",
            ),
        )
        await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=f"{outcome_value.event_id}-fresh-open",
                classification=SettlementCaseClassification.SOURCE_MISSING,
                reason_code="gamma_fetch_failed",
                event_resolves_at=api_now - timedelta(hours=2),
                observed_at=api_now - timedelta(minutes=5),
                last_error="temporary upstream failure",
            ),
        )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reconciliation_response = await client.get(
            "/api/v1/resolution-reconciliation",
            params={"classification": "truth_unavailable", "limit": 1},
        )
        case_response = await client.get(
            "/api/v1/settlement-cases",
            params={"classification": "resolved", "limit": 1},
        )

    assert reconciliation_response.status_code == 200
    reconciliation_payload = reconciliation_response.json()
    assert reconciliation_payload["total"] == 2
    assert reconciliation_payload["metar_available"] == 2
    assert reconciliation_payload["metar_consistent"] == 2
    assert reconciliation_payload["exact_available"] == 0
    assert reconciliation_payload["exact_source_hash_available"] == 0
    assert reconciliation_payload["exact_coverage"] == 0.0
    assert reconciliation_payload["latest_checked_at"] is not None
    assert reconciliation_payload["oldest_checked_at"] is not None
    assert reconciliation_payload["stale_records"] == 1
    assert reconciliation_payload["fresh"] is False
    assert reconciliation_payload["stale_after_seconds"] == 7200
    assert reconciliation_payload["classification_counts"] == {"match": 2}
    assert reconciliation_payload["items"] == []
    assert case_response.status_code == 200
    case_payload = case_response.json()
    assert case_payload["total"] == 3
    assert case_payload["open_total"] == 2
    assert case_payload["oldest_open_at"] is not None
    assert case_payload["latest_checked_at"] is not None
    assert case_payload["stale_open_cases"] == 1
    assert case_payload["fresh"] is False
    assert case_payload["stale_after_seconds"] == 1800
    assert case_payload["classification_counts"] == {
        "resolved": 1,
        "source_missing": 1,
        "venue_pending": 1,
    }
    assert len(case_payload["items"]) == 1
    assert case_payload["items"][0]["previous_classification"] == "venue_pending"

    async with db_sessions() as session:
        with pytest.raises(SettlementCaseIntegrityError, match="cannot regress"):
            await upsert_settlement_case(
                session,
                SettlementCaseObservation(
                    event_id=outcome_value.event_id,
                    classification=SettlementCaseClassification.SOURCE_MISSING,
                    reason_code="late_fetch_error",
                    event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                    observed_at=pending_at + timedelta(minutes=20),
                    last_error="source disappeared",
                ),
            )
        await session.rollback()


async def test_empirical_ops_empty_ledger_contract_is_explicit(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        reconciliation_response = await client.get("/api/v1/resolution-reconciliation")
        settlement_response = await client.get("/api/v1/settlement-cases")

    assert reconciliation_response.status_code == 200
    reconciliation_payload = reconciliation_response.json()
    assert reconciliation_payload["total"] == 0
    assert reconciliation_payload["metar_available"] == 0
    assert reconciliation_payload["metar_consistent"] == 0
    assert reconciliation_payload["exact_available"] == 0
    assert reconciliation_payload["exact_source_hash_available"] == 0
    assert reconciliation_payload["exact_coverage"] == 0.0
    assert reconciliation_payload["latest_checked_at"] is None
    assert reconciliation_payload["oldest_checked_at"] is None
    assert reconciliation_payload["stale_records"] == 0
    assert reconciliation_payload["fresh"] is False
    assert reconciliation_payload["stale_after_seconds"] == 7200

    assert settlement_response.status_code == 200
    settlement_payload = settlement_response.json()
    assert settlement_payload["total"] == 0
    assert settlement_payload["open_total"] == 0
    assert settlement_payload["oldest_open_at"] is None
    assert settlement_payload["latest_checked_at"] is None
    assert settlement_payload["stale_open_cases"] == 0
    assert settlement_payload["fresh"] is True
    assert settlement_payload["stale_after_seconds"] == 1800


async def test_exact_coverage_counts_only_attested_official_evidence(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    outcome_value, markets = await _seed_value(db_sessions)
    market = markets[0]
    assert market.station_id is not None
    async with db_sessions.begin() as session:
        station = await session.get(Station, market.station_id)
        assert station is not None
        source_url = f"https://example.test/final/{market.polymarket_id}"
        contract_rules_raw = market.rules_raw
        contract = ResolutionContract(
            event_id=market.event_id,
            rules_hash=hashlib.sha256(contract_rules_raw.encode("utf-8")).hexdigest(),
            source_family="national",
            source_url=source_url,
            station_code=station.icao,
            local_timezone=station.tz,
            statistic=market.kind.value,
            source_unit="C",
            decimal_places=1,
            finalization_rule="source_published",
            license_status="unreviewed",
            storage_permitted=False,
            rules_raw=contract_rules_raw,
        )
        session.add(contract)
        await session.flush()
        await _persist_attested_official_actual(
            session,
            contract=contract,
            station=station,
            market=market,
            source_value=Decimal("19.5"),
        )
        outcome = await upsert_gamma_outcome_event(session, outcome_value)
        await _link_projection(session, outcome, markets)

    assert (
        await reconcile_resolutions(
            Settings(database_url=migrated_database_url, operator_api_key=None), db_sessions
        )
        == 2
    )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/resolution-reconciliation")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["exact_available"] == 1
    assert payload["exact_source_hash_available"] == 1
    assert payload["exact_coverage"] == 0.5


async def test_storage_capacity_samples_are_database_append_only(
    db_sessions: SessionFactory,
) -> None:
    captured_at = datetime.now(UTC)
    async with db_sessions.begin() as session:
        session.add(
            StorageCapacitySample(
                captured_at=captured_at,
                database_bytes=1000,
                clob_book_batches_bytes=400,
                clob_market_snapshots_bytes=300,
                clob_batch_count=4,
                clob_snapshot_count=8,
                oldest_batch_at=captured_at - timedelta(hours=1),
                newest_batch_at=captured_at,
                free_disk_bytes=10_000,
                retention_policy_version="dual-token-retention-v1",
                details={"source": "integration-test"},
            )
        )

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(update(StorageCapacitySample).values(database_bytes=1001))
            await session.commit()
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(delete(StorageCapacitySample))
            await session.commit()
        await session.rollback()


async def test_database_rejects_foreign_reconciliation_and_settlement_evidence(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    outcome_value, markets = await _seed_value(db_sessions)
    observed_at = RESOLVED_AT + timedelta(hours=2)
    async with db_sessions.begin() as session:
        outcome = await upsert_gamma_outcome_event(session, outcome_value)
        await _link_projection(session, outcome, markets)
        station_id = markets[0].station_id
        assert station_id is not None
        session.add(
            DailyActual(
                station_id=station_id,
                date=markets[0].market_date,
                kind=markets[0].kind,
                temp_c=Decimal("19.5000"),
            )
        )
        case = await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome.event_id,
                classification=SettlementCaseClassification.RESOLVED,
                reason_code="gamma_outcome_attested",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=observed_at,
                source_url=outcome.source_url,
                source_response_sha256=outcome.source_response_sha256,
                resolution_event_id=outcome.id,
                resolution_vector_hash=outcome.outcome_vector_hash,
                details={"underlying_classification": "resolved"},
                resolved_at=outcome.fully_resolved_at,
            ),
        )
        case_id = case.id

    assert (
        await reconcile_resolutions(
            Settings(database_url=migrated_database_url, operator_api_key=None), db_sessions
        )
        == 2
    )
    async with db_sessions() as session:
        reconciliation = await session.scalar(
            select(ResolutionReconciliation).order_by(ResolutionReconciliation.market_id)
        )
    assert reconciliation is not None

    reconciliation_tampers = (
        {"event_id": "foreign-event"},
        {"resolution_vector_hash": "f" * 64},
        {"resolved_yes": not reconciliation.resolved_yes},
        {"market_id": markets[1].id},
    )
    for tamper in reconciliation_tampers:
        async with db_sessions() as session:
            with pytest.raises(
                DBAPIError,
                match="attested identity is immutable|does not match Gamma/market evidence",
            ):
                await session.execute(
                    update(ResolutionReconciliation)
                    .where(ResolutionReconciliation.id == reconciliation.id)
                    .values(**tamper)
                )
                await session.commit()
            await session.rollback()

    settlement_tampers = (
        {"event_id": "foreign-event"},
        {"resolution_vector_hash": "e" * 64},
        {"source_response_sha256": "d" * 64},
        {"resolved_at": outcome.fully_resolved_at + timedelta(seconds=1)},
    )
    for tamper in settlement_tampers:
        async with db_sessions() as session:
            with pytest.raises(
                DBAPIError,
                match="terminal evidence is immutable|does not match immutable Gamma",
            ):
                await session.execute(
                    update(SettlementCase).where(SettlementCase.id == case_id).values(**tamper)
                )
                await session.commit()
            await session.rollback()


async def test_evidence_projection_updates_are_versioned_and_history_is_append_only(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    outcome_value, markets = await _seed_value(db_sessions)
    pending_at = RESOLVED_AT + timedelta(hours=2)
    async with db_sessions.begin() as session:
        pending = await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome_value.event_id,
                classification=SettlementCaseClassification.VENUE_PENDING,
                reason_code="gamma_resolution_pending",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at,
                source_url=outcome_value.source_url,
                source_response_sha256=outcome_value.source_response_sha256,
                last_error="not final",
            ),
        )
        case_id = pending.id

    async with db_sessions.begin() as session:
        outcome = await upsert_gamma_outcome_event(session, outcome_value)
        await _link_projection(session, outcome, markets)
        station_id = markets[0].station_id
        assert station_id is not None
        session.add(
            DailyActual(
                station_id=station_id,
                date=markets[0].market_date,
                kind=markets[0].kind,
                temp_c=Decimal("19.5000"),
            )
        )
        await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome.event_id,
                classification=SettlementCaseClassification.RESOLVED,
                reason_code="gamma_outcome_attested",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at + timedelta(minutes=10),
                source_url=outcome.source_url,
                source_response_sha256=outcome.source_response_sha256,
                resolution_event_id=outcome.id,
                resolution_vector_hash=outcome.outcome_vector_hash,
                details={"observation": 1},
                resolved_at=outcome.fully_resolved_at,
            ),
        )

    async with db_sessions.begin() as session:
        await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome.event_id,
                classification=SettlementCaseClassification.RESOLVED,
                reason_code="gamma_outcome_attested",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at + timedelta(minutes=15),
                source_url=outcome.source_url,
                source_response_sha256=outcome.source_response_sha256,
                resolution_event_id=outcome.id,
                resolution_vector_hash=outcome.outcome_vector_hash,
                details={"observation": 1},
                resolved_at=outcome.fully_resolved_at,
            ),
        )

    async with db_sessions() as session:
        heartbeat_history_count = await session.scalar(
            select(func.count())
            .select_from(EvidenceVersionHistory)
            .where(
                EvidenceVersionHistory.entity_type == "settlement_case",
                EvidenceVersionHistory.entity_id == case_id,
            )
        )
    assert heartbeat_history_count == 1

    async with db_sessions.begin() as session:
        await upsert_settlement_case(
            session,
            SettlementCaseObservation(
                event_id=outcome.event_id,
                classification=SettlementCaseClassification.RESOLVED,
                reason_code="gamma_outcome_rechecked",
                event_resolves_at=RESOLVED_AT - timedelta(hours=1),
                observed_at=pending_at + timedelta(minutes=20),
                source_url=outcome.source_url,
                source_response_sha256=outcome.source_response_sha256,
                resolution_event_id=outcome.id,
                resolution_vector_hash=outcome.outcome_vector_hash,
                details={"observation": 2},
                resolved_at=outcome.fully_resolved_at,
            ),
        )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    assert await reconcile_resolutions(settings, db_sessions) == 2
    assert await reconcile_resolutions(settings, db_sessions) == 2

    async with db_sessions() as session:
        heartbeat_reconciliation_history = await session.scalar(
            select(func.count())
            .select_from(EvidenceVersionHistory)
            .where(EvidenceVersionHistory.entity_type == "resolution_reconciliation")
        )
    assert heartbeat_reconciliation_history == 0

    # Revision 0043 makes every prospective DailyActual append-only. Model a
    # later evidence upgrade by appending the reviewed official source for
    # each contract instead of rewriting the already observed METAR label.
    async with db_sessions.begin() as session:
        station_id = markets[0].station_id
        assert station_id is not None
        station = await session.get(Station, station_id)
        assert station is not None
        for market in markets:
            stored_market = await session.get(Market, market.id)
            assert stored_market is not None
            source_url = (
                "https://example.test/final/"
                f"{stored_market.polymarket_id}"
            )
            contract_rules_raw = stored_market.rules_raw
            contract = ResolutionContract(
                event_id=stored_market.event_id,
                rules_hash=hashlib.sha256(
                    contract_rules_raw.encode("utf-8")
                ).hexdigest(),
                source_family="national",
                source_url=source_url,
                station_code=station.icao,
                local_timezone=station.tz,
                statistic=stored_market.kind.value,
                source_unit="C",
                decimal_places=1,
                finalization_rule="source_published",
                license_status="unreviewed",
                storage_permitted=False,
                rules_raw=contract_rules_raw,
            )
            session.add(contract)
            await session.flush()
            await _persist_attested_official_actual(
                session,
                contract=contract,
                station=station,
                market=stored_market,
                source_value=Decimal("21.5"),
            )
    assert await reconcile_resolutions(settings, db_sessions) == 2

    async with db_sessions() as session:
        case_history = list(
            (
                await session.scalars(
                    select(EvidenceVersionHistory)
                    .where(
                        EvidenceVersionHistory.entity_type == "settlement_case",
                        EvidenceVersionHistory.entity_id == case_id,
                    )
                    .order_by(EvidenceVersionHistory.version)
                )
            ).all()
        )
        reconciliation_rows = list((await session.scalars(select(ResolutionReconciliation))).all())
        reconciliation_history = list(
            (
                await session.scalars(
                    select(EvidenceVersionHistory).where(
                        EvidenceVersionHistory.entity_type == "resolution_reconciliation"
                    )
                )
            ).all()
        )

    assert [row.version for row in case_history] == [1, 2]
    assert [row.snapshot["classification"] for row in case_history] == [
        "venue_pending",
        "resolved",
    ]
    assert all(len(row.snapshot_sha256) == 64 for row in case_history)
    assert len(reconciliation_rows) == 2
    assert len(reconciliation_history) == 2
    assert {row.entity_id for row in reconciliation_history} == {
        row.id for row in reconciliation_rows
    }

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="current evidence projection cannot be deleted"):
            await session.execute(delete(SettlementCase).where(SettlementCase.id == case_id))
            await session.commit()
        await session.rollback()

    history_id = case_history[0].id
    for statement in (
        update(EvidenceVersionHistory)
        .where(EvidenceVersionHistory.id == history_id)
        .values(version=99),
        delete(EvidenceVersionHistory).where(EvidenceVersionHistory.id == history_id),
    ):
        async with db_sessions() as session:
            with pytest.raises(DBAPIError, match="history is append-only"):
                await session.execute(statement)
                await session.commit()
            await session.rollback()


async def test_final_settlement_actual_projection_rejects_supersession(
    db_sessions: SessionFactory,
) -> None:
    _outcome_value, markets = await _seed_value(db_sessions)
    market = markets[0]
    assert market.station_id is not None
    source_url = f"https://example.test/final/{market.polymarket_id}"
    contract_rules_raw = market.rules_raw
    rules_hash = hashlib.sha256(contract_rules_raw.encode("utf-8")).hexdigest()
    async with db_sessions.begin() as session:
        station = await session.get(Station, market.station_id)
        assert station is not None
        contract = ResolutionContract(
            event_id=market.event_id,
            rules_hash=rules_hash,
            source_family="national",
            source_url=source_url,
            station_code=station.icao,
            local_timezone=station.tz,
            statistic=market.kind.value,
            source_unit="C",
            decimal_places=1,
            finalization_rule="source_published",
            license_status="unreviewed",
            storage_permitted=False,
            rules_raw=contract_rules_raw,
        )
        session.add(contract)
        await session.flush()
        actual = await _persist_attested_official_actual(
            session,
            contract=contract,
            station=station,
            market=market,
            source_value=Decimal("19.5"),
        )
        actual_id = actual.id

    async with db_sessions() as session:
        with pytest.raises(
            DBAPIError,
            match="settlement actual projection is immutable after 0039",
        ):
            await session.execute(
                update(SettlementActual)
                .where(SettlementActual.id == actual_id)
                .values(
                    source_value=Decimal("19.6"),
                    value_c=Decimal("19.6000"),
                    source_published_at=RESOLVED_AT + timedelta(minutes=10),
                )
            )
            await session.commit()
        await session.rollback()

    async with db_sessions() as session:
        current = await session.get(SettlementActual, actual_id)
        history_count = await session.scalar(
            select(func.count(EvidenceVersionHistory.id)).where(
                EvidenceVersionHistory.entity_type == "settlement_actual",
                EvidenceVersionHistory.entity_id == actual_id,
            )
        )
    assert current is not None and current.value_c == Decimal("19.5000")
    assert current.official_exact_actual_id is not None
    assert history_count == 0

    async with db_sessions() as session:
        with pytest.raises(
            DBAPIError,
            match="current evidence projection cannot be deleted",
        ):
            await session.execute(delete(SettlementActual).where(SettlementActual.id == actual_id))
            await session.commit()
        await session.rollback()


async def test_prospective_validation_is_zero_backfill_and_gamma_sealed(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    # This test keeps the close strictly after enrollment so it exercises both
    # sides of the temporal benchmark rule: the original signal snapshot is
    # pre-fill and excluded, while the second immutable batch is post-fill and
    # becomes the sole close witness.
    resolved_at = datetime.now(UTC) + timedelta(seconds=5)
    outcome_value, markets = await _seed_value(db_sessions, resolved_at=resolved_at)
    batch, targets = _validation_clob_batch(markets)
    cohort = "validation-ledger-test+0123456789abcdef"

    async with db_sessions.begin() as session:
        persisted = await persist_clob_batch(session, batch, targets)
        target = targets[0]
        snapshot = next(item for item in persisted.snapshots if item.market_id == target.market_id)
        signal_at = persisted.batch.first_seen_at + timedelta(microseconds=1)
        model = ModelProb(
            market_id=target.market_id,
            ts=signal_at,
            prob=Decimal("0.650000"),
            sigma=Decimal("1.5000"),
            horizon_h=Decimal("6.00"),
            components={"pipeline": {"cohort": cohort}},
            market_snapshot_id=snapshot.id,
            market_anchor_protocol=CLOB_DUAL_PROTOCOL,
        )
        session.add(model)
        await session.flush()
        dual_signal = Signal(
            market_id=target.market_id,
            ts=signal_at,
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.650000"),
            market_price=Decimal("0.520000"),
            edge=Decimal("0.130000"),
            kelly_frac=Decimal("0.020000"),
            size_usd=Decimal("20.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.PENDING,
            clob_snapshot_id=snapshot.id,
            token_id=target.yes_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )
        legacy_signal = Signal(
            market_id=targets[1].market_id,
            ts=signal_at,
            side=TradeSide.BUY_NO,
            model_prob=Decimal("0.400000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.100000"),
            kelly_frac=Decimal("0.020000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.C,
            status=SignalStatus.PENDING,
        )
        session.add_all([dual_signal, legacy_signal])
        await session.flush()
        runtime = await session.get(ProspectiveValidationRuntime, 1)
        assert runtime is not None
        db_now = await session.scalar(select(func.clock_timestamp()))
        assert db_now is not None
        written = await persist_paper_executions(
            session,
            [
                PaperExecutionValue(
                    signal_id=dual_signal.id,
                    market_id=target.market_id,
                    side=TradeSide.BUY_YES,
                    requested_usd=Decimal("20.0000"),
                    filled_usd=Decimal("20.0000"),
                    avg_price=Decimal("0.520000"),
                    size_shares=Decimal("38.461538"),
                    fee_usd=Decimal("0"),
                    total_cost_usd=Decimal("20.0000"),
                    filled_at=db_now,
                ),
                PaperExecutionValue(
                    signal_id=legacy_signal.id,
                    market_id=targets[1].market_id,
                    side=TradeSide.BUY_NO,
                    requested_usd=Decimal("10.0000"),
                    filled_usd=Decimal("10.0000"),
                    avg_price=Decimal("0.500000"),
                    size_shares=Decimal("20.000000"),
                    fee_usd=Decimal("0"),
                    total_cost_usd=Decimal("10.0000"),
                    filled_at=db_now,
                ),
            ],
        )
        assert written == 2
        close_batch, close_targets = _validation_clob_batch(markets)
        close_persisted = await persist_clob_batch(session, close_batch, close_targets)
        expected_close_snapshot = next(
            item for item in close_persisted.snapshots if item.market_id == target.market_id
        )

    async with db_sessions() as session:
        enrollments = (
            (
                await session.execute(
                    select(ProspectiveValidationEnrollment).order_by(
                        ProspectiveValidationEnrollment.id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(enrollments) == 2
        assert [(row.eligible, row.eligibility_reason) for row in enrollments] == [
            (True, "eligible"),
            (False, "non_dual_protocol"),
        ]
        assert enrollments[0].definition_hash == VALIDATION_DEFINITION_HASH

    async with db_sessions() as session:
        pending_dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=cohort,
        )
        # These immutable rows predate execution-cost intents.  They remain
        # auditable inventory above, but cannot enter the current formal gate
        # because they carry no predictive/execution continuity boundaries.
        assert pending_dataset.enrolled_orders == 0
        assert pending_dataset.eligible_enrollments == 0
        assert pending_dataset.excluded_enrollments == 0
        assert pending_dataset.pending_outcomes == 0
        assert pending_dataset.pending_blocks == 0
        assert pending_dataset.trades == []

    pending_app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    pending_app.state.sessions = db_sessions
    pending_app.state.read_sessions = pending_app.state.sessions
    async with AsyncClient(
        transport=ASGITransport(app=pending_app), base_url="http://test"
    ) as client:
        pending_response = await client.get("/api/v1/validation-ledger")
    assert pending_response.status_code == 200
    pending_payload = pending_response.json()
    assert pending_payload["cohort_scope"] == "current"
    # A formal current-scope report follows the approved release epoch, not a
    # merely newer model row.  This legacy inventory has no activated epoch;
    # callers can still inspect it explicitly by passing ``cohort`` below.
    assert pending_payload["selected_pipeline_cohort"] is None
    assert pending_payload["enrolled_orders"] == 0
    assert pending_payload["eligible_enrollments"] == 0
    assert pending_payload["closed_orders"] == 0
    assert pending_payload["pending_blocks"] == 0

    seconds_until_resolution = (resolved_at - datetime.now(UTC)).total_seconds()
    if seconds_until_resolution > 0:
        await asyncio.sleep(seconds_until_resolution + 0.05)
    async with db_sessions.begin() as session:
        event = await upsert_gamma_outcome_event(session, outcome_value)
        outcomes = gamma_outcome_map(event)
        for market in markets:
            stored = await session.get(Market, market.id)
            assert stored is not None
            stored.status = MarketStatus.RESOLVED
            stored.resolved_outcome = outcomes[market.id]
            stored.resolution_event_id = event.id
        await session.flush()
        assert (
            await seal_prospective_validation_outcomes(session, resolution_event_id=event.id) == 1
        )

    async with db_sessions() as session:
        outcome = await session.scalar(select(ProspectiveValidationOutcome))
        assert outcome is not None
        assert outcome.close_snapshot_id == expected_close_snapshot.id
        assert outcome.close_snapshot_hash == expected_close_snapshot.record_hash
        assert outcome.close_price == Decimal("0.510000")
        assert outcome.clv == Decimal("-0.010000")
        assert outcome.skill_pnl_usd == Decimal("-0.3846")
        assert outcome.luck_pnl_usd == Decimal("18.8461")
        dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=cohort,
        )
        # Gamma sealing preserves the legacy outcome evidence, but does not
        # retroactively manufacture an attested execution-intent lineage.
        assert dataset.enrolled_orders == 0
        assert dataset.eligible_enrollments == 0
        assert dataset.excluded_enrollments == 0
        assert dataset.pending_outcomes == 0
        assert dataset.pending_blocks == 0
        assert dataset.trades == []

    # A database client cannot clone an authentic row and alter one convenient
    # fact.  Each case reaches the BEFORE INSERT attestation trigger before the
    # duplicate-order/outcome uniqueness constraint and is rolled back to a
    # savepoint so all evidence dimensions are exercised independently.
    enrollment_tamper_cases = (
        (
            "'market_id', (SELECT max(id) FROM markets WHERE id <> source.market_id)",
            "source identity mismatch",
        ),
        ("'order_record_hash', repeat('0', 64)", "source hash mismatch"),
        (
            "'eligible', false, 'eligibility_reason', 'eligible'",
            "eligibility mismatch",
        ),
        (
            "'enrolled_at', to_jsonb(source.filled_at - interval '1 second')",
            "runtime boundary mismatch",
        ),
    )
    async with db_sessions.begin() as session:
        for patch_sql, expected_error in enrollment_tamper_cases:
            clone_sql = f"""
                INSERT INTO prospective_validation_enrollments
                SELECT (jsonb_populate_record(
                    NULL::prospective_validation_enrollments,
                    to_jsonb(source) || jsonb_build_object(
                        'id', nextval(pg_get_serial_sequence(
                            'prospective_validation_enrollments', 'id'
                        )),
                        {patch_sql}
                    )
                )).*
                FROM prospective_validation_enrollments AS source
                WHERE source.eligible
                ORDER BY source.id
                LIMIT 1
            """
            with pytest.raises(DBAPIError, match=expected_error):
                async with session.begin_nested():
                    await session.execute(text(clone_sql))

    outcome_tamper_cases = (
        ("'gamma_record_hash', repeat('0', 64)", "Gamma/market identity mismatch"),
        ("'pnl_usd', source.pnl_usd + 1", "payout arithmetic mismatch"),
        (
            "'close_snapshot_id', (SELECT min(id) FROM clob_market_snapshots "
            "WHERE market_id = (SELECT market_id FROM "
            "prospective_validation_enrollments WHERE id = source.enrollment_id)), "
            "'close_snapshot_hash', (SELECT record_hash FROM clob_market_snapshots "
            "WHERE id = (SELECT min(id) FROM clob_market_snapshots "
            "WHERE market_id = (SELECT market_id FROM "
            "prospective_validation_enrollments "
            "WHERE id = source.enrollment_id)))",
            "close/CLV arithmetic mismatch",
        ),
        ("'clv', source.clv + 0.01", "close/CLV arithmetic mismatch"),
        ("'record_hash', repeat('0', 64)", "outcome record hash mismatch"),
    )
    async with db_sessions.begin() as session:
        for patch_sql, expected_error in outcome_tamper_cases:
            clone_sql = f"""
                INSERT INTO prospective_validation_outcomes
                SELECT (jsonb_populate_record(
                    NULL::prospective_validation_outcomes,
                    to_jsonb(source) || jsonb_build_object(
                        'id', nextval(pg_get_serial_sequence(
                            'prospective_validation_outcomes', 'id'
                        )),
                        {patch_sql}
                    )
                )).*
                FROM prospective_validation_outcomes AS source
                ORDER BY source.id
                LIMIT 1
            """
            with pytest.raises(DBAPIError, match=expected_error):
                async with session.begin_nested():
                    await session.execute(text(clone_sql))

    # The deferred database invariant makes the enrollment atomic: a crash or
    # forgotten repository hook cannot commit a post-boundary terminal fill.
    async with db_sessions() as session:
        unguarded_at = await session.scalar(select(func.clock_timestamp()))
        assert unguarded_at is not None
        unguarded_signal = Signal(
            market_id=targets[1].market_id,
            ts=unguarded_at,
            side=TradeSide.BUY_NO,
            model_prob=Decimal("0.400000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.100000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("3.0000"),
            confidence=SignalConfidence.C,
            status=SignalStatus.EXECUTED,
        )
        session.add(unguarded_signal)
        await session.flush()
        session.add(
            Order(
                signal_id=unguarded_signal.id,
                mode=ExecutionMode.PAPER,
                side=TradeSide.BUY_NO,
                price=Decimal("0.500000"),
                size_usd=Decimal("3.0000"),
                filled_usd=Decimal("3.0000"),
                fee_usd=Decimal("0"),
                status="filled",
                filled_at=unguarded_at,
            )
        )
        with pytest.raises(DBAPIError, match="lacks prospective enrollment"):
            await session.commit()
        await session.rollback()

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/validation-ledger",
            params={"mode": "paper", "cohort": cohort},
        )
    assert response.status_code == 200
    payload = response.json()
    assert payload["source_integrity"] == "database-attested-order-fill+gamma+clob-v1"
    assert payload["selected_pipeline_cohort"] == cohort
    assert payload["enrolled_orders"] == 0
    assert payload["closed_orders"] == 0
    assert payload["independent_blocks"] == 0
    assert payload["largest_order_id"] is None


async def test_prospective_enrollment_cohort_anchor_and_outcome_cutoff(
    db_sessions: SessionFactory,
) -> None:
    outcome_value, markets = await _seed_value(db_sessions)
    batch, targets = _validation_clob_batch(markets)
    cohort = "same-cohort-test+0123456789abcdef"

    async with db_sessions.begin() as session:
        persisted = await persist_clob_batch(session, batch, targets)
        snapshots = {item.market_id: item for item in persisted.snapshots}
        base_at = persisted.batch.first_seen_at
        fill_at = await session.scalar(select(func.clock_timestamp()))
        assert fill_at is not None

        same_target = targets[0]
        same_snapshot = snapshots[same_target.market_id]
        same_models = [
            ModelProb(
                market_id=same_target.market_id,
                ts=base_at + timedelta(microseconds=offset),
                prob=Decimal("0.650000"),
                sigma=Decimal("1.5000"),
                horizon_h=Decimal("6.00"),
                components={"pipeline": {"cohort": cohort}},
                market_snapshot_id=same_snapshot.id,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
            for offset in (1, 2)
        ]
        same_signal = Signal(
            market_id=same_target.market_id,
            ts=base_at + timedelta(microseconds=3),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.650000"),
            market_price=Decimal("0.520000"),
            edge=Decimal("0.140000"),
            kelly_frac=Decimal("0.020000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.EXECUTED,
            clob_snapshot_id=same_snapshot.id,
            token_id=same_target.yes_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )
        post_outcome_signal = Signal(
            market_id=same_target.market_id,
            ts=base_at + timedelta(microseconds=4),
            side=TradeSide.BUY_NO,
            model_prob=Decimal("0.650000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.100000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("5.0000"),
            confidence=SignalConfidence.C,
            status=SignalStatus.PENDING,
            clob_snapshot_id=same_snapshot.id,
            token_id=same_target.no_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )

        ambiguous_target = targets[1]
        ambiguous_snapshot = snapshots[ambiguous_target.market_id]
        ambiguous_models = [
            ModelProb(
                market_id=ambiguous_target.market_id,
                ts=base_at + timedelta(microseconds=offset),
                prob=Decimal("0.400000"),
                sigma=Decimal("1.5000"),
                horizon_h=Decimal("6.00"),
                components={"pipeline": {"cohort": candidate_cohort}},
                market_snapshot_id=ambiguous_snapshot.id,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
            for offset, candidate_cohort in (
                (1, "cohort-a+0123456789abcdef"),
                (2, "cohort-b+0123456789abcdef"),
            )
        ]
        session.add_all([*same_models, *ambiguous_models, same_signal, post_outcome_signal])
        ambiguous_signal = Signal(
            market_id=ambiguous_target.market_id,
            ts=base_at + timedelta(microseconds=3),
            side=TradeSide.BUY_NO,
            model_prob=Decimal("0.400000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.090000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.C,
            status=SignalStatus.EXECUTED,
            clob_snapshot_id=ambiguous_snapshot.id,
            token_id=ambiguous_target.no_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )
        bypass_signal = Signal(
            market_id=ambiguous_target.market_id,
            ts=base_at + timedelta(microseconds=5),
            side=TradeSide.BUY_NO,
            model_prob=Decimal("0.400000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.050000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("1.0000"),
            confidence=SignalConfidence.C,
            status=SignalStatus.EXECUTED,
        )
        session.add_all([ambiguous_signal, bypass_signal])
        await session.flush()

        same_order = Order(
            signal_id=same_signal.id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{same_signal.id}",
            token_id=same_target.yes_token_id,
            side=TradeSide.BUY_YES,
            price=Decimal("0.510000"),
            size_usd=Decimal("10.0000"),
            filled_usd=Decimal("10.0000"),
            fee_usd=Decimal("0"),
            status="filled",
            filled_at=fill_at,
        )
        ambiguous_order = Order(
            signal_id=ambiguous_signal.id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{ambiguous_signal.id}",
            token_id=ambiguous_target.no_token_id,
            side=TradeSide.BUY_NO,
            price=Decimal("0.490000"),
            size_usd=Decimal("10.0000"),
            filled_usd=Decimal("10.0000"),
            fee_usd=Decimal("0"),
            status="filled",
            filled_at=fill_at,
        )
        bypass_order = Order(
            signal_id=bypass_signal.id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{bypass_signal.id}",
            side=TradeSide.BUY_NO,
            price=Decimal("0.500000"),
            size_usd=Decimal("1.0000"),
            filled_usd=Decimal("0"),
            fee_usd=Decimal("0"),
            status="partially_filled",
            filled_at=fill_at,
        )
        session.add_all([same_order, ambiguous_order, bypass_order])
        await session.flush()
        same_enrollment_id = await enroll_terminal_order(session, same_order.id)
        ambiguous_enrollment_id = await enroll_terminal_order(session, ambiguous_order.id)
        assert same_enrollment_id is not None
        assert ambiguous_enrollment_id is not None
        bypass_order_id = bypass_order.id

    async with db_sessions() as session:
        await session.execute(
            update(Order).where(Order.id == bypass_order_id).values(filled_usd=Decimal("1.0000"))
        )
        with pytest.raises(DBAPIError, match="lacks prospective enrollment"):
            await session.commit()
        await session.rollback()

    async with db_sessions() as session:
        same_enrollment = await session.get(
            ProspectiveValidationEnrollment,
            same_enrollment_id,
        )
        ambiguous_enrollment = await session.get(
            ProspectiveValidationEnrollment,
            ambiguous_enrollment_id,
        )
        assert same_enrollment is not None
        assert same_enrollment.eligible is True
        assert same_enrollment.eligibility_reason == "eligible"
        assert same_enrollment.model_prob_id == same_models[-1].id
        assert same_enrollment.pipeline_cohort == cohort
        assert ambiguous_enrollment is not None
        assert ambiguous_enrollment.eligible is False
        assert ambiguous_enrollment.eligibility_reason == "model_anchor_ambiguous"
        assert ambiguous_enrollment.pipeline_cohort is None

    async with db_sessions.begin() as session:
        await lock_gamma_event(session, outcome_value.event_id)
        event = await upsert_gamma_outcome_event(session, outcome_value)
        outcomes = gamma_outcome_map(event)
        for market in markets:
            stored = await session.get(Market, market.id)
            assert stored is not None
            stored.status = MarketStatus.RESOLVED
            stored.resolved_outcome = outcomes[market.id]
            stored.resolution_event_id = event.id

    async with db_sessions.begin() as session:
        stored_signal = await session.get(Signal, post_outcome_signal.id)
        assert stored_signal is not None
        stored_signal.status = SignalStatus.EXECUTED
        post_fill_at = await session.scalar(select(func.clock_timestamp()))
        assert post_fill_at is not None
        post_outcome_order = Order(
            signal_id=post_outcome_signal.id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{post_outcome_signal.id}",
            token_id=same_target.no_token_id,
            side=TradeSide.BUY_NO,
            price=Decimal("0.490000"),
            size_usd=Decimal("5.0000"),
            filled_usd=Decimal("5.0000"),
            fee_usd=Decimal("0"),
            status="filled",
            filled_at=post_fill_at,
        )
        session.add(post_outcome_order)
        await session.flush()
        post_enrollment_id = await enroll_terminal_order(session, post_outcome_order.id)
        assert post_enrollment_id is not None

    async with db_sessions() as session:
        post_enrollment = await session.get(
            ProspectiveValidationEnrollment,
            post_enrollment_id,
        )
        assert post_enrollment is not None
        assert post_enrollment.eligible is False
        assert post_enrollment.eligibility_reason == "outcome_already_known"
