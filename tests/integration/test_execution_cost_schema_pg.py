from __future__ import annotations

import asyncio
import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.clob import (
    ClobBooksBatch,
    parse_strict_book_snapshot,
)
from stratus.collectors.gamma import (
    ExpectedGammaMarket,
    GammaEventSnapshot,
    parse_complete_event_resolution,
)
from stratus.config import Settings
from stratus.db.models import (
    ExecutionCostObligation,
    ExecutionCostRuntime,
    ExecutionCostSeal,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
    ExecutionPostfillMark,
    ExecutionReconciliationObservation,
    Market,
    MarketStatus,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    ProspectiveValidationEnrollment,
    ProspectiveValidationOutcome,
    ProspectiveValidationRuntime,
    Signal,
    SignalConfidence,
    SignalStatus,
    StationReliabilityStatus,
    TradeSide,
)
from stratus.jobs.backfill_fees import backfill_fees
from stratus.jobs.maintain_execution_cost_evidence import (
    maintain_execution_cost_evidence,
)
from stratus.repositories.accuracy_promotions import (
    PROMOTION_REGISTRATION_EPOCH,
    build_effective_promotion_manifest,
    run_accuracy_promotion_protocol,
)
from stratus.repositories.analytics import execution_fill_aggregate_subquery
from stratus.repositories.clob_snapshots import PersistedClobBatch, persist_clob_batch
from stratus.repositories.evidence_continuity import (
    EvidenceLanePlan,
    append_probability_pipeline_release_epoch,
    build_probability_pipeline_release_plan,
    current_probability_pipeline_release_epoch,
    persist_probability_pipeline_release_plan,
    probability_pipeline_release_cohort,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.execution_cost_health import execution_cost_health_summary
from stratus.repositories.execution_cost_ledger import (
    ExecutionCostIntegrityError,
    PaperFillLegSpec,
    append_live_reconciliation_observation,
    create_intent_for_order,
    record_paper_fill,
    record_terminal_no_fill,
    record_terminal_unverified_live_execution,
)
from stratus.repositories.gamma_outcomes import (
    build_gamma_outcome_event_value,
    gamma_outcome_map,
    upsert_gamma_outcome_event,
)
from stratus.repositories.market_prices import PriceTarget
from stratus.repositories.selection_policy import (
    SelectionPolicyLedgerCounts,
    capture_selection_policy_decisions,
    selection_policy_ledger_counts,
    valid_selection_policy_decisions,
)
from stratus.repositories.station_reliability import (
    StationReliabilitySnapshotValue,
    insert_station_reliability_snapshot,
)
from stratus.repositories.validation_ledger import (
    ProspectiveValidationIntegrityError,
    _source_hashes,
    enroll_terminal_order,
    load_prospective_validation_dataset,
    seal_prospective_validation_outcomes,
)
from stratus.services.evidence_release_manifest import ALEMBIC_HEAD
from stratus.services.execution_cost import (
    EXECUTION_COST_DEFINITION_HASH,
    EXECUTION_COST_DEFINITION_VERSION,
    execution_record_hash,
    source_payload_hash,
)
from stratus.services.live_analytics import prospective_trade_outcomes
from stratus.services.pipeline_identity import (
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
)
from stratus.services.sealed_accuracy import (
    sealed_experiment_fingerprint,
    sealed_expert_version,
)
from stratus.services.selection_policy import evaluate_recorded_selection_policies
from stratus.services.station_reliability import station_reliability_policy_hash
from stratus.services.telegram_reports import calculate_live_gate

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
_TEST_SETTINGS = Settings(_env_file=None)
TEST_PIPELINE_VERSION = _TEST_SETTINGS.probability_pipeline_version
TEST_MATERIAL_FINGERPRINT = probability_pipeline_fingerprint(_TEST_SETTINGS)
TEST_ACTIVATION_MANIFEST_HASH = build_effective_promotion_manifest(()).manifest_hash
TEST_IMAGE_DIGEST = "b" * 64
TEST_SUCCESSOR_IMAGE_DIGEST = "c" * 64
TEST_RELEASE_MANIFEST = {
    "protocol": "execution-cost-release-test-v1",
    "revision": "0040",
}
TEST_LANE_NAMES = (
    "predictive_validation",
    "selection_policy",
    "execution_evidence",
    "operational_legacy",
    "station_lifecycle",
    "official_resolution_truth",
)
TEST_PIPELINE_COHORT = probability_pipeline_release_cohort(
    pipeline_version=TEST_PIPELINE_VERSION,
    material_fingerprint=TEST_MATERIAL_FINGERPRINT,
    activation_manifest_hash=TEST_ACTIVATION_MANIFEST_HASH,
)
_INCIDENT_RECONCILIATION_SQL = (
    Path(__file__).parents[2]
    / "scripts"
    / "sql"
    / "execution_cost_incident_reconciliation.sql"
)


def _incident_reconciliation_statement(block_name: str) -> str:
    source = _INCIDENT_RECONCILIATION_SQL.read_text(encoding="utf-8")
    begin_marker = f"-- BEGIN {block_name}"
    end_marker = f"-- END {block_name}"
    assert source.count(begin_marker) == 1
    assert source.count(end_marker) == 1
    statement = source.split(begin_marker, 1)[1].split(end_marker, 1)[0].strip()
    assert statement.endswith(";")
    assert not statement.startswith("\\")
    return statement


def _test_lane_plans(*, action: str) -> tuple[EvidenceLanePlan, ...]:
    plans: list[EvidenceLanePlan] = []
    for lane_name in TEST_LANE_NAMES:
        manifest = {
            "protocol": "execution-cost-lane-test-v1",
            "lane": lane_name,
            "identity": "baseline",
        }
        plans.append(
            EvidenceLanePlan(
                lane_name=lane_name,
                action=action,
                identity_hash=source_payload_hash(manifest),
                manifest=manifest,
                rationale=f"Preserve execution integration evidence for {lane_name}.",
            )
        )
    return tuple(plans)


def _versioned_lane_plans(
    *,
    identities: dict[str, str],
    forked: set[str],
) -> tuple[EvidenceLanePlan, ...]:
    plans: list[EvidenceLanePlan] = []
    for lane_name in TEST_LANE_NAMES:
        manifest = {
            "protocol": "execution-cost-lane-test-v1",
            "lane": lane_name,
            "identity": identities.get(lane_name, "baseline"),
        }
        plans.append(
            EvidenceLanePlan(
                lane_name=lane_name,
                action="fork" if lane_name in forked else "continue",
                identity_hash=source_payload_hash(manifest),
                manifest=manifest,
                rationale=f"Exercise validation continuity for {lane_name}.",
            )
        )
    return tuple(plans)


async def _activate_successor_release(
    session: AsyncSession,
    *,
    image_digest: str,
    lane_plans: tuple[EvidenceLanePlan, ...],
) -> None:
    current = await current_probability_pipeline_release_epoch(session)
    assert current is not None
    plan = build_probability_pipeline_release_plan(
        pipeline_version=TEST_PIPELINE_VERSION,
        material_fingerprint=TEST_MATERIAL_FINGERPRINT,
        activation_manifest_hash=TEST_ACTIVATION_MANIFEST_HASH,
        image_digest=image_digest,
        previous_epoch_id=current.id,
        release_manifest=TEST_RELEASE_MANIFEST,
        lane_plans=lane_plans,
        reviewed_by="pytest-execution",
        approval_reason="Exercise prospective validation lane isolation.",
    )
    await persist_probability_pipeline_release_plan(session, plan)
    await append_probability_pipeline_release_epoch(
        session,
        plan,
        expected_plan_hash=plan.plan_hash,
    )


def _book(
    token_id: str,
    condition_id: str,
    timestamp_ms: int,
    *,
    bid: str,
    ask: str,
) -> dict[str, object]:
    return {
        "asset_id": token_id,
        "market": condition_id,
        "timestamp": str(timestamp_ms),
        "hash": f"source-{token_id}-{timestamp_ms}",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [{"price": bid, "size": "100"}],
        "asks": [{"price": ask, "size": "100"}],
    }


async def _persist_snapshot(
    session: AsyncSession,
    *,
    market_id: int,
    event_id: str,
    condition_id: str,
    yes_token_id: str,
    no_token_id: str,
    request_started_at: datetime | None = None,
) -> PersistedClobBatch:
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    source_at = now.astimezone(UTC).replace(microsecond=0) - timedelta(seconds=2)
    timestamp_ms = int(source_at.timestamp()) * 1000
    raw_books = (
        _book(yes_token_id, condition_id, timestamp_ms, bid="0.40", ask="0.42"),
        _book(no_token_id, condition_id, timestamp_ms, bid="0.58", ask="0.60"),
    )
    body = json.dumps(raw_books, separators=(",", ":")).encode("utf-8")
    batch = ClobBooksBatch(
        source_url="https://clob.polymarket.test/books",
        requested_token_ids=(yes_token_id, no_token_id),
        request_started_at=request_started_at or now,
        response_received_at=now,
        response_body=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(item) for item in raw_books),
    )
    return await persist_clob_batch(
        session,
        batch,
        [
            PriceTarget(
                market_id=market_id,
                event_id=event_id,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
            )
        ],
    )


async def _seed_decision(
    session: AsyncSession,
    *,
    suffix: str,
    mode: ExecutionMode = ExecutionMode.PAPER,
    activate_release: bool = True,
    model_components: dict[str, object] | None = None,
    paper_order_price: Decimal = Decimal("0.590000"),
    paper_size_usd: Decimal = Decimal("5.9000"),
    release_image_digest: str = TEST_IMAGE_DIGEST,
    attach_station_reliability: bool = True,
) -> dict[str, Any]:
    active_epoch = (
        await current_probability_pipeline_release_epoch(session) if activate_release else None
    )
    if activate_release and active_epoch is None:
        plan = build_probability_pipeline_release_plan(
            pipeline_version=TEST_PIPELINE_VERSION,
            material_fingerprint=TEST_MATERIAL_FINGERPRINT,
            activation_manifest_hash=TEST_ACTIVATION_MANIFEST_HASH,
            image_digest=TEST_IMAGE_DIGEST,
            previous_epoch_id=None,
            release_manifest=TEST_RELEASE_MANIFEST,
            lane_plans=_test_lane_plans(action="fork"),
            reviewed_by="pytest-execution",
            approval_reason="Execution cost integration test cohort activation.",
        )
        await persist_probability_pipeline_release_plan(session, plan)
        await append_probability_pipeline_release_epoch(
            session,
            plan,
            expected_plan_hash=plan.plan_hash,
        )
    elif activate_release:
        assert active_epoch is not None
        assert active_epoch.cohort == TEST_PIPELINE_COHORT
        assert active_epoch.image_digest == release_image_digest
    else:
        assert active_epoch is None
    if activate_release:
        await session.execute(
            text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
            {"digest": release_image_digest},
        )
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    station_id = await session.scalar(
        text(
            """
            INSERT INTO stations (icao, name, city, lat, lon, tz, active, stage)
            VALUES (:icao, :name, :name, 40, -73, 'UTC', true, 'paper')
            RETURNING id
            """
        ),
        {"icao": f"E{suffix:0>3}"[-4:], "name": f"Execution {suffix}"},
    )
    assert station_id is not None
    station_reliability = await insert_station_reliability_snapshot(
        session,
        StationReliabilitySnapshotValue(
            station_id=int(station_id),
            evaluated_at=now,
            window_days=_TEST_SETTINGS.station_reliability_window_days,
            comparable_units=0,
            match_units=0,
            conflict_units=0,
            exact_source_mismatch_units=0,
            clear_mismatch_units=0,
            exact_truth_gap_units=0,
            boundary_units=0,
            unavailable_units=0,
            reliability_score=None,
            reliability_lower_bound=None,
            status=StationReliabilityStatus.COLLECTING,
            latest_conflict_at=None,
            latest_conflict_date=None,
            latest_conflict_kind=None,
            resolution_cooldown_until=None,
            latest_observation_at=now,
            observation_local_date=now.date(),
            observation_cooldown_until=None,
            reasons=("station_reliability_collecting",),
            policy_hash=station_reliability_policy_hash(_TEST_SETTINGS),
            source_hash=hashlib.sha256(
                f"execution-test-station-reliability:{suffix}".encode()
            ).hexdigest(),
        ),
    )
    event_id = f"execution-event-{suffix}"
    condition_id = f"0xexecution-condition-{suffix}"
    yes_token_id = f"execution-yes-{suffix}"
    no_token_id = f"execution-no-{suffix}"
    market_id = await session.scalar(
        text(
            """
            INSERT INTO markets (
                polymarket_id, event_id, condition_id, yes_token_id, no_token_id,
                station_id, market_date, kind, rules_raw, station_verified,
                status, created_at, resolves_at
            ) VALUES (
                :external_id, :event_id, :condition_id, :yes_token_id, :no_token_id,
                :station_id, :market_date, 'high', 'execution cost test', true,
                'active', :created_at, :resolves_at
            ) RETURNING id
            """
        ),
        {
            "external_id": f"execution-market-{suffix}",
            "event_id": event_id,
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "station_id": station_id,
            "market_date": now.date(),
            "created_at": now - timedelta(days=1),
            "resolves_at": now + timedelta(days=1),
        },
    )
    assert market_id is not None
    decision_batch = await _persist_snapshot(
        session,
        market_id=int(market_id),
        event_id=event_id,
        condition_id=condition_id,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
    )
    snapshot = decision_batch.snapshots[0]
    decision_at = decision_batch.batch.first_seen_at
    model = ModelProb(
        market_id=int(market_id),
        ts=decision_at,
        prob=Decimal("0.400000"),
        sigma=Decimal("1.0000"),
        horizon_h=Decimal("6.00"),
        components=(
            model_components
            if model_components is not None
            else {"pipeline": {"cohort": TEST_PIPELINE_COHORT}}
        ),
        market_snapshot_id=snapshot.id,
        market_anchor_protocol="clob-dual-v1",
    )
    session.add(model)
    await session.flush()
    signal = Signal(
        market_id=int(market_id),
        ts=decision_at,
        side=TradeSide.BUY_NO,
        model_prob=Decimal("0.400000"),
        market_price=Decimal("0.600000"),
        edge=Decimal("0.000000"),
        kelly_frac=Decimal("0.000000"),
        size_usd=(Decimal("6.0000") if mode is ExecutionMode.LIVE else paper_size_usd),
        confidence=SignalConfidence.A,
        status=SignalStatus.PENDING,
        clob_snapshot_id=snapshot.id,
        station_reliability_snapshot_id=(
            station_reliability.id if attach_station_reliability else None
        ),
        token_id=no_token_id,
        benchmark_protocol="clob-dual-v1",
    )
    session.add(signal)
    await session.flush()
    order = Order(
        signal_id=signal.id,
        mode=mode,
        clob_order_id=f"execution-order-{suffix}",
        token_id=no_token_id,
        side=TradeSide.BUY_NO,
        price=(Decimal("0.600000") if mode is ExecutionMode.LIVE else paper_order_price),
        size_usd=signal.size_usd,
        filled_usd=Decimal("0"),
        status="submitting" if mode is ExecutionMode.LIVE else "paper_resting",
    )
    session.add(order)
    await session.flush()
    await session.refresh(order)
    return {
        "market_id": int(market_id),
        "event_id": event_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "snapshot": snapshot,
        "station_reliability_snapshot": station_reliability,
        "model": model,
        "signal": signal,
        "order": order,
    }


