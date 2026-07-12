from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient
from stratus.config import Settings
from stratus.db.models import (
    BankrollLog,
    Calibration,
    ExecutionMode,
    HealthAudit,
    JobRun,
    Market,
    MarketPrice,
    MarketStatus,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
    TradeSide,
    ValidationCheckpoint,
)
from stratus.repositories.analytics import (
    resolved_clv_outcomes,
    resolved_trade_outcomes,
)
from stratus.repositories.operator_commands import enqueue_command, recent_commands
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.live_analytics import (
    BrierSlice,
    ClvSlice,
    LivePerformance,
    PositionStat,
    TradeOutcome,
    brier_comparison,
    brier_root_cause,
    clv_root_cause,
    clv_summary,
    cumulative_equity,
    edge_calibration,
    edge_slope,
    price_band,
    raw_model_brier,
    summarize_live_performance,
)
from stratus.services.operator_commands import validate_command
from stratus.services.telegram_reports import calculate_live_gate

router = APIRouter(prefix="/api/v1", tags=["operator-monitoring"])
SessionFactory = async_sessionmaker[AsyncSession]


def require_configured_operator_key(request: Request) -> None:
    """Mutations are refused outright until an API key is configured.

    Read-only monitoring may run keyless on loopback, but anything that can
    stop or resume trading must never be an unauthenticated HTTP call.
    """
    settings = cast(Settings, request.app.state.settings)
    if settings.operator_api_key is None:
        raise HTTPException(
            status_code=403,
            detail="mutations are disabled until OPERATOR_API_KEY is configured",
        )


class RuntimeDto(BaseModel):
    mode: str
    kill_switch: bool
    halted_reason: str | None


class HealthAuditDto(BaseModel):
    status: str
    timestamp: datetime
    issues: list[dict[str, Any]]


class ValidationDto(BaseModel):
    checkpoint_day: int
    evaluated_at: datetime
    eligible: bool
    passed: bool
    metrics: dict[str, Any]


class OverviewResponse(BaseModel):
    generated_at: datetime
    runtime: RuntimeDto
    active_markets: int
    blocked_markets: int
    open_positions: int
    pending_signals: int
    bankroll_usd: float | None
    open_exposure_usd: float
    health_audit: HealthAuditDto | None
    validation: ValidationDto | None


class MarketItem(BaseModel):
    id: int
    polymarket_id: str
    station_icao: str | None
    city: str | None
    market_date: date
    kind: str
    bucket_low_c: float | None
    bucket_high_c: float | None
    status: str
    station_verified: bool
    verification_reason: str | None
    resolves_at: datetime


class MarketsResponse(BaseModel):
    generated_at: datetime
    items: list[MarketItem]


class SignalItem(BaseModel):
    id: int
    market_id: int
    timestamp: datetime
    polymarket_id: str
    station_icao: str | None
    side: str
    model_probability: float
    market_price: float
    edge: float
    kelly_fraction: float
    size_usd: float
    confidence: str
    status: str
    skip_reason: str | None


class SignalsResponse(BaseModel):
    generated_at: datetime
    items: list[SignalItem]


class PositionItem(BaseModel):
    id: int
    market_id: int
    polymarket_id: str
    station_icao: str | None
    market_date: date
    kind: str
    mode: str
    side: str
    average_price: float
    size_usd: float
    fee_usd: float | None
    status: str
    pnl_usd: float | None
    clv: float | None
    closed_at: datetime | None
    # Open positions only: the side-adjusted latest midpoint and the
    # mark-to-market against it. Null when closed or no price snapshot.
    mark_price: float | None
    unrealized_pnl_usd: float | None


class PositionsResponse(BaseModel):
    generated_at: datetime
    items: list[PositionItem]


class CalibrationItem(BaseModel):
    station_icao: str
    model: str
    horizon_bucket: str
    kind: str
    bias_c: float
    sigma_c: float
    sample_count: int
    brier: float | None
    updated_at: datetime


class CalibrationsResponse(BaseModel):
    generated_at: datetime
    items: list[CalibrationItem]


class LiveSummaryResponse(BaseModel):
    generated_at: datetime
    positions: int
    closed: int
    open: int
    wins: int
    win_rate: float | None
    total_pnl_usd: float
    robust_pnl_usd: float | None
    total_cost_usd: float
    total_fees_usd: float
    roi: float | None
    robust_roi: float | None
    avg_clv: float | None
    clv_beat_rate: float | None
    # Skill/luck decomposition and bootstrap CIs: the anti-luck view.
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    decomposed: int
    roi_ci_low: float | None
    roi_ci_high: float | None
    skill_pnl_ci_low: float | None
    skill_pnl_ci_high: float | None
    statistically_positive: bool | None
    brier_model: float | None
    brier_market: float | None
    n_resolved_trades: int


class EdgeBucketItem(BaseModel):
    low: float
    high: float | None
    count: int
    avg_predicted_edge: float | None
    avg_realized_return: float | None


class GateCheckItem(BaseModel):
    name: str
    passed: bool
    detail: str


class EdgeReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    n_resolved_trades: int
    brier_model: float | None
    brier_market: float | None
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    roi_ci_low: float | None
    roi_ci_high: float | None
    skill_pnl_ci_low: float | None
    skill_pnl_ci_high: float | None
    statistically_positive: bool | None
    edge_slope: float | None
    buckets: list[EdgeBucketItem]
    # Formal "edge is proven" checklist gating live mode (always computed
    # from the paper track record, regardless of the mode filter above).
    gate_passed: bool
    gate_checks: list[GateCheckItem]


class BrierSliceItem(BaseModel):
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


class BrierReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    n_resolved_trades: int
    brier_model: float | None
    brier_market: float | None
    brier_raw_model: float | None
    raw_count: int
    model_minus_market: float | None
    raw_model_minus_market: float | None
    slices: list[BrierSliceItem]


