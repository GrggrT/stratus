from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import Calibration, WeatherKind


@dataclass(frozen=True, slots=True)
class CalibrationValue:
    station_id: int
    model: str
    horizon_bucket: str
    kind: WeatherKind
    bias_c: Decimal
    sigma_c: Decimal
    emos_a: Decimal
    emos_b: Decimal
    emos_c: Decimal
    emos_d: Decimal
    sample_n: int
    updated_at: datetime


async def upsert_calibrations(
    session: AsyncSession, values: list[CalibrationValue]
) -> int:
    if not values:
        return 0
    rows = [asdict(value) for value in values]
    statement = insert(Calibration).values(rows)
    statement = statement.on_conflict_do_update(
        index_elements=["station_id", "model", "horizon_bucket", "kind"],
        set_={
            "bias_c": statement.excluded.bias_c,
            "sigma_c": statement.excluded.sigma_c,
            "emos_a": statement.excluded.emos_a,
            "emos_b": statement.excluded.emos_b,
            "emos_c": statement.excluded.emos_c,
            "emos_d": statement.excluded.emos_d,
            "sample_n": statement.excluded.sample_n,
            "updated_at": statement.excluded.updated_at,
        },
    )
    await session.execute(statement)
    return len(values)