async def _detach_test_station_reliability_evidence(
    session: AsyncSession,
    seed: dict[str, Any],
) -> None:
    """Remove only this test's 0042 witness before exercising older schemas."""

    await session.execute(text("SET LOCAL session_replication_role = replica"))
    await session.execute(
        text(
            "UPDATE signals SET station_reliability_snapshot_id = NULL "
            "WHERE id = :signal_id"
        ),
        {"signal_id": seed["signal"].id},
    )
    await session.execute(
        text("DELETE FROM station_reliability_snapshots WHERE id = :snapshot_id"),
        {"snapshot_id": seed["station_reliability_snapshot"].id},
    )


async def _seal_seed_gamma_outcome(
    session: AsyncSession,
    seed: dict[str, Any],
    *,
    yes_wins: bool,
) -> ProspectiveValidationOutcome:
    """Resolve one modern test fill through the immutable Gamma projection."""

    # Force a distinct post-fill batch so the closing-line witness cannot be
    # the decision/fill snapshot accidentally deduplicated within one second.
    await asyncio.sleep(1.05)
    close_batch = await _persist_snapshot(
        session,
        market_id=seed["market_id"],
        event_id=seed["event_id"],
        condition_id=seed["condition_id"],
        yes_token_id=seed["yes_token_id"],
        no_token_id=seed["no_token_id"],
    )
    resolved_at = close_batch.batch.first_seen_at
    market = await session.get(Market, seed["market_id"])
    assert market is not None
    companion = Market(
        polymarket_id=f"{market.polymarket_id}-companion",
        event_id=market.event_id,
        condition_id=f"{market.condition_id}-companion",
        yes_token_id=f"{market.yes_token_id}-companion",
        no_token_id=f"{market.no_token_id}-companion",
        station_id=market.station_id,
        market_date=market.market_date,
        kind=market.kind,
        rules_raw=f"{market.rules_raw} companion bucket",
        station_verified=True,
        status=MarketStatus.ACTIVE,
        created_at=market.created_at,
        resolves_at=market.resolves_at,
    )
    session.add(companion)
    await session.flush()

    def raw_market(row: Market, *, row_yes_wins: bool) -> dict[str, object]:
        return {
            "id": row.polymarket_id,
            "closed": True,
            "umaResolutionStatus": "resolved",
            "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
            "outcomes": '["Yes", "No"]',
            "outcomePrices": '["1", "0"]' if row_yes_wins else '["0", "1"]',
            "clobTokenIds": json.dumps([row.yes_token_id, row.no_token_id]),
            "conditionId": row.condition_id,
            "questionID": f"question-{row.polymarket_id}",
            "resolvedBy": "0xexecution-gate-test",
        }

    event_markets = [market, companion]
    payload: dict[str, object] = {
        "id": market.event_id,
        "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": (resolved_at + timedelta(seconds=1)).isoformat().replace(
            "+00:00", "Z"
        ),
        "markets": [
            raw_market(market, row_yes_wins=yes_wins),
            raw_market(companion, row_yes_wins=not yes_wins),
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    witnessed = GammaEventSnapshot(
        event_id=market.event_id,
        payload=payload,
        source_url=f"https://gamma-api.polymarket.test/events/{market.event_id}",
        response_body=body,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
        fetched_at=datetime.now(UTC),
        http_date=resolved_at + timedelta(seconds=2),
        last_modified=resolved_at + timedelta(seconds=1),
        etag=f'"{market.event_id}-resolved"',
    )
    expected = [
        ExpectedGammaMarket(
            market_id=row.id,
            polymarket_id=row.polymarket_id,
            condition_id=row.condition_id,
            yes_token_id=row.yes_token_id,
            no_token_id=row.no_token_id,
            rules_hash=hashlib.sha256(row.rules_raw.encode("utf-8")).hexdigest(),
        )
        for row in event_markets
    ]
    resolution = parse_complete_event_resolution(
        payload,
        expected_event_id=market.event_id,
        expected_markets=expected,
    )
    event = await upsert_gamma_outcome_event(
        session,
        build_gamma_outcome_event_value(witnessed, resolution, event_markets),
    )
    outcomes = gamma_outcome_map(event)
    for row in event_markets:
        row.status = MarketStatus.RESOLVED
        row.resolved_outcome = outcomes[row.id]
        row.resolution_event_id = event.id
    await session.flush()
    assert (
        await seal_prospective_validation_outcomes(
            session,
            resolution_event_id=event.id,
        )
        == 1
    )
    outcome = await session.scalar(
        select(ProspectiveValidationOutcome).join(
            ProspectiveValidationEnrollment,
            ProspectiveValidationEnrollment.id
            == ProspectiveValidationOutcome.enrollment_id,
        ).where(ProspectiveValidationEnrollment.order_id == seed["order"].id)
    )
    assert outcome is not None
    return outcome


async def _fill_and_seal_modern_trade(
    session: AsyncSession,
    *,
    suffix: str,
    image_digest: str,
) -> int:
    seed = await _seed_decision(
        session,
        suffix=suffix,
        release_image_digest=image_digest,
    )
    intent = await create_intent_for_order(
        session,
        seed["order"].id,
        release_image_digest=image_digest,
        order_style="maker",
        expected_fee_usd=Decimal("0"),
        expected_slippage_per_share=Decimal("0"),
    )
    requested_at = await session.scalar(select(func.clock_timestamp()))
    assert requested_at is not None
    await asyncio.sleep(0.15)
    fill_batch = await _persist_snapshot(
        session,
        market_id=seed["market_id"],
        event_id=seed["event_id"],
        condition_id=seed["condition_id"],
        yes_token_id=seed["yes_token_id"],
        no_token_id=seed["no_token_id"],
        request_started_at=requested_at,
    )
    await record_paper_fill(
        session,
        intent.id,
        fill_batch.snapshots[0].id,
        [
            PaperFillLegSpec(
                sequence=1,
                shares=Decimal("10"),
                price=Decimal("0.59"),
                fee_usd=Decimal("0"),
                liquidity_role="maker",
            )
        ],
    )
    await _seal_seed_gamma_outcome(session, seed, yes_wins=False)
    return int(seed["order"].id)


async def _append_unactivated_model_probability(session: AsyncSession) -> None:
    """Append a newer model row that was never approved as a release epoch."""

    current = await session.scalar(select(ModelProb).order_by(ModelProb.id.desc()).limit(1))
    assert current is not None
    components = json.loads(json.dumps(current.components))
    pipeline = components.setdefault("pipeline", {})
    assert isinstance(pipeline, dict)
    pipeline["cohort"] = "unactivated-stray-cohort"
    session.add(
        ModelProb(
            market_id=current.market_id,
            ts=current.ts + timedelta(microseconds=1),
            prob=current.prob,
            sigma=current.sigma,
            horizon_h=current.horizon_h,
            components=components,
            market_snapshot_id=current.market_snapshot_id,
            market_anchor_protocol=current.market_anchor_protocol,
        )
    )
    await session.flush()


def _intent(seed: dict[str, Any], *, wrong_midpoint: bool = False) -> ExecutionOrderIntent:
    order: Order = seed["order"]
    signal: Signal = seed["signal"]
    model: ModelProb = seed["model"]
    snapshot = seed["snapshot"]
    _, signal_hash, model_hash = _source_hashes(order, signal, model)
    assert model_hash is not None
    return ExecutionOrderIntent(
        order_id=order.id,
        signal_id=signal.id,
        model_prob_id=model.id,
        market_id=seed["market_id"],
        decision_clob_snapshot_id=snapshot.id,
        mode=order.mode,
        side=order.side,
        token_id=seed["no_token_id"],
        pipeline_cohort=TEST_PIPELINE_COHORT,
        signal_record_hash=signal_hash,
        model_record_hash=model_hash,
        snapshot_record_hash=snapshot.record_hash,
        yes_model_probability=Decimal("0.40000000"),
        side_model_probability=Decimal("0.60000000"),
        side_best_bid=Decimal("0.58000000"),
        side_best_ask=Decimal("0.60000000"),
        side_midpoint=Decimal("0.58000000" if wrong_midpoint else "0.59000000"),
        order_style="taker" if order.mode is ExecutionMode.LIVE else "maker",
        requested_limit_price=order.price,
        requested_shares=(order.size_usd / order.price).quantize(Decimal("0.000001")),
        requested_notional_usd=order.size_usd,
        expected_fee_usd=Decimal("0"),
        expected_fee_unavailable_reason=None,
        expected_slippage_per_share=Decimal("0"),
        expected_slippage_unavailable_reason=None,
        decision_at=signal.ts,
        intent_at=datetime(2000, 1, 1, tzinfo=UTC),
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        record_hash="0" * 64,
    )


def _paper_fill_leg(
    seed: dict[str, Any],
    intent: ExecutionOrderIntent,
    evidence: PersistedClobBatch,
    *,
    sequence: int = 1,
    external_leg_key: str = "race-leg-1",
) -> ExecutionFillLeg:
    snapshot = evidence.snapshots[0]
    return ExecutionFillLeg(
        intent_id=intent.id,
        order_id=seed["order"].id,
        signal_id=seed["signal"].id,
        market_id=seed["market_id"],
        reconciliation_observation_id=None,
        fill_clob_snapshot_id=snapshot.id,
        mode=ExecutionMode.PAPER,
        side=TradeSide.BUY_NO,
        token_id=seed["no_token_id"],
        source_kind="paper_simulation",
        fill_clob_snapshot_hash=snapshot.record_hash,
        external_trade_id=None,
        external_leg_key=external_leg_key,
        leg_sequence=sequence,
        liquidity_role="maker",
        shares=Decimal("10.000000"),
        price=Decimal("0.59000000"),
        notional_usd=Decimal("5.900000"),
        fee_usd=Decimal("0"),
        fee_semantics="paper_fee_model_v1",
        venue_filled_at=None,
        first_seen_at=datetime(2000, 1, 1, tzinfo=UTC),
        fill_effective_at=evidence.batch.first_seen_at,
        timestamp_semantics="paper_clob_first_seen",
        fill_best_bid=Decimal("0.58000000"),
        fill_best_ask=Decimal("0.60000000"),
        fill_midpoint=Decimal("0.59000000"),
        eligible=True,
        eligibility_reason="eligible",
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        source_hash=evidence.batch.record_hash,
        record_hash="0" * 64,
    )


def _record_values(row: Any, names: tuple[str, ...]) -> dict[str, object]:
    return {name: getattr(row, name) for name in names}


async def _wait_for_backend_lock(
    sessions: SessionFactory,
    backend_pid: int,
) -> None:
    for _ in range(200):
        async with sessions() as observer:
            waiting = await observer.scalar(
                text(
                    "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                    "WHERE pid = :pid"
                ),
                {"pid": backend_pid},
            )
        if waiting:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"backend {backend_pid} did not block on the intent lock")


async def test_runtime_is_frozen_and_migration_zero_backfills(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions() as session:
        runtime = await session.get(ExecutionCostRuntime, 1)
        assert runtime is not None
        assert runtime.definition_hash == EXECUTION_COST_DEFINITION_HASH
        assert runtime.mark_horizons_seconds == [300, 1800]
        for table_name in (
            "execution_order_intents",
            "execution_reconciliation_observations",
            "execution_fill_legs",
            "execution_cost_obligations",
            "execution_postfill_marks",
            "execution_cost_seals",
        ):
            assert await session.scalar(text(f"SELECT count(*) FROM {table_name}")) == 0
        with pytest.raises(DBAPIError, match="execution cost runtime is immutable"):
            await session.execute(
                text("UPDATE execution_cost_runtime SET mark_grace_seconds = 301 WHERE id = 1")
            )
        await session.rollback()


async def test_fee_backfill_mutates_only_proven_pre_ledger_paper_position(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        runtime = await session.get(ExecutionCostRuntime, 1)
        assert runtime is not None
        prospective_runtime = await session.get(ProspectiveValidationRuntime, 1)
        assert prospective_runtime is not None
        legacy_boundary = min(runtime.opened_at, prospective_runtime.opened_at)

        legacy = await _seed_decision(
            session,
            suffix="106",
            activate_release=False,
        )
        legacy_order: Order = legacy["order"]
        # Current dual-CLOB guards correctly prevent manufacturing historical
        # rows through the application. Build the pre-0038 fixture with the
        # same narrowly scoped trigger bypass used by migration tests.
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text(
                "UPDATE orders SET created_at = :created_at, status = 'filled', "
                "filled_usd = 5.9000, filled_at = :filled_at WHERE id = :order_id"
            ),
            {
                "created_at": legacy_boundary - timedelta(hours=2),
                "filled_at": legacy_boundary - timedelta(hours=1),
                "order_id": legacy_order.id,
            },
        )
        await session.execute(
            text(
                "UPDATE signals SET benchmark_protocol = 'legacy-synthetic-yes-v0' "
                "WHERE id = :signal_id"
            ),
            {"signal_id": legacy["signal"].id},
        )
        await session.execute(text("SET LOCAL session_replication_role = origin"))
        await session.refresh(legacy_order)
        await session.execute(
            text(
                "UPDATE markets SET fees_enabled = true, fee_rate = 0.02, "
                "fee_exponent = 1 WHERE id = :market_id"
            ),
            {"market_id": legacy["market_id"]},
        )
        legacy_position = Position(
            market_id=legacy["market_id"],
            mode=ExecutionMode.PAPER,
            side=TradeSide.BUY_NO,
            avg_price=Decimal("0.590000"),
            size_shares=Decimal("10.000000"),
            size_usd=Decimal("5.9000"),
            fee_usd=None,
            status=PositionStatus.OPEN,
            benchmark_protocol="legacy-synthetic-yes-v0",
            token_id=legacy["no_token_id"],
        )
        session.add(legacy_position)

        modern = await _seed_decision(session, suffix="107")
        await create_intent_for_order(
            session,
            modern["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        modern_position = Position(
            market_id=modern["market_id"],
            mode=ExecutionMode.PAPER,
            side=TradeSide.BUY_NO,
            avg_price=Decimal("0.590000"),
            size_shares=Decimal("10.000000"),
            size_usd=Decimal("5.9000"),
            fee_usd=None,
            status=PositionStatus.OPEN,
            benchmark_protocol="clob-dual-v1",
            token_id=modern["no_token_id"],
        )
        session.add(modern_position)

        # A post-boundary terminal fill without an intent is corrupt/incomplete
        # modern evidence, not a license to reconstruct legacy economics.
        ambiguous = await _seed_decision(session, suffix="108")
        ambiguous_order: Order = ambiguous["order"]
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text(
                "UPDATE orders SET status = 'filled', filled_usd = 5.9000, "
                "filled_at = :filled_at WHERE id = :order_id"
            ),
            {
                "filled_at": runtime.opened_at + timedelta(hours=1),
                "order_id": ambiguous_order.id,
            },
        )
        await session.execute(text("SET LOCAL session_replication_role = origin"))
        ambiguous_position = Position(
            market_id=ambiguous["market_id"],
            mode=ExecutionMode.PAPER,
            side=TradeSide.BUY_NO,
            avg_price=Decimal("0.590000"),
            size_shares=Decimal("10.000000"),
            size_usd=Decimal("5.9000"),
            fee_usd=None,
            status=PositionStatus.OPEN,
            benchmark_protocol="clob-dual-v1",
            token_id=ambiguous["no_token_id"],
        )
        session.add(ambiguous_position)
        await session.flush()
        legacy_position_id = legacy_position.id
        modern_position_id = modern_position.id
        ambiguous_position_id = ambiguous_position.id

    assert await backfill_fees(
        Settings(_env_file=None),
        session_factory=db_sessions,
    ) == (1, 1)

    async with db_sessions() as session:
        refreshed_legacy = await session.get(Position, legacy_position_id)
        refreshed_modern = await session.get(Position, modern_position_id)
        refreshed_ambiguous = await session.get(Position, ambiguous_position_id)
        refreshed_legacy_order = await session.get(Order, legacy_order.id)
        refreshed_modern_order = await session.get(Order, modern["order"].id)
        refreshed_ambiguous_order = await session.get(Order, ambiguous_order.id)

    assert refreshed_legacy is not None
    assert refreshed_legacy.fee_usd is not None
    assert refreshed_legacy.fee_usd > 0
    assert refreshed_legacy.size_shares < Decimal("10.000000")
    assert refreshed_legacy_order is not None
    assert refreshed_legacy_order.fee_usd is not None

    assert refreshed_modern is not None
    assert refreshed_modern.fee_usd is None
    assert refreshed_modern.size_shares == Decimal("10.000000")
    assert refreshed_modern.size_usd == Decimal("5.9000")
    assert refreshed_modern_order is not None
    assert refreshed_modern_order.fee_usd is None

    assert refreshed_ambiguous is not None
    assert refreshed_ambiguous.fee_usd is None
    assert refreshed_ambiguous.size_shares == Decimal("10.000000")
    assert refreshed_ambiguous_order is not None
    assert refreshed_ambiguous_order.fee_usd is None


async def test_postgres_hash_domain_matches_python_for_mark_records(
    db_sessions: SessionFactory,
) -> None:
    values: dict[str, object] = {
        "clob_snapshot_hash": None,
        "clob_snapshot_id": None,
        "definition_version": EXECUTION_COST_DEFINITION_VERSION,
        "fill_leg_id": 17,
        "horizon_seconds": 300,
        "missing_reason": "snapshot_missing_after_grace",
        "observed_at": None,
        "offset_seconds": None,
        "sealed_at": "2026-07-15T04:05:06+00:00",
        "side_best_ask": None,
        "side_best_bid": None,
        "side_midpoint": None,
        "status": "missing",
        "target_at": "2026-07-15T04:00:00+00:00",
    }
    payload = {
        "record_kind": "mark",
        "definition_hash": EXECUTION_COST_DEFINITION_HASH,
        **values,
    }
    async with db_sessions() as session:
        db_hash = await session.scalar(
            text("SELECT execution_cost_hash(CAST(:payload AS jsonb))"),
            {"payload": json.dumps(payload)},
        )
    assert db_hash == execution_record_hash("mark", values)


async def test_model_source_hash_matches_postgres_for_nested_exponent_floats(
    db_sessions: SessionFactory,
) -> None:
    components: dict[str, object] = {
        "pipeline": {"cohort": TEST_PIPELINE_COHORT},
        "diagnostics": {
            "small": 3.62e-05,
            "large": 1.25e20,
            "nested": [1e-7, {"very_large": 9.87e40}],
        },
    }
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="hsh", model_components=components)
        model: ModelProb = seed["model"]

        _, _, python_model_hash = _source_hashes(seed["order"], seed["signal"], model)
        postgres_model_hash = await session.scalar(
            text(
                """
                SELECT prospective_validation_hash(jsonb_build_object(
                    'id', source.id,
                    'market_id', source.market_id,
                    'ts', prospective_validation_iso_utc(source.ts),
                    'prob', trim_scale(source.prob)::text,
                    'sigma', trim_scale(source.sigma)::text,
                    'horizon_h', trim_scale(source.horizon_h)::text,
                    'components', source.components,
                    'market_snapshot_id', source.market_snapshot_id,
                    'market_anchor_protocol', source.market_anchor_protocol
                ))
                FROM model_probs AS source
                WHERE source.id = :model_id
                """
            ),
            {"model_id": model.id},
        )
        intent = _intent(seed)
        session.add(intent)
        await session.flush()

    assert python_model_hash is not None
    assert python_model_hash == postgres_model_hash
    assert intent.model_record_hash == postgres_model_hash


async def test_empty_execution_cost_ledger_downgrades_and_reapplies(
    db_sessions: SessionFactory,
) -> None:
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260715_0037")
    await asyncio.to_thread(command.upgrade, config, "head")
    async with db_sessions() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD
        )


