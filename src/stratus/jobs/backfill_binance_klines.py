from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_archive import (
    BinanceArchiveClient,
    BinanceArchiveError,
    BinanceArchiveGateway,
)
from stratus.collectors.binance_spot import (
    BinancePayloadError,
    BinanceSdkPublicGateway,
)
from stratus.config import Settings, get_settings
from stratus.db.models import TradingVenue, VenueMode
from stratus.db.session import session_factory_scope
from stratus.repositories.crypto_market_data import (
    completed_crypto_archive_imports,
    crypto_symbol_ids,
    open_crypto_data_gap,
    record_crypto_archive_import,
    resolve_crypto_data_gap,
    upsert_crypto_candles,
    upsert_crypto_symbols,
)
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    set_venue_kill_switch,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
_BACKFILL_MODES = frozenset(
    {VenueMode.BACKTEST, VenueMode.SHADOW, VenueMode.TESTNET, VenueMode.LIVE}
)


@dataclass(frozen=True, slots=True)
class BinanceBackfillResult:
    status: str
    files_imported: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    candles_written: int = 0


def _dates(start_date: date, end_date: date) -> list[date]:
    return [
        start_date + timedelta(days=offset) for offset in range((end_date - start_date).days + 1)
    ]


def _source_url(settings: Settings, symbol: str, trading_date: date) -> str:
    filename = f"{symbol}-{settings.binance_kline_interval}-{trading_date.isoformat()}.zip"
    return (
        f"{settings.binance_archive_base_url.rstrip('/')}/daily/klines/"
        f"{symbol}/{settings.binance_kline_interval}/{filename}"
    )


async def _bootstrap_symbols(
    settings: Settings,
    sessions: SessionFactory,
) -> dict[str, int]:
    async with sessions() as session:
        symbol_ids = await crypto_symbol_ids(session, settings.binance_symbols)
    if set(symbol_ids) == set(settings.binance_symbols):
        return symbol_ids
    gateway = BinanceSdkPublicGateway(
        base_url=settings.binance_market_data_rest_url,
        timeout_ms=settings.binance_market_data_timeout_ms,
        retries=settings.binance_market_data_retries,
    )
    values = await gateway.exchange_info(settings.binance_symbols)
    invalid = [
        value.symbol for value in values if value.quote_asset != settings.binance_quote_asset
    ]
    if invalid:
        raise BinancePayloadError(
            "configured Binance quote asset mismatch: " + ", ".join(invalid),
            kind="payload",
        )
    async with sessions.begin() as session:
        return await upsert_crypto_symbols(session, values)


