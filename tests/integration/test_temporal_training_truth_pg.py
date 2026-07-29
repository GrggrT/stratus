from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.open_meteo import ForecastValue
from stratus.config import Settings
from stratus.db.models import (
    Calibration,
    DailyActual,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelProb,
    ResolutionContract,
    ResolutionReconciliationClassification,
    SettlementActual,
    Station,
    StationStage,
    WeatherKind,
)
from stratus.jobs.generate_weighted_ensemble import (
    _historical_training_statement as weighted_training_statement,
)
from stratus.jobs.recalibrate_emos import (
    _historical_training_statement as recalibration_training_statement,
)
from stratus.jobs.recalibrate_emos import recalibrate_emos
from stratus.jobs.recompute_probs import recompute_probs
from stratus.repositories.forecasts import upsert_forecasts
from stratus.repositories.gamma_outcomes import (
    GAMMA_OUTCOME_PROTOCOL,
    GammaOutcomeEventValue,
    gamma_outcome_map,
    upsert_gamma_outcome_event,
)
from stratus.repositories.resolution_reconciliation import (
    ResolutionReconciliationValue,
    upsert_resolution_reconciliation,
)

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


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


async def _seed_clear_mismatch_reconciliation(
    session: AsyncSession,
    *,
    station: Station,
    market_date: date,
    metar_temp_c: Decimal,
) -> None:
    suffix = uuid4().hex
    event_id = f"training-truth-conflict-{suffix}"
    resolved_at = datetime.now(UTC) - timedelta(minutes=5)
    markets = [
        Market(
            polymarket_id=f"training-truth-conflict-{suffix}-{index}",
            event_id=event_id,
            condition_id=f"condition-{suffix}-{index}",
            yes_token_id=f"yes-{suffix}-{index}",
            no_token_id=f"no-{suffix}-{index}",
            station_id=station.id,
            market_date=market_date,
            kind=WeatherKind.HIGH,
            bucket_low_c=None if index == 0 else Decimal("21"),
            bucket_high_c=Decimal("21") if index == 0 else None,
            rules_raw=f"training truth conflict rules {suffix} {index}",
            resolves_at=resolved_at - timedelta(hours=1),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        for index in range(2)
    ]
    session.add_all(markets)
    await session.flush()

    closed_at = resolved_at.isoformat().replace("+00:00", "Z")
    vector = []
    for index, market in enumerate(markets):
        outcome_yes = index == 0
        vector.append(
            {
                "market_id": market.id,
                "polymarket_id": market.polymarket_id,
                "condition_id": market.condition_id,
                "question_id": f"question-{suffix}-{index}",
                "yes_token_id": market.yes_token_id,
                "no_token_id": market.no_token_id,
                "outcome_yes": outcome_yes,
                "yes_price": "1" if outcome_yes else "0",
                "no_price": "0" if outcome_yes else "1",
                "closed_at": closed_at,
                "uma_resolution_status": "resolved",
                "resolved_by": "0xtraining-truth-test",
                "rules_hash": hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest(),
            }
        )
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
    payload = {
        "id": event_id,
        "closedTime": closed_at,
        "updatedAt": closed_at,
        "markets": [
            {
                "id": market.polymarket_id,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "closedTime": closed_at,
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["1", "0"]' if index == 0 else '["0", "1"]',
                "clobTokenIds": json.dumps([market.yes_token_id, market.no_token_id]),
                "conditionId": market.condition_id,
                "questionID": f"question-{suffix}-{index}",
                "resolvedBy": "0xtraining-truth-test",
            }
            for index, market in enumerate(markets)
        ],
    }
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    event = await upsert_gamma_outcome_event(
        session,
        GammaOutcomeEventValue(
            event_id=event_id,
            evidence_protocol=GAMMA_OUTCOME_PROTOCOL,
            source_url=f"https://example.test/gamma/{event_id}",
            source_http_date=resolved_at,
            source_last_modified=resolved_at,
            source_etag='"training-truth-conflict"',
            source_response_body=body,
            source_response_sha256=hashlib.sha256(body).hexdigest(),
            source_response_bytes=len(body),
            source_event_updated_at=resolved_at,
            first_outcome_at=resolved_at,
            fully_resolved_at=resolved_at,
            market_count=len(markets),
            market_universe_hash=_canonical_hash(universe),
            rules_manifest_hash=_canonical_hash(rules_manifest),
            outcome_vector_hash=_canonical_hash(vector),
            outcome_vector=vector,
        ),
    )
    outcomes = gamma_outcome_map(event)
    for market in markets:
        market.status = MarketStatus.RESOLVED
        market.resolved_outcome = outcomes[market.id]
        market.resolution_event_id = event.id
    await session.flush()

    for market in markets:
        metar_implied_yes = (
            metar_temp_c < Decimal("21")
            if market.bucket_high_c is not None
            else metar_temp_c >= Decimal("21")
        )
        await upsert_resolution_reconciliation(
            session,
            ResolutionReconciliationValue(
                market_id=market.id,
                event_id=event_id,
                station_id=station.id,
                market_date=market_date,
                kind=WeatherKind.HIGH,
                resolution_event_id=event.id,
                settlement_actual_id=None,
                classification=ResolutionReconciliationClassification.CLEAR_MISMATCH,
                resolved_yes=outcomes[market.id],
                metar_temp_c=metar_temp_c,
                metar_implied_yes=metar_implied_yes,
                exact_temp_c=None,
                exact_implied_yes=None,
                boundary_margin_c=abs(metar_temp_c - Decimal("21")),
                exact_truth_gap_c=None,
                rules_hash=hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest(),
                resolution_vector_hash=event.outcome_vector_hash,
                metar_value_hash="d" * 64,
                exact_value_hash=None,
                exact_source_hash=None,
                checked_at=datetime.now(UTC),
                provenance={"fixture": "training-truth-conflict"},
            ),
        )


async def test_training_consumers_exclude_current_clear_mismatch_fallback(
    db_sessions: SessionFactory,
) -> None:
    seeded_at = datetime.now(UTC) - timedelta(days=1)
    conflict_date = seeded_at.date() - timedelta(days=2)
    clean_date = conflict_date - timedelta(days=1)
    async with db_sessions.begin() as session:
        station = Station(
            icao="KTR3",
            name="Training truth quarantine station",
            city="Temporal",
            lat=Decimal("42.0"),
            lon=Decimal("-71.0"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        for actual_date, actual_temp in (
            (conflict_date, Decimal("25")),
            (clean_date, Decimal("18")),
        ):
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=actual_date,
                    kind=WeatherKind.HIGH,
                    temp_c=actual_temp,
                )
            )
            for model, offset in (("ecmwf", Decimal("0.2")), ("gfs", Decimal("-0.3"))):
                session.add(
                    HistoricalForecast(
                        station_id=station.id,
                        model=model,
                        valid_date=actual_date,
                        kind=WeatherKind.HIGH,
                        horizon_h=12,
                        temp_c=actual_temp + offset,
                        source="truth-quarantine-test",
                        fetched_at=seeded_at,
                    )
                )
        await session.flush()
        await _seed_clear_mismatch_reconciliation(
            session,
            station=station,
            market_date=conflict_date,
            metar_temp_c=Decimal("25"),
        )
        truth_cutoff = await session.scalar(select(func.clock_timestamp()))
        assert truth_cutoff is not None

    statements = (
        recalibration_training_statement(
            earliest_date=clean_date,
            truth_cutoff=truth_cutoff,
        ),
        weighted_training_statement(
            earliest_date=clean_date,
            truth_cutoff=truth_cutoff,
        ),
    )
    async with db_sessions() as session:
        selected = [(await session.execute(statement)).all() for statement in statements]

    for statement, rows in zip(statements, selected, strict=True):
        assert {(forecast.valid_date, forecast.model) for forecast, _ in rows} == {
            (clean_date, "ecmwf"),
            (clean_date, "gfs"),
        }
        sql = str(statement).lower()
        assert "coalesce(canonical_settlement_actuals.value_c, daily_actuals.temp_c)" in sql
        assert "group by eligible_settlement_actuals.station_id" in sql
        assert "having min(eligible_settlement_actuals.value_c) = max(" in sql
        assert "not (exists (select settlement_actuals.id" in sql
        assert "settlement_actuals.is_final is true" in sql
        assert "settlement_actuals.retrieved_at" in sql
        assert "official_resolution_exact_actuals.extracted_at" in sql


async def _seed_legacy_exact_candidates(
    session: AsyncSession,
    *,
    station: Station,
    actual_date: date,
    values: tuple[Decimal, ...],
    retrieved_at: datetime,
) -> None:
    """Model grandfathered pre-0039 exact rows for canonical-query regressions."""

    suffix = uuid4().hex
    contracts = []
    for index, _value in enumerate(values):
        date_token = (
            f"{actual_date.day} {actual_date.strftime('%b')} "
            f"'{actual_date.strftime('%y')}"
        )
        rules_raw = (
            f"Legacy exact highest temperature on {date_token}; "
            f"training truth fixture {suffix} {index}."
        )
        contract = ResolutionContract(
            event_id=f"legacy-exact-{suffix}-{index}",
            rules_hash=hashlib.sha256(rules_raw.encode("utf-8")).hexdigest(),
            source_family="legacy_test",
            source_url=f"https://example.test/legacy-exact/{suffix}/{index}",
            station_code=station.icao,
            local_timezone=station.tz,
            statistic="high",
            source_unit="C",
            decimal_places=1,
            finalization_rule="legacy_import",
            license_status="legacy",
            storage_permitted=False,
            rules_raw=rules_raw,
        )
        session.add(contract)
        contracts.append(contract)
    await session.flush()

    # The production schema deliberately retains nullable, pre-0039 projections.
    # This narrow trigger suspension constructs that migrated legacy state; the
    # trigger is restored before the training statements execute.
    await session.execute(
        text(
            "ALTER TABLE settlement_actuals DISABLE TRIGGER "
            "trg_settlement_actuals_require_official_exact"
        )
    )
    try:
        session.add_all(
            [
                SettlementActual(
                    contract_id=contract.id,
                    official_exact_actual_id=None,
                    station_id=station.id,
                    date=actual_date,
                    kind=WeatherKind.HIGH,
                    source_value=value,
                    source_unit="C",
                    value_c=value,
                    is_final=True,
                    source_published_at=retrieved_at - timedelta(minutes=1),
                    retrieved_at=retrieved_at,
                    source_response_hash=None,
                    provenance={"fixture": "pre-0039-legacy-exact"},
                )
                for contract, value in zip(contracts, values, strict=True)
            ]
        )
        await session.flush()
    finally:
        await session.execute(
            text(
                "ALTER TABLE settlement_actuals ENABLE TRIGGER "
                "trg_settlement_actuals_require_official_exact"
            )
        )


async def test_training_consumers_collapse_agreement_and_fail_closed_on_exact_conflict(
    db_sessions: SessionFactory,
) -> None:
    seeded_at = datetime.now(UTC) - timedelta(days=1)
    duplicate_date = seeded_at.date() - timedelta(days=3)
    conflict_date = duplicate_date + timedelta(days=1)
    fallback_date = duplicate_date + timedelta(days=2)
    async with db_sessions.begin() as session:
        station = Station(
            icao="KTR4",
            name="Canonical exact training station",
            city="Temporal",
            lat=Decimal("43.0"),
            lon=Decimal("-70.0"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        for actual_date, daily_value in (
            (duplicate_date, Decimal("11")),
            (conflict_date, Decimal("17")),
            (fallback_date, Decimal("19")),
        ):
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=actual_date,
                    kind=WeatherKind.HIGH,
                    temp_c=daily_value,
                )
            )
            session.add(
                HistoricalForecast(
                    station_id=station.id,
                    model="ecmwf",
                    valid_date=actual_date,
                    kind=WeatherKind.HIGH,
                    horizon_h=12,
                    temp_c=Decimal("20"),
                    source="canonical-exact-test",
                    fetched_at=seeded_at,
                )
            )
        await session.flush()
        await _seed_legacy_exact_candidates(
            session,
            station=station,
            actual_date=duplicate_date,
            values=(Decimal("21"), Decimal("21")),
            retrieved_at=seeded_at,
        )
        await _seed_legacy_exact_candidates(
            session,
            station=station,
            actual_date=conflict_date,
            values=(Decimal("22"), Decimal("23")),
            retrieved_at=seeded_at,
        )
        truth_cutoff = await session.scalar(select(func.clock_timestamp()))
        assert truth_cutoff is not None

    statements = (
        recalibration_training_statement(
            earliest_date=duplicate_date,
            truth_cutoff=truth_cutoff,
        ),
        weighted_training_statement(
            earliest_date=duplicate_date,
            truth_cutoff=truth_cutoff,
        ),
    )
    async with db_sessions() as session:
        selected = [(await session.execute(statement)).all() for statement in statements]

    for rows in selected:
        assert len(rows) == 2
        by_date = {forecast.valid_date: Decimal(actual) for forecast, actual in rows}
        assert by_date == {
            duplicate_date: Decimal("21.0000"),
            fallback_date: Decimal("19.0000"),
        }


async def test_recalibration_excludes_then_includes_db_attested_training_rows(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    seeded_at = datetime.now(UTC) - timedelta(days=1)
    async with db_sessions.begin() as session:
        station = Station(
            icao="KTR1",
            name="Temporal truth calibration station",
            city="Temporal",
            lat=Decimal("40.0"),
            lon=Decimal("-73.0"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()

        for index in range(20):
            valid_date = seeded_at.date() - timedelta(days=20 - index)
            actual = 17.0 + math.sin(index / 3)
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(actual)),
                )
            )
            for horizon_h, offset in ((3, 0.3), (12, -0.5)):
                forecast = actual + offset + 0.2 * math.cos(index / 2)
                session.add(
                    HistoricalForecast(
                        station_id=station.id,
                        model="ecmwf",
                        valid_date=valid_date,
                        kind=WeatherKind.HIGH,
                        horizon_h=horizon_h,
                        temp_c=Decimal(str(forecast)),
                        source="temporal-truth-test",
                        fetched_at=seeded_at,
                    )
                )
        await session.flush()
        earliest_actual_seen = await session.scalar(select(func.min(DailyActual.first_seen_at)))
        earliest_forecast_seen = await session.scalar(
            select(func.min(HistoricalForecast.first_seen_at))
        )
        latest_actual_seen = await session.scalar(select(func.max(DailyActual.first_seen_at)))
        latest_forecast_seen = await session.scalar(
            select(func.max(HistoricalForecast.first_seen_at))
        )

    assert earliest_actual_seen is not None
    assert earliest_forecast_seen is not None
    assert latest_actual_seen is not None
    assert latest_forecast_seen is not None
    before = min(earliest_actual_seen, earliest_forecast_seen) - timedelta(microseconds=1)
    after = max(latest_actual_seen, latest_forecast_seen) + timedelta(microseconds=1)
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key=None,
        calibration_min_samples=20,
    )

    assert await recalibrate_emos(settings, db_sessions, now=before) == 0
    assert await recalibrate_emos(settings, db_sessions, now=after) == 2

    async with db_sessions() as session:
        calibrations = (await session.execute(select(Calibration))).scalars().all()
    assert {row.horizon_bucket for row in calibrations} == {"0-6h", "6-24h"}
    assert all(row.sample_n == 20 for row in calibrations)


