from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.open_meteo import (
    ForecastBundle,
    ForecastTrajectoryValue,
    ForecastValue,
    SourceResponseEvidence,
)
from stratus.db.models import (
    Forecast,
    ForecastIngest,
    ForecastRun,
    ForecastSourceResponse,
    ForecastTrajectory,
    Station,
    WeatherKind,
)
from stratus.repositories.forecast_provenance import record_forecast_ingest
from stratus.repositories.forecast_runs import upsert_forecast_run
from stratus.repositories.forecast_trajectories import upsert_forecast_trajectories
from stratus.repositories.forecasts import upsert_forecasts
from stratus.services.forecast_collection import persist_forecast_bundle
from stratus.services.forecast_provenance import (
    FORECAST_AVAILABILITY_SEMANTICS,
    build_forecast_manifest,
)
from stratus.services.model_catalog import descriptor_for

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def _station(session: AsyncSession, *, icao: str = "KSEA") -> Station:
    station = Station(
        icao=icao,
        name="Seattle Tacoma International Airport",
        city="Seattle",
        lat=Decimal("47.4502"),
        lon=Decimal("-122.3088"),
        tz="America/Los_Angeles",
    )
    session.add(station)
    await session.flush()
    return station


def _response(
    *,
    suffix: str = "forecast",
    body: bytes = b'{"hourly":{"temperature_2m":[12.3]}}',
    offset_seconds: int = 0,
) -> SourceResponseEvidence:
    received = datetime.now(UTC) - timedelta(seconds=2 + offset_seconds)
    return SourceResponseEvidence(
        request_started_at=received - timedelta(milliseconds=150),
        response_received_at=received,
        source_url=f"https://api.example.test/{suffix}?latitude=47.45&longitude=-122.31",
        http_date=received.replace(microsecond=0),
        etag=f'"{suffix}-v1"',
        response_body=body,
    )


async def _run(
    session: AsyncSession,
    *,
    init_time: datetime,
    model: str = "gfs",
    metadata: dict[str, object] | None = None,
) -> int:
    return await upsert_forecast_run(
        session,
        descriptor=descriptor_for(
            model,
            init_time=init_time,
            source_url="https://api.example.test/forecast",
        ),
        init_time=init_time,
        available_at=init_time + timedelta(minutes=5),
        retrieved_at=init_time + timedelta(minutes=6),
        source_url="https://api.example.test/forecast",
        status="complete",
        checksum="a" * 64,
        metadata=metadata or {"attempt": 1},
    )


