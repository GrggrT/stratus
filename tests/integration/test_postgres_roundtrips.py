"""Round-trips against a real Postgres for the SQL the mock tests cannot see:

every ON CONFLICT constraint target, DISTINCT ON, the advisory lock, row
locks, and the retention delete. A wrong constraint name or a Postgres-only
construct that does not parse fails here instead of in production.
"""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.aviation_weather import MetarObservation
from stratus.collectors.clob import PriceSnapshot
from stratus.collectors.open_meteo import ForecastValue
from stratus.db.models import (
    ExecutionMode,
    Forecast,
    JobRun,
    Market,
    MarketPrice,
    MarketStatus,
    Observation,
    Order,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    TradeSide,
    WeatherKind,
)
from stratus.repositories.daily_actuals import DailyActualValue, upsert_daily_actuals
from stratus.repositories.forecasts import upsert_forecasts
from stratus.repositories.job_runs import record_job_run
from stratus.repositories.market_prices import upsert_market_prices
from stratus.repositories.markets import DiscoveredMarket, upsert_market
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs
from stratus.repositories.observations import upsert_observations
from stratus.repositories.signals import SignalValue, insert_signals, prune_signals
from stratus.services.telegram_reports import _resolved_probability_rows

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

NOW = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)

SessionFactory = async_sessionmaker[AsyncSession]


async def _make_station(sessions: SessionFactory) -> int:
    async with sessions.begin() as session:
        station = Station(
            icao="KLGA",
            name="LaGuardia Airport",
            city="New York City",
            lat=Decimal("40.7772"),
            lon=Decimal("-73.8726"),
            tz="America/New_York",
        )
        session.add(station)
        await session.flush()
        return station.id


async def _make_market(sessions: SessionFactory, station_id: int, **overrides: object) -> int:
    fields: dict[str, object] = {
        "polymarket_id": "pm-1",
        "event_id": "ev-1",
        "station_id": station_id,
        "market_date": date(2026, 7, 5),
        "kind": WeatherKind.HIGH,
        "rules_raw": "Resolves to the official KLGA reading.",
        "resolves_at": NOW - timedelta(hours=1),
        "status": MarketStatus.ACTIVE,
        "station_verified": True,
        "bucket_low_c": Decimal("28.50"),
        "bucket_high_c": Decimal("29.50"),
    }
    fields.update(overrides)
    async with sessions.begin() as session:
        market = Market(**fields)
        session.add(market)
        await session.flush()
        return market.id


def _signal_value(market_id: int, ts: datetime, status: SignalStatus) -> SignalValue:
    return SignalValue(
        market_id=market_id,
        ts=ts,
        side=TradeSide.BUY_YES,
        model_prob=Decimal("0.600000"),
        market_price=Decimal("0.500000"),
        edge=Decimal("0.100000"),
        kelly_frac=Decimal("0.010000"),
        size_usd=Decimal("10.0000"),
        confidence=SignalConfidence.B,
        status=status,
        skip_reason="integration-test",
    )


async def test_forecast_upsert_updates_in_place(db_sessions: SessionFactory) -> None:
    station_id = await _make_station(db_sessions)
    value = ForecastValue(
        station_id=station_id,
        model="gfs",
        run_time=NOW,
        valid_date=date(2026, 7, 7),
        kind=WeatherKind.HIGH,
        temp_c=Decimal("22.40"),
        fetched_at=NOW,
    )
    async with db_sessions.begin() as session:
        await upsert_forecasts(session, [value])
    async with db_sessions.begin() as session:
        await upsert_forecasts(
            session, [replace(value, temp_c=Decimal("23.10"))]
        )

    async with db_sessions() as session:
        rows = (await session.execute(select(Forecast))).scalars().all()
    assert len(rows) == 1
    assert rows[0].temp_c == Decimal("23.10")


async def test_observation_and_daily_actual_upserts(db_sessions: SessionFactory) -> None:
    station_id = await _make_station(db_sessions)
    observation = MetarObservation(
        station_id=station_id,
        obs_time=NOW,
        temp_c=Decimal("21.00"),
        raw_metar="KLGA 061151Z ...",
    )
    async with db_sessions.begin() as session:
        await upsert_observations(session, [observation])
    async with db_sessions.begin() as session:
        await upsert_observations(
            session, [replace(observation, temp_c=Decimal("21.70"))]
        )

    actual = DailyActualValue(
        station_id=station_id,
        date=date(2026, 7, 5),
        kind=WeatherKind.HIGH,
        temp_c=Decimal("29.00"),
    )
    async with db_sessions.begin() as session:
        await upsert_daily_actuals(session, [actual])
    async with db_sessions.begin() as session:
        await upsert_daily_actuals(
            session, [replace(actual, temp_c=Decimal("29.30"))]
        )

    async with db_sessions() as session:
        observations = (await session.execute(select(Observation))).scalars().all()
    assert len(observations) == 1
    assert observations[0].temp_c == Decimal("21.70")


