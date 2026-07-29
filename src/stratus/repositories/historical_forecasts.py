from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.open_meteo_previous_runs import HistoricalForecastValue
from stratus.db.models import HistoricalForecast, WeatherKind


class HistoricalForecastConflictError(RuntimeError):
    """A previous-runs identity was replayed with different training evidence."""


HistoricalIdentity = tuple[int, str, date, WeatherKind, int]


def _identity(value: HistoricalForecastValue) -> HistoricalIdentity:
    return (
        value.station_id,
        value.model,
        value.valid_date,
        value.kind,
        value.horizon_h,
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("historical forecast fetched_at must be timezone-aware")
    return value.astimezone(UTC)


def _temp(value: Decimal) -> Decimal:
    if not value.is_finite():
        raise ValueError("historical forecast temperature must be finite")
    return value.quantize(Decimal("0.01"))


def _signature(value: HistoricalForecastValue) -> tuple[Decimal, str, datetime]:
    return (_temp(value.temp_c), value.source, _aware_utc(value.fetched_at))


def _row_signature(row: HistoricalForecast) -> tuple[Decimal, str, datetime]:
    return (_temp(row.temp_c), row.source, _aware_utc(row.fetched_at))


async def upsert_historical_forecasts(
    session: AsyncSession,
    values: Sequence[HistoricalForecastValue],
    *,
    batch_size: int = 1000,
) -> int:
    """Insert-or-compare immutable historical training forecasts.

    Migration 0043 gives every new row a PostgreSQL ``first_seen_at`` and
    rejects updates/deletes.  Exact retries remain idempotent; a provider
    revision under the same natural identity is a typed conflict and requires
    a reviewed predictive fork rather than an in-place rewrite.
    """

    if not values:
        return 0
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    unique: dict[HistoricalIdentity, HistoricalForecastValue] = {}
    for value in values:
        key = _identity(value)
        previous = unique.get(key)
        if previous is not None and _signature(previous) != _signature(value):
            raise HistoricalForecastConflictError(
                f"conflicting historical forecast values in one batch: {key}"
            )
        unique[key] = value

    ordered = list(unique.values())
    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        rows = [
            {
                "station_id": value.station_id,
                "model": value.model,
                "valid_date": value.valid_date,
                "kind": value.kind,
                "horizon_h": value.horizon_h,
                "temp_c": _temp(value.temp_c),
                "source": value.source,
                "fetched_at": _aware_utc(value.fetched_at),
            }
            for value in batch
        ]
        await session.execute(
            insert(HistoricalForecast)
            .values(rows)
            .on_conflict_do_nothing(constraint="uq_historical_forecasts_identity")
        )
        keys = [_identity(value) for value in batch]
        stored = list(
            (
                await session.scalars(
                    select(HistoricalForecast).where(
                        tuple_(
                            HistoricalForecast.station_id,
                            HistoricalForecast.model,
                            HistoricalForecast.valid_date,
                            HistoricalForecast.kind,
                            HistoricalForecast.horizon_h,
                        ).in_(keys)
                    )
                )
            ).all()
        )
        by_identity = {
            (
                row.station_id,
                row.model,
                row.valid_date,
                row.kind,
                row.horizon_h,
            ): row
            for row in stored
        }
        for value in batch:
            key = _identity(value)
            row = by_identity.get(key)
            if row is None or _row_signature(row) != _signature(value):
                raise HistoricalForecastConflictError(
                    f"historical forecast identity conflicts with stored evidence: {key}"
                )
    return len(values)


__all__ = [
    "HistoricalForecastConflictError",
    "upsert_historical_forecasts",
]