class ClvSliceItem(BaseModel):
    dimension: str
    value: str
    count: int
    avg_clv: float
    clv_beat_rate: float
    avg_entry_price: float
    avg_fee_usd: float
    avg_realized_return: float
    win_rate: float


class ClvReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    n_closed_positions: int
    avg_clv: float | None
    clv_beat_rate: float | None
    slices: list[ClvSliceItem]


class RootCauseTradeItem(BaseModel):
    id: int
    record_type: str
    order_id: int | None
    signal_id: int | None
    position_id: int | None
    market_id: int
    polymarket_id: str
    station_icao: str | None
    market_date: date
    kind: str
    side: str
    confidence: str | None
    horizon_bucket: str
    timestamp: datetime | None
    closed_at: datetime | None
    entry_price: float
    closing_line_price: float | None
    model_probability: float | None
    market_probability: float | None
    raw_model_probability: float | None
    predicted_edge: float | None
    brier_model_error: float | None
    brier_market_error: float | None
    brier_delta: float | None
    clv: float | None
    pnl_usd: float
    cost_usd: float
    fee_usd: float | None
    won: bool
    resolved_outcome: bool | None


class RootCauseDrilldownResponse(BaseModel):
    generated_at: datetime
    mode: str
    metric: str
    dimension: str
    value: str
    count: int
    items: list[RootCauseTradeItem]


class RootCauseActionItem(BaseModel):
    metric: str
    dimension: str
    value: str
    count: int
    action: str
    severity: str
    detail: str


class RootCauseActionsResponse(BaseModel):
    generated_at: datetime
    mode: str
    items: list[RootCauseActionItem]


class EquityPointItem(BaseModel):
    t: datetime
    cumulative_pnl_usd: float


class EquityResponse(BaseModel):
    generated_at: datetime
    points: list[EquityPointItem]


class OrderItem(BaseModel):
    id: int
    signal_id: int
    polymarket_id: str
    station_icao: str | None
    mode: str
    clob_order_id: str | None
    side: str
    price: float
    size_usd: float
    filled_usd: float
    fee_usd: float | None
    status: str
    created_at: datetime
    filled_at: datetime | None


class OrdersResponse(BaseModel):
    generated_at: datetime
    items: list[OrderItem]


class JobItem(BaseModel):
    id: int
    job_id: str
    scheduled_at: datetime
    finished_at: datetime
    status: str
    error: str | None


class JobsResponse(BaseModel):
    generated_at: datetime
    items: list[JobItem]


class MarketPricePoint(BaseModel):
    t: datetime
    midpoint: float
    best_bid: float
    best_ask: float
    bid_depth_usd: float
    ask_depth_usd: float


class MarketPriceSeriesResponse(BaseModel):
    generated_at: datetime
    market_id: int
    points: list[MarketPricePoint]


class ProbabilityPoint(BaseModel):
    t: datetime
    source: str
    model_probability: float | None
    market_price: float | None
    edge: float | None
    sigma: float | None
    horizon_h: float | None
    confidence: str | None
    status: str | None


class ProbabilitySeriesResponse(BaseModel):
    generated_at: datetime
    market_id: int
    points: list[ProbabilityPoint]


class OrderBookLevel(BaseModel):
    price: float
    size: float
    notional_usd: float


class OrderBookResponse(BaseModel):
    generated_at: datetime
    market_id: int
    side: str
    token_id: str | None
    source_status: str
    error: str | None
    midpoint: float | None
    best_bid: float | None
    best_ask: float | None
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


class CommandRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    command: str = Field(max_length=32)
    reason: str = Field(min_length=4, max_length=2000)
    actor: str = Field(default="console", min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandItem(BaseModel):
    id: int
    request_id: str
    command: str
    payload: dict[str, Any]
    actor: str
    reason: str
    status: str
    detail: str | None
    created_at: datetime
    executed_at: datetime | None


class CommandAccepted(BaseModel):
    created: bool
    item: CommandItem


class CommandsResponse(BaseModel):
    generated_at: datetime
    items: list[CommandItem]


def _sessions(request: Request) -> SessionFactory:
    return cast(SessionFactory, request.app.state.sessions)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


_FILLED_ORDER_STATUSES = ("filled", "partially_filled")
_BRIER_DRILLDOWN_DIMENSIONS = {"station", "kind", "horizon", "confidence", "side"}
_CLV_DRILLDOWN_DIMENSIONS = {
    "station",
    "kind",
    "horizon",
    "confidence",
    "side",
    "price_band",
}


def _enum_text(value: Any | None) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw)


def _side_adjusted_probability(yes_probability: float, side: TradeSide) -> float:
    return yes_probability if side is TradeSide.BUY_YES else 1 - yes_probability


def _root_cause_horizon_bucket(signal_ts: datetime | None, resolves_at: datetime) -> str:
    if signal_ts is None:
        return "unknown"
    hours = (resolves_at - signal_ts).total_seconds() / 3600
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "24-48h"
    return "48h+"


def _root_cause_raw_model_probability(
    components: object, side: TradeSide
) -> float | None:
    if not isinstance(components, dict):
        return None
    shrinkage = components.get("shrinkage")
    if not isinstance(shrinkage, dict):
        return None
    raw = shrinkage.get("p_model_raw")
    if not isinstance(raw, int | float):
        return None
    return _side_adjusted_probability(float(raw), side)


def _root_cause_dimension_value(
    dimension: str,
    *,
    station_icao: str | None,
    kind: Any,
    horizon_bucket: str,
    confidence: Any | None,
    side: TradeSide,
    entry_price: float | None = None,
) -> str:
    if dimension == "station":
        return station_icao or "unknown"
    if dimension == "kind":
        return _enum_text(kind) or "unknown"
    if dimension == "horizon":
        return horizon_bucket
    if dimension == "confidence":
        return _enum_text(confidence) or "unknown"
    if dimension == "side":
        return _enum_text(side) or "unknown"
    if dimension == "price_band" and entry_price is not None:
        return price_band(entry_price)
    return "unknown"


