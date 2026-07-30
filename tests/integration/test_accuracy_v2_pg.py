from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.open_meteo import ForecastValue
from stratus.config import Settings
from stratus.db.models import (
    DailyActual,
    Forecast,
    ForecastModelRegistry,
    ForecastRun,
    HealthAudit,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelDriftEvent,
    RuntimeControl,
    SettlementActual,
    Station,
    WeatherKind,
)
from stratus.jobs.audit_accuracy import _selected_forecast_rows, audit_accuracy
from stratus.jobs.audit_health import audit_health
from stratus.jobs.audit_storage_capacity import audit_storage_capacity
from stratus.repositories.accuracy import upsert_resolution_contract
from stratus.repositories.forecast_runs import upsert_forecast_run
from stratus.repositories.forecasts import upsert_forecasts
from stratus.services.model_catalog import descriptor_for
from stratus.services.resolution_contracts import parse_resolution_contract

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 12, 6, tzinfo=UTC)


async def test_accuracy_forecast_selector_ranks_in_database_with_dst_and_as_of_truth(
    db_sessions: SessionFactory,
) -> None:
    source_url = "https://api.open-meteo.com/v1/forecast"
    valid_date = date(2026, 3, 8)
    # New York enters daylight time on this date, so the following local
    # midnight is 2026-03-09 04:00 UTC.  The two short-horizon runs below are
    # equidistant from the 3h anchor; the lower Forecast.id must win.
    short_early = datetime(2026, 3, 9, 0, tzinfo=UTC)
    short_late = datetime(2026, 3, 9, 2, tzinfo=UTC)
    medium_anchor = datetime(2026, 3, 8, 16, tzinfo=UTC)
    async with db_sessions.begin() as session:
        station = Station(
            icao="KDST",
            name="DST selector fixture",
            city="New York",
            lat=Decimal("40.7128"),
            lon=Decimal("-74.0060"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        actual = DailyActual(
            station_id=station.id,
            date=valid_date,
            kind=WeatherKind.HIGH,
            temp_c=Decimal("15.0"),
        )
        session.add(actual)
        await session.flush()
        assert actual.first_seen_at is not None
        truth_first_seen = actual.first_seen_at

        values: list[ForecastValue] = []
        for available_at, temp_c in (
            (short_early, Decimal("14.0")),
            (short_late, Decimal("25.0")),
            (medium_anchor, Decimal("16.0")),
        ):
            init_time = available_at - timedelta(minutes=30)
            run_id = await upsert_forecast_run(
                session,
                descriptor=descriptor_for(
                    "gfs", init_time=init_time, source_url=source_url
                ),
                init_time=init_time,
                available_at=available_at,
                retrieved_at=available_at + timedelta(minutes=1),
                source_url=source_url,
                metadata={
                    "upstream_availability_verified": True,
                    "init_time_semantics": "upstream_run",
                },
            )
            values.append(
                ForecastValue(
                    station_id=station.id,
                    model="gfs",
                    run_time=init_time,
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=temp_c,
                    fetched_at=available_at + timedelta(minutes=1),
                    run_id=run_id,
                    available_at=available_at,
                )
            )
        await upsert_forecasts(session, values)

    async with db_sessions() as session:
        before_truth = await _selected_forecast_rows(
            session,
            as_of=truth_first_seen - timedelta(microseconds=1),
            cutoff=date(2026, 1, 1),
        )
        after_truth = await _selected_forecast_rows(
            session,
            as_of=truth_first_seen + timedelta(microseconds=1),
            cutoff=date(2026, 1, 1),
        )

    assert before_truth == []
    assert len(after_truth) == 2
    selected = {row.horizon_bucket: row for row in after_truth}
    assert selected["0-6h"].temp_c == Decimal("14.0")
    assert selected["6-24h"].temp_c == Decimal("16.0")
    assert all(row.availability_verified for row in after_truth)


async def test_point_in_time_run_and_exact_settlement_roundtrip(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> None:
    rules = (
        "Resolve from https://www.wunderground.com/history/daily/test/KAAA using "
        "whole degrees Celsius after the first data point for the following date, "
        "12 Jul '26."
    )
    async with db_sessions.begin() as session:
        station = Station(
            icao="KAAA",
            name="Official accuracy fixture",
            city="Fixture",
            lat=Decimal("40.0000"),
            lon=Decimal("-73.0000"),
            tz="UTC",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-hko-accuracy",
            event_id="event-hko-accuracy",
            station_id=station.id,
            market_date=date(2026, 7, 12),
            kind=WeatherKind.HIGH,
            rules_raw=rules,
            resolution_source="https://www.wunderground.com/history/daily/test/KAAA",
            resolves_at=NOW + timedelta(hours=18),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        contract = await upsert_resolution_contract(
            session,
            parse_resolution_contract(
                event_id=market.event_id,
                rules_raw=rules,
                resolution_source=market.resolution_source,
                station_code=station.icao,
                local_timezone=station.tz,
                statistic=market.kind,
            ),
            expected_market_date=market.market_date,
        )
        contract_id = contract.id
        rules_hash = contract.rules_hash
        run_id = await upsert_forecast_run(
            session,
            descriptor=descriptor_for(
                "aifs-single",
                init_time=NOW,
                source_url="https://api.open-meteo.com/v1/forecast",
            ),
            init_time=NOW,
            available_at=NOW + timedelta(minutes=7),
            retrieved_at=NOW + timedelta(minutes=8),
            source_url="https://api.open-meteo.com/v1/forecast",
            status="snapshot",
            metadata={"upstream_init_verified": False},
        )
        await upsert_forecasts(
            session,
            [
                ForecastValue(
                    station_id=station.id,
                    model="aifs-single",
                    run_time=NOW,
                    valid_date=market.market_date,
                    kind=market.kind,
                    temp_c=Decimal("32.4"),
                    fetched_at=NOW + timedelta(minutes=8),
                    run_id=run_id,
                    available_at=NOW + timedelta(minutes=7),
                )
            ],
        )

    target_date = NOW.date()
    witness = datetime.combine(
        target_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
    )
    raw_body = json.dumps(
        {
            "validTimeLocal": [f"{target_date.isoformat()}T00:00:00+00:00"],
            "temperatureMax": [33],
            "temperatureMin": [20],
            "finalizationWitnessAt": witness.isoformat(),
            "publishedAt": (witness + timedelta(minutes=1)).isoformat(),
        },
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(raw_body).hexdigest()
    relative_path = f"imports/wunderground/{contract_id}/{digest}.bin"
    artifact_path = tmp_path.joinpath(*relative_path.split("/"))
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(raw_body)

    attested_at = datetime.now(UTC)
    license_draft = {
        "contract_id": contract_id,
        "rules_hash": rules_hash,
        "license_id": "accuracy-fixture-v1",
        "grantee": "STRATUS accuracy integration fixture",
        "endpoint_url": "https://licensed.example/v1/daily/KAAA?units=m",
        "adapter_name": "wunderground-licensed-json-field-map",
        "adapter_version": "1",
        "parser_config": {
            "schema_id": "provider-daily-v1",
            "series_mode": "parallel_arrays",
            "date_path": ["validTimeLocal"],
            "high_path": ["temperatureMax"],
            "low_path": ["temperatureMin"],
            "finalization_witness_at_path": ["finalizationWitnessAt"],
            "published_at_path": ["publishedAt"],
            "station_code_literal": "KAAA",
            "source_unit_literal": "C",
            "decimal_places_literal": 0,
            "finalization_rule_literal": "next_day_first_point",
            "series_anchor_local_time": "00:00:00",
            "source_window_start_local": "00:00:00",
            "source_window_end_local": "00:00:00",
            "source_window_end_day_offset": 1,
        },
        "terms_url": "https://licensed.example/terms/v1",
        "terms_sha256": "b" * 64,
        "valid_from": (attested_at - timedelta(days=1)).isoformat(),
        "expires_at": (attested_at + timedelta(days=30)).isoformat(),
        "storage_permitted": True,
        "commercial_use_permitted": True,
        "max_response_age_seconds": 3600,
    }
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key="secret-key",
        official_resolution_artifact_root=tmp_path,
    )
    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        preview = await client.post(
            "/api/v1/official-resolution-sources/license-policy-hash",
            headers={"X-API-Key": "secret-key"},
            json=license_draft,
        )
        assert preview.status_code == 200, preview.text
        attestation = await client.post(
            "/api/v1/official-resolution-sources/license-attestations",
            headers={"X-API-Key": "secret-key"},
            json={
                **license_draft,
                "approved_policy_sha256": preview.json()["policy_sha256"],
                "reviewer": "accuracy integration reviewer",
                "reviewed_at": attested_at.isoformat(),
                "rationale": "approved fixture retention and exact contract equivalence",
            },
        )
        assert attestation.status_code == 200, attestation.text
        response = await client.post(
            "/api/v1/official-resolution-sources/import",
            headers={"X-API-Key": "secret-key"},
            json={
                "contract_id": contract_id,
                "rules_hash": rules_hash,
                "relative_artifact_path": relative_path,
                "actor": "integration-test",
                "reason": "seal exact official accuracy fixture",
            },
        )
        report_count, drift_count = await audit_accuracy(settings, db_sessions)
        overview = await client.get(
            "/api/v1/accuracy-overview", headers={"X-API-Key": "secret-key"}
        )

    assert response.status_code == 200, response.text
    assert report_count >= 1
    assert drift_count == 0
    assert response.json()["value_c"] == pytest.approx(33.0)
    assert overview.status_code == 200
    assert overview.json()["resolution_contracts"] == 1
    assert overview.json()["exact_settlement_actuals"] == 1
    assert any(item["model"] == "aifs-single" for item in overview.json()["models"])

    async with db_sessions() as session:
        forecast = await session.scalar(select(Forecast))
        run = await session.scalar(select(ForecastRun))
        registry = await session.scalar(select(ForecastModelRegistry))
        actual = await session.scalar(select(SettlementActual))
    assert forecast is not None and forecast.run_id == run_id
    assert run is not None and run.metadata_["upstream_init_verified"] is False
    assert registry is not None and registry.shadow_only is True
    assert actual is not None and actual.kind is WeatherKind.HIGH


async def test_accuracy_audit_detects_versionless_residual_regime_shift(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    today = datetime.now(UTC).date()
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
        for day_offset in range(1, 121):
            valid_date = today - timedelta(days=day_offset)
            actual = Decimal("20.0")
            residual = Decimal("-1.0") if day_offset <= 30 else Decimal("0.5")
            session.add(
                DailyActual(
                    station_id=station.id,
                    date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=actual,
                )
            )
            session.add(
                HistoricalForecast(
                    station_id=station.id,
                    model="gfs",
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    horizon_h=12,
                    temp_c=actual - residual,
                    source="integration-test",
                )
            )

    settings = Settings(
        database_url=migrated_database_url,
        accuracy_regime_min_recent=20,
        accuracy_regime_min_baseline=40,
        accuracy_regime_bootstrap_samples=100,
    )
    _reports, drift_count = await audit_accuracy(settings, db_sessions)

    async with db_sessions() as session:
        drift = await session.scalar(
            select(ModelDriftEvent).where(ModelDriftEvent.metric == "residual_bias_shift")
        )

    assert drift_count == 1
    assert drift is not None
    assert float(drift.delta) == pytest.approx(-1.5)
    assert drift.details["model"] == "gfs"
    assert drift.details["boundary_kind"] == "rolling_change_point"
    assert drift.details["adjustment_c"] == pytest.approx(-1.5)
    assert drift.details["ci_high_c"] < 0


async def test_shadow_residual_drift_warns_without_engaging_kill_switch(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
    canonical_probability_release_inputs: None,
) -> None:
    now = datetime.now(UTC)
    settings = Settings(
        database_url=migrated_database_url,
        clob_ledger_capacity_path=str(tmp_path),
        clob_ledger_capacity_min_free_bytes=500_000_000,
        release_image_digest="b" * 64,
    )
    assessment = await audit_storage_capacity(settings, db_sessions)
    assert assessment.severity != "blocking"
    async with db_sessions.begin() as session:
        session.add(
            ModelDriftEvent(
                event_key="shadow-drift-health-test",
                registry_id=None,
                station_id=None,
                detected_at=now,
                window_days=30,
                metric="residual_bias_shift",
                baseline_value=Decimal("0"),
                recent_value=Decimal("1.5"),
                delta=Decimal("1.5"),
                severity="critical",
                details={"expert": "regime_emos"},
            )
        )

    status, _issue_count = await audit_health(settings, db_sessions)

    async with db_sessions() as session:
        control = await session.get(RuntimeControl, 1)
        audit = await session.scalar(select(HealthAudit).order_by(HealthAudit.ts.desc()).limit(1))

    assert status == "critical"
    assert control is not None and control.kill_switch is False
    assert audit is not None
    assert any(issue["code"] == "accuracy_shadow_drift" for issue in audit.issues)
    assert any(
        issue["code"] == "probability_release_epoch_missing"
        and issue["severity"] == "critical"
        for issue in audit.issues
    )
