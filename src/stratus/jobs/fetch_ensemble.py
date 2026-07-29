from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.open_meteo import ForecastBundle, StationTarget
from stratus.collectors.open_meteo_ensemble import OpenMeteoEnsembleClient
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import session_factory_scope
from stratus.services.forecast_collection import persist_forecast_bundle
from stratus.services.pipeline_identity import probability_pipeline_fingerprint

logger = logging.getLogger(__name__)


class EnsembleCollectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _CollectedEnsembleBundle:
    station: StationTarget
    request_model: str
    bundle: ForecastBundle


async def fetch_ensemble(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    snapshot_time = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    fetched_at = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
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
                    client.fetch_bundle(
                        station,
                        model,
                        run_time=snapshot_time,
                        fetched_at=fetched_at,
                    )
                    for station, model in keys
                ),
                return_exceptions=True,
            )

        collected: list[_CollectedEnsembleBundle] = []
        failures: list[str] = []
        for (station, model), result in zip(keys, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(f"{station.icao}/{model}: {result}")
            else:
                collected.append(
                    _CollectedEnsembleBundle(
                        station=station,
                        request_model=model,
                        bundle=result,
                    )
                )
        pipeline_fingerprint = probability_pipeline_fingerprint(settings)
        async with session_factory.begin() as session:
            written = 0
            for item in collected:
                persisted = await persist_forecast_bundle(
                    session,
                    bundle=item.bundle,
                    station_id=item.station.id,
                    request_model=item.request_model,
                    collector="open_meteo_ensemble_api",
                    run_source_url=settings.open_meteo_ensemble_url,
                    pipeline_fingerprint=pipeline_fingerprint,
                    upstream_init_verified=False,
                )
                written += persisted.forecast_rows
        logger.info(
            "ensemble snapshot complete: stations=%d models=%d rows=%d failures=%d",
            len(stations),
            len(settings.ensemble_models),
            written,
            len(failures),
        )
        if failures and not collected:
            raise EnsembleCollectionError("all ensemble sources failed: " + "; ".join(failures))
        if failures:
            logger.warning(
                "partial ensemble collection failure isolated: %s",
                "; ".join(failures),
            )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_ensemble(settings))


if __name__ == "__main__":
    main()