def _book_levels(raw: Any, *, reverse: bool, limit: int = 10) -> list[OrderBookLevel]:
    if not isinstance(raw, list):
        raise ValueError("book side is missing or not a list")
    parsed: list[tuple[Decimal, Decimal]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("book level is not an object")
        try:
            price = Decimal(str(item["price"]))
            size = Decimal(str(item["size"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("book level contains an invalid price or size") from exc
        if not Decimal(0) < price < Decimal(1) or size <= 0:
            raise ValueError("book level is out of range")
        parsed.append((price, size))
    parsed.sort(key=lambda level: level[0], reverse=reverse)
    return [
        OrderBookLevel(
            price=float(price),
            size=float(size),
            notional_usd=float((price * size).quantize(Decimal("0.0001"))),
        )
        for price, size in parsed[:limit]
    ]


def _empty_orderbook(
    *,
    market_id: int,
    side: str,
    token_id: str | None,
    error: str,
) -> OrderBookResponse:
    return OrderBookResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        side=side,
        token_id=token_id,
        source_status="unavailable",
        error=error,
        midpoint=None,
        best_bid=None,
        best_ask=None,
        bids=[],
        asks=[],
    )


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request) -> OverviewResponse:
    async with _sessions(request)() as session:
        runtime = await get_runtime_control(session)
        market_rows = (
            await session.execute(
                select(Market.status, func.count(Market.id)).group_by(Market.status)
            )
        ).all()
        market_counts = {status: int(count) for status, count in market_rows}
        open_positions = int(
            await session.scalar(
                select(func.count(Position.id)).where(
                    Position.status == PositionStatus.OPEN,
                    Position.mode == runtime.mode,
                )
            )
            or 0
        )
        pending_signals = int(
            await session.scalar(
                select(func.count(Signal.id)).where(Signal.status == SignalStatus.PENDING)
            )
            or 0
        )
        bankroll = await session.scalar(
            select(BankrollLog.balance_usd)
            .where(BankrollLog.mode == runtime.mode)
            .order_by(BankrollLog.ts.desc())
            .limit(1)
        )
        exposure = await session.scalar(
            select(func.coalesce(func.sum(Position.size_usd), 0)).where(
                Position.status == PositionStatus.OPEN,
                Position.mode == runtime.mode,
            )
        )
        health = await session.scalar(
            select(HealthAudit).order_by(HealthAudit.ts.desc()).limit(1)
        )
        validation = await session.scalar(
            select(ValidationCheckpoint)
            .order_by(ValidationCheckpoint.evaluated_at.desc())
            .limit(1)
        )

    return OverviewResponse(
        generated_at=datetime.now(UTC),
        runtime=RuntimeDto(
            mode=runtime.mode.value,
            kill_switch=runtime.kill_switch,
            halted_reason=runtime.halted_reason,
        ),
        active_markets=market_counts.get(MarketStatus.ACTIVE, 0),
        blocked_markets=market_counts.get(MarketStatus.BLOCKED, 0),
        open_positions=open_positions,
        pending_signals=pending_signals,
        bankroll_usd=_number(bankroll),
        open_exposure_usd=float(exposure or 0),
        health_audit=(
            HealthAuditDto(status=health.status, timestamp=health.ts, issues=health.issues)
            if health is not None
            else None
        ),
        validation=(
            ValidationDto(
                checkpoint_day=validation.checkpoint_day,
                evaluated_at=validation.evaluated_at,
                eligible=validation.eligible,
                passed=validation.passed,
                metrics=validation.metrics,
            )
            if validation is not None
            else None
        ),
    )


@router.get("/markets", response_model=MarketsResponse)
async def markets(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MarketsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Market, Station.icao, Station.city)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Market.resolves_at.desc())
                .limit(limit)
            )
        ).all()
    return MarketsResponse(
        generated_at=datetime.now(UTC),
        items=[
            MarketItem(
                id=market.id,
                polymarket_id=market.polymarket_id,
                station_icao=icao,
                city=city,
                market_date=market.market_date,
                kind=market.kind.value,
                bucket_low_c=_number(market.bucket_low_c),
                bucket_high_c=_number(market.bucket_high_c),
                status=market.status.value,
                station_verified=market.station_verified,
                verification_reason=market.station_verification_reason,
                resolves_at=market.resolves_at,
            )
            for market, icao, city in rows
        ],
    )


@router.get("/signals", response_model=SignalsResponse)
async def signals(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SignalsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Signal, Market.polymarket_id, Station.icao)
                .join(Market, Market.id == Signal.market_id)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Signal.ts.desc())
                .limit(limit)
            )
        ).all()
    return SignalsResponse(
        generated_at=datetime.now(UTC),
        items=[
            SignalItem(
                id=signal.id,
                market_id=signal.market_id,
                timestamp=signal.ts,
                polymarket_id=polymarket_id,
                station_icao=icao,
                side=signal.side.value,
                model_probability=float(signal.model_prob),
                market_price=float(signal.market_price),
                edge=float(signal.edge),
                kelly_fraction=float(signal.kelly_frac),
                size_usd=float(signal.size_usd),
                confidence=signal.confidence.value,
                status=signal.status.value,
                skip_reason=signal.skip_reason,
            )
            for signal, polymarket_id, icao in rows
        ],
    )


