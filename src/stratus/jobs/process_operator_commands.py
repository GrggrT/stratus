from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    OperatorCommand,
    TradingVenue,
    VenueMode,
)
from stratus.db.session import session_factory_scope
from stratus.jobs.backfill_fees import backfill_fees
from stratus.jobs.discover_markets import discover_markets
from stratus.jobs.prune_signals import prune_signals
from stratus.repositories.markets import verify_market_station
from stratus.repositories.operator_commands import claim_pending_commands, mark_command
from stratus.repositories.paper_canary_leases import (
    PaperCanaryLeaseError,
    resume_paper_canary_exact,
)
from stratus.repositories.runtime_control import (
    set_kill_switch,
    set_mode,
)
from stratus.repositories.safety_lock import (
    PaperCanarySafetyLockBusy,
    try_lock_paper_canary_safety_domain,
)
from stratus.repositories.venue_runtime_control import (
    seal_binance_backtest,
    set_venue_enabled,
    set_venue_kill_switch,
    set_venue_mode,
)
from stratus.services.operator_commands import command_venue, validate_command

logger = logging.getLogger(__name__)

MaintenanceJob = Callable[[Settings, async_sessionmaker[AsyncSession]], Awaitable[object]]
_MAINTENANCE_JOBS: dict[str, MaintenanceJob] = {
    "discover-markets": discover_markets,
    "backfill-fees": backfill_fees,
    "prune-signals": prune_signals,
}


def _reconcile_command_terminal_state(
    command: OperatorCommand,
    *,
    requested_status: str,
    requested_detail: str,
    executed_at: datetime,
) -> tuple[str, str]:
    """Preserve database terminal state and constrain runtime STOP results."""
    if command.status == "pending":
        if command.command == "stop" and requested_status != "succeeded":
            raise RuntimeError(
                "pending STOP may only succeed after its exact database control effect"
            )
        mark_command(
            command,
            status=requested_status,
            detail=requested_detail,
            executed_at=executed_at,
        )
        return requested_status, requested_detail
    if (
        command.status != requested_status
        or command.status not in {"succeeded", "rejected", "failed"}
        or command.detail is None
        or not command.detail.strip()
        or command.executed_at is None
    ):
        raise RuntimeError(
            "operator command terminal state disagrees with execution result"
        )
    return command.status, command.detail


async def _stop_venues(
    session: AsyncSession,
    command: OperatorCommand,
) -> tuple[str, str]:
    venue = command_venue(command.command, command.payload)
    expired = 0
    if venue in {"polymarket", "all"}:
        # The worker already owns the pending command row at this point.  A
        # safety-control STOP takes the safety-domain advisory lock before it
        # locks that same audit row, so waiting for the advisory lock here
        # would invert the lock order and permit a database deadlock.  Defer
        # the queued command instead; its transaction rolls back the row claim
        # and lets the independent safety transaction complete immediately.
        if not await try_lock_paper_canary_safety_domain(session):
            raise PaperCanarySafetyLockBusy(
                "Polymarket PAPER safety transition is busy"
            )
    if venue in {"binance_spot", "all"}:
        await seal_binance_backtest(session)
        # The STOP audit trigger accepts success only after the payload's
        # control effect exists in this exact transaction. Flush Binance before
        # a Binance-only audit update or the owner primitive terminalizes all.
        await session.flush()
    if venue in {"polymarket", "all"}:
        expired = await set_kill_switch(
            session,
            True,
            request_id=command.request_id,
            actor=command.actor,
            reason=command.reason,
        )
        await set_venue_kill_switch(session, TradingVenue.POLYMARKET, True)
    return (
        "succeeded",
        f"Kill switch ON for {venue}; expired Polymarket signals: {expired}.",
    )


async def _resume_venues(
    session: AsyncSession,
    command: OperatorCommand,
) -> tuple[str, str]:
    venue = command_venue(command.command, command.payload)
    if venue in {"binance_spot", "all"}:
        return (
            "rejected",
            "Binance resume is disabled; mode=backtest and kill_switch=true "
            "must remain unchanged.",
        )
    if venue != "polymarket":
        return "rejected", "resume requires venue=polymarket"
    if not await try_lock_paper_canary_safety_domain(session):
        raise PaperCanarySafetyLockBusy(
            "Polymarket PAPER safety transition is busy"
        )
    try:
        await resume_paper_canary_exact(session, command_id=command.id)
    except PaperCanaryLeaseError as exc:
        return "rejected", str(exc)
    return (
        "succeeded",
        "Polymarket PAPER kill switches disabled by exact database lease authority.",
    )


async def _execute_binance_set_mode(
    session: AsyncSession,
    mode: str,
) -> tuple[str, str]:
    target = VenueMode(mode)
    if target is not VenueMode.BACKTEST:
        return (
            "rejected",
            "Binance Spot is sealed at enabled+backtest+kill_switch=true for "
            "this release.",
        )
    await seal_binance_backtest(session)
    return (
        "succeeded",
        "Binance Spot remains enabled in backtest with kill switch ON.",
    )


