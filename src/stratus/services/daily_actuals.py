from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from zoneinfo import ZoneInfo


class IncompleteObservationDayError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ObservationPoint:
    obs_time: datetime
    temp_c: Decimal


@dataclass(frozen=True, slots=True)
class DailyExtremes:
    high_c: Decimal
    low_c: Decimal
    sample_n: int


def utc_bounds_for_local_date(target_date: date, timezone: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone)
    start = datetime.combine(target_date, time.min, tzinfo=tz)
    end = datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)


def compute_daily_extremes(
    points: list[ObservationPoint],
    *,
    target_date: date,
    timezone: str,
    min_samples: int = 20,
    boundary_tolerance: timedelta = timedelta(minutes=90),
    max_gap: timedelta = timedelta(hours=2),
) -> DailyExtremes:
    start, end = utc_bounds_for_local_date(target_date, timezone)
    selected = sorted(
        (point for point in points if start <= point.obs_time < end),
        key=lambda point: point.obs_time,
    )
    if len(selected) < min_samples:
        raise IncompleteObservationDayError(
            f"only {len(selected)} observations; at least {min_samples} required"
        )
    if selected[0].obs_time - start > boundary_tolerance:
        raise IncompleteObservationDayError("observations do not cover the start of the local day")
    if end - selected[-1].obs_time > boundary_tolerance:
        raise IncompleteObservationDayError("observations do not cover the end of the local day")
    largest_gap = max(
        (right.obs_time - left.obs_time for left, right in pairwise(selected)),
        default=timedelta(0),
    )
    if largest_gap > max_gap:
        raise IncompleteObservationDayError(
            f"largest observation gap is {largest_gap}; maximum allowed is {max_gap}"
        )
    temperatures = [point.temp_c for point in selected]
    return DailyExtremes(
        high_c=max(temperatures), low_c=min(temperatures), sample_n=len(selected)
    )
