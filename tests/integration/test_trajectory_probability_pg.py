from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.open_meteo import ForecastTrajectoryValue, ForecastValue
from stratus.config import Settings
from stratus.db.models import (
    AccuracyPrediction,
    Calibration,
    DailyActual,
    ForecastTrajectory,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelDriftEvent,
    ModelProb,
    ModelSkillReport,
    Observation,
    Station,
    StationStage,
    WeatherKind,
)
from stratus.jobs.audit_accuracy import audit_accuracy
from stratus.jobs.recompute_probs import recompute_probs
from stratus.repositories.forecast_trajectories import upsert_forecast_trajectories
from stratus.repositories.forecasts import upsert_forecasts

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def _legacy_forecast_available_at(session: AsyncSession) -> datetime:
    """Return a DB-attested timestamp just before provenance enforcement.

    These tests exercise probability algorithms, not HTTP collection. Their
    synthetic rows belong explicitly to the supported pre-protocol domain.
    """

    value = await session.scalar(
        text(
            "SELECT opened_at - interval '1 microsecond' "
            "FROM forecast_provenance_runtime WHERE id = 1"
        )
    )
    if not isinstance(value, datetime):
        raise RuntimeError("forecast provenance boundary is missing")
    return value


async def test_trajectory_challenger_is_persisted_but_fail_closed(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    # Anchor the synthetic trajectory to the completed prior UTC day.  A
    # current-day fixture has fewer than the required three observed hours
    # when the suite runs shortly after midnight and becomes time-dependent.
    market_date = (now - timedelta(days=1)).date()
    observation_anchor = now.replace(hour=23, minute=0, second=0) - timedelta(days=1)
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
        station = Station(
            icao="EDDF",
            name="Frankfurt Airport",
            city="Frankfurt",
            lat=Decimal("50.0379"),
            lon=Decimal("8.5622"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        peer_stations = []
        for index in range(3):
            peer = Station(
                icao=f"EDG{index}",
                name=f"Peer Airport {index}",
                city=f"Peer {index}",
                lat=Decimal("50.0") + Decimal(index) / Decimal("10"),
                lon=Decimal("8.0"),
                tz="UTC",
                stage=StationStage.SHADOW,
            )
            session.add(peer)
            peer_stations.append(peer)
        await session.flush()
        for calibration_station in [station, *peer_stations]:
            for model in ("ecmwf", "gfs"):
                session.add(
                    Calibration(
                        station_id=calibration_station.id,
                        model=model,
                        horizon_bucket="6-24h",
                        kind=WeatherKind.HIGH,
                        bias_c=Decimal("0.2"),
                        sigma_c=Decimal("1.5"),
                        emos_a=Decimal("0.2"),
                        emos_b=Decimal("1.0"),
                        emos_c=Decimal("1.5"),
                        emos_d=Decimal("0.01"),
                        sample_n=120,
                        updated_at=now,
                    )
                )
        session.add(
            ModelDriftEvent(
                event_key="integration-regime-shift",
                registry_id=None,
                station_id=station.id,
                detected_at=now,
                window_days=30,
                metric="residual_bias_shift",
                baseline_value=Decimal("0.2"),
                recent_value=Decimal("1.2"),
                delta=Decimal("1.0"),
                severity="critical",
                details={
                    "model": "gfs",
                    "model_version": "operational-unversioned",
                    "horizon_bucket": "6-24h",
                    "kind": "high",
                    "boundary_kind": "rolling_change_point",
                    "boundary_date": market_date.isoformat(),
                    "adjustment_c": 1.0,
                    "ci_low_c": 0.5,
                    "ci_high_c": 1.4,
                    "baseline_n": 90,
                    "recent_n": 30,
                },
            )
        )
        market = Market(
            polymarket_id="trajectory-pg-market",
            event_id="trajectory-pg-event",
            station_id=station.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("23"),
            bucket_high_c=None,
            rules_raw="Frankfurt daily high.",
            resolves_at=now + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        companion_market = Market(
            polymarket_id="trajectory-pg-market-companion",
            event_id="trajectory-pg-event",
            station_id=station.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=None,
            bucket_high_c=Decimal("23"),
            rules_raw="Frankfurt daily high.",
            resolves_at=now + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add_all((market, companion_market))
        await session.flush()
        incomplete_markets = (
            ("lower", None, Decimal("21"), MarketStatus.ACTIVE, True),
            ("middle", Decimal("21"), Decimal("24"), MarketStatus.ACTIVE, True),
            ("upper", Decimal("24"), None, MarketStatus.BLOCKED, False),
        )
        for suffix, low, high, status, verified in incomplete_markets:
            session.add(
                Market(
                    polymarket_id=f"trajectory-incomplete-{suffix}",
                    event_id="trajectory-incomplete-event",
                    station_id=station.id,
                    market_date=market_date,
                    kind=WeatherKind.HIGH,
                    bucket_low_c=low,
                    bucket_high_c=high,
                    rules_raw="Incomplete Frankfurt event.",
                    resolves_at=now + timedelta(hours=12),
                    status=status,
                    station_verified=verified,
                )
            )
        await session.flush()
        forecasts = [
            ForecastValue(
                station_id=station.id,
                model=model,
                run_time=now - timedelta(hours=1),
                valid_date=market_date,
                kind=WeatherKind.HIGH,
                temp_c=Decimal(temp),
                fetched_at=legacy_available_at,
                available_at=legacy_available_at,
            )
            for model, temp in (("ecmwf", "25"), ("gfs", "26"))
        ]
        await upsert_forecasts(session, forecasts)
        trajectories = [
            ForecastTrajectoryValue(
                station_id=station.id,
                model=model,
                run_time=now - timedelta(hours=1),
                valid_date=market_date,
                points=tuple(
                    (
                        f"{market_date.isoformat()}T{hour:02d}:00",
                        Decimal("15") + Decimal(hour) / Decimal("2") + offset,
                    )
                    for hour in range(24)
                ),
                fetched_at=legacy_available_at,
                available_at=legacy_available_at,
            )
            for model, offset in (("ecmwf", Decimal("0")), ("gfs", Decimal("0.5")))
        ]
        await upsert_forecast_trajectories(session, trajectories)
        for hour_offset in range(4):
            observation_time = observation_anchor - timedelta(hours=hour_offset)
            session.add(
                Observation(
                    station_id=station.id,
                    obs_time=observation_time,
                    temp_c=(
                        Decimal("17.25")
                        + Decimal(observation_time.hour) / Decimal("2")
                    ),
                    raw_metar="integration-test",
                )
            )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    written, skipped = await recompute_probs(
        settings, db_sessions, seal_accuracy_evidence=True
    )

    async with db_sessions() as session:
        prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "trajectory_ar1"
            )
        )
        nowcast_prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "trajectory_nowcast"
            )
        )
        hierarchical_prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "hierarchical_emos"
            )
        )
        regime_prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "regime_emos"
            )
        )
        model_prob = await session.scalar(select(ModelProb))
        trajectory_count = int(
            await session.scalar(select(ForecastTrajectory.id).limit(1)) is not None
        )
        incomplete_evidence = (
            (
                await session.execute(
                    select(AccuracyPrediction)
                    .join(Market, AccuracyPrediction.market_id == Market.id)
                    .where(Market.event_id == "trajectory-incomplete-event")
                )
            )
            .scalars()
            .all()
        )

    assert written == 4
    assert skipped == 0
    assert prediction is not None
    assert prediction.point_in_time_valid is True
    assert prediction.evidence_eligible is True
    assert prediction.expert_version.endswith("-sealed-v2")
    assert prediction.baseline_model_prob_id is not None
    assert prediction.distribution["evidence_protocol"] == "sealed-v2"
    assert prediction.distribution["normalized_baseline_probability"] >= 0
    assert prediction.distribution["eligible"] is False
    assert nowcast_prediction is not None
    assert nowcast_prediction.distribution["eligible"] is False
    assert nowcast_prediction.distribution["matched_hours"] >= 3
    assert nowcast_prediction.distribution["recent_bias_c"] > 0
    assert hierarchical_prediction is not None
    assert hierarchical_prediction.distribution["eligible"] is False
    assert hierarchical_prediction.distribution["peer_stations"] == 3
    assert hierarchical_prediction.distribution["local_weight"] == pytest.approx(2 / 3)
    assert regime_prediction is not None
    assert regime_prediction.distribution["eligible"] is False
    assert regime_prediction.distribution["strongest_model"] == "gfs"
    assert regime_prediction.distribution["strongest_adjustment_c"] == 1.0
    assert model_prob is not None
    detail = model_prob.components["trajectory_ar1"]
    assert detail["eligible"] is False
    assert detail["applied"] is False
    nowcast_detail = model_prob.components["trajectory_nowcast"]
    assert nowcast_detail["eligible"] is False
    assert nowcast_detail["applied"] is False
    hierarchical_detail = model_prob.components["hierarchical_emos"]
    assert hierarchical_detail["applied"] is False
    assert hierarchical_detail["peer_stations"] == 3
    regime_detail = model_prob.components["regime_emos"]
    assert regime_detail["applied"] is False
    assert regime_detail["strongest_model"] == "gfs"
    assert trajectory_count == 1
    assert incomplete_evidence == []

    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/accuracy-overview")

    assert response.status_code == 200, response.text
    challenger = response.json()["trajectory_challenger"]
    assert challenger["stored_trajectories"] == 2
    assert challenger["applied"] is False
    assert challenger["maximum_blocks"] == 0
    nowcast = response.json()["nowcast_challenger"]
    assert nowcast["current_markets"] == 2
    assert nowcast["recent_bias_c"] > 0
    assert nowcast["applied"] is False
    hierarchical = response.json()["hierarchical_challenger"]
    assert hierarchical["current_markets"] == 2
    assert hierarchical["peer_stations"] == 3
    assert hierarchical["local_weight"] == pytest.approx(2 / 3)
    assert hierarchical["applied"] is False
    regime = response.json()["regime_challenger"]
    assert regime["active_shifts"] == 1
    assert regime["critical_shifts"] == 1
    assert regime["current_markets"] == 2
    assert regime["strongest_model"] == "gfs"
    assert regime["strongest_adjustment_c"] == 1.0
    assert regime["maximum_blocks"] == 0
    assert regime["applied"] is False


