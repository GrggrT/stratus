from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_spot import (
    BinanceBookTickerData,
    BinanceCandleData,
    BinanceIpBanned,
    BinanceSymbolInfo,
    parse_book_ticker,
    parse_depth_snapshot,
)
from stratus.config import Settings
from stratus.db.models import (
    CryptoCandle,
    CryptoDataGap,
    CryptoMarketSnapshot,
    CryptoSymbol,
    RuntimeControl,
    TradingVenue,
    VenueMode,
)
from stratus.jobs.collect_binance_market_data import collect_binance_market_data
from stratus.jobs.stream_binance_market_data import consume_binance_market_stream
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    set_venue_enabled,
    set_venue_kill_switch,
    set_venue_mode,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _symbol_info(symbol: str, fetched_at: datetime) -> BinanceSymbolInfo:
    base_asset = symbol.removesuffix("USDT")
    filters = {
        "PRICE_FILTER": {"filter_type": "PRICE_FILTER", "tick_size": "0.01"},
        "LOT_SIZE": {
            "filter_type": "LOT_SIZE",
            "min_qty": "0.00001",
            "step_size": "0.00001",
        },
        "NOTIONAL": {
            "filter_type": "NOTIONAL",
            "min_notional": "5",
            "max_notional": "9000000",
        },
    }
    return BinanceSymbolInfo(
        symbol=symbol,
        base_asset=base_asset,
        quote_asset="USDT",
        status="TRADING",
        spot_trading_allowed=True,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.00001"),
        minimum_quantity=Decimal("0.00001"),
        minimum_notional=Decimal("5"),
        maximum_notional=Decimal("9000000"),
        supports_oco=True,
        supports_oto=True,
        filters=filters,
        filter_hash=("a" if symbol == "BTCUSDT" else "b") * 64,
        fetched_at=fetched_at,
    )


def _candles(symbol: str, base_time: datetime) -> list[BinanceCandleData]:
    values = []
    for offset in (-2, -1, 0):
        open_time = base_time + timedelta(minutes=offset)
        values.append(
            BinanceCandleData(
                symbol=symbol,
                interval="1m",
                open_time=open_time,
                close_time=open_time + timedelta(minutes=1) - timedelta(milliseconds=1),
                open_price=Decimal("64000"),
                high_price=Decimal("64100"),
                low_price=Decimal("63900"),
                close_price=Decimal("64050"),
                base_volume=Decimal("1.25"),
                quote_volume=Decimal("80062.5"),
                trade_count=123,
                taker_buy_base_volume=Decimal("0.7"),
                taker_buy_quote_volume=Decimal("44835"),
                closed=offset < 0,
                source="binance_rest",
                received_at=base_time,
            )
        )
    return values


class FakePublicGateway:
    def __init__(self) -> None:
        self.base_time = datetime.now(UTC).replace(second=0, microsecond=0)
        self.exchange_info_calls = 0

    async def ping(self) -> None:
        return None

    async def server_time(self) -> datetime:
        return datetime.now(UTC)

    async def exchange_info(self, symbols: tuple[str, ...]) -> list[BinanceSymbolInfo]:
        self.exchange_info_calls += 1
        now = datetime.now(UTC)
        return [_symbol_info(symbol, now) for symbol in symbols]

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
        server_time: datetime,
    ) -> list[BinanceCandleData]:
        assert interval == "1m"
        assert limit == 3
        assert server_time.tzinfo is not None
        return _candles(symbol, self.base_time)

    async def book_tickers(self, symbols: tuple[str, ...]) -> list[BinanceBookTickerData]:
        now = datetime.now(UTC)
        return [
            parse_book_ticker(
                {
                    "u": index,
                    "s": symbol,
                    "b": "64000.00",
                    "B": "1.5",
                    "a": "64000.01",
                    "A": "0.75",
                },
                received_at=now,
                source="binance_rest",
            )
            for index, symbol in enumerate(symbols, start=100)
        ]

    async def depth_snapshots(
        self, symbols: tuple[str, ...], *, limit: int = 20
    ) -> list[BinanceBookTickerData]:
        assert limit == 20
        now = datetime.now(UTC)
        return [
            parse_depth_snapshot(
                symbol,
                {
                    "last_update_id": index,
                    "bids": [["64000.00", "1.5"], ["63999.99", "0.5"]],
                    "asks": [["64000.01", "0.75"], ["64000.02", "0.25"]],
                },
                received_at=now,
            )
            for index, symbol in enumerate(symbols, start=300)
        ]


class BannedPublicGateway(FakePublicGateway):
    async def server_time(self) -> datetime:
        raise BinanceIpBanned("IP banned", kind="ip_ban", status_code=418)


class ClockDriftPublicGateway(FakePublicGateway):
    async def server_time(self) -> datetime:
        return datetime.now(UTC) + timedelta(seconds=2)


class FakeBookTickerStream:
    def __init__(self, values: list[BinanceBookTickerData]) -> None:
        self.values = values

    async def messages(
        self,
        symbols: tuple[str, ...],
        *,
        stop_event: asyncio.Event,
        stale_after: timedelta,
    ) -> AsyncIterator[BinanceBookTickerData]:
        assert set(symbols) == {"BTCUSDT", "ETHUSDT"}
        assert stale_after.total_seconds() > 0
        for value in self.values:
            yield value
        stop_event.set()


async def _enable_binance_shadow(sessions: SessionFactory) -> None:
    async with sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.SHADOW)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)


def _settings(url: str) -> Settings:
    return Settings(
        database_url=url,
        binance_enabled=True,
        binance_max_clock_drift_ms=10_000,
    )