@router.get("/positions", response_model=PositionsResponse)
async def positions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PositionsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Position, Market)
                .join(Market, Market.id == Position.market_id)
                .order_by(Position.id.desc())
                .limit(limit)
            )
        ).all()
        market_ids = {market.id for _, market in rows}
        station_rows = (
            await session.execute(
                select(Market.id, Station.icao)
                .join(Station, Station.id == Market.station_id)
                .where(Market.id.in_(market_ids))
            )
        ).all()
        station_names: dict[int, str] = {mid: icao for mid, icao in station_rows}
        mid_rows = (
            await session.execute(
                select(MarketPrice.market_id, MarketPrice.midpoint)
                .where(MarketPrice.market_id.in_(market_ids))
                .distinct(MarketPrice.market_id)
                .order_by(MarketPrice.market_id, MarketPrice.ts.desc())
            )
        ).all()
        latest_mid: dict[int, Decimal] = {mid: mp for mid, mp in mid_rows}

    items = []
    for position, market in rows:
        mark_price: float | None = None
        unrealized: float | None = None
        midpoint = latest_mid.get(market.id)
        if position.status is PositionStatus.OPEN and midpoint is not None:
            side_mid = (
                midpoint if position.side.value == "buy_yes" else Decimal(1) - midpoint
            )
            mark_price = float(side_mid)
            unrealized = float(
                (position.size_shares * side_mid - position.size_usd).quantize(
                    Decimal("0.0001")
                )
            )
        items.append(
            PositionItem(
                id=position.id,
                market_id=market.id,
                polymarket_id=market.polymarket_id,
                station_icao=station_names.get(market.id),
                market_date=market.market_date,
                kind=market.kind.value,
                mode=position.mode.value,
                side=position.side.value,
                average_price=float(position.avg_price),
                size_usd=float(position.size_usd),
                fee_usd=_number(position.fee_usd),
                status=position.status.value,
                pnl_usd=_number(position.pnl_usd),
                clv=_number(position.clv),
                closed_at=position.closed_at,
                mark_price=mark_price,
                unrealized_pnl_usd=unrealized,
            )
        )
    return PositionsResponse(generated_at=datetime.now(UTC), items=items)


@router.get("/market-timeseries", response_model=MarketPriceSeriesResponse)
async def market_timeseries(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    limit: Annotated[int, Query(ge=1, le=500)] = 240,
) -> MarketPriceSeriesResponse:
    async with _sessions(request)() as session:
        exists = await session.scalar(select(Market.id).where(Market.id == market_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="market not found")
        rows = (
            await session.execute(
                select(MarketPrice)
                .where(MarketPrice.market_id == market_id)
                .order_by(MarketPrice.ts.desc())
                .limit(limit)
            )
        ).scalars().all()
    return MarketPriceSeriesResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        points=[
            MarketPricePoint(
                t=row.ts,
                midpoint=float(row.midpoint),
                best_bid=float(row.best_bid),
                best_ask=float(row.best_ask),
                bid_depth_usd=float(row.bid_depth_usd),
                ask_depth_usd=float(row.ask_depth_usd),
            )
            for row in reversed(rows)
        ],
    )


@router.get("/probability-timeseries", response_model=ProbabilitySeriesResponse)
async def probability_timeseries(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    limit: Annotated[int, Query(ge=1, le=500)] = 240,
) -> ProbabilitySeriesResponse:
    async with _sessions(request)() as session:
        exists = await session.scalar(select(Market.id).where(Market.id == market_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="market not found")
        model_rows = (
            await session.execute(
                select(ModelProb)
                .where(ModelProb.market_id == market_id)
                .order_by(ModelProb.ts.desc())
                .limit(limit)
            )
        ).scalars().all()
        signal_rows = (
            await session.execute(
                select(Signal)
                .where(Signal.market_id == market_id)
                .order_by(Signal.ts.desc())
                .limit(limit)
            )
        ).scalars().all()

    points = [
        ProbabilityPoint(
            t=row.ts,
            source="model",
            model_probability=float(row.prob),
            market_price=None,
            edge=None,
            sigma=float(row.sigma),
            horizon_h=float(row.horizon_h),
            confidence=None,
            status=None,
        )
        for row in model_rows
    ]
    points.extend(
        ProbabilityPoint(
            t=row.ts,
            source="signal",
            model_probability=float(row.model_prob),
            market_price=float(row.market_price),
            edge=float(row.edge),
            sigma=None,
            horizon_h=None,
            confidence=row.confidence.value,
            status=row.status.value,
        )
        for row in signal_rows
    )
    points.sort(key=lambda point: (point.t, point.source))
    return ProbabilitySeriesResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        points=points[-limit:],
    )


@router.get("/orderbook", response_model=OrderBookResponse)
async def orderbook(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    side: Annotated[str, Query(pattern="^(yes|no)$")] = "yes",
) -> OrderBookResponse:
    settings = cast(Settings, request.app.state.settings)
    async with _sessions(request)() as session:
        market = await session.scalar(select(Market).where(Market.id == market_id))
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")

    token_id = market.yes_token_id if side == "yes" else market.no_token_id
    if not token_id:
        return _empty_orderbook(
            market_id=market_id,
            side=side,
            token_id=None,
            error=f"market has no {side.upper()} token id",
        )

    try:
        async with ClobClient(
            books_url=settings.clob_books_url,
            batch_size=settings.clob_batch_size,
        ) as client:
            books = await client.fetch_books([token_id])
        if not books:
            return _empty_orderbook(
                market_id=market_id,
                side=side,
                token_id=token_id,
                error="CLOB returned no book for token",
            )
        book = books[0]
        bids = _book_levels(book.get("bids"), reverse=True)
        asks = _book_levels(book.get("asks"), reverse=False)
        if not bids or not asks:
            return _empty_orderbook(
                market_id=market_id,
                side=side,
                token_id=token_id,
                error="book has no two-sided liquidity",
            )
        best_bid = bids[0].price
        best_ask = asks[0].price
        midpoint = (best_bid + best_ask) / 2
    except Exception as exc:  # noqa: BLE001 - external CLOB snapshot must fail soft.
        return _empty_orderbook(
            market_id=market_id,
            side=side,
            token_id=token_id,
            error=f"CLOB unavailable: {exc}",
        )

    return OrderBookResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        side=side,
        token_id=token_id,
        source_status="ok",
        error=None,
        midpoint=midpoint,
        best_bid=best_bid,
        best_ask=best_ask,
        bids=bids,
        asks=asks,
    )


