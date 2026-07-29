from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.collectors.gamma import (
    CompleteGammaResolution,
    GammaEventSnapshot,
    ResolvedMarket,
)
from stratus.config import Settings
from stratus.db.models import (
    AccuracyPrediction,
    ForecastEvaluation,
    Market,
    MarketStatus,
    ModelProb,
    ModelSkillReport,
    Station,
    WeatherKind,
)
from stratus.jobs.audit_accuracy import audit_accuracy
from stratus.jobs.audit_health import attest_accuracy_prediction_hashes
from stratus.jobs.generate_weighted_ensemble import (
    EXPERT,
    EXPERT_VERSION,
    SHRUNK_EXPERT,
    SHRUNK_EXPERT_VERSION,
)
from stratus.repositories.accuracy import (
    AccuracyPredictionIntegrityError,
    AccuracyPredictionValue,
    accuracy_prediction_record_hash,
    upsert_accuracy_predictions,
)
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
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)


async def _seed_baseline(db_sessions: SessionFactory) -> tuple[int, int]:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.7769"),
            lon=Decimal("-73.8740"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="ledger-market",
            event_id="ledger-event",
            station_id=station.id,
            market_date=NOW.date() + timedelta(days=1),
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("20"),
            bucket_high_c=Decimal("22"),
            rules_raw="sealed ledger integration test",
            resolves_at=NOW + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        baseline = ModelProb(
            market_id=market.id,
            ts=NOW,
            prob=Decimal("0.400000"),
            sigma=Decimal("1.2500"),
            horizon_h=Decimal("12"),
            components={"pipeline": {"version": "test-pipeline-v1"}},
        )
        session.add(baseline)
        await session.flush()
        return market.id, baseline.id


def _sealed_value(market_id: int, baseline_id: int) -> AccuracyPredictionValue:
    return AccuracyPredictionValue(
        market_id=market_id,
        ts=NOW,
        expert="historical_weighted_shrunk",
        expert_version="convex-affine-empirical-shrink25-sealed-v2",
        probability=Decimal("0.425000"),
        mean_c=Decimal("21.1000"),
        sigma_c=Decimal("1.3000"),
        training_sample_n=380,
        point_in_time_valid=True,
        distribution={
            "evidence_protocol": "sealed-v2",
            "baseline_pipeline_version": "test-pipeline-v1",
            "baseline_probability": 0.4,
            "normalized_baseline_probability": 0.4,
        },
        generated_at=NOW + timedelta(seconds=2),
        training_cutoff=NOW,
        baseline_model_prob_id=baseline_id,
        input_hash="a" * 64,
        experiment_fingerprint="b" * 64,
        evidence_eligible=True,
    )


def _dual_clob_batch(
    targets: list[PriceTarget],
    *,
    observed_at: datetime,
) -> ClobBooksBatch:
    source_timestamp = observed_at - timedelta(milliseconds=100)
    raw_books: list[dict[str, Any]] = []
    for target in targets:
        for yes_side in (True, False):
            raw_books.append(
                {
                    "market": target.condition_id,
                    "asset_id": (target.yes_token_id if yes_side else target.no_token_id),
                    "timestamp": str(int(source_timestamp.timestamp() * 1000)),
                    "hash": (f"formal-{target.market_id}-{'yes' if yes_side else 'no'}"),
                    "tick_size": "0.01",
                    "neg_risk": False,
                    "bids": [{"price": "0.49", "size": "100"}],
                    "asks": [{"price": "0.51", "size": "100"}],
                }
            )
    raw = json.dumps(list(reversed(raw_books)), separators=(",", ":")).encode()
    requested = tuple(sorted(str(book["asset_id"]) for book in raw_books))
    raw_by_token = {str(book["asset_id"]): book for book in raw_books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=observed_at - timedelta(milliseconds=300),
        response_received_at=observed_at - timedelta(milliseconds=50),
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(raw_by_token[token]) for token in requested),
    )