async def test_recompute_excludes_then_includes_db_attested_training_rows(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    seeded_at = datetime.now(UTC) - timedelta(days=1)
    market_date = seeded_at.date() + timedelta(days=2)
    async with db_sessions.begin() as session:
        legacy_available_at = await _legacy_forecast_available_at(session)
        station = Station(
            icao="KTR2",
            name="Temporal truth probability station",
            city="Temporal",
            lat=Decimal("41.0"),
            lon=Decimal("-72.0"),
            tz="UTC",
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()

        for model in ("ecmwf", "gfs"):
            session.add(
                Calibration(
                    station_id=station.id,
                    model=model,
                    horizon_bucket="6-24h",
                    kind=WeatherKind.HIGH,
                    bias_c=Decimal("0"),
                    sigma_c=Decimal("1.5"),
                    emos_a=Decimal("0"),
                    emos_b=Decimal("1"),
                    emos_c=Decimal("1.5"),
                    emos_d=Decimal("0"),
                    sample_n=120,
                    updated_at=seeded_at,
                )
            )

        markets = (
            Market(
                polymarket_id="temporal-truth-low",
                event_id="temporal-truth-event",
                station_id=station.id,
                market_date=market_date,
                kind=WeatherKind.HIGH,
                bucket_low_c=None,
                bucket_high_c=Decimal("23"),
                rules_raw="Temporal truth lower bucket.",
                resolves_at=seeded_at + timedelta(days=1, hours=12),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            ),
            Market(
                polymarket_id="temporal-truth-high",
                event_id="temporal-truth-event",
                station_id=station.id,
                market_date=market_date,
                kind=WeatherKind.HIGH,
                bucket_low_c=Decimal("23"),
                bucket_high_c=None,
                rules_raw="Temporal truth upper bucket.",
                resolves_at=seeded_at + timedelta(days=1, hours=12),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            ),
        )
        session.add_all(markets)
        await session.flush()

        await upsert_forecasts(
            session,
            [
                ForecastValue(
                    station_id=station.id,
                    model=model,
                    run_time=seeded_at,
                    valid_date=market_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(temp),
                    fetched_at=legacy_available_at,
                    available_at=legacy_available_at,
                )
                for model, temp in (("ecmwf", "24"), ("gfs", "23"))
            ],
        )

        for index in range(12):
            valid_date = seeded_at.date() - timedelta(days=12 - index)
            residual = 0.4 * math.sin(index / 2) + index / 100
            actual = 18.0 + math.cos(index / 3)
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal(str(actual)),
                )
            )
            session.add(
                HistoricalForecast(
                    station_id=station.id,
                    model="ecmwf",
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    horizon_h=12,
                    temp_c=Decimal(str(actual - residual)),
                    source="temporal-truth-test",
                    fetched_at=seeded_at,
                )
            )
        await session.flush()
        earliest_actual_seen = await session.scalar(select(func.min(DailyActual.first_seen_at)))
        earliest_forecast_seen = await session.scalar(
            select(func.min(HistoricalForecast.first_seen_at))
        )
        latest_actual_seen = await session.scalar(select(func.max(DailyActual.first_seen_at)))
        latest_forecast_seen = await session.scalar(
            select(func.max(HistoricalForecast.first_seen_at))
        )

    assert earliest_actual_seen is not None
    assert earliest_forecast_seen is not None
    assert latest_actual_seen is not None
    assert latest_forecast_seen is not None
    before = min(earliest_actual_seen, earliest_forecast_seen) - timedelta(microseconds=1)
    after = max(latest_actual_seen, latest_forecast_seen) + timedelta(microseconds=1)
    settings = Settings(database_url=migrated_database_url, operator_api_key=None)

    before_written, before_skipped = await recompute_probs(
        settings,
        db_sessions,
        now=before,
    )
    after_written, after_skipped = await recompute_probs(
        settings,
        db_sessions,
        now=after,
    )
    assert (before_written, before_skipped) == (2, 0)
    assert (after_written, after_skipped) == (2, 0)

    async with db_sessions() as session:
        before_prob = await session.scalar(select(ModelProb).where(ModelProb.ts == before))
        after_prob = await session.scalar(select(ModelProb).where(ModelProb.ts == after))
    assert before_prob is not None
    assert after_prob is not None
    assert before_prob.components["blend"]["ar_emos"]["ecmwf_ens"]["applied"] is False
    assert after_prob.components["blend"]["ar_emos"]["ecmwf_ens"]["applied"] is True
