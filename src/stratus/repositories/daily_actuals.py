from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import DailyActual, WeatherKind


@dataclass(frozen=True, slots=True)
class DailyActualValue:
    station_id: int
    date: date
    kind: WeatherKind
    temp_c: Decimal


async def upsert_daily_actuals(
    session: AsyncSession,
    values: list[DailyActualValue],
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
                "date": value.date,
                "kind": value.kind,
                "temp_c": value.temp_c,
            }
            for value in values[start : start + batch_size]
        ]
        statement = insert(DailyActual).values(rows)
        statement = statement.on_conflict_do_update(
            index_elements=["station_id", "date", "kind"],
            set_={"temp_c": statement.excluded.temp_c},
        )
        await session.execute(statement)
    return len(values)