async def test_market_data_job_is_idempotent_and_preserves_manual_disable(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _enable_binance_shadow(db_sessions)
    gateway = FakePublicGateway()
    settings = _settings(migrated_database_url)

    first = await collect_binance_market_data(settings, db_sessions, gateway=gateway)
    assert first.status == "success"
    assert (first.symbols_refreshed, first.candles_written, first.snapshots_written) == (
        2,
        6,
        4,
    )
    assert first.gaps_detected == 0

    second = await collect_binance_market_data(settings, db_sessions, gateway=gateway)
    assert second.status == "success"
    assert second.symbols_refreshed == 0
    assert second.candles_written == 6
    assert second.snapshots_written == 0
    assert gateway.exchange_info_calls == 1

    async with db_sessions() as session:
        symbol_count = await session.scalar(select(func.count()).select_from(CryptoSymbol))
        candle_count = await session.scalar(select(func.count()).select_from(CryptoCandle))
        snapshot_count = await session.scalar(
            select(func.count()).select_from(CryptoMarketSnapshot)
        )
        legacy = await session.get(RuntimeControl, 1)
    assert (symbol_count, candle_count, snapshot_count) == (2, 6, 4)
    assert legacy is not None
    assert legacy.kill_switch is False

    async with db_sessions.begin() as session:
        await session.execute(
            update(CryptoSymbol)
            .where(CryptoSymbol.symbol == "BTCUSDT")
            .values(
                enabled=False,
                fetched_at=datetime.now(UTC) - timedelta(days=1),
            )
        )
    refreshed = await collect_binance_market_data(settings, db_sessions, gateway=gateway)
    assert refreshed.symbols_refreshed == 2
    async with db_sessions() as session:
        btc = await session.scalar(select(CryptoSymbol).where(CryptoSymbol.symbol == "BTCUSDT"))
    assert btc is not None
    assert btc.enabled is False


async def test_ip_ban_opens_gap_and_halts_only_binance(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _enable_binance_shadow(db_sessions)
    settings = _settings(migrated_database_url)

    with pytest.raises(BinanceIpBanned):
        await collect_binance_market_data(settings, db_sessions, gateway=BannedPublicGateway())

    async with db_sessions() as session:
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        legacy = await session.get(RuntimeControl, 1)
        gap = await session.scalar(select(CryptoDataGap))
    assert control.kill_switch is True
    assert "IP ban" in (control.halted_reason or "")
    assert legacy is not None
    assert legacy.kill_switch is False
    assert gap is not None
    assert gap.stream == "rest_collection"
    assert gap.status == "open"

    retry_gateway = FakePublicGateway()
    retry = await collect_binance_market_data(settings, db_sessions, gateway=retry_gateway)
    assert retry.status == "venue_halted"
    assert retry_gateway.exchange_info_calls == 0


async def test_websocket_stream_deduplicates_and_fails_closed_on_sequence_regression(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _enable_binance_shadow(db_sessions)
    settings = _settings(migrated_database_url)
    await collect_binance_market_data(settings, db_sessions, gateway=FakePublicGateway())

    received_at = datetime.now(UTC)

    def ticker(symbol: str, update_id: int) -> BinanceBookTickerData:
        return parse_book_ticker(
            {
                "u": update_id,
                "s": symbol,
                "b": "64000.00",
                "B": "1.5",
                "a": "64000.01",
                "A": "0.75",
            },
            received_at=received_at,
            source="binance_ws",
        )

    stream = FakeBookTickerStream(
        [
            ticker("BTCUSDT", 1000),
            ticker("BTCUSDT", 1000),
            ticker("ETHUSDT", 2000),
            ticker("BTCUSDT", 999),
        ]
    )
    stopped = asyncio.Event()
    result = await consume_binance_market_stream(
        settings,
        db_sessions,
        stream,
        stop_event=stopped,
        flush_interval=0,
    )

    assert result.status == "stopped"
    assert result.snapshots_written == 2
    assert result.duplicates_skipped == 1
    assert result.sequence_regressions == 1
    async with db_sessions() as session:
        ws_count = await session.scalar(
            select(func.count())
            .select_from(CryptoMarketSnapshot)
            .where(CryptoMarketSnapshot.snapshot_type == "book_ticker_ws")
        )
        gap = await session.scalar(
            select(CryptoDataGap).where(CryptoDataGap.stream == "book_ticker_sequence")
        )
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        legacy = await session.get(RuntimeControl, 1)
    assert ws_count == 2
    assert gap is not None
    assert gap.expected_cursor == ">=1000"
    assert gap.actual_cursor == "999"
    assert control.kill_switch is True
    assert legacy is not None
    assert legacy.kill_switch is False


async def test_clock_drift_stops_execution_but_keeps_public_collection(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _enable_binance_shadow(db_sessions)
    settings = Settings(
        database_url=migrated_database_url,
        binance_enabled=True,
        binance_max_clock_drift_ms=100,
    )

    result = await collect_binance_market_data(
        settings, db_sessions, gateway=ClockDriftPublicGateway()
    )

    assert result.status == "success"
    assert result.candles_written == 6
    assert result.snapshots_written == 4
    assert result.gaps_detected == 1
    assert result.clock_drift_ms is not None
    assert result.clock_drift_ms > 100
    async with db_sessions() as session:
        gap = await session.scalar(
            select(CryptoDataGap).where(CryptoDataGap.stream == "clock_drift")
        )
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        legacy = await session.get(RuntimeControl, 1)
    assert gap is not None
    assert gap.status == "open"
    assert control.kill_switch is True
    assert legacy is not None
    assert legacy.kill_switch is False
