from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import OperatorCommand
from stratus.db.session import dml_rowcount


async def enqueue_command(
    session: AsyncSession,
    *,
    request_id: str,
    command: str,
    payload: dict[str, Any],
    actor: str,
    reason: str,
) -> tuple[OperatorCommand, bool]:
    """Insert a command; idempotent on request_id.

    Returns the (possibly pre-existing) row and whether this call created it,
    so a retried console request never queues the same operation twice.
    """
    statement = (
        insert(OperatorCommand)
        .values(
            request_id=request_id,
            command=command,
            payload=payload,
            actor=actor,
            reason=reason,
        )
        .on_conflict_do_nothing(index_elements=[OperatorCommand.request_id])
    )
    created = bool(dml_rowcount(await session.execute(statement)))
    row = (
        await session.execute(
            select(OperatorCommand).where(OperatorCommand.request_id == request_id)
        )
    ).scalar_one()
    return row, created


async def claim_pending_commands(
    session: AsyncSession, limit: int = 10
) -> list[OperatorCommand]:
    rows = (
        await session.execute(
            select(OperatorCommand)
            .where(OperatorCommand.status == "pending")
            .order_by(OperatorCommand.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    return list(rows)


def mark_command(
    command: OperatorCommand, *, status: str, detail: str, executed_at: datetime
) -> None:
    command.status = status
    command.detail = detail
    command.executed_at = executed_at


async def recent_commands(
    session: AsyncSession, limit: int = 50
) -> list[OperatorCommand]:
    rows = (
        await session.execute(
            select(OperatorCommand).order_by(OperatorCommand.id.desc()).limit(limit)
        )
    ).scalars().all()
    return list(rows)