def _precision_dual_clob_batch(
    target: PriceTarget,
    *,
    observed_at: datetime,
    yes_midpoint: Decimal,
    no_midpoint: Decimal,
) -> ClobBooksBatch:
    """Build an eligible pair whose normalized probability retains 8 decimals."""

    tick = Decimal("0.00000001")
    source_timestamp = observed_at - timedelta(milliseconds=100)
    raw_books: list[dict[str, Any]] = []
    for token_id, midpoint, label in (
        (target.yes_token_id, yes_midpoint, "yes"),
        (target.no_token_id, no_midpoint, "no"),
    ):
        raw_books.append(
            {
                "market": target.condition_id,
                "asset_id": token_id,
                "timestamp": str(int(source_timestamp.timestamp() * 1000)),
                "hash": f"precision-{label}-{int(observed_at.timestamp() * 1_000_000)}",
                "tick_size": str(tick),
                "neg_risk": False,
                "bids": [{"price": str(midpoint - tick), "size": "100"}],
                "asks": [{"price": str(midpoint + tick), "size": "100"}],
            }
        )
    raw = json.dumps(raw_books, separators=(",", ":")).encode()
    requested = tuple(sorted((target.yes_token_id, target.no_token_id)))
    raw_by_token = {str(book["asset_id"]): book for book in raw_books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=observed_at - timedelta(milliseconds=300),
        response_received_at=observed_at - timedelta(milliseconds=50),
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(raw_by_token[token]) for token in requested),
    )


