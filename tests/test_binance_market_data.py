from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from stratus.collectors.binance_archive import BinanceAggregateTradeData
from stratus.collectors.binance_spot import (
    BinancePayloadError,
    candle_sequence_gaps,
    parse_book_ticker,
    parse_depth_snapshot,
    parse_kline,
    parse_symbol_info,
)
from stratus.config import Settings
from stratus.jobs.collect_binance_market_data import collect_binance_market_data
from stratus.repositories.crypto_market_data import upsert_crypto_aggregate_trades

NOW = datetime(2026, 7, 12, 12, 1, tzinfo=UTC)


async def test_aggregate_trade_upsert_is_bounded_without_temporary_ddl() -> None:
    session = AsyncMock()
    template = BinanceAggregateTradeData(
        symbol="BTCUSDT",
        aggregate_trade_id=1,
        price=Decimal("64000.125"),
        quantity=Decimal("0.00125"),
        first_trade_id=10,
        last_trade_id=10,
        trade_time=NOW,
        buyer_is_maker=False,
        best_price_match=True,
        source="binance_archive",
        received_at=NOW,
    )
    values = [
        replace(
            template,
            aggregate_trade_id=index,
            first_trade_id=index + 10,
            last_trade_id=index + 10,
        )
        for index in range(5)
    ]

    assert await upsert_crypto_aggregate_trades(
        session,
        values,
        {"BTCUSDT": 7},
        batch_size=2,
    ) == 5
    assert session.execute.await_count == 3
    rendered = "\n".join(str(call.args[0]) for call in session.execute.await_args_list)
    assert "INSERT INTO crypto_aggregate_trades" in rendered
    assert "CREATE TEMP" not in rendered
    assert "TRUNCATE" not in rendered


def _symbol_payload() -> dict[str, object]:
    return {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "isSpotTradingAllowed": True,
        "ocoAllowed": True,
        "otoAllowed": True,
        "orderTypes": ["LIMIT", "MARKET"],
        "defaultSelfTradePreventionMode": "EXPIRE_MAKER",
        "allowedSelfTradePreventionModes": ["EXPIRE_MAKER", "EXPIRE_TAKER"],
        "filters": [
            {
                "filterType": "PRICE_FILTER",
                "minPrice": "0.01000000",
                "maxPrice": "1000000.00000000",
                "tickSize": "0.01000000",
            },
            {
                "filterType": "LOT_SIZE",
                "minQty": "0.00001000",
                "maxQty": "9000.00000000",
                "stepSize": "0.00001000",
            },
            {
                "filterType": "NOTIONAL",
                "minNotional": "5.00000000",
                "maxNotional": "9000000.00000000",
            },
        ],
    }


def _kline(open_time: datetime) -> list[object]:
    open_ms = int(open_time.timestamp() * 1000)
    return [
        open_ms,
        "64000.00000000",
        "64100.00000000",
        "63900.00000000",
        "64050.00000000",
        "1.25000000",
        open_ms + 59_999,
        "80062.50000000",
        123,
        "0.70000000",
        "44835.00000000",
        "0",
    ]


def test_parse_symbol_info_extracts_critical_filters_and_stable_hash() -> None:
    payload = _symbol_payload()
    parsed = parse_symbol_info(payload, fetched_at=NOW)

    assert parsed.symbol == "BTCUSDT"
    assert parsed.price_tick == Decimal("0.01000000")
    assert parsed.quantity_step == Decimal("0.00001000")
    assert parsed.minimum_quantity == Decimal("0.00001000")
    assert parsed.minimum_notional == Decimal("5.00000000")
    assert parsed.maximum_notional == Decimal("9000000.00000000")
    assert parsed.spot_trading_allowed is True
    assert parsed.supports_oco is True
    assert parsed.supports_oto is True
    assert parsed.filters["_SYMBOL_METADATA"]["order_types"] == ["LIMIT", "MARKET"]

    reversed_payload = _symbol_payload()
    reversed_payload["filters"] = list(reversed(reversed_payload["filters"]))  # type: ignore[arg-type]
    reordered = parse_symbol_info(reversed_payload, fetched_at=NOW)
    assert reordered.filter_hash == parsed.filter_hash


