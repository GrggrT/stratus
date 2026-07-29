from __future__ import annotations

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from stratus.services.emos import horizon_bucket

HORIZON_ANCHORS_H = {
    "0-6h": 3.0,
    "6-24h": 12.0,
    "24-48h": 36.0,
    "48h+": 72.0,
}


@dataclass(frozen=True, slots=True)
class ProbabilityPoint:
    id: int
    market_id: int
    ts: datetime
    probability: float
    horizon_h: float


@dataclass(frozen=True, slots=True)
class PricePoint:
    market_id: int
    ts: datetime
    midpoint: float
    best_bid: float
    best_ask: float


def select_horizon_points(points: list[ProbabilityPoint]) -> list[ProbabilityPoint]:
    selected: dict[tuple[int, str], ProbabilityPoint] = {}
    for point in points:
        bucket = horizon_bucket(round(point.horizon_h))
        key = (point.market_id, bucket)
        current = selected.get(key)
        anchor = HORIZON_ANCHORS_H[bucket]
        if current is None or abs(point.horizon_h - anchor) < abs(current.horizon_h - anchor):
            selected[key] = point
    return sorted(selected.values(), key=lambda point: (point.market_id, point.horizon_h))


class PriceTimeline:
    def __init__(self, prices: list[PricePoint]) -> None:
        grouped: dict[int, list[PricePoint]] = defaultdict(list)
        for price in prices:
            grouped[price.market_id].append(price)
        self._prices = {
            market_id: sorted(rows, key=lambda row: row.ts) for market_id, rows in grouped.items()
        }
        self._times = {
            market_id: [row.ts for row in rows] for market_id, rows in self._prices.items()
        }

    def latest_at(self, market_id: int, at: datetime) -> PricePoint | None:
        rows = self._prices.get(market_id)
        if not rows:
            return None
        index = bisect.bisect_right(self._times[market_id], at) - 1
        return rows[index] if index >= 0 else None


@dataclass(frozen=True, slots=True)
class DualPricePoint:
    """One immutable, independently observed YES/NO market-book pair."""

    id: int
    batch_id: int
    market_id: int
    observed_at: datetime
    record_hash: str
    binary_yes_probability: float | None
    yes_midpoint: float | None
    yes_best_bid: float | None
    yes_best_ask: float | None
    no_midpoint: float | None
    no_best_bid: float | None
    no_best_ask: float | None
    eligible: bool


class DualPriceTimeline:
    """Point-in-time lookup over immutable dual-token snapshots.

    Batch identity is retained so an event benchmark can require every sibling
    bucket to have been observed in the same CLOB response.  Combining latest
    rows from unrelated requests would create a price vector that never
    existed and is therefore deliberately unsupported.
    """

    def __init__(self, prices: list[DualPricePoint]) -> None:
        grouped: dict[int, list[DualPricePoint]] = defaultdict(list)
        by_batch_market: dict[tuple[int, int], DualPricePoint | None] = {}
        self._by_id: dict[int, DualPricePoint] = {}
        for price in prices:
            grouped[price.market_id].append(price)
            self._by_id[price.id] = price
            key = (price.batch_id, price.market_id)
            # The database has a unique constraint for this key.  Treat an
            # in-memory duplicate as ambiguous rather than silently picking a
            # row, which also keeps unit callers fail-closed.
            by_batch_market[key] = price if key not in by_batch_market else None
        self._prices = {
            market_id: sorted(rows, key=lambda row: row.observed_at)
            for market_id, rows in grouped.items()
        }
        self._times = {
            market_id: [row.observed_at for row in rows]
            for market_id, rows in self._prices.items()
        }
        self._by_batch_market = by_batch_market

    def get(self, snapshot_id: int) -> DualPricePoint | None:
        return self._by_id.get(snapshot_id)

    def in_batch(self, batch_id: int, market_id: int) -> DualPricePoint | None:
        return self._by_batch_market.get((batch_id, market_id))

    def latest_at(
        self, market_id: int, at: datetime, *, eligible_only: bool = True
    ) -> DualPricePoint | None:
        rows = self._prices.get(market_id)
        if not rows:
            return None
        index = bisect.bisect_right(self._times[market_id], at) - 1
        while index >= 0:
            row = rows[index]
            if not eligible_only or row.eligible:
                return row
            index -= 1
        return None


