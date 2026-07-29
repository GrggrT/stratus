from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.open_meteo import ForecastTrajectoryValue
from stratus.db.models import ForecastTrajectory


async def upsert_forecast_trajectories(
    session: AsyncSession,
    values: Sequence[ForecastTrajectoryValue],
    *,
    batch_size: int = 500,
) -> int:
    if not values:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(values), batch_size):
        rows = [
            {
                "station_id": value.station_id,
                "model": value.model,
                "run_time": value.run_time,
                "valid_date": value.valid_date,
                "points": [
                    {"local_time": local_time, "temp_c": float(temp_c)}
                    for local_time, temp_c in value.points
                ],
                "fetched_at": value.fetched_at,
                "run_id": value.run_id,
                "ingest_id": value.ingest_id,
                "available_at": value.available_at or value.fetched_at,
            }
            for value in values[start : start + batch_size]
        ]
        statement = insert(ForecastTrajectory).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_forecast_trajectory_identity",
            set_={
                "points": statement.excluded.points,
                "fetched_at": statement.excluded.fetched_at,
                "run_id": statement.excluded.run_id,
                "ingest_id": statement.excluded.ingest_id,
                "available_at": statement.excluded.available_at,
            },
        )
        await session.execute(statement)
    return len(values)
