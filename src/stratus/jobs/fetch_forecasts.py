from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from stratus.collectors.nws import NwsForecastClient
from stratus.collectors.open_meteo import OpenMeteoClient, StationTarget
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.forecasts import upsert_forecasts

logger = logging.getLogger(__name__)


class ForecastCollectionError(RuntimeError):
    pass


async def fetch_forecasts(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    snapshot_time = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    fetched_at = datetime.now(UTC)
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    try:
        async with session_factory() as session:
            station_rows = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars()
            stations = [
                StationTarget(
                    id=station.id,
                    icao=station.icao,
                    lat=float(station.lat),
                    lon=float(station.lon),
                    tz=station.tz,
                )
                for station in station_rows
            ]

        if not stations:
            raise ForecastCollectionError("no active stations are configured")

        async with OpenMeteoClient(
            base_url=settings.open_meteo_base_url,
            forecast_days=settings.open_meteo_forecast_days,
            concurrency=settings.open_meteo_concurrency,
        ) as client:
            keys = [(station, model) for station in stations for model in settings.forecast_models]
            results = await asyncio.gather(
                *(
                    client.fetch(
                        station,
                        model,
                        run_time=snapshot_time,
                        fetched_at=fetched_at,
                    )
                    for station, model in keys
                ),
                return_exceptions=True,
            )

        nws_targets = [station for station in stations if station.icao in settings.nws_stations]
        async with NwsForecastClient(
            base_url=settings.nws_base_url,
            user_agent=settings.nws_user_agent,
        ) as nws_client:
            nws_results = await asyncio.gather(
                *(nws_client.fetch(station, fetched_at=fetched_at) for station in nws_targets),
                return_exceptions=True,
            )

        values = []
        failures = []
        for (station, model), result in zip(keys, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{station.icao}/{model}: {result}")
            else:
                values.extend(result)
        for station, result in zip(nws_targets, nws_results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{station.icao}/nws: {result}")
            else:
                values.extend(result)

        async with session_factory.begin() as session:
            written = await upsert_forecasts(session, values)

        logger.info(
            "forecast snapshot complete: stations=%d requests=%d rows=%d failures=%d",
            len(stations),
            len(keys) + len(nws_targets),
            written,
            len(failures),
        )
        if failures:
            raise ForecastCollectionError(
                "partial forecast collection failure: " + "; ".join(failures)
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
    asyncio.run(fetch_forecasts(settings))


if __name__ == "__main__":
    main()
