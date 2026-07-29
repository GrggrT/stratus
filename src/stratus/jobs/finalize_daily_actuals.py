from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import DailyActual, Observation, Station, WeatherKind
from stratus.db.session import session_factory_scope
from stratus.jobs.recover_observations import recover_observations
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


async def finalize_daily_actuals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    values: list[DailyActualValue] = []
    failures: list[tuple[int, date, str]] = []
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            stations = (
                (await session.execute(select(Station).where(Station.active.is_(True))))
                .scalars()
                .all()
            )
            for station in stations:
                local_today = now.astimezone(ZoneInfo(station.tz)).date()
                target_date = local_today - timedelta(days=1)
                existing_kinds = set(
                    (
                        await session.scalars(
                            select(DailyActual.kind).where(
                                DailyActual.station_id == station.id,
                                DailyActual.date == target_date,
                            )
                        )
                    ).all()
                )
                if existing_kinds == {WeatherKind.HIGH, WeatherKind.LOW}:
                    continue
                if existing_kinds:
                    failures.append(
                        (
                            station.id,
                            target_date,
                            f"{station.icao}/{target_date}: partial daily-actual pair conflict",
                        )
                    )
                    continue
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
                        points,
                        target_date=target_date,
                        timezone=station.tz,
                        min_samples=settings.daily_actual_min_samples,
                        boundary_tolerance=timedelta(
                            minutes=settings.daily_actual_boundary_tolerance_minutes
                        ),
                        max_gap=timedelta(minutes=settings.daily_actual_max_gap_minutes),
                    )
                except IncompleteObservationDayError as exc:
                    failures.append(
                        (station.id, target_date, f"{station.icao}/{target_date}: {exc}")
                    )
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

        recovered_rows = 0
        if settings.daily_actual_recovery_enabled:
            recovery = await recover_observations(settings, session_factory)
            recovered_rows = recovery.daily_actual_rows

        unresolved = failures
        if failures and recovered_rows:
            keys = {(station_id, target_date) for station_id, target_date, _ in failures}
            async with session_factory() as session:
                recovered_pairs = (
                    await session.execute(
                        select(DailyActual.station_id, DailyActual.date, DailyActual.kind).where(
                            DailyActual.station_id.in_({key[0] for key in keys}),
                            DailyActual.date.in_({key[1] for key in keys}),
                        )
                    )
                ).all()
            kinds_by_key: dict[tuple[int, date], set[WeatherKind]] = {}
            for station_id, actual_date, kind in recovered_pairs:
                kinds_by_key.setdefault((station_id, actual_date), set()).add(kind)
            unresolved = [
                failure
                for failure in failures
                if kinds_by_key.get((failure[0], failure[1]))
                != {WeatherKind.HIGH, WeatherKind.LOW}
            ]
        logger.info(
            "daily actual finalization complete: live_rows=%d recovered_rows=%d failures=%d",
            written,
            recovered_rows,
            len(unresolved),
        )
        if unresolved and not values:
            raise DailyActualFinalizationError(
                "all station days were incomplete: "
                + "; ".join(failure[2] for failure in unresolved)
            )
        if unresolved:
            logger.warning(
                "partial daily actual finalization failure isolated: %s",
                "; ".join(failure[2] for failure in unresolved),
            )
        return written + recovered_rows


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(finalize_daily_actuals(settings))


if __name__ == "__main__":
    main()