async def test_forecast_evaluation_close_uses_stored_six_decimal_precision(
    db_sessions: SessionFactory,
) -> None:
    """The 8dp immutable CLOB witness must agree at the evaluation's 6dp scale."""

    observed_at = datetime.now(UTC) - timedelta(minutes=2)
    async with db_sessions.begin() as session:
        station = Station(
            icao="KPRC",
            name="Precision regression",
            city="Precision",
            lat=Decimal("40.0000"),
            lon=Decimal("-73.0000"),
            tz="UTC",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="precision-close-market",
            event_id="precision-close-event",
            condition_id="precision-close-condition",
            yes_token_id="precision-close-yes",
            no_token_id="precision-close-no",
            station_id=station.id,
            market_date=observed_at.date() + timedelta(days=1),
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("20"),
            bucket_high_c=Decimal("22"),
            rules_raw="forecast evaluation close precision regression",
            resolves_at=observed_at + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        target = PriceTarget(
            market_id=market.id,
            event_id=market.event_id,
            condition_id=str(market.condition_id),
            yes_token_id=str(market.yes_token_id),
            no_token_id=str(market.no_token_id),
        )
        horizon = await persist_clob_batch(
            session,
            _dual_clob_batch(
                [target],
                observed_at=observed_at,
            ),
            [target],
        )
        close = await persist_clob_batch(
            session,
            _precision_dual_clob_batch(
                target,
                observed_at=observed_at + timedelta(seconds=1),
                yes_midpoint=Decimal("0.51234567"),
                no_midpoint=Decimal("0.48765433"),
            ),
            [target],
        )
        horizon_snapshot = horizon.snapshots[0]
        close_snapshot = close.snapshots[0]
        assert close_snapshot.binary_yes_probability == Decimal("0.51234567")

        valid_model = ModelProb(
            market_id=market.id,
            ts=horizon.batch.first_seen_at,
            prob=Decimal("0.400000"),
            sigma=Decimal("1.2500"),
            horizon_h=Decimal("12"),
            components={"pipeline": {"version": "precision-regression-v1"}},
            market_snapshot_id=horizon_snapshot.id,
            market_anchor_protocol=CLOB_DUAL_PROTOCOL,
        )
        invalid_model = ModelProb(
            market_id=market.id,
            ts=horizon.batch.first_seen_at + timedelta(microseconds=1),
            prob=Decimal("0.400000"),
            sigma=Decimal("1.2500"),
            horizon_h=Decimal("12"),
            components={"pipeline": {"version": "precision-regression-v1"}},
            market_snapshot_id=horizon_snapshot.id,
            market_anchor_protocol=CLOB_DUAL_PROTOCOL,
        )
        session.add_all([valid_model, invalid_model])
        await session.flush()
        session.add(
            ForecastEvaluation(
                market_id=market.id,
                model_prob_id=valid_model.id,
                decision_at=valid_model.ts,
                independent_sample_key="precision-close-valid",
                model_probability=valid_model.prob,
                horizon_market_probability=None,
                market_snapshot_at=horizon.batch.first_seen_at,
                executable_bid=horizon_snapshot.yes_best_bid,
                executable_ask=horizon_snapshot.yes_best_ask,
                pre_resolution_close=Decimal("0.512346"),
                close_snapshot_at=close.batch.first_seen_at,
                event_probability_total=None,
                event_overround=None,
                resolved_outcome=True,
                resolution_event_id=None,
                resolution_vector_hash=None,
                model_brier=Decimal("0.36000000"),
                market_brier=None,
                evaluated_at=datetime.now(UTC),
                details={"regression": "close_precision"},
                market_benchmark_protocol=CLOB_DUAL_PROTOCOL,
                market_snapshot_id=horizon_snapshot.id,
                market_snapshot_hash=horizon_snapshot.record_hash,
                close_snapshot_id=close_snapshot.id,
                close_snapshot_hash=close_snapshot.record_hash,
            )
        )
        await session.flush()
        invalid_model_id = invalid_model.id

    async with db_sessions() as session:
        stored = await session.scalar(
            select(ForecastEvaluation).where(
                ForecastEvaluation.independent_sample_key == "precision-close-valid"
            )
        )
    assert stored is not None
    assert stored.pre_resolution_close == Decimal("0.512346")

    async with db_sessions() as session:
        with pytest.raises(
            DBAPIError,
            match="evaluation close does not match immutable dual CLOB snapshot",
        ):
            session.add(
                ForecastEvaluation(
                    market_id=stored.market_id,
                    model_prob_id=invalid_model_id,
                    decision_at=stored.decision_at + timedelta(microseconds=1),
                    independent_sample_key="precision-close-invalid",
                    model_probability=stored.model_probability,
                    horizon_market_probability=None,
                    market_snapshot_at=stored.market_snapshot_at,
                    executable_bid=stored.executable_bid,
                    executable_ask=stored.executable_ask,
                    pre_resolution_close=Decimal("0.512345"),
                    close_snapshot_at=stored.close_snapshot_at,
                    event_probability_total=None,
                    event_overround=None,
                    resolved_outcome=stored.resolved_outcome,
                    resolution_event_id=None,
                    resolution_vector_hash=None,
                    model_brier=stored.model_brier,
                    market_brier=None,
                    evaluated_at=datetime.now(UTC),
                    details={"regression": "close_precision_invalid"},
                    market_benchmark_protocol=CLOB_DUAL_PROTOCOL,
                    market_snapshot_id=stored.market_snapshot_id,
                    market_snapshot_hash=stored.market_snapshot_hash,
                    close_snapshot_id=stored.close_snapshot_id,
                    close_snapshot_hash=stored.close_snapshot_hash,
                )
            )
            await session.flush()
        await session.rollback()

    async with db_sessions() as session:
        evaluations = (
            (await session.execute(select(ForecastEvaluation).order_by(ForecastEvaluation.id)))
            .scalars()
            .all()
        )
    assert [row.independent_sample_key for row in evaluations] == ["precision-close-valid"]


async def test_sealed_accuracy_prediction_is_append_only_and_retry_idempotent(
    db_sessions: SessionFactory,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    value = _sealed_value(market_id, baseline_id)

    insert_started_at = datetime.now(UTC)
    async with db_sessions.begin() as session:
        assert await upsert_accuracy_predictions(session, [value]) == 1
    async with db_sessions() as session:
        first_generated_at = await session.scalar(
            select(AccuracyPrediction.generated_at).where(AccuracyPrediction.market_id == market_id)
        )
    assert first_generated_at is not None
    assert first_generated_at >= insert_started_at

    retry = replace(value, generated_at=NOW + timedelta(minutes=1))
    async with db_sessions.begin() as session:
        assert await upsert_accuracy_predictions(session, [retry]) == 1

    async with db_sessions() as session:
        predictions = (await session.execute(select(AccuracyPrediction))).scalars().all()
    assert len(predictions) == 1
    stored = predictions[0]
    assert stored.generated_at == first_generated_at
    assert stored.training_cutoff == value.training_cutoff
    assert stored.baseline_model_prob_id == baseline_id
    assert stored.input_hash == value.input_hash
    assert stored.experiment_fingerprint == value.experiment_fingerprint
    assert stored.evidence_eligible is True
    assert stored.record_hash == accuracy_prediction_record_hash(value)
    assert stored.distribution == value.distribution

    conflicting = replace(value, probability=Decimal("0.426000"))
    async with db_sessions() as session:
        with pytest.raises(
            AccuracyPredictionIntegrityError,
            match="sealed accuracy prediction conflict",
        ):
            await upsert_accuracy_predictions(session, [conflicting])
        await session.rollback()

    async with db_sessions() as session:
        stored_after_conflict = (await session.execute(select(AccuracyPrediction))).scalar_one()
    assert stored_after_conflict.probability == value.probability
    assert stored_after_conflict.record_hash == accuracy_prediction_record_hash(value)


@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_database_trigger_rejects_accuracy_prediction_mutation(
    db_sessions: SessionFactory,
    operation: str,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    value = _sealed_value(market_id, baseline_id)
    async with db_sessions.begin() as session:
        await upsert_accuracy_predictions(session, [value])

    statement = (
        update(AccuracyPrediction)
        .where(AccuracyPrediction.market_id == market_id)
        .values(probability=Decimal("0.900000"))
        if operation == "update"
        else delete(AccuracyPrediction).where(AccuracyPrediction.market_id == market_id)
    )
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="append-only evidence ledger"):
            await session.execute(statement)
        await session.rollback()

    async with db_sessions() as session:
        stored = (
            await session.execute(
                select(AccuracyPrediction).where(AccuracyPrediction.market_id == market_id)
            )
        ).scalar_one()
    assert stored.probability == value.probability
    assert stored.record_hash == accuracy_prediction_record_hash(value)


async def test_hash_attestation_uses_generated_time_and_detects_invalid_hash(
    db_sessions: SessionFactory,
) -> None:
    market_id, _baseline_id = await _seed_baseline(db_sessions)
    historical_ts = NOW - timedelta(days=181)
    async with db_sessions.begin() as session:
        baseline = ModelProb(
            market_id=market_id,
            ts=historical_ts,
            prob=Decimal("0.400000"),
            sigma=Decimal("1.2500"),
            horizon_h=Decimal("12"),
            components={"pipeline": {"version": "test-pipeline-v1"}},
        )
        session.add(baseline)
        await session.flush()
        session.add(
            AccuracyPrediction(
                market_id=market_id,
                ts=historical_ts,
                expert="hash-attestation-regression",
                expert_version="hash-attestation-v1",
                probability=Decimal("0.425000"),
                mean_c=Decimal("21.1000"),
                sigma_c=Decimal("1.3000"),
                training_sample_n=380,
                point_in_time_valid=True,
                distribution={
                    "evidence_protocol": "sealed-v2",
                    "baseline_pipeline_version": "test-pipeline-v1",
                    "baseline_probability": 0.4,
                    "normalized_baseline_probability": 0.4,
                },
                generated_at=datetime.now(UTC),
                training_cutoff=historical_ts,
                baseline_model_prob_id=baseline.id,
                input_hash="a" * 64,
                experiment_fingerprint="b" * 64,
                record_hash="f" * 64,
                evidence_eligible=True,
            )
        )

    async with db_sessions() as session:
        scanned, invalid = await attest_accuracy_prediction_hashes(
            session,
            now=datetime.now(UTC),
        )
        assert len(session.identity_map) == 0

    # The evidence timestamp is outside 180 days, but the database-sealed
    # generation timestamp is current and therefore must still be attested.
    assert scanned == 1
    assert invalid == 1


async def test_sealed_baseline_rejects_content_change_but_allows_identical_retry(
    db_sessions: SessionFactory,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    async with db_sessions.begin() as session:
        await upsert_accuracy_predictions(session, [_sealed_value(market_id, baseline_id)])

    async with db_sessions.begin() as session:
        await session.execute(
            update(ModelProb).where(ModelProb.id == baseline_id).values(prob=Decimal("0.400000"))
        )

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="immutable baseline"):
            await session.execute(
                update(ModelProb)
                .where(ModelProb.id == baseline_id)
                .values(prob=Decimal("0.410000"))
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="immutable baseline"):
            await session.execute(delete(ModelProb).where(ModelProb.id == baseline_id))
        await session.rollback()

    async with db_sessions() as session:
        baseline = await session.get(ModelProb, baseline_id)
    assert baseline is not None
    assert baseline.prob == Decimal("0.400000")


async def test_database_trigger_prefers_effective_pipeline_cohort(
    db_sessions: SessionFactory,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    effective_cohort = "test-pipeline-v1+source-fingerprint"
    async with db_sessions.begin() as session:
        await session.execute(
            update(ModelProb)
            .where(ModelProb.id == baseline_id)
            .values(
                components={
                    "pipeline": {
                        "version": "test-pipeline-v1",
                        "cohort": effective_cohort,
                    }
                }
            )
        )

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="pipeline version mismatch"):
            await upsert_accuracy_predictions(
                session,
                [_sealed_value(market_id, baseline_id)],
            )
        await session.rollback()

    value = _sealed_value(market_id, baseline_id)
    value = replace(
        value,
        distribution={
            **value.distribution,
            "baseline_pipeline_version": effective_cohort,
        },
    )
    async with db_sessions.begin() as session:
        assert await upsert_accuracy_predictions(session, [value]) == 1

    async with db_sessions() as session:
        stored = await session.scalar(
            select(AccuracyPrediction).where(AccuracyPrediction.market_id == market_id)
        )
    assert stored is not None
    assert stored.distribution["baseline_pipeline_version"] == effective_cohort


async def test_database_trigger_falls_back_from_empty_cohort_to_version(
    db_sessions: SessionFactory,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    async with db_sessions.begin() as session:
        await session.execute(
            update(ModelProb)
            .where(ModelProb.id == baseline_id)
            .values(components={"pipeline": {"version": "test-pipeline-v1", "cohort": ""}})
        )

    value = _sealed_value(market_id, baseline_id)
    async with db_sessions.begin() as session:
        assert await upsert_accuracy_predictions(session, [value]) == 1


async def test_database_trigger_rejects_missing_effective_pipeline(
    db_sessions: SessionFactory,
) -> None:
    market_id, baseline_id = await _seed_baseline(db_sessions)
    async with db_sessions.begin() as session:
        await session.execute(
            update(ModelProb).where(ModelProb.id == baseline_id).values(components={"pipeline": {}})
        )

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="pipeline version mismatch"):
            await upsert_accuracy_predictions(
                session,
                [_sealed_value(market_id, baseline_id)],
            )
        await session.rollback()


async def test_effective_cohort_migration_downgrade_is_reversible(
    db_sessions: SessionFactory,
) -> None:
    config = Config("alembic.ini")
    await asyncio.to_thread(command.downgrade, config, "20260714_0028")
    try:
        # Exercise the historical schema with historical-shape SQL.  The
        # current ORM contains columns introduced by later migrations (for
        # example markets.resolution_event_id), so using it while the database
        # is intentionally at 0028 would test ORM/schema incompatibility rather
        # than downgrade reversibility.
        async with db_sessions.begin() as session:
            station_id = await session.scalar(
                text(
                    """
                    INSERT INTO stations (icao, name, city, lat, lon, tz)
                    VALUES ('KLGA', 'LaGuardia', 'New York City',
                            40.7769, -73.8740, 'America/New_York')
                    RETURNING id
                    """
                )
            )
            assert station_id is not None
            market_id = await session.scalar(
                text(
                    """
                    INSERT INTO markets (
                        polymarket_id, event_id, station_id, market_date, kind,
                        bucket_low_c, bucket_high_c, rules_raw, resolves_at,
                        status, station_verified
                    ) VALUES (
                        'ledger-market', 'ledger-event', :station_id,
                        :market_date, 'high', 20, 22,
                        'sealed ledger integration test', :resolves_at,
                        'active', true
                    )
                    RETURNING id
                    """
                ),
                {
                    "station_id": station_id,
                    "market_date": NOW.date() + timedelta(days=1),
                    "resolves_at": NOW + timedelta(hours=12),
                },
            )
            assert market_id is not None
            baseline_id = await session.scalar(
                text(
                    """
                    INSERT INTO model_probs (
                        market_id, ts, prob, sigma, horizon_h, components
                    ) VALUES (
                        :market_id, :ts, 0.4, 1.25, 12,
                        '{"pipeline":{"version":"test-pipeline-v1"}}'::jsonb
                    )
                    RETURNING id
                    """
                ),
                {"market_id": market_id, "ts": NOW},
            )
            assert baseline_id is not None
            await session.execute(
                text(
                    """
                    INSERT INTO accuracy_predictions (
                        market_id, ts, expert, expert_version, probability,
                        mean_c, sigma_c, training_sample_n,
                        point_in_time_valid, distribution, generated_at,
                        training_cutoff, baseline_model_prob_id, input_hash,
                        experiment_fingerprint, record_hash, evidence_eligible
                    ) VALUES (
                        :market_id, :ts, 'rollback_legacy_writer',
                        'sealed-v2-legacy-shape', 0.425, 21.1, 1.3, 380, true,
                        CAST(:distribution AS jsonb),
                        :ts, :ts, :baseline_id, :input_hash,
                        :experiment_fingerprint, :record_hash, true
                    )
                    """
                ),
                {
                    "market_id": market_id,
                    "ts": NOW,
                    "baseline_id": baseline_id,
                    "input_hash": "d" * 64,
                    "experiment_fingerprint": "e" * 64,
                    "record_hash": "f" * 64,
                    "distribution": json.dumps(
                        {
                            "evidence_protocol": "sealed-v2",
                            "baseline_pipeline_version": "test-pipeline-v1",
                            "baseline_probability": 0.4,
                        }
                    ),
                },
            )
        async with db_sessions() as session:
            distribution = await session.scalar(
                text(
                    """
                    SELECT distribution
                    FROM accuracy_predictions
                    WHERE expert = 'rollback_legacy_writer'
                    """
                )
            )
        assert distribution is not None
        assert "normalized_baseline_probability" not in distribution
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")


async def test_formal_audit_uses_complete_sealed_vectors_and_real_coverage(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    fingerprint = "b" * 64
    async with db_sessions.begin() as session:
        station = Station(
            icao="EDDF",
            name="Frankfurt Airport",
            city="Frankfurt",
            lat=Decimal("50.0379"),
            lon=Decimal("8.5622"),
            tz="Europe/Berlin",
        )
        session.add(station)
        await session.flush()
        for day_offset in range(31):
            event_id = f"sealed-vector-{day_offset}"
            provisional_now = datetime.now(UTC)
            market_rows: list[tuple[Market, bool, int]] = []
            for bucket_index, outcome in enumerate((True, False)):
                market = Market(
                    polymarket_id=f"sealed-vector-{day_offset}-{bucket_index}",
                    event_id=event_id,
                    condition_id=f"condition-{day_offset}-{bucket_index}",
                    yes_token_id=f"yes-token-{day_offset}-{bucket_index}",
                    no_token_id=f"no-token-{day_offset}-{bucket_index}",
                    station_id=station.id,
                    market_date=provisional_now.date() + timedelta(days=day_offset + 1),
                    kind=WeatherKind.HIGH,
                    bucket_low_c=(None if bucket_index == 0 else Decimal("20")),
                    bucket_high_c=(Decimal("20") if bucket_index == 0 else None),
                    rules_raw="sealed prospective vector",
                    resolves_at=provisional_now + timedelta(days=day_offset + 1),
                    status=MarketStatus.ACTIVE,
                    station_verified=True,
                )
                session.add(market)
                await session.flush()
                market_rows.append((market, outcome, bucket_index))

            targets = [
                PriceTarget(
                    market_id=market.id,
                    event_id=market.event_id,
                    condition_id=str(market.condition_id),
                    yes_token_id=str(market.yes_token_id),
                    no_token_id=str(market.no_token_id),
                )
                for market, _outcome, _bucket_index in market_rows
            ]
            persisted = await persist_clob_batch(
                session,
                _dual_clob_batch(targets, observed_at=datetime.now(UTC)),
                targets,
            )
            decision_at = persisted.batch.first_seen_at
            snapshots_by_market = {snapshot.market_id: snapshot for snapshot in persisted.snapshots}
            event_rows: list[tuple[Market, ModelProb, bool, int]] = []
            for market, outcome, bucket_index in market_rows:
                market.resolves_at = decision_at + timedelta(days=day_offset + 1)
                snapshot = snapshots_by_market[market.id]
                baseline = ModelProb(
                    market_id=market.id,
                    ts=decision_at,
                    prob=Decimal("0.500000"),
                    sigma=Decimal("1.5"),
                    horizon_h=Decimal("12"),
                    components={"pipeline": {"version": "test-pipeline-v1"}},
                    market_snapshot_id=snapshot.id,
                    market_anchor_protocol=CLOB_DUAL_PROTOCOL,
                )
                session.add(baseline)
                await session.flush()
                event_rows.append((market, baseline, outcome, bucket_index))

            event_values: list[AccuracyPredictionValue] = []
            for market, baseline, outcome, bucket_index in event_rows:
                candidate_probability = Decimal("0.800000" if outcome else "0.200000")
                event_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=decision_at,
                        expert=SHRUNK_EXPERT,
                        expert_version=SHRUNK_EXPERT_VERSION,
                        probability=candidate_probability,
                        mean_c=Decimal("20.0000"),
                        sigma_c=Decimal("1.5000"),
                        training_sample_n=380,
                        point_in_time_valid=True,
                        distribution={
                            "evidence_protocol": "sealed-v2",
                            "horizon_bucket": "6-24h",
                            "baseline_pipeline_version": "test-pipeline-v1",
                            "baseline_probability": 0.5,
                            "normalized_baseline_probability": 0.5,
                        },
                        generated_at=datetime.now(UTC),
                        training_cutoff=decision_at,
                        baseline_model_prob_id=baseline.id,
                        input_hash=f"{day_offset * 2 + bucket_index + 1:064x}",
                        experiment_fingerprint=fingerprint,
                        evidence_eligible=True,
                    )
                )
                if day_offset < 30:
                    event_values.append(
                        replace(
                            event_values[-1],
                            expert=EXPERT,
                            expert_version=EXPERT_VERSION,
                            experiment_fingerprint="c" * 64,
                        )
                    )
            await upsert_accuracy_predictions(session, event_values)
            # PostgreSQL supplies challenger generated_at. Take the outcome
            # timestamp from the same server only after those inserts, keeping
            # the fixture prospective while still satisfying first_seen >=
            # fully_resolved_at when the immutable ledger is inserted.
            resolved_at = await session.scalar(text("SELECT clock_timestamp()"))
            assert isinstance(resolved_at, datetime)
            resolution = CompleteGammaResolution(
                event_id=event_id,
                outcomes=tuple(
                    ResolvedMarket(
                        polymarket_id=market.polymarket_id,
                        outcome_yes=outcome,
                        closed_at=resolved_at,
                        yes_price="1" if outcome else "0",
                        no_price="0" if outcome else "1",
                        condition_id=market.condition_id,
                        question_id=f"question-{day_offset}-{bucket_index}",
                        yes_token_id=str(market.yes_token_id),
                        no_token_id=str(market.no_token_id),
                        uma_resolution_status="resolved",
                        resolved_by="test-oracle",
                    )
                    for market, _baseline, outcome, bucket_index in event_rows
                ),
                first_outcome_at=resolved_at,
                fully_resolved_at=resolved_at,
                event_closed_at=resolved_at,
                event_updated_at=resolved_at,
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
                        "outcomePrices": ('["1", "0"]' if outcome else '["0", "1"]'),
                        "clobTokenIds": json.dumps([market.yes_token_id, market.no_token_id]),
                        "conditionId": market.condition_id,
                        "questionID": f"question-{day_offset}-{bucket_index}",
                        "resolvedBy": "test-oracle",
                    }
                    for market, _baseline, outcome, bucket_index in event_rows
                ],
            }
            raw_payload = json.dumps(payload, separators=(",", ":")).encode()
            snapshot = GammaEventSnapshot(
                event_id=event_id,
                payload=payload,
                source_url=f"https://gamma-api.polymarket.com/events/{event_id}",
                response_body=raw_payload,
                response_sha256=hashlib.sha256(raw_payload).hexdigest(),
                response_bytes=len(raw_payload),
                fetched_at=datetime.now(UTC),
                http_date=resolved_at,
                last_modified=resolved_at,
                etag=f'"sealed-vector-{day_offset}"',
            )
            outcome_event = await upsert_gamma_outcome_event(
                session,
                build_gamma_outcome_event_value(
                    snapshot,
                    resolution,
                    [market for market, _baseline, _outcome, _index in event_rows],
                ),
            )
            for market, _baseline, outcome, _bucket_index in event_rows:
                market.status = MarketStatus.RESOLVED
                market.resolved_outcome = outcome
                market.resolution_event_id = outcome_event.id
            await session.flush()

            for market, baseline, outcome, _bucket_index in event_rows:
                clob_snapshot = snapshots_by_market[market.id]
                session.add(
                    ForecastEvaluation(
                        market_id=market.id,
                        model_prob_id=baseline.id,
                        decision_at=decision_at,
                        independent_sample_key=event_id,
                        model_probability=baseline.prob,
                        horizon_market_probability=Decimal("0.500000"),
                        market_snapshot_at=persisted.batch.first_seen_at,
                        executable_bid=clob_snapshot.yes_best_bid,
                        executable_ask=clob_snapshot.yes_best_ask,
                        pre_resolution_close=None,
                        close_snapshot_at=None,
                        event_probability_total=Decimal("1"),
                        event_overround=Decimal("0"),
                        resolved_outcome=outcome,
                        resolution_event_id=outcome_event.id,
                        resolution_vector_hash=outcome_event.outcome_vector_hash,
                        model_brier=Decimal("0.25000000"),
                        market_brier=Decimal("0.25000000"),
                        evaluated_at=datetime.now(UTC),
                        details={"horizon_bucket": "6-24h"},
                        market_benchmark_protocol=CLOB_DUAL_PROTOCOL,
                        market_snapshot_id=clob_snapshot.id,
                        market_snapshot_hash=clob_snapshot.record_hash,
                    )
                )

            if day_offset == 0:
                # This formally resolved row deliberately uses the historical
                # synthetic protocol.  The accuracy audit must not let it
                # enlarge the prospective dual-token denominator.
                legacy_decision_at = decision_at - timedelta(seconds=1)
                legacy_baseline = ModelProb(
                    market_id=event_rows[0][0].id,
                    ts=legacy_decision_at,
                    prob=Decimal("0.500000"),
                    sigma=Decimal("1.5"),
                    horizon_h=Decimal("12"),
                    components={"pipeline": {"version": "test-pipeline-v1"}},
                )
                session.add(legacy_baseline)
                await session.flush()
                session.add(
                    ForecastEvaluation(
                        market_id=event_rows[0][0].id,
                        model_prob_id=legacy_baseline.id,
                        decision_at=legacy_decision_at,
                        independent_sample_key=f"{event_id}:legacy",
                        model_probability=legacy_baseline.prob,
                        horizon_market_probability=None,
                        market_snapshot_at=None,
                        executable_bid=None,
                        executable_ask=None,
                        pre_resolution_close=None,
                        close_snapshot_at=None,
                        event_probability_total=None,
                        event_overround=None,
                        resolved_outcome=event_rows[0][2],
                        resolution_event_id=outcome_event.id,
                        resolution_vector_hash=outcome_event.outcome_vector_hash,
                        model_brier=Decimal("0.25000000"),
                        market_brier=None,
                        evaluated_at=datetime.now(UTC),
                        details={"horizon_bucket": "6-24h"},
                        market_benchmark_protocol="legacy-synthetic-yes-v0",
                    )
                )

    settings = Settings(
        database_url=migrated_database_url,
        accuracy_trajectory_min_blocks=30,
    )
    await audit_accuracy(settings, db_sessions)

    async with db_sessions() as session:
        reports = (await session.execute(select(ModelSkillReport))).scalars().all()
    report = next(
        row
        for row in reports
        if row.details.get("expert") == SHRUNK_EXPERT and row.window_days == 30
    )
    assert report.sample_n == 31
    assert float(report.coverage) == pytest.approx(1.0)
    assert report.delta_brier is not None and report.delta_brier < 0
    assert report.ci_high is not None and report.ci_high < 0
    assert report.eligible is True
    assert report.details["experiment_fingerprint"] == fingerprint
    assert report.details["expected_blocks"] == 31
    assert report.details["covered_blocks"] == 31

    incomplete_report = next(
        row for row in reports if row.details.get("expert") == EXPERT and row.window_days == 30
    )
    assert incomplete_report.sample_n == 30
    assert float(incomplete_report.coverage) == pytest.approx(30 / 31)
    assert incomplete_report.delta_brier is not None and incomplete_report.delta_brier < 0
    assert incomplete_report.ci_high is not None and incomplete_report.ci_high < 0
    assert incomplete_report.eligible is False
    assert incomplete_report.details["expected_blocks"] == 31
    assert incomplete_report.details["covered_blocks"] == 30

    baseline_report = next(
        row
        for row in reports
        if row.details.get("market_benchmark_protocol") == CLOB_DUAL_PROTOCOL
        and row.details.get("expert") is None
        and row.window_days == 30
    )
    assert baseline_report.sample_n == 62
