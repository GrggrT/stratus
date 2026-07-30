from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    AccuracyPrediction,
    DailyActual,
    Forecast,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelProb,
    Station,
    WeatherKind,
)
from stratus.jobs.generate_weighted_ensemble import (
    EXPERT,
    EXPERT_VERSION,
    SHRUNK_EXPERT,
    SHRUNK_EXPERT_VERSION,
    generate_weighted_ensemble,
)
from stratus.repositories.accuracy import accuracy_prediction_record_is_valid

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def _legacy_forecast_available_at(session: AsyncSession) -> datetime:
    value = await session.scalar(
        text(
            "SELECT opened_at - interval '1 microsecond' "
            "FROM forecast_provenance_runtime WHERE id = 1"
        )
    )
    if not isinstance(value, datetime):
        raise RuntimeError("forecast provenance boundary is missing")
    return value


async def test_weighted_ensemble_writes_only_shadow_predictions(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=1)
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
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
        historical = []
        actuals = []
        for index in range(400):
            valid_date = now.date() - timedelta(days=400 - index)
            actual = 20 + 6 * math.sin(index * 2 * math.pi / 365.25)
            actuals.append(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(actual)),
                )
            )
            values = {
                "ecmwf": actual + 0.2 * math.sin(index * 1.1),
                "gfs": actual + 2.5 * math.sin(index * 0.37) + 0.8,
                "icon": actual + 0.4 * math.cos(index * 0.83),
                "gem": actual + 1.8 * math.cos(index * 0.43) - 0.5,
            }
            historical.extend(
                HistoricalForecast(
                    station_id=station.id,
                    model=model,
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    horizon_h=12,
                    temp_c=Decimal(str(value)),
                    source="test-walk-forward",
                    fetched_at=now - timedelta(days=1),
                )
                for model, value in values.items()
            )
        session.add_all(actuals)
        session.add_all(historical)
        await session.flush()
        actual_first_seen_at = await session.scalar(
            select(func.max(DailyActual.first_seen_at))
        )
        historical_first_seen_at = await session.scalar(
            select(func.max(HistoricalForecast.first_seen_at))
        )
        assert actual_first_seen_at is not None
        assert historical_first_seen_at is not None
        # The baseline snapshot is physically after PostgreSQL attested the
        # synthetic labels and forecasts; caller-supplied backdating is forbidden.
        now = max(actual_first_seen_at, historical_first_seen_at) + timedelta(
            microseconds=1
        )

        event_id = "weighted-event"
        buckets = ((None, 18.0), (18.0, 22.0), (22.0, None))
        markets = []
        for index, (low, high) in enumerate(buckets):
            market = Market(
                polymarket_id=f"weighted-{index}",
                event_id=event_id,
                station_id=station.id,
                market_date=now.date() + timedelta(days=1),
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal(str(low)) if low is not None else None,
                bucket_high_c=Decimal(str(high)) if high is not None else None,
                rules_raw="test rules",
                resolves_at=now + timedelta(hours=12),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
            session.add(market)
            markets.append(market)
        await session.flush()
        for market in markets:
            session.add(
                ModelProb(
                    market_id=market.id,
                    ts=now,
                    prob=Decimal("0.333333"),
                    sigma=Decimal("1.5"),
                    horizon_h=Decimal("12"),
                    components={"pipeline": {"version": "test-v4"}},
                )
            )
        current_values = {"ecmwf": 20.1, "gfs": 22.2, "icon": 20.2, "gem": 18.4}
        for model, value in current_values.items():
            session.add(
                Forecast(
                    station_id=station.id,
                    model=model,
                    run_time=now - timedelta(hours=2),
                    valid_date=now.date() + timedelta(days=1),
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(value)),
                    fetched_at=legacy_available_at,
                    available_at=legacy_available_at,
                )
            )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    assert await generate_weighted_ensemble(settings, db_sessions) == 6
    async with db_sessions() as session:
        first_record_hashes = dict(
            (
                (
                    row.market_id,
                    row.expert,
                    row.expert_version,
                ),
                row.record_hash,
            )
            for row in (
                await session.execute(select(AccuracyPrediction))
            ).scalars()
        )
    # A worker restart over the same fresh baseline cohort must be an
    # idempotent replay, not a sealed-evidence conflict.
    assert await generate_weighted_ensemble(settings, db_sessions) == 6

    async with db_sessions() as session:
        predictions = (
            await session.execute(
                select(AccuracyPrediction).order_by(AccuracyPrediction.market_id)
            )
        ).scalars().all()
    assert len(predictions) == 6
    assert {
        (row.market_id, row.expert, row.expert_version): row.record_hash
        for row in predictions
    } == first_record_hashes
    baseline_predictions = [row for row in predictions if row.expert == EXPERT]
    shrunk_predictions = [row for row in predictions if row.expert == SHRUNK_EXPERT]
    assert len(baseline_predictions) == 3
    assert len(shrunk_predictions) == 3
    assert all(row.expert_version == EXPERT_VERSION for row in baseline_predictions)
    assert all(row.expert_version == SHRUNK_EXPERT_VERSION for row in shrunk_predictions)
    assert sum(
        (row.probability for row in baseline_predictions),
        start=Decimal("0"),
    ) == Decimal("1.000000")
    assert sum(
        (row.probability for row in shrunk_predictions),
        start=Decimal("0"),
    ) == Decimal("1.000000")
    assert sum(
        (
            Decimal(str(row.distribution["normalized_baseline_probability"]))
            for row in baseline_predictions
        ),
        start=Decimal("0"),
    ) == Decimal("1.000000")
    assert all(row.point_in_time_valid for row in predictions)
    assert all(row.evidence_eligible for row in predictions)
    assert all(row.input_hash is not None and len(row.input_hash) == 64 for row in predictions)
    assert all(
        row.experiment_fingerprint is not None
        and len(row.experiment_fingerprint) == 64
        for row in predictions
    )
    assert all(row.baseline_model_prob_id is not None for row in predictions)
    assert all(row.training_cutoff == now for row in predictions)
    assert all(row.generated_at >= now for row in predictions)
    assert all(accuracy_prediction_record_is_valid(row) for row in predictions)
    assert all(row.distribution["evidence_protocol"] == "sealed-v2" for row in predictions)
    assert all(row.distribution["event_cohort"]["market_count"] == 3 for row in predictions)
    assert all(row.distribution["applied"] is False for row in predictions)
    assert all(row.distribution["historical_oof_eligible"] is True for row in predictions)
    assert all(row.distribution["historical_delta_ci_high"] < 0 for row in predictions)
    assert all(row.distribution["minimum_prospective_blocks"] == 60 for row in predictions)
    assert all(
        row.distribution["empirical_blend_weight"] == 0.25 for row in shrunk_predictions
    )

    app = create_app(settings)
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/accuracy-overview")
    assert response.status_code == 200, response.text
    weighted = response.json()["weighted_ensemble"]
    assert weighted["current_markets"] == 3
    assert weighted["fitted_scopes"] == 1
    assert weighted["minimum_training_sample_n"] >= 360
    assert weighted["maximum_training_sample_n"] >= weighted["minimum_training_sample_n"]
    assert weighted["historical_samples"] >= 170
    assert weighted["historical_candidate_rmse_c"] < weighted["historical_baseline_rmse_c"]
    assert weighted["historical_ci_high"] < 0
    assert weighted["historical_oof_eligible"] is True
    assert weighted["settlement_available_events"] == 0
    assert weighted["settlement_backtest_blocks"] == 0
    assert weighted["settlement_aligned_eligible"] is False
    assert weighted["settlement_shrunk_empirical_weight"] == 0.25
    assert weighted["settlement_shrunk_empirical_eligible"] is False
    assert weighted["minimum_settlement_backtest_blocks"] == 30
    assert weighted["shrunk_current_markets"] == 3
    assert weighted["shrunk_maximum_blocks"] == 0
    assert weighted["maximum_blocks"] == 0
    assert weighted["applied"] is False


