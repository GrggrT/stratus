from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.open_meteo_previous_runs import HistoricalForecastValue
from stratus.db.models import (
    DailyActual,
    HistoricalForecast,
    ObservationRecoveryArtifact,
    ObservationRecoveryPoint,
    ObservationRecoveryRuntime,
    WeatherKind,
)
from stratus.repositories.historical_forecasts import (
    HistoricalForecastConflictError,
    upsert_historical_forecasts,
)
from stratus.repositories.observation_recovery import (
    IEM_ASOS_RECOVERY_PROTOCOL,
    IEM_RETENTION_POLICY_STATEMENT,
    IEM_RETENTION_POLICY_URL,
    IEM_RETENTION_POLICY_VERSION,
    ObservationRecoveryEvidence,
    ObservationRecoveryIntegrityError,
    ObservationRecoveryPointValue,
    record_observation_recovery,
)
from stratus.services.evidence_release_manifest import ALEMBIC_HEAD

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)


async def _station(session: AsyncSession, icao: str = "KREC") -> int:
    return int(
        await session.scalar(
            text(
                """
                INSERT INTO stations (
                    icao, name, city, lat, lon, tz, iem_network,
                    iem_station_id, active, stage
                )
                VALUES (
                    :icao, 'Recovery Test', 'Recovery', 40, -73, 'UTC',
                    'ZZ_ASOS', 'KREC', true, 'paper'
                )
                RETURNING id
                """
            ),
            {"icao": icao},
        )
    )


def _points(
    local_date: date, *, shift: Decimal = Decimal("0")
) -> list[ObservationRecoveryPointValue]:
    start = datetime.combine(local_date, datetime.min.time(), tzinfo=UTC)
    return [
        ObservationRecoveryPointValue(
            obs_time=start + timedelta(hours=hour),
            temp_c=Decimal(hour) + shift,
            raw_report=f"KREC {hour:02d}00Z TEST",
            source_row_bytes=(
                f"KREC,{hour:02d}:00,{hour + shift},KREC {hour:02d}00Z TEST"
            ).encode(),
        )
        for hour in range(24)
    ]


def _evidence(
    station_id: int,
    local_date: date,
    *,
    points: list[ObservationRecoveryPointValue] | None = None,
    suffix: bytes = b"",
) -> ObservationRecoveryEvidence:
    now = datetime.now(UTC)
    selected = points or _points(local_date)
    source_url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    manifest: dict[str, object] = {
        "schema_version": "stratus.iem-request.v1",
        "source_url": source_url,
        "station_id": station_id,
        "icao": "KREC",
        "network": "ZZ_ASOS",
        "source_station_id": "KREC",
        "start": local_date.isoformat(),
        "end": (local_date + timedelta(days=1)).isoformat(),
        "params": [{"name": "station", "value": "KREC"}],
    }
    return ObservationRecoveryEvidence(
        station_id=station_id,
        local_date=local_date,
        timezone="UTC",
        utc_start=datetime.combine(local_date, datetime.min.time(), tzinfo=UTC),
        utc_end=datetime.combine(
            local_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC
        ),
        network="ZZ_ASOS",
        source_station_id="KREC",
        source_name="IEM ASOS archive",
        source_protocol=IEM_ASOS_RECOVERY_PROTOCOL,
        source_url=source_url,
        request_manifest=manifest,
        request_started_at=now - timedelta(seconds=3),
        response_received_at=now - timedelta(seconds=1),
        response_body=(
            b"station,valid,tmpc,metar\n"
            + b"\n".join(point.source_row_bytes for point in selected)
            + suffix
        ),
        retention_policy_url=IEM_RETENTION_POLICY_URL,
        retention_policy_reviewed_at=datetime(2026, 7, 17, tzinfo=UTC),
        retention_policy_version=IEM_RETENTION_POLICY_VERSION,
        retention_policy_statement=IEM_RETENTION_POLICY_STATEMENT,
        storage_permitted=True,
    )


