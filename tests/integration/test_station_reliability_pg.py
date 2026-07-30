from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    Observation,
    Station,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    StationStage,
)
from stratus.jobs.assess_station_reliability import assess_station_reliability_job
from stratus.repositories.station_reliability import (
    station_reliability_record_is_valid,
)
from stratus.services.evidence_release_manifest import ALEMBIC_HEAD

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)


async def test_station_reliability_job_persists_append_only_selection_evidence(
    db_sessions: async_sessionmaker[AsyncSession],
    migrated_database_url: str,
) -> None:
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        station = Station(
            icao="TEST",
            name="Test Station",
            city="Test City",
            lat=Decimal("48.200000"),
            lon=Decimal("16.370000"),
            tz="Europe/Vienna",
            iem_network="XX_ASOS",
            iem_station_id="TEST",
            active=True,
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        session.add(
            Observation(
                station_id=station.id,
                obs_time=now,
                temp_c=Decimal("21.50"),
                raw_metar="TEST 170000Z 00000KT CAVOK 22/10 Q1013",
            )
        )

    settings = Settings(
        _env_file=None,
        database_url=migrated_database_url,
    )
    assert await assess_station_reliability_job(settings, db_sessions) == 1

    async with db_sessions() as session:
        snapshot = await session.scalar(select(StationReliabilitySnapshot))
        assert snapshot is not None
        assert snapshot.status is StationReliabilityStatus.COLLECTING
        assert snapshot.comparable_units == 0
        assert snapshot.unavailable_units == 0
        assert snapshot.observation_cooldown_until is None
    assert snapshot.reasons == ["station_reliability_collecting"]
    assert station_reliability_record_is_valid(snapshot)
    snapshot_id = snapshot.id

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(update(StationReliabilitySnapshot).values(match_units=1))

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(
                delete(StationReliabilitySnapshot).where(
                    StationReliabilitySnapshot.id == snapshot_id
                )
            )

    app = create_app(settings)
    app.state.sessions = db_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/stations")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["icao"] == "TEST"
    assert item["reliability_status"] == "collecting"
    assert item["reliability_score"] is None
    assert item["reliability_lower_bound"] is None
    assert item["reliability_samples"] == 0
    assert item["reliability_conflicts"] == 0
    assert item["reliability_boundary_samples"] == 0
    assert item["reliability_unavailable_samples"] == 0
    assert item["reliability_evaluated_at"] is not None
    assert item["reliability_reasons"] == ["station_reliability_collecting"]
    assert item["latest_conflict_at"] is None
    assert item["latest_conflict_date"] is None
    assert item["latest_conflict_kind"] is None
    assert item["resolution_cooldown_until"] is None
    assert item["observation_cooldown_until"] is None
    assert len(item["reliability_policy_hash"]) == 64
    assert len(item["reliability_source_hash"]) == 64
    assert len(item["reliability_record_hash"]) == 64
    assert item["selection_evaluated_for_date"] == now.astimezone(
        ZoneInfo("Europe/Vienna")
    ).date().isoformat()
    assert item["selection_blocked"] is False
    assert item["selection_reasons"] == []


async def test_station_reliability_database_rejects_inconsistent_counts(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        station = Station(
            icao="TBAD",
            name="Constraint Test Station",
            city="Constraint City",
            lat=Decimal("48.200000"),
            lon=Decimal("16.370000"),
            tz="UTC",
            active=True,
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        station_id = station.id

    invalid = StationReliabilitySnapshot(
        station_id=station_id,
        evaluated_at=now,
        window_days=180,
        comparable_units=2,
        match_units=1,
        conflict_units=0,
        exact_source_mismatch_units=0,
        clear_mismatch_units=0,
        exact_truth_gap_units=0,
        boundary_units=0,
        unavailable_units=0,
        reliability_score=Decimal("1.000000"),
        reliability_lower_bound=Decimal("0.500000"),
        status=StationReliabilityStatus.RELIABLE,
        latest_conflict_at=None,
        latest_conflict_date=None,
        latest_conflict_kind=None,
        resolution_cooldown_until=None,
        latest_observation_at=now,
        observation_local_date=now.date(),
        observation_cooldown_until=None,
        reasons=[],
        policy_hash="a" * 64,
        source_hash="b" * 64,
        record_hash="c" * 64,
    )
    with pytest.raises(DBAPIError, match="ck_station_reliability_comparable_sum"):
        async with db_sessions.begin() as session:
            session.add(invalid)
            await session.flush()


async def test_0042_downgrade_refuses_while_station_evidence_exists(
    db_sessions: async_sessionmaker[AsyncSession],
    migrated_database_url: str,
) -> None:
    assert migrated_database_url
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        station = Station(
            icao="TDWN",
            name="Downgrade Test Station",
            city="Downgrade City",
            lat=Decimal("48.200000"),
            lon=Decimal("16.370000"),
            tz="UTC",
            active=True,
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        session.add(
            StationReliabilitySnapshot(
                station_id=station.id,
                evaluated_at=now + timedelta(microseconds=1),
                window_days=180,
                comparable_units=0,
                match_units=0,
                conflict_units=0,
                exact_source_mismatch_units=0,
                clear_mismatch_units=0,
                exact_truth_gap_units=0,
                boundary_units=0,
                unavailable_units=0,
                reliability_score=None,
                reliability_lower_bound=None,
                status=StationReliabilityStatus.COLLECTING,
                latest_conflict_at=None,
                latest_conflict_date=None,
                latest_conflict_kind=None,
                resolution_cooldown_until=None,
                latest_observation_at=now,
                observation_local_date=now.date(),
                observation_cooldown_until=None,
                reasons=["station_reliability_collecting"],
                policy_hash="d" * 64,
                source_hash="e" * 64,
                record_hash="f" * 64,
            )
        )

    with pytest.raises(Exception, match="refusing 0042 downgrade"):
        await asyncio.to_thread(
            command.downgrade,
            Config("alembic.ini"),
            "20260715_0041",
        )

    async with db_sessions() as session:
        assert await session.scalar(text("SELECT version_num FROM alembic_version")) == (
            ALEMBIC_HEAD
        )
