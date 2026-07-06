from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.open_meteo_previous_runs import HistoricalForecastValue
from stratus.db.models import HistoricalForecast


async def upsert_historical_forecasts(
    session: AsyncSession,
    values: Sequence[HistoricalForecastValue],
    *,
    batch_size: int = 1000,
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
                "valid_date": value.valid_date,
                "kind": value.kind,
                "horizon_h": value.horizon_h,
                "temp_c": value.temp_c,
                "source": value.source,
                "fetched_at": value.fetched_at,
            }
            for value in values[start : start + batch_size]
        ]
        statement = insert(HistoricalForecast).values(rows)
        statement = statement.on_conflict_do_update(
            constraint="uq_historical_forecasts_identity",
            set_={
                "temp_c": statement.excluded.temp_c,
                "source": statement.excluded.source,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
        await session.execute(statement)
    return len(values)