async def test_recovery_is_prospective_atomic_and_idempotent(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    local_date = date(2026, 7, 16)
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        runtime = await session.get(ObservationRecoveryRuntime, 1)
        assert runtime is not None
        assert runtime.opened_at.tzinfo is not None

        evidence = _evidence(station_id, local_date)
        points = _points(local_date)
        result = await record_observation_recovery(
            session, evidence=evidence, points=points
        )
        repeated = await record_observation_recovery(
            session, evidence=evidence, points=points
        )
        later_http_retry = await record_observation_recovery(
            session,
            evidence=replace(
                evidence,
                request_started_at=evidence.request_started_at
                + timedelta(milliseconds=250),
                response_received_at=evidence.response_received_at
                + timedelta(milliseconds=250),
            ),
            points=points,
        )

        assert result.validation_status == "accepted"
        assert result.validation_code == "complete"
        assert result.materialized
        assert not result.idempotent
        assert repeated.artifact_id == result.artifact_id
        assert repeated.record_hash == result.record_hash
        assert repeated.idempotent
        assert later_http_retry.artifact_id == result.artifact_id
        assert later_http_retry.idempotent

    async with db_sessions() as session:
        artifact = await session.get(ObservationRecoveryArtifact, result.artifact_id)
        assert artifact is not None
        assert artifact.response_body == evidence.response_body
        assert artifact.retention_policy_statement == evidence.retention_policy_statement
        assert artifact.point_count == 24
        assert artifact.sample_n == 24
        assert artifact.max_gap_seconds == 3600
        assert artifact.response_received_at >= runtime.opened_at
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryPoint)
        ) == 24
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 1
        actuals = list(
            (
                await session.scalars(
                    select(DailyActual)
                    .where(DailyActual.station_id == station_id)
                    .order_by(DailyActual.kind)
                )
            ).all()
        )
        assert len(actuals) == 2
        by_kind = {actual.kind: actual for actual in actuals}
        assert by_kind[WeatherKind.HIGH].temp_c == Decimal("23.00")
        assert by_kind[WeatherKind.LOW].temp_c == Decimal("0.00")
        assert all(actual.recovery_artifact_id == result.artifact_id for actual in actuals)
        assert all(actual.record_hash is not None for actual in actuals)
        assert all(actual.sample_n == 24 for actual in actuals)


