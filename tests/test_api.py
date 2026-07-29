from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient

from stratus.api import create_app, evaluate_readiness
from stratus.config import Settings


async def test_healthz_is_process_liveness_only() -> None:
    app = create_app(Settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_accepts_fresh_noncritical_audit() -> None:
    now = datetime(2026, 7, 6, 20, tzinfo=UTC)
    for status in ("healthy", "info", "warning"):
        state = evaluate_readiness(
            audit_status=status,
            audit_time=now - timedelta(minutes=5),
            kill_switch=False,
            halted_reason=None,
            now=now,
        )

        assert state.ready is True
        assert state.reasons == ()


def test_readiness_rejects_stale_audit_and_runtime_blocks() -> None:
    now = datetime(2026, 7, 6, 20, tzinfo=UTC)

    state = evaluate_readiness(
        audit_status="critical",
        audit_time=now - timedelta(minutes=21),
        kill_switch=True,
        halted_reason="validation failed",
        now=now,
    )

    assert state.ready is False
    assert state.reasons == (
        "health_audit_stale",
        "health_audit_critical",
        "kill_switch",
        "project_halted",
    )


def test_readiness_rejects_new_blocking_audit() -> None:
    now = datetime(2026, 7, 6, 20, tzinfo=UTC)

    state = evaluate_readiness(
        audit_status="blocking",
        audit_time=now - timedelta(minutes=5),
        kill_switch=False,
        halted_reason=None,
        now=now,
    )

    assert state.ready is False
    assert state.reasons == ("health_audit_blocking",)
