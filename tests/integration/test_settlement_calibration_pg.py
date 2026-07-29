from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.collectors.gamma import (
    CompleteGammaResolution,
    GammaEventSnapshot,
    ResolvedMarket,
)
from stratus.config import Settings
from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ModelProb,
    SettlementProbabilityCalibrator,
    Station,
    WeatherKind,
)
from stratus.jobs.fit_settlement_calibration import fit_settlement_calibration
from stratus.repositories.clob_snapshots import persist_clob_batch
from stratus.repositories.gamma_outcomes import (
    build_gamma_outcome_event_value,
    upsert_gamma_outcome_event,
)
from stratus.repositories.market_prices import PriceTarget
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)
PIPELINE_COHORT = "calibration-dual-test-v1"


def _raw_book(
    target: PriceTarget,
    *,
    yes: bool,
    midpoint: Decimal,
    source_timestamp: datetime,
) -> dict[str, Any]:
    bid = midpoint - Decimal("0.01")
    ask = midpoint + Decimal("0.01")
    return {
        "market": target.condition_id,
        "asset_id": target.yes_token_id if yes else target.no_token_id,
        "timestamp": str(int(source_timestamp.timestamp() * 1000)),
        "hash": f"calibration-{target.market_id}-{'yes' if yes else 'no'}",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [{"price": str(bid), "size": "100"}],
        "asks": [{"price": str(ask), "size": "100"}],
    }


def _dual_batch(targets: list[PriceTarget]) -> ClobBooksBatch:
    response_received_at = datetime.now(UTC) - timedelta(milliseconds=10)
    source_timestamp = response_received_at - timedelta(milliseconds=10)
    yes_midpoints = (Decimal("0.20"), Decimal("0.30"), Decimal("0.50"))
    raw_books = [
        _raw_book(
            target,
            yes=yes,
            midpoint=(midpoint if yes else Decimal(1) - midpoint),
            source_timestamp=source_timestamp,
        )
        for target, midpoint in zip(targets, yes_midpoints, strict=True)
        for yes in (True, False)
    ]
    raw = json.dumps(raw_books, separators=(",", ":")).encode("utf-8")
    requested = tuple(sorted(str(book["asset_id"]) for book in raw_books))
    raw_by_token = {str(book["asset_id"]): book for book in raw_books}
    return ClobBooksBatch(
        source_url="https://clob.test/books",
        requested_token_ids=requested,
        request_started_at=response_received_at - timedelta(milliseconds=10),
        response_received_at=response_received_at,
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(),
        snapshots=tuple(
            parse_strict_book_snapshot(raw_by_token[token]) for token in requested
        ),
    )


