from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from stratus.api import create_app
from stratus.config import Settings
from stratus.operator_api import router as operator_router


def test_operator_monitoring_routes_are_registered() -> None:
    routes = {
        route.path: route
        for route in operator_router.routes
        if isinstance(route, APIRoute)
    }

    expected = {
        "/api/v1/overview",
        "/api/v1/markets",
        "/api/v1/signals",
        "/api/v1/positions",
        "/api/v1/calibration",
        "/api/v1/orders",
        "/api/v1/live-summary",
        "/api/v1/jobs",
    }
    assert expected <= routes.keys()
    assert all(routes[path].methods == {"GET"} for path in expected)


async def test_operator_collection_limit_is_bounded() -> None:
    app = create_app(Settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/markets?limit=0")

    assert response.status_code == 422


async def test_operator_api_key_is_enforced_when_configured() -> None:
    app = create_app(Settings(operator_api_key="secret-key"))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        missing = await client.get("/api/v1/jobs")
        wrong = await client.get("/api/v1/jobs", headers={"X-API-Key": "nope"})
        health = await client.get("/healthz")

    # 401 fires in the router dependency, before any database access.
    assert missing.status_code == 401
    assert wrong.status_code == 401
    # Health probes stay open for the docker healthcheck.
    assert health.status_code == 200


async def test_operator_api_stays_open_when_no_key_configured() -> None:
    app = create_app(Settings())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 422 (not 401) proves the request reached parameter validation.
        response = await client.get("/api/v1/jobs?limit=0")

    assert response.status_code == 422