async def test_legacy_trajectory_predictions_are_excluded_from_formal_gate(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    async with db_sessions.begin() as session:
        station = Station(
            icao="EDDF",
            name="Frankfurt Airport",
            city="Frankfurt",
            lat=Decimal("50.0379"),
            lon=Decimal("8.5622"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        for day_offset in range(35):
            market_date = (now - timedelta(days=day_offset + 1)).date()
            outcome = day_offset % 2 == 0
            decision_at = now - timedelta(days=day_offset + 1, hours=12)
            baseline_probability = Decimal("0.60") if outcome else Decimal("0.40")
            trajectory_probability = Decimal("0.90") if outcome else Decimal("0.10")
            market = Market(
                polymarket_id=f"trajectory-resolved-{day_offset}",
                event_id=f"trajectory-event-{day_offset}",
                station_id=station.id,
                market_date=market_date,
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal("23"),
                bucket_high_c=Decimal("27"),
                rules_raw="Frankfurt daily high.",
                resolves_at=decision_at + timedelta(hours=12),
                status=MarketStatus.RESOLVED,
                station_verified=True,
                resolved_outcome=outcome,
            )
            session.add(market)
            await session.flush()
            model_prob = ModelProb(
                market_id=market.id,
                ts=decision_at,
                prob=baseline_probability,
                sigma=Decimal("1.5"),
                horizon_h=Decimal("12"),
                components={
                    "blend": {
                        "target_calibration_bucket": "6-24h",
                        "kind": "high",
                    },
                    "ecmwf_ens": {"probability": float(baseline_probability)},
                    "trajectory_ar1": {
                        "probability": float(trajectory_probability),
                        "eligible": False,
                    },
                },
            )
            session.add(model_prob)
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="regularized_stack",
                    expert_version="regularized-brier-v1",
                    probability=(Decimal("0.91") if outcome else Decimal("0.09")),
                    mean_c=None,
                    sigma_c=None,
                    training_sample_n=90,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": "6-24h",
                        "baseline_probability": float(baseline_probability),
                        "eligible": False,
                    },
                )
            )
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="trajectory_ar1",
                    expert_version="hourly-ar1-v1",
                    probability=trajectory_probability,
                    mean_c=Decimal("25"),
                    sigma_c=Decimal("1.5"),
                    training_sample_n=0,
                    point_in_time_valid=True,
                    distribution={"horizon_bucket": "6-24h", "eligible": False},
                )
            )
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="regime_emos",
                    expert_version="version-break-v1",
                    probability=(Decimal("0.89") if outcome else Decimal("0.11")),
                    mean_c=Decimal("25"),
                    sigma_c=Decimal("1.5"),
                    training_sample_n=120,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": "6-24h",
                        "strongest_adjustment_c": 1.0,
                        "eligible": False,
                    },
                )
            )
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="hierarchical_emos",
                    expert_version="partial-pooling-v1",
                    probability=(Decimal("0.88") if outcome else Decimal("0.12")),
                    mean_c=Decimal("25"),
                    sigma_c=Decimal("1.6"),
                    training_sample_n=600,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": "6-24h",
                        "local_weight": 0.67,
                        "peer_stations": 3,
                        "eligible": False,
                    },
                )
            )
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="trajectory_nowcast",
                    expert_version="remaining-day-bias-v1",
                    probability=(Decimal("0.92") if outcome else Decimal("0.08")),
                    mean_c=Decimal("25"),
                    sigma_c=Decimal("1.5"),
                    training_sample_n=4,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": "6-24h",
                        "recent_bias_c": 1.2,
                        "matched_hours": 4,
                        "eligible": False,
                    },
                )
            )
            session.add(
                AccuracyPrediction(
                    market_id=market.id,
                    ts=decision_at,
                    expert="conformal_gaussian",
                    expert_version="split-conformal-v1",
                    probability=(Decimal("0.85") if outcome else Decimal("0.15")),
                    mean_c=Decimal("25"),
                    sigma_c=Decimal("1.8"),
                    training_sample_n=120,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": "6-24h",
                        "scale": 1.2,
                        "eligible": False,
                    },
                )
            )
            for expert, expert_version in (
                ("qrf", "qrf-v1"),
                ("analog", "analog-distance-v1"),
                ("mixture", "residual-mixture-v1"),
            ):
                session.add(
                    AccuracyPrediction(
                        market_id=market.id,
                        ts=decision_at,
                        expert=expert,
                        expert_version=expert_version,
                        probability=(Decimal("0.87") if outcome else Decimal("0.13")),
                        mean_c=None,
                        sigma_c=None,
                        training_sample_n=120,
                        point_in_time_valid=True,
                        distribution={"horizon_bucket": "6-24h", "eligible": False},
                    )
                )

    settings = Settings(
        database_url=migrated_database_url,
        accuracy_trajectory_min_blocks=30,
        accuracy_stack_min_blocks=30,
        accuracy_nowcast_min_blocks=30,
        accuracy_conformal_min_blocks=30,
        accuracy_hierarchical_min_blocks=30,
        accuracy_regime_min_blocks=30,
        accuracy_nonlinear_min_blocks=30,
    )
    await audit_accuracy(settings, db_sessions)

    async with db_sessions() as session:
        reports = (await session.execute(select(ModelSkillReport))).scalars().all()

    legacy_experts = {
        "regularized_stack",
        "trajectory_ar1",
        "trajectory_nowcast",
        "conformal_gaussian",
        "hierarchical_emos",
        "regime_emos",
        "qrf",
        "analog",
        "mixture",
    }
    assert not [
        report for report in reports if report.details.get("expert") in legacy_experts
    ]


