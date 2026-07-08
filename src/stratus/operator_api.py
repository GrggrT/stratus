from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings
from stratus.db.models import (
    BankrollLog,
    Calibration,
    ExecutionMode,
    HealthAudit,
    JobRun,
    Market,
    MarketStatus,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
    ValidationCheckpoint,
)
from stratus.repositories.operator_commands import enqueue_command, recent_commands
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.live_analytics import (
    PositionStat,
    cumulative_equity,
    summarize_live_performance,
)
from stratus.services.operator_commands import validate_command

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
    polymarket_id: str
    station_icao: str | None
    mode: str
    side: str
    average_price: float
    size_usd: float
    fee_usd: float | None
    status: str
    pnl_usd: float | None
    clv: float | None
    closed_at: datetime | None


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
    total_cost_usd: float
    total_fees_usd: float
    roi: float | None
    avg_clv: float | None
    clv_beat_rate: float | None


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


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


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
                select(Position, Market.polymarket_id, Station.icao)
                .join(Market, Market.id == Position.market_id)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Position.id.desc())
                .limit(limit)
            )
        ).all()
    return PositionsResponse(
        generated_at=datetime.now(UTC),
        items=[
            PositionItem(
                id=position.id,
                polymarket_id=polymarket_id,
                station_icao=icao,
                mode=position.mode.value,
                side=position.side.value,
                average_price=float(position.avg_price),
                size_usd=float(position.size_usd),
                fee_usd=_number(position.fee_usd),
                status=position.status.value,
                pnl_usd=_number(position.pnl_usd),
                clv=_number(position.clv),
                closed_at=position.closed_at,
            )
            for position, polymarket_id, icao in rows
        ],
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


async def _live_position_stats(session: AsyncSession) -> list[PositionStat]:
    rows = (
        await session.execute(
            select(
                Position.status,
                Position.pnl_usd,
                Position.clv,
                Position.size_usd,
                Position.fee_usd,
            ).where(Position.mode == ExecutionMode.LIVE)
        )
    ).all()
    return [
        PositionStat(
            closed=status is PositionStatus.CLOSED,
            pnl_usd=_number(pnl),
            clv=_number(clv),
            cost_usd=float(cost),
            fee_usd=_number(fee),
        )
        for status, pnl, clv, cost, fee in rows
    ]


@router.get("/live-summary", response_model=LiveSummaryResponse)
async def live_summary(request: Request) -> LiveSummaryResponse:
    async with _sessions(request)() as session:
        perf = summarize_live_performance(await _live_position_stats(session))
    return LiveSummaryResponse(
        generated_at=datetime.now(UTC),
        positions=perf.positions,
        closed=perf.closed,
        open=perf.open,
        wins=perf.wins,
        win_rate=perf.win_rate,
        total_pnl_usd=perf.total_pnl_usd,
        total_cost_usd=perf.total_cost_usd,
        total_fees_usd=perf.total_fees_usd,
        roi=perf.roi,
        avg_clv=perf.avg_clv,
        clv_beat_rate=perf.clv_beat_rate,
    )


@router.get("/live-equity", response_model=EquityResponse)
async def live_equity(request: Request) -> EquityResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Position.closed_at, Position.pnl_usd).where(
                    Position.mode == ExecutionMode.LIVE,
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
