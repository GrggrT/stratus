"""Operator monitoring endpoints whose SQL joins need a real Postgres."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import (
    ExecutionMode,
    ForecastEvaluation,
    Market,
    MarketPrice,
    MarketStatus,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    ResolutionContract,
    SelectionPolicyDecision,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    StationStage,
    TradeSide,
    WeatherKind,
)
from stratus.services.selection_policy import (
    SHADOW_SELECTION_POLICIES,
    selection_horizon_bucket,
    selection_policy_accepts,
    selection_policy_hash,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
NOW = datetime(2026, 7, 7, 12, tzinfo=UTC)


async def test_official_resolution_operator_workflow_is_source_derived(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> None:
    now = datetime.now(UTC).replace(microsecond=123456)
    target_date = now.date() - timedelta(days=1)
    source_url = "https://www.wunderground.com/history/daily/KAAA"
    rules_raw = (
        f"Resolution is based on {source_url} for "
        f"{target_date.day} {target_date:%b} '{target_date:%y} using whole degrees "
        "Celsius after the first data point for the following date."
    )
    rules_hash = hashlib.sha256(rules_raw.encode("utf-8")).hexdigest()
    async with db_sessions.begin() as session:
        station = Station(
            icao="KAAA",
            name="Official API station",
            city="Fixture",
            lat=Decimal("40.0"),
            lon=Decimal("-73.0"),
            tz="UTC",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="official-api-market",
            event_id="official-api-event",
            station_id=station.id,
            market_date=target_date,
            kind=WeatherKind.HIGH,
            rules_raw=rules_raw,
            resolves_at=now - timedelta(minutes=5),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        contract = ResolutionContract(
            event_id=market.event_id,
            rules_hash=rules_hash,
            source_family="wunderground",
            source_url=source_url,
            station_code=station.icao,
            local_timezone=station.tz,
            statistic="high",
            source_unit="C",
            decimal_places=0,
            finalization_rule="next_day_first_point",
            license_status="restricted_unlicensed",
            storage_permitted=False,
            license_url="https://www.wunderground.com/company/legal",
            rules_raw=rules_raw,
        )
        session.add(contract)
        await session.flush()
        contract_id = contract.id

    witness = datetime.combine(target_date + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    published = witness + timedelta(minutes=1)
    raw_body = json.dumps(
        {
            "validTimeLocal": [f"{target_date.isoformat()}T00:00:00+00:00"],
            "temperatureMax": [31],
            "temperatureMin": [19],
            "finalizationWitnessAt": witness.isoformat(),
            "publishedAt": published.isoformat(),
            "rawSentinel": "must-never-appear-in-read-api",
        },
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(raw_body).hexdigest()
    relative_path = f"imports/wunderground/{contract_id}/{digest}.bin"
    artifact_path = tmp_path.joinpath(*relative_path.split("/"))
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_bytes(raw_body)
    unfinalized_body = json.dumps(
        {
            "validTimeLocal": [f"{target_date.isoformat()}T00:00:00+00:00"],
            "temperatureMax": [99],
            "temperatureMin": [-99],
        },
        separators=(",", ":"),
    ).encode("utf-8")
    unfinalized_digest = hashlib.sha256(unfinalized_body).hexdigest()
    unfinalized_relative_path = (
        f"imports/wunderground/{contract_id}/{unfinalized_digest}.bin"
    )
    unfinalized_path = tmp_path.joinpath(*unfinalized_relative_path.split("/"))
    unfinalized_path.write_bytes(unfinalized_body)

    draft = {
        "contract_id": contract_id,
        "rules_hash": rules_hash,
        "license_id": "api-license-v1",
        "grantee": "STRATUS integration fixture",
        "endpoint_url": "https://licensed.example/v1/daily/KAAA?units=m",
        "adapter_name": "wunderground-licensed-json-field-map",
        "adapter_version": "1",
        "parser_config": {
            "schema_id": "provider-daily-v1",
            "series_mode": "parallel_arrays",
            "date_path": ["validTimeLocal"],
            "high_path": ["temperatureMax"],
            "low_path": ["temperatureMin"],
            "finalization_witness_at_path": ["finalizationWitnessAt"],
            "published_at_path": ["publishedAt"],
            "station_code_literal": "KAAA",
            "source_unit_literal": "C",
            "decimal_places_literal": 0,
            "finalization_rule_literal": "next_day_first_point",
            "series_anchor_local_time": "00:00:00",
            "source_window_start_local": "00:00:00",
            "source_window_end_local": "00:00:00",
            "source_window_end_day_offset": 1,
        },
        "terms_url": "https://licensed.example/terms/v1",
        "terms_sha256": "b" * 64,
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "storage_permitted": True,
        "commercial_use_permitted": True,
        "max_response_age_seconds": 3600,
    }
    app = create_app(
        Settings(
            database_url=migrated_database_url,
            operator_api_key="secret-key",
            official_resolution_artifact_root=tmp_path,
        )
    )
    app.state.sessions = db_sessions
    headers = {"X-API-Key": "secret-key"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mismatched_window = await client.post(
            "/api/v1/official-resolution-sources/license-policy-hash",
            headers=headers,
            json={
                **draft,
                "parser_config": {
                    **draft["parser_config"],
                    "source_window_start_local": "07:00:00",
                    "source_window_end_local": "07:00:00",
                },
            },
        )
        assert mismatched_window.status_code == 409, mismatched_window.text
        assert "window" in mismatched_window.json()["detail"].lower()
        preview = await client.post(
            "/api/v1/official-resolution-sources/license-policy-hash",
            headers=headers,
            json=draft,
        )
        assert preview.status_code == 200, preview.text
        policy_hash = preview.json()["policy_sha256"]
        wrong_hash = await client.post(
            "/api/v1/official-resolution-sources/license-attestations",
            headers=headers,
            json={
                **draft,
                "approved_policy_sha256": "0" * 64,
                "reviewer": "integration reviewer",
                "reviewed_at": (now - timedelta(minutes=5)).isoformat(),
                "rationale": "written commercial retention and equivalence approval",
            },
        )
        assert wrong_hash.status_code == 409, wrong_hash.text
        assert "policy hash" in wrong_hash.json()["detail"].lower()
        attestation = await client.post(
            "/api/v1/official-resolution-sources/license-attestations",
            headers=headers,
            json={
                **draft,
                "approved_policy_sha256": policy_hash,
                "reviewer": "integration reviewer",
                "reviewed_at": (now - timedelta(minutes=5)).isoformat(),
                "rationale": "written commercial retention and equivalence approval",
            },
        )
        assert attestation.status_code == 200, attestation.text
        unfinalized = await client.post(
            "/api/v1/official-resolution-sources/import",
            headers=headers,
            json={
                "contract_id": contract_id,
                "rules_hash": rules_hash,
                "relative_artifact_path": unfinalized_relative_path,
                "actor": "integration-test",
                "reason": "prove missing finality remains fail closed",
            },
        )
        assert unfinalized.status_code == 422, unfinalized.text
        imported = await client.post(
            "/api/v1/official-resolution-sources/import",
            headers=headers,
            json={
                "contract_id": contract_id,
                "rules_hash": rules_hash,
                "relative_artifact_path": relative_path,
                "actor": "integration-test",
                "reason": "seal licensed official source response",
            },
        )
        summary = await client.get("/api/v1/official-resolution-sources", headers=headers)
        drilldown = await client.get(
            f"/api/v1/official-resolution-sources/contracts?contract_id={contract_id}",
            headers=headers,
        )

    assert imported.status_code == 200, imported.text
    assert imported.json()["source_value"] == 31.0
    assert imported.json()["source_token"] == "31"
    assert summary.status_code == 200
    assert summary.json()["source_integrity"] == "valid"
    assert summary.json()["exact_actuals"] == 1
    assert summary.json()["projected_settlement_actuals"] == 1
    assert drilldown.status_code == 200
    serialized = drilldown.text
    assert "must-never-appear-in-read-api" not in serialized
    assert "response_body" not in serialized


async def test_accuracy_overview_excludes_legacy_unattested_evaluations(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="legacy-overview-market",
            event_id="legacy-overview-event",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="legacy accuracy overview fixture",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            resolved_outcome=True,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        model_prob = ModelProb(
            market_id=market.id,
            ts=NOW - timedelta(hours=12),
            prob=Decimal("0.7"),
            sigma=Decimal("1"),
            horizon_h=Decimal("12"),
            components={},
        )
        session.add(model_prob)
        await session.flush()
        session.add(
            ForecastEvaluation(
                market_id=market.id,
                model_prob_id=model_prob.id,
                decision_at=model_prob.ts,
                independent_sample_key="legacy-overview-sample",
                model_probability=model_prob.prob,
                resolved_outcome=True,
                model_brier=Decimal("0.09"),
                market_brier=Decimal("0.16"),
                evaluated_at=NOW,
                details={"legacy": True},
            )
        )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    engine = db_sessions.kw["bind"]
    statement_count = 0

    def count_statement(
        _connection: Any,
        _cursor: Any,
        _statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        nonlocal statement_count
        statement_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_statement)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/v1/accuracy-overview")
            cold_statement_count = statement_count
            cached_response = await client.get("/api/v1/accuracy-overview")
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", count_statement)

    assert response.status_code == 200
    assert cached_response.status_code == 200
    assert cached_response.json() == response.json()
    assert statement_count == cold_statement_count
    assert cold_statement_count == 16
    payload = response.json()
    assert payload["benchmark_protocol"] == "clob-dual-v1"
    assert payload["sample_scope"] == "prospective"
    assert payload["forecast_evaluations"] == 0
    assert payload["independent_samples"] == 0
    assert payload["mean_model_brier"] is None
    assert payload["mean_market_brier"] is None
    assert payload["promotion"]["protocol_version"] == "fixed-range-brier-v3"
    assert payload["promotion"]["registration_epoch"] == "fixed-range-brier-v3-20260714"
    assert payload["promotion"]["test_method"] == "predictable-range-hoeffding-v1"
    assert payload["promotion"]["registered_trials"] == 0
    assert payload["promotion"]["activated_trials"] == 0
    assert payload["promotion"]["maximum_evidence_blocks"] == 0
    assert len(payload["promotion"]["activation_manifest_hash"]) == 64


async def test_station_lifecycle_reports_stage_and_readiness(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="RJTT",
            name="Tokyo Haneda Airport",
            city="Tokyo",
            lat=Decimal("35.5533"),
            lon=Decimal("139.7811"),
            tz="Asia/Tokyo",
            iem_network="JP__ASOS",
            iem_station_id="RJTT",
            stage=StationStage.PAPER,
        )
        session.add(station)
        session.add(
            Station(
                icao="EGLL",
                name="London Heathrow Airport",
                city="London",
                lat=Decimal("51.4700"),
                lon=Decimal("-0.4543"),
                tz="Europe/London",
                active=False,
                stage=StationStage.SHADOW,
            )
        )
        await session.flush()
        session.add(
            Market(
                polymarket_id="pm-rjtt-1",
                event_id="ev-rjtt-1",
                station_id=station.id,
                market_date=NOW.date(),
                kind=WeatherKind.HIGH,
                rules_raw="Resolves to RJTT.",
                resolves_at=NOW + timedelta(hours=6),
                status=MarketStatus.ACTIVE,
                station_verified=True,
            )
        )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/stations")

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_stations"] == 1
    assert payload["shadow_stations"] == 0
    assert payload["inactive_stations"] == 1
    tokyo = next(item for item in payload["items"] if item["icao"] == "RJTT")
    assert tokyo["active_markets"] == 1
    assert tokyo["data_ready_for_paper"] is False


async def test_orders_endpoint_joins_order_signal_market_station(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-orders-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        signal = Signal(
            market_id=market.id,
            ts=NOW,
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.6"),
            market_price=Decimal("0.52"),
            edge=Decimal("0.08"),
            kelly_frac=Decimal("0.01"),
            size_usd=Decimal("20"),
            confidence=SignalConfidence.B,
            status=SignalStatus.EXECUTED,
        )
        session.add(signal)
        await session.flush()
        session.add(
            Order(
                signal_id=signal.id,
                mode=ExecutionMode.LIVE,
                clob_order_id="0xlive",
                side=TradeSide.BUY_YES,
                price=Decimal("0.53"),
                size_usd=Decimal("20"),
                filled_usd=Decimal("20"),
                fee_usd=Decimal("0.04"),
                status="filled",
                filled_at=NOW,
            )
        )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/orders")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["mode"] == "live"
    assert item["clob_order_id"] == "0xlive"
    assert item["station_icao"] == "KLGA"
    assert item["polymarket_id"] == "pm-orders-1"
    # Compatibility `price` and the explicit field both carry the requested
    # limit for legacy rows; without atomic legs its historical execution
    # price is exposed as the actual VWAP fallback.
    assert item["price"] == 0.53
    assert item["requested_limit_price"] == 0.53
    assert item["actual_fill_vwap"] == 0.53


async def test_positions_endpoint_reports_mark_to_market(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    from stratus.db.models import MarketPrice

    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-mtm-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        # Open BUY_NO position: 40 shares at 0.50 = $20 cost.
        session.add(
            Position(
                market_id=market.id,
                mode=ExecutionMode.PAPER,
                side=TradeSide.BUY_NO,
                avg_price=Decimal("0.5"),
                size_shares=Decimal("40"),
                size_usd=Decimal("20"),
                status=PositionStatus.OPEN,
            )
        )
        # Latest YES midpoint 0.40 -> NO side mid 0.60 -> mark $24, +$4.
        price_now = datetime.now(UTC)
        for age_minutes, midpoint in ((70, "0.50"), (10, "0.48"), (2, "0.45"), (0, "0.40")):
            session.add(
                MarketPrice(
                    market_id=market.id,
                    ts=price_now - timedelta(minutes=age_minutes),
                    midpoint=Decimal(midpoint),
                    best_bid=Decimal(midpoint) - Decimal("0.01"),
                    best_ask=Decimal(midpoint) + Decimal("0.01"),
                    bid_depth_usd=Decimal("100"),
                    ask_depth_usd=Decimal("100"),
                )
            )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/positions")

    item = response.json()["items"][0]
    assert item["benchmark_protocol"] == "legacy-synthetic-yes-v0"
    assert item["sample_scope"] == "historical"
    assert item["mark_price"] == 0.6
    assert item["unrealized_pnl_usd"] == 4.0
    assert item["liquidation_price"] == 0.59
    assert item["liquidation_pnl_usd"] == 3.6
    assert item["pnl_change_1m_usd"] == 2.0
    assert item["pnl_change_5m_usd"] == 3.2
    assert item["pnl_change_60m_usd"] == 4.0
    assert item["mark_stale"] is False
    assert item["mark_age_seconds"] < 30
    assert item["market_date"] == str(NOW.date())
    assert item["kind"] == "high"


async def test_equity_endpoint_filters_by_mode(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-eqm-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        for mode, pnl in ((ExecutionMode.PAPER, "12.5"), (ExecutionMode.LIVE, "-3.0")):
            session.add(
                Position(
                    market_id=market.id,
                    mode=mode,
                    side=TradeSide.BUY_YES,
                    avg_price=Decimal("0.5"),
                    size_shares=Decimal("40"),
                    size_usd=Decimal("20"),
                    status=PositionStatus.CLOSED,
                    pnl_usd=Decimal(pnl),
                    closed_at=NOW + timedelta(hours=1),
                )
            )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        paper = await client.get("/api/v1/equity?mode=paper")
        live = await client.get("/api/v1/equity?mode=live")
        bad = await client.get("/api/v1/equity?mode=yolo")

    assert [p["cumulative_pnl_usd"] for p in paper.json()["points"]] == [12.5]
    assert [p["cumulative_pnl_usd"] for p in live.json()["points"]] == [-3.0]
    assert bad.status_code == 422


async def test_market_drilldown_endpoints_return_series_and_fail_soft_orderbook(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="LFPB",
            name="Paris Le Bourget",
            city="Paris",
            lat=Decimal("48.96"),
            lon=Decimal("2.44"),
            tz="Europe/Paris",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-drill-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        session.add_all(
            [
                MarketPrice(
                    market_id=market.id,
                    ts=NOW - timedelta(minutes=2),
                    midpoint=Decimal("0.410000"),
                    best_bid=Decimal("0.40"),
                    best_ask=Decimal("0.42"),
                    bid_depth_usd=Decimal("12.5"),
                    ask_depth_usd=Decimal("18.0"),
                ),
                MarketPrice(
                    market_id=market.id,
                    ts=NOW - timedelta(minutes=1),
                    midpoint=Decimal("0.430000"),
                    best_bid=Decimal("0.42"),
                    best_ask=Decimal("0.44"),
                    bid_depth_usd=Decimal("14.5"),
                    ask_depth_usd=Decimal("20.0"),
                ),
                ModelProb(
                    market_id=market.id,
                    ts=NOW - timedelta(minutes=1),
                    prob=Decimal("0.610000"),
                    sigma=Decimal("1.2500"),
                    horizon_h=Decimal("12.00"),
                    components={},
                ),
                Signal(
                    market_id=market.id,
                    ts=NOW,
                    side=TradeSide.BUY_YES,
                    model_prob=Decimal("0.62"),
                    market_price=Decimal("0.43"),
                    edge=Decimal("0.19"),
                    kelly_frac=Decimal("0.05"),
                    size_usd=Decimal("25"),
                    confidence=SignalConfidence.A,
                    status=SignalStatus.PENDING,
                ),
            ]
        )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        prices = await client.get(f"/api/v1/market-timeseries?market_id={market.id}")
        prospective_probability = await client.get(
            f"/api/v1/probability-timeseries?market_id={market.id}"
        )
        historical_probability = await client.get(
            f"/api/v1/probability-timeseries?market_id={market.id}"
            "&benchmark_protocol=legacy-synthetic-yes-v0"
        )
        book = await client.get(f"/api/v1/orderbook?market_id={market.id}")

    assert prices.status_code == 200
    prices_payload = prices.json()
    assert prices_payload["benchmark_protocol"] == "legacy-synthetic-yes-v0"
    assert prices_payload["sample_scope"] == "historical"
    assert [p["midpoint"] for p in prices_payload["points"]] == [0.41, 0.43]
    assert prospective_probability.status_code == 200
    prospective_payload = prospective_probability.json()
    assert prospective_payload["benchmark_protocol"] == "clob-dual-v1"
    assert prospective_payload["sample_scope"] == "prospective"
    assert prospective_payload["points"] == []
    assert historical_probability.status_code == 200
    historical_payload = historical_probability.json()
    assert historical_payload["benchmark_protocol"] == "legacy-synthetic-yes-v0"
    assert historical_payload["sample_scope"] == "historical"
    probability_points = historical_payload["points"]
    assert {p["source"] for p in probability_points} == {"model", "signal"}
    assert {p["benchmark_protocol"] for p in probability_points} == {
        "legacy-synthetic-yes-v0"
    }
    assert {p["sample_scope"] for p in probability_points} == {"historical"}
    assert probability_points[-1]["edge"] == 0.19
    assert book.status_code == 200
    assert book.json()["source_status"] == "unavailable"
    assert "token id" in book.json()["error"]


async def test_live_equity_endpoint_returns_cumulative_series(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-eq-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        for offset, pnl in ((1, "20.0"), (2, "-5.0")):
            session.add(
                Position(
                    market_id=market.id,
                    mode=ExecutionMode.LIVE,
                    side=TradeSide.BUY_YES,
                    avg_price=Decimal("0.5"),
                    size_shares=Decimal("40"),
                    size_usd=Decimal("20"),
                    status=PositionStatus.CLOSED,
                    pnl_usd=Decimal(pnl),
                    closed_at=NOW + timedelta(hours=offset),
                )
            )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/live-equity")

    assert response.status_code == 200
    points = response.json()["points"]
    assert [p["cumulative_pnl_usd"] for p in points] == [20.0, 15.0]


async def test_performance_summary_endpoint_supports_paper_robust_metrics(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-paper-summary",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        for pnl, cost in (("100.0", "50.0"), ("-10.0", "25.0"), ("-10.0", "25.0")):
            session.add(
                Position(
                    market_id=market.id,
                    mode=ExecutionMode.PAPER,
                    side=TradeSide.BUY_YES,
                    avg_price=Decimal("0.5"),
                    size_shares=Decimal("50"),
                    size_usd=Decimal(cost),
                    status=PositionStatus.CLOSED,
                    pnl_usd=Decimal(pnl),
                    closed_at=NOW,
                )
            )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/performance-summary?mode=paper")

    assert response.status_code == 200
    body = response.json()
    assert body["performance_sample_scope"] == "all-protocol-history"
    assert body["benchmark_protocol"] == "clob-dual-v1"
    assert body["benchmark_sample_scope"] == "prospective"
    assert body["benchmark_positions"] == 0
    assert body["total_pnl_usd"] == 80.0
    assert body["roi"] == 0.8
    assert body["robust_pnl_usd"] == -20.0
    assert body["robust_roi"] == -0.4


async def test_benchmark_reports_exclude_legacy_protocol_rows(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia",
            city="New York City",
            lat=Decimal("40.77"),
            lon=Decimal("-73.87"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id="pm-edge-1",
            event_id="ev-1",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW,
            status=MarketStatus.RESOLVED,
            station_verified=True,
            resolved_outcome=True,
        )
        session.add(market)
        await session.flush()
        # A resolved winning paper position with a recorded closing line, plus
        # its filled order and signal so the edge report can decompose it.
        position = Position(
            market_id=market.id,
            mode=ExecutionMode.PAPER,
            side=TradeSide.BUY_YES,
            avg_price=Decimal("0.5"),
            size_shares=Decimal("40"),
            size_usd=Decimal("20"),
            status=PositionStatus.CLOSED,
            pnl_usd=Decimal("20"),
            clv=Decimal("0.04"),
            fee_usd=Decimal("0.04"),
            closed_at=NOW,
        )
        session.add(position)
        signal = Signal(
            market_id=market.id,
            ts=NOW - timedelta(minutes=5),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.7"),
            market_price=Decimal("0.5"),
            edge=Decimal("0.2"),
            kelly_frac=Decimal("0.02"),
            size_usd=Decimal("20"),
            confidence=SignalConfidence.A,
            status=SignalStatus.EXECUTED,
        )
        session.add(signal)
        await session.flush()
        session.add(
            MarketPrice(
                market_id=market.id,
                ts=NOW - timedelta(minutes=6),
                best_bid=Decimal("0.590000"),
                best_ask=Decimal("0.610000"),
                midpoint=Decimal("0.600000"),
                bid_depth_usd=Decimal("100"),
                ask_depth_usd=Decimal("100"),
            )
        )
        session.add(
            ModelProb(
                market_id=market.id,
                ts=signal.ts,
                prob=Decimal("0.700000"),
                sigma=Decimal("1.0000"),
                horizon_h=Decimal("0.08"),
                components={
                    "pipeline": {"version": "stratus-accuracy-v4-20260713"},
                    "shrinkage": {"p_model_raw": 0.8},
                },
            )
        )
        order = Order(
                signal_id=signal.id,
                mode=ExecutionMode.PAPER,
                clob_order_id=f"paper-{signal.id}",
                side=TradeSide.BUY_YES,
                price=Decimal("0.5"),
                size_usd=Decimal("20"),
                filled_usd=Decimal("20"),
                fee_usd=Decimal("0.04"),
                status="filled",
                filled_at=NOW - timedelta(minutes=2),
            )
        session.add(order)
        await session.flush()
        for policy in SHADOW_SELECTION_POLICIES:
            session.add(
                SelectionPolicyDecision(
                    order_id=order.id,
                    policy_name=policy.name,
                    policy_hash=selection_policy_hash(policy),
                    max_predicted_edge=(
                        Decimal(str(policy.max_predicted_edge))
                        if policy.max_predicted_edge is not None
                        else None
                    ),
                    predicted_edge=signal.edge,
                    accepted=selection_policy_accepts(
                        policy,
                        predicted_edge=float(signal.edge),
                        side=signal.side.value,
                        horizon_bucket=selection_horizon_bucket(
                            signal.ts,
                            market.resolves_at,
                        ),
                    ),
                    pipeline_version="stratus-accuracy-v4-20260713",
                    signal_ts=signal.ts,
                    filled_at=order.filled_at,
                    decided_at=NOW - timedelta(minutes=1),
                )
            )

    app = create_app(
        Settings(database_url=migrated_database_url, operator_api_key=None)
    )
    app.state.sessions = db_sessions
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/edge-report?mode=paper")

    assert response.status_code == 200
    body = response.json()
    assert body["benchmark_protocol"] == "clob-dual-v1"
    assert body["sample_scope"] == "prospective"
    assert body["benchmark_positions"] == 0
    assert body["clv_samples"] == 0
    assert body["n_resolved_trades"] == 0
    assert body["skill_pnl_usd"] is None
    assert body["luck_pnl_usd"] is None
    assert body["pipeline_version"] is None
    assert body["pipeline_resolved_trades"] == 0
    assert body["pipeline_brier_model"] is None
    assert body["pipeline_brier_market"] is None
    # The full formal checklist remains present and the legacy fixture cannot
    # satisfy any prospective dual-token evidence requirement.
    assert body["gate_passed"] is False
    names = {check["name"] for check in body["gate_checks"]}
    assert names == {
        "stations_calibrated",
        "resolved_positions_200",
        "independent_station_days_100",
        "pipeline_version_samples_100",
        "clv_samples_100",
        "avg_clv_positive",
        "clv_beat_rate_52",
        "roi_ci_excludes_zero",
        "skill_pnl_significant",
        "brier_model_beats_market",
        "forecast_input_provenance",
    }
    assert any(not check["passed"] for check in body["gate_checks"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        selection_response = await client.get(
            "/api/v1/selection-policy-report?mode=paper"
        )

    assert selection_response.status_code == 200
    selection = selection_response.json()
    assert selection["benchmark_protocol"] == "clob-dual-v1"
    assert selection["sample_scope"] == "prospective"
    assert selection["pipeline_version"] is None
    assert selection["historical_benchmark_protocol"] == "legacy-synthetic-yes-v0"
    assert selection["historical_sample_scope"] == "historical"
    assert selection["historical_opportunities"] == 1
    assert selection["recorded_opportunities"] == 0
    assert selection["current_opportunities"] == 0
    assert {item["name"] for item in selection["candidates"]} == {
        "baseline",
        "edge-cap-10c-v1",
        "edge-cap-15c-v1",
        "edge-cap-20c-v1",
        "exclude-buy-yes-v1",
        "exclude-horizon-6-24h-v1",
    }
    assert all(item["description"] for item in selection["candidates"])
    assert sum(item["is_reference"] for item in selection["candidates"]) == 1
    assert all(item["opportunities"] == 0 for item in selection["candidates"])
    assert all(item["eligible"] is False for item in selection["candidates"])

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        brier_response = await client.get("/api/v1/brier-report?mode=paper")

    assert brier_response.status_code == 200
    brier = brier_response.json()
    assert brier["benchmark_protocol"] == "clob-dual-v1"
    assert brier["sample_scope"] == "prospective"
    assert brier["n_resolved_trades"] == 0
    assert brier["brier_model"] is None
    assert brier["brier_market"] is None
    assert brier["brier_raw_model"] is None
    assert brier["raw_count"] == 0
    assert brier["slices"] == []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        clv_response = await client.get("/api/v1/clv-report?mode=paper")

    assert clv_response.status_code == 200
    clv = clv_response.json()
    assert clv["benchmark_protocol"] == "clob-dual-v1"
    assert clv["sample_scope"] == "prospective"
    assert clv["n_closed_positions"] == 0
    assert clv["avg_clv"] is None
    assert clv["clv_beat_rate"] is None
    assert clv["slices"] == []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        brier_drilldown_response = await client.get(
            "/api/v1/root-cause-drilldown?mode=paper&metric=brier&dimension=station&value=KLGA"
        )

    assert brier_drilldown_response.status_code == 200
    brier_drilldown = brier_drilldown_response.json()
    assert brier_drilldown["metric"] == "brier"
    assert brier_drilldown["dimension"] == "station"
    assert brier_drilldown["value"] == "KLGA"
    assert brier_drilldown["benchmark_protocol"] == "clob-dual-v1"
    assert brier_drilldown["sample_scope"] == "prospective"
    assert brier_drilldown["count"] == 0
    assert brier_drilldown["items"] == []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        pipeline_drilldown_response = await client.get(
            "/api/v1/root-cause-drilldown?mode=paper&metric=brier"
            "&dimension=pipeline_version&value=stratus-accuracy-v4-20260713"
        )

    assert pipeline_drilldown_response.status_code == 200
    pipeline_drilldown = pipeline_drilldown_response.json()
    assert pipeline_drilldown["dimension"] == "pipeline_version"
    assert pipeline_drilldown["count"] == 0
    assert pipeline_drilldown["items"] == []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        clv_drilldown_response = await client.get(
            "/api/v1/root-cause-drilldown"
            "?mode=paper&metric=clv&dimension=price_band&value=0.50-0.75"
        )

    assert clv_drilldown_response.status_code == 200
    clv_drilldown = clv_drilldown_response.json()
    assert clv_drilldown["metric"] == "clv"
    assert clv_drilldown["dimension"] == "price_band"
    assert clv_drilldown["value"] == "0.50-0.75"
    assert clv_drilldown["benchmark_protocol"] == "clob-dual-v1"
    assert clv_drilldown["sample_scope"] == "prospective"
    assert clv_drilldown["count"] == 0
    assert clv_drilldown["items"] == []

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        actions_response = await client.get("/api/v1/root-cause-actions?mode=paper")

    assert actions_response.status_code == 200
    actions = actions_response.json()
    assert actions["mode"] == "paper"
    assert actions["benchmark_protocol"] == "clob-dual-v1"
    assert actions["sample_scope"] == "prospective"
    assert actions["items"] == []
