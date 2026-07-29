from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.nws import NwsForecastClient
from stratus.collectors.open_meteo import (
    ForecastBundle,
    OpenMeteoClient,
    StationTarget,
)
from stratus.config import Settings, get_settings
from stratus.db.models import ForecastTrajectory, Station
from stratus.db.session import session_factory_scope
from stratus.services.forecast_collection import persist_forecast_bundle
from stratus.services.model_catalog import STATION_REGIONAL_MODELS
from stratus.services.pipeline_identity import probability_pipeline_fingerprint

logger = logging.getLogger(__name__)


class ForecastCollectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _CollectedBundle:
    station: StationTarget
    request_model: str
    collector: str
    run_source_url: str
    upstream_init_verified: bool
    bundle: ForecastBundle


async def fetch_forecasts(
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
            raise ForecastCollectionError("no active stations are configured")

        async with OpenMeteoClient(
            base_url=settings.open_meteo_base_url,
            forecast_days=settings.open_meteo_forecast_days,
            concurrency=settings.open_meteo_concurrency,
        ) as client:
            keys = [(station, model) for station in stations for model in settings.forecast_models]
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
            regional_keys = (
                [
                    (station, model)
                    for station in stations
                    for model in STATION_REGIONAL_MODELS.get(station.icao, ())
                ]
                if settings.accuracy_regional_shadow_enabled
                else []
            )
            regional_results = await asyncio.gather(
                *(
                    client.fetch_bundle(
                        station,
                        model,
                        run_time=snapshot_time,
                        fetched_at=fetched_at,
                    )
                    for station, model in regional_keys
                ),
                return_exceptions=True,
            )

        nws_targets = [station for station in stations if station.icao in settings.nws_stations]
        async with NwsForecastClient(
            base_url=settings.nws_base_url,
            user_agent=settings.nws_user_agent,
        ) as nws_client:
            nws_results = await asyncio.gather(
                *(
                    nws_client.fetch_bundle(station, fetched_at=fetched_at)
                    for station in nws_targets
                ),
                return_exceptions=True,
            )

        collected: list[_CollectedBundle] = []
        failures: list[str] = []
        for (station, model), bundle_result in zip(keys, results, strict=True):
            if isinstance(bundle_result, BaseException):
                failures.append(f"{station.icao}/{model}: {bundle_result}")
            else:
                collected.append(
                    _CollectedBundle(
                        station=station,
                        request_model=model,
                        collector="open_meteo_forecast_api",
                        run_source_url=settings.open_meteo_base_url,
                        upstream_init_verified=False,
                        bundle=bundle_result,
                    )
                )
        for (station, model), regional_result in zip(
            regional_keys, regional_results, strict=True
        ):
            if isinstance(regional_result, BaseException):
                failures.append(f"{station.icao}/{model}/shadow: {regional_result}")
            else:
                collected.append(
                    _CollectedBundle(
                        station=station,
                        request_model=model,
                        collector="open_meteo_forecast_api",
                        run_source_url=settings.open_meteo_base_url,
                        upstream_init_verified=False,
                        bundle=regional_result,
                    )
                )
        for station, nws_result in zip(nws_targets, nws_results, strict=True):
            if isinstance(nws_result, BaseException):
                failures.append(f"{station.icao}/nws: {nws_result}")
            else:
                collected.append(
                    _CollectedBundle(
                        station=station,
                        request_model="nws",
                        collector="nws_api",
                        run_source_url=settings.nws_base_url,
                        upstream_init_verified=True,
                        bundle=nws_result,
                    )
                )

        pipeline_fingerprint = probability_pipeline_fingerprint(settings)
        async with session_factory.begin() as session:
            written = 0
            trajectory_written = 0
            for item in collected:
                persisted = await persist_forecast_bundle(
                    session,
                    bundle=item.bundle,
                    station_id=item.station.id,
                    request_model=item.request_model,
                    collector=item.collector,
                    run_source_url=item.run_source_url,
                    pipeline_fingerprint=pipeline_fingerprint,
                    upstream_init_verified=item.upstream_init_verified,
                )
                written += persisted.forecast_rows
                trajectory_written += persisted.trajectory_rows
            await session.execute(
                delete(ForecastTrajectory).where(
                    ForecastTrajectory.available_at
                    < fetched_at - timedelta(days=settings.calibration_window_days)
                )
            )

        logger.info(
            "forecast snapshot complete: stations=%d requests=%d rows=%d "
            "trajectories=%d retention_days=%d failures=%d",
            len(stations),
            len(keys) + len(regional_keys) + len(nws_targets),
            written,
            trajectory_written,
            settings.calibration_window_days,
            len(failures),
        )
        if failures and not collected:
            raise ForecastCollectionError("all forecast sources failed: " + "; ".join(failures))
        if failures:
            logger.warning(
                "partial forecast collection failure isolated: %s",
                "; ".join(failures),
            )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_forecasts(settings))


if __name__ == "__main__":
    main()
