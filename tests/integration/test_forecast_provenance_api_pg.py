from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.collectors.open_meteo import (
    ForecastBundle,
    ForecastTrajectoryValue,
    ForecastValue,
    SourceResponseEvidence,
)
from stratus.config import Settings
from stratus.db.models import Station, WeatherKind
from stratus.services.forecast_collection import persist_forecast_bundle

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def test_forecast_provenance_endpoint_reports_zero_backfill_then_live_ingest(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    settings = Settings(database_url=migrated_database_url, operator_api_key=None)
    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        empty_response = await client.get("/api/v1/forecast-provenance")

    assert empty_response.status_code == 200, empty_response.text
    empty = empty_response.json()
    assert empty["state"] == "not_collecting"
    assert empty["ingests"] == 0
    assert empty["raw_bytes"] == 0
    assert empty["materialized_forecasts"] == 0

    run_time = datetime.now(UTC) - timedelta(hours=1)
    fetched_at = datetime.now(UTC)
    response_body = b'{"hourly":{"time":["2026-07-16T00:00"],"temperature_2m":[12.4]}}'
    evidence = SourceResponseEvidence(
        request_started_at=fetched_at - timedelta(milliseconds=200),
        response_received_at=fetched_at - timedelta(milliseconds=50),
        source_url=(
            "https://api.open-meteo.test/v1/forecast?latitude=47.45&longitude=-122.31"
        ),
        http_date=fetched_at.replace(microsecond=0),
        etag='"forecast-v1"',
        response_body=response_body,
    )
    async with db_sessions.begin() as session:
        station = Station(
            icao="KSEA",
            name="Seattle Tacoma International Airport",
            city="Seattle",
            lat=Decimal("47.4502"),
            lon=Decimal("-122.3088"),
            tz="America/Los_Angeles",
        )
        session.add(station)
        await session.flush()
        persisted = await persist_forecast_bundle(
            session,
            bundle=ForecastBundle(
                forecasts=(
                    ForecastValue(
                        station_id=station.id,
                        model="gfs",
                        run_time=run_time,
                        valid_date=date.today() + timedelta(days=1),
                        kind=WeatherKind.HIGH,
                        temp_c=Decimal("21.40"),
                        fetched_at=fetched_at,
                    ),
                ),
                trajectories=(
                    ForecastTrajectoryValue(
                        station_id=station.id,
                        model="gfs",
                        run_time=run_time,
                        valid_date=date.today() + timedelta(days=1),
                        points=(("2026-07-16T00:00", Decimal("12.40")),),
                        fetched_at=fetched_at,
                    ),
                ),
                evidence=(evidence,),
            ),
            station_id=station.id,
            request_model="gfs",
            collector="open_meteo_forecast_api",
            run_source_url="https://api.open-meteo.test/v1/forecast",
            pipeline_fingerprint="0123456789abcdef",
            upstream_init_verified=False,
        )

    assert persisted.forecast_rows == 1
    assert persisted.trajectory_rows == 1
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/forecast-provenance")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "healthy"
    assert body["ingests"] == 1
    assert body["stations"] == 1
    assert body["responses"] == 1
    assert body["raw_bytes"] == len(response_body)
    assert body["materialized_forecasts"] == 1
    assert body["materialized_trajectories"] == 1
    assert body["verified_upstream_inits"] == 0
    assert body["single_run_ingests"] == 1
    assert body["average_http_latency_ms"] == pytest.approx(150.0, abs=1.0)
    assert body["p95_http_latency_ms"] == pytest.approx(150.0, abs=1.0)
    assert body["collectors"][0]["collector"] == "open_meteo_forecast_api"
    assert body["collectors"][0]["raw_bytes"] == len(response_body)
