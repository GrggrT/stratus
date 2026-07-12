from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime

# Deterministic bootstrap: analytics must be reproducible across calls so the
# gate cannot flap between evaluations on identical data.
BOOTSTRAP_SEED = 20260712
BOOTSTRAP_RESAMPLES = 10_000


@dataclass(frozen=True, slots=True)
class PositionStat:
    closed: bool
    pnl_usd: float | None
    clv: float | None
    cost_usd: float
    fee_usd: float | None
    # Needed for the skill/luck decomposition; older callers may omit them,
    # in which case the position simply does not decompose.
    size_shares: float | None = None
    avg_price: float | None = None


@dataclass(frozen=True, slots=True)
class LivePerformance:
    positions: int
    closed: int
    open: int
    wins: int
    total_pnl_usd: float
    robust_pnl_usd: float | None
    total_cost_usd: float
    total_fees_usd: float
    roi: float | None
    robust_roi: float | None
    avg_clv: float | None
    clv_beat_rate: float | None
    # Skill/luck decomposition over closed positions that have a CLV. Skill is
    # what we earned relative to the market close (information + entry
    # quality); luck is the close-to-resolution variance we do not control.
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    decomposed: int
    roi_ci_low: float | None
    roi_ci_high: float | None
    skill_pnl_ci_low: float | None
    skill_pnl_ci_high: float | None

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.closed if self.closed else None

    @property
    def statistically_positive(self) -> bool | None:
        """True only when both the ROI and the skill CI exclude zero from
        below. None while either interval is unavailable."""
        if self.roi_ci_low is None or self.skill_pnl_ci_low is None:
            return None
        return self.roi_ci_low > 0 and self.skill_pnl_ci_low > 0


@dataclass(frozen=True, slots=True)
class EquityPoint:
    ts: datetime
    cumulative_pnl_usd: float


def cumulative_equity(
    realized: list[tuple[datetime, float]],
) -> list[EquityPoint]:
    """Running realised PnL over closed live positions, ordered by close time.
    Each point is the cumulative PnL after that position settled."""
    running = 0.0
    points = []
    for ts, pnl in sorted(realized, key=lambda item: item[0]):
        running += pnl
        points.append(EquityPoint(ts=ts, cumulative_pnl_usd=round(running, 4)))
    return points


def decompose_pnl(stat: PositionStat) -> tuple[float, float] | None:
    """Split a closed position's PnL into (skill, luck).

    skill = shares x (close - entry) - fees   [shares x clv - fees]
    luck  = pnl - skill = shares x (outcome - close), up to fill rounding.

    The market close price is the non-leaking pre-resolution midpoint already
    captured as CLV, so the split needs no extra data. Positions without a CLV
    or share count do not decompose.
    """
    if (
        not stat.closed
        or stat.pnl_usd is None
        or stat.clv is None
        or stat.size_shares is None
        or stat.size_shares <= 0
    ):
        return None
    skill = stat.size_shares * stat.clv - (stat.fee_usd or 0.0)
    return skill, stat.pnl_usd - skill


