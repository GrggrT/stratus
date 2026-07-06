from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    ExecutionMode,
    Order,
    RuntimeControl,
    Signal,
    SignalStatus,
)
from stratus.db.session import dml_rowcount


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
        return RuntimeSnapshot(
            True, ExecutionMode.PAPER, None, "runtime_control row missing"
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
) -> int:
    control = await get_runtime_control(session)
    if not enabled and control.halted_reason is not None:
        raise RuntimeError(f"project is halted: {control.halted_reason}")
    await session.execute(
        update(RuntimeControl)
        .where(RuntimeControl.id == 1)
        .values(kill_switch=enabled, updated_at=datetime.now(UTC))
    )
    if not enabled:
        return 0
    result = await session.execute(
        update(Signal)
        .where(Signal.status == SignalStatus.PENDING)
        .values(status=SignalStatus.EXPIRED, skip_reason=expiration_reason)
    )
    await session.execute(
        update(Order)
        .where(Order.mode == ExecutionMode.LIVE, Order.status.in_(["open", "pending"]))
        .values(status="cancel_requested")
    )
    return dml_rowcount(result)


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