async def _execute(
    session: AsyncSession,
    command: OperatorCommand,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    """Run one allowlisted command; returns (status, detail)."""
    # Rows queued before the API policy tightened, or inserted through an
    # owner repair path, must still be unable to relax Binance controls.
    if (
        command.command == "resume"
        and command_venue(command.command, command.payload)
        in {"binance_spot", "all"}
    ):
        return (
            "rejected",
            "Binance resume is disabled; mode=backtest and kill_switch=true "
            "must remain unchanged.",
        )
    # Defence in depth: the API validates too, but rows could be inserted by
    # other paths and the allowlist may change between enqueue and execution.
    error = validate_command(command.command, command.payload)
    if error is not None:
        return "rejected", error

    if command.command == "stop":
        return await _stop_venues(session, command)
    if command.command == "resume":
        return await _resume_venues(session, command)
    if command.command == "verify_market":
        unblocked, message = await verify_market_station(session, int(command.payload["market_id"]))
        return ("succeeded" if unblocked else "rejected"), message
    if command.command == "set_mode":
        return await _execute_set_mode(
            session,
            command_venue(command.command, command.payload),
            str(command.payload["mode"]),
        )
    # run_job: the job manages its own transactions on the shared factory.
    job = _MAINTENANCE_JOBS[str(command.payload["job"])]
    result = await job(settings, session_factory)
    return "succeeded", f"{command.payload['job']} completed: {result}."


async def _execute_set_mode(
    session: AsyncSession,
    venue: str,
    mode: str,
) -> tuple[str, str]:
    """Normalize the release to its only supported execution modes."""
    if venue == "binance_spot":
        return await _execute_binance_set_mode(session, mode)
    if mode == "live":
        return (
            "rejected",
            "Live mode is disabled for this PAPER-only release.",
        )
    if mode != "paper":
        return "rejected", f"unsupported Polymarket mode: {mode}"

    if not await try_lock_paper_canary_safety_domain(session):
        raise PaperCanarySafetyLockBusy(
            "Polymarket PAPER safety transition is busy"
        )

    # PAPER is a fail-closed normalization, not a resume: it never clears
    # either kill switch or a persisted halt. It must remain available when
    # the synthetic lease guard reports "halted" because no canary is active.
    await set_mode(session, ExecutionMode.PAPER)
    await set_venue_enabled(session, TradingVenue.POLYMARKET, True)
    await set_venue_mode(session, TradingVenue.POLYMARKET, VenueMode.PAPER)
    return "succeeded", "Mode set to paper."


async def process_operator_commands(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    succeeded = 0
    finished = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        # One command per transaction: a crash marks that command failed in a
        # fresh transaction instead of rolling back the batch and retrying the
        # same poison command forever.
        while True:
            claimed_id: int | None = None
            try:
                async with sessions.begin() as session:
                    commands = await claim_pending_commands(session, limit=1)
                    if not commands:
                        break
                    command = commands[0]
                    claimed_id = command.id
                    status, detail = await _execute(session, command, settings, sessions)
                    # Exact stop/resume primitives may terminalize this row in
                    # the same transaction as the control change. Reload
                    # before writing so runtime never rewrites durable audit
                    # evidence that the database already committed logically.
                    await session.refresh(
                        command,
                        attribute_names=("status", "detail", "executed_at"),
                    )
                    status, detail = _reconcile_command_terminal_state(
                        command,
                        requested_status=status,
                        requested_detail=detail[:2000],
                        executed_at=datetime.now(UTC),
                    )
                    logger.info(
                        "operator command %s: id=%d command=%s actor=%s detail=%s",
                        status,
                        command.id,
                        command.command,
                        command.actor,
                        detail,
                    )
            except PaperCanarySafetyLockBusy:
                # Roll back immediately so the emergency/release transaction
                # holding the safety lock can claim and reject the exact
                # pending resume. A later scheduler pass retries if it remains.
                logger.info(
                    "operator safety command deferred: id=%s",
                    claimed_id,
                )
                break
            except Exception as exc:
                if claimed_id is None:
                    raise
                logger.exception("operator command crashed: id=%d", claimed_id)
                stop_left_pending = False
                async with sessions.begin() as session:
                    crashed = await session.get(OperatorCommand, claimed_id, with_for_update=True)
                    if crashed is not None and crashed.status == "pending":
                        if crashed.command == "stop":
                            # A STOP failure cannot be reclassified by the
                            # runtime login. Leave it pending for exact
                            # owner-primitive replay and end this scheduler
                            # pass so the same row cannot spin indefinitely.
                            stop_left_pending = True
                        else:
                            mark_command(
                                crashed,
                                status="failed",
                                detail=f"{type(exc).__name__}: {exc}"[:2000],
                                executed_at=datetime.now(UTC),
                            )
                if stop_left_pending:
                    logger.error(
                        "operator STOP left pending for database primitive retry: id=%d",
                        claimed_id,
                    )
                    break
                finished += 1
                continue
            finished += 1
            succeeded += status == "succeeded"
    return succeeded, finished


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(process_operator_commands(settings))


if __name__ == "__main__":
    main()
