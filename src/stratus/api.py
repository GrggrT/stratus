from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from stratus.config import Settings, get_settings
from stratus.db.models import HealthAudit
from stratus.db.session import (
    RuntimeDatabaseBoundaryError,
    RuntimeReadCapacityError,
    create_engine,
    create_read_session_factory,
    create_session_factory,
    verify_runtime_engine,
)
from stratus.operator_api import (
    AccuracyOverviewResponseCache,
    EvidenceContinuityResponseCache,
)
from stratus.operator_api import router as operator_router
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.health import health_status_blocks_readiness
from stratus.services.http_telemetry import (
    RuntimeHttpTelemetry,
    normalize_load_gate_run_id,
)

logger = logging.getLogger(__name__)

_MAX_CONCURRENT_OPERATOR_READS = 3
_OPERATOR_READ_QUEUE_TIMEOUT_SECONDS = 2.5
_LOAD_GATE_RUN_HEADER = "X-Stratus-Load-Gate-Run"


def _operator_read_capacity(settings: Settings) -> int:
    total_pool_capacity = (
        settings.stratus_database_pool_size
        + settings.stratus_database_max_overflow
    )
    if total_pool_capacity < 2:
        raise ValueError(
            "the operator API database pool requires at least two total connections"
        )
    return min(_MAX_CONCURRENT_OPERATOR_READS, total_pool_capacity - 1)


@dataclass(frozen=True, slots=True)
class ReadinessState:
    ready: bool
    reasons: tuple[str, ...]


def evaluate_readiness(
    *,
    audit_status: str | None,
    audit_time: datetime | None,
    kill_switch: bool,
    halted_reason: str | None,
    now: datetime,
) -> ReadinessState:
    reasons = []
    if audit_status is None or audit_time is None:
        reasons.append("health_audit_missing")
    else:
        if now - audit_time > timedelta(minutes=20):
            reasons.append("health_audit_stale")
        normalized_status = audit_status.strip().lower()
        if health_status_blocks_readiness(normalized_status):
            reasons.append(f"health_audit_{normalized_status}")
    if kill_switch:
        reasons.append("kill_switch")
    if halted_reason:
        reasons.append("project_halted")
    return ReadinessState(not reasons, tuple(reasons))