async def test_conflicting_revision_is_quarantined_without_overwrite(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    local_date = date(2026, 7, 16)
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        evidence = _evidence(station_id, local_date)
        accepted = await record_observation_recovery(
            session, evidence=evidence, points=_points(local_date)
        )

    revised_points = _points(local_date, shift=Decimal("10"))
    revised = _evidence(
        station_id,
        local_date,
        points=revised_points,
        suffix=b"\nrevision\n",
    )
    async with db_sessions.begin() as session:
        quarantined = await record_observation_recovery(
            session,
            evidence=revised,
            points=revised_points,
        )
        repeated = await record_observation_recovery(
            session,
            evidence=revised,
            points=revised_points,
        )
        assert quarantined.validation_status == "quarantined"
        assert quarantined.validation_code == "conflicting_revision"
        assert not quarantined.materialized
        assert repeated.artifact_id == quarantined.artifact_id
        assert repeated.idempotent

    async with db_sessions() as session:
        actuals = list(
            (
                await session.scalars(
                    select(DailyActual).where(DailyActual.station_id == station_id)
                )
            ).all()
        )
        assert len(actuals) == 2
        by_kind = {actual.kind: actual for actual in actuals}
        assert by_kind[WeatherKind.HIGH].temp_c == Decimal("23.00")
        assert by_kind[WeatherKind.LOW].temp_c == Decimal("0.00")
        assert all(actual.recovery_artifact_id == accepted.artifact_id for actual in actuals)
        assert await session.scalar(
            select(func.count())
            .select_from(ObservationRecoveryPoint)
            .where(ObservationRecoveryPoint.artifact_id == quarantined.artifact_id)
        ) == 24


async def test_existing_daily_actuals_are_never_overwritten(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    local_date = date(2026, 7, 16)
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        session.add_all(
            [
                DailyActual(
                    station_id=station_id,
                    date=local_date,
                    kind=WeatherKind.HIGH,
                    temp_c=Decimal("99.00"),
                ),
                DailyActual(
                    station_id=station_id,
                    date=local_date,
                    kind=WeatherKind.LOW,
                    temp_c=Decimal("-99.00"),
                ),
            ]
        )
        await session.flush()
        result = await record_observation_recovery(
            session,
            evidence=_evidence(station_id, local_date),
            points=_points(local_date),
        )
        assert result.validation_status == "quarantined"
        assert result.validation_code == "existing_daily_actuals"
        assert not result.materialized

    async with db_sessions() as session:
        values = set(
            (
                await session.scalars(
                    select(DailyActual.temp_c).where(
                        DailyActual.station_id == station_id
                    )
                )
            ).all()
        )
        assert values == {Decimal("99.00"), Decimal("-99.00")}


async def test_point_requires_raw_report_containment_in_exact_csv_row(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    local_date = date(2026, 7, 16)
    points = _points(local_date)
    points[0] = replace(points[0], source_row_bytes=b"KREC,00:00,0,OTHER REPORT")
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        with pytest.raises(
            ObservationRecoveryIntegrityError,
            match="exact CSV row must contain",
        ):
            await record_observation_recovery(
                session,
                evidence=_evidence(station_id, local_date, points=points),
                points=points,
            )
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 0


async def test_recovery_evidence_and_projected_actuals_are_append_only(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    local_date = date(2026, 7, 16)
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        result = await record_observation_recovery(
            session,
            evidence=_evidence(station_id, local_date),
            points=_points(local_date),
        )

    statements = (
        (
            "UPDATE observation_recovery_artifacts SET validation_code='tampered' "
            "WHERE id=:id",
            {"id": result.artifact_id},
        ),
        (
            "DELETE FROM observation_recovery_points WHERE artifact_id=:id",
            {"id": result.artifact_id},
        ),
        (
            "UPDATE daily_actuals SET temp_c=42 WHERE recovery_artifact_id=:id",
            {"id": result.artifact_id},
        ),
    )
    for statement, params in statements:
        async with db_sessions() as session:
            with pytest.raises(DBAPIError, match="append-only"):
                await session.execute(text(statement), params)
                await session.commit()
            await session.rollback()


async def test_ordinary_post_boundary_actual_gets_availability_without_recovery_claim(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        inserted_after = await session.scalar(text("SELECT clock_timestamp()"))
        assert inserted_after is not None
        await session.execute(
            text(
                """
                INSERT INTO daily_actuals (
                    station_id, date, kind, temp_c, first_seen_at
                ) VALUES
                    (:station_id, '2026-07-15', 'high'::weather_kind, 30.00,
                     '2000-01-01T00:00:00Z'),
                    (:station_id, '2026-07-15', 'low'::weather_kind, 20.00,
                     NULL)
                """
            ),
            {"station_id": station_id},
        )

    async with db_sessions() as session:
        actuals = list(
            (
                await session.scalars(
                    select(DailyActual)
                    .where(DailyActual.station_id == station_id)
                    .order_by(DailyActual.kind)
                )
            ).all()
        )
        assert len(actuals) == 2
        assert all(actual.recovery_artifact_id is None for actual in actuals)
        assert all(actual.first_seen_at is not None for actual in actuals)
        assert all(
            actual.first_seen_at >= inserted_after
            for actual in actuals
            if actual.first_seen_at
        )
        assert all(actual.record_hash is None for actual in actuals)
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 0

    statements = (
        "UPDATE daily_actuals SET temp_c=31 "
        "WHERE station_id=:station_id AND date='2026-07-15'",
        "DELETE FROM daily_actuals "
        "WHERE station_id=:station_id AND date='2026-07-15'",
    )
    for statement in statements:
        async with db_sessions() as session:
            with pytest.raises(DBAPIError, match="prospective daily actual evidence"):
                await session.execute(text(statement), {"station_id": station_id})
                await session.commit()
            await session.rollback()


async def test_historical_forecast_is_db_attested_insert_or_compare_and_append_only(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    fetched_at = datetime(2026, 7, 14, 12, tzinfo=UTC)
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        inserted_after = await session.scalar(text("SELECT clock_timestamp()"))
        assert inserted_after is not None
        value = HistoricalForecastValue(
            station_id=station_id,
            model="icon_seamless",
            valid_date=date(2026, 7, 15),
            kind=WeatherKind.HIGH,
            horizon_h=24,
            temp_c=Decimal("30.25"),
            source="open_meteo_previous_runs",
            fetched_at=fetched_at,
        )
        assert await upsert_historical_forecasts(session, [value]) == 1
        assert await upsert_historical_forecasts(session, [value]) == 1

    async with db_sessions() as session:
        stored = await session.scalar(
            select(HistoricalForecast).where(
                HistoricalForecast.station_id == station_id,
                HistoricalForecast.model == value.model,
                HistoricalForecast.valid_date == value.valid_date,
                HistoricalForecast.kind == value.kind,
                HistoricalForecast.horizon_h == value.horizon_h,
            )
        )
        assert stored is not None
        assert stored.first_seen_at is not None
        assert stored.first_seen_at >= inserted_after
        assert stored.temp_c == Decimal("30.25")
        assert stored.fetched_at == fetched_at

    async with db_sessions.begin() as session:
        with pytest.raises(
            HistoricalForecastConflictError,
            match="identity conflicts with stored evidence",
        ):
            await upsert_historical_forecasts(
                session,
                [replace(value, temp_c=Decimal("31.25"))],
            )

    statements = (
        "UPDATE historical_forecasts SET temp_c=31.25 WHERE station_id=:station_id",
        "DELETE FROM historical_forecasts WHERE station_id=:station_id",
    )
    for statement in statements:
        async with db_sessions() as session:
            with pytest.raises(
                DBAPIError,
                match="historical forecast training evidence is append-only",
            ):
                await session.execute(text(statement), {"station_id": station_id})
                await session.commit()
            await session.rollback()


async def test_downgrade_refuses_once_prospective_actual_evidence_exists(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with db_sessions.begin() as session:
        station_id = await _station(session)
        await record_observation_recovery(
            session,
            evidence=_evidence(station_id, date(2026, 7, 16)),
            points=_points(date(2026, 7, 16)),
        )

    with pytest.raises(Exception, match="refusing 0043 downgrade"):
        await asyncio.to_thread(
            command.downgrade,
            Config("alembic.ini"),
            "20260717_0042",
        )

    async with db_sessions() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD
        )