async def test_create_intent_rejects_nonpositive_order_request_before_database_flush(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="zrq",
            paper_size_usd=Decimal("0.0000"),
        )
        with pytest.raises(
            ExecutionCostIntegrityError,
            match="requested notional must be finite and positive",
        ):
            await create_intent_for_order(
                session,
                seed["order"].id,
                release_image_digest=TEST_IMAGE_DIGEST,
                order_style="maker",
                expected_fee_usd=Decimal("0"),
                expected_slippage_per_share=Decimal("0"),
            )

    async with db_sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionOrderIntent)
                .where(ExecutionOrderIntent.order_id == seed["order"].id)
            )
            == 0
        )


async def test_0041_fill_projection_is_zero_backfill_reversible_and_append_only(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="fp1",
            paper_order_price=Decimal("0.590000"),
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        request_started_at = await session.scalar(select(func.clock_timestamp()))
        assert request_started_at is not None
        await asyncio.sleep(0.15)
        fill_batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=request_started_at,
        )
        await record_paper_fill(
            session,
            intent.id,
            fill_batch.snapshots[0].id,
            [
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10"),
                    price=Decimal("0.590000"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                )
            ],
        )
        await _detach_test_station_reliability_evidence(session, seed)

    async with db_sessions.begin() as session:
        enrollment = await session.scalar(select(ProspectiveValidationEnrollment))
        assert enrollment is not None
        enrollment_id = enrollment.id
        with pytest.raises(DBAPIError, match="prospective validation evidence is append-only"):
            async with session.begin_nested():
                await session.execute(
                    update(ProspectiveValidationEnrollment)
                    .where(ProspectiveValidationEnrollment.id == enrollment_id)
                    .values(fill_price=Decimal("0.580000"))
                )

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260715_0040")
    try:
        async with db_sessions() as session:
            assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
                "20260715_0040"
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(ProspectiveValidationEnrollment)
                )
                == 1
            )
            legacy_function = await session.scalar(
                text(
                    "SELECT pg_get_functiondef("
                    "'validate_prospective_validation_enrollment_insert()'::regprocedure)"
                )
            )
            assert legacy_function is not None
            assert "execution_fill_legs" not in legacy_function
            assert (
                await session.scalar(
                    text("SELECT to_regprocedure('execution_cost_reject_late_fill_leg_insert()')")
                )
                is None
            )
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")

    async with db_sessions() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD
        )
        assert (
            await session.scalar(select(func.count()).select_from(ProspectiveValidationEnrollment))
            == 1
        )
        projected_function = await session.scalar(
            text(
                "SELECT pg_get_functiondef("
                "'validate_prospective_validation_enrollment_insert()'::regprocedure)"
            )
        )
        assert projected_function is not None
        assert "execution_fill_legs" in projected_function
        assert "prospective execution intent has no fill legs" in projected_function
        assert (
            await session.scalar(
                text("SELECT to_regprocedure('execution_cost_reject_late_fill_leg_insert()')")
            )
            is not None
        )


async def test_0041_rejects_terminal_intent_without_legs_and_freezes_late_append(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="fp2")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        order: Order = seed["order"]
        signal: Signal = seed["signal"]
        order.status = "filled"
        order.filled_usd = Decimal("5.9000")
        order.fee_usd = Decimal("0")
        order.filled_at = now
        signal.status = SignalStatus.EXECUTED
        session.add(
            ExecutionCostObligation(
                order_id=order.id,
                intent_id=intent.id,
                terminal_status="filled",
                next_due_at=now,
                state="pending",
                finalized_at=None,
                failure_reason=None,
                definition_version=EXECUTION_COST_DEFINITION_VERSION,
                definition_hash=EXECUTION_COST_DEFINITION_HASH,
            )
        )
        await session.flush()

        with pytest.raises(
            ProspectiveValidationIntegrityError,
            match="no authoritative fill legs",
        ):
            await enroll_terminal_order(session, order.id)
        with pytest.raises(DBAPIError, match="execution intent has no fill legs"):
            async with session.begin_nested():
                await session.execute(
                    text(
                        """
                        INSERT INTO prospective_validation_enrollments (
                            order_id, signal_id, market_id, mode, market_date,
                            kind, side, fill_price, filled_usd, fee_usd,
                            size_shares, filled_at, enrolled_at,
                            benchmark_protocol, definition_version,
                            definition_hash, order_record_hash,
                            signal_record_hash, eligible, eligibility_reason,
                            record_hash
                        )
                        SELECT o.id, s.id, m.id, o.mode, m.market_date,
                               m.kind, o.side, o.price, o.filled_usd,
                               coalesce(o.fee_usd, 0), o.filled_usd / o.price,
                               o.filled_at, clock_timestamp(),
                               s.benchmark_protocol, 'prospective-validation-v1',
                               repeat('0', 64), repeat('0', 64),
                               repeat('0', 64), false, 'test', repeat('0', 64)
                        FROM orders o
                        JOIN signals s ON s.id = o.signal_id
                        JOIN markets m ON m.id = s.market_id
                        WHERE o.id = :order_id
                        """
                    ),
                    {"order_id": order.id},
                )

        with pytest.raises(DBAPIError, match="fill leg set is frozen"):
            async with session.begin_nested():
                await session.execute(
                    insert(ExecutionFillLeg).values(
                        intent_id=intent.id,
                        order_id=order.id,
                        signal_id=signal.id,
                        market_id=seed["market_id"],
                        mode=ExecutionMode.PAPER,
                        side=TradeSide.BUY_NO,
                        token_id=seed["no_token_id"],
                        source_kind="paper_simulation",
                        external_leg_key="late-leg",
                        leg_sequence=1,
                        liquidity_role="maker",
                        shares=Decimal("10"),
                        price=Decimal("0.59"),
                        notional_usd=Decimal("5.9"),
                        fee_usd=Decimal("0"),
                        fee_semantics="paper_fee_model_v1",
                        timestamp_semantics="paper_clob_first_seen",
                        eligible=True,
                        eligibility_reason="eligible",
                        definition_version=EXECUTION_COST_DEFINITION_VERSION,
                        definition_hash=EXECUTION_COST_DEFINITION_HASH,
                        source_hash="0" * 64,
                        record_hash="0" * 64,
                    )
                )
        # Deliberately invalid no-leg terminal state must never commit; the
        # deferred prospective-enrollment guard independently proves that too.
        await session.rollback()