@router.get("/calibration", response_model=CalibrationsResponse)
async def calibrations(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> CalibrationsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Calibration, Station.icao)
                .join(Station, Station.id == Calibration.station_id)
                .order_by(Station.icao, Calibration.model, Calibration.horizon_bucket)
                .limit(limit)
            )
        ).all()
    return CalibrationsResponse(
        generated_at=datetime.now(UTC),
        items=[
            CalibrationItem(
                station_icao=icao,
                model=calibration.model,
                horizon_bucket=calibration.horizon_bucket,
                kind=calibration.kind.value,
                bias_c=float(calibration.bias_c),
                sigma_c=float(calibration.sigma_c),
                sample_count=calibration.sample_n,
                brier=_number(calibration.brier),
                updated_at=calibration.updated_at,
            )
            for calibration, icao in rows
        ],
    )


def _command_item(row: Any) -> CommandItem:
    return CommandItem(
        id=row.id,
        request_id=row.request_id,
        command=row.command,
        payload=row.payload,
        actor=row.actor,
        reason=row.reason,
        status=row.status,
        detail=row.detail,
        created_at=row.created_at,
        executed_at=row.executed_at,
    )


@router.post(
    "/commands",
    response_model=CommandAccepted,
    status_code=202,
    dependencies=[Depends(require_configured_operator_key)],
)
async def submit_command(request: Request, body: CommandRequest) -> CommandAccepted:
    error = validate_command(body.command, body.payload)
    if error is not None:
        raise HTTPException(status_code=422, detail=error)
    async with _sessions(request).begin() as session:
        row, created = await enqueue_command(
            session,
            request_id=body.request_id,
            command=body.command,
            payload=body.payload,
            actor=body.actor,
            reason=body.reason,
        )
        item = _command_item(row)
    return CommandAccepted(created=created, item=item)


