from __future__ import annotations

from typing import Any

# The only operations the console may request. Anything else is rejected at
# the API boundary and never reaches the queue.
SUPPORTED_COMMANDS = frozenset({"stop", "resume", "verify_market", "run_job", "set_mode"})

EXECUTION_MODES = frozenset({"paper"})
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
    if command == "stop":
        unexpected = _unexpected_keys(payload, {"venue"})
        if unexpected:
            return f"{command} received unsupported payload fields: {', '.join(sorted(unexpected))}"
        venue = command_venue(command, payload)
        if venue not in CONTROL_VENUES:
            return f"{command} requires venue to be polymarket, binance_spot, or all"
    elif command == "resume":
        venue = command_venue(command, payload)
        if venue == "all":
            return "global resume is disabled; venues require independent safety policy"
        if venue == "binance_spot":
            return (
                "Binance resume is disabled until a separate safety protocol "
                "is approved; keep mode=backtest and kill_switch=true"
            )
        elif venue == "polymarket":
            if payload != {"venue": "polymarket"}:
                return "Polymarket PAPER resume requires exactly venue=polymarket"
        else:
            return "resume requires venue to be polymarket or binance_spot"
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
        if venue == "binance_spot" and payload.get("mode") != "backtest":
            return (
                "Binance mode is frozen at backtest until a separate safety "
                "protocol is approved; keep enabled=true and kill_switch=true"
            )
        if venue == "polymarket" and payload.get("mode") == "live":
            return "Live mode is disabled for this Polymarket PAPER-only release"
        modes = EXECUTION_MODES if venue == "polymarket" else BINANCE_EXECUTION_MODES
        if payload.get("mode") not in modes:
            allowed = ", ".join(sorted(modes))
            return f"set_mode for {venue} requires mode to be one of: {allowed}"
    return None
