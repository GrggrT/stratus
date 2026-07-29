from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.open_meteo import OpenMeteoClient, StationTarget
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import session_factory_scope
from stratus.repositories.forecast_runs import upsert_forecast_run
from stratus.repositories.forecasts import upsert_forecasts
from stratus.services.model_catalog import (
    STATION_REGIONAL_MODELS,
    descriptor_for,
)

logger = logging.getLogger(__name__)
GLOBAL_MODELS = ("ecmwf", "gfs", "icon", "gem")


def _run_times(*, start: datetime, end: datetime, frequency_hours: int) -> list[datetime]:
    cursor = start.replace(minute=0, second=0, microsecond=0)
    while cursor.hour % frequency_hours:
        cursor += timedelta(hours=1)
    values: list[datetime] = []
    while cursor <= end:
        values.append(cursor)
        cursor += timedelta(hours=frequency_hours)
    return values


async def backfill_single_runs(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    """Backfill exact model initialisations without pretending latency is exact.

    Open-Meteo identifies the archived run exactly, but documents availability
    only as a model-specific delay. We therefore use the registry latency as a
    conservative proxy and persist that provenance on every run.
    """

    settings = settings or get_settings()
    retrieved_at = datetime.now(UTC)
    start = retrieved_at - timedelta(days=settings.accuracy_single_runs_backfill_days)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            rows = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars()
            stations = [
                StationTarget(
                    id=row.id,
                    icao=row.icao,
                    lat=float(row.lat),
                    lon=float(row.lon),
                    tz=row.tz,
                )
                for row in rows
            ]

        requests: list[tuple[StationTarget, str, datetime]] = []
        for station in stations:
            models = dict.fromkeys((*GLOBAL_MODELS, *STATION_REGIONAL_MODELS.get(station.icao, ())))
            for model in models:
                descriptor = descriptor_for(
                    model,
                    init_time=retrieved_at,
                    source_url=settings.open_meteo_single_runs_base_url,
                )
                frequency = int(descriptor.run_frequency_hours or 6)
                latency = descriptor.expected_latency_minutes or 360
                latest_available = retrieved_at - timedelta(minutes=latency)
                requests.extend(
                    (station, model, run_time)
                    for run_time in _run_times(
                        start=start,
                        end=latest_available,
                        frequency_hours=frequency,
                    )
                )
        if len(requests) > settings.accuracy_single_runs_max_requests:
            requests = requests[-settings.accuracy_single_runs_max_requests :]
            logger.warning(
                "single-runs request cap applied: retained newest %d calls",
                len(requests),
            )

        async with OpenMeteoClient(
            base_url=settings.open_meteo_single_runs_base_url,
            forecast_days=settings.open_meteo_forecast_days,
            concurrency=settings.open_meteo_concurrency,
        ) as client:
            results = await asyncio.gather(
                *(
                    client.fetch(
                        station,
                        model,
                        run_time=run_time,
                        fetched_at=retrieved_at,
                        exact_run=True,
                    )
                    for station, model, run_time in requests
                ),
                return_exceptions=True,
            )

        written = 0
        failures = 0
        async with session_factory.begin() as session:
            for (station, model, run_time), result in zip(requests, results, strict=True):
                if isinstance(result, BaseException):
                    failures += 1
                    logger.debug(
                        "single-runs unavailable: %s/%s/%s: %s",
                        station.icao,
                        model,
                        run_time.isoformat(),
                        result,
                    )
                    continue
                descriptor = descriptor_for(
                    model,
                    init_time=run_time,
                    source_url=settings.open_meteo_single_runs_base_url,
                )
                latency = descriptor.expected_latency_minutes or 360
                available_at = run_time + timedelta(minutes=latency)
                run_id = await upsert_forecast_run(
                    session,
                    descriptor=descriptor,
                    init_time=run_time,
                    available_at=available_at,
                    retrieved_at=retrieved_at,
                    source_url=settings.open_meteo_single_runs_base_url,
                    status="archived",
                    metadata={
                        "init_time_semantics": "upstream_exact_run",
                        "upstream_init_verified": True,
                        "availability_semantics": "documented_expected_latency_proxy",
                        "upstream_availability_verified": False,
                    },
                )
                written += await upsert_forecasts(
                    session,
                    [replace(value, run_id=run_id, available_at=available_at) for value in result],
                )
        logger.info(
            "single-runs backfill complete: requests=%d failures=%d rows=%d",
            len(requests),
            failures,
            written,
        )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(backfill_single_runs(settings))


if __name__ == "__main__":
    main()
