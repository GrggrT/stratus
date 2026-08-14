from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import OperatorCommand
from stratus.jobs.process_operator_commands import (
    _reconcile_command_terminal_state,
)
from stratus.services.operator_commands import validate_command


def test_validate_command_enforces_the_allowlist() -> None:
    assert validate_command("stop", {}) is None
    assert validate_command("stop", {"venue": "all"}) is None
    assert validate_command("stop", {"venue": "binance_spot"}) is None
    assert validate_command("resume", {"venue": "polymarket"}) is None
    binance_resume_rejection = validate_command(
        "resume",
        {"venue": "binance_spot"},
    )
    assert binance_resume_rejection is not None
    assert "keep mode=backtest and kill_switch=true" in binance_resume_rejection
    assert validate_command("verify_market", {"market_id": 7}) is None
    assert validate_command("run_job", {"job": "prune-signals"}) is None

    assert validate_command("set_mode", {"mode": "paper"}) is None
    polymarket_live_rejection = validate_command("set_mode", {"mode": "live"})
    assert polymarket_live_rejection is not None
    assert "PAPER-only" in polymarket_live_rejection
    assert (
        validate_command(
            "set_mode",
            {"venue": "binance_spot", "mode": "backtest"},
        )
        is None
    )
    for forbidden_mode in ("disabled", "shadow", "testnet", "live"):
        rejection = validate_command(
            "set_mode",
            {"venue": "binance_spot", "mode": forbidden_mode},
        )
        assert rejection is not None
        assert "keep enabled=true and kill_switch=true" in rejection

    assert validate_command("drop_tables", {}) is not None
    assert validate_command("verify_market", {}) is not None
    assert validate_command("verify_market", {"market_id": "7"}) is not None
    assert validate_command("verify_market", {"market_id": 0}) is not None
    assert validate_command("run_job", {"job": "rm -rf"}) is not None
    assert validate_command("set_mode", {}) is not None
    assert validate_command("set_mode", {"mode": "yolo"}) is not None
    assert validate_command("set_mode", {"venue": "all", "mode": "live"}) is not None
    assert validate_command("set_mode", {"venue": "binance_spot", "mode": "paper"}) is not None
    assert validate_command("resume", {"venue": "unknown"}) is not None
    assert validate_command("resume", {}) is not None
    assert validate_command("resume", {"venue": "all"}) is not None
    assert validate_command("resume", {"venue": "polymarket", "lease_id": "secret"}) is not None
    assert validate_command("stop", {"extra": True}) is not None


async def test_mutations_are_refused_without_a_configured_key() -> None:
    app = create_app(Settings(_env_file=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/commands",
            json={
                "request_id": "req-12345678",
                "command": "stop",
                "reason": "unit test",
            },
        )

    assert response.status_code == 403


async def test_unknown_commands_are_rejected_before_the_queue() -> None:
    app = create_app(Settings(operator_api_key="secret-key"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 422 fires in the handler's validation, before any database access.
        response = await client.post(
            "/api/v1/commands",
            headers={"X-API-Key": "secret-key"},
            json={
                "request_id": "req-12345678",
                "command": "drop_tables",
                "reason": "unit test",
            },
        )

    assert response.status_code == 422


async def test_polymarket_live_mode_is_rejected_before_the_queue() -> None:
    app = create_app(Settings(operator_api_key="secret-key"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/commands",
            headers={"X-API-Key": "secret-key"},
            json={
                "request_id": "req-live-disabled",
                "command": "set_mode",
                "payload": {"venue": "polymarket", "mode": "live"},
                "reason": "prove PAPER-only submission boundary",
            },
        )

    assert response.status_code == 422
    assert "PAPER-only" in response.json()["detail"]


def _command(
    *,
    status: str,
    detail: str | None = None,
    command: str = "stop",
) -> OperatorCommand:
    return OperatorCommand(
        request_id="req-authoritative-terminal-1234",
        command=command,
        payload=(
            {"venue": "polymarket"}
            if command == "stop"
            else {"job": "prune-signals"}
        ),
        actor="unit-test",
        reason="prove terminal audit reconciliation",
        status=status,
        detail=detail,
        executed_at=(
            datetime(2026, 7, 30, tzinfo=UTC)
            if status != "pending"
            else None
        ),
    )


def test_pending_command_is_terminalized_by_runtime_result() -> None:
    command = _command(status="pending", command="run_job")
    executed_at = datetime(2026, 7, 30, 1, tzinfo=UTC)

    result = _reconcile_command_terminal_state(
        command,
        requested_status="succeeded",
        requested_detail="runtime result",
        executed_at=executed_at,
    )

    assert result == ("succeeded", "runtime result")
    assert command.status == "succeeded"
    assert command.detail == "runtime result"
    assert command.executed_at == executed_at


def test_pending_stop_success_is_delegated_to_database_effect_guard() -> None:
    command = _command(status="pending")
    executed_at = datetime(2026, 7, 30, 1, tzinfo=UTC)

    result = _reconcile_command_terminal_state(
        command,
        requested_status="succeeded",
        requested_detail="exact controls engaged",
        executed_at=executed_at,
    )

    assert result == ("succeeded", "exact controls engaged")
    assert command.status == "succeeded"
    assert command.detail == "exact controls engaged"
    assert command.executed_at == executed_at


@pytest.mark.parametrize("requested_status", ["rejected", "failed"])
def test_pending_stop_cannot_be_terminalized_as_non_success(
    requested_status: str,
) -> None:
    command = _command(status="pending")

    with pytest.raises(RuntimeError, match="may only succeed"):
        _reconcile_command_terminal_state(
            command,
            requested_status=requested_status,
            requested_detail="runtime result",
            executed_at=datetime(2026, 7, 30, 1, tzinfo=UTC),
        )

    assert command.status == "pending"
    assert command.detail is None
    assert command.executed_at is None


def test_database_terminal_state_is_preserved_and_mismatch_fails() -> None:
    command = _command(
        status="succeeded",
        detail="database authoritative result",
    )
    original_executed_at = command.executed_at

    result = _reconcile_command_terminal_state(
        command,
        requested_status="succeeded",
        requested_detail="runtime would have overwritten this",
        executed_at=datetime(2026, 7, 30, 1, tzinfo=UTC),
    )

    assert result == ("succeeded", "database authoritative result")
    assert command.executed_at == original_executed_at

    with pytest.raises(
        RuntimeError,
        match="terminal state disagrees",
    ):
        _reconcile_command_terminal_state(
            command,
            requested_status="failed",
            requested_detail="contradictory runtime result",
            executed_at=datetime(2026, 7, 30, 2, tzinfo=UTC),
        )
