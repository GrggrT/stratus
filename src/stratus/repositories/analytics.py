from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from stratus.db.models import (
    ClobMarketSnapshot,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
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
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.live_analytics import ClvOutcome, TradeOutcome

# Orders that actually put money at risk. Resting/failed/canceled orders carry
# no outcome to score.
_FILLED_STATUSES = ("filled", "partially_filled")


@dataclass(frozen=True, slots=True)
class ActualOrderFill:
    entry_price: Decimal
    filled_notional_usd: Decimal
    fee_usd: Decimal | None
    shares: Decimal


def execution_fill_aggregate_subquery() -> Subquery:
    """Aggregate immutable atomic legs once per order for downstream scoring.

    The stored six-decimal notional remains the cash-accounting source.  VWAP
    independently follows the database seal identity and is derived from the
    canonical shares and prices rather than from rounded notionals.
    """

    total_shares = func.sum(ExecutionFillLeg.shares)
    exact_notional = func.sum(ExecutionFillLeg.shares * ExecutionFillLeg.price)
    missing_fees = func.count(ExecutionFillLeg.id).filter(
        ExecutionFillLeg.fee_usd.is_(None)
    )
    return (
        select(
            ExecutionFillLeg.order_id.label("order_id"),
            total_shares.label("total_shares"),
            func.sum(ExecutionFillLeg.notional_usd).label("filled_notional_usd"),
            case(
                (missing_fees == 0, func.sum(ExecutionFillLeg.fee_usd)),
                else_=None,
            ).label("total_fee_usd"),
            (exact_notional / func.nullif(total_shares, 0)).label("vwap_fill_price"),
        )
        .group_by(ExecutionFillLeg.order_id)
        .subquery()
    )


def actual_order_fill(
    *,
    order_price: Decimal,
    order_filled_usd: Decimal,
    order_fee_usd: Decimal | None,
    execution_intent_id: int | None,
    leg_vwap_fill_price: Decimal | None,
    leg_filled_notional_usd: Decimal | None,
    leg_fee_usd: Decimal | None,
    leg_total_shares: Decimal | None,
) -> ActualOrderFill | None:
    """Prefer immutable fill-leg execution facts with a legacy-only fallback."""

    if leg_total_shares is not None:
        if leg_vwap_fill_price is None or leg_filled_notional_usd is None:
            return None
        entry = Decimal(leg_vwap_fill_price)
        notional = Decimal(leg_filled_notional_usd)
        shares = Decimal(leg_total_shares)
        # Atomic fees are authoritative when complete. Some live venue
        # observations cannot attest per-leg fees even though the legacy
        # order projection has a known cumulative fee; retain that explicit
        # fallback instead of silently treating an unknown fee as zero.
        fee_source = leg_fee_usd if leg_fee_usd is not None else order_fee_usd
        fee = Decimal(fee_source) if fee_source is not None else None
    else:
        # The compatibility projection is only evidence for pre-ledger rows.
        # A modern intent without atomic legs is incomplete, not legacy.
        if execution_intent_id is not None:
            return None
        entry = Decimal(order_price)
        notional = Decimal(order_filled_usd)
        fee = Decimal(order_fee_usd) if order_fee_usd is not None else None
        if not entry.is_finite() or entry <= 0:
            return None
        shares = notional / entry
    if (
        not entry.is_finite()
        or not Decimal(0) < entry < Decimal(1)
        or not notional.is_finite()
        or notional <= 0
        or not shares.is_finite()
        or shares <= 0
        or (fee is not None and (not fee.is_finite() or fee < 0))
    ):
        return None
    return ActualOrderFill(
        entry_price=entry,
        filled_notional_usd=notional,
        fee_usd=fee,
        shares=shares,
    )


async def resolved_trade_outcomes(
    session: AsyncSession,
    mode: ExecutionMode,
    *,
    benchmark_protocol: str | None = None,
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
    dual_probability = (
        select(ClobMarketSnapshot.binary_yes_probability)
        .where(ClobMarketSnapshot.id == Signal.clob_snapshot_id)
        .correlate(Signal)
        .scalar_subquery()
    )
    pre_signal_components = (
        select(ModelProb.components)
        .where(
            ModelProb.market_id == Signal.market_id,
            ModelProb.ts <= Signal.ts,
            ModelProb.market_anchor_protocol == Signal.benchmark_protocol,
            or_(
                Signal.benchmark_protocol != CLOB_DUAL_PROTOCOL,
                ModelProb.market_snapshot_id == Signal.clob_snapshot_id,
            ),
        )
        .order_by(ModelProb.ts.desc())
        .limit(1)
        .correlate(Signal)
        .scalar_subquery()
    )
    fill_aggregate = execution_fill_aggregate_subquery()
    statement = (
        select(
                Order.id,
                Order.side,
                Order.price,
                Order.filled_usd,
                Order.fee_usd,
                ExecutionOrderIntent.id,
                fill_aggregate.c.vwap_fill_price,
                fill_aggregate.c.filled_notional_usd,
                fill_aggregate.c.total_fee_usd,
                fill_aggregate.c.total_shares,
                Signal.model_prob,
                Signal.market_price,
                Signal.edge,
                Signal.confidence,
                Signal.ts,
                pre_signal_midpoint,
                dual_probability,
                pre_signal_components,
                Market.kind,
                Market.market_date,
                Market.resolves_at,
                Market.resolved_outcome,
                Station.icao,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .outerjoin(Station, Market.station_id == Station.id)
            .outerjoin(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
            .outerjoin(fill_aggregate, fill_aggregate.c.order_id == Order.id)
        .where(
            Order.mode == mode,
            Order.status.in_(_FILLED_STATUSES),
            Order.filled_usd > 0,
            Market.resolved_outcome.is_not(None),
        )
    )
    if benchmark_protocol is not None:
        statement = statement.where(Signal.benchmark_protocol == benchmark_protocol)
        if benchmark_protocol == CLOB_DUAL_PROTOCOL:
            statement = statement.where(Market.resolution_event_id.is_not(None))
    rows = (await session.execute(statement)).all()

    outcomes = []
    for (
        order_id,
        side,
        price,
        filled_usd,
        fee_usd,
        execution_intent_id,
        leg_vwap,
        leg_filled_usd,
        leg_fee_usd,
        leg_total_shares,
        model_prob,
        entry,
        edge,
        confidence,
        signal_ts,
        mid,
        dual_market_probability,
        components,
        kind,
        market_date,
        resolves_at,
        resolved,
        station_icao,
    ) in rows:
        fill = actual_order_fill(
            order_price=price,
            order_filled_usd=filled_usd,
            order_fee_usd=fee_usd,
            execution_intent_id=execution_intent_id,
            leg_vwap_fill_price=leg_vwap,
            leg_filled_notional_usd=leg_filled_usd,
            leg_fee_usd=leg_fee_usd,
            leg_total_shares=leg_total_shares,
        )
        # Net P&L/ROI is undefined while the fee is unknown.  Excluding this
        # order is fail-closed and prevents optimistic zero-fee scoring.
        if fill is None or fill.fee_usd is None:
            continue
        won = (side is TradeSide.BUY_YES) == bool(resolved)
        shares = float(fill.shares)
        cost = float(fill.filled_notional_usd + fill.fee_usd)
        pnl = (shares if won else 0.0) - cost
        yes_model = float(model_prob)
        if dual_market_probability is not None:
            yes_market = float(dual_market_probability)
            market_prob = yes_market if side is TradeSide.BUY_YES else 1 - yes_market
        elif mid is not None:
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
                pipeline_version=probability_pipeline_version(components),
                market_date=market_date,
                order_id=order_id,
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


def probability_pipeline_version(components: object) -> str | None:
    """Return the explicit probability cohort encoded in model metadata."""
    if not isinstance(components, dict):
        return None
    pipeline = components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    cohort = pipeline.get("cohort")
    if isinstance(cohort, str) and cohort:
        return cohort
    version = pipeline.get("version")
    return version if isinstance(version, str) and version else None


async def current_probability_pipeline_cohort(
    session: AsyncSession,
    *,
    market_anchor_protocol: str | None = None,
) -> str | None:
    statement = (
        select(ModelProb.components)
        .order_by(ModelProb.ts.desc(), ModelProb.id.desc())
        .limit(1)
    )
    if market_anchor_protocol is not None:
        statement = statement.where(
            ModelProb.market_anchor_protocol == market_anchor_protocol
        )
    components = await session.scalar(statement)
    return probability_pipeline_version(components)


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
    session: AsyncSession,
    mode: ExecutionMode,
    *,
    benchmark_protocol: str | None = None,
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
            or_(
                Position.benchmark_protocol != CLOB_DUAL_PROTOCOL,
                and_(
                    Signal.benchmark_protocol == Position.benchmark_protocol,
                    Signal.token_id == Position.token_id,
                ),
            ),
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
            or_(
                Position.benchmark_protocol != CLOB_DUAL_PROTOCOL,
                and_(
                    Signal.benchmark_protocol == Position.benchmark_protocol,
                    Signal.token_id == Position.token_id,
                ),
            ),
        )
        .order_by(Signal.ts.desc())
        .limit(1)
        .correlate(Position)
        .scalar_subquery()
    )
    statement = (
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
    if benchmark_protocol is not None:
        statement = statement.where(Position.benchmark_protocol == benchmark_protocol)
        if benchmark_protocol == CLOB_DUAL_PROTOCOL:
            statement = statement.where(Market.resolution_event_id.is_not(None))
    rows = (await session.execute(statement)).all()

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