async def _run_backfill(
    settings: Settings,
    sessions: SessionFactory,
    archive: BinanceArchiveGateway,
    *,
    start_date: date,
    end_date: date,
) -> BinanceBackfillResult:
    symbol_ids = await _bootstrap_symbols(settings, sessions)
    async with sessions() as session:
        completed = await completed_crypto_archive_imports(
            session,
            symbol_ids,
            interval=settings.binance_kline_interval,
            start_date=start_date,
            end_date=end_date,
        )

    imported = 0
    skipped = 0
    failed = 0
    candles_written = 0
    stream = f"archive_klines:{settings.binance_kline_interval}"
    for trading_date in _dates(start_date, end_date):
        for symbol in settings.binance_symbols:
            if (symbol, trading_date) in completed:
                skipped += 1
                continue
            source_url = _source_url(settings, symbol, trading_date)
            try:
                batch = await archive.fetch_daily_klines(
                    symbol,
                    settings.binance_kline_interval,
                    trading_date,
                )
                if (
                    batch.symbol != symbol
                    or batch.interval != settings.binance_kline_interval
                    or batch.trading_date != trading_date
                ):
                    raise BinanceArchiveError(
                        "Binance archive metadata does not match request",
                        kind="archive_payload",
                    )
            except BinanceArchiveError as exc:
                failed += 1
                now = datetime.now(UTC)
                async with sessions.begin() as session:
                    await record_crypto_archive_import(
                        session,
                        symbol_id=symbol_ids[symbol],
                        interval=settings.binance_kline_interval,
                        trading_date=trading_date,
                        source_url=source_url,
                        checksum_sha256=None,
                        archive_bytes=0,
                        row_count=0,
                        status="failed",
                        error=str(exc)[:2000],
                        imported_at=now,
                    )
                    await open_crypto_data_gap(
                        session,
                        symbol_id=symbol_ids[symbol],
                        stream=stream,
                        detected_at=now,
                        expected_cursor=trading_date.isoformat(),
                        actual_cursor=exc.kind,
                        details={"source_url": source_url, "error": str(exc)[:1000]},
                    )
                    await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)
                logger.error(
                    "Binance archive import failed: symbol=%s date=%s kind=%s error=%s",
                    symbol,
                    trading_date,
                    exc.kind,
                    exc,
                )
                continue

            now = datetime.now(UTC)
            async with sessions.begin() as session:
                candles_written += await upsert_crypto_candles(session, batch.candles, symbol_ids)
                await record_crypto_archive_import(
                    session,
                    symbol_id=symbol_ids[symbol],
                    interval=batch.interval,
                    trading_date=batch.trading_date,
                    source_url=batch.source_url,
                    checksum_sha256=batch.checksum_sha256,
                    archive_bytes=batch.archive_bytes,
                    row_count=len(batch.candles),
                    status="imported",
                    error=None,
                    imported_at=now,
                )
                await resolve_crypto_data_gap(
                    session,
                    stream=stream,
                    symbol_id=symbol_ids[symbol],
                    expected_cursor=trading_date.isoformat(),
                    resolved_at=now,
                )
            imported += 1

    status = "success" if failed == 0 else "partial"
    return BinanceBackfillResult(
        status=status,
        files_imported=imported,
        files_skipped=skipped,
        files_failed=failed,
        candles_written=candles_written,
    )


async def backfill_binance_klines(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    *,
    start_date: date,
    end_date: date,
    archive_client: BinanceArchiveGateway | None = None,
) -> BinanceBackfillResult:
    settings = settings or get_settings()
    if not settings.binance_enabled:
        return BinanceBackfillResult(status="configuration_disabled")
    if start_date > end_date:
        raise ValueError("Binance archive start_date must not exceed end_date")
    if end_date >= datetime.now(UTC).date():
        raise ValueError("Binance archive end_date must be before today UTC")
    if (end_date - start_date).days + 1 > settings.binance_archive_lookback_days:
        raise ValueError("Binance archive date range exceeds configured lookback")

    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        if control.halted_reason is not None:
            return BinanceBackfillResult(status="venue_halted")
        if not control.enabled or control.mode not in _BACKFILL_MODES:
            return BinanceBackfillResult(status="venue_inactive")

        if archive_client is not None:
            return await _run_backfill(
                settings,
                sessions,
                archive_client,
                start_date=start_date,
                end_date=end_date,
            )
        async with BinanceArchiveClient(
            base_url=settings.binance_archive_base_url,
            timeout_seconds=settings.binance_archive_timeout_seconds,
            max_archive_bytes=settings.binance_archive_max_bytes,
            max_uncompressed_bytes=settings.binance_archive_max_uncompressed_bytes,
            retries=settings.binance_market_data_retries,
        ) as client:
            return await _run_backfill(
                settings,
                sessions,
                client,
                start_date=start_date,
                end_date=end_date,
            )


def main() -> None:
    settings = get_settings()
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    default_start = yesterday - timedelta(days=settings.binance_archive_lookback_days - 1)
    parser = argparse.ArgumentParser(description="Backfill verified Binance Spot klines")
    parser.add_argument("--start-date", type=date.fromisoformat, default=default_start)
    parser.add_argument("--end-date", type=date.fromisoformat, default=yesterday)
    arguments = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    result = asyncio.run(
        backfill_binance_klines(
            settings,
            start_date=arguments.start_date,
            end_date=arguments.end_date,
        )
    )
    logger.info("Binance archive backfill complete: %s", result)
    if result.files_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