@router.get("/commands", response_model=CommandsResponse)
async def commands(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CommandsResponse:
    async with _sessions(request)() as session:
        rows = await recent_commands(session, limit=limit)
    return CommandsResponse(
        generated_at=datetime.now(UTC),
        items=[_command_item(row) for row in rows],
    )


async def _position_stats(
    session: AsyncSession, mode: ExecutionMode
) -> list[PositionStat]:
    rows = (
        await session.execute(
            select(
                Position.status,
                Position.pnl_usd,
                Position.clv,
                Position.size_usd,
                Position.fee_usd,
                Position.size_shares,
                Position.avg_price,
            ).where(Position.mode == mode)
        )
    ).all()
    return [
        PositionStat(
            closed=status is PositionStatus.CLOSED,
            pnl_usd=_number(pnl),
            clv=_number(clv),
            cost_usd=float(cost),
            fee_usd=_number(fee),
            size_shares=_number(shares),
            avg_price=_number(price),
        )
        for status, pnl, clv, cost, fee, shares, price in rows
    ]


def _summary_response(
    perf: LivePerformance, trades: list[TradeOutcome]
) -> LiveSummaryResponse:
    briers = brier_comparison(trades)
    return LiveSummaryResponse(
        generated_at=datetime.now(UTC),
        positions=perf.positions,
        closed=perf.closed,
        open=perf.open,
        wins=perf.wins,
        win_rate=perf.win_rate,
        total_pnl_usd=perf.total_pnl_usd,
        robust_pnl_usd=perf.robust_pnl_usd,
        total_cost_usd=perf.total_cost_usd,
        total_fees_usd=perf.total_fees_usd,
        roi=perf.roi,
        robust_roi=perf.robust_roi,
        avg_clv=perf.avg_clv,
        clv_beat_rate=perf.clv_beat_rate,
        skill_pnl_usd=perf.skill_pnl_usd,
        luck_pnl_usd=perf.luck_pnl_usd,
        decomposed=perf.decomposed,
        roi_ci_low=perf.roi_ci_low,
        roi_ci_high=perf.roi_ci_high,
        skill_pnl_ci_low=perf.skill_pnl_ci_low,
        skill_pnl_ci_high=perf.skill_pnl_ci_high,
        statistically_positive=perf.statistically_positive,
        brier_model=briers[0] if briers else None,
        brier_market=briers[1] if briers else None,
        n_resolved_trades=len(trades),
    )


@router.get("/live-summary", response_model=LiveSummaryResponse)
async def live_summary(request: Request) -> LiveSummaryResponse:
    async with _sessions(request)() as session:
        perf = summarize_live_performance(
            await _position_stats(session, ExecutionMode.LIVE)
        )
        trades = await resolved_trade_outcomes(session, ExecutionMode.LIVE)
    return _summary_response(perf, trades)


@router.get("/performance-summary", response_model=LiveSummaryResponse)
async def performance_summary(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> LiveSummaryResponse:
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        perf = summarize_live_performance(
            await _position_stats(session, selected_mode)
        )
        trades = await resolved_trade_outcomes(session, selected_mode)
    return _summary_response(perf, trades)


@router.get("/edge-report", response_model=EdgeReportResponse)
async def edge_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> EdgeReportResponse:
    """Is the predicted edge real? Brier model-vs-market, skill/luck split,
    bootstrap CIs and realised return per predicted-edge bucket."""
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    settings = _settings(request)
    async with _sessions(request)() as session:
        perf = summarize_live_performance(
            await _position_stats(session, selected_mode)
        )
        trades = await resolved_trade_outcomes(session, selected_mode)
        gate = await calculate_live_gate(session, settings.initial_bankroll_usd)
    briers = brier_comparison(trades)
    return EdgeReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        n_resolved_trades=len(trades),
        brier_model=briers[0] if briers else None,
        brier_market=briers[1] if briers else None,
        skill_pnl_usd=perf.skill_pnl_usd,
        luck_pnl_usd=perf.luck_pnl_usd,
        roi_ci_low=perf.roi_ci_low,
        roi_ci_high=perf.roi_ci_high,
        skill_pnl_ci_low=perf.skill_pnl_ci_low,
        skill_pnl_ci_high=perf.skill_pnl_ci_high,
        statistically_positive=perf.statistically_positive,
        edge_slope=edge_slope(trades),
        buckets=[
            EdgeBucketItem(
                low=bucket.low,
                high=bucket.high,
                count=bucket.count,
                avg_predicted_edge=bucket.avg_predicted_edge,
                avg_realized_return=bucket.avg_realized_return,
            )
            for bucket in edge_calibration(trades)
        ],
        gate_passed=gate.passed,
        gate_checks=[
            GateCheckItem(name=check.name, passed=check.passed, detail=check.detail)
            for check in gate.checks
        ],
    )


def _brier_slice_item(slice_: BrierSlice) -> BrierSliceItem:
    return BrierSliceItem(
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        brier_model=slice_.brier_model,
        brier_market=slice_.brier_market,
        model_minus_market=slice_.model_minus_market,
        brier_raw_model=slice_.brier_raw_model,
        raw_model_minus_market=slice_.raw_model_minus_market,
        raw_count=slice_.raw_count,
        avg_predicted_edge=slice_.avg_predicted_edge,
        avg_realized_return=slice_.avg_realized_return,
        win_rate=slice_.win_rate,
    )


@router.get("/brier-report", response_model=BrierReportResponse)
async def brier_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> BrierReportResponse:
    """Root-cause where the model is worse or better than the market.

    Positive *_minus_market values mean the model has higher Brier error than
    the market in that slice. raw_model fields use the pre-shrink probability
    when component metadata is available.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        trades = await resolved_trade_outcomes(session, selected_mode)
    briers = brier_comparison(trades)
    raw = raw_model_brier(trades)
    brier_model = briers[0] if briers else None
    brier_market = briers[1] if briers else None
    brier_raw_model = raw[0] if raw else None
    return BrierReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        n_resolved_trades=len(trades),
        brier_model=brier_model,
        brier_market=brier_market,
        brier_raw_model=brier_raw_model,
        raw_count=raw[1] if raw else 0,
        model_minus_market=(
            round(brier_model - brier_market, 6)
            if brier_model is not None and brier_market is not None
            else None
        ),
        raw_model_minus_market=(
            round(brier_raw_model - brier_market, 6)
            if brier_raw_model is not None and brier_market is not None
            else None
        ),
        slices=[_brier_slice_item(slice_) for slice_ in brier_root_cause(trades)],
    )


def _clv_slice_item(slice_: ClvSlice) -> ClvSliceItem:
    return ClvSliceItem(
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        avg_clv=slice_.avg_clv,
        clv_beat_rate=slice_.clv_beat_rate,
        avg_entry_price=slice_.avg_entry_price,
        avg_fee_usd=slice_.avg_fee_usd,
        avg_realized_return=slice_.avg_realized_return,
        win_rate=slice_.win_rate,
    )


def _brier_action_item(slice_: BrierSlice) -> RootCauseActionItem:
    if slice_.count < 10:
        action = "Watch"
        severity = "warning"
        detail = (
            "Action: keep collecting until n≥10 for this slice; "
            "current model-vs-market gap may be noise."
        )
    elif slice_.model_minus_market <= 0:
        action = "Keep"
        severity = "positive"
        detail = (
            "Action: no model restriction; model is not worse than "
            "the market in this slice."
        )
    elif slice_.count >= 30 and slice_.model_minus_market >= 0.05:
        action = "Block cand."
        severity = "negative"
        detail = (
            "Action: candidate for block/recalibration rule; model is "
            "materially worse than market with enough sample."
        )
    elif slice_.model_minus_market >= 0.02:
        action = "Reduce"
        severity = "warning"
        detail = "Action: reduce sizing or require stronger edge until Brier delta improves."
    else:
        action = "Watch"
        severity = "warning"
        detail = "Action: mild model drag; monitor before changing rules."

    return RootCauseActionItem(
        metric="brier",
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        action=action,
        severity=severity,
        detail=detail,
    )


def _clv_action_item(slice_: ClvSlice) -> RootCauseActionItem:
    if slice_.count < 10:
        action = "Watch"
        severity = "warning"
        detail = (
            "Action: keep collecting until n≥10 for this slice; "
            "current CLV may be noisy."
        )
    elif slice_.avg_clv >= 0:
        action = "Keep"
        severity = "positive"
        detail = "Action: execution quality acceptable; entries beat or match the closing line."
    elif slice_.count >= 30 and slice_.avg_clv <= -0.03:
        action = "Block cand."
        severity = "negative"
        detail = (
            "Action: candidate for block/maker-only rule; repeated entries "
            "are far worse than closing line."
        )
    elif slice_.avg_clv <= -0.01:
        action = "Maker only"
        severity = "warning"
        detail = (
            "Action: avoid taker-style fills; prefer passive midpoint/maker "
            "entry or reduce size."
        )
    else:
        action = "Watch"
        severity = "warning"
        detail = "Action: slight negative CLV; monitor entry quality before changing rules."

    return RootCauseActionItem(
        metric="clv",
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        action=action,
        severity=severity,
        detail=detail,
    )


def _action_rank(item: RootCauseActionItem) -> tuple[int, str, str, str]:
    severity_rank = {"negative": 0, "warning": 1, "positive": 2}.get(item.severity, 3)
    return severity_rank, item.metric, item.dimension, item.value


@router.get("/clv-report", response_model=ClvReportResponse)
async def clv_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> ClvReportResponse:
    """Entry-quality root cause: where do we enter worse than the market?

    Closing-line value by station, kind, horizon, confidence, side and entry
    price band. Negative avg_clv in a slice means we systematically pay up
    (spread / slippage / adverse selection) entering those trades. Slices are
    sorted worst-CLV first within each dimension.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        outcomes = await resolved_clv_outcomes(session, selected_mode)
    summary = clv_summary(outcomes)
    return ClvReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        n_closed_positions=len(outcomes),
        avg_clv=summary[0] if summary else None,
        clv_beat_rate=summary[1] if summary else None,
        slices=[_clv_slice_item(slice_) for slice_ in clv_root_cause(outcomes)],
    )