def bootstrap_sum_ci(
    values: list[float],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """Percentile 2.5/97.5 interval for sum(values) under resampling with
    replacement. Deterministic for a given seed."""
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    sums = sorted(
        sum(values[rng.randrange(n)] for _ in range(n)) for _ in range(resamples)
    )
    return _percentile(sums, 0.025), _percentile(sums, 0.975)


def bootstrap_ratio_ci(
    pairs: list[tuple[float, float]],
    *,
    resamples: int = BOOTSTRAP_RESAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | None:
    """Percentile CI for sum(numerator)/sum(denominator) — e.g. ROI as
    sum(pnl)/sum(cost) — under resampling pairs with replacement."""
    if len(pairs) < 2:
        return None
    rng = random.Random(seed)
    n = len(pairs)
    ratios = []
    for _ in range(resamples):
        numerator = 0.0
        denominator = 0.0
        for _ in range(n):
            pair = pairs[rng.randrange(n)]
            numerator += pair[0]
            denominator += pair[1]
        if denominator > 0:
            ratios.append(numerator / denominator)
    if len(ratios) < 2:
        return None
    ratios.sort()
    return _percentile(ratios, 0.025), _percentile(ratios, 0.975)


def _percentile(sorted_values: list[float], q: float) -> float:
    index = q * (len(sorted_values) - 1)
    low = int(index)
    high = min(low + 1, len(sorted_values) - 1)
    fraction = index - low
    return round(
        sorted_values[low] * (1 - fraction) + sorted_values[high] * fraction, 6
    )


def summarize_live_performance(positions: list[PositionStat]) -> LivePerformance:
    """Retrospective on realised trading.

    ROI is realised PnL over the cost of closed positions (not bankroll, so a
    partial deployment is not flattered). CLV beat-rate is the share of closed
    positions with a recorded closing-line value that entered better than the
    close. skill_pnl (PnL relative to the market close) is the primary KPI:
    a positive raw ROI with non-positive skill is variance, not edge.
    """
    closed = [p for p in positions if p.closed]
    clv_values = [p.clv for p in closed if p.clv is not None]
    closed_cost = sum(p.cost_usd for p in closed)
    total_pnl = sum(p.pnl_usd or 0.0 for p in closed)
    robust_pnl: float | None = None
    robust_roi: float | None = None
    if len(closed) >= 2:
        largest = max(closed, key=lambda p: abs(p.pnl_usd or 0.0))
        robust_cost = closed_cost - largest.cost_usd
        robust_pnl = total_pnl - (largest.pnl_usd or 0.0)
        robust_roi = robust_pnl / robust_cost if robust_cost > 0 else None

    decompositions = [
        split for split in (decompose_pnl(p) for p in closed) if split is not None
    ]
    skill_values = [skill for skill, _ in decompositions]
    skill_pnl = sum(skill_values) if decompositions else None
    luck_pnl = sum(luck for _, luck in decompositions) if decompositions else None
    roi_ci = bootstrap_ratio_ci(
        [(p.pnl_usd or 0.0, p.cost_usd) for p in closed if p.cost_usd > 0]
    )
    skill_ci = bootstrap_sum_ci(skill_values)

    return LivePerformance(
        positions=len(positions),
        closed=len(closed),
        open=len(positions) - len(closed),
        wins=sum(1 for p in closed if (p.pnl_usd or 0.0) > 0),
        total_pnl_usd=round(total_pnl, 4),
        robust_pnl_usd=round(robust_pnl, 4) if robust_pnl is not None else None,
        total_cost_usd=round(sum(p.cost_usd for p in positions), 4),
        total_fees_usd=round(sum(p.fee_usd or 0.0 for p in positions), 5),
        roi=round(total_pnl / closed_cost, 6) if closed_cost > 0 else None,
        robust_roi=round(robust_roi, 6) if robust_roi is not None else None,
        avg_clv=round(sum(clv_values) / len(clv_values), 6) if clv_values else None,
        clv_beat_rate=(
            round(sum(1 for v in clv_values if v > 0) / len(clv_values), 4)
            if clv_values
            else None
        ),
        skill_pnl_usd=round(skill_pnl, 4) if skill_pnl is not None else None,
        luck_pnl_usd=round(luck_pnl, 4) if luck_pnl is not None else None,
        decomposed=len(decompositions),
        roi_ci_low=roi_ci[0] if roi_ci else None,
        roi_ci_high=roi_ci[1] if roi_ci else None,
        skill_pnl_ci_low=skill_ci[0] if skill_ci else None,
        skill_pnl_ci_high=skill_ci[1] if skill_ci else None,
    )


@dataclass(frozen=True, slots=True)
class TradeOutcome:
    """One resolved order, side-adjusted: probabilities and the outcome are
    expressed for the side we actually bought."""

    model_prob: float
    market_prob: float
    won: bool
    pnl_usd: float
    cost_usd: float
    predicted_edge: float
    raw_model_prob: float | None = None
    station_icao: str | None = None
    kind: str | None = None
    horizon_bucket: str | None = None
    confidence: str | None = None
    side: str | None = None

    @property
    def realized_return(self) -> float:
        return self.pnl_usd / self.cost_usd if self.cost_usd > 0 else 0.0


def brier_comparison(trades: list[TradeOutcome]) -> tuple[float, float] | None:
    """(brier_model, brier_market) over resolved trades. An informational
    edge exists only while brier_model < brier_market."""
    if not trades:
        return None
    model = sum((t.model_prob - float(t.won)) ** 2 for t in trades) / len(trades)
    market = sum((t.market_prob - float(t.won)) ** 2 for t in trades) / len(trades)
    return round(model, 6), round(market, 6)


def raw_model_brier(trades: list[TradeOutcome]) -> tuple[float, int] | None:
    """Brier for the pre-shrink model probability when available.

    This tells whether shrinkage is helping. It is optional because older
    signals may not have model-probability component metadata.
    """
    available = [
        (float(t.raw_model_prob), float(t.won))
        for t in trades
        if t.raw_model_prob is not None
    ]
    if not available:
        return None
    value = sum((probability - outcome) ** 2 for probability, outcome in available) / len(
        available
    )
    return round(value, 6), len(available)


@dataclass(frozen=True, slots=True)
class BrierSlice:
    dimension: str
    value: str
    count: int
    brier_model: float
    brier_market: float
    model_minus_market: float
    brier_raw_model: float | None
    raw_model_minus_market: float | None
    raw_count: int
    avg_predicted_edge: float
    avg_realized_return: float
    win_rate: float


_BRIER_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("station", "station_icao"),
    ("kind", "kind"),
    ("horizon", "horizon_bucket"),
    ("confidence", "confidence"),
    ("side", "side"),
)


def brier_root_cause(trades: list[TradeOutcome]) -> tuple[BrierSlice, ...]:
    """Brier model-vs-market by explanatory slices.

    Positive model_minus_market means the model is worse than the market in
    that slice. The report is intentionally descriptive: small sample slices
    remain visible with their counts rather than being hidden.
    """
    slices: list[BrierSlice] = []
    for dimension, attr in _BRIER_DIMENSIONS:
        values = sorted({str(getattr(t, attr) or "unknown") for t in trades})
        for value in values:
            members = [t for t in trades if str(getattr(t, attr) or "unknown") == value]
            briers = brier_comparison(members)
            if briers is None:
                continue
            model, market = briers
            raw = raw_model_brier(members)
            raw_value = raw[0] if raw is not None else None
            raw_count = raw[1] if raw is not None else 0
            slices.append(
                BrierSlice(
                    dimension=dimension,
                    value=value,
                    count=len(members),
                    brier_model=model,
                    brier_market=market,
                    model_minus_market=round(model - market, 6),
                    brier_raw_model=raw_value,
                    raw_model_minus_market=(
                        round(raw_value - market, 6) if raw_value is not None else None
                    ),
                    raw_count=raw_count,
                    avg_predicted_edge=round(
                        sum(t.predicted_edge for t in members) / len(members), 6
                    ),
                    avg_realized_return=round(
                        sum(t.realized_return for t in members) / len(members), 6
                    ),
                    win_rate=round(sum(1 for t in members if t.won) / len(members), 6),
                )
            )
    return tuple(
        sorted(
            slices,
            key=lambda s: (s.dimension, -s.model_minus_market, s.value),
        )
    )


EDGE_BUCKET_BOUNDS: tuple[tuple[float, float | None], ...] = (
    (0.0, 0.03),
    (0.03, 0.06),
    (0.06, 0.10),
    (0.10, None),
)


@dataclass(frozen=True, slots=True)
class EdgeBucket:
    low: float
    high: float | None
    count: int
    avg_predicted_edge: float | None
    avg_realized_return: float | None


def edge_calibration(trades: list[TradeOutcome]) -> tuple[EdgeBucket, ...]:
    """Mean realised return per predicted-edge bucket. If predicted edge is
    real, higher buckets should realise higher returns."""
    buckets = []
    for low, high in EDGE_BUCKET_BOUNDS:
        members = [
            t
            for t in trades
            if t.predicted_edge >= low and (high is None or t.predicted_edge < high)
        ]
        buckets.append(
            EdgeBucket(
                low=low,
                high=high,
                count=len(members),
                avg_predicted_edge=(
                    round(sum(t.predicted_edge for t in members) / len(members), 6)
                    if members
                    else None
                ),
                avg_realized_return=(
                    round(sum(t.realized_return for t in members) / len(members), 6)
                    if members
                    else None
                ),
            )
        )
    return tuple(buckets)


def edge_slope(trades: list[TradeOutcome]) -> float | None:
    """OLS slope of realised return on predicted edge. Slope near 1 means the
    predicted edge converts into returns; near 0 means it is noise."""
    if len(trades) < 3:
        return None
    xs = [t.predicted_edge for t in trades]
    ys = [t.realized_return for t in trades]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    variance = sum((x - mean_x) ** 2 for x in xs)
    if variance <= 1e-12:
        return None
    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    return round(covariance / variance, 6)


_PRICE_BANDS: tuple[tuple[float, float], ...] = (
    (0.0, 0.10),
    (0.10, 0.25),
    (0.25, 0.50),
    (0.50, 0.75),
    (0.75, 0.90),
    (0.90, 1.01),
)


def price_band(entry_price: float) -> str:
    """Coarse entry-price band label, e.g. '0.25-0.50'. Tail bands are where
    calibration and slippage bite hardest, so they get their own buckets."""
    for low, high in _PRICE_BANDS:
        if low <= entry_price < high:
            upper = min(high, 1.0)
            return f"{low:.2f}-{upper:.2f}"
    return "unknown"


@dataclass(frozen=True, slots=True)
class ClvOutcome:
    """One closed position scored by closing-line value: did we enter better
    (positive CLV) or worse (negative) than the pre-resolution market close?
    Side-adjusted, so CLV is expressed for the side we actually bought."""

    clv: float
    entry_price: float
    pnl_usd: float
    cost_usd: float
    fee_usd: float
    won: bool
    station_icao: str | None = None
    kind: str | None = None
    side: str | None = None
    confidence: str | None = None
    horizon_bucket: str | None = None

    @property
    def realized_return(self) -> float:
        return self.pnl_usd / self.cost_usd if self.cost_usd > 0 else 0.0

    @property
    def price_band(self) -> str:
        return price_band(self.entry_price)


@dataclass(frozen=True, slots=True)
class ClvSlice:
    dimension: str
    value: str
    count: int
    avg_clv: float
    clv_beat_rate: float
    avg_entry_price: float
    avg_fee_usd: float
    avg_realized_return: float
    win_rate: float


def clv_summary(outcomes: list[ClvOutcome]) -> tuple[float, float] | None:
    """(avg_clv, clv_beat_rate) over all outcomes, or None when empty."""
    if not outcomes:
        return None
    avg = sum(o.clv for o in outcomes) / len(outcomes)
    beat = sum(1 for o in outcomes if o.clv > 0) / len(outcomes)
    return round(avg, 6), round(beat, 6)


_CLV_DIMENSIONS: tuple[tuple[str, str], ...] = (
    ("station", "station_icao"),
    ("kind", "kind"),
    ("horizon", "horizon_bucket"),
    ("confidence", "confidence"),
    ("side", "side"),
    ("price_band", "price_band"),
)


def clv_root_cause(outcomes: list[ClvOutcome]) -> tuple[ClvSlice, ...]:
    """Closing-line value by explanatory slice: where do we enter worse than
    the market? Negative avg_clv means we systematically pay up (spread /
    slippage / adverse selection) in that slice. Descriptive, like the Brier
    report — small-sample slices stay visible with their counts. Sorted worst
    (most negative CLV) first within each dimension."""
    slices: list[ClvSlice] = []
    for dimension, attr in _CLV_DIMENSIONS:
        values = sorted({str(getattr(o, attr) or "unknown") for o in outcomes})
        for value in values:
            members = [
                o for o in outcomes if str(getattr(o, attr) or "unknown") == value
            ]
            if not members:
                continue
            slices.append(
                ClvSlice(
                    dimension=dimension,
                    value=value,
                    count=len(members),
                    avg_clv=round(sum(o.clv for o in members) / len(members), 6),
                    clv_beat_rate=round(
                        sum(1 for o in members if o.clv > 0) / len(members), 6
                    ),
                    avg_entry_price=round(
                        sum(o.entry_price for o in members) / len(members), 6
                    ),
                    avg_fee_usd=round(
                        sum(o.fee_usd for o in members) / len(members), 6
                    ),
                    avg_realized_return=round(
                        sum(o.realized_return for o in members) / len(members), 6
                    ),
                    win_rate=round(
                        sum(1 for o in members if o.won) / len(members), 6
                    ),
                )
            )
    return tuple(
        sorted(slices, key=lambda s: (s.dimension, s.avg_clv, s.value))
    )
