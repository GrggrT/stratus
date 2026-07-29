from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.aviation_weather import AviationWeatherClient, AviationWeatherError
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import session_factory_scope
from stratus.repositories.observations import upsert_observations

logger = logging.getLogger(__name__)


async def fetch_observations(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            stations = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars()
            station_ids_by_icao = {station.icao: station.id for station in stations}
        if not station_ids_by_icao:
            raise RuntimeError("no active stations are configured")

        station_items = list(station_ids_by_icao.items())
        batches = [
            dict(station_items[index : index + settings.observation_batch_size])
            for index in range(0, len(station_items), settings.observation_batch_size)
        ]
        async with AviationWeatherClient(
            metar_url=settings.aviation_weather_metar_url,
            user_agent=settings.aviation_weather_user_agent,
            lookback_hours=settings.observation_lookback_hours,
        ) as client:
            results = await asyncio.gather(
                *(client.fetch_metars(batch) for batch in batches),
                return_exceptions=True,
            )

        observations = []
        failures = []
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{','.join(sorted(batch))}: {result}")
            else:
                observations.extend(result)
        if failures and len(failures) == len(batches):
            raise AviationWeatherError("all METAR batches failed: " + "; ".join(failures))
        if failures:
            logger.warning(
                "partial METAR collection failure isolated: %s",
                "; ".join(failures),
            )

        async with session_factory.begin() as session:
            written = await upsert_observations(session, observations)
        logger.info(
            "METAR collection complete: stations=%d reports=%d failed_batches=%d",
            len(station_ids_by_icao),
            written,
            len(failures),
        )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_observations(settings))


if __name__ == "__main__":
    main()
