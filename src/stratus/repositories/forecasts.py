from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.open_meteo import ForecastValue
from stratus.db.models import Forecast


def build_forecast_upsert(values: Sequence[ForecastValue]) -> Insert:
    rows = [
        {
            "station_id": value.station_id,
            "model": value.model,
            "run_time": value.run_time,
            "valid_date": value.valid_date,
            "kind": value.kind,
            "temp_c": value.temp_c,
            "fetched_at": value.fetched_at,
        }
        for value in values
    ]
    statement = insert(Forecast).values(rows)
    return statement.on_conflict_do_update(
        constraint="uq_forecasts_identity",
        set_={"temp_c": statement.excluded.temp_c, "fetched_at": statement.excluded.fetched_at},
    )


async def upsert_forecasts(
    session: AsyncSession,
    values: Sequence[ForecastValue],
    *,
    batch_size: int = 1000,
) -> int:
    if not values:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    for start in range(0, len(values), batch_size):
        await session.execute(build_forecast_upsert(values[start : start + batch_size]))
    return len(values)
