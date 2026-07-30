from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pytest import MonkeyPatch
from starlette.requests import Request

from stratus import operator_api
from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import ExecutionMode
from stratus.operator_api import _sessions, _summary_response
from stratus.operator_api import router as operator_router
from stratus.repositories.execution_cost_reporting import (
    ExecutionCostMetrics,
    ExecutionCostOrderPage,
    ExecutionCostOrderSummary,
    ExecutionCostReasonCount,
    ExecutionCostSummary,
)
from stratus.services.live_analytics import (
    PositionStat,
    TradeOutcome,
    summarize_live_performance,
)


def test_operator_monitoring_routes_are_registered() -> None:
    routes = {route.path: route for route in operator_router.routes if isinstance(route, APIRoute)}

    expected = {
        "/api/v1/venues",
        "/api/v1/stations",
        "/api/v1/overview",
        "/api/v1/markets",
        "/api/v1/signals",
        "/api/v1/positions",
        "/api/v1/calibration",
        "/api/v1/orders",
        "/api/v1/live-summary",
        "/api/v1/live-equity",
        "/api/v1/execution-cost-report",
        "/api/v1/execution-cost-report/orders",
        "/api/v1/market-timeseries",
        "/api/v1/probability-timeseries",
        "/api/v1/orderbook",
        "/api/v1/official-resolution-sources",
        "/api/v1/official-resolution-sources/contracts",
        "/api/v1/jobs",
    }
    assert expected <= routes.keys()
    assert all(routes[path].methods == {"GET"} for path in expected)
    mutations = {
        "/api/v1/official-resolution-sources/license-policy-hash",
        "/api/v1/official-resolution-sources/license-attestations",
        "/api/v1/official-resolution-sources/import",
    }
    assert mutations <= routes.keys()
    assert all(routes[path].methods == {"POST"} for path in mutations)


def test_operator_reads_require_an_explicit_read_session_factory() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.sessions = _fake_sessions
    request = Request({"type": "http", "app": app})

    with pytest.raises(RuntimeError, match="read-session factory"):
        _sessions(request)


def test_summary_keeps_history_but_uses_only_dual_benchmark_sample() -> None:
    legacy = PositionStat(
        closed=True,
        pnl_usd=10.0,
        clv=0.5,
        cost_usd=5.0,
        fee_usd=0.0,
        size_shares=10.0,
        avg_price=0.5,
    )
    dual = PositionStat(
        closed=True,
        pnl_usd=-2.0,
        clv=0.02,
        cost_usd=5.0,
        fee_usd=0.1,
        size_shares=10.0,
        avg_price=0.5,
    )
    strict_trade = TradeOutcome(
        model_prob=0.7,
        market_prob=0.6,
        won=True,
        pnl_usd=-2.0,
        cost_usd=5.0,
        predicted_edge=0.1,
    )

    response = _summary_response(
        summarize_live_performance([legacy, dual]),
        summarize_live_performance([dual]),
        [strict_trade],
    )

    assert response.performance_sample_scope == "all-protocol-history"
    assert response.positions == 2
    assert response.total_pnl_usd == 8.0
    assert response.benchmark_protocol == "clob-dual-v1"
    assert response.benchmark_sample_scope == "prospective"
    assert response.benchmark_positions == 1
    assert response.avg_clv == 0.02
    assert response.skill_pnl_usd == 0.1
    assert response.brier_model == 0.09
    assert response.brier_market == 0.16