async def test_0041_fill_leg_projection_lock_has_deterministic_race_outcomes(
    db_sessions: SessionFactory,
) -> None:
    async def seed_race(
        suffix: str,
    ) -> tuple[dict[str, Any], ExecutionOrderIntent, PersistedClobBatch]:
        async with db_sessions.begin() as session:
            seed = await _seed_decision(session, suffix=suffix)
            intent = await create_intent_for_order(
                session,
                seed["order"].id,
                release_image_digest=TEST_IMAGE_DIGEST,
                order_style="maker",
                expected_fee_usd=Decimal("0"),
                expected_slippage_per_share=Decimal("0"),
            )
            requested_at = await session.scalar(select(func.clock_timestamp()))
            assert requested_at is not None
            await asyncio.sleep(1.05)
            evidence = await _persist_snapshot(
                session,
                market_id=seed["market_id"],
                event_id=seed["event_id"],
                condition_id=seed["condition_id"],
                yes_token_id=seed["yes_token_id"],
                no_token_id=seed["no_token_id"],
                request_started_at=requested_at,
            )
            return seed, intent, evidence

    def obligation(
        seed: dict[str, Any],
        intent: ExecutionOrderIntent,
    ) -> ExecutionCostObligation:
        return ExecutionCostObligation(
            order_id=seed["order"].id,
            intent_id=intent.id,
            terminal_status="filled",
            next_due_at=datetime.now(UTC),
            state="pending",
            finalized_at=None,
            failure_reason=None,
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
        )

    async def mark_terminal(
        session: AsyncSession,
        seed: dict[str, Any],
    ) -> None:
        await session.execute(
            update(Order)
            .where(Order.id == seed["order"].id)
            .values(
                status="filled",
                filled_usd=Decimal("5.9000"),
                fee_usd=Decimal("0"),
                filled_at=func.clock_timestamp(),
            )
        )
        await session.execute(
            update(Signal)
            .where(Signal.id == seed["signal"].id)
            .values(status=SignalStatus.EXECUTED)
        )

    # Leg wins the shared intent lock: projection waits, then commits and
    # freezes the now-complete leg set. A later append is rejected.
    leg_seed, leg_intent, leg_evidence = await seed_race("rl1")
    leg_session = db_sessions()
    await leg_session.begin()
    leg_session.add(_paper_fill_leg(leg_seed, leg_intent, leg_evidence))
    await leg_session.flush()

    leg_projection_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def project_after_leg() -> None:
        async with db_sessions.begin() as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert pid is not None
            leg_projection_pid.set_result(int(pid))
            await mark_terminal(session, leg_seed)
            session.add(obligation(leg_seed, leg_intent))
            await session.flush()
            await enroll_terminal_order(session, leg_seed["order"].id)

    projection_task = asyncio.create_task(project_after_leg())
    await _wait_for_backend_lock(db_sessions, await leg_projection_pid)
    await leg_session.commit()
    await asyncio.wait_for(projection_task, timeout=5)
    await leg_session.close()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="fill leg set is frozen"):
            async with session.begin():
                session.add(
                    _paper_fill_leg(
                        leg_seed,
                        leg_intent,
                        leg_evidence,
                        sequence=2,
                        external_leg_key="race-leg-2",
                    )
                )
                await session.flush()

    # Projection wins the same lock: the concurrent leg waits, observes the
    # committed freeze, and fails rather than changing the projected VWAP.
    projection_seed, projection_intent, projection_evidence = await seed_race("rl2")
    projection_session = db_sessions()
    await projection_session.begin()
    await record_terminal_no_fill(
        projection_session,
        projection_intent.id,
        terminal_status="canceled",
        failure_reason="race_projection_wins",
    )

    fill_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def append_after_projection() -> None:
        async with db_sessions.begin() as session:
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert pid is not None
            fill_pid.set_result(int(pid))
            session.add(
                _paper_fill_leg(
                    projection_seed,
                    projection_intent,
                    projection_evidence,
                )
            )
            await session.flush()

    fill_task = asyncio.create_task(append_after_projection())
    await _wait_for_backend_lock(db_sessions, await fill_pid)
    await projection_session.commit()
    with pytest.raises(DBAPIError, match="fill leg set is frozen"):
        await asyncio.wait_for(fill_task, timeout=5)
    await projection_session.close()

    async with db_sessions() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionFillLeg)
                .where(ExecutionFillLeg.intent_id == projection_intent.id)
            )
            == 0
        )


async def test_0040_keeps_preexisting_intent_attestations_null(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="099",
            activate_release=False,
        )
        legacy = _intent(seed)
        await _detach_test_station_reliability_evidence(session, seed)

    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260715_0039")
    legacy_intent_id: int | None = None
    try:
        async with db_sessions.begin() as session:
            old_columns = (
                "order_id",
                "signal_id",
                "model_prob_id",
                "market_id",
                "decision_clob_snapshot_id",
                "mode",
                "side",
                "token_id",
                "pipeline_cohort",
                "signal_record_hash",
                "model_record_hash",
                "snapshot_record_hash",
                "yes_model_probability",
                "side_model_probability",
                "side_best_bid",
                "side_best_ask",
                "side_midpoint",
                "order_style",
                "requested_limit_price",
                "requested_shares",
                "requested_notional_usd",
                "expected_fee_usd",
                "expected_fee_unavailable_reason",
                "expected_slippage_per_share",
                "expected_slippage_unavailable_reason",
                "decision_at",
                "intent_at",
                "definition_version",
                "definition_hash",
                "record_hash",
            )
            result = await session.scalar(
                insert(ExecutionOrderIntent)
                .values(**_record_values(legacy, old_columns))
                .returning(ExecutionOrderIntent.id)
            )
            assert result is not None
            legacy_intent_id = result
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
    assert legacy_intent_id is not None
    async with db_sessions() as session:
        attestations = (
            await session.execute(
                text(
                    """
                    SELECT release_epoch_id, predictive_lane_id, selection_lane_id,
                           execution_lane_id, operational_lane_id, station_lane_id,
                           truth_lane_id
                    FROM execution_order_intents
                    WHERE id = :intent_id
                    """
                ),
                {"intent_id": legacy_intent_id},
            )
        ).one()
    assert tuple(attestations) == (None,) * 7


async def test_preactivation_probability_evidence_cannot_create_intent(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="100",
            activate_release=False,
        )
        plan = build_probability_pipeline_release_plan(
            pipeline_version=TEST_PIPELINE_VERSION,
            material_fingerprint=TEST_MATERIAL_FINGERPRINT,
            activation_manifest_hash=TEST_ACTIVATION_MANIFEST_HASH,
            image_digest=TEST_IMAGE_DIGEST,
            previous_epoch_id=None,
            release_manifest=TEST_RELEASE_MANIFEST,
            lane_plans=_test_lane_plans(action="fork"),
            reviewed_by="pytest-execution",
            approval_reason="Activate only evidence created after this reviewed boundary.",
        )
        await persist_probability_pipeline_release_plan(session, plan)
        await append_probability_pipeline_release_epoch(
            session,
            plan,
            expected_plan_hash=plan.plan_hash,
        )

        with pytest.raises(
            ExecutionCostIntegrityError,
            match="predates an active evidence-lane boundary",
        ):
            await create_intent_for_order(
                session,
                seed["order"].id,
                release_image_digest=TEST_IMAGE_DIGEST,
                order_style="maker",
                expected_fee_usd=Decimal("0"),
                expected_slippage_per_share=Decimal("0"),
            )

        await session.execute(
            text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
            {"digest": TEST_IMAGE_DIGEST},
        )
        with pytest.raises(DBAPIError, match="decision evidence predates release cohort"):
            async with session.begin_nested():
                session.add(_intent(seed))
                await session.flush()


async def test_0042_intent_requires_station_reliability_snapshot(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="sr-missing",
            attach_station_reliability=False,
        )

        with pytest.raises(
            DBAPIError,
            match="station reliability provenance is unavailable",
        ):
            async with session.begin_nested():
                await create_intent_for_order(
                    session,
                    seed["order"].id,
                    release_image_digest=TEST_IMAGE_DIGEST,
                    order_style="maker",
                    expected_fee_usd=Decimal("0"),
                    expected_slippage_per_share=Decimal("0"),
                )


async def test_0042_intent_rejects_station_evidence_before_active_lane_boundary(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="sr-boundary")
        await _activate_successor_release(
            session,
            image_digest=TEST_SUCCESSOR_IMAGE_DIGEST,
            lane_plans=_versioned_lane_plans(
                identities={
                    "selection_policy": "station-reliability-v2",
                    "station_lifecycle": "station-reliability-v2",
                },
                forked={"selection_policy", "station_lifecycle"},
            ),
        )

        with pytest.raises(
            ExecutionCostIntegrityError,
            match="predates an active evidence-lane boundary",
        ):
            async with session.begin_nested():
                await create_intent_for_order(
                    session,
                    seed["order"].id,
                    release_image_digest=TEST_SUCCESSOR_IMAGE_DIGEST,
                    order_style="maker",
                    expected_fee_usd=Decimal("0"),
                    expected_slippage_per_share=Decimal("0"),
                )

        await session.execute(
            text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
            {"digest": TEST_SUCCESSOR_IMAGE_DIGEST},
        )
        with pytest.raises(
            DBAPIError,
            match="station decision evidence predates active lanes",
        ):
            async with session.begin_nested():
                session.add(_intent(seed))
                await session.flush()


async def test_same_cohort_image_successor_keeps_first_evidence_boundary(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="100")
        first = await current_probability_pipeline_release_epoch(session)
        assert first is not None
        successor_plan = build_probability_pipeline_release_plan(
            pipeline_version=TEST_PIPELINE_VERSION,
            material_fingerprint=TEST_MATERIAL_FINGERPRINT,
            activation_manifest_hash=TEST_ACTIVATION_MANIFEST_HASH,
            image_digest=TEST_SUCCESSOR_IMAGE_DIGEST,
            previous_epoch_id=first.id,
            release_manifest=TEST_RELEASE_MANIFEST,
            lane_plans=_test_lane_plans(action="continue"),
            reviewed_by="pytest-execution",
            approval_reason="Approve the rebuilt image without resetting evidence time.",
        )
        await persist_probability_pipeline_release_plan(session, successor_plan)
        successor = await append_probability_pipeline_release_epoch(
            session,
            successor_plan,
            expected_plan_hash=successor_plan.plan_hash,
        )
        assert successor.cohort == first.cohort
        assert seed["signal"].ts < successor.opened_at

        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_SUCCESSOR_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        assert intent.pipeline_cohort == TEST_PIPELINE_COHORT
        assert intent.release_epoch_id == successor.id
        lane_ids = {
            lane_name: lane_id
            for lane_name, lane_id in (
                await session.execute(
                    text(
                        "SELECT lane_name, id FROM evidence_continuity_lanes "
                        "WHERE epoch_id = :epoch_id"
                    ),
                    {"epoch_id": successor.id},
                )
            ).all()
        }
        assert intent.predictive_lane_id == lane_ids["predictive_validation"]
        assert intent.selection_lane_id == lane_ids["selection_policy"]
        assert intent.execution_lane_id == lane_ids["execution_evidence"]
        assert intent.operational_lane_id == lane_ids["operational_legacy"]
        assert intent.station_lane_id == lane_ids["station_lifecycle"]
        assert intent.truth_lane_id == lane_ids["official_resolution_truth"]


