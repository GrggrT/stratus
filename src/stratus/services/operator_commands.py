from __future__ import annotations

from typing import Any

# The only operations the console may request. Anything else is rejected at
# the API boundary and never reaches the queue.
SUPPORTED_COMMANDS = frozenset({"stop", "resume", "verify_market", "run_job", "set_mode"})

EXECUTION_MODES = frozenset({"paper", "live"})

# Maintenance jobs the worker will run on request. Names map to callables in
# jobs/process_operator_commands.py; keeping only names here avoids importing
# job modules from the API path.
MAINTENANCE_JOBS = frozenset({"discover-markets", "backfill-fees", "prune-signals"})


def validate_command(command: str, payload: dict[str, Any]) -> str | None:
    """Return a human-readable rejection reason, or None when valid."""
    if command not in SUPPORTED_COMMANDS:
        return f"unsupported command: {command}"
    if command == "verify_market":
        market_id = payload.get("market_id")
        if not isinstance(market_id, int) or market_id < 1:
            return "verify_market requires a positive integer market_id"
    elif command == "run_job":
        job = payload.get("job")
        if job not in MAINTENANCE_JOBS:
            allowed = ", ".join(sorted(MAINTENANCE_JOBS))
            return f"run_job requires job to be one of: {allowed}"
    elif command == "set_mode":
        if payload.get("mode") not in EXECUTION_MODES:
            return "set_mode requires mode to be paper or live"
    elif payload:
        return f"{command} does not accept a payload"
    return None
