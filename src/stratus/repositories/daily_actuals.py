from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import DailyActual, WeatherKind


@dataclass(frozen=True, slots=True)
class DailyActualValue:
    station_id: int
    date: date
    kind: WeatherKind
    temp_c: Decimal


class DailyActualConflictError(RuntimeError):
    """A persisted station/date/kind truth disagrees with a new value."""


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
    canonical: dict[tuple[int, date, WeatherKind], DailyActualValue] = {}
    for value in values:
        key = (value.station_id, value.date, value.kind)
        previous = canonical.get(key)
        if previous is not None and previous.temp_c != value.temp_c:
            raise DailyActualConflictError(
                "daily actual batch contains conflicting values for "
                f"station={value.station_id} date={value.date} kind={value.kind.value}"
            )
        canonical[key] = value
    keys = sorted(canonical, key=lambda item: (item[0], item[1], item[2].value))
    ordered = [canonical[key] for key in keys]
    try:
        async with session.begin_nested():
            for start in range(0, len(ordered), batch_size):
                for value in ordered[start : start + batch_size]:
                    key_text = (
                        f"daily-actual:{value.station_id}:"
                        f"{value.date.isoformat()}:{value.kind.value}"
                    )
                    await session.execute(
                        text(
                            "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
                        ),
                        {"key": key_text},
                    )
                    existing = await session.scalar(
                        select(DailyActual)
                        .where(
                            DailyActual.station_id == value.station_id,
                            DailyActual.date == value.date,
                            DailyActual.kind == value.kind,
                        )
                        .with_for_update()
                    )
                    if existing is None:
                        session.add(
                            DailyActual(
                                station_id=value.station_id,
                                date=value.date,
                                kind=value.kind,
                                temp_c=value.temp_c,
                            )
                        )
                    elif existing.temp_c != value.temp_c:
                        raise DailyActualConflictError(
                            "daily actual is immutable and conflicts with persisted truth: "
                            f"station={value.station_id} date={value.date} "
                            f"kind={value.kind.value} stored={existing.temp_c} "
                            f"candidate={value.temp_c}"
                        )
            await session.flush()
    except IntegrityError as exc:
        raise DailyActualConflictError(
            "concurrent daily actual insert conflicted with persisted truth"
        ) from exc
    return len(values)


__all__ = [
    "DailyActualConflictError",
    "DailyActualValue",
    "upsert_daily_actuals",
]
