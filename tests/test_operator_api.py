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
