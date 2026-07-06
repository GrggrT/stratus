from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from stratus.collectors.iem import IemClient, IemStation, year_chunks
from stratus.config import Settings, get_settings
from stratus.db.models import Observation, Station, WeatherKind
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.daily_actuals import DailyActualValue, upsert_daily_actuals
from stratus.repositories.observations import upsert_observations
from stratus.services.daily_actuals import (
    IncompleteObservationDayError,
    ObservationPoint,
    compute_daily_extremes,
    utc_bounds_for_local_date,
)

logger = logging.getLogger(__name__)


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


async def _backfill_daily_actuals(
    session_factory,
    stations: list[Station],
    *,
    start: date,
    end: date,
) -> tuple[int, int]:
    values: list[DailyActualValue] = []
    incomplete = 0
    async with session_factory() as session:
        for station in stations:
            query_start, _ = utc_bounds_for_local_date(start, station.tz)
            _, query_end = utc_bounds_for_local_date(end - date.resolution, station.tz)
            rows = (
                await session.execute(
                    select(Observation.obs_time, Observation.temp_c)
                    .where(
                        Observation.station_id == station.id,
                        Observation.obs_time >= query_start,
                        Observation.obs_time < query_end,
                    )
                    .order_by(Observation.obs_time)
                )
            ).all()
            points_by_date: dict[date, list[ObservationPoint]] = defaultdict(list)
            timezone = ZoneInfo(station.tz)
            for row in rows:
                local_date = row.obs_time.astimezone(timezone).date()
                points_by_date[local_date].append(
                    ObservationPoint(obs_time=row.obs_time, temp_c=row.temp_c)
                )
            for target_date, points in points_by_date.items():
                if not start <= target_date < end:
                    continue
                try:
                    extremes = compute_daily_extremes(
                        points, target_date=target_date, timezone=station.tz
                    )
                except IncompleteObservationDayError:
                    incomplete += 1
                    continue
                values.extend(
                    (
                        DailyActualValue(
                            station_id=station.id,
                            date=target_date,
                            kind=WeatherKind.HIGH,
                            temp_c=extremes.high_c,
                        ),
                        DailyActualValue(
                            station_id=station.id,
                            date=target_date,
                            kind=WeatherKind.LOW,
                            temp_c=extremes.low_c,
                        ),
                    )
                )
    async with session_factory.begin() as session:
        written = await upsert_daily_actuals(session, values)
    return written, incomplete


async def bootstrap_iem(settings: Settings | None = None) -> tuple[int, int]:
    settings = settings or get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    end = datetime.now(UTC).date()
    start = _subtract_years(end, settings.iem_bootstrap_years)
    # UTC API bounds need a one-day pad to fully cover local dates on both sides.
    download_start = start - timedelta(days=1)
    download_end = end + timedelta(days=1)
    try:
        async with session_factory() as session:
            stations = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars().all()
        missing_source = [
            station.icao
            for station in stations
            if not station.iem_network or not station.iem_station_id
        ]
        if missing_source:
            raise RuntimeError(f"stations missing IEM metadata: {', '.join(missing_source)}")

        requests = [
            (
                IemStation(
                    station_id=station.id,
                    icao=station.icao,
                    network=station.iem_network,
                    source_station_id=station.iem_station_id,
                ),
                chunk_start,
                chunk_end,
            )
            for station in stations
            for chunk_start, chunk_end in year_chunks(download_start, download_end)
        ]
        imported = 0
        async with IemClient(asos_url=settings.iem_asos_url) as client:
            for index, (station, chunk_start, chunk_end) in enumerate(requests):
                observations = await client.fetch(
                    station, start=chunk_start, end=chunk_end
                )
                async with session_factory.begin() as session:
                    imported += await upsert_observations(session, observations)
                logger.info(
                    "IEM chunk complete: station=%s start=%s end=%s reports=%d",
                    station.icao,
                    chunk_start,
                    chunk_end,
                    len(observations),
                )
                if index + 1 < len(requests):
                    await asyncio.sleep(settings.iem_request_delay_seconds)

        actual_rows, incomplete_days = await _backfill_daily_actuals(
            session_factory, stations, start=start, end=end
        )
        logger.info(
            "IEM bootstrap complete: reports=%d daily_actual_rows=%d incomplete_days=%d",
            imported,
            actual_rows,
            incomplete_days,
        )
        return imported, actual_rows
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(bootstrap_iem(settings))


if __name__ == "__main__":
    main()