async def test_signal_dedup_and_retention(db_sessions: SessionFactory) -> None:
    station_id = await _make_station(db_sessions)
    market_id = await _make_market(db_sessions, station_id)
    old_ts = NOW - timedelta(days=40)

    skipped = _signal_value(market_id, old_ts, SignalStatus.SKIPPED)
    async with db_sessions.begin() as session:
        assert await insert_signals(session, [skipped]) == 1
    async with db_sessions.begin() as session:
        # Same (market_id, ts): uq_signals_market_time deduplicates.
        assert await insert_signals(session, [skipped]) == 0

    async with db_sessions.begin() as session:
        expired_with_order = Signal(
            market_id=market_id,
            ts=old_ts + timedelta(minutes=1),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.600000"),
            market_price=Decimal("0.500000"),
            edge=Decimal("0.100000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.EXPIRED,
        )
        session.add(expired_with_order)
        await session.flush()
        session.add(
            Order(
                signal_id=expired_with_order.id,
                mode=ExecutionMode.PAPER,
                side=TradeSide.BUY_YES,
                price=Decimal("0.500000"),
                size_usd=Decimal("10.0000"),
                status="filled",
            )
        )

    async with db_sessions.begin() as session:
        deleted = await prune_signals(session, older_than=NOW - timedelta(days=30))
    assert deleted == 1

    async with db_sessions() as session:
        remaining = (await session.execute(select(Signal))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].status is SignalStatus.EXPIRED


async def test_market_upsert_revokes_verification_on_rules_change(
    db_sessions: SessionFactory,
) -> None:
    station_id = await _make_station(db_sessions)
    value = DiscoveredMarket(
        polymarket_id="pm-42",
        event_id="ev-42",
        condition_id="0xc",
        yes_token_id="yes-token",
        no_token_id="no-token",
        station_id=station_id,
        market_date=date(2026, 7, 7),
        kind=WeatherKind.HIGH,
        bucket_low_c=Decimal("28.50"),
        bucket_high_c=Decimal("29.50"),
        resolution_source="https://www.weather.gov/okx/KLGA",
        rules_raw="Resolves to the official KLGA reading.",
        station_verified=True,
        station_verification_reason=None,
        created_at=NOW - timedelta(days=1),
        resolves_at=NOW + timedelta(days=1),
        fees_enabled=False,
        fee_rate=Decimal(0),
        fee_exponent=1,
        fee_taker_only=True,
    )
    async with db_sessions.begin() as session:
        _, revoked = await upsert_market(session, value)
    assert revoked is False

    changed = replace(
        value,
        rules_raw="Resolves to a DIFFERENT station reading.",
        station_verified=False,
        station_verification_reason="market_station_mismatch",
    )
    async with db_sessions.begin() as session:
        _, revoked = await upsert_market(session, changed)
    assert revoked is True

    async with db_sessions() as session:
        row = (await session.execute(select(Market))).scalar_one()
    assert row.station_verified is False
    assert row.status is MarketStatus.BLOCKED
    assert row.station_verification_reason == "market_station_mismatch"


async def test_model_probs_distinct_on_latest_pre_resolution_row(
    db_sessions: SessionFactory,
) -> None:
    station_id = await _make_station(db_sessions)
    market_id = await _make_market(
        db_sessions,
        station_id,
        status=MarketStatus.RESOLVED,
        resolved_outcome=True,
    )

    def prob(ts: datetime, value: str) -> ModelProbabilityValue:
        return ModelProbabilityValue(
            market_id=market_id,
            ts=ts,
            prob=Decimal(value),
            sigma=Decimal("1.0000"),
            horizon_h=Decimal("12.00"),
            components={},
        )

    async with db_sessions.begin() as session:
        # Exercises the advisory lock and uq_model_probs_market_time.
        await upsert_model_probs(
            session,
            [
                prob(NOW - timedelta(hours=3), "0.400000"),
                prob(NOW - timedelta(hours=2), "0.700000"),
                # After resolves_at: must be excluded by the SQL filter.
                prob(NOW - timedelta(minutes=30), "0.900000"),
            ],
        )

    async with db_sessions() as session:
        rows = await _resolved_probability_rows(session, now=NOW)
    assert rows == [("KLGA", True, 0.7)]


async def test_market_price_and_job_run_upserts(db_sessions: SessionFactory) -> None:
    station_id = await _make_station(db_sessions)
    market_id = await _make_market(db_sessions, station_id)

    def snapshot(midpoint: str) -> PriceSnapshot:
        return PriceSnapshot(
            token_id="token-1",
            ts=NOW,
            midpoint=Decimal(midpoint),
            best_bid=Decimal("0.480000"),
            best_ask=Decimal("0.520000"),
            bid_depth_usd=Decimal("100.0000"),
            ask_depth_usd=Decimal("120.0000"),
        )

    async with db_sessions.begin() as session:
        await upsert_market_prices(session, [(market_id, snapshot("0.500000"))])
    async with db_sessions.begin() as session:
        await upsert_market_prices(session, [(market_id, snapshot("0.510000"))])

    async with db_sessions() as session:
        prices = (await session.execute(select(MarketPrice))).scalars().all()
    assert len(prices) == 1
    assert prices[0].midpoint == Decimal("0.510000")

    async with db_sessions.begin() as session:
        await record_job_run(
            session,
            job_id="integration",
            scheduled_at=NOW,
            finished_at=NOW,
            status="error",
            error="boom",
        )
    async with db_sessions.begin() as session:
        await record_job_run(
            session,
            job_id="integration",
            scheduled_at=NOW,
            finished_at=NOW + timedelta(seconds=5),
            status="success",
            error=None,
        )

    async with db_sessions() as session:
        runs = (await session.execute(select(JobRun))).scalars().all()
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].error is None