@dataclass(frozen=True, slots=True)
class EventMarketBenchmark:
    normalized_probability: float | None
    raw_total: float | None
    overround: float | None
    own_price: PricePoint | None
    complete: bool


@dataclass(frozen=True, slots=True)
class DualEventMarketBenchmark:
    normalized_probability: float | None
    raw_total: float | None
    overround: float | None
    own_price: DualPricePoint | None
    complete: bool


def dual_event_market_benchmark(
    *,
    event_market_ids: list[int],
    target_market_id: int,
    target_snapshot_id: int,
    at: datetime,
    prices: DualPriceTimeline,
    max_age_seconds: float = 1800,
) -> DualEventMarketBenchmark:
    """Build a same-response executable-market benchmark.

    ``target_snapshot_id`` is the snapshot bound to the model probability at
    decision time.  All sibling buckets must come from that exact batch and
    be individually dual-token eligible.  This prevents both look-ahead and
    synthetic cross-request event vectors.
    """

    expected_ids = set(event_market_ids)
    target = prices.get(target_snapshot_id)
    if (
        target is None
        or target.market_id != target_market_id
        or target_market_id not in expected_ids
        or len(expected_ids) != len(event_market_ids)
    ):
        return DualEventMarketBenchmark(None, None, None, target, False)
    age_seconds = (at - target.observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > max_age_seconds:
        return DualEventMarketBenchmark(None, None, None, target, False)

    snapshots = [prices.in_batch(target.batch_id, market_id) for market_id in event_market_ids]
    complete = all(
        snapshot is not None
        and snapshot.eligible
        and snapshot.binary_yes_probability is not None
        and 0 <= (at - snapshot.observed_at).total_seconds() <= max_age_seconds
        for snapshot in snapshots
    )
    if not complete:
        return DualEventMarketBenchmark(None, None, None, target, False)
    valid = [snapshot for snapshot in snapshots if snapshot is not None]
    probabilities: list[float] = []
    for snapshot in valid:
        assert snapshot.binary_yes_probability is not None
        probabilities.append(snapshot.binary_yes_probability)
    total = sum(probabilities)
    if total <= 0:
        return DualEventMarketBenchmark(None, total, total - 1, target, False)
    own = next(snapshot for snapshot in valid if snapshot.market_id == target_market_id)
    assert own.binary_yes_probability is not None
    return DualEventMarketBenchmark(
        float(own.binary_yes_probability) / total,
        total,
        total - 1,
        own,
        True,
    )


def event_market_benchmark(
    *,
    event_market_ids: list[int],
    target_market_id: int,
    at: datetime,
    prices: PriceTimeline,
    max_age_seconds: float = 1800,
) -> EventMarketBenchmark:
    snapshots = [prices.latest_at(market_id, at) for market_id in event_market_ids]
    complete = all(
        snapshot is not None and 0 <= (at - snapshot.ts).total_seconds() <= max_age_seconds
        for snapshot in snapshots
    )
    own = prices.latest_at(target_market_id, at)
    if not complete:
        return EventMarketBenchmark(None, None, None, own, False)
    valid = [snapshot for snapshot in snapshots if snapshot is not None]
    total = sum(snapshot.midpoint for snapshot in valid)
    if total <= 0:
        return EventMarketBenchmark(None, total, total - 1, own, False)
    target = next(snapshot for snapshot in valid if snapshot.market_id == target_market_id)
    return EventMarketBenchmark(target.midpoint / total, total, total - 1, own, True)