@router.get("/root-cause-actions", response_model=RootCauseActionsResponse)
async def root_cause_actions(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> RootCauseActionsResponse:
    """Advisory actions for diagnostic slices.

    These are operator hints only: they do not change signal generation,
    sizing, station eligibility, or runtime state. Keeping the policy in the
    backend gives Cockpit, Telegram, and future reports one source of truth.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        trades = await resolved_trade_outcomes(session, selected_mode)
        clv_outcomes = await resolved_clv_outcomes(session, selected_mode)
    items = [
        *[_brier_action_item(slice_) for slice_ in brier_root_cause(trades)],
        *[_clv_action_item(slice_) for slice_ in clv_root_cause(clv_outcomes)],
    ]
    return RootCauseActionsResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        items=sorted(items, key=_action_rank),
    )


async def _brier_drilldown_items(
    session: AsyncSession,
    mode: ExecutionMode,
    dimension: str,
    value: str,
    limit: int,
) -> tuple[int, list[RootCauseTradeItem]]:
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
                Order.id,
                Signal.id,
                Market.id,
                Market.polymarket_id,
                Station.icao,
                Market.market_date,
                Market.kind,
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
                Market.resolves_at,
                Market.resolved_outcome,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .outerjoin(Station, Market.station_id == Station.id)
            .where(
                Order.mode == mode,
                Order.status.in_(_FILLED_ORDER_STATUSES),
                Order.filled_usd > 0,
                Market.resolved_outcome.is_not(None),
            )
        )
    ).all()

    items: list[RootCauseTradeItem] = []
    for (
        order_id,
        signal_id,
        market_id,
        polymarket_id,
        station_icao,
        market_date,
        kind,
        side,
        price,
        filled_usd,
        fee_usd,
        model_prob,
        signal_market_price,
        edge,
        confidence,
        signal_ts,
        midpoint,
        components,
        resolves_at,
        resolved_outcome,
    ) in rows:
        entry_price = float(price)
        if entry_price <= 0:
            continue
        horizon = _root_cause_horizon_bucket(signal_ts, resolves_at)
        slice_value = _root_cause_dimension_value(
            dimension,
            station_icao=station_icao,
            kind=kind,
            horizon_bucket=horizon,
            confidence=confidence,
            side=side,
        )
        if slice_value != value:
            continue

        won = (side is TradeSide.BUY_YES) == bool(resolved_outcome)
        shares = float(filled_usd) / entry_price
        cost = float(filled_usd) + float(fee_usd or 0)
        pnl = (shares if won else 0.0) - cost
        model_side = _side_adjusted_probability(float(model_prob), side)
        if midpoint is not None:
            market_side = _side_adjusted_probability(float(midpoint), side)
        else:
            market_side = float(signal_market_price)
        raw_side = _root_cause_raw_model_probability(components, side)
        outcome = float(won)
        model_error = (model_side - outcome) ** 2
        market_error = (market_side - outcome) ** 2
        items.append(
            RootCauseTradeItem(
                id=int(order_id),
                record_type="order",
                order_id=int(order_id),
                signal_id=int(signal_id),
                position_id=None,
                market_id=int(market_id),
                polymarket_id=str(polymarket_id),
                station_icao=station_icao,
                market_date=market_date,
                kind=_enum_text(kind) or "unknown",
                side=_enum_text(side) or "unknown",
                confidence=_enum_text(confidence),
                horizon_bucket=horizon,
                timestamp=signal_ts,
                closed_at=None,
                entry_price=round(entry_price, 6),
                closing_line_price=None,
                model_probability=round(model_side, 6),
                market_probability=round(market_side, 6),
                raw_model_probability=round(raw_side, 6) if raw_side is not None else None,
                predicted_edge=round(float(edge), 6),
                brier_model_error=round(model_error, 6),
                brier_market_error=round(market_error, 6),
                brier_delta=round(model_error - market_error, 6),
                clv=None,
                pnl_usd=round(pnl, 4),
                cost_usd=round(cost, 4),
                fee_usd=_number(fee_usd),
                won=won,
                resolved_outcome=bool(resolved_outcome),
            )
        )

    items.sort(key=lambda item: item.brier_delta or 0, reverse=True)
    return len(items), items[:limit]


async def _clv_drilldown_items(
    session: AsyncSession,
    mode: ExecutionMode,
    dimension: str,
    value: str,
    limit: int,
) -> tuple[int, list[RootCauseTradeItem]]:
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
    representative_signal_id = (
        select(Signal.id)
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
    representative_model_prob = (
        select(Signal.model_prob)
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
    representative_market_price = (
        select(Signal.market_price)
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
    representative_edge = (
        select(Signal.edge)
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
                Position.id,
                Market.id,
                Market.polymarket_id,
                Station.icao,
                Market.market_date,
                Market.kind,
                Position.side,
                Position.avg_price,
                Position.size_usd,
                Position.fee_usd,
                Position.pnl_usd,
                Position.clv,
                Position.closed_at,
                Market.resolves_at,
                Market.resolved_outcome,
                representative_signal_id,
                representative_confidence,
                representative_signal_ts,
                representative_model_prob,
                representative_market_price,
                representative_edge,
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

    items: list[RootCauseTradeItem] = []
    for (
        position_id,
        market_id,
        polymarket_id,
        station_icao,
        market_date,
        kind,
        side,
        avg_price,
        size_usd,
        fee_usd,
        pnl_usd,
        clv,
        closed_at,
        resolves_at,
        resolved_outcome,
        signal_id,
        confidence,
        signal_ts,
        model_prob,
        signal_market_price,
        edge,
    ) in rows:
        entry_price = float(avg_price)
        clv_value = float(clv)
        horizon = _root_cause_horizon_bucket(signal_ts, resolves_at)
        slice_value = _root_cause_dimension_value(
            dimension,
            station_icao=station_icao,
            kind=kind,
            horizon_bucket=horizon,
            confidence=confidence,
            side=side,
            entry_price=entry_price,
        )
        if slice_value != value:
            continue
        won = (side is TradeSide.BUY_YES) == bool(resolved_outcome)
        model_side = (
            _side_adjusted_probability(float(model_prob), side)
            if model_prob is not None
            else None
        )
        items.append(
            RootCauseTradeItem(
                id=int(position_id),
                record_type="position",
                order_id=None,
                signal_id=int(signal_id) if signal_id is not None else None,
                position_id=int(position_id),
                market_id=int(market_id),
                polymarket_id=str(polymarket_id),
                station_icao=station_icao,
                market_date=market_date,
                kind=_enum_text(kind) or "unknown",
                side=_enum_text(side) or "unknown",
                confidence=_enum_text(confidence),
                horizon_bucket=horizon,
                timestamp=signal_ts,
                closed_at=closed_at,
                entry_price=round(entry_price, 6),
                closing_line_price=round(entry_price + clv_value, 6),
                model_probability=round(model_side, 6) if model_side is not None else None,
                market_probability=(
                    round(float(signal_market_price), 6)
                    if signal_market_price is not None
                    else None
                ),
                raw_model_probability=None,
                predicted_edge=round(float(edge), 6) if edge is not None else None,
                brier_model_error=None,
                brier_market_error=None,
                brier_delta=None,
                clv=round(clv_value, 6),
                pnl_usd=round(float(pnl_usd or 0), 4),
                cost_usd=round(float(size_usd), 4),
                fee_usd=_number(fee_usd),
                won=won,
                resolved_outcome=bool(resolved_outcome),
            )
        )

    items.sort(key=lambda item: item.clv if item.clv is not None else 0)
    return len(items), items[:limit]


@router.get("/root-cause-drilldown", response_model=RootCauseDrilldownResponse)
async def root_cause_drilldown(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
    metric: Annotated[str, Query(pattern="^(brier|clv)$")] = "brier",
    dimension: Annotated[str, Query(min_length=1, max_length=32)] = "station",
    value: Annotated[str, Query(min_length=1, max_length=64)] = "unknown",
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> RootCauseDrilldownResponse:
    """Concrete trades/positions behind a diagnostic slice.

    This powers Cockpit drill-down: clicking "LFPB", "6-24h", "buy_yes",
    etc. shows the rows that made that Brier or CLV slice bad.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    if metric == "brier":
        if dimension not in _BRIER_DRILLDOWN_DIMENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"dimension '{dimension}' is not available for brier",
            )
        async with _sessions(request)() as session:
            total_count, items = await _brier_drilldown_items(
                session, selected_mode, dimension, value, limit
            )
    else:
        if dimension not in _CLV_DRILLDOWN_DIMENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"dimension '{dimension}' is not available for clv",
            )
        async with _sessions(request)() as session:
            total_count, items = await _clv_drilldown_items(
                session, selected_mode, dimension, value, limit
            )
    return RootCauseDrilldownResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        metric=metric,
        dimension=dimension,
        value=value,
        count=total_count,
        items=items,
    )