async def test_conformal_guardrail_is_fitted_but_shadow_only(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    market_date = now.date()
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
        station = Station(
            icao="EDDF",
            name="Frankfurt Airport",
            city="Frankfurt",
            lat=Decimal("50.0379"),
            lon=Decimal("8.5622"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="conformal-pg-market",
            event_id="conformal-pg-event",
            station_id=station.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("23"),
            bucket_high_c=None,
            rules_raw="Frankfurt daily high.",
            resolves_at=now + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        companion_market = Market(
            polymarket_id="conformal-pg-market-companion",
            event_id="conformal-pg-event",
            station_id=station.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=None,
            bucket_high_c=Decimal("23"),
            rules_raw="Frankfurt daily high.",
            resolves_at=now + timedelta(hours=12),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add_all((market, companion_market))
        await session.flush()
        await upsert_forecasts(
            session,
            [
                ForecastValue(
                    station_id=station.id,
                    model=model,
                    run_time=now - timedelta(hours=1),
                    valid_date=market_date,
                    kind=WeatherKind.HIGH,
                    temp_c=temp,
                    fetched_at=legacy_available_at,
                    available_at=legacy_available_at,
                )
                for model, temp in (
                    ("ecmwf", Decimal("25")),
                    ("gfs", Decimal("26")),
                )
            ],
        )
        for day_offset in range(120):
            valid_date = market_date - timedelta(days=day_offset + 1)
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal("24"),
                )
            )
            for model in ("ecmwf", "gfs"):
                session.add(
                    HistoricalForecast(
                        station_id=station.id,
                        model=model,
                        valid_date=valid_date,
                        kind=WeatherKind.HIGH,
                        horizon_h=12,
                        temp_c=Decimal("20"),
                        source="integration-test",
                    )
                )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    await recompute_probs(settings, db_sessions, seal_accuracy_evidence=True)

    async with db_sessions() as session:
        prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "conformal_gaussian"
            )
        )
        nonlinear_predictions = (
            (
                await session.execute(
                    select(AccuracyPrediction).where(
                        AccuracyPrediction.expert.in_(("qrf", "analog", "mixture"))
                    )
                )
            )
            .scalars()
            .all()
        )
        model_prob = await session.scalar(select(ModelProb))

    assert prediction is not None
    assert prediction.training_sample_n == 240
    assert prediction.distribution["scale"] > 1
    assert prediction.distribution["eligible"] is False
    assert model_prob is not None
    detail = model_prob.components["conformal_gaussian"]
    assert detail["applied"] is False
    assert detail["coverage_after"] >= detail["coverage_before"]
    assert {row.expert for row in nonlinear_predictions} == {"qrf", "analog", "mixture"}
    assert all(row.distribution["eligible"] is False for row in nonlinear_predictions)
    assert all(
        model_prob.components[expert]["applied"] is False
        for expert in ("qrf", "analog", "mixture")
    )

    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/accuracy-overview")

    assert response.status_code == 200, response.text
    stack = response.json()["stack_challenger"]
    assert stack["minimum_training_samples"] == 30
    assert stack["minimum_blocks"] == 60
    assert stack["applied"] is False
    guardrail = response.json()["conformal_guardrail"]
    assert guardrail["residual_samples"] == 240
    assert guardrail["scale"] > 1
    assert guardrail["applied"] is False
    nonlinear = response.json()["nonlinear_challengers"]
    assert nonlinear["minimum_blocks"] == 60
    assert {row["expert"] for row in nonlinear["experts"]} == {"qrf", "analog", "mixture"}
    assert all(row["current_markets"] == 2 for row in nonlinear["experts"])
    assert all(row["applied"] is False for row in nonlinear["experts"])