async def test_operator_collection_limit_is_bounded() -> None:
    app = create_app(Settings(_env_file=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/markets?limit=0")

    assert response.status_code == 422


async def test_direct_settlement_value_endpoint_is_permanently_retired() -> None:
    app = create_app(Settings(operator_api_key="secret-key"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/settlement-actuals",
            headers={"X-API-Key": "secret-key"},
            json={"source_value": "99", "source_response_hash": "a" * 64},
        )

    assert response.status_code == 410
    assert "licensed hash-addressed source artifact" in response.json()["detail"]


async def test_operator_api_key_is_enforced_when_configured() -> None:
    app = create_app(Settings(operator_api_key="secret-key"))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        missing = await client.get("/api/v1/jobs")
        wrong = await client.get("/api/v1/jobs", headers={"X-API-Key": "nope"})
        health = await client.get("/healthz")

    # 401 fires in the router dependency, before any database access.
    assert missing.status_code == 401
    assert wrong.status_code == 401
    # Health probes stay open for the docker healthcheck.
    assert health.status_code == 200


async def test_operator_api_stays_open_when_no_key_configured() -> None:
    app = create_app(Settings(_env_file=None))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 422 (not 401) proves the request reached parameter validation.
        response = await client.get("/api/v1/jobs?limit=0")

    assert response.status_code == 422


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


def _fake_sessions() -> _FakeSessionContext:
    return _FakeSessionContext()


def _execution_metrics() -> ExecutionCostMetrics:
    return ExecutionCostMetrics(
        eligible_orders=1,
        eligible_shares=Decimal("12.5"),
        gross_model_edge=Decimal("0.08"),
        latency_movement=Decimal("0.01"),
        taker_spread=None,
        taker_slippage=None,
        maker_improvement=Decimal("0.005"),
        fee_drag=Decimal("0.001"),
        realized_net_edge=Decimal("0.074"),
        adverse_selection_5m=None,
        adverse_selection_30m=Decimal("0.012"),
    )


async def test_execution_cost_report_preserves_unknowns_and_mode(
    monkeypatch: MonkeyPatch,
) -> None:
    seen: list[ExecutionMode] = []

    async def fake_report(_session: object, mode: ExecutionMode) -> ExecutionCostSummary:
        seen.append(mode)
        return ExecutionCostSummary(
            mode=mode,
            opened_at=datetime(2026, 7, 15, 4, tzinfo=UTC),
            definition_version="execution-cost-v1",
            definition_hash="a" * 64,
            attempts=3,
            filled_attempts=2,
            no_fill_attempts=1,
            pending_attempts=0,
            sealed_attempts=3,
            eligible_seals=1,
            ineligible_seals=2,
            maker_attempts=2,
            maker_fills=1,
            maker_fill_rate=Decimal("0.5"),
            maker_seals=1,
            taker_seals=0,
            mixed_seals=0,
            unknown_role_seals=1,
            expected_slippage_known=0,
            expected_slippage_total=2,
            exact_fee_known=1,
            exact_fee_total=2,
            mark_5m_known=0,
            mark_5m_total=0,
            mark_30m_known=1,
            mark_30m_total=2,
            metrics=_execution_metrics(),
            reasons=(ExecutionCostReasonCount(reason="fee_unknown", orders=2),),
            slices=(),
        )

    monkeypatch.setattr(operator_api, "load_execution_cost_report", fake_report)
    app = create_app(Settings(_env_file=None))
    app.state.sessions = _fake_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/execution-cost-report?mode=live")

    assert response.status_code == 200
    body = response.json()
    assert seen == [ExecutionMode.LIVE]
    assert body["mode"] == "live"
    assert body["state"] == "partial"
    assert body["maker_fill_rate"] == 0.5
    assert body["metrics"]["taker_slippage"] is None
    assert body["metrics"]["adverse_selection_5m"] is None
    assert body["expected_slippage_coverage"] == {
        "known": 0,
        "total": 2,
        "ratio": 0.0,
    }
    assert body["markout_5m_coverage"] == {
        "known": 0,
        "total": 0,
        "ratio": None,
    }
    assert body["reasons"] == [{"reason": "fee_unknown", "orders": 2}]


async def test_execution_cost_report_requires_explicit_mode() -> None:
    app = create_app(Settings(_env_file=None))
    app.state.sessions = _fake_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/execution-cost-report")

    assert response.status_code == 422


async def test_execution_cost_order_drilldown_forwards_explicit_filters(
    monkeypatch: MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    now = datetime(2026, 7, 15, 5, tzinfo=UTC)

    async def fake_orders(_session: object, **kwargs: Any) -> ExecutionCostOrderPage:
        captured.update(kwargs)
        return ExecutionCostOrderPage(
            mode=ExecutionMode.PAPER,
            total=1,
            limit=25,
            offset=5,
            orders=(
                ExecutionCostOrderSummary(
                    order_id=7,
                    intent_id=8,
                    signal_id=9,
                    market_id=10,
                    station_icao="KLGA",
                    market_date=date(2026, 7, 16),
                    kind="high",
                    confidence="A",
                    horizon_h=Decimal("12"),
                    mode=ExecutionMode.PAPER,
                    side="buy_yes",
                    order_style="maker",
                    requested_price=Decimal("0.52"),
                    requested_notional_usd=Decimal("25"),
                    intent_at=now,
                    terminal_status="filled",
                    obligation_state="sealed",
                    eligible=True,
                    eligibility_reason="eligible",
                    aggregate_role="maker",
                    leg_count=1,
                    total_shares=Decimal("48.076923"),
                    vwap_fill_price=Decimal("0.52"),
                    total_fee_usd=None,
                    gross_model_edge=Decimal("0.06"),
                    latency_movement=Decimal("0.01"),
                    taker_spread=None,
                    taker_slippage=None,
                    maker_improvement=Decimal("0.01"),
                    fee_drag=None,
                    realized_net_edge=None,
                    adverse_selection_5m=None,
                    adverse_selection_30m=None,
                    sealed_at=now,
                ),
            ),
        )

    monkeypatch.setattr(operator_api, "load_execution_cost_orders", fake_orders)
    app = create_app(Settings(_env_file=None))
    app.state.sessions = _fake_sessions
    app.state.read_sessions = app.state.sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/v1/execution-cost-report/orders"
            "?mode=paper&limit=25&offset=5&eligible=true&role=maker"
            "&station=KLGA&kind=high&horizon=6-24h&side=buy_yes&reason=eligible"
        )

    assert response.status_code == 200
    assert captured == {
        "mode": ExecutionMode.PAPER,
        "limit": 25,
        "offset": 5,
        "eligible": True,
        "role": "maker",
        "station": "KLGA",
        "kind": "high",
        "horizon": "6-24h",
        "side": "buy_yes",
        "reason": "eligible",
    }
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["order_id"] == 7
    assert body["items"][0]["total_fee_usd"] is None
    assert body["items"][0]["realized_net_edge"] is None