async def test_execution_intent_guard_waits_for_release_advisory_lock(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="100")

    locker = db_sessions()
    writer: asyncio.Task[None] | None = None
    try:
        await locker.begin()
        await locker.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('stratus:probability-pipeline-release-epoch-v1', 0))"
            )
        )

        async def insert_intent() -> None:
            async with db_sessions.begin() as session:
                await session.execute(
                    text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
                    {"digest": TEST_IMAGE_DIGEST},
                )
                session.add(_intent(seed))
                await session.flush()

        writer = asyncio.create_task(insert_intent())
        waiter_observed = False
        for _ in range(40):
            async with db_sessions() as observer:
                waiter_observed = bool(
                    await observer.scalar(
                        text(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM pg_locks
                                WHERE locktype = 'advisory'
                                  AND database = (
                                      SELECT oid FROM pg_database
                                      WHERE datname = current_database()
                                  )
                                  AND NOT granted
                            )
                            """
                        )
                    )
                )
            if waiter_observed:
                break
            await asyncio.sleep(0.05)
        assert waiter_observed
        assert not writer.done()

        await locker.commit()
        await asyncio.wait_for(writer, timeout=5)
    finally:
        if locker.in_transaction():
            await locker.rollback()
        await locker.close()
        if writer is not None and not writer.done():
            writer.cancel()
            await asyncio.gather(writer, return_exceptions=True)


async def test_promotion_commit_before_intent_rechecks_manifest_and_blocks(
    db_sessions: SessionFactory,
) -> None:
    """A promotion committed first must make an old-cohort intent fail closed."""

    settings = Settings(_env_file=None, release_image_digest=TEST_IMAGE_DIGEST)
    fingerprint = probability_pipeline_fingerprint(settings)
    assert fingerprint == TEST_MATERIAL_FINGERPRINT
    base_cohort = probability_pipeline_base_cohort(
        settings,
        fingerprint=fingerprint,
    )
    expert = "qrf"
    legacy_version = "promotion-race-v1"
    expert_version = sealed_expert_version(legacy_version)
    experiment_fingerprint = sealed_experiment_fingerprint(
        pipeline_fingerprint=fingerprint,
        expert=expert,
        legacy_version=legacy_version,
        sealed_version=expert_version,
        pipeline_version=base_cohort,
    )

    async with db_sessions.begin() as session:
        await session.execute(
            text("TRUNCATE TABLE accuracy_promotion_trials RESTART IDENTITY CASCADE")
        )
        seed = await _seed_decision(session, suffix="105")

    promotion_session = db_sessions()
    intent_task: asyncio.Task[str] | None = None
    try:
        intent_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        start_gate = asyncio.Event()

        async def attempt_intent() -> str:
            async with db_sessions.begin() as session:
                backend_pid = await session.scalar(text("SELECT pg_backend_pid()"))
                assert backend_pid is not None
                intent_pid.set_result(int(backend_pid))
                await start_gate.wait()
                gate = await probability_release_gate(session, settings)
                if not gate.allowed:
                    return gate.reason
                await create_intent_for_order(
                    session,
                    seed["order"].id,
                    release_image_digest=TEST_IMAGE_DIGEST,
                    order_style="maker",
                    expected_fee_usd=Decimal("0"),
                    expected_slippage_per_share=Decimal("0"),
                )
                return "intent_created"

        # Establish the contender connection before the promotion transaction
        # takes the protocol locks. The event below controls the semantic race;
        # connection-pool scheduling must not decide whether the test reaches it.
        intent_task = asyncio.create_task(attempt_intent())
        backend_pid = await asyncio.wait_for(intent_pid, timeout=2)

        await promotion_session.begin()
        # The public protocol path takes locks in promotion -> release order.
        # With no accuracy-prediction rows it records nothing, but retains both
        # transaction-scoped locks while this test commits one synthetic,
        # source-compatible material activation below.
        promotion_run = await run_accuracy_promotion_protocol(
            promotion_session,
            evaluated_at=datetime.now(UTC),
            pipeline_fingerprint=fingerprint,
        )
        assert promotion_run.recorded_activations == 0
        await promotion_session.execute(text("SET LOCAL session_replication_role = replica"))
        await promotion_session.execute(
            text(
                """
                INSERT INTO accuracy_promotion_activations (
                    activation_id, trial_id, look_id, definition_hash,
                    decision_hash, family_id, experiment_id, candidate_id,
                    baseline_id, pipeline_cohort, expert,
                    legacy_expert_version, expert_version,
                    experiment_fingerprint, station_id, horizon_bucket, kind,
                    activated_at, activated_by, record_hash
                ) VALUES (
                    'promotion-race-activation', 900001, 900001, :definition_hash,
                    :decision_hash, :family_id, 'promotion-race-experiment',
                    'promotion-race-candidate', 'production-baseline',
                    :pipeline_cohort, :expert, :legacy_version, :expert_version,
                    :experiment_fingerprint, 900001, '24-48h',
                    'high'::weather_kind, clock_timestamp(),
                    'integration-race-test', :record_hash
                )
                """
            ),
            {
                "definition_hash": "c" * 64,
                "decision_hash": "d" * 64,
                "family_id": PROMOTION_REGISTRATION_EPOCH,
                "pipeline_cohort": base_cohort,
                "expert": expert,
                "legacy_version": legacy_version,
                "expert_version": expert_version,
                "experiment_fingerprint": experiment_fingerprint,
                "record_hash": "e" * 64,
            },
        )

        start_gate.set()
        waiter_observed = False
        for _ in range(40):
            async with db_sessions() as observer:
                waiter_observed = bool(
                    await observer.scalar(
                        text(
                            """
                            SELECT wait_event_type = 'Lock' AND wait_event = 'advisory'
                            FROM pg_stat_activity
                            WHERE pid = :pid
                            """
                        ),
                        {"pid": backend_pid},
                    )
                )
            if waiter_observed:
                break
            await asyncio.sleep(0.05)
        assert waiter_observed
        assert not intent_task.done()

        # Enforce the dangerous order: the material promotion is visible
        # before the contender can finish its old-manifest intent transaction.
        await promotion_session.commit()
        result = await asyncio.wait_for(intent_task, timeout=5)
        assert result == "evidence_lane_identity_mismatch"
        async with db_sessions() as observer:
            assert (
                await observer.scalar(select(func.count()).select_from(ExecutionOrderIntent)) == 0
            )
    finally:
        if promotion_session.in_transaction():
            await promotion_session.rollback()
        await promotion_session.close()
        if intent_task is not None and not intent_task.done():
            intent_task.cancel()
            await asyncio.gather(intent_task, return_exceptions=True)
        async with db_sessions.begin() as cleanup:
            await cleanup.execute(
                text("TRUNCATE TABLE accuracy_promotion_trials RESTART IDENTITY CASCADE")
            )


async def test_intent_uses_independent_no_book_db_clock_and_db_hash(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="101")
        with pytest.raises(DBAPIError, match="decision projection mismatch"):
            async with session.begin_nested():
                session.add(_intent(seed, wrong_midpoint=True))
                await session.flush()
        intent = _intent(seed)
        session.add(intent)
        await session.flush()
        await session.refresh(intent)
        runtime = await session.get(ExecutionCostRuntime, 1)
        assert runtime is not None
        assert intent.intent_at >= runtime.opened_at
        assert intent.intent_at >= seed["order"].created_at
        assert intent.side_best_bid == Decimal("0.58000000")
        assert intent.side_best_ask == Decimal("0.60000000")
        assert intent.side_midpoint == Decimal("0.59000000")
        assert intent.side_model_probability == Decimal("0.60000000")
        assert intent.expected_slippage_per_share == Decimal("0E-10")
        names = (
            "decision_at",
            "decision_clob_snapshot_id",
            "definition_version",
            "expected_fee_unavailable_reason",
            "expected_fee_usd",
            "expected_slippage_per_share",
            "expected_slippage_unavailable_reason",
            "intent_at",
            "market_id",
            "mode",
            "model_prob_id",
            "model_record_hash",
            "order_id",
            "order_style",
            "pipeline_cohort",
            "requested_limit_price",
            "requested_notional_usd",
            "requested_shares",
            "side",
            "side_best_ask",
            "side_best_bid",
            "side_midpoint",
            "side_model_probability",
            "signal_id",
            "signal_record_hash",
            "snapshot_record_hash",
            "token_id",
            "yes_model_probability",
        )
        assert intent.record_hash == execution_record_hash("intent", _record_values(intent, names))
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                update(ExecutionOrderIntent)
                .where(ExecutionOrderIntent.id == intent.id)
                .values(order_style="taker")
            )


async def test_fill_sequence_constraint_and_append_only(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="102")
        intent = _intent(seed)
        session.add(intent)
        await session.flush()
        await session.refresh(intent)
        pre_intent_request_batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=intent.intent_at - timedelta(milliseconds=1),
        )
        fill_batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
        )

        def leg(
            sequence: int,
            *,
            evidence: PersistedClobBatch = fill_batch,
        ) -> ExecutionFillLeg:
            evidence_snapshot = evidence.snapshots[0]
            return ExecutionFillLeg(
                intent_id=intent.id,
                order_id=seed["order"].id,
                signal_id=seed["signal"].id,
                market_id=seed["market_id"],
                reconciliation_observation_id=None,
                fill_clob_snapshot_id=evidence_snapshot.id,
                mode=ExecutionMode.PAPER,
                side=TradeSide.BUY_NO,
                token_id=seed["no_token_id"],
                source_kind="paper_simulation",
                fill_clob_snapshot_hash=evidence_snapshot.record_hash,
                external_trade_id=None,
                external_leg_key="paper-level-1",
                leg_sequence=sequence,
                liquidity_role="maker",
                shares=Decimal("10.000000"),
                price=Decimal("0.59000000"),
                notional_usd=Decimal("5.900000"),
                fee_usd=Decimal("0"),
                fee_semantics="paper_fee_model_v1",
                venue_filled_at=None,
                first_seen_at=datetime(2000, 1, 1, tzinfo=UTC),
                fill_effective_at=evidence.batch.first_seen_at,
                timestamp_semantics="paper_clob_first_seen",
                fill_best_bid=Decimal("0.58000000"),
                fill_best_ask=Decimal("0.60000000"),
                fill_midpoint=Decimal("0.59000000"),
                eligible=True,
                eligibility_reason="eligible",
                definition_version=EXECUTION_COST_DEFINITION_VERSION,
                definition_hash=EXECUTION_COST_DEFINITION_HASH,
                source_hash=evidence.batch.record_hash,
                record_hash="0" * 64,
            )

        with pytest.raises(DBAPIError, match="paper execution fill evidence mismatch"):
            async with session.begin_nested():
                session.add(leg(1, evidence=pre_intent_request_batch))
                await session.flush()
        with pytest.raises(DBAPIError, match="ck_execution_fill_legs_sequence"):
            async with session.begin_nested():
                session.add(leg(0))
                await session.flush()
        stored = leg(1)
        session.add(stored)
        await session.flush()
        await session.refresh(stored)
        assert stored.first_seen_at >= fill_batch.batch.first_seen_at
        leg_names = (
            "definition_version",
            "eligibility_reason",
            "eligible",
            "external_leg_key",
            "external_trade_id",
            "fee_semantics",
            "fee_usd",
            "fill_best_ask",
            "fill_best_bid",
            "fill_clob_snapshot_hash",
            "fill_clob_snapshot_id",
            "fill_effective_at",
            "fill_midpoint",
            "first_seen_at",
            "intent_id",
            "leg_sequence",
            "liquidity_role",
            "market_id",
            "mode",
            "notional_usd",
            "order_id",
            "price",
            "reconciliation_observation_id",
            "shares",
            "side",
            "signal_id",
            "source_hash",
            "source_kind",
            "timestamp_semantics",
            "token_id",
            "venue_filled_at",
        )
        assert stored.record_hash == execution_record_hash(
            "fill_leg", _record_values(stored, leg_names)
        )
        health_summary = await execution_cost_health_summary(
            session,
            now=datetime.now(UTC),
        )
        assert health_summary.invalid_eligible_source_legs == 0
        with pytest.raises(DBAPIError, match="append-only"):
            await session.execute(
                update(ExecutionFillLeg)
                .where(ExecutionFillLeg.id == stored.id)
                .values(price=Decimal("0.58"))
            )


async def test_terminal_intent_order_requires_obligation_in_same_transaction(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="terminal 0038 order lacks"):
            async with session.begin():
                seed = await _seed_decision(session, suffix="105")
                session.add(_intent(seed))
                await session.flush()
                await session.execute(
                    update(Order)
                    .where(Order.id == seed["order"].id)
                    .values(status="canceled", filled_usd=Decimal("0"))
                )
        await session.rollback()


async def test_unverified_live_fill_seals_without_impossible_markouts_and_checks_position(
    db_sessions: SessionFactory,
) -> None:
    # Roll this focused schema transaction back: revision 0032 has a separate
    # deferred prospective-outcome enrollment invariant for committed fills.
    async with db_sessions() as session:
        seed = await _seed_decision(session, suffix="103", mode=ExecutionMode.LIVE)
        intent = _intent(seed)
        session.add(intent)
        await session.flush()
        request_at = await session.scalar(select(func.clock_timestamp()))
        assert request_at is not None
        payload = {"status": "matched", "matched_shares": "10"}
        observation = ExecutionReconciliationObservation(
            order_id=seed["order"].id,
            observation_kind="trades",
            sdk_method="get_trades",
            parser_version="live-sdk-parser-v0-unverified",
            request_started_at=request_at,
            response_received_at=request_at,
            normalized_payload=payload,
            payload_sha256=source_payload_hash(payload),
            first_seen_at=datetime(2000, 1, 1, tzinfo=UTC),
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
            record_hash="0" * 64,
        )
        session.add(observation)
        await session.flush()
        await session.refresh(observation)
        observation_names = (
            "definition_version",
            "first_seen_at",
            "payload_sha256",
            "observation_kind",
            "order_id",
            "parser_version",
            "request_started_at",
            "response_received_at",
            "sdk_method",
        )
        assert observation.record_hash == execution_record_hash(
            "observation", _record_values(observation, observation_names)
        )
        leg = ExecutionFillLeg(
            intent_id=intent.id,
            order_id=seed["order"].id,
            signal_id=seed["signal"].id,
            market_id=seed["market_id"],
            reconciliation_observation_id=observation.id,
            fill_clob_snapshot_id=None,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_NO,
            token_id=seed["no_token_id"],
            source_kind="live_sdk",
            fill_clob_snapshot_hash=None,
            external_trade_id=None,
            external_leg_key="unverified-cumulative-fill",
            leg_sequence=1,
            liquidity_role="unknown",
            shares=Decimal("10.000000"),
            price=Decimal("0.60000000"),
            notional_usd=Decimal("6.000000"),
            fee_usd=None,
            fee_semantics="unknown",
            venue_filled_at=None,
            first_seen_at=datetime(2000, 1, 1, tzinfo=UTC),
            fill_effective_at=None,
            timestamp_semantics="unverified",
            fill_best_bid=None,
            fill_best_ask=None,
            fill_midpoint=None,
            eligible=False,
            eligibility_reason="live_schema_unverified",
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
            source_hash=observation.record_hash,
            record_hash="0" * 64,
        )
        session.add(leg)
        await session.flush()
        health_summary = await execution_cost_health_summary(
            session,
            now=datetime.now(UTC),
        )
        assert health_summary.live_unverified_legs == 1
        assert health_summary.live_unverified_reasons == (("live_schema_unverified", 1),)
        await session.execute(
            update(Order)
            .where(Order.id == seed["order"].id)
            .values(
                status="filled",
                filled_usd=Decimal("6.0000"),
                filled_at=func.clock_timestamp(),
            )
        )
        position = Position(
            market_id=seed["market_id"],
            order_id=seed["order"].id,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_NO,
            avg_price=Decimal("0.600000"),
            size_shares=Decimal("9.000000"),
            size_usd=Decimal("6.0000"),
            fee_usd=None,
            status=PositionStatus.OPEN,
            benchmark_protocol="clob-dual-v1",
            token_id=seed["no_token_id"],
        )
        session.add(position)
        await session.flush()
        obligation = ExecutionCostObligation(
            order_id=seed["order"].id,
            intent_id=intent.id,
            terminal_status="filled",
            created_at=datetime(2000, 1, 1, tzinfo=UTC),
            next_due_at=request_at,
            state="pending",
            finalized_at=None,
            failure_reason=None,
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
        )
        session.add(obligation)
        await session.flush()

        def seal() -> ExecutionCostSeal:
            return ExecutionCostSeal(
                obligation_id=obligation.id,
                intent_id=intent.id,
                order_id=seed["order"].id,
                eligible=False,
                eligibility_reason="live_schema_unverified",
                reason_detail={"evidence": "normalized SDK only"},
                aggregate_role="unknown",
                leg_count=1,
                total_shares=Decimal("10.000000"),
                filled_notional_usd=Decimal("6.000000"),
                vwap_fill_price=Decimal("0.60000000"),
                total_fee_usd=None,
                fee_per_share=None,
                intent_midpoint=Decimal("0.59000000"),
                side_model_probability=Decimal("0.60000000"),
                weighted_fill_midpoint=None,
                gross_model_edge=None,
                latency_movement=None,
                taker_spread=None,
                taker_slippage=None,
                maker_improvement=None,
                fee_drag=None,
                realized_net_edge=None,
                expected_slippage_per_share=Decimal("0"),
                expected_slippage_coverage="modeled",
                adverse_selection_300s=None,
                markout_300s_coverage="not_applicable",
                adverse_selection_1800s=None,
                markout_1800s_coverage="not_applicable",
                definition_version=EXECUTION_COST_DEFINITION_VERSION,
                definition_hash=EXECUTION_COST_DEFINITION_HASH,
                sealed_at=datetime(2000, 1, 1, tzinfo=UTC),
                record_hash="0" * 64,
            )

        with pytest.raises(DBAPIError, match="live position totals"):
            async with session.begin_nested():
                session.add(seal())
                await session.flush()
        position.size_shares = Decimal("10.000000")
        await session.flush()
        stored_seal = seal()
        session.add(stored_seal)
        await session.flush()
        await session.refresh(stored_seal)
        assert stored_seal.markout_300s_coverage == "not_applicable"
        assert stored_seal.markout_1800s_coverage == "not_applicable"
        seal_names = (
            "adverse_selection_1800s",
            "adverse_selection_300s",
            "aggregate_role",
            "definition_version",
            "eligibility_reason",
            "eligible",
            "expected_slippage_coverage",
            "expected_slippage_per_share",
            "fee_drag",
            "fee_per_share",
            "filled_notional_usd",
            "gross_model_edge",
            "intent_id",
            "intent_midpoint",
            "latency_movement",
            "leg_count",
            "maker_improvement",
            "markout_1800s_coverage",
            "markout_300s_coverage",
            "obligation_id",
            "order_id",
            "realized_net_edge",
            "reason_detail",
            "sealed_at",
            "side_model_probability",
            "taker_slippage",
            "taker_spread",
            "total_fee_usd",
            "total_shares",
            "vwap_fill_price",
            "weighted_fill_midpoint",
        )
        assert stored_seal.record_hash == execution_record_hash(
            "seal", _record_values(stored_seal, seal_names)
        )
        obligation.state = "sealed"
        await session.flush()
        await session.refresh(obligation)
        assert obligation.finalized_at is not None
        await session.rollback()


async def test_downgrade_refuses_once_execution_evidence_exists(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="104")
        session.add(_intent(seed))
    # Test-owner-only cleanup: expose the older 0038 downgrade guard after
    # proving the newer 0040 chain elsewhere. Production code has no truncate
    # path and cannot delete/relabel an activated epoch.
    async with db_sessions.begin() as session:
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await _detach_test_station_reliability_evidence(session, seed)
        await session.execute(
            text(
                "UPDATE execution_order_intents SET "
                "release_epoch_id = NULL, predictive_lane_id = NULL, "
                "selection_lane_id = NULL, execution_lane_id = NULL, "
                "operational_lane_id = NULL, station_lane_id = NULL, "
                "truth_lane_id = NULL"
            )
        )
        await session.execute(text("DELETE FROM evidence_continuity_lanes"))
        await session.execute(text("DELETE FROM probability_pipeline_release_epochs"))
        await session.execute(text("DELETE FROM probability_pipeline_release_plans"))
    config = Config("alembic.ini")
    try:
        with pytest.raises(DBAPIError, match="refusing 0038 downgrade"):
            await asyncio.to_thread(command.downgrade, config, "20260715_0037")
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")


async def test_repository_terminal_no_fill_is_atomic_and_idempotent(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="106")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        seal = await record_terminal_no_fill(
            session,
            intent.id,
            terminal_status="unfilled_maker",
            failure_reason="paper_order_ttl",
        )
        repeated = await record_terminal_no_fill(
            session,
            intent.id,
            terminal_status="unfilled_maker",
            failure_reason="paper_order_ttl",
        )
        assert repeated.id == seal.id
        assert seal.eligibility_reason == "no_fill"
        assert seal.markout_300s_coverage == "not_applicable"
        order = await session.get(Order, seed["order"].id)
        signal = await session.get(Signal, seed["signal"].id)
        obligation = await session.scalar(
            select(ExecutionCostObligation).where(ExecutionCostObligation.intent_id == intent.id)
        )
        assert order is not None and order.status == "unfilled_maker"
        assert signal is not None and signal.status is SignalStatus.EXPIRED
        assert obligation is not None and obligation.state == "sealed"


async def test_repository_paper_fill_updates_legacy_and_ledger_atomically(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="107")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        request_started_at = await session.scalar(select(func.clock_timestamp()))
        assert request_started_at is not None
        await asyncio.sleep(0.15)
        fill_batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=request_started_at,
        )
        result = await record_paper_fill(
            session,
            intent.id,
            fill_batch.snapshots[0].id,
            [
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10.001153"),
                    price=Decimal("0.589932"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                )
            ],
        )
        repeated = await record_paper_fill(
            session,
            intent.id,
            fill_batch.snapshots[0].id,
            [
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10.001153"),
                    price=Decimal("0.589932"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                )
            ],
        )
        assert repeated.obligation_id == result.obligation_id
        assert result.terminal_status == "filled"
        assert result.filled_notional_usd == Decimal("5.900000")
        order = await session.get(Order, seed["order"].id)
        signal = await session.get(Signal, seed["signal"].id)
        fill_leg = await session.scalar(
            select(ExecutionFillLeg).where(ExecutionFillLeg.intent_id == intent.id)
        )
        enrollment = await session.scalar(
            select(ProspectiveValidationEnrollment).where(
                ProspectiveValidationEnrollment.order_id == seed["order"].id
            )
        )
        position = await session.scalar(
            select(Position).where(
                Position.market_id == seed["market_id"],
                Position.mode == ExecutionMode.PAPER,
            )
        )
        assert order is not None and order.status == "filled"
        assert order.price == Decimal("0.590000")
        assert order.filled_usd == Decimal("5.900000")
        assert signal is not None and signal.status is SignalStatus.EXECUTED
        assert fill_leg is not None and fill_leg.price == Decimal("0.58993200")
        assert enrollment is not None
        assert enrollment.fill_price == Decimal("0.589932")
        assert enrollment.filled_usd == Decimal("5.9000")
        assert enrollment.size_shares == Decimal("10.001153")
        assert position is not None and position.size_shares == Decimal("10.001153")
        assert position.avg_price == Decimal("0.589932")
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionFillLeg)
                .where(ExecutionFillLeg.intent_id == intent.id)
            )
            == 1
        )


async def test_paper_fill_return_enrollment_and_aggregate_use_exact_stored_leg_vwap(
    db_sessions: SessionFactory,
) -> None:
    """Paper projections must not derive VWAP from six-decimal leg notionals.

    This reproduces the production 0.73 rounding class: the stored notional
    is 5.748064, whose quotient by 7.874060 shares is 0.7300000254.  The DB
    trigger authoritatively recomputes the price from stored shares * price,
    which is exactly 0.73 and has a 5e-9 comparison tolerance.
    """

    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="111",
            paper_order_price=Decimal("0.730000"),
            paper_size_usd=Decimal("5.7481"),
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        request_started_at = await session.scalar(select(func.clock_timestamp()))
        assert request_started_at is not None
        await asyncio.sleep(0.15)
        fill_batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=request_started_at,
        )
        leg = PaperFillLegSpec(
            sequence=1,
            shares=Decimal("7.874060"),
            price=Decimal("0.73000000"),
            fee_usd=Decimal("0"),
            liquidity_role="maker",
        )
        result = await record_paper_fill(
            session,
            intent.id,
            fill_batch.snapshots[0].id,
            [leg],
        )
        repeated = await record_paper_fill(
            session,
            intent.id,
            fill_batch.snapshots[0].id,
            [leg],
        )

        assert result.filled_notional_usd == Decimal("5.748064")
        assert result.vwap_fill_price == Decimal("0.73000000")
        assert repeated.vwap_fill_price == Decimal("0.73000000")
        fill_aggregate = execution_fill_aggregate_subquery()
        aggregate = (
            await session.execute(
                select(
                    fill_aggregate.c.total_shares,
                    fill_aggregate.c.filled_notional_usd,
                    fill_aggregate.c.total_fee_usd,
                    fill_aggregate.c.vwap_fill_price,
                ).where(fill_aggregate.c.order_id == seed["order"].id)
            )
        ).one()
        assert aggregate.total_shares == Decimal("7.874060")
        assert aggregate.filled_notional_usd == Decimal("5.748064")
        assert aggregate.total_fee_usd == Decimal("0E-8")
        assert aggregate.vwap_fill_price == Decimal("0.73000000")
        order = await session.get(Order, seed["order"].id)
        enrollment = await session.scalar(
            select(ProspectiveValidationEnrollment).where(
                ProspectiveValidationEnrollment.order_id == seed["order"].id
            )
        )
        assert order is not None and order.price == Decimal("0.730000")
        assert enrollment is not None
        assert enrollment.fill_price == Decimal("0.730000")
        assert enrollment.filled_usd == Decimal("5.7481")
        assert enrollment.size_shares == Decimal("7.874060")


async def test_validation_dataset_follows_predictive_and_execution_boundaries(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:

        async def fill(*, suffix: str, image_digest: str) -> None:
            seed = await _seed_decision(
                session,
                suffix=suffix,
                release_image_digest=image_digest,
            )
            intent = await create_intent_for_order(
                session,
                seed["order"].id,
                release_image_digest=image_digest,
                order_style="maker",
                expected_fee_usd=Decimal("0"),
                expected_slippage_per_share=Decimal("0"),
            )
            requested_at = await session.scalar(select(func.clock_timestamp()))
            assert requested_at is not None
            await asyncio.sleep(0.15)
            batch = await _persist_snapshot(
                session,
                market_id=seed["market_id"],
                event_id=seed["event_id"],
                condition_id=seed["condition_id"],
                yes_token_id=seed["yes_token_id"],
                no_token_id=seed["no_token_id"],
                request_started_at=requested_at,
            )
            await record_paper_fill(
                session,
                intent.id,
                batch.snapshots[0].id,
                [
                    PaperFillLegSpec(
                        sequence=1,
                        shares=Decimal("10"),
                        price=Decimal("0.59"),
                        fee_usd=Decimal("0"),
                        liquidity_role="maker",
                    )
                ],
            )

        await fill(suffix="ln1", image_digest=TEST_IMAGE_DIGEST)
        initial = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert initial.enrolled_orders == 1
        assert initial.formal_order_ids == ()
        assert initial.predictive_lineage is not None
        assert initial.execution_lineage is not None
        assert initial.effective_opened_at == max(
            initial.opened_at,
            initial.predictive_lineage.boundary_at,
            initial.execution_lineage.boundary_at,
        )

        continued_digest = "d" * 64
        await _activate_successor_release(
            session,
            image_digest=continued_digest,
            lane_plans=_versioned_lane_plans(identities={}, forked=set()),
        )
        continued = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert continued.enrolled_orders == 1
        assert continued.formal_order_ids == ()
        assert continued.predictive_lineage is not None
        assert continued.execution_lineage is not None
        assert continued.predictive_lineage.lane_id != initial.predictive_lineage.lane_id
        assert (
            continued.predictive_lineage.identity_hash
            == initial.predictive_lineage.identity_hash
        )
        assert (
            continued.predictive_lineage.boundary_at
            == initial.predictive_lineage.boundary_at
        )
        assert continued.execution_lineage.lane_id != initial.execution_lineage.lane_id
        assert continued.execution_lineage.boundary_at == initial.execution_lineage.boundary_at

        predictive_digest = "e" * 64
        await _activate_successor_release(
            session,
            image_digest=predictive_digest,
            lane_plans=_versioned_lane_plans(
                identities={"predictive_validation": "v2"},
                forked={"predictive_validation"},
            ),
        )
        predictive_reset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert predictive_reset.enrolled_orders == 0
        assert predictive_reset.predictive_lineage is not None
        assert (
            predictive_reset.predictive_lineage.identity_hash
            != continued.predictive_lineage.identity_hash
        )
        assert predictive_reset.effective_opened_at >= continued.effective_opened_at

        await fill(suffix="ln2", image_digest=predictive_digest)
        post_predictive = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert post_predictive.enrolled_orders == 1
        assert post_predictive.execution_lineage is not None
        assert post_predictive.predictive_lineage == predictive_reset.predictive_lineage

        await _activate_successor_release(
            session,
            image_digest="f" * 64,
            lane_plans=_versioned_lane_plans(
                identities={
                    "predictive_validation": "v2",
                    "execution_evidence": "v2",
                },
                forked={"execution_evidence"},
            ),
        )
        execution_reset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert execution_reset.enrolled_orders == 0
        assert execution_reset.execution_lineage is not None
        assert (
            execution_reset.execution_lineage.identity_hash
            != post_predictive.execution_lineage.identity_hash
        )
        assert execution_reset.effective_opened_at >= post_predictive.effective_opened_at


async def test_live_gate_counts_only_current_predictive_and_execution_lanes(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:

        async def fill_and_resolve(*, suffix: str, image_digest: str) -> int:
            return await _fill_and_seal_modern_trade(
                session,
                suffix=suffix,
                image_digest=image_digest,
            )

        first_order_id = await fill_and_resolve(
            suffix="g01",
            image_digest=TEST_IMAGE_DIGEST,
        )
        initial = await calculate_live_gate(session, initial_bankroll_usd=1_080.0)
        assert initial.resolved_positions == 1
        assert initial.pipeline_resolved_trades == 1
        assert initial.clv_samples == 1
        assert initial.independent_samples == 1

        await _append_unactivated_model_probability(session)
        after_stray_model = await calculate_live_gate(
            session,
            initial_bankroll_usd=1_080.0,
        )
        assert after_stray_model.pipeline_version == TEST_PIPELINE_COHORT
        assert after_stray_model.resolved_positions == 1

        continued_digest = "d" * 64
        await _activate_successor_release(
            session,
            image_digest=continued_digest,
            lane_plans=_versioned_lane_plans(identities={}, forked=set()),
        )
        continued = await calculate_live_gate(session, initial_bankroll_usd=1_080.0)
        assert continued.resolved_positions == 1
        continued_dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert continued_dataset.formal_order_ids == (first_order_id,)

        predictive_digest = "e" * 64
        await _activate_successor_release(
            session,
            image_digest=predictive_digest,
            lane_plans=_versioned_lane_plans(
                identities={"predictive_validation": "gate-v2"},
                forked={"predictive_validation"},
            ),
        )
        predictive_reset = await calculate_live_gate(
            session,
            initial_bankroll_usd=1_080.0,
        )
        assert predictive_reset.resolved_positions == 0
        assert predictive_reset.pipeline_resolved_trades == 0
        assert predictive_reset.clv_samples == 0
        assert predictive_reset.independent_samples == 0

        second_order_id = await fill_and_resolve(
            suffix="g02",
            image_digest=predictive_digest,
        )
        post_predictive = await calculate_live_gate(
            session,
            initial_bankroll_usd=1_080.0,
        )
        assert post_predictive.resolved_positions == 1
        assert post_predictive.pipeline_resolved_trades == 1
        post_predictive_dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert post_predictive_dataset.formal_order_ids == (second_order_id,)

        await _activate_successor_release(
            session,
            image_digest="f" * 64,
            lane_plans=_versioned_lane_plans(
                identities={
                    "predictive_validation": "gate-v2",
                    "execution_evidence": "gate-v2",
                },
                forked={"execution_evidence"},
            ),
        )
        execution_reset = await calculate_live_gate(
            session,
            initial_bankroll_usd=1_080.0,
        )
        assert execution_reset.resolved_positions == 0
        assert execution_reset.pipeline_resolved_trades == 0
        assert execution_reset.clv_samples == 0
        assert execution_reset.independent_samples == 0


async def test_prospective_operator_reports_reset_with_predictive_lane_fork(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        await _fill_and_seal_modern_trade(
            session,
            suffix="api",
            image_digest=TEST_IMAGE_DIGEST,
        )

    app = create_app(Settings(_env_file=None, database_url=migrated_database_url))
    app.state.sessions = db_sessions

    async def assert_formal_counts(expected: int) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            performance = (await client.get("/api/v1/performance-summary?mode=paper")).json()
            edge = (await client.get("/api/v1/edge-report?mode=paper")).json()
            brier = (await client.get("/api/v1/brier-report?mode=paper")).json()
            clv = (await client.get("/api/v1/clv-report?mode=paper")).json()
            actions = (await client.get("/api/v1/root-cause-actions?mode=paper")).json()
            brier_rows = (
                await client.get(
                    "/api/v1/root-cause-drilldown?mode=paper&metric=brier"
                    "&dimension=side&value=buy_no"
                )
            ).json()
            clv_rows = (
                await client.get(
                    "/api/v1/root-cause-drilldown?mode=paper&metric=clv"
                    "&dimension=price_band&value=0.50-0.75"
                )
            ).json()
            ledger = (await client.get("/api/v1/validation-ledger?mode=paper")).json()

        assert performance["benchmark_positions"] == expected
        assert performance["n_resolved_trades"] == expected
        assert edge["benchmark_positions"] == expected
        assert edge["n_resolved_trades"] == expected
        assert edge["clv_samples"] == expected
        assert brier["n_resolved_trades"] == expected
        assert clv["n_closed_positions"] == expected
        assert brier_rows["count"] == expected
        assert clv_rows["count"] == expected
        assert ledger["closed_orders"] == expected
        assert ledger["effective_sample_opened_at"] is not None
        if expected:
            assert actions["items"]
            assert ledger["predictive_lane_id"] is not None
            assert ledger["execution_lane_id"] is not None
        else:
            assert actions["items"] == []

    await assert_formal_counts(1)

    async with db_sessions.begin() as session:
        await _append_unactivated_model_probability(session)

    await assert_formal_counts(1)

    async with db_sessions.begin() as session:
        await _activate_successor_release(
            session,
            image_digest="d" * 64,
            lane_plans=_versioned_lane_plans(
                identities={"predictive_validation": "operator-api-v2"},
                forked={"predictive_validation"},
            ),
        )

    await assert_formal_counts(0)


async def test_selection_policy_sample_continues_and_resets_on_its_own_lane_fork(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="sl1")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        requested_at = await session.scalar(select(func.clock_timestamp()))
        assert requested_at is not None
        await asyncio.sleep(0.15)
        batch = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=requested_at,
        )
        await record_paper_fill(
            session,
            intent.id,
            batch.snapshots[0].id,
            [
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10"),
                    price=Decimal("0.59"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                )
            ],
        )
        decision_at = await session.scalar(select(func.clock_timestamp()))
        assert decision_at is not None
        assert await capture_selection_policy_decisions(
            session,
            now=decision_at,
        ) == 6
        await _seal_seed_gamma_outcome(session, seed, yes_wins=False)
        formal_dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        formal_order_ids = formal_dataset.formal_order_ids
        assert formal_order_ids == (seed["order"].id,)

        initial_decisions = await valid_selection_policy_decisions(
            session,
            formal_order_ids,
            pipeline_version=TEST_PIPELINE_COHORT,
        )
        initial_counts = await selection_policy_ledger_counts(
            session,
            formal_order_ids,
            mode=ExecutionMode.PAPER,
            pipeline_version=TEST_PIPELINE_COHORT,
            benchmark_protocol="clob-dual-v1",
        )
        assert initial_decisions[seed["order"].id]["baseline"] is True
        assert initial_counts.recorded == 1
        assert initial_counts.resolved == 1
        formal_trades = prospective_trade_outcomes(formal_dataset.trades)
        initial_candidates = evaluate_recorded_selection_policies(
            formal_trades,
            initial_decisions,
        )
        assert initial_candidates[0].opportunities == 1

        continued_digest = "d" * 64
        await _activate_successor_release(
            session,
            image_digest=continued_digest,
            lane_plans=_versioned_lane_plans(identities={}, forked=set()),
        )
        continued_decisions = await valid_selection_policy_decisions(
            session,
            formal_order_ids,
            pipeline_version=TEST_PIPELINE_COHORT,
        )
        continued_counts = await selection_policy_ledger_counts(
            session,
            formal_order_ids,
            mode=ExecutionMode.PAPER,
            pipeline_version=TEST_PIPELINE_COHORT,
            benchmark_protocol="clob-dual-v1",
        )
        assert continued_decisions == initial_decisions
        assert continued_counts == initial_counts

        await _activate_successor_release(
            session,
            image_digest="e" * 64,
            lane_plans=_versioned_lane_plans(
                identities={"selection_policy": "v2"},
                forked={"selection_policy"},
            ),
        )
        forked_decisions = await valid_selection_policy_decisions(
            session,
            formal_order_ids,
            pipeline_version=TEST_PIPELINE_COHORT,
        )
        assert forked_decisions == {}
        post_fork_dataset = await load_prospective_validation_dataset(
            session,
            mode=ExecutionMode.PAPER,
            pipeline_cohort=TEST_PIPELINE_COHORT,
        )
        assert post_fork_dataset.formal_order_ids == formal_order_ids
        forked_candidates = evaluate_recorded_selection_policies(
            prospective_trade_outcomes(post_fork_dataset.trades),
            forked_decisions,
        )
        assert all(candidate.opportunities == 0 for candidate in forked_candidates)
        assert await selection_policy_ledger_counts(
            session,
            formal_order_ids,
            mode=ExecutionMode.PAPER,
            pipeline_version=TEST_PIPELINE_COHORT,
            benchmark_protocol="clob-dual-v1",
        ) == SelectionPolicyLedgerCounts(recorded=0, resolved=0)


async def _live_observation(
    session: AsyncSession,
    *,
    order_id: int,
    payload: dict[str, object],
) -> ExecutionReconciliationObservation:
    request_at = await session.scalar(select(func.clock_timestamp()))
    assert request_at is not None
    return await append_live_reconciliation_observation(
        session,
        order_id,
        observation_kind="order",
        sdk_method="get_order",
        parser_version="live-order-normalizer-v1",
        request_started_at=request_at,
        response_received_at=request_at,
        normalized_payload=payload,
    )


async def test_unverified_live_finalizer_uses_exact_stored_leg_vwap(
    db_sessions: SessionFactory,
) -> None:
    """Immediate unverified-live finalization preserves the atomic-leg VWAP."""

    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="112",
            mode=ExecutionMode.LIVE,
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="taker",
        )
        observation = await _live_observation(
            session,
            order_id=seed["order"].id,
            payload={
                "status": "canceled",
                "size_matched": "7.874060",
                "price": "0.73000000",
            },
        )
        seal = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="canceled",
            matched_shares=Decimal("7.874060"),
            avg_price=Decimal("0.73000000"),
            cost_usd=Decimal("5.748064"),
        )

        leg = await session.scalar(
            select(ExecutionFillLeg).where(ExecutionFillLeg.intent_id == intent.id)
        )
        order = await session.get(Order, seed["order"].id)
        assert leg is not None
        assert leg.notional_usd == Decimal("5.748064")
        assert leg.price == Decimal("0.73000000")
        assert order is not None and order.price == Decimal("0.600000")
        assert seal.filled_notional_usd == Decimal("5.748064")
        assert seal.vwap_fill_price == Decimal("0.73000000")
        assert seal.markout_300s_coverage == "not_applicable"
        assert seal.markout_1800s_coverage == "not_applicable"


async def test_live_observation_dedupes_and_zero_fill_seals(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="108",
            mode=ExecutionMode.LIVE,
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="taker",
        )
        payload = {"status": "canceled", "size_matched": "0", "price": "0.60"}
        observation = await _live_observation(
            session,
            order_id=seed["order"].id,
            payload=payload,
        )
        repeated_observation = await append_live_reconciliation_observation(
            session,
            seed["order"].id,
            observation_kind="order",
            sdk_method="get_order",
            parser_version="live-order-normalizer-v1",
            request_started_at=observation.request_started_at,
            response_received_at=observation.response_received_at,
            normalized_payload=payload,
        )
        assert repeated_observation.id == observation.id
        seal = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="canceled",
            matched_shares=Decimal("0"),
            failure_reason="venue_terminal_no_fill",
        )
        repeated_seal = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="canceled",
            matched_shares=Decimal("0"),
            failure_reason="venue_terminal_no_fill",
        )
        assert repeated_seal.id == seal.id
        assert seal.eligibility_reason == "no_fill"
        assert seal.markout_300s_coverage == "not_applicable"
        assert not await session.scalar(
            select(func.count())
            .select_from(ExecutionFillLeg)
            .where(ExecutionFillLeg.intent_id == intent.id)
        )
        order = await session.get(Order, seed["order"].id)
        assert order is not None and order.status == "canceled"


async def test_unverified_live_full_fill_is_explicitly_ineligible_and_idempotent(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="109",
            mode=ExecutionMode.LIVE,
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="taker",
        )
        observation = await _live_observation(
            session,
            order_id=seed["order"].id,
            payload={"status": "matched", "size_matched": "10", "price": "0.60"},
        )
        seal = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="filled",
            matched_shares=Decimal("10"),
            avg_price=Decimal("0.599930"),
            cost_usd=Decimal("5.999300"),
        )
        repeated = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="filled",
            matched_shares=Decimal("10"),
            avg_price=Decimal("0.599930"),
            cost_usd=Decimal("5.999300"),
        )
        assert repeated.id == seal.id
        assert seal.eligible is False
        assert seal.eligibility_reason == "live_schema_unverified"
        assert seal.aggregate_role == "unknown"
        assert seal.markout_300s_coverage == "not_applicable"
        assert seal.markout_1800s_coverage == "not_applicable"
        leg = await session.scalar(
            select(ExecutionFillLeg).where(ExecutionFillLeg.intent_id == intent.id)
        )
        assert leg is not None
        assert leg.reconciliation_observation_id == observation.id
        assert leg.fill_effective_at is None
        assert leg.venue_filled_at is None
        assert leg.fill_clob_snapshot_id is None
        assert leg.liquidity_role == "unknown"
        assert leg.fee_usd is None
        assert leg.price == Decimal("0.59993000")
        order = await session.get(Order, seed["order"].id)
        enrollment = await session.scalar(
            select(ProspectiveValidationEnrollment).where(
                ProspectiveValidationEnrollment.order_id == seed["order"].id
            )
        )
        position = await session.scalar(
            select(Position).where(Position.order_id == seed["order"].id)
        )
        assert order is not None
        assert order.price == Decimal("0.600000")
        assert enrollment is not None
        assert enrollment.fill_price == Decimal("0.599930")
        assert enrollment.filled_usd == Decimal("5.9993")
        assert enrollment.fee_usd == Decimal("0.00000")
        assert enrollment.size_shares == Decimal("10.000000")
        assert enrollment.eligible is False
        assert enrollment.eligibility_reason == "execution_evidence_ineligible"
        assert position is not None
        assert position.avg_price == Decimal("0.599930")
        assert position.size_shares == Decimal("10.000000")
        assert position.size_usd == Decimal("5.9993")


async def test_unverified_live_partial_terminal_preserves_known_fee(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(
            session,
            suffix="110",
            mode=ExecutionMode.LIVE,
        )
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="taker",
        )
        session.add(
            Position(
                market_id=seed["market_id"],
                order_id=seed["order"].id,
                mode=ExecutionMode.LIVE,
                side=TradeSide.BUY_NO,
                avg_price=Decimal("0.600000"),
                size_shares=Decimal("5.000000"),
                size_usd=Decimal("3.0000"),
                fee_usd=Decimal("0.01234"),
                status=PositionStatus.OPEN,
                benchmark_protocol="clob-dual-v1",
                token_id=seed["no_token_id"],
            )
        )
        await session.flush()
        observation = await _live_observation(
            session,
            order_id=seed["order"].id,
            payload={"status": "canceled", "size_matched": "5", "price": "0.60"},
        )
        seal = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="canceled",
            matched_shares=Decimal("5"),
            avg_price=Decimal("0.60"),
            cost_usd=Decimal("3.0000"),
            fee_usd=None,
        )
        repeated = await record_terminal_unverified_live_execution(
            session,
            intent.id,
            observation.id,
            terminal_status="canceled",
            matched_shares=Decimal("5"),
            avg_price=Decimal("0.60"),
            cost_usd=Decimal("3.0000"),
            fee_usd=None,
        )
        assert repeated.id == seal.id
        assert seal.eligible is False
        assert seal.total_fee_usd == Decimal("0.01234000")
        leg = await session.scalar(
            select(ExecutionFillLeg).where(ExecutionFillLeg.intent_id == intent.id)
        )
        order = await session.get(Order, seed["order"].id)
        obligation = await session.scalar(
            select(ExecutionCostObligation).where(ExecutionCostObligation.intent_id == intent.id)
        )
        assert leg is not None and leg.fee_semantics == "venue_reported"
        assert leg.fee_usd == Decimal("0.01234000")
        assert order is not None and order.status == "partially_filled"
        assert order.fee_usd == Decimal("0.01234")
        assert obligation is not None and obligation.state == "sealed"


async def test_execution_cost_maintenance_skips_lock_then_finalizes_once(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="maintenance-no-fill")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        order: Order = seed["order"]
        signal: Signal = seed["signal"]
        order.status = "canceled"
        order.filled_usd = Decimal(0)
        order.fee_usd = None
        order.filled_at = None
        signal.status = SignalStatus.EXPIRED
        signal.skip_reason = "maintenance_test"
        await session.flush()
        now = await session.scalar(select(func.clock_timestamp()))
        assert now is not None
        obligation = ExecutionCostObligation(
            order_id=order.id,
            intent_id=intent.id,
            terminal_status="canceled",
            next_due_at=now,
            state="pending",
            finalized_at=None,
            failure_reason="maintenance_test",
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
        )
        session.add(obligation)
        await session.flush()
        obligation_id = obligation.id

    locker = db_sessions()
    await locker.begin()
    await locker.execute(
        select(ExecutionCostObligation)
        .where(ExecutionCostObligation.id == obligation_id)
        .with_for_update()
    )
    locked_run = await asyncio.wait_for(
        maintain_execution_cost_evidence(_TEST_SETTINGS, db_sessions),
        timeout=2,
    )
    assert locked_run.markouts.obligations_claimed == 0
    assert locked_run.finalization.obligations_claimed == 0
    await locker.commit()
    await locker.close()

    first = await maintain_execution_cost_evidence(_TEST_SETTINGS, db_sessions)
    repeated = await maintain_execution_cost_evidence(_TEST_SETTINGS, db_sessions)

    assert first.markouts.obligations_claimed == 0
    assert first.finalization.obligations_claimed == 1
    assert first.finalization.obligations_sealed == 1
    assert repeated.markouts.obligations_claimed == 0
    assert repeated.finalization.obligations_claimed == 0
    async with db_sessions() as session:
        stored = await session.get(ExecutionCostObligation, obligation_id)
        seal_count = await session.scalar(
            select(func.count())
            .select_from(ExecutionCostSeal)
            .where(ExecutionCostSeal.obligation_id == obligation_id)
        )
        assert stored is not None
        assert stored.state == "sealed"
        assert stored.next_due_at is None
        assert stored.finalized_at is not None
        assert seal_count == 1


async def test_execution_cost_maintenance_never_finalizes_before_marks(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        seed = await _seed_decision(session, suffix="maintenance-mark-pending")
        intent = await create_intent_for_order(
            session,
            seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        requested_at = await session.scalar(select(func.clock_timestamp()))
        assert requested_at is not None
        await asyncio.sleep(1.05)
        evidence = await _persist_snapshot(
            session,
            market_id=seed["market_id"],
            event_id=seed["event_id"],
            condition_id=seed["condition_id"],
            yes_token_id=seed["yes_token_id"],
            no_token_id=seed["no_token_id"],
            request_started_at=requested_at,
        )
        fill = await record_paper_fill(
            session,
            intent.id,
            evidence.snapshots[0].id,
            (
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10"),
                    price=Decimal("0.59"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                ),
            ),
        )
        obligation = await session.get(ExecutionCostObligation, fill.obligation_id)
        assert obligation is not None
        obligation.next_due_at = await session.scalar(select(func.clock_timestamp()))
        obligation_id = obligation.id

    run = await maintain_execution_cost_evidence(_TEST_SETTINGS, db_sessions)

    assert run.markouts.obligations_claimed == 1
    assert run.markouts.obligations_deferred == 1
    assert run.finalization.obligations_claimed == 0
    async with db_sessions() as session:
        stored = await session.get(ExecutionCostObligation, obligation_id)
        seal_count = await session.scalar(
            select(func.count())
            .select_from(ExecutionCostSeal)
            .where(ExecutionCostSeal.obligation_id == obligation_id)
        )
        assert stored is not None
        assert stored.state == "waiting_for_marks"
        assert stored.next_due_at is not None
        assert stored.next_due_at > run.observed_at
        assert seal_count == 0


async def test_incident_reconciliation_verifies_hashes_and_immutable_triggers(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        missing_seed = await _seed_decision(session, suffix="reconcile-missing")
        missing_intent = await create_intent_for_order(
            session,
            missing_seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        missing_batch = await _persist_snapshot(
            session,
            market_id=missing_seed["market_id"],
            event_id=missing_seed["event_id"],
            condition_id=missing_seed["condition_id"],
            yes_token_id=missing_seed["yes_token_id"],
            no_token_id=missing_seed["no_token_id"],
        )
        missing_fill = await record_paper_fill(
            session,
            missing_intent.id,
            missing_batch.snapshots[0].id,
            (
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10"),
                    price=Decimal("0.59"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                ),
            ),
        )
        missing_leg = await session.scalar(
            select(ExecutionFillLeg).where(
                ExecutionFillLeg.intent_id == missing_intent.id
            )
        )
        missing_obligation = await session.get(
            ExecutionCostObligation,
            missing_fill.obligation_id,
        )
        assert missing_leg is not None
        assert missing_obligation is not None

        observed_seed = await _seed_decision(session, suffix="reconcile-observed")
        observed_intent = await create_intent_for_order(
            session,
            observed_seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        observed_fill_batch = await _persist_snapshot(
            session,
            market_id=observed_seed["market_id"],
            event_id=observed_seed["event_id"],
            condition_id=observed_seed["condition_id"],
            yes_token_id=observed_seed["yes_token_id"],
            no_token_id=observed_seed["no_token_id"],
        )
        observed_fill = await record_paper_fill(
            session,
            observed_intent.id,
            observed_fill_batch.snapshots[0].id,
            (
                PaperFillLegSpec(
                    sequence=1,
                    shares=Decimal("10"),
                    price=Decimal("0.59"),
                    fee_usd=Decimal("0"),
                    liquidity_role="maker",
                ),
            ),
        )
        observed_mark_batch = await _persist_snapshot(
            session,
            market_id=observed_seed["market_id"],
            event_id=observed_seed["event_id"],
            condition_id=observed_seed["condition_id"],
            yes_token_id=observed_seed["yes_token_id"],
            no_token_id=observed_seed["no_token_id"],
        )
        observed_leg = await session.scalar(
            select(ExecutionFillLeg).where(
                ExecutionFillLeg.intent_id == observed_intent.id
            )
        )
        observed_obligation = await session.get(
            ExecutionCostObligation,
            observed_fill.obligation_id,
        )
        assert observed_leg is not None
        assert observed_obligation is not None

        no_fill_seed = await _seed_decision(session, suffix="reconcile-no-fill")
        no_fill_intent = await create_intent_for_order(
            session,
            no_fill_seed["order"].id,
            release_image_digest=TEST_IMAGE_DIGEST,
            order_style="maker",
            expected_fee_usd=Decimal("0"),
            expected_slippage_per_share=Decimal("0"),
        )
        no_fill_seal = await record_terminal_no_fill(
            session,
            no_fill_intent.id,
            terminal_status="unfilled_maker",
            failure_reason="reconciliation_hash_fixture",
        )

        database_now = await session.scalar(select(func.clock_timestamp()))
        assert database_now is not None
        missing_effective_at = database_now - timedelta(minutes=40)
        observed_target_at = observed_mark_batch.batch.first_seen_at
        observed_effective_at = observed_target_at - timedelta(seconds=300)
        await session.execute(text("SET LOCAL session_replication_role = replica"))
        await session.execute(
            text(
                "UPDATE execution_fill_legs SET fill_effective_at = :effective_at "
                "WHERE id = :leg_id"
            ),
            {
                "effective_at": missing_effective_at,
                "leg_id": missing_leg.id,
            },
        )
        await session.execute(
            text(
                "UPDATE execution_cost_obligations "
                "SET state = 'pending', next_due_at = :next_due_at, finalized_at = NULL "
                "WHERE id = :obligation_id"
            ),
            {
                "next_due_at": missing_effective_at + timedelta(seconds=300),
                "obligation_id": missing_obligation.id,
            },
        )
        await session.execute(
            text(
                "UPDATE execution_fill_legs SET fill_effective_at = :effective_at "
                "WHERE id = :leg_id"
            ),
            {
                "effective_at": observed_effective_at,
                "leg_id": observed_leg.id,
            },
        )
        await session.execute(
            text(
                "UPDATE execution_cost_obligations "
                "SET state = 'pending', next_due_at = :next_due_at, finalized_at = NULL "
                "WHERE id = :obligation_id"
            ),
            {
                "next_due_at": observed_target_at,
                "obligation_id": observed_obligation.id,
            },
        )
        await session.execute(text("SET LOCAL session_replication_role = origin"))
        no_fill_seal_id = no_fill_seal.id

    maintenance = await maintain_execution_cost_evidence(_TEST_SETTINGS, db_sessions)
    assert maintenance.markouts.obligations_claimed == 2
    assert maintenance.markouts.marks_observed == 1
    assert maintenance.markouts.marks_missing == 2
    assert maintenance.finalization.obligations_claimed == 1
    assert maintenance.finalization.obligations_sealed == 1

    hash_statement = _incident_reconciliation_statement(
        "execution-cost-record-hash-reconciliation"
    )
    trigger_statement = _incident_reconciliation_statement(
        "execution-cost-trigger-integrity"
    )
    function_statement = _incident_reconciliation_statement(
        "execution-cost-hash-function-integrity"
    )

    async with db_sessions.begin() as session:
        hash_rows = {
            row["record_kind"]: row
            for row in (
                await session.execute(text(hash_statement))
            ).mappings()
        }
        assert hash_rows["mark"]["rows_checked"] == 3
        assert hash_rows["seal"]["rows_checked"] == 2
        for row in hash_rows.values():
            assert row["invalid_record_hashes"] == 0
            assert row["hash_function_mismatches"] == 0
            assert row["all_hashes_valid"] is True
            assert row["mismatches"] == []
            assert row["scopes"]
            assert {scope["mode"] for scope in row["scopes"]} == {"paper"}

        trigger_rows = (
            await session.execute(text(trigger_statement))
        ).mappings().all()
        assert len(trigger_rows) == 4
        for row in trigger_rows:
            assert row["present"] is True
            assert row["enabled"] is True
            assert row["function_matches"] is True
            assert row["event_matches"] is True
            assert row["observed_definition"]

        function_row = (
            await session.execute(text(function_statement))
        ).mappings().one()
        assert function_row["present"] is True
        assert function_row["language"] == "sql"
        assert function_row["immutable"] is True
        assert function_row["strict"] is True
        assert function_row["invoker_security"] is True
        assert function_row["known_vector_matches_inline"] is True

        mark_ids = tuple(
            await session.scalars(
                select(ExecutionPostfillMark.id).order_by(ExecutionPostfillMark.id)
            )
        )
        seal_ids = tuple(
            await session.scalars(
                select(ExecutionCostSeal.id).order_by(ExecutionCostSeal.id)
            )
        )
        assert len(mark_ids) == 3
        assert len(seal_ids) == 2
        assert no_fill_seal_id in seal_ids

        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    update(ExecutionPostfillMark)
                    .where(ExecutionPostfillMark.id == mark_ids[0])
                    .values(record_hash="e" * 64)
                )
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(
                    update(ExecutionCostSeal)
                    .where(ExecutionCostSeal.id == seal_ids[0])
                    .values(record_hash="e" * 64)
                )

    corrupt_session = db_sessions()
    await corrupt_session.begin()
    try:
        await corrupt_session.execute(
            text("SET LOCAL session_replication_role = replica")
        )
        await corrupt_session.execute(
            update(ExecutionPostfillMark)
            .where(ExecutionPostfillMark.id == mark_ids[0])
            .values(record_hash="f" * 64)
        )
        await corrupt_session.execute(
            update(ExecutionCostSeal)
            .where(ExecutionCostSeal.id == seal_ids[0])
            .values(record_hash="f" * 64)
        )
        await corrupt_session.execute(
            text("SET LOCAL session_replication_role = origin")
        )
        corrupt_rows = {
            row["record_kind"]: row
            for row in (
                await corrupt_session.execute(text(hash_statement))
            ).mappings()
        }
        for record_kind, record_id in (
            ("mark", mark_ids[0]),
            ("seal", seal_ids[0]),
        ):
            row = corrupt_rows[record_kind]
            assert row["invalid_record_hashes"] == 1
            assert row["hash_function_mismatches"] == 0
            assert row["all_hashes_valid"] is False
            assert len(row["mismatches"]) == 1
            assert row["mismatches"][0]["id"] == record_id
            assert row["mismatches"][0]["stored_hash"] == "f" * 64
            assert (
                row["mismatches"][0]["expected_hash"]
                == row["mismatches"][0]["function_hash"]
            )
    finally:
        await corrupt_session.rollback()
        await corrupt_session.close()

    async with db_sessions() as session:
        restored_rows = (
            await session.execute(text(hash_statement))
        ).mappings().all()
        assert all(row["invalid_record_hashes"] == 0 for row in restored_rows)
        assert all(row["all_hashes_valid"] is True for row in restored_rows)
