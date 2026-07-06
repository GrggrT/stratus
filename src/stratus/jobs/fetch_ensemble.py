from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select

from stratus.collectors.open_meteo import StationTarget
from stratus.collectors.open_meteo_ensemble import OpenMeteoEnsembleClient
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.forecasts import upsert_forecasts

logger = logging.getLogger(__name__)


class EnsembleCollectionError(RuntimeError):
    pass


async def fetch_ensemble(settings: Settings | None = None) -> int:
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
            raise EnsembleCollectionError("no active stations are configured")

        async with OpenMeteoEnsembleClient(
            base_url=settings.open_meteo_ensemble_url,
            forecast_days=settings.open_meteo_ensemble_forecast_days,
            concurrency=settings.open_meteo_ensemble_concurrency,
        ) as client:
            keys = [(station, model) for station in stations for model in settings.ensemble_models]
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

        values = []
        failures = []
        for (station, model), result in zip(keys, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{station.icao}/{model}: {result}")
            else:
                values.extend(result)
        async with session_factory.begin() as session:
            written = await upsert_forecasts(session, values)
        logger.info(
            "ensemble snapshot complete: stations=%d models=%d rows=%d failures=%d",
            len(stations),
            len(settings.ensemble_models),
            written,
            len(failures),
        )
        if failures:
            raise EnsembleCollectionError(
                "partial ensemble collection failure: " + "; ".join(failures)
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
    asyncio.run(fetch_ensemble(settings))


if __name__ == "__main__":
    main()

