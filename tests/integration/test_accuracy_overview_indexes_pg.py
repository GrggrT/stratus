from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.db.models import (
    Market,
    MarketStatus,
    ModelProb,
    Station,
    WeatherKind,
)
from stratus.operator_api import _latest_accuracy_prediction_batches
from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    upsert_accuracy_predictions,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)


def _serialized_plan(value: object) -> str:
    return json.dumps(value, sort_keys=True)


async def test_accuracy_overview_indexes_match_the_bounded_read_queries(
    db_sessions: SessionFactory,
) -> None:
    expected = {
        "ix_accuracy_predictions_eligible_expert_ts",
        "ix_accuracy_predictions_eligible_expert_version_market_ts",
        "ix_model_skill_window_evaluated",
    }
    async with db_sessions() as session:
        definitions = {
            str(name): str(definition)
            for name, definition in (
                await session.execute(
                    text(
                        """
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND indexname = ANY(CAST(:names AS text[]))
                        """
                    ),
                    {"names": sorted(expected)},
                )
            ).all()
        }

    assert definitions.keys() == expected
    assert "(expert, ts)" in definitions["ix_accuracy_predictions_eligible_expert_ts"]
    assert (
        "(expert, expert_version, market_id, ts)"
        in definitions["ix_accuracy_predictions_eligible_expert_version_market_ts"]
    )
    assert (
        "WHERE (evidence_eligible IS TRUE)"
        in definitions["ix_accuracy_predictions_eligible_expert_ts"]
    )
    assert (
        "WHERE (evidence_eligible IS TRUE)"
        in definitions["ix_accuracy_predictions_eligible_expert_version_market_ts"]
    )
    assert "(window_days, evaluated_at)" in definitions["ix_model_skill_window_evaluated"]

    async with db_sessions.begin() as session:
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        skill_plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT *
                    FROM model_skill_reports
                    WHERE window_days = 60
                      AND evaluated_at >= now() - interval '2 days'
                    """
                )
            )
        ).scalar_one()
        latest_plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT expert, max(ts)
                    FROM accuracy_predictions
                    WHERE expert IN ('censored_gaussian', 'regime_emos')
                      AND evidence_eligible IS TRUE
                    GROUP BY expert
                    """
                )
            )
        ).scalar_one()
        weighted_plan = (
            await session.execute(
                text(
                    """
                    EXPLAIN (FORMAT JSON)
                    SELECT DISTINCT ON (expert, market_id) id
                    FROM accuracy_predictions
                    WHERE expert = 'historical_weighted'
                      AND expert_version = 'weighted-v1'
                      AND evidence_eligible IS TRUE
                    ORDER BY expert, market_id, ts DESC
                    """
                )
            )
        ).scalar_one()

    assert "ix_model_skill_window_evaluated" in _serialized_plan(skill_plan)
    serialized_latest_plan = _serialized_plan(latest_plan)
    assert any(
        name in serialized_latest_plan
        for name in (
            "ix_accuracy_predictions_eligible_expert_ts",
            "ix_accuracy_predictions_eligible_expert_version_market_ts",
        )
    )
    assert "ix_accuracy_predictions_eligible_expert_version_market_ts" in _serialized_plan(
        weighted_plan
    )


async def test_latest_expert_batch_excludes_ineligible_same_timestamp(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KOVW",
            name="Overview index fixture",
            city="Overview",
            lat=Decimal("40"),
            lon=Decimal("-73"),
            tz="UTC",
        )
        session.add(station)
        await session.flush()
        markets = [
            Market(
                polymarket_id=f"overview-index-{suffix}",
                event_id=f"overview-index-event-{suffix}",
                station_id=station.id,
                market_date=NOW.date(),
                kind=WeatherKind.HIGH,
                rules_raw=f"overview index fixture {suffix}",
                resolves_at=NOW + timedelta(days=1),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
            for suffix in ("eligible", "ineligible")
        ]
        session.add_all(markets)
        await session.flush()
        baseline = ModelProb(
            market_id=markets[0].id,
            ts=NOW,
            prob=Decimal("0.50"),
            sigma=Decimal("1"),
            horizon_h=Decimal("12"),
            components={"pipeline": {"version": "overview-index-v1"}},
        )
        session.add(baseline)
        await session.flush()
        await upsert_accuracy_predictions(
            session,
            [
                AccuracyPredictionValue(
                    market_id=markets[0].id,
                    ts=NOW,
                    expert="censored_gaussian",
                    expert_version="eligible-v1",
                    probability=Decimal("0.55"),
                    mean_c=None,
                    sigma_c=None,
                    training_sample_n=100,
                    point_in_time_valid=True,
                    generated_at=NOW,
                    training_cutoff=NOW - timedelta(hours=1),
                    baseline_model_prob_id=baseline.id,
                    input_hash="a" * 64,
                    experiment_fingerprint="b" * 64,
                    evidence_eligible=True,
                    distribution={
                        "evidence_protocol": "sealed-v2",
                        "baseline_pipeline_version": "overview-index-v1",
                        "baseline_probability": 0.5,
                        "normalized_baseline_probability": 0.5,
                    },
                ),
                AccuracyPredictionValue(
                    market_id=markets[1].id,
                    ts=NOW,
                    expert="censored_gaussian",
                    expert_version="ineligible-v1",
                    probability=Decimal("0.60"),
                    mean_c=None,
                    sigma_c=None,
                    training_sample_n=0,
                    point_in_time_valid=False,
                    generated_at=NOW,
                    evidence_eligible=False,
                    distribution={},
                ),
            ],
        )

        batches = await _latest_accuracy_prediction_batches(session)

    assert len(batches["censored_gaussian"]) == 1
    assert batches["censored_gaussian"][0].expert_version == "eligible-v1"
    assert batches["censored_gaussian"][0].evidence_eligible is True