async def test_forecast_provenance_roundtrip_and_exact_materialization(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    evidence = _response()
    async with db_sessions.begin() as session:
        station = await _station(session)
        run_id = await _run(session, init_time=init_time)
        forecast = ForecastValue(
            station_id=station.id,
            model="gfs",
            run_time=init_time,
            valid_date=valid_date,
            kind=WeatherKind.HIGH,
            temp_c=Decimal("20.40"),
            fetched_at=datetime.now(UTC),
            run_id=run_id,
        )
        trajectory = ForecastTrajectoryValue(
            station_id=station.id,
            model="gfs",
            run_time=init_time,
            valid_date=valid_date,
            points=(("2026-07-16T12:00", Decimal("19.20")),),
            fetched_at=datetime.now(UTC),
            run_id=run_id,
        )
        result = await record_forecast_ingest(
            session,
            run_id=run_id,
            station_id=station.id,
            request_model="gfs",
            collector="open-meteo",
            pipeline_fingerprint="0123456789abcdef",
            availability_semantics=FORECAST_AVAILABILITY_SEMANTICS,
            normalized_manifest=build_forecast_manifest([forecast], [trajectory]),
            responses=[evidence],
            upstream_init_time=None,
            upstream_init_verified=False,
        )
        assert result.first_seen_at >= evidence.response_received_at
        await upsert_forecasts(
            session,
            [
                replace(
                    forecast,
                    ingest_id=result.ingest_id,
                    available_at=result.first_seen_at,
                )
            ],
        )
        await upsert_forecast_trajectories(
            session,
            [
                replace(
                    trajectory,
                    ingest_id=result.ingest_id,
                    available_at=result.first_seen_at,
                )
            ],
        )

    async with db_sessions() as session:
        ingest = await session.get(ForecastIngest, result.ingest_id)
        response = (
            await session.scalars(
                select(ForecastSourceResponse).where(
                    ForecastSourceResponse.ingest_id == result.ingest_id
                )
            )
        ).one()
        stored_forecast = (
            await session.scalars(select(Forecast).where(Forecast.ingest_id == result.ingest_id))
        ).one()
        stored_trajectory = (
            await session.scalars(
                select(ForecastTrajectory).where(
                    ForecastTrajectory.ingest_id == result.ingest_id
                )
            )
        ).one()
        assert ingest is not None
        assert ingest.first_seen_at == result.first_seen_at
        assert ingest.normalized_manifest_sha256 == result.normalized_manifest_sha256
        assert response.response_body == evidence.response_body
        assert response.response_sha256 == evidence.response_sha256
        assert response.response_bytes == evidence.response_bytes
        assert stored_forecast.available_at == result.first_seen_at
        assert stored_trajectory.available_at == result.first_seen_at


async def test_nws_ingest_preserves_two_exact_source_responses(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    discovery = _response(suffix="points", body=b'{"properties":{"forecastHourly":"x"}}')
    forecast_http = _response(
        suffix="forecast-hourly",
        body=b'{"properties":{"periods":[{"temperature":70}]}}',
        offset_seconds=1,
    )
    async with db_sessions.begin() as session:
        station = await _station(session)
        run_id = await _run(session, init_time=init_time, model="nws")
        value = ForecastValue(
            station_id=station.id,
            model="nws",
            run_time=init_time,
            valid_date=valid_date,
            kind=WeatherKind.HIGH,
            temp_c=Decimal("21.1"),
            fetched_at=datetime.now(UTC),
            run_id=run_id,
        )
        result = await record_forecast_ingest(
            session,
            run_id=run_id,
            station_id=station.id,
            request_model="nws",
            collector="nws",
            pipeline_fingerprint="0123456789abcdef",
            availability_semantics=FORECAST_AVAILABILITY_SEMANTICS,
            normalized_manifest=build_forecast_manifest([value], []),
            responses=[discovery, forecast_http],
            upstream_init_time=init_time,
            upstream_init_verified=True,
        )

    async with db_sessions() as session:
        rows = (
            await session.scalars(
                select(ForecastSourceResponse)
                .where(ForecastSourceResponse.ingest_id == result.ingest_id)
                .order_by(ForecastSourceResponse.sequence_no)
            )
        ).all()
        assert [row.sequence_no for row in rows] == [0, 1]
        assert [row.response_body for row in rows] == [
            discovery.response_body,
            forecast_http.response_body,
        ]


async def test_nws_high_precision_temperature_matches_numeric_materialization(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    fetched_at = datetime.now(UTC)
    nws_temperature_c = (Decimal("70") - Decimal("32")) * Decimal("5") / Decimal("9")
    assert nws_temperature_c == Decimal("21.11111111111111111111111111")

    async with db_sessions.begin() as session:
        station = await _station(session)
        persisted = await persist_forecast_bundle(
            session,
            bundle=ForecastBundle(
                forecasts=(
                    ForecastValue(
                        station_id=station.id,
                        model="nws",
                        run_time=init_time,
                        valid_date=valid_date,
                        kind=WeatherKind.HIGH,
                        temp_c=nws_temperature_c,
                        fetched_at=fetched_at,
                    ),
                ),
                trajectories=(),
                evidence=(_response(suffix="forecast-hourly"),),
            ),
            station_id=station.id,
            request_model="nws",
            collector="nws",
            run_source_url="https://api.weather.gov/gridpoints/SEW/124,67/forecast/hourly",
            pipeline_fingerprint="0123456789abcdef",
            upstream_init_verified=True,
        )

    async with db_sessions() as session:
        ingest = await session.get(ForecastIngest, persisted.ingest_id)
        stored = (
            await session.scalars(
                select(Forecast).where(Forecast.ingest_id == persisted.ingest_id)
            )
        ).one()
        assert ingest is not None
        assert ingest.normalized_manifest["forecasts"][0]["temp_c"] == "21.11"
        assert stored.temp_c == Decimal("21.11")


async def test_high_precision_trajectory_matches_json_materialization(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    fetched_at = datetime.now(UTC)
    raw_temperature = Decimal("12.123456789123456789")

    async with db_sessions.begin() as session:
        station = await _station(session)
        persisted = await persist_forecast_bundle(
            session,
            bundle=ForecastBundle(
                forecasts=(),
                trajectories=(
                    ForecastTrajectoryValue(
                        station_id=station.id,
                        model="gfs",
                        run_time=init_time,
                        valid_date=valid_date,
                        points=(("2026-07-16T00:00", raw_temperature),),
                        fetched_at=fetched_at,
                    ),
                ),
                evidence=(_response(),),
            ),
            station_id=station.id,
            request_model="gfs",
            collector="open_meteo_forecast_api",
            run_source_url="https://api.open-meteo.test/v1/forecast",
            pipeline_fingerprint="0123456789abcdef",
            upstream_init_verified=False,
        )

    async with db_sessions() as session:
        ingest = await session.get(ForecastIngest, persisted.ingest_id)
        stored = (
            await session.scalars(
                select(ForecastTrajectory).where(
                    ForecastTrajectory.ingest_id == persisted.ingest_id
                )
            )
        ).one()
        assert ingest is not None
        manifest_point = ingest.normalized_manifest["trajectories"][0]["points"][0]
        assert manifest_point["temp_c"] == "12.123456789123457"
        assert stored.points[0]["temp_c"] == 12.123456789123457


async def test_provenance_rows_and_cited_run_are_append_only(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    async with db_sessions.begin() as session:
        station = await _station(session)
        run_id = await _run(session, init_time=init_time)
        value = ForecastValue(
            station_id=station.id,
            model="gfs",
            run_time=init_time,
            valid_date=valid_date,
            kind=WeatherKind.HIGH,
            temp_c=Decimal("18.2"),
            fetched_at=datetime.now(UTC),
            run_id=run_id,
        )
        result = await record_forecast_ingest(
            session,
            run_id=run_id,
            station_id=station.id,
            request_model="gfs",
            collector="open-meteo",
            pipeline_fingerprint="0123456789abcdef",
            availability_semantics=FORECAST_AVAILABILITY_SEMANTICS,
            normalized_manifest=build_forecast_manifest([value], []),
            responses=[_response()],
        )

    for statement in (
        "UPDATE forecast_ingests SET collector = 'tampered' WHERE id = :id",
        "UPDATE forecast_source_responses SET response_body = 'bad'::bytea "
        "WHERE ingest_id = :id",
        "UPDATE forecast_runs SET status = 'tampered' WHERE id = :run_id",
    ):
        with pytest.raises(DBAPIError):
            async with db_sessions.begin() as session:
                await session.execute(
                    text(statement), {"id": result.ingest_id, "run_id": run_id}
                )

    with pytest.raises(DBAPIError, match="bytes do not match size/hash"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO forecast_source_responses (
                        ingest_id, sequence_no, request_method,
                        request_started_at, response_received_at, source_url,
                        request_sha256, http_date, etag, response_body,
                        response_sha256, response_bytes, evidence_hash, record_hash
                    )
                    SELECT ingest_id, sequence_no, request_method,
                           request_started_at, response_received_at, source_url,
                           request_sha256, http_date, etag,
                           convert_to('tampered', 'UTF8'),
                           response_sha256, response_bytes, evidence_hash, record_hash
                    FROM forecast_source_responses
                    WHERE ingest_id = :id AND sequence_no = 0
                    """
                ),
                {"id": result.ingest_id},
            )


async def test_prospective_consistency_and_legacy_tolerance(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    async with db_sessions.begin() as session:
        station = await _station(session)
        run_id = await _run(session, init_time=init_time)
        boundary = await session.scalar(
            text("SELECT opened_at FROM forecast_provenance_runtime WHERE id = 1")
        )
        assert isinstance(boundary, datetime)
        session.add(
            Forecast(
                station_id=station.id,
                model="legacy",
                run_time=init_time - timedelta(days=2),
                valid_date=valid_date,
                kind=WeatherKind.LOW,
                temp_c=Decimal("8.0"),
                fetched_at=boundary - timedelta(seconds=1),
                run_id=None,
                ingest_id=None,
                available_at=boundary - timedelta(seconds=1),
            )
        )

    with pytest.raises(DBAPIError):
        async with db_sessions.begin() as session:
            station_id = await session.scalar(select(Station.id))
            assert station_id is not None
            session.add(
                Forecast(
                    station_id=station_id,
                    model="missing-provenance",
                    run_time=init_time,
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal("17.0"),
                    fetched_at=datetime.now(UTC),
                    run_id=run_id,
                    ingest_id=None,
                    available_at=datetime.now(UTC),
                )
            )


async def test_mismatched_availability_and_trajectory_points_are_rejected(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=1)
    valid_date = date.today() + timedelta(days=1)
    async with db_sessions.begin() as session:
        station = await _station(session)
        run_id = await _run(session, init_time=init_time)
        trajectory = ForecastTrajectoryValue(
            station_id=station.id,
            model="gfs",
            run_time=init_time,
            valid_date=valid_date,
            points=(("2026-07-16T12:00", Decimal("19.2")),),
            fetched_at=datetime.now(UTC),
            run_id=run_id,
        )
        result = await record_forecast_ingest(
            session,
            run_id=run_id,
            station_id=station.id,
            request_model="gfs",
            collector="open-meteo",
            pipeline_fingerprint="0123456789abcdef",
            availability_semantics=FORECAST_AVAILABILITY_SEMANTICS,
            normalized_manifest=build_forecast_manifest([], [trajectory]),
            responses=[_response()],
        )

    with pytest.raises(DBAPIError):
        async with db_sessions.begin() as session:
            await upsert_forecast_trajectories(
                session,
                [
                    replace(
                        trajectory,
                        points=(("2026-07-16T12:00", Decimal("25.0")),),
                        ingest_id=result.ingest_id,
                        available_at=result.first_seen_at,
                    )
                ],
            )

    with pytest.raises(DBAPIError):
        async with db_sessions.begin() as session:
            value = ForecastValue(
                station_id=station.id,
                model="gfs",
                run_time=init_time,
                valid_date=valid_date,
                kind=WeatherKind.HIGH,
                temp_c=Decimal("18.0"),
                fetched_at=datetime.now(UTC),
                run_id=run_id,
                ingest_id=result.ingest_id,
                available_at=result.first_seen_at + timedelta(seconds=1),
            )
            await upsert_forecasts(session, [value])


async def test_duplicate_run_keeps_original_point_in_time_evidence(
    db_sessions: SessionFactory,
) -> None:
    init_time = datetime.now(UTC) - timedelta(hours=2)
    async with db_sessions.begin() as session:
        await _station(session)
        run_id = await _run(session, init_time=init_time, metadata={"attempt": 1})
        original = await session.get(ForecastRun, run_id)
        assert original is not None
        original_values = (
            original.first_seen_at,
            original.available_at,
            original.retrieved_at,
            original.status,
            original.metadata_,
        )
        duplicate_id = await upsert_forecast_run(
            session,
            descriptor=descriptor_for(
                "gfs",
                init_time=init_time,
                source_url="https://api.example.test/retry",
            ),
            init_time=init_time,
            available_at=init_time + timedelta(minutes=20),
            retrieved_at=init_time + timedelta(minutes=21),
            source_url="https://api.example.test/retry",
            status="retry",
            checksum="b" * 64,
            metadata={"attempt": 2},
        )
        assert duplicate_id == run_id
        await session.refresh(original)
        assert (
            original.first_seen_at,
            original.available_at,
            original.retrieved_at,
            original.status,
            original.metadata_,
        ) == original_values
