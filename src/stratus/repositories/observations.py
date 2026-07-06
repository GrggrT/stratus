from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.aviation_weather import MetarObservation
from stratus.db.models import Observation


def build_observation_upsert(values: Sequence[MetarObservation]) -> Insert:
    rows = [
        {
            "station_id": value.station_id,
            "obs_time": value.obs_time,
            "temp_c": value.temp_c,
            "raw_metar": value.raw_metar,
        }
        for value in values
    ]
    statement = insert(Observation).values(rows)
    return statement.on_conflict_do_update(
        constraint="uq_observations_station_time",
        set_={
            "temp_c": statement.excluded.temp_c,
            "raw_metar": statement.excluded.raw_metar,
        },
    )


async def upsert_observations(
    session: AsyncSession,
    values: Sequence[MetarObservation],
    *,
    batch_size: int = 1000,
) -> int:
    if not values:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(values), batch_size):
        await session.execute(build_observation_upsert(values[start : start + batch_size]))
    return len(values)
