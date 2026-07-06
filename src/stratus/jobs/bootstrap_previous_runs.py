from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from stratus.collectors.iem import year_chunks
from stratus.collectors.open_meteo import MODEL_API_IDS, StationTarget
from stratus.collectors.open_meteo_previous_runs import OpenMeteoPreviousRunsClient
from stratus.config import Settings, get_settings
from stratus.db.models import DailyActual, Station
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.historical_forecasts import upsert_historical_forecasts

logger = logging.getLogger(__name__)


async def bootstrap_previous_runs(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    fetched_at = datetime.now(UTC)
    try:
        async with session_factory() as session:
            stations = (
                await session.execute(select(Station).where(Station.active.is_(True)))
            ).scalars().all()
            date_bounds = (
                await session.execute(
                    select(DailyActual.date)
                    .order_by(DailyActual.date)
                    .limit(1)
                )
            ).scalar_one_or_none()
        if not stations or date_bounds is None:
            raise RuntimeError("active stations and daily actual history are required")
        requested_start = datetime.now(UTC).date() - timedelta(
            days=365 * settings.previous_runs_bootstrap_years + 2
        )
        start = max(requested_start, date_bounds)
        end_exclusive = datetime.now(UTC).date()
        chunks = year_chunks(start, end_exclusive)

        targets = [
            (
                StationTarget(
                    id=station.id,
                    icao=station.icao,
                    lat=float(station.lat),
                    lon=float(station.lon),
                    tz=station.tz,
                ),
                model,
                chunk_start,
                chunk_end,
            )
            for station in stations
            for model in MODEL_API_IDS
            for chunk_start, chunk_end in chunks
        ]
        imported = 0
        failures: list[str] = []
        async with OpenMeteoPreviousRunsClient(
            base_url=settings.open_meteo_previous_runs_url,
            concurrency=settings.previous_runs_concurrency,
        ) as client:
            for batch_start in range(0, len(targets), settings.previous_runs_concurrency):
                batch = targets[
                    batch_start : batch_start + settings.previous_runs_concurrency
                ]
                results = await asyncio.gather(
                    *(
                        client.fetch(
                            station,
                            model,
                            start_date=chunk_start,
                            end_date=chunk_end - date.resolution,
                            horizon_days=settings.previous_run_horizon_days,
                            fetched_at=fetched_at,
                        )
                        for station, model, chunk_start, chunk_end in batch
                    ),
                    return_exceptions=True,
                )
                for target, result in zip(batch, results, strict=True):
                    station, model, chunk_start, chunk_end = target
                    if isinstance(result, BaseException):
                        failures.append(
                            f"{station.icao}/{model}/{chunk_start}:{chunk_end}: {result}"
                        )
                        continue
                    async with session_factory.begin() as session:
                        imported += await upsert_historical_forecasts(session, result)
                    logger.info(
                        "previous-runs chunk complete: station=%s model=%s "
                        "start=%s end=%s rows=%d",
                        station.icao,
                        model,
                        chunk_start,
                        chunk_end,
                        len(result),
                    )
        if failures:
            raise RuntimeError("partial previous-runs failure: " + "; ".join(failures))
        logger.info("previous-runs bootstrap complete: rows=%d", imported)
        return imported
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(bootstrap_previous_runs(settings))


if __name__ == "__main__":
    main()

