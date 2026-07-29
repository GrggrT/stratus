from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.iem import (
    IemError,
    IemFetchResult,
    IemRequestManifest,
    IemStation,
    parse_iem_csv_rows,
)
from stratus.config import Settings
from stratus.db.models import (
    DailyActual,
    Observation,
    ObservationRecoveryArtifact,
    ObservationRecoveryPoint,
    ProbabilityPipelineReleaseEpoch,
    WeatherKind,
)
from stratus.jobs.recover_observations import (
    ObservationRecoveryAllFailedError,
    ObservationRecoveryReleaseBlockedError,
    recover_observations,
)
from stratus.services.daily_actuals import utc_bounds_for_local_date

TEST_IMAGE_DIGEST = "b" * 64

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)


async def _no_sleep(_: float) -> None:
    return None


async def _set_iem_mapping(
    sessions: async_sessionmaker[AsyncSession],
    *,
    icao: str,
    network: str,
    source_station_id: str,
) -> int:
    async with sessions.begin() as session:
        return int(
            await session.scalar(
                text(
                    "UPDATE stations SET iem_network=:network, "
                    "iem_station_id=:source_station_id WHERE icao=:icao RETURNING id"
                ),
                {
                    "icao": icao,
                    "network": network,
                    "source_station_id": source_station_id,
                },
            )
        )


class _FakeIemClient:
    def __init__(
        self,
        *,
        now: datetime,
        station_days: dict[str, tuple[date, str]],
        fail_icaos: set[str] | None = None,
        oversize_icaos: set[str] | None = None,
        sparse_icaos: set[str] | None = None,
    ) -> None:
        self.now = now
        self.station_days = station_days
        self.fail_icaos = fail_icaos or set()
        self.oversize_icaos = oversize_icaos or set()
        self.sparse_icaos = sparse_icaos or set()
        self.calls: list[str] = []
        self.bodies: dict[str, bytes] = {}

    async def fetch_with_evidence(
        self,
        station: IemStation,
        *,
        start: date,
        end: date,
    ) -> IemFetchResult:
        self.calls.append(station.icao)
        if station.icao in self.fail_icaos:
            raise IemError("synthetic IEM transport failure")
        target_date, timezone = self.station_days[station.icao]
        utc_start, utc_end = utc_bounds_for_local_date(target_date, timezone)
        lines = [b"station,valid,tmpc,metar\r\n"]
        cursor = utc_start
        index = 0
        while cursor < utc_end:
            if station.icao in self.sparse_icaos and index >= 10:
                break
            temp = Decimal(index % 17) + Decimal("0.25")
            lines.append(
                (
                    f"{station.source_station_id},{cursor:%Y-%m-%d %H:%M},"
                    f"{temp},{station.icao} {cursor:%d%H%M}Z TEST {index}\r\n"
                ).encode()
            )
            cursor += timedelta(hours=1)
            index += 1
        body = b"".join(lines)
        if station.icao in self.oversize_icaos:
            body += b"#" + (b"x" * 11_000) + b"\r\n"
        self.bodies[station.icao] = body
        rows = parse_iem_csv_rows(
            body,
            station_id=station.station_id,
            start=start,
            end=end,
            source_station_id=station.source_station_id,
        )
        manifest = IemRequestManifest(
            source_url="https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
            station_id=station.station_id,
            icao=station.icao,
            network=station.network,
            source_station_id=station.source_station_id,
            start=start,
            end=end,
            params=(
                ("network", station.network),
                ("station", station.source_station_id),
                ("year1", str(start.year)),
                ("month1", str(start.month)),
                ("day1", str(start.day)),
                ("year2", str(end.year)),
                ("month2", str(end.month)),
                ("day2", str(end.day)),
                ("tz", "Etc/UTC"),
            ),
        )
        observations = tuple(row.observation for row in rows)
        return IemFetchResult(
            request_manifest=manifest,
            request_started_at=self.now - timedelta(seconds=2),
            response_received_at=self.now - timedelta(seconds=1),
            response_status_code=200,
            response_headers=(("content-type", "text/csv"), ("etag", '"test-v1"')),
            response_body=body,
            observations=observations,
            exact_rows=tuple(rows),
        )


