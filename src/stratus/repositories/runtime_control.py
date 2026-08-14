from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionMode,
    RuntimeControl,
)


@dataclass(frozen=True, slots=True)
class RuntimeSnapshot:
    kill_switch: bool
    mode: ExecutionMode
    live_confirmation_until: datetime | None
    halted_reason: str | None


async def get_runtime_control(session: AsyncSession) -> RuntimeSnapshot:
    control = await session.scalar(select(RuntimeControl).where(RuntimeControl.id == 1))
    if control is None:
        # Migration 0009 seeds this row; a missing row is an abnormal state,
        # so fail closed instead of defaulting to "trading allowed".
        return RuntimeSnapshot(True, ExecutionMode.PAPER, None, "runtime_control row missing")
    if not control.kill_switch:
        execution_authorized = await session.scalar(
            select(func.paper_canary_execution_authorized())
        )
        if execution_authorized is not True:
            # Read-side defence in depth: execution jobs fail closed even if
            # the independent watchdog process is unavailable at lease expiry.
            # The runtime login receives only this boolean authority bit; RLS
            # keeps lease identities and clocks inside the safety boundary.
            return RuntimeSnapshot(
                True,
                control.mode,
                None,
                ("Polymarket PAPER canary lease is absent, unobserved, watchdog-stale, or expired"),
            )
    return RuntimeSnapshot(
        kill_switch=control.kill_switch,
        mode=control.mode,
        live_confirmation_until=control.live_confirmation_until,
        halted_reason=control.halted_reason,
    )


async def set_kill_switch(
    session: AsyncSession,
    enabled: bool,
    *,
    expiration_reason: str = "kill_switch",
    request_id: str | None = None,
    actor: str = "stratus-runtime-stop",
    reason: str | None = None,
) -> int:
    if enabled:
        stop_request_id = request_id or (f"paper-runtime-stop-{secrets.token_hex(16)}")
        stop_reason = reason or (
            f"Runtime safety stop engaged Polymarket controls; cause={expiration_reason}."
        )
        expired = await session.scalar(
            select(
                func.engage_paper_canary_runtime_stop(
                    stop_request_id,
                    actor,
                    stop_reason,
                )
            )
        )
        if expired is None:
            raise RuntimeError("database PAPER stop returned no result")
        return int(expired)

    control = await get_runtime_control(session)
    if control.halted_reason is not None:
        raise RuntimeError(f"project is halted: {control.halted_reason}")
    await session.execute(
        update(RuntimeControl)
        .where(RuntimeControl.id == 1)
        .values(kill_switch=enabled, updated_at=datetime.now(UTC))
    )
    return 0


async def set_mode(session: AsyncSession, mode: ExecutionMode) -> None:
    await session.execute(
        update(RuntimeControl)
        .where(RuntimeControl.id == 1)
        .values(
            mode=mode,
            live_confirmation_until=None,
            updated_at=datetime.now(UTC),
        )
    )


async def request_live_confirmation(session: AsyncSession) -> datetime:
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    await session.execute(
        update(RuntimeControl)
        .where(RuntimeControl.id == 1)
        .values(live_confirmation_until=deadline, updated_at=datetime.now(UTC))
    )
    return deadline
