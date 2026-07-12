from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketPrice,
    Order,
    Signal,
    TradeSide,
)
from stratus.services.live_analytics import TradeOutcome

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
                pre_signal_midpoint,
                Market.resolved_outcome,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .where(
                Order.mode == mode,
                Order.status.in_(_FILLED_STATUSES),
                Order.filled_usd > 0,
                Market.resolved_outcome.is_not(None),
            )
        )
    ).all()

    outcomes = []
    for side, price, filled_usd, fee_usd, model_prob, entry, edge, mid, resolved in rows:
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
        outcomes.append(
            TradeOutcome(
                model_prob=yes_model if side is TradeSide.BUY_YES else 1 - yes_model,
                market_prob=market_prob,
                won=won,
                pnl_usd=round(pnl, 4),
                cost_usd=round(cost, 4),
                predicted_edge=float(edge),
            )
        )
    return outcomes
