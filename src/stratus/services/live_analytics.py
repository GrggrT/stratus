from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PositionStat:
    closed: bool
    pnl_usd: float | None
    clv: float | None
    cost_usd: float
    fee_usd: float | None


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

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.closed if self.closed else None


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


def summarize_live_performance(positions: list[PositionStat]) -> LivePerformance:
    """Retrospective on realised live trading.

    ROI is realised PnL over the cost of closed positions (not bankroll, so a
    partial deployment is not flattered). CLV beat-rate is the share of closed
    positions with a recorded closing-line value that entered better than the
    close — the core skill signal, independent of individual outcomes.
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
    )
