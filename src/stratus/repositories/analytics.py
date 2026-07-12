from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketPrice,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
    TradeSide,
)
from stratus.services.live_analytics import ClvOutcome, TradeOutcome

# Orders that actually put money at risk. Resting/failed/canceled orders carry
# no outcome to score.
_FILLED_STATUSES = ("filled", "partially_filled")


async def resolved_trade_outcomes(
    session: AsyncSession, mode: ExecutionMode
) -> list[TradeOutcome]:
    """Per-order outcomes on resolved markets, side-adjusted.

    Order-level (not position-level) because paper positions aggregate
    multiple fills per market/side and would blur per-signal attribution.
    The market anchor is the last pre-signal midpoint; when no snapshot
    exists the signal's entry price stands in (slightly worse for the
    market's Brier since it includes our side of the spread).
    """
    pre_signal_midpoint = (
        select(MarketPrice.midpoint)
        .where(
            MarketPrice.market_id == Signal.market_id,
            MarketPrice.ts <= Signal.ts,
        )
        .order_by(MarketPrice.ts.desc())
        .limit(1)
        .correlate(Signal)
        .scalar_subquery()
    )
    pre_signal_components = (
        select(ModelProb.components)
        .where(
            ModelProb.market_id == Signal.market_id,
            ModelProb.ts <= Signal.ts,
        )
        .order_by(ModelProb.ts.desc())
        .limit(1)
        .correlate(Signal)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                Order.side,
                Order.price,
                Order.filled_usd,
                Order.fee_usd,
                Signal.model_prob,
                Signal.market_price,
                Signal.edge,
                Signal.confidence,
                Signal.ts,
                pre_signal_midpoint,
                pre_signal_components,
                Market.kind,
                Market.resolves_at,
                Market.resolved_outcome,
                Station.icao,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .outerjoin(Station, Market.station_id == Station.id)
            .where(
                Order.mode == mode,
                Order.status.in_(_FILLED_STATUSES),
                Order.filled_usd > 0,
                Market.resolved_outcome.is_not(None),
            )
        )
    ).all()

    outcomes = []
    for (
        side,
        price,
        filled_usd,
        fee_usd,
        model_prob,
        entry,
        edge,
        confidence,
        signal_ts,
        mid,
        components,
        kind,
        resolves_at,
        resolved,
        station_icao,
    ) in rows:
        if price is None or float(price) <= 0:
            continue
        won = (side is TradeSide.BUY_YES) == bool(resolved)
        shares = float(filled_usd) / float(price)
        cost = float(filled_usd) + float(fee_usd or 0)
        pnl = (shares if won else 0.0) - cost
        yes_model = float(model_prob)
        if mid is not None:
            yes_market = float(mid)
            market_prob = yes_market if side is TradeSide.BUY_YES else 1 - yes_market
        else:
            market_prob = float(entry)
        raw_model_prob = _raw_model_probability(components, side)
        outcomes.append(
            TradeOutcome(
                model_prob=yes_model if side is TradeSide.BUY_YES else 1 - yes_model,
                market_prob=market_prob,
                won=won,
                pnl_usd=round(pnl, 4),
                cost_usd=round(cost, 4),
                predicted_edge=float(edge),
                raw_model_prob=raw_model_prob,
                station_icao=station_icao,
                kind=kind.value if hasattr(kind, "value") else str(kind),
                horizon_bucket=_horizon_bucket(signal_ts, resolves_at),
                confidence=confidence.value if hasattr(confidence, "value") else str(confidence),
                side=side.value if hasattr(side, "value") else str(side),
            )
        )
    return outcomes


def _raw_model_probability(components: object, side: TradeSide) -> float | None:
    if not isinstance(components, dict):
        return None
    shrinkage = components.get("shrinkage")
    if not isinstance(shrinkage, dict):
        return None
    raw = shrinkage.get("p_model_raw")
    if not isinstance(raw, int | float):
        return None
    yes_raw = float(raw)
    return yes_raw if side is TradeSide.BUY_YES else 1 - yes_raw


def _horizon_bucket(signal_ts: object, resolves_at: object) -> str:
    if not hasattr(resolves_at, "__sub__"):
        return "unknown"
    try:
        hours = (resolves_at - signal_ts).total_seconds() / 3600
    except Exception:
        return "unknown"
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "24-48h"
    return "48h+"


async def resolved_clv_outcomes(
    session: AsyncSession, mode: ExecutionMode
) -> list[ClvOutcome]:
    """Closed positions scored by their stored closing-line value.

    Position-level (not order-level) so avg_clv reconciles exactly with the
    gate and performance summary. Confidence and horizon come from the most
    recent executed signal on the same market/side at or before close — a
    representative anchor, since a paper position may aggregate several fills.
    """
    representative_confidence = (
        select(Signal.confidence)
        .where(
            Signal.market_id == Position.market_id,
            Signal.side == Position.side,
            Signal.status == SignalStatus.EXECUTED,
            Signal.ts <= Position.closed_at,
        )
        .order_by(Signal.ts.desc())
        .limit(1)
        .correlate(Position)
        .scalar_subquery()
    )
    representative_signal_ts = (
        select(Signal.ts)
        .where(
            Signal.market_id == Position.market_id,
            Signal.side == Position.side,
            Signal.status == SignalStatus.EXECUTED,
            Signal.ts <= Position.closed_at,
        )
        .order_by(Signal.ts.desc())
        .limit(1)
        .correlate(Position)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                Position.clv,
                Position.avg_price,
                Position.pnl_usd,
                Position.size_usd,
                Position.fee_usd,
                Position.side,
                Market.kind,
                Market.resolves_at,
                Market.resolved_outcome,
                Station.icao,
                representative_confidence,
                representative_signal_ts,
            )
            .join(Market, Position.market_id == Market.id)
            .outerjoin(Station, Market.station_id == Station.id)
            .where(
                Position.mode == mode,
                Position.status == PositionStatus.CLOSED,
                Position.clv.is_not(None),
            )
        )
    ).all()

    outcomes = []
    for (
        clv,
        entry,
        pnl_usd,
        size_usd,
        fee_usd,
        side,
        kind,
        resolves_at,
        resolved,
        station_icao,
        confidence,
        signal_ts,
    ) in rows:
        won = (side is TradeSide.BUY_YES) == bool(resolved)
        outcomes.append(
            ClvOutcome(
                clv=float(clv),
                entry_price=float(entry),
                pnl_usd=float(pnl_usd or 0),
                cost_usd=float(size_usd),
                fee_usd=float(fee_usd or 0),
                won=won,
                station_icao=station_icao,
                kind=kind.value if hasattr(kind, "value") else str(kind),
                side=side.value if hasattr(side, "value") else str(side),
                confidence=(
                    confidence.value if hasattr(confidence, "value")
                    else str(confidence) if confidence is not None else None
                ),
                horizon_bucket=(
                    _horizon_bucket(signal_ts, resolves_at)
                    if signal_ts is not None
                    else "unknown"
                ),
            )
        )
    return outcomes
