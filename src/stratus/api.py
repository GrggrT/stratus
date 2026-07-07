from __future__ import annotations

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
from sqlalchemy import select

from stratus.config import Settings, get_settings
from stratus.db.models import HealthAudit
from stratus.db.session import create_engine, create_session_factory
from stratus.operator_api import router as operator_router
from stratus.repositories.runtime_control import get_runtime_control

logger = logging.getLogger(__name__)


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
        if audit_status == "critical":
            reasons.append("health_audit_critical")
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.sessions = create_session_factory(engine)
        try:
            yield
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
    app.include_router(operator_router, dependencies=[Depends(require_operator_api_key)])

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        try:
            async with app.state.sessions() as session:
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
