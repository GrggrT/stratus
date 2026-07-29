from __future__ import annotations

from typing import Any

# The only operations the console may request. Anything else is rejected at
# the API boundary and never reaches the queue.
SUPPORTED_COMMANDS = frozenset({"stop", "resume", "verify_market", "run_job", "set_mode"})

EXECUTION_MODES = frozenset({"paper", "live"})
BINANCE_EXECUTION_MODES = frozenset({"disabled", "backtest", "shadow", "testnet", "live"})
CONTROL_VENUES = frozenset({"polymarket", "binance_spot", "all"})

# Maintenance jobs the worker will run on request. Names map to callables in
# jobs/process_operator_commands.py; keeping only names here avoids importing
# job modules from the API path.
MAINTENANCE_JOBS = frozenset({"discover-markets", "backfill-fees", "prune-signals"})


def command_venue(command: str, payload: dict[str, Any]) -> str:
    """Resolve the backwards-compatible venue default for a command."""
    default = "all" if command == "stop" else "polymarket"
    return str(payload.get("venue", default))


def _unexpected_keys(payload: dict[str, Any], allowed: set[str]) -> set[str]:
    return set(payload).difference(allowed)


def validate_command(command: str, payload: dict[str, Any]) -> str | None:
    """Return a human-readable rejection reason, or None when valid."""
    if command not in SUPPORTED_COMMANDS:
        return f"unsupported command: {command}"
    if command in {"stop", "resume"}:
        unexpected = _unexpected_keys(payload, {"venue"})
        if unexpected:
            return f"{command} received unsupported payload fields: {', '.join(sorted(unexpected))}"
        venue = command_venue(command, payload)
        if venue not in CONTROL_VENUES:
            return f"{command} requires venue to be polymarket, binance_spot, or all"
    elif command == "verify_market":
        market_id = payload.get("market_id")
        if not isinstance(market_id, int) or market_id < 1:
            return "verify_market requires a positive integer market_id"
    elif command == "run_job":
        job = payload.get("job")
        if job not in MAINTENANCE_JOBS:
            allowed = ", ".join(sorted(MAINTENANCE_JOBS))
            return f"run_job requires job to be one of: {allowed}"
    elif command == "set_mode":
        unexpected = _unexpected_keys(payload, {"venue", "mode"})
        if unexpected:
            return "set_mode received unsupported payload fields: " + ", ".join(sorted(unexpected))
        venue = command_venue(command, payload)
        if venue == "all" or venue not in CONTROL_VENUES:
            return "set_mode requires venue to be polymarket or binance_spot"
        modes = EXECUTION_MODES if venue == "polymarket" else BINANCE_EXECUTION_MODES
        if payload.get("mode") not in modes:
            allowed = ", ".join(sorted(modes))
            return f"set_mode for {venue} requires mode to be one of: {allowed}"
    return None
