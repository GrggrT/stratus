from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import OperatorCommand, TradingVenue

EMERGENCY_STOP_COMMAND = "stop"
EMERGENCY_STOP_PAYLOAD: dict[str, str] = {
    "venue": TradingVenue.POLYMARKET.value
}


class EmergencyStopIdentityCollisionError(RuntimeError):
    """A request id is already bound to a different audited operation."""


class EmergencyStopCommandStateError(RuntimeError):
    """An exact request exists, but its terminal state cannot be replayed."""


@dataclass(frozen=True, slots=True)
class EmergencyStopResult:
    command: OperatorCommand
    created: bool
    idempotent: bool
    expired_signals: int
    cancel_requested_live_orders: int
    rejected_pending_resume: int


async def execute_polymarket_emergency_stop(
    session: AsyncSession,
    *,
    request_id: str,
    actor: str,
    reason: str,
    now: datetime | None = None,
    lease_terminal_state: str = "revoked_stopped",
    revoke_active_lease: bool = True,
) -> EmergencyStopResult:
    """Invoke the exact safety-login stop primitive.

    The safety login owns no DML on controls, commands, signals, or orders.
    PostgreSQL validates ``session_user`` and performs the complete audited
    transition under one advisory lock and one transaction. Cleanup counts
    report rows claimed by this bounded, nonblocking invocation. Replaying the
    same succeeded request is idempotent for audit identity and advances any
    cleanup rows that were locked or beyond the previous batch window.
    """
    if now is not None:
        raise ValueError("safety stop time is owned by PostgreSQL")
    if lease_terminal_state != "revoked_stopped" or not revoke_active_lease:
        raise ValueError(
            "direct safety stop only supports revoked_stopped terminalization"
        )
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM public.safety_paper_canary_stop(
                        CAST(:request_id AS text),
                        CAST(:actor AS text),
                        CAST(:reason AS text)
                    )
                    """
                ),
                {
                    "request_id": request_id,
                    "actor": actor,
                    "reason": reason,
                },
            )
        ).mappings().one()
    except DBAPIError as exc:
        message = str(exc.orig)
        lowered = message.lower()
        if (
            "audit identity collision" in lowered
            or "does not match the reserved lease identity" in lowered
        ):
            raise EmergencyStopIdentityCollisionError(message) from exc
        if "already rejected" in lowered or "already failed" in lowered:
            raise EmergencyStopCommandStateError(message) from exc
        raise

    command = OperatorCommand(
        id=int(row["command_id"]),
        request_id=request_id,
        command=EMERGENCY_STOP_COMMAND,
        payload=EMERGENCY_STOP_PAYLOAD,
        actor=actor,
        reason=reason,
        status=str(row["command_status"]),
        detail=(
            None
            if row["command_detail"] is None
            else str(row["command_detail"])
        ),
        created_at=row["command_created_at"],
        executed_at=row["command_executed_at"],
    )
    return EmergencyStopResult(
        command=command,
        created=bool(row["result_created"]),
        idempotent=bool(row["result_idempotent"]),
        expired_signals=int(row["expired_signals"]),
        cancel_requested_live_orders=int(
            row["cancel_requested_live_orders"]
        ),
        rejected_pending_resume=int(row["rejected_pending_resume"]),
    )
