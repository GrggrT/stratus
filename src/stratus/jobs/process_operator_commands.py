from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import ExecutionMode, OperatorCommand
from stratus.db.session import session_factory_scope
from stratus.jobs.backfill_fees import backfill_fees
from stratus.jobs.discover_markets import discover_markets
from stratus.jobs.prune_signals import prune_signals
from stratus.repositories.markets import verify_market_station
from stratus.repositories.operator_commands import claim_pending_commands, mark_command
from stratus.repositories.runtime_control import (
    get_runtime_control,
    request_live_confirmation,
    set_kill_switch,
    set_mode,
)
from stratus.services.live_execution import live_executor_configured
from stratus.services.operator_commands import validate_command
from stratus.services.telegram_reports import calculate_live_gate

logger = logging.getLogger(__name__)

MaintenanceJob = Callable[
    [Settings, async_sessionmaker[AsyncSession]], Awaitable[object]
]
_MAINTENANCE_JOBS: dict[str, MaintenanceJob] = {
    "discover-markets": discover_markets,
    "backfill-fees": backfill_fees,
    "prune-signals": prune_signals,
}


async def _execute(
    session: AsyncSession,
    command: OperatorCommand,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str]:
    """Run one allowlisted command; returns (status, detail)."""
    # Defence in depth: the API validates too, but rows could be inserted by
    # other paths and the allowlist may change between enqueue and execution.
    error = validate_command(command.command, command.payload)
    if error is not None:
        return "rejected", error

    if command.command == "stop":
        expired = await set_kill_switch(session, True)
        return "succeeded", f"Kill switch ON; expired pending signals: {expired}."
    if command.command == "resume":
        try:
            await set_kill_switch(session, False)
        except RuntimeError as exc:
            return "rejected", str(exc)
        return "succeeded", "Kill switch off."
    if command.command == "verify_market":
        unblocked, message = await verify_market_station(
            session, int(command.payload["market_id"])
        )
        return ("succeeded" if unblocked else "rejected"), message
    if command.command == "set_mode":
        return await _execute_set_mode(
            session, str(command.payload["mode"]), settings
        )
    # run_job: the job manages its own transactions on the shared factory.
    job = _MAINTENANCE_JOBS[str(command.payload["job"])]
    result = await job(settings, session_factory)
    return "succeeded", f"{command.payload['job']} completed: {result}."


async def _execute_set_mode(
    session: AsyncSession, mode: str, settings: Settings
) -> tuple[str, str]:
    """Switch the execution mode with the same gates as Telegram /mode.

    Live is only reachable when the operator's legal/tax flags are set, a
    trading wallet is configured, the quantitative paper gate has passed, and
    the request is repeated within the 5-minute confirmation window.
    """
    if mode == "paper":
        await set_mode(session, ExecutionMode.PAPER)
        return "succeeded", "Mode set to paper."
    if not settings.live_legal_approved:
        return "rejected", "Live mode blocked: legal classification is not approved."
    if not settings.live_tax_approved:
        return "rejected", "Live mode blocked: tax treatment is not approved."
    if not live_executor_configured(settings.polygon_private_key):
        return "rejected", "Live mode blocked: no trading wallet is configured."
    gate = await calculate_live_gate(session, settings.initial_bankroll_usd)
    if not gate.passed:
        failing = "; ".join(
            f"{check.name} ({check.detail})"
            for check in gate.checks
            if not check.passed
        )
        return (
            "rejected",
            "Live mode blocked: the validation gate has not passed. "
            f"Failing: {failing or 'no checklist available'}.",
        )
    control = await get_runtime_control(session)
    now = datetime.now(UTC)
    if control.live_confirmation_until is None or control.live_confirmation_until < now:
        deadline = await request_live_confirmation(session)
        return (
            "succeeded",
            "Gate passed. Repeat set_mode live before "
            f"{deadline:%H:%M:%S} UTC to confirm.",
        )
    await set_mode(session, ExecutionMode.LIVE)
    return "succeeded", "LIVE mode enabled. Use stop to halt immediately."


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
                    status, detail = await _execute(
                        session, command, settings, sessions
                    )
                    mark_command(
                        command,
                        status=status,
                        detail=detail[:2000],
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
            except Exception as exc:
                if claimed_id is None:
                    raise
                logger.exception("operator command crashed: id=%d", claimed_id)
                async with sessions.begin() as session:
                    crashed = await session.get(
                        OperatorCommand, claimed_id, with_for_update=True
                    )
                    if crashed is not None and crashed.status == "pending":
                        mark_command(
                            crashed,
                            status="failed",
                            detail=f"{type(exc).__name__}: {exc}"[:2000],
                            executed_at=datetime.now(UTC),
                        )
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
