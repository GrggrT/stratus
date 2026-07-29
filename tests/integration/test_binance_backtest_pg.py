from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.backtesting.engine import BacktestConfig, BacktestDataError, BacktestStrategy
from stratus.backtesting.strategies import BuyAndHoldBenchmark
from stratus.collectors.binance_archive import (
    AGG_TRADES_ARCHIVE_KEY,
    BinanceAggregateTradeData,
)
from stratus.collectors.binance_spot import BinanceCandleData, BinanceSymbolInfo
from stratus.config import Settings
from stratus.db.models import CryptoArchiveImport, TradingVenue, VenueMode
from stratus.jobs.backtest_binance import run_binance_backtest
from stratus.repositories.crypto_market_data import (
    crypto_symbol_ids,
    record_crypto_archive_import,
    upsert_crypto_aggregate_trades,
    upsert_crypto_candles,
    upsert_crypto_symbols,
)
from stratus.repositories.venue_runtime_control import (
    set_venue_enabled,
    set_venue_kill_switch,
    set_venue_mode,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
SYMBOL = "BTCUSDT"
TRADING_DATE = date(2026, 7, 10)
START = datetime.combine(TRADING_DATE, datetime.min.time(), tzinfo=UTC)
END = START + timedelta(minutes=1)


def _symbol_info() -> BinanceSymbolInfo:
    return BinanceSymbolInfo(
        symbol=SYMBOL,
        base_asset="BTC",
        quote_asset="USDT",
        status="TRADING",
        spot_trading_allowed=True,
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
        maximum_notional=Decimal("9000000"),
        supports_oco=True,
        supports_oto=True,
        filters={"PRICE_FILTER": {"tick_size": "0.01"}},
        filter_hash="a" * 64,
        fetched_at=datetime.now(UTC),
    )


def _candles() -> list[BinanceCandleData]:
    return [
        BinanceCandleData(
            symbol=SYMBOL,
            interval="5m",
            open_time=START + timedelta(seconds=10 * index),
            close_time=START + timedelta(seconds=10 * (index + 1)),
            open_price=Decimal("100") + index,
            high_price=Decimal("101") + index,
            low_price=Decimal("99") + index,
            close_price=Decimal("100") + index,
            base_volume=Decimal("10"),
            quote_volume=Decimal("1000"),
            trade_count=100,
            taker_buy_base_volume=Decimal("5"),
            taker_buy_quote_volume=Decimal("500"),
            closed=True,
            source="binance_archive",
            received_at=datetime.now(UTC),
        )
        for index in range(2)
    ]


def _trades() -> list[BinanceAggregateTradeData]:
    values = [(1, "100"), (11, "101"), (21, "110")]
    return [
        BinanceAggregateTradeData(
            symbol=SYMBOL,
            aggregate_trade_id=index,
            price=Decimal(price),
            quantity=Decimal("2"),
            first_trade_id=1000 + index,
            last_trade_id=1000 + index,
            trade_time=START + timedelta(seconds=seconds),
            buyer_is_maker=bool(index % 2),
            best_price_match=True,
            source="binance_archive",
            received_at=datetime.now(UTC),
        )
        for index, (seconds, price) in enumerate(values, start=1)
    ]


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_quote=Decimal("1000"),
        order_notional=Decimal("100"),
        maker_fee_bps=Decimal("5"),
        taker_fee_bps=Decimal("10"),
        entry_slippage_bps=Decimal("5"),
        exit_slippage_bps=Decimal("5"),
        latency=timedelta(0),
        order_ttl=timedelta(seconds=20),
        maker_participation_rate=Decimal("0.05"),
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
    )


def _strategy_factory() -> BacktestStrategy:
    return BuyAndHoldBenchmark()


async def test_research_runner_streams_verified_data_and_fails_closed_on_manifest_gap(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.BACKTEST)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)
        symbol_ids = await upsert_crypto_symbols(session, [_symbol_info()])
        await upsert_crypto_candles(session, _candles(), symbol_ids)
        await upsert_crypto_aggregate_trades(
            session,
            _trades(),
            symbol_ids,
            batch_size=100,
        )
        for dataset in ("5m", AGG_TRADES_ARCHIVE_KEY):
            await record_crypto_archive_import(
                session,
                symbol_id=symbol_ids[SYMBOL],
                interval=dataset,
                trading_date=TRADING_DATE,
                source_url=f"https://data.binance.vision/{dataset}.zip",
                checksum_sha256="b" * 64,
                archive_bytes=1000,
                row_count=3 if dataset == AGG_TRADES_ARCHIVE_KEY else 2,
                status="imported",
                error=None,
                imported_at=datetime.now(UTC),
            )
    settings = Settings(database_url=migrated_database_url, binance_enabled=True)

    report = await run_binance_backtest(
        settings=settings,
        symbol=SYMBOL,
        interval="5m",
        start_time=START,
        end_time=END,
        config=_config(),
        strategy_factory=_strategy_factory,
        session_factory=db_sessions,
        bootstrap_samples=100,
    )

    assert report.baseline.event_count == 5
    assert report.baseline.data_hash
    assert report.baseline.data_hash != report.baseline.config_hash
    assert report.baseline.final_base_quantity == 0
    assert len(report.baseline.cycles) == 1
    assert report.gate.status == "NO_GO"

    with pytest.raises(BacktestDataError, match="persisted symbol filters"):
        await run_binance_backtest(
            settings=settings,
            symbol=SYMBOL,
            interval="5m",
            start_time=START,
            end_time=END,
            config=replace(_config(), price_tick=Decimal("0.1")),
            strategy_factory=_strategy_factory,
            session_factory=db_sessions,
            bootstrap_samples=100,
        )

    async with db_sessions.begin() as session:
        await session.execute(
            update(CryptoArchiveImport)
            .where(CryptoArchiveImport.interval == AGG_TRADES_ARCHIVE_KEY)
            .values(row_count=4)
        )

    with pytest.raises(BacktestDataError, match="row count mismatch"):
        await run_binance_backtest(
            settings=settings,
            symbol=SYMBOL,
            interval="5m",
            start_time=START,
            end_time=END,
            config=_config(),
            strategy_factory=_strategy_factory,
            session_factory=db_sessions,
            bootstrap_samples=100,
        )

    async with db_sessions.begin() as session:
        symbol_ids = await crypto_symbol_ids(session, (SYMBOL,))
        await session.execute(
            update(CryptoArchiveImport)
            .where(
                CryptoArchiveImport.symbol_id == symbol_ids[SYMBOL],
                CryptoArchiveImport.interval == AGG_TRADES_ARCHIVE_KEY,
            )
            .values(status="failed")
        )

    with pytest.raises(BacktestDataError, match="manifests are missing"):
        await run_binance_backtest(
            settings=settings,
            symbol=SYMBOL,
            interval="5m",
            start_time=START,
            end_time=END,
            config=_config(),
            strategy_factory=_strategy_factory,
            session_factory=db_sessions,
            bootstrap_samples=100,
        )