async def _seal_gamma_event(
    session: AsyncSession,
    *,
    event_id: str,
    markets: list[Market],
    winner_index: int,
    resolved_at: datetime,
) -> GammaOutcomeEvent:
    outcomes = tuple(
        ResolvedMarket(
            polymarket_id=market.polymarket_id,
            outcome_yes=index == winner_index,
            closed_at=resolved_at,
            yes_price="1" if index == winner_index else "0",
            no_price="0" if index == winner_index else "1",
            condition_id=market.condition_id,
            question_id=f"question-{market.polymarket_id}",
            yes_token_id=str(market.yes_token_id),
            no_token_id=str(market.no_token_id),
            uma_resolution_status="resolved",
            resolved_by="integration-fixture",
        )
        for index, market in enumerate(markets)
    )
    payload = {
        "id": event_id,
        "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": resolved_at.isoformat().replace("+00:00", "Z"),
        "markets": [
            {
                "id": market.polymarket_id,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "closedTime": resolved_at.isoformat().replace("+00:00", "Z"),
                "outcomes": '["Yes", "No"]',
                "outcomePrices": (
                    '["1", "0"]' if index == winner_index else '["0", "1"]'
                ),
                "clobTokenIds": json.dumps(
                    [market.yes_token_id, market.no_token_id]
                ),
                "conditionId": market.condition_id,
                "questionID": f"question-{market.polymarket_id}",
                "resolvedBy": "integration-fixture",
            }
            for index, market in enumerate(markets)
        ],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    snapshot = GammaEventSnapshot(
        event_id=event_id,
        payload=payload,
        source_url=f"https://gamma.test/events/{event_id}",
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        fetched_at=datetime.now(UTC),
        http_date=resolved_at + timedelta(minutes=1),
        last_modified=resolved_at,
        etag=f'"{event_id}"',
    )
    resolution = CompleteGammaResolution(
        event_id=event_id,
        outcomes=outcomes,
        first_outcome_at=resolved_at,
        fully_resolved_at=resolved_at,
        event_closed_at=resolved_at,
        event_updated_at=resolved_at,
    )
    return await upsert_gamma_outcome_event(
        session,
        build_gamma_outcome_event_value(snapshot, resolution, markets),
    )


async def test_settlement_calibrator_is_oof_gated_and_reported(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    event_evidence: list[
        tuple[
            int,
            list[tuple[Market, ClobMarketSnapshot, Decimal, bool]],
            ClobBookBatch,
        ]
    ] = []
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        # The final event is deliberately only partially evaluated. Its three
        # buckets are sealed in Gamma, but the first two rows still sum to 0.8
        # with one winner and therefore passed the old heuristic.
        for event_index in range(31):
            market_date = (NOW - timedelta(days=event_index)).date()
            winner = event_index % 3
            event_id = f"cal-event-{event_index}"
            event_rows: list[tuple[Market, Decimal, bool]] = []
            targets: list[PriceTarget] = []
            for bucket_index in range(3):
                outcome = bucket_index == winner
                probability = (
                    (Decimal("0.60") if outcome else Decimal("0.20"))
                    if event_index == 30
                    else (Decimal("0.50") if outcome else Decimal("0.25"))
                )
                external_id = f"cal-{event_index}-{bucket_index}"
                market = Market(
                    polymarket_id=external_id,
                    event_id=event_id,
                    condition_id=f"condition-{external_id}",
                    yes_token_id=f"yes-token-{external_id}",
                    no_token_id=f"no-token-{external_id}",
                    station_id=station.id,
                    market_date=market_date,
                    kind=WeatherKind.HIGH,
                    bucket_low_c=Decimal(str(20 + bucket_index)),
                    bucket_high_c=Decimal(str(21 + bucket_index)),
                    rules_raw="integration calibration fixture",
                    resolves_at=NOW,
                    status=MarketStatus.RESOLVED,
                    station_verified=True,
                    resolved_outcome=outcome,
                )
                session.add(market)
                await session.flush()
                targets.append(
                    PriceTarget(
                        market_id=market.id,
                        event_id=event_id,
                        condition_id=str(market.condition_id),
                        yes_token_id=str(market.yes_token_id),
                        no_token_id=str(market.no_token_id),
                    )
                )
                event_rows.append((market, probability, outcome))

            persisted = await persist_clob_batch(session, _dual_batch(targets), targets)
            snapshots = {
                snapshot.market_id: snapshot for snapshot in persisted.snapshots
            }
            event_evidence.append(
                (
                    event_index,
                    [
                        (market, snapshots[market.id], probability, outcome)
                        for market, probability, outcome in event_rows
                    ],
                    persisted.batch,
                )
            )

    async with db_sessions.begin() as session:
        for event_index, event_rows, batch in event_evidence:
            market_date = (NOW - timedelta(days=event_index)).date()
            event_id = f"cal-event-{event_index}"
            winner = event_index % 3
            decision_at = batch.first_seen_at
            resolved_at = datetime.now(UTC)
            model_rows: list[
                tuple[Market, ClobMarketSnapshot, ModelProb, Decimal, bool]
            ] = []
            for market, snapshot, probability, outcome in event_rows:
                session.add(market)
                model_prob = ModelProb(
                    market_id=market.id,
                    ts=decision_at,
                    prob=probability,
                    sigma=Decimal("1"),
                    horizon_h=Decimal("12"),
                    components={
                        "pipeline": {
                            "cohort": PIPELINE_COHORT,
                            "version": PIPELINE_COHORT,
                        }
                    },
                    market_snapshot_id=snapshot.id,
                    market_anchor_protocol=CLOB_DUAL_PROTOCOL,
                )
                session.add(model_prob)
                await session.flush()
                model_rows.append(
                    (market, snapshot, model_prob, probability, outcome)
                )

            for market, _snapshot, _model_prob, _probability, _outcome in model_rows:
                market.resolves_at = resolved_at
            await session.flush()
            resolution_event = await _seal_gamma_event(
                session,
                event_id=event_id,
                markets=[row[0] for row in model_rows],
                winner_index=winner,
                resolved_at=resolved_at,
            )
            for market, _snapshot, _model_prob, _probability, _outcome in model_rows:
                market.resolution_event_id = resolution_event.id
            await session.flush()
            evaluation_rows = model_rows if event_index < 30 else model_rows[:2]
            for market, snapshot, model_prob, probability, outcome in evaluation_rows:
                market_probability = Decimal(snapshot.binary_yes_probability).quantize(
                    Decimal("0.000001")
                )
                session.add(
                    ForecastEvaluation(
                        market_id=market.id,
                        model_prob_id=model_prob.id,
                        decision_at=decision_at,
                        independent_sample_key=(f"KLGA:{market_date.isoformat()}:high:6-24h"),
                        model_probability=probability,
                        horizon_market_probability=market_probability,
                        market_snapshot_at=batch.first_seen_at,
                        executable_bid=snapshot.yes_best_bid,
                        executable_ask=snapshot.yes_best_ask,
                        event_probability_total=Decimal("1.000000"),
                        event_overround=Decimal("0.000000"),
                        resolved_outcome=outcome,
                        resolution_event_id=resolution_event.id,
                        resolution_vector_hash=resolution_event.outcome_vector_hash,
                        model_brier=(probability - Decimal(int(outcome))) ** 2,
                        market_brier=(
                            market_probability - Decimal(int(outcome))
                        )
                        ** 2,
                        evaluated_at=datetime.now(UTC),
                        details={"horizon_bucket": "6-24h"},
                        market_benchmark_protocol=CLOB_DUAL_PROTOCOL,
                        market_snapshot_id=snapshot.id,
                        market_snapshot_hash=snapshot.record_hash,
                    )
                )

    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key=None,
        settlement_calibrator_min_blocks=30,
    )
    calibrator_id = await fit_settlement_calibration(settings, db_sessions)
    async with db_sessions() as session:
        calibrator = await session.scalar(
            select(SettlementProbabilityCalibrator).where(
                SettlementProbabilityCalibrator.id == calibrator_id
            )
        )
    assert calibrator is not None
    assert calibrator.training_blocks == 30
    assert calibrator.eligible is True
    assert calibrator.oof_calibrated_brier < calibrator.oof_baseline_brier
    assert calibrator.delta_brier_ci_high < 0

    app = create_app(settings)
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/accuracy-overview")
    assert response.status_code == 200
    payload = response.json()["settlement_calibrator"]
    assert payload["eligible"] is True
    assert payload["training_blocks"] == 30
    assert payload["calibrated_brier"] < payload["baseline_brier"]


async def test_settlement_calibrator_resets_when_only_legacy_probabilities_exist(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="legacy-calibration-market",
            event_id="legacy-calibration-event",
            condition_id="legacy-calibration-condition",
            yes_token_id="legacy-calibration-yes",
            no_token_id="legacy-calibration-no",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("20"),
            bucket_high_c=Decimal("21"),
            rules_raw="legacy calibration fixture",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            station_verified=True,
            resolved_outcome=True,
        )
        session.add(market)
        await session.flush()
        session.add(
            ModelProb(
                market_id=market.id,
                ts=NOW - timedelta(hours=12),
                prob=Decimal("0.80"),
                sigma=Decimal("1"),
                horizon_h=Decimal("12"),
                components={
                    "pipeline": {
                        "cohort": "legacy-calibration-v0",
                        "version": "legacy-calibration-v0",
                    }
                },
            )
        )
        session.add(
            SettlementProbabilityCalibrator(
                scope_key="global:all:all",
                station_id=None,
                horizon_bucket=None,
                kind=None,
                alpha=Decimal("1.5"),
                beta=Decimal("0.2"),
                training_blocks=30,
                training_buckets=90,
                oof_baseline_brier=Decimal("0.20"),
                oof_calibrated_brier=Decimal("0.10"),
                oof_baseline_ece=Decimal("0.20"),
                oof_calibrated_ece=Decimal("0.10"),
                delta_brier_ci_low=Decimal("-0.15"),
                delta_brier_ci_high=Decimal("-0.05"),
                eligible=True,
                trained_through=NOW,
                details={"market_benchmark_protocol": "legacy-synthetic-yes-v0"},
            )
        )

    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key=None,
        settlement_calibrator_min_blocks=30,
    )
    calibrator_id = await fit_settlement_calibration(settings, db_sessions)

    async with db_sessions() as session:
        calibrator = await session.get(
            SettlementProbabilityCalibrator,
            calibrator_id,
        )
    assert calibrator is not None
    assert calibrator.alpha == Decimal("1.00000000")
    assert calibrator.beta == Decimal("0E-8")
    assert calibrator.training_blocks == 0
    assert calibrator.training_buckets == 0
    assert calibrator.eligible is False
    assert calibrator.oof_baseline_brier is None
    assert calibrator.oof_calibrated_brier is None
    assert calibrator.details["market_benchmark_protocol"] == CLOB_DUAL_PROTOCOL
    assert calibrator.details["probability_pipeline_cohort"] is None
