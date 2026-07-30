import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

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


async def test_operator_reads_are_globally_bounded_below_pool_capacity() -> None:
    app = create_app(
        Settings(
            _env_file=None,
            stratus_database_pool_size=4,
            stratus_database_max_overflow=0,
        )
    )
    current = 0
    maximum = 0
    lock = asyncio.Lock()

    @app.get("/api/v1/test-read-capacity")
    async def test_read_capacity() -> dict[str, bool]:
        nonlocal current, maximum
        async with lock:
            current += 1
            maximum = max(maximum, current)
        await asyncio.sleep(0.05)
        async with lock:
            current -= 1
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = await asyncio.gather(
            *(client.get("/api/v1/test-read-capacity") for _ in range(12))
        )

    assert all(response.status_code == 200 for response in responses)
    assert maximum == 3


def test_operator_api_requires_reserved_write_capacity() -> None:
    settings = Settings(
        _env_file=None,
        stratus_database_pool_size=1,
        stratus_database_max_overflow=0,
    )

    with pytest.raises(ValueError, match="at least two total connections"):
        create_app(settings)


async def test_database_pool_timeout_is_a_retryable_service_unavailable() -> None:
    app = create_app(Settings(_env_file=None))

    @app.get("/api/v1/test-pool-timeout")
    async def test_pool_timeout() -> None:
        raise SQLAlchemyTimeoutError("synthetic pool exhaustion")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/test-pool-timeout")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "1"
    assert response.json() == {
        "detail": "database connection capacity is temporarily unavailable",
        "retryable": True,
    }


async def test_readiness_never_falls_back_to_writable_sessions() -> None:
    app = create_app(Settings(_env_file=None))
    writable_factory_called = False

    def writable_sessions() -> None:
        nonlocal writable_factory_called
        writable_factory_called = True
        raise AssertionError("readiness must not use the writable session factory")

    app.state.sessions = writable_sessions
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "reasons": ["database_unavailable"],
    }
    assert writable_factory_called is False


async def test_runtime_http_counters_detect_foreign_operator_requests() -> None:
    app = create_app(Settings(_env_file=None))
    run_id = "a" * 32
    headers = {"X-Stratus-Load-Gate-Run": run_id}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        baseline = await client.get(
            "/api/v1/runtime-http-counters",
            headers=headers,
        )
        foreign = await client.get("/api/v1/not-a-real-endpoint")
        observed = await client.get(
            "/api/v1/runtime-http-counters",
            headers=headers,
        )

    assert baseline.status_code == 200
    assert baseline.json()["run_started"] == 1
    assert baseline.json()["run_completed"] == 0
    assert foreign.status_code == 404
    assert observed.status_code == 200
    assert observed.json() == {
        "run_id": run_id,
        "run_started": 2,
        "run_completed": 1,
        "run_aborted": 0,
        "run_5xx": 0,
        "total_started": 3,
        "total_completed": 2,
        "total_aborted": 0,
        "total_5xx": 0,
        "active_requests": 1,
        "untagged_api_started": 1,
        "untagged_api_completed": 1,
        "untagged_api_5xx": 0,
    }


async def test_runtime_http_counters_require_valid_run_id() -> None:
    app = create_app(Settings(_env_file=None))

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/v1/runtime-http-counters")

    assert response.status_code == 422


async def test_unhandled_operator_request_is_recorded_as_aborted() -> None:
    app = create_app(Settings(_env_file=None))
    run_id = "a" * 32

    @app.get("/api/v1/test-aborted-request")
    async def aborted_request() -> None:
        raise RuntimeError("synthetic request abort")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://test",
    ) as client:
        with pytest.raises(RuntimeError, match="synthetic request abort"):
            await client.get(
                "/api/v1/test-aborted-request",
                headers={"X-Stratus-Load-Gate-Run": run_id},
            )

    snapshot = app.state.http_telemetry.snapshot(run_id)
    assert snapshot.run_started == 1
    assert snapshot.run_completed == 1
    assert snapshot.run_aborted == 1
    assert snapshot.active_requests == 0


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