def test_parse_symbol_info_rejects_missing_execution_filter() -> None:
    payload = _symbol_payload()
    payload["filters"] = payload["filters"][:1]  # type: ignore[index]
    with pytest.raises(BinancePayloadError, match="LOT_SIZE"):
        parse_symbol_info(payload, fetched_at=NOW)


def test_parse_kline_preserves_decimals_and_detects_sequence_gap() -> None:
    first = parse_kline(
        "BTCUSDT",
        "1m",
        _kline(NOW - timedelta(minutes=1)),
        server_time=NOW,
        received_at=NOW,
    )
    current = parse_kline(
        "BTCUSDT",
        "1m",
        _kline(NOW.replace(second=0, microsecond=0)),
        server_time=NOW,
        received_at=NOW,
    )

    assert first.close_price == Decimal("64050.00000000")
    assert first.trade_count == 123
    assert first.closed is True
    assert current.closed is False

    third = replace(
        current,
        open_time=current.open_time + timedelta(minutes=1),
        close_time=current.close_time + timedelta(minutes=1),
    )
    assert candle_sequence_gaps([first, third]) == [
        (first.open_time + timedelta(minutes=1), third.open_time)
    ]


def test_parse_book_ticker_supports_stream_payload_and_rejects_crossed_book() -> None:
    parsed = parse_book_ticker(
        {
            "u": 42,
            "s": "BTCUSDT",
            "b": "64000.00",
            "B": "1.5",
            "a": "64000.01",
            "A": "0.75",
        },
        received_at=NOW,
        source="binance_ws",
    )

    assert parsed.update_id == 42
    assert parsed.best_bid == Decimal("64000.00")
    assert parsed.best_ask == Decimal("64000.01")
    assert Decimal("0") < parsed.spread_bps < Decimal("1")
    assert Decimal("-1") <= parsed.imbalance <= Decimal("1")

    with pytest.raises(BinancePayloadError, match="crossed"):
        parse_book_ticker(
            {
                "symbol": "BTCUSDT",
                "bidPrice": "64001",
                "bidQty": "1",
                "askPrice": "64000",
                "askQty": "1",
            },
            received_at=NOW,
        )


def test_parse_depth_snapshot_aggregates_valid_ordered_levels() -> None:
    parsed = parse_depth_snapshot(
        "BTCUSDT",
        {
            "lastUpdateId": 99,
            "bids": [["64000", "1"], ["63999", "2"]],
            "asks": [["64001", "0.5"], ["64002", "1.5"]],
        },
        received_at=NOW,
    )

    assert parsed.update_id == 99
    assert parsed.depth_valid is True
    assert parsed.bid_depth_quote == Decimal("191998")
    assert parsed.ask_depth_quote == Decimal("128003.5")
    assert parsed.best_bid == Decimal("64000")
    assert parsed.best_ask == Decimal("64001")

    with pytest.raises(BinancePayloadError, match="not descending"):
        parse_depth_snapshot(
            "BTCUSDT",
            {
                "lastUpdateId": 100,
                "bids": [["63999", "1"], ["64000", "1"]],
                "asks": [["64001", "1"]],
            },
            received_at=NOW,
        )


async def test_disabled_market_data_job_is_a_database_and_network_noop() -> None:
    result = await collect_binance_market_data(Settings(binance_enabled=False))
    assert result.status == "configuration_disabled"
    assert result.candles_written == 0


def test_binance_market_data_settings_are_fail_closed_and_validated() -> None:
    settings = Settings()
    assert settings.binance_enabled is False
    assert settings.binance_market_data_rest_url == "https://data-api.binance.vision"
    assert settings.binance_kline_interval == "1m"
    with pytest.raises(ValueError, match="kline interval"):
        Settings(binance_kline_interval="13m")
    with pytest.raises(ValueError, match="depth limit"):
        Settings(binance_depth_limit=30)