async def require_operator_api_key(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Enforce OPERATOR_API_KEY on monitoring endpoints when configured.

    The health probes stay open; they carry no market or bankroll data.
    """
    expected = cast(Settings, request.app.state.settings).operator_api_key
    if expected is None:
        return
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    operator_read_capacity = _operator_read_capacity(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        total_pool_capacity = (
            settings.stratus_database_pool_size
            + settings.stratus_database_max_overflow
        )
        write_pool_capacity = total_pool_capacity - operator_read_capacity
        engine = create_engine(
            settings,
            pool_size=write_pool_capacity,
            max_overflow=0,
        )
        read_engine = create_engine(
            settings,
            pool_size=operator_read_capacity,
            max_overflow=0,
            read_only=True,
        )
        try:
            await verify_runtime_engine(engine)
            await verify_runtime_engine(read_engine)
            app.state.sessions = create_session_factory(engine)
            app.state.read_sessions = create_read_session_factory(
                read_engine,
                max_concurrent=operator_read_capacity,
                queue_timeout_seconds=_OPERATOR_READ_QUEUE_TIMEOUT_SECONDS,
            )
            yield
        finally:
            cache = cast(
                AccuracyOverviewResponseCache,
                app.state.accuracy_overview_response_cache,
            )
            try:
                await cache.shutdown()
            finally:
                try:
                    await read_engine.dispose()
                finally:
                    await engine.dispose()

    app = FastAPI(
        title="STRATUS local API",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.operator_read_gate = asyncio.Semaphore(operator_read_capacity)
    app.state.http_telemetry = RuntimeHttpTelemetry()
    app.state.accuracy_overview_response_cache = AccuracyOverviewResponseCache()
    app.state.evidence_continuity_response_cache = EvidenceContinuityResponseCache()
    app.include_router(operator_router, dependencies=[Depends(require_operator_api_key)])

    @app.middleware("http")
    async def bound_operator_reads(
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        path = request.url.path
        is_operator_api = path.startswith("/api/v1/")
        run_id = normalize_load_gate_run_id(
            request.headers.get(_LOAD_GATE_RUN_HEADER)
        )
        telemetry = cast(RuntimeHttpTelemetry, request.app.state.http_telemetry)
        attribution_token = (
            telemetry.begin_api_request(run_id) if is_operator_api else None
        )
        telemetry_finished = False

        def observed(response: Response) -> Response:
            nonlocal telemetry_finished
            if is_operator_api:
                telemetry.record_api_response(
                    status_code=response.status_code,
                    attribution_token=attribution_token,
                )
                telemetry_finished = True
            return response

        try:
            requires_database_read_slot = request.method == "GET" and (
                path == "/readyz" or is_operator_api
            )
            if not requires_database_read_slot:
                return observed(await call_next(request))

            gate = cast(asyncio.Semaphore, request.app.state.operator_read_gate)
            try:
                async with asyncio.timeout(_OPERATOR_READ_QUEUE_TIMEOUT_SECONDS):
                    await gate.acquire()
            except TimeoutError:
                return observed(
                    JSONResponse(
                        status_code=503,
                        content={
                            "detail": "operator read capacity is temporarily saturated",
                            "retryable": True,
                        },
                        headers={"Retry-After": "1"},
                    )
                )
            try:
                return observed(await call_next(request))
            finally:
                gate.release()
        finally:
            if is_operator_api and not telemetry_finished:
                telemetry.record_api_abort(attribution_token=attribution_token)

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def database_pool_timeout(
        _request: Request,
        _error: SQLAlchemyTimeoutError,
    ) -> JSONResponse:
        logger.warning("runtime database pool capacity exhausted")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "database connection capacity is temporarily unavailable",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(RuntimeReadCapacityError)
    async def runtime_read_capacity_timeout(
        _request: Request,
        _error: RuntimeReadCapacityError,
    ) -> JSONResponse:
        logger.warning("runtime read-session capacity saturated")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "operator read capacity is temporarily saturated",
                "retryable": True,
            },
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(RuntimeDatabaseBoundaryError)
    async def runtime_database_boundary_failure(
        _request: Request,
        _error: RuntimeDatabaseBoundaryError,
    ) -> JSONResponse:
        logger.error("runtime database boundary rejected a checkout")
        return JSONResponse(
            status_code=503,
            content={
                "detail": "runtime database boundary rejected",
                "retryable": False,
            },
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get(
        "/api/v1/runtime-http-counters",
        dependencies=[Depends(require_operator_api_key)],
    )
    async def runtime_http_counters(
        x_stratus_load_gate_run: Annotated[
            str | None,
            Header(alias=_LOAD_GATE_RUN_HEADER),
        ] = None,
    ) -> dict[str, object]:
        run_id = normalize_load_gate_run_id(x_stratus_load_gate_run)
        if run_id is None:
            raise HTTPException(
                status_code=422,
                detail="a 32-hex load-gate run ID is required",
            )
        telemetry = cast(RuntimeHttpTelemetry, app.state.http_telemetry)
        snapshot = telemetry.snapshot(run_id)
        return {
            "run_id": snapshot.run_id,
            "run_started": snapshot.run_started,
            "run_completed": snapshot.run_completed,
            "run_aborted": snapshot.run_aborted,
            "run_5xx": snapshot.run_5xx,
            "total_started": snapshot.total_started,
            "total_completed": snapshot.total_completed,
            "total_aborted": snapshot.total_aborted,
            "total_5xx": snapshot.total_5xx,
            "active_requests": snapshot.active_requests,
            "untagged_api_started": snapshot.untagged_api_started,
            "untagged_api_completed": snapshot.untagged_api_completed,
            "untagged_api_5xx": snapshot.untagged_api_5xx,
        }

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            read_sessions = getattr(app.state, "read_sessions", None)
            if read_sessions is None:
                raise RuntimeError("readiness read-session factory is not initialized")
            async with read_sessions() as session:
                read_only_setting = await session.scalar(
                    text("SELECT current_setting('default_transaction_read_only')")
                )
                if read_only_setting != "on":
                    raise RuntimeError(
                        "database read lane is not enforced read-only"
                    )
                audit = await session.scalar(
                    select(HealthAudit).order_by(HealthAudit.ts.desc()).limit(1)
                )
                control = await get_runtime_control(session)
        except Exception:
            logger.exception("readiness database check failed")
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "reasons": ["database_unavailable"]},
            )
        state = evaluate_readiness(
            audit_status=audit.status if audit else None,
            audit_time=audit.ts if audit else None,
            kill_switch=control.kill_switch,
            halted_reason=control.halted_reason,
            now=datetime.now(UTC),
        )
        return JSONResponse(
            status_code=200 if state.ready else 503,
            content={
                "status": "ready" if state.ready else "not_ready",
                "reasons": list(state.reasons),
            },
        )

    return app


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
