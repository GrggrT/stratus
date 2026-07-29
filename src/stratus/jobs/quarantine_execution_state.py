from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    ExecutionOrderIntent,
    Order,
    RuntimeControl,
    Signal,
    SignalStatus,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import (
    LIVE_STATUS_CANCEL_REQUESTED,
    LIVE_STATUS_OPEN,
    LIVE_STATUS_PARTIALLY_FILLED,
    LIVE_STATUS_SUBMITTING,
    LIVE_STATUS_UNCONFIRMED,
    PAPER_STATUS_PENDING,
    PAPER_STATUS_RESTING,
    PAPER_STATUS_UNFILLED,
)
from stratus.repositories.execution_cost_ledger import record_terminal_no_fill
from stratus.repositories.station_reliability import acquire_station_reliability_gate_lock

logger = logging.getLogger(__name__)

_ACTIVE_PAPER_STATUSES = frozenset(
    {
        "pending",
        "open",
        PAPER_STATUS_PENDING,
        PAPER_STATUS_RESTING,
    }
)
_NONTERMINAL_LIVE_STATUSES = frozenset(
    {
        "pending",
        LIVE_STATUS_SUBMITTING,
        LIVE_STATUS_OPEN,
        LIVE_STATUS_PARTIALLY_FILLED,
        LIVE_STATUS_CANCEL_REQUESTED,
        LIVE_STATUS_UNCONFIRMED,
    }
)
_QUARANTINE_REASON = "kill_switch_execution_quarantine"


class ExecutionStateQuarantineError(RuntimeError):
    """The execution state cannot be made terminal without venue reconciliation."""


@dataclass(frozen=True, slots=True)
class ExecutionStateQuarantineResult:
    paper_orders_terminalized: int
    paper_intents_sealed: int
    legacy_paper_orders_terminalized: int


async def _quarantine_locked(session: AsyncSession) -> ExecutionStateQuarantineResult:
    control = await session.get(RuntimeControl, 1, with_for_update=True)
    if control is None:
        raise ExecutionStateQuarantineError("runtime_control row is missing")
    if not control.kill_switch:
        raise ExecutionStateQuarantineError(
            "execution-state quarantine requires the kill switch to be enabled"
        )

    live_orders = list(
        (
            await session.scalars(
                select(Order)
                .where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status.in_(_NONTERMINAL_LIVE_STATUSES),
                )
                .order_by(Order.id)
                .with_for_update(of=Order)
            )
        ).all()
    )
    if live_orders:
        summary = ",".join(f"{order.id}:{order.status}" for order in live_orders[:10])
        raise ExecutionStateQuarantineError(
            "nonterminal LIVE orders require venue reconciliation before quarantine: "
            f"count={len(live_orders)} sample={summary}"
        )

    rows = (
        await session.execute(
            select(Order, Signal, ExecutionOrderIntent)
            .join(Signal, Signal.id == Order.signal_id)
            .outerjoin(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
            .where(
                Order.mode == ExecutionMode.PAPER,
                Order.status.in_(_ACTIVE_PAPER_STATUSES),
            )
            .order_by(Order.id)
            .with_for_update(of=Order)
        )
    ).all()

    # Validate every row before mutating any of them.  A partial/ambiguous fill
    # must never be rewritten as a zero-fill terminal outcome.
    for order, _signal, intent in rows:
        if order.filled_at is not None or (order.filled_usd or Decimal(0)) != 0:
            raise ExecutionStateQuarantineError(
                f"paper order {order.id} has fill evidence and requires manual reconciliation"
            )
        if intent is not None and intent.mode is not ExecutionMode.PAPER:
            raise ExecutionStateQuarantineError(
                f"paper order {order.id} is linked to a non-paper execution intent"
            )

    sealed = 0
    legacy = 0
    for order, signal, intent in rows:
        terminal_status = (
            PAPER_STATUS_UNFILLED
            if order.status == PAPER_STATUS_RESTING
            else "expired"
        )
        if intent is not None:
            await record_terminal_no_fill(
                session,
                intent.id,
                terminal_status=terminal_status,
                failure_reason=_QUARANTINE_REASON,
            )
            sealed += 1
            continue

        order.status = terminal_status
        order.filled_usd = Decimal(0)
        order.fee_usd = None
        order.filled_at = None
        if signal.status is SignalStatus.PENDING:
            signal.status = SignalStatus.EXPIRED
            signal.skip_reason = _QUARANTINE_REASON
        legacy += 1

    await session.flush()
    return ExecutionStateQuarantineResult(
        paper_orders_terminalized=len(rows),
        paper_intents_sealed=sealed,
        legacy_paper_orders_terminalized=legacy,
    )


async def quarantine_execution_state(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> ExecutionStateQuarantineResult:
    """Terminalize stranded PAPER orders while trading is authoritatively stopped.

    LIVE state is deliberately not inferred.  Any nonterminal LIVE order makes
    the whole transaction fail so an operator must reconcile it with the venue.
    """

    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            await acquire_station_reliability_gate_lock(session)
            result = await _quarantine_locked(session)
    logger.info(
        "execution-state quarantine complete: paper=%d sealed=%d legacy=%d",
        result.paper_orders_terminalized,
        result.paper_intents_sealed,
        result.legacy_paper_orders_terminalized,
    )
    return result


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(quarantine_execution_state(settings))


if __name__ == "__main__":
    main()
