from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_archive import (
    BinanceAggregateTradeData,
    BinanceAggTradesArchiveBatch,
)
from stratus.collectors.binance_spot import BinanceSymbolInfo
from stratus.config import Settings
from stratus.db.models import (
    CryptoAggregateTrade,
    CryptoArchiveImport,
    CryptoDataGap,
    TradingVenue,
    VenueMode,
)
from stratus.jobs.backfill_binance_agg_trades import backfill_binance_agg_trades
from stratus.repositories.crypto_market_data import (
    upsert_crypto_aggregate_trades,
    upsert_crypto_symbols,
)
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
FIRST_DATE = date(2026, 7, 9)
SECOND_DATE = FIRST_DATE + timedelta(days=1)
SYMBOL = "BTCUSDT"


def _symbol_info() -> BinanceSymbolInfo:
    return BinanceSymbolInfo(
        symbol=SYMBOL,
        base_asset="BTC",
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
        filters={"PRICE_FILTER": {"tick_size": "0.01"}},
        filter_hash="a" * 64,
        fetched_at=datetime.now(UTC),
    )


def _trades(
    trading_date: date,
    *,
    first_aggregate_id: int,
    first_trade_id: int,
) -> list[BinanceAggregateTradeData]:
    midnight = datetime.combine(trading_date, datetime.min.time(), tzinfo=UTC)
    return [
        BinanceAggregateTradeData(
            symbol=SYMBOL,
            aggregate_trade_id=first_aggregate_id + index,
            price=Decimal("64000.125"),
            quantity=Decimal("0.00125"),
            first_trade_id=first_trade_id + index,
            last_trade_id=first_trade_id + index,
            trade_time=midnight + timedelta(seconds=index),
            buyer_is_maker=bool(index),
            best_price_match=True,
            source="binance_archive",
            received_at=datetime.now(UTC),
        )
        for index in range(2)
    ]


class FakeAggTradesArchiveGateway:
    def __init__(self, *, bad_second_day: bool = False) -> None:
        self.bad_second_day = bad_second_day

    async def fetch_daily_agg_trades(
        self, symbol: str, trading_date: date
    ) -> BinanceAggTradesArchiveBatch:
        assert symbol == SYMBOL
        if trading_date == FIRST_DATE:
            first_aggregate_id, first_trade_id = 100, 1000
        else:
            first_aggregate_id = 103 if self.bad_second_day else 102
            first_trade_id = 1003 if self.bad_second_day else 1002
        trades = _trades(
            trading_date,
            first_aggregate_id=first_aggregate_id,
            first_trade_id=first_trade_id,
        )
        return BinanceAggTradesArchiveBatch(
            symbol=symbol,
            trading_date=trading_date,
            source_url=f"https://data.binance.vision/{symbol}-{trading_date}.zip",
            checksum_sha256="b" * 64,
            archive_bytes=1000,
            trades=trades,
        )


async def test_agg_trades_backfill_is_atomic_idempotent_and_checks_adjacent_days(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.BACKTEST)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)
        await upsert_crypto_symbols(session, [_symbol_info()])
    settings = Settings(
        database_url=migrated_database_url,
        binance_enabled=True,
        binance_symbols=(SYMBOL,),
    )

    first = await backfill_binance_agg_trades(
        settings,
        db_sessions,
        start_date=FIRST_DATE,
        end_date=FIRST_DATE,
        archive_client=FakeAggTradesArchiveGateway(),
    )
    assert first.status == "success"
    assert (first.files_imported, first.files_skipped, first.files_failed) == (1, 0, 0)
    assert first.trades_written == 2

    repeated = await backfill_binance_agg_trades(
        settings,
        db_sessions,
        start_date=FIRST_DATE,
        end_date=FIRST_DATE,
        archive_client=FakeAggTradesArchiveGateway(),
    )
    assert (repeated.files_imported, repeated.files_skipped) == (0, 1)
    assert repeated.trades_written == 0

    failed = await backfill_binance_agg_trades(
        settings,
        db_sessions,
        start_date=SECOND_DATE,
        end_date=SECOND_DATE,
        archive_client=FakeAggTradesArchiveGateway(bad_second_day=True),
    )
    assert failed.status == "partial"
    assert failed.files_failed == 1
    assert failed.trades_written == 0

    async with db_sessions() as session:
        trade_count = await session.scalar(select(func.count()).select_from(CryptoAggregateTrade))
        failed_manifest = await session.scalar(
            select(CryptoArchiveImport).where(CryptoArchiveImport.trading_date == SECOND_DATE)
        )
        gap = await session.scalar(select(CryptoDataGap))
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
    assert trade_count == 2
    assert failed_manifest is not None and failed_manifest.status == "failed"
    assert gap is not None and gap.status == "open"
    assert control.kill_switch is True

    repaired = await backfill_binance_agg_trades(
        settings,
        db_sessions,
        start_date=SECOND_DATE,
        end_date=SECOND_DATE,
        archive_client=FakeAggTradesArchiveGateway(),
    )
    assert repaired.status == "success"
    assert repaired.trades_written == 2

    async with db_sessions() as session:
        trade_count = await session.scalar(select(func.count()).select_from(CryptoAggregateTrade))
        manifest_count = await session.scalar(select(func.count()).select_from(CryptoArchiveImport))
        repaired_manifest = await session.scalar(
            select(CryptoArchiveImport).where(CryptoArchiveImport.trading_date == SECOND_DATE)
        )
        gap = await session.scalar(select(CryptoDataGap))
    assert trade_count == 4
    assert manifest_count == 2
    assert repaired_manifest is not None and repaired_manifest.status == "imported"
    assert gap is not None and gap.status == "resolved"


async def test_aggregate_trade_bulk_insert_respects_asyncpg_bind_limit(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        symbol_ids = await upsert_crypto_symbols(session, [_symbol_info()])
        values = [
            BinanceAggregateTradeData(
                symbol=SYMBOL,
                aggregate_trade_id=10_000 + index,
                price=Decimal("64000.125"),
                quantity=Decimal("0.00125"),
                first_trade_id=20_000 + index,
                last_trade_id=20_000 + index,
                trade_time=datetime.combine(FIRST_DATE, datetime.min.time(), tzinfo=UTC)
                + timedelta(microseconds=index),
                buyer_is_maker=bool(index % 2),
                best_price_match=True,
                source="binance_archive",
                received_at=datetime.now(UTC),
            )
            for index in range(3001)
        ]
        assert await upsert_crypto_aggregate_trades(session, values, symbol_ids) == 3001

    async with db_sessions() as session:
        stored = await session.scalar(select(func.count()).select_from(CryptoAggregateTrade))
    assert stored == 3001

    async with db_sessions.begin() as session:
        with pytest.raises(ValueError, match="asyncpg bind limit"):
            await upsert_crypto_aggregate_trades(
                session,
                values,
                symbol_ids,
                batch_size=2501,
            )
