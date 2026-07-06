from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from stratus.collectors.aviation_weather import AviationWeatherClient
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.observations import upsert_observations

logger = logging.getLogger(__name__)


async def fetch_observations(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            stations = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars()
            station_ids_by_icao = {station.icao: station.id for station in stations}
        if not station_ids_by_icao:
            raise RuntimeError("no active stations are configured")

        async with AviationWeatherClient(
            metar_url=settings.aviation_weather_metar_url,
            user_agent=settings.aviation_weather_user_agent,
            lookback_hours=settings.observation_lookback_hours,
        ) as client:
            observations = await client.fetch_metars(station_ids_by_icao)

        async with session_factory.begin() as session:
            written = await upsert_observations(session, observations)
        logger.info(
            "METAR collection complete: stations=%d reports=%d",
            len(station_ids_by_icao),
            written,
        )
        return written
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_observations(settings))


if __name__ == "__main__":
    main()

