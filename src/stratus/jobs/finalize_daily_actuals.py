from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from stratus.config import Settings, get_settings
from stratus.db.models import Observation, Station, WeatherKind
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.daily_actuals import DailyActualValue, upsert_daily_actuals
from stratus.services.daily_actuals import (
    IncompleteObservationDayError,
    ObservationPoint,
    compute_daily_extremes,
    utc_bounds_for_local_date,
)

logger = logging.getLogger(__name__)


class DailyActualFinalizationError(RuntimeError):
    pass


async def finalize_daily_actuals(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    now = datetime.now(UTC)
    values: list[DailyActualValue] = []
    failures: list[str] = []
    try:
        async with session_factory() as session:
            stations = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars().all()
            for station in stations:
                local_today = now.astimezone(ZoneInfo(station.tz)).date()
                target_date = local_today - timedelta(days=1)
                start, end = utc_bounds_for_local_date(target_date, station.tz)
                rows = (
                    await session.execute(
                        select(Observation)
                        .where(
                            Observation.station_id == station.id,
                            Observation.obs_time >= start,
                            Observation.obs_time < end,
                        )
                        .order_by(Observation.obs_time)
                    )
                ).scalars()
                points = [
                    ObservationPoint(obs_time=row.obs_time, temp_c=row.temp_c) for row in rows
                ]
                try:
                    extremes = compute_daily_extremes(
                        points, target_date=target_date, timezone=station.tz
                    )
                except IncompleteObservationDayError as exc:
                    failures.append(f"{station.icao}/{target_date}: {exc}")
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
        logger.info(
            "daily actual finalization complete: rows=%d failures=%d", written, len(failures)
        )
        if failures:
            raise DailyActualFinalizationError("; ".join(failures))
        return written
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(finalize_daily_actuals(settings))


if __name__ == "__main__":
    main()