async def test_weighted_training_respects_db_attested_first_seen_cutoff(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    snapshot_before = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=1)
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
        station = Station(
            icao="KAVL",
            name="Availability boundary",
            city="Test City",
            lat=Decimal("40.00"),
            lon=Decimal("-73.00"),
            tz="UTC",
        )
        session.add(station)
        await session.flush()

        for index in range(400):
            valid_date = snapshot_before.date() - timedelta(days=400 - index)
            actual = 18 + 4 * math.sin(index * 2 * math.pi / 180)
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(actual)),
                )
            )
            values = {
                "ecmwf": actual + 0.1 * math.sin(index),
                "gfs": actual + 1.5 * math.sin(index * 0.31),
                "icon": actual + 0.2 * math.cos(index * 0.73),
                "gem": actual + 1.2 * math.cos(index * 0.41),
            }
            for model, value in values.items():
                session.add(
                    HistoricalForecast(
                        station_id=station.id,
                        model=model,
                        valid_date=valid_date,
                        kind=WeatherKind.HIGH,
                        horizon_h=12,
                        temp_c=Decimal(str(value)),
                        source="test-db-attested-availability",
                        fetched_at=snapshot_before - timedelta(days=1),
                    )
                )

        event_id = "weighted-availability-event"
        markets = []
        for index, (low, high) in enumerate(((None, 16.0), (16.0, 20.0), (20.0, None))):
            market = Market(
                polymarket_id=f"weighted-availability-{index}",
                event_id=event_id,
                station_id=station.id,
                market_date=snapshot_before.date() + timedelta(days=1),
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal(str(low)) if low is not None else None,
                bucket_high_c=Decimal(str(high)) if high is not None else None,
                rules_raw="availability boundary test rules",
                resolves_at=snapshot_before + timedelta(days=1),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
            session.add(market)
            markets.append(market)
        await session.flush()
        for market in markets:
            session.add(
                ModelProb(
                    market_id=market.id,
                    ts=snapshot_before,
                    prob=Decimal("0.333333"),
                    sigma=Decimal("1.5"),
                    horizon_h=Decimal("12"),
                    components={"pipeline": {"version": "availability-before"}},
                )
            )
        for model, value in {
            "ecmwf": 18.1,
            "gfs": 19.2,
            "icon": 18.2,
            "gem": 17.4,
        }.items():
            session.add(
                Forecast(
                    station_id=station.id,
                    model=model,
                    run_time=snapshot_before - timedelta(hours=2),
                    valid_date=snapshot_before.date() + timedelta(days=1),
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(value)),
                    fetched_at=legacy_available_at,
                    available_at=legacy_available_at,
                )
            )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    # The labels and historical forecasts were physically inserted after this
    # model snapshot, even though their source/fetched timestamps are older.
    assert (
        await generate_weighted_ensemble(
            settings,
            db_sessions,
            now=snapshot_before + timedelta(microseconds=1),
        )
        == 0
    )
    async with db_sessions() as session:
        assert await session.scalar(select(func.count(AccuracyPrediction.id))) == 0
        actual_first_seen = await session.scalar(select(func.max(DailyActual.first_seen_at)))
        forecast_first_seen = await session.scalar(
            select(func.max(HistoricalForecast.first_seen_at))
        )
    assert actual_first_seen is not None
    assert forecast_first_seen is not None
    assert actual_first_seen > snapshot_before
    assert forecast_first_seen > snapshot_before

    snapshot_after = max(actual_first_seen, forecast_first_seen) + timedelta(microseconds=1)
    async with db_sessions.begin() as session:
        for market in markets:
            session.add(
                ModelProb(
                    market_id=market.id,
                    ts=snapshot_after,
                    prob=Decimal("0.333333"),
                    sigma=Decimal("1.5"),
                    horizon_h=Decimal("12"),
                    components={"pipeline": {"version": "availability-after"}},
                )
            )

    assert (
        await generate_weighted_ensemble(
            settings,
            db_sessions,
            now=snapshot_after + timedelta(microseconds=1),
        )
        == 6
    )
    async with db_sessions() as session:
        predictions = (await session.scalars(select(AccuracyPrediction))).all()
    assert len(predictions) == 6
    assert all(row.training_cutoff == snapshot_after for row in predictions)
    assert all(row.training_sample_n >= 180 for row in predictions)