async def test_peer_stack_is_persisted_but_cannot_bypass_future_gate(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    market_date = now.date()
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
        target = Station(
            icao="TST0",
            name="Target Airport",
            city="Target",
            lat=Decimal("50"),
            lon=Decimal("8"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(target)
        await session.flush()
        for model in ("ecmwf", "gfs"):
            session.add(
                Calibration(
                    station_id=target.id,
                    model=model,
                    horizon_bucket="0-6h",
                    kind=WeatherKind.HIGH,
                    bias_c=Decimal("0"),
                    sigma_c=Decimal("1.5"),
                    emos_a=Decimal("0"),
                    emos_b=Decimal("1"),
                    emos_c=Decimal("1.5"),
                    emos_d=Decimal("0"),
                    sample_n=120,
                    updated_at=now,
                )
            )
        target_market = Market(
            polymarket_id="peer-stack-target",
            event_id="peer-stack-target-event",
            station_id=target.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=Decimal("24.5"),
            bucket_high_c=None,
            rules_raw="Target daily high.",
            resolves_at=now + timedelta(hours=3),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        target_companion = Market(
            polymarket_id="peer-stack-target-companion",
            event_id="peer-stack-target-event",
            station_id=target.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=None,
            bucket_high_c=Decimal("24.5"),
            rules_raw="Target daily high.",
            resolves_at=now + timedelta(hours=3),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add_all((target_market, target_companion))
        await session.flush()
        await upsert_forecasts(
            session,
            [
                ForecastValue(
                    station_id=target.id,
                    model=model,
                    run_time=now - timedelta(hours=1),
                    valid_date=market_date,
                    kind=WeatherKind.HIGH,
                    temp_c=temp_c,
                    fetched_at=legacy_available_at,
                    available_at=legacy_available_at,
                )
                for model, temp_c in (
                    ("ecmwf", Decimal("25")),
                    ("gfs", Decimal("25.5")),
                )
            ],
        )

        for peer_index in range(3):
            peer = Station(
                icao=f"P{peer_index:03d}",
                name=f"Peer Airport {peer_index}",
                city=f"Peer {peer_index}",
                lat=Decimal("51") + Decimal(peer_index) / Decimal("10"),
                lon=Decimal("9"),
                tz="UTC",
                stage=StationStage.PAPER,
            )
            session.add(peer)
            await session.flush()
            session.add(
                Market(
                    polymarket_id=f"peer-active-{peer_index}",
                    event_id=f"peer-active-event-{peer_index}",
                    station_id=peer.id,
                    market_date=market_date,
                    kind=WeatherKind.HIGH,
                    bucket_low_c=Decimal("24.5"),
                    bucket_high_c=Decimal("25.5"),
                    rules_raw="Peer active daily high.",
                    resolves_at=now + timedelta(hours=3),
                    status=MarketStatus.ACTIVE,
                    station_verified=True,
                )
            )
            for bucket_index in range(10):
                outcome = bucket_index == 5
                resolved_market = Market(
                    polymarket_id=f"peer-resolved-{peer_index}-{bucket_index}",
                    event_id=f"peer-resolved-event-{peer_index}",
                    station_id=peer.id,
                    market_date=market_date - timedelta(days=1),
                    kind=WeatherKind.HIGH,
                    bucket_low_c=Decimal(bucket_index),
                    bucket_high_c=Decimal(bucket_index + 1),
                    rules_raw="Peer resolved daily high.",
                    resolves_at=now - timedelta(hours=12),
                    status=MarketStatus.RESOLVED,
                    station_verified=True,
                    resolved_outcome=outcome,
                )
                session.add(resolved_market)
                await session.flush()
                probability = Decimal("0.8") if outcome else Decimal("0.2")
                session.add(
                    ModelProb(
                        market_id=resolved_market.id,
                        ts=now - timedelta(days=1),
                        prob=probability,
                        sigma=Decimal("1.5"),
                        horizon_h=Decimal("3"),
                        components={
                            "blend": {
                                "target_calibration_bucket": "0-6h",
                                "kind": "high",
                            },
                            "ecmwf": {"probability": float(probability)},
                            "gfs": {"probability": float(probability)},
                            "shrinkage": {
                                "p_market": 0.7 if outcome else 0.3,
                            },
                        },
                    )
                )

    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    written, skipped = await recompute_probs(
        settings, db_sessions, seal_accuracy_evidence=True
    )

    async with db_sessions() as session:
        prediction = await session.scalar(
            select(AccuracyPrediction).where(
                AccuracyPrediction.expert == "regularized_stack",
                AccuracyPrediction.market_id == target_market.id,
            )
        )
        target_probability = await session.scalar(
            select(ModelProb).where(ModelProb.market_id == target_market.id)
        )

    assert written == 2
    assert skipped == 3
    assert prediction is not None
    assert prediction.training_sample_n == 30
    assert prediction.distribution["fit_source"] == "peer_pool"
    assert prediction.distribution["local_sample_n"] == 0
    assert prediction.distribution["peer_sample_n"] == 30
    assert prediction.distribution["peer_stations"] == 3
    assert prediction.distribution["eligible"] is False
    assert target_probability is not None
    assert target_probability.components["stacking"]["applied"] is False
    assert target_probability.components["stacking"]["candidate_probability"] is not None