async def test_job_materializes_atomic_pair_from_exact_artifact_without_live_merge(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    station_id = await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    settings = Settings(_env_file=None, release_image_digest=TEST_IMAGE_DIGEST)
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    target_date = now.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
    )

    summary = await recover_observations(
        settings,
        db_sessions,
        target_date=target_date,
        station_icao="KLGA",
        client=client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert summary.attempted == 1
    assert summary.materialized == 1
    assert summary.daily_actual_rows == 2
    assert not summary.failures

    async with db_sessions() as session:
        actuals = list(
            (
                await session.scalars(
                    select(DailyActual).where(
                        DailyActual.station_id == station_id,
                        DailyActual.date == target_date,
                    )
                )
            ).all()
        )
        artifact = await session.scalar(select(ObservationRecoveryArtifact))
        points = list((await session.scalars(select(ObservationRecoveryPoint))).all())
        assert len(actuals) == 2
        assert {value.kind for value in actuals} == {WeatherKind.HIGH, WeatherKind.LOW}
        assert artifact is not None
        assert artifact.response_body == client.bodies["KLGA"]
        assert points
        assert all(point.source_row_bytes in artifact.response_body for point in points)
        assert await session.scalar(select(func.count()).select_from(Observation)) == 0


async def test_automatic_run_recovers_after_missed_station_local_hour_and_repeat_skips(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    async with db_sessions.begin() as session:
        await session.execute(text("UPDATE stations SET active=false WHERE icao <> 'KLGA'"))
    settings = Settings(
        _env_file=None,
        release_image_digest=TEST_IMAGE_DIGEST,
        daily_actual_recovery_lookback_days=1,
        daily_actual_recovery_local_hour=0,
    )
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    timezone = ZoneInfo("America/New_York")
    local_date = now.astimezone(timezone).date()
    # The midnight scheduler boundary has passed; any later hourly run must drain it.
    target_date = local_date - timedelta(days=1)
    client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
    )

    first = await recover_observations(
        settings,
        db_sessions,
        client=client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert first.scanned == 1
    assert first.attempted == 1
    assert first.materialized == 1
    assert client.calls == ["KLGA"]

    repeat_client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
    )
    repeated = await recover_observations(
        settings,
        db_sessions,
        client=repeat_client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert repeated.scanned == 1
    assert repeated.attempted == 0
    assert repeated.skipped_complete == 1
    assert repeat_client.calls == []
    async with db_sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 1
        assert await session.scalar(select(func.count()).select_from(DailyActual)) == 2


async def test_automatic_runs_drain_backlog_across_request_cap(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    await _set_iem_mapping(
        db_sessions, icao="KCOH", network="ZZ_ASOS", source_station_id="COH"
    )
    settings = Settings(
        _env_file=None,
        release_image_digest=TEST_IMAGE_DIGEST,
        daily_actual_recovery_lookback_days=1,
        daily_actual_recovery_local_hour=0,
        daily_actual_recovery_max_requests_per_run=1,
    )
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    kcoh_target_date = now.date() - timedelta(days=1)
    klga_target_date = (
        now.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    )
    station_days = {
        "KCOH": (kcoh_target_date, "UTC"),
        "KLGA": (klga_target_date, "America/New_York"),
    }

    first_client = _FakeIemClient(now=now, station_days=station_days)
    first = await recover_observations(
        settings,
        db_sessions,
        client=first_client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert first.scanned == 2
    assert first.attempted == 1
    assert first.materialized == 1
    assert first.skipped_cap == 1
    assert first_client.calls == ["KCOH"]

    second_client = _FakeIemClient(now=now, station_days=station_days)
    second = await recover_observations(
        settings,
        db_sessions,
        client=second_client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert second.scanned == 2
    assert second.attempted == 1
    assert second.materialized == 1
    assert second.skipped_complete == 1
    assert second.skipped_cap == 0
    assert second_client.calls == ["KLGA"]

    async with db_sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 2
        assert await session.scalar(select(func.count()).select_from(DailyActual)) == 4


async def test_job_isolates_one_station_failure_and_continues_the_next(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    kcoh_id = await _set_iem_mapping(
        db_sessions, icao="KCOH", network="ZZ_ASOS", source_station_id="COH"
    )
    settings = Settings(_env_file=None, release_image_digest=TEST_IMAGE_DIGEST)
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    target_date = now.date() - timedelta(days=2)
    client = _FakeIemClient(
        now=now,
        station_days={
            "KLGA": (target_date, "America/New_York"),
            "KCOH": (target_date, "UTC"),
        },
        fail_icaos={"KLGA"},
    )

    summary = await recover_observations(
        settings,
        db_sessions,
        target_date=target_date,
        client=client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert summary.attempted == 2
    assert summary.materialized == 1
    assert len(summary.failures) == 1
    assert "KLGA" in summary.failures[0]
    async with db_sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(DailyActual).where(
                DailyActual.station_id == kcoh_id,
                DailyActual.date == target_date,
            )
        ) == 2


async def test_release_mismatch_blocks_before_http_and_evidence_write(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    del activated_probability_release_epoch
    now = datetime.now(UTC)
    client = _FakeIemClient(now=now, station_days={})
    settings = Settings(_env_file=None, release_image_digest="c" * 64)
    with pytest.raises(ObservationRecoveryReleaseBlockedError):
        await recover_observations(
            settings,
            db_sessions,
            target_date=now.date() - timedelta(days=1),
            station_icao="KLGA",
            client=client,
            clock=lambda: now,
            sleep=_no_sleep,
        )
    assert client.calls == []
    async with db_sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 0


async def test_partial_pair_is_conflict_and_all_failed_is_typed(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    station_id = await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    settings = Settings(_env_file=None, release_image_digest=TEST_IMAGE_DIGEST)
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    target_date = now.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    async with db_sessions.begin() as session:
        session.add(
            DailyActual(
                station_id=station_id,
                date=target_date,
                kind=WeatherKind.HIGH,
                temp_c=Decimal("20"),
            )
        )
    client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
    )
    with pytest.raises(ObservationRecoveryAllFailedError) as raised:
        await recover_observations(
            settings,
            db_sessions,
            target_date=target_date,
            station_icao="KLGA",
            client=client,
            clock=lambda: now,
            sleep=_no_sleep,
        )
    assert raised.value.summary.conflicts == 1
    assert client.calls == []


async def test_oversized_response_is_not_persisted(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    settings = Settings(
        _env_file=None,
        release_image_digest=TEST_IMAGE_DIGEST,
        daily_actual_recovery_max_response_bytes=10_000,
    )
    await activated_probability_release_epoch(settings)
    now = datetime.now(UTC)
    target_date = now.astimezone(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
        oversize_icaos={"KLGA"},
    )
    with pytest.raises(ObservationRecoveryAllFailedError, match="byte cap"):
        await recover_observations(
            settings,
            db_sessions,
            target_date=target_date,
            station_icao="KLGA",
            client=client,
            clock=lambda: now,
            sleep=_no_sleep,
        )
    async with db_sessions() as session:
        assert await session.scalar(
            select(func.count()).select_from(ObservationRecoveryArtifact)
        ) == 0


async def test_incomplete_artifact_is_retained_without_actuals_and_enters_cooldown(
    db_sessions: async_sessionmaker[AsyncSession],
    activated_probability_release_epoch: Callable[
        [Settings], Awaitable[ProbabilityPipelineReleaseEpoch]
    ],
) -> None:
    await _set_iem_mapping(
        db_sessions, icao="KLGA", network="NY_ASOS", source_station_id="LGA"
    )
    async with db_sessions.begin() as session:
        await session.execute(text("UPDATE stations SET active=false WHERE icao <> 'KLGA'"))
    now = datetime.now(UTC)
    local_now = now.astimezone(ZoneInfo("America/New_York"))
    settings = Settings(
        _env_file=None,
        release_image_digest=TEST_IMAGE_DIGEST,
        daily_actual_recovery_lookback_days=1,
        daily_actual_recovery_local_hour=local_now.hour,
    )
    await activated_probability_release_epoch(settings)
    target_date = local_now.date() - timedelta(days=1)
    client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
        sparse_icaos={"KLGA"},
    )
    with pytest.raises(ObservationRecoveryAllFailedError) as raised:
        await recover_observations(
            settings,
            db_sessions,
            target_date=target_date,
            station_icao="KLGA",
            client=client,
            clock=lambda: now,
            sleep=_no_sleep,
        )
    assert raised.value.summary.quarantined == 1
    async with db_sessions() as session:
        artifact = await session.scalar(select(ObservationRecoveryArtifact))
        assert artifact is not None
        assert artifact.validation_code == "incomplete_coverage"
        assert await session.scalar(select(func.count()).select_from(DailyActual)) == 0

    retry_client = _FakeIemClient(
        now=now,
        station_days={"KLGA": (target_date, "America/New_York")},
    )
    summary = await recover_observations(
        settings,
        db_sessions,
        client=retry_client,
        clock=lambda: now,
        sleep=_no_sleep,
    )
    assert summary.attempted == 0
    assert summary.skipped_cooldown == 1
    assert retry_client.calls == []