async def _equity_response(request: Request, mode: ExecutionMode) -> EquityResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Position.closed_at, Position.pnl_usd).where(
                    Position.mode == mode,
                    Position.status == PositionStatus.CLOSED,
                    Position.closed_at.is_not(None),
                )
            )
        ).all()
    points = cumulative_equity(
        [(ts, float(pnl or 0)) for ts, pnl in rows if ts is not None]
    )
    return EquityResponse(
        generated_at=datetime.now(UTC),
        points=[
            EquityPointItem(t=p.ts, cumulative_pnl_usd=p.cumulative_pnl_usd)
            for p in points
        ],
    )


@router.get("/live-equity", response_model=EquityResponse)
async def live_equity(request: Request) -> EquityResponse:
    return await _equity_response(request, ExecutionMode.LIVE)


@router.get("/equity", response_model=EquityResponse)
async def equity(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> EquityResponse:
    """Cumulative realised PnL for either execution mode."""
    return await _equity_response(
        request, ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    )


@router.get("/orders", response_model=OrdersResponse)
async def orders(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OrdersResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Order, Market.polymarket_id, Station.icao)
                .join(Signal, Signal.id == Order.signal_id)
                .join(Market, Market.id == Signal.market_id)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Order.id.desc())
                .limit(limit)
            )
        ).all()
    return OrdersResponse(
        generated_at=datetime.now(UTC),
        items=[
            OrderItem(
                id=order.id,
                signal_id=order.signal_id,
                polymarket_id=polymarket_id,
                station_icao=icao,
                mode=order.mode.value,
                clob_order_id=order.clob_order_id,
                side=order.side.value,
                price=float(order.price),
                size_usd=float(order.size_usd),
                filled_usd=float(order.filled_usd),
                fee_usd=_number(order.fee_usd),
                status=order.status,
                created_at=order.created_at,
                filled_at=order.filled_at,
            )
            for order, polymarket_id, icao in rows
        ],
    )


@router.get("/jobs", response_model=JobsResponse)
async def jobs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> JobsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(JobRun).order_by(JobRun.finished_at.desc()).limit(limit)
            )
        ).scalars().all()
    return JobsResponse(
        generated_at=datetime.now(UTC),
        items=[
            JobItem(
                id=job.id,
                job_id=job.job_id,
                scheduled_at=job.scheduled_at,
                finished_at=job.finished_at,
                status=job.status,
                error=job.error,
            )
            for job in rows
        ],
    )
