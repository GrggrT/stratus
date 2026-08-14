from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_archive import (
    BinanceArchiveBatch,
    BinanceArchiveError,
)
from stratus.collectors.binance_spot import BinanceCandleData, BinanceSymbolInfo
from stratus.config import Settings
from stratus.db.models import (
    CryptoArchiveImport,
    CryptoCandle,
    CryptoDataGap,
    RuntimeControl,
    TradingVenue,
    VenueMode,
)
from stratus.jobs.backfill_binance_klines import backfill_binance_klines
from stratus.repositories.crypto_market_data import (
    record_crypto_archive_import,
    record_crypto_archive_stream_verification,
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
TRADING_DATE = date(2026, 7, 10)


def _symbol_info(symbol: str) -> BinanceSymbolInfo:
    return BinanceSymbolInfo(
        symbol=symbol,
        base_asset=symbol.removesuffix("USDT"),
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
        filter_hash=("a" if symbol == "BTCUSDT" else "b") * 64,
        fetched_at=datetime.now(UTC),
    )


def _archive_candles(symbol: str) -> list[BinanceCandleData]:
    midnight = datetime.combine(TRADING_DATE, datetime.min.time(), tzinfo=UTC)
    return [
        BinanceCandleData(
            symbol=symbol,
            interval="12h",
            open_time=midnight + timedelta(hours=12 * index),
            close_time=midnight + timedelta(hours=12 * (index + 1)) - timedelta(microseconds=1),
            open_price=Decimal("64000"),
            high_price=Decimal("64100"),
            low_price=Decimal("63900"),
            close_price=Decimal("64050"),
            base_volume=Decimal("1.25"),
            quote_volume=Decimal("80062.5"),
            trade_count=123,
            taker_buy_base_volume=Decimal("0.7"),
            taker_buy_quote_volume=Decimal("44835"),
            closed=True,
            source="binance_archive",
            received_at=datetime.now(UTC),
        )
        for index in range(2)
    ]


class FakeArchiveGateway:
    def __init__(self, *, fail_symbols: set[str] | None = None) -> None:
        self.fail_symbols = fail_symbols or set()

    async def fetch_daily_klines(
        self, symbol: str, interval: str, trading_date: date
    ) -> BinanceArchiveBatch:
        if symbol in self.fail_symbols:
            raise BinanceArchiveError("checksum mismatch", kind="checksum")
        assert interval == "12h"
        assert trading_date == TRADING_DATE
        return BinanceArchiveBatch(
            symbol=symbol,
            interval=interval,
            trading_date=trading_date,
            source_url=f"https://data.binance.vision/{symbol}.zip",
            checksum_sha256=("c" if symbol == "BTCUSDT" else "d") * 64,
            archive_bytes=1000,
            candles=_archive_candles(symbol),
        )


async def test_archive_manifest_is_idempotent_and_failed_day_is_retryable(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    async with db_sessions.begin() as session:
        legacy = await session.get(RuntimeControl, 1, with_for_update=True)
        assert legacy is not None
        # Migration 0047 boots the global control fail-closed. Establish the
        # prior state explicitly so this test can prove that a Binance archive
        # failure seals only Binance and does not mutate the legacy control.
        legacy.kill_switch = False
        await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
        await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.BACKTEST)
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, False)
        await upsert_crypto_symbols(
            session,
            [_symbol_info("BTCUSDT"), _symbol_info("ETHUSDT")],
        )
    settings = Settings(
        database_url=migrated_database_url,
        binance_enabled=True,
        binance_kline_interval="12h",
    )

    first = await backfill_binance_klines(
        settings,
        db_sessions,
        start_date=TRADING_DATE,
        end_date=TRADING_DATE,
        archive_client=FakeArchiveGateway(fail_symbols={"ETHUSDT"}),
    )
    assert first.status == "partial"
    assert (first.files_imported, first.files_skipped, first.files_failed) == (1, 0, 1)
    assert first.candles_written == 2

    async with db_sessions() as session:
        manifests = (
            (
                await session.execute(
                    select(CryptoArchiveImport).order_by(CryptoArchiveImport.symbol_id)
                )
            )
            .scalars()
            .all()
        )
        gap = await session.scalar(select(CryptoDataGap))
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        legacy = await session.get(RuntimeControl, 1)
    assert [item.status for item in manifests] == ["imported", "failed"]
    assert gap is not None
    assert gap.status == "open"
    assert control.kill_switch is True
    assert legacy is not None
    assert legacy.kill_switch is False

    second = await backfill_binance_klines(
        settings,
        db_sessions,
        start_date=TRADING_DATE,
        end_date=TRADING_DATE,
        archive_client=FakeArchiveGateway(),
    )
    assert second.status == "success"
    assert (second.files_imported, second.files_skipped, second.files_failed) == (1, 1, 0)
    assert second.candles_written == 2

    async with db_sessions() as session:
        manifest_count = await session.scalar(select(func.count()).select_from(CryptoArchiveImport))
        candle_count = await session.scalar(select(func.count()).select_from(CryptoCandle))
        gap = await session.scalar(select(CryptoDataGap))
    assert manifest_count == 2
    assert candle_count == 4
    assert gap is not None
    assert gap.status == "resolved"


async def test_stream_verification_preserves_import_and_rejects_conflicting_provenance(
    db_sessions: SessionFactory,
) -> None:
    imported_at = datetime(2026, 7, 11, tzinfo=UTC)
    checksum = "e" * 64
    async with db_sessions.begin() as session:
        symbol_ids = await upsert_crypto_symbols(session, [_symbol_info("BTCUSDT")])
        symbol_id = symbol_ids["BTCUSDT"]
        await record_crypto_archive_import(
            session,
            symbol_id=symbol_id,
            interval="aggTrades",
            trading_date=TRADING_DATE,
            source_url="https://data.binance.vision/original.zip",
            checksum_sha256=checksum,
            archive_bytes=1_000,
            row_count=200,
            status="imported",
            error=None,
            imported_at=imported_at,
        )

    async with db_sessions.begin() as session:
        await record_crypto_archive_stream_verification(
            session,
            symbol_id=symbol_id,
            interval="aggTrades",
            trading_date=TRADING_DATE,
            source_url="https://data.binance.vision/verified.zip",
            checksum_sha256=checksum,
            archive_bytes=1_000,
            row_count=200,
            verified_at=imported_at + timedelta(hours=1),
        )

    async with db_sessions() as session:
        manifest = await session.scalar(select(CryptoArchiveImport))
    assert manifest is not None
    assert manifest.status == "imported"
    assert manifest.source_url == "https://data.binance.vision/original.zip"
    assert manifest.imported_at == imported_at

    async with db_sessions() as session:
        with pytest.raises(ValueError, match="conflicts with persisted provenance"):
            await record_crypto_archive_stream_verification(
                session,
                symbol_id=symbol_id,
                interval="aggTrades",
                trading_date=TRADING_DATE,
                source_url="https://data.binance.vision/conflict.zip",
                checksum_sha256="f" * 64,
                archive_bytes=1_000,
                row_count=200,
                verified_at=imported_at + timedelta(hours=2),
            )
