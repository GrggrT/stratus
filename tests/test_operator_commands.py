from httpx import ASGITransport, AsyncClient

from stratus.api import create_app
from stratus.config import Settings
from stratus.services.operator_commands import validate_command


def test_validate_command_enforces_the_allowlist() -> None:
    assert validate_command("stop", {}) is None
    assert validate_command("resume", {}) is None
    assert validate_command("stop", {"venue": "all"}) is None
    assert validate_command("stop", {"venue": "binance_spot"}) is None
    assert validate_command("resume", {"venue": "polymarket"}) is None
    assert validate_command("verify_market", {"market_id": 7}) is None
    assert validate_command("run_job", {"job": "prune-signals"}) is None

    assert validate_command("set_mode", {"mode": "paper"}) is None
    assert validate_command("set_mode", {"mode": "live"}) is None
    assert validate_command("set_mode", {"venue": "binance_spot", "mode": "shadow"}) is None
    assert validate_command("set_mode", {"venue": "binance_spot", "mode": "testnet"}) is None

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
