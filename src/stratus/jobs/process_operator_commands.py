from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import OperatorCommand
from stratus.db.session import session_factory_scope
from stratus.jobs.backfill_fees import backfill_fees
from stratus.jobs.discover_markets import discover_markets
from stratus.jobs.prune_signals import prune_signals
from stratus.repositories.markets import verify_market_station
from stratus.repositories.operator_commands import claim_pending_commands, mark_command
from stratus.repositories.runtime_control import set_kill_switch
from stratus.services.operator_commands import validate_command

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
    # run_job: the job manages its own transactions on the shared factory.
    job = _MAINTENANCE_JOBS[str(command.payload["job"])]
    result = await job(settings, session_factory)
    return "succeeded", f"{command.payload['job']} completed: {result}."


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
