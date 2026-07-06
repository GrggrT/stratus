from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from stratus.collectors.open_meteo import ForecastValue
from stratus.db.models import Market, MarketStatus, WeatherKind
from stratus.repositories.forecasts import build_forecast_upsert, upsert_forecasts
from stratus.repositories.markets import (
    DiscoveredMarket,
    latest_station_before,
    upsert_market,
)
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs


def test_forecast_upsert_targets_identity_constraint() -> None:
    value = ForecastValue(
        station_id=1,
        model="gfs",
        run_time=datetime(2026, 7, 5, 18, tzinfo=UTC),
        valid_date=date(2026, 7, 6),
        kind=WeatherKind.HIGH,
        temp_c=Decimal("22.4"),
        fetched_at=datetime(2026, 7, 5, 18, 2, tzinfo=UTC),
    )

    sql = str(build_forecast_upsert([value]).compile(dialect=postgresql.dialect()))

    assert "ON CONFLICT ON CONSTRAINT uq_forecasts_identity DO UPDATE" in sql
    assert "temp_c = excluded.temp_c" in sql


async def test_forecast_upsert_is_batched_below_driver_parameter_limit() -> None:
    value = ForecastValue(
        station_id=1,
        model="gefs_m01",
        run_time=datetime(2026, 7, 5, 18, tzinfo=UTC),
        valid_date=date(2026, 7, 6),
        kind=WeatherKind.HIGH,
        temp_c=Decimal("22.4"),
        fetched_at=datetime(2026, 7, 5, 18, 2, tzinfo=UTC),
    )
    session = AsyncMock()

    written = await upsert_forecasts(session, [value] * 2001, batch_size=1000)

    assert written == 2001
    assert session.execute.await_count == 3


async def test_model_probability_upsert_takes_transaction_lock_and_sorts_rows() -> None:
    now = datetime(2026, 7, 6, 18, tzinfo=UTC)
    values = [
        ModelProbabilityValue(
            market_id=market_id,
            ts=now,
            prob=Decimal("0.5"),
            sigma=Decimal("1.0"),
            horizon_h=Decimal("12"),
            components={},
        )
        for market_id in (2, 1)
    ]
    session = AsyncMock()

    written = await upsert_model_probs(session, values)

    assert written == 2
    assert session.execute.await_count == 2
    lock_statement = session.execute.await_args_list[0].args[0]
    insert_statement = session.execute.await_args_list[1].args[0]
    assert "pg_advisory_xact_lock" in str(lock_statement)
    assert insert_statement.compile().params["market_id_m0"] == 1


async def test_model_probability_upsert_validates_batch_size() -> None:
    value = ModelProbabilityValue(
        market_id=1,
        ts=datetime(2026, 7, 6, 18, tzinfo=UTC),
        prob=Decimal("0.5"),
        sigma=Decimal("1.0"),
        horizon_h=Decimal("12"),
        components={},
    )

    try:
        await upsert_model_probs(AsyncMock(), [value], batch_size=0)
    except ValueError as exc:
        assert "batch_size" in str(exc)
    else:
        raise AssertionError("batch_size=0 must fail")


def _discovered_market(**overrides) -> DiscoveredMarket:
    defaults = dict(
        polymarket_id="pm-1",
        event_id="ev-1",
        condition_id="0xc",
        yes_token_id="yes-token",
        no_token_id="no-token",
        station_id=5,
        market_date=date(2026, 7, 7),
        kind=WeatherKind.HIGH,
        bucket_low_c=Decimal("28.5"),
        bucket_high_c=Decimal("29.5"),
        resolution_source="https://www.weather.gov/okx/KLGA",
        rules_raw="Resolves to the official KLGA reading.",
        station_verified=False,
        station_verification_reason="market_station_mismatch",
        created_at=datetime(2026, 7, 5, 12, tzinfo=UTC),
        resolves_at=datetime(2026, 7, 8, 4, tzinfo=UTC),
        fees_enabled=False,
        fee_rate=Decimal(0),
        fee_exponent=1,
        fee_taker_only=True,
    )
    defaults.update(overrides)
    return DiscoveredMarket(**defaults)


def _existing_verified_market(value: DiscoveredMarket) -> Market:
    return Market(
        polymarket_id=value.polymarket_id,
        event_id=value.event_id,
        station_id=value.station_id,
        market_date=value.market_date,
        kind=value.kind,
        rules_raw=value.rules_raw,
        resolution_source=value.resolution_source,
        station_verified=True,
        station_verification_reason=None,
        status=MarketStatus.ACTIVE,
    )


def _session_returning(market: Market | None) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()  # Session.add is synchronous
    result = MagicMock()
    result.scalar_one_or_none.return_value = market
    session.execute.return_value = result
    return session


async def test_upsert_market_revokes_verification_when_rules_change() -> None:
    baseline = _discovered_market()
    existing = _existing_verified_market(baseline)
    value = _discovered_market(rules_raw="Resolves to a DIFFERENT station reading.")

    market, revoked = await upsert_market(_session_returning(existing), value)

    assert revoked is True
    assert market.station_verified is False
    assert market.status is MarketStatus.BLOCKED
    assert market.station_verification_reason == "market_station_mismatch"


async def test_upsert_market_keeps_manual_verification_when_rules_unchanged() -> None:
    baseline = _discovered_market()
    existing = _existing_verified_market(baseline)
    value = _discovered_market(station_verification_reason="station_unknown")

    market, revoked = await upsert_market(_session_returning(existing), value)

    assert revoked is False
    assert market.station_verified is True
    assert market.status is MarketStatus.ACTIVE
    assert market.station_verification_reason is None


async def test_upsert_market_new_market_uses_parser_verdict() -> None:
    value = _discovered_market(station_verified=True, station_verification_reason=None)

    market, revoked = await upsert_market(_session_returning(None), value)

    assert revoked is False
    assert market.station_verified is True
    assert market.status is MarketStatus.ACTIVE


async def test_station_lineage_ignores_unverified_markets() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session.execute.return_value = result

    await latest_station_before(session, "New York City", date(2026, 7, 7))

    statement = session.execute.await_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "markets.station_verified IS true" in sql
