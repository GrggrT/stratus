from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.gamma import (
    CompleteGammaResolution,
    GammaEventSnapshot,
    ResolvedMarket,
)
from stratus.config import Settings
from stratus.db.models import (
    AccuracyPrediction,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ModelProb,
    Station,
    WeatherKind,
)
from stratus.jobs.generate_censored_challenger import (
    EXPERT,
    generate_censored_challenger,
)
from stratus.repositories.gamma_outcomes import (
    build_gamma_outcome_event_value,
    upsert_gamma_outcome_event,
)
from stratus.services.censored_probability import censored_gaussian_probabilities
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    EVENT_PROBABILITY_QUANTUM,
    event_probability_distribution_sha256,
)
from stratus.services.probability import Bucket

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
_PIPELINE_COHORT = "test-accuracy-v4"


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
                "outcomePrices": ('["1", "0"]' if index == winner_index else '["0", "1"]'),
                "clobTokenIds": json.dumps([market.yes_token_id, market.no_token_id]),
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


async def test_censored_challenger_is_shadow_and_reported(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
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
        # The final event has a complete three-market Gamma ledger but only a
        # two-market evaluation cohort. It must not become training block 21.
        for event_index in range(21):
            resolves_at = now - timedelta(days=event_index + 1)
            event_id = f"censored-event-{event_index}"
            mean = 18.0 + event_index % 6
            boundaries = (
                (None, mean + 0.25),
                (mean + 0.25, mean + 1.5),
                (mean + 1.5, None),
            )
            buckets = tuple(Bucket(low, high) for low, high in boundaries)
            probabilities = censored_gaussian_probabilities(
                buckets,
                mean_c=mean,
                sigma_c=0.8,
                bias_c=0.0,
                sigma_scale=1.0,
            )
            event_rows: list[tuple[Market, ModelProb, Decimal, bool]] = []
            for bucket_index, ((low, high), probability) in enumerate(
                zip(boundaries, probabilities, strict=True)
            ):
                external_id = f"censored-{event_index}-{bucket_index}"
                market = Market(
                    polymarket_id=external_id,
                    event_id=event_id,
                    condition_id=f"condition-{external_id}",
                    yes_token_id=f"yes-token-{external_id}",
                    no_token_id=f"no-token-{external_id}",
                    station_id=station.id,
                    market_date=resolves_at.date(),
                    kind=WeatherKind.HIGH,
                    bucket_low_c=Decimal(str(low)) if low is not None else None,
                    bucket_high_c=Decimal(str(high)) if high is not None else None,
                    rules_raw="interval-censored fixture",
                    resolves_at=resolves_at,
                    status=MarketStatus.RESOLVED,
                    station_verified=True,
                    resolved_outcome=bucket_index == 1,
                )
                session.add(market)
                await session.flush()
                model_prob = ModelProb(
                    market_id=market.id,
                    ts=resolves_at - timedelta(hours=12),
                    prob=Decimal(str(probability)),
                    sigma=Decimal("0.8"),
                    horizon_h=Decimal("12"),
                    components={
                        "blend": {
                            "mean_c": mean,
                            "sigma_c": 0.8,
                            "target_calibration_bucket": "6-24h",
                        }
                    },
                )
                session.add(model_prob)
                await session.flush()
                outcome = bucket_index == 1
                event_rows.append((market, model_prob, Decimal(str(probability)), outcome))

            resolution_event = await _seal_gamma_event(
                session,
                event_id=event_id,
                markets=[row[0] for row in event_rows],
                winner_index=1,
                resolved_at=resolves_at,
            )
            for market, _model_prob, _probability, _outcome in event_rows:
                market.resolution_event_id = resolution_event.id
            # The evaluation provenance trigger reads the persisted market
            # projection; flush the Gamma link before queuing evaluation rows.
            await session.flush()
            evaluation_rows = event_rows if event_index < 20 else event_rows[:2]
            for market, model_prob, probability, outcome in evaluation_rows:
                session.add(
                    ForecastEvaluation(
                        market_id=market.id,
                        model_prob_id=model_prob.id,
                        decision_at=model_prob.ts,
                        independent_sample_key=(
                            f"KLGA:{market.market_date.isoformat()}:high:6-24h"
                        ),
                        model_probability=probability,
                        resolved_outcome=outcome,
                        resolution_event_id=resolution_event.id,
                        resolution_vector_hash=resolution_event.outcome_vector_hash,
                        model_brier=(probability - Decimal(int(outcome))) ** 2,
                        evaluated_at=now,
                        details={"horizon_bucket": "6-24h"},
                    )
                )

        current_boundaries = ((None, 20.25), (20.25, 21.5), (21.5, None))
        current_buckets = tuple(Bucket(low, high) for low, high in current_boundaries)
        current_probabilities = censored_gaussian_probabilities(
            current_buckets,
            mean_c=20.0,
            sigma_c=0.8,
            bias_c=0.0,
            sigma_scale=1.0,
        )
        current_event_id = "censored-current-event"
        current_event_rows: list[tuple[Market, Decimal]] = []
        for bucket_index, ((low, high), probability) in enumerate(
            zip(current_boundaries, current_probabilities, strict=True)
        ):
            market = Market(
                polymarket_id=f"censored-current-{bucket_index}",
                event_id=current_event_id,
                station_id=station.id,
                market_date=(now + timedelta(days=1)).date(),
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal(str(low)) if low is not None else None,
                bucket_high_c=Decimal(str(high)) if high is not None else None,
                rules_raw="current interval-censored fixture",
                resolves_at=now + timedelta(days=1),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
            session.add(market)
            await session.flush()
            current_event_rows.append((market, Decimal(str(probability))))

        raw_total = sum(
            (probability for _market, probability in current_event_rows),
            start=Decimal("0"),
        )
        materialized_probabilities = {
            market.id: (probability / raw_total).quantize(
                EVENT_PROBABILITY_QUANTUM,
                rounding=ROUND_HALF_EVEN,
            )
            for market, probability in current_event_rows
        }
        residual = Decimal("1.000000") - sum(
            materialized_probabilities.values(),
            start=Decimal("0"),
        )
        residual_market_id = max(
            materialized_probabilities,
            key=lambda market_id: (
                materialized_probabilities[market_id],
                -market_id,
            ),
        )
        materialized_probabilities[residual_market_id] += residual
        distribution_hash = event_probability_distribution_sha256(materialized_probabilities)
        complete_current_market_ids = set(materialized_probabilities)

        for market, raw_probability in current_event_rows:
            final_probability = materialized_probabilities[market.id]
            session.add(
                ModelProb(
                    market_id=market.id,
                    ts=now,
                    prob=final_probability,
                    sigma=Decimal("0.8"),
                    horizon_h=Decimal("24"),
                    components={
                        "blend": {
                            "mean_c": 20.0,
                            "sigma_c": 0.8,
                            "target_calibration_bucket": "6-24h",
                        },
                        "pipeline": {"version": _PIPELINE_COHORT},
                        "event_coherence": {
                            "protocol": EVENT_PROBABILITY_COHERENCE_PROTOCOL,
                            "event_id": current_event_id,
                            "materialized": True,
                            "materialization_reason": None,
                            # This fixture has no executable CLOB anchors. That
                            # keeps trade coherence blocked while independently
                            # proving the exhaustive model vector for accuracy.
                            "passed": False,
                            "reason": "event_probability_anchor_incomplete",
                            "detail": None,
                            "bucket_count": len(current_event_rows),
                            "probability_count": len(current_event_rows),
                            "raw_probability": str(raw_probability),
                            "final_probability": str(final_probability),
                            "event_total_before": str(raw_total),
                            "event_total_final": "1.000000",
                            "normalization_scale": str(Decimal("1") / raw_total),
                            "applied": raw_total != Decimal("1"),
                            "distribution_sha256": distribution_hash,
                            "probability_ts": now.isoformat(),
                            "pipeline_cohort": _PIPELINE_COHORT,
                            "anchor_batch_id": None,
                        },
                    },
                )
            )

        # A second active event deliberately lacks one baseline bucket. The
        # sealer must compare against the complete active market universe and
        # reject the whole event rather than treating two available baselines
        # as a complete binary partition.
        for bucket_index, (low, high) in enumerate(current_boundaries):
            market = Market(
                polymarket_id=f"censored-incomplete-{bucket_index}",
                event_id="censored-incomplete-event",
                station_id=station.id,
                market_date=(now + timedelta(days=2)).date(),
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal(str(low)) if low is not None else None,
                bucket_high_c=Decimal(str(high)) if high is not None else None,
                rules_raw="incomplete interval-censored fixture",
                resolves_at=now + timedelta(days=2),
                status=(MarketStatus.ACTIVE if bucket_index < 2 else MarketStatus.BLOCKED),
                station_verified=bucket_index < 2,
            )
            session.add(market)
            await session.flush()
            if bucket_index < 2:
                session.add(
                    ModelProb(
                        market_id=market.id,
                        ts=now,
                        prob=Decimal("0.5"),
                        sigma=Decimal("0.8"),
                        horizon_h=Decimal("24"),
                        components={
                            "blend": {
                                "mean_c": 20.0,
                                "sigma_c": 0.8,
                                "target_calibration_bucket": "6-24h",
                            },
                            "pipeline": {"version": _PIPELINE_COHORT},
                        },
                    )
                )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    assert await generate_censored_challenger(settings, db_sessions) == 3

    async with db_sessions() as session:
        predictions = (
            (
                await session.execute(
                    select(AccuracyPrediction).where(AccuracyPrediction.expert == EXPERT)
                )
            )
            .scalars()
            .all()
        )
        original_hashes = {row.id: row.record_hash for row in predictions}
    assert len(predictions) == 3
    assert {row.market_id for row in predictions} == complete_current_market_ids
    assert all(row.point_in_time_valid for row in predictions)
    assert all(row.evidence_eligible for row in predictions)
    assert all(row.expert_version.endswith("-sealed-v2") for row in predictions)
    assert all(row.distribution["evidence_protocol"] == "sealed-v2" for row in predictions)
    assert all(row.distribution["applied"] is False for row in predictions)
    assert all(row.training_sample_n == 20 for row in predictions)
    assert all(
        row.distribution["source_truth"] == "polymarket_winning_bucket_only" for row in predictions
    )

    # Replaying the hourly job against an unchanged baseline must be a no-op.
    # A later wall-clock training cutoff is not allowed to conflict with or
    # rewrite the already sealed prospective cohort.
    assert await generate_censored_challenger(settings, db_sessions) == 0
    async with db_sessions() as session:
        replayed = (
            (
                await session.execute(
                    select(AccuracyPrediction).where(AccuracyPrediction.expert == EXPERT)
                )
            )
            .scalars()
            .all()
        )
    assert len(replayed) == 3
    assert {row.id: row.record_hash for row in replayed} == original_hashes

    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/accuracy-overview")
    assert response.status_code == 200
    challenger = response.json()["censored_challenger"]
    assert challenger["current_markets"] == 3
    assert challenger["training_blocks"] == 20
    assert challenger["bias_c"] > 0
    assert challenger["applied"] is False
