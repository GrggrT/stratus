from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_spot import (
    BinanceCandleData,
    BinanceGatewayError,
    BinanceIpBanned,
    BinancePayloadError,
    BinancePublicGateway,
    BinanceSdkPublicGateway,
    candle_sequence_gaps,
    kline_interval_delta,
)
from stratus.config import Settings, get_settings
from stratus.db.models import TradingVenue, VenueMode
from stratus.db.session import session_factory_scope
from stratus.repositories.crypto_market_data import (
    crypto_symbol_ids,
    crypto_symbols_need_refresh,
    insert_crypto_market_snapshots,
    latest_candle_open_times,
    open_crypto_data_gap,
    resolve_crypto_data_gaps,
    upsert_crypto_candles,
    upsert_crypto_symbols,
)
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    halt_venue,
    set_venue_kill_switch,
)

logger = logging.getLogger(__name__)

_COLLECTION_MODES = frozenset({VenueMode.SHADOW, VenueMode.TESTNET, VenueMode.LIVE})


@dataclass(frozen=True, slots=True)
class BinanceMarketDataResult:
    status: str
    symbols_refreshed: int = 0
    candles_written: int = 0
    snapshots_written: int = 0
    gaps_detected: int = 0
    clock_drift_ms: int | None = None


def _clock_midpoint(started_at: datetime, finished_at: datetime) -> datetime:
    return started_at + (finished_at - started_at) / 2


def _historical_candle_gaps(
    candles: list[BinanceCandleData],
    latest_by_symbol: dict[str, datetime],
) -> list[tuple[str, datetime, datetime]]:
    gaps: list[tuple[str, datetime, datetime]] = []
    by_symbol: dict[str, list[BinanceCandleData]] = {}
    for candle in candles:
        by_symbol.setdefault(candle.symbol, []).append(candle)
    for symbol, values in by_symbol.items():
        for expected, actual in candle_sequence_gaps(values):
            gaps.append((symbol, expected, actual))
        previous = latest_by_symbol.get(symbol)
        delta = kline_interval_delta(values[0].interval)
        if previous is None or delta is None:
            continue
        new_times = sorted(value.open_time for value in values if value.open_time > previous)
        if new_times and new_times[0] > previous + delta:
            gaps.append((symbol, previous + delta, new_times[0]))
    return gaps


async def _record_gateway_failure(
    sessions: async_sessionmaker[AsyncSession],
    error: BinanceGatewayError,
) -> None:
    now = datetime.now(UTC)
    stream = "clock_drift" if error.kind == "clock_drift" else "rest_collection"
    async with sessions.begin() as session:
        await open_crypto_data_gap(
            session,
            stream=stream,
            detected_at=now,
            details={
                "kind": error.kind,
                "status_code": error.status_code,
                "error": str(error)[:1000],
            },
        )
        if isinstance(error, BinanceIpBanned):
            await halt_venue(
                session,
                TradingVenue.BINANCE_SPOT,
                f"Binance public API IP ban: {str(error)[:500]}",
            )
        else:
            await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)


async def collect_binance_market_data(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    gateway: BinancePublicGateway | None = None,
) -> BinanceMarketDataResult:
    settings = settings or get_settings()
    if not settings.binance_enabled:
        return BinanceMarketDataResult(status="configuration_disabled")

    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
        if control.halted_reason is not None:
            return BinanceMarketDataResult(status="venue_halted")
        if not control.enabled or control.mode not in _COLLECTION_MODES:
            return BinanceMarketDataResult(status="venue_inactive")

        public_gateway = gateway or BinanceSdkPublicGateway(
            base_url=settings.binance_market_data_rest_url,
            timeout_ms=settings.binance_market_data_timeout_ms,
            retries=settings.binance_market_data_retries,
        )
        try:
            started_at = datetime.now(UTC)
            server_time = await public_gateway.server_time()
            finished_at = datetime.now(UTC)
            local_midpoint = _clock_midpoint(started_at, finished_at)
            clock_drift_ms = round((server_time - local_midpoint).total_seconds() * 1000)
            clock_drift_exceeded = abs(clock_drift_ms) > settings.binance_max_clock_drift_ms
            if clock_drift_exceeded:
                async with sessions.begin() as session:
                    await open_crypto_data_gap(
                        session,
                        stream="clock_drift",
                        detected_at=finished_at,
                        expected_cursor=f"<={settings.binance_max_clock_drift_ms}ms",
                        actual_cursor=f"{clock_drift_ms}ms",
                        details={"source": "binance_server_time"},
                    )
                    await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)

            now = datetime.now(UTC)
            async with sessions() as session:
                refresh_symbols = await crypto_symbols_need_refresh(
                    session,
                    settings.binance_symbols,
                    now=now,
                    max_age=timedelta(minutes=settings.binance_exchange_info_ttl_minutes),
                )

            symbols_refreshed = 0
            if refresh_symbols:
                symbol_info = await public_gateway.exchange_info(settings.binance_symbols)
                invalid_quotes = [
                    item.symbol
                    for item in symbol_info
                    if item.quote_asset != settings.binance_quote_asset
                ]
                if invalid_quotes:
                    raise BinancePayloadError(
                        "configured Binance quote asset mismatch: " + ", ".join(invalid_quotes),
                        kind="payload",
                    )
                async with sessions.begin() as session:
                    symbol_ids = await upsert_crypto_symbols(session, symbol_info)
                symbols_refreshed = len(symbol_info)
            else:
                async with sessions() as session:
                    symbol_ids = await crypto_symbol_ids(session, settings.binance_symbols)

            if set(symbol_ids) != set(settings.binance_symbols):
                raise BinancePayloadError(
                    "not all configured Binance symbols are registered",
                    kind="database_state",
                )

            async with sessions() as session:
                latest_by_symbol = await latest_candle_open_times(
                    session,
                    symbol_ids,
                    interval=settings.binance_kline_interval,
                )

            candles: list[BinanceCandleData] = []
            for symbol in settings.binance_symbols:
                values = await public_gateway.klines(
                    symbol,
                    settings.binance_kline_interval,
                    limit=settings.binance_kline_limit,
                    server_time=server_time,
                )
                if not values:
                    raise BinancePayloadError(
                        f"Binance returned no klines for {symbol}", kind="payload"
                    )
                if any(value.symbol != symbol for value in values):
                    raise BinancePayloadError(
                        f"Binance kline symbol mismatch for {symbol}", kind="payload"
                    )
                candles.extend(values)
            book_tickers = await public_gateway.book_tickers(settings.binance_symbols)
            depth_snapshots = await public_gateway.depth_snapshots(
                settings.binance_symbols,
                limit=settings.binance_depth_limit,
            )
            candle_gaps = _historical_candle_gaps(candles, latest_by_symbol)

            persisted_at = datetime.now(UTC)
            async with sessions.begin() as session:
                candles_written = await upsert_crypto_candles(session, candles, symbol_ids)
                snapshots_written = await insert_crypto_market_snapshots(
                    session, [*book_tickers, *depth_snapshots], symbol_ids
                )
                for symbol, expected, actual in candle_gaps:
                    await open_crypto_data_gap(
                        session,
                        symbol_id=symbol_ids[symbol],
                        stream=f"klines:{settings.binance_kline_interval}",
                        detected_at=persisted_at,
                        expected_cursor=expected.isoformat(),
                        actual_cursor=actual.isoformat(),
                        details={"symbol": symbol, "source": "binance_rest"},
                    )
                await resolve_crypto_data_gaps(
                    session,
                    streams=(
                        ("rest_collection", "clock_drift")
                        if not clock_drift_exceeded
                        else ("rest_collection",)
                    ),
                    resolved_at=persisted_at,
                )

            result = BinanceMarketDataResult(
                status="success",
                symbols_refreshed=symbols_refreshed,
                candles_written=candles_written,
                snapshots_written=snapshots_written,
                gaps_detected=len(candle_gaps) + int(clock_drift_exceeded),
                clock_drift_ms=clock_drift_ms,
            )
            logger.info(
                "Binance market data complete: symbols=%d candles=%d snapshots=%d "
                "gaps=%d drift_ms=%d",
                result.symbols_refreshed,
                result.candles_written,
                result.snapshots_written,
                result.gaps_detected,
                result.clock_drift_ms,
            )
            return result
        except BinanceGatewayError as exc:
            await _record_gateway_failure(sessions, exc)
            logger.error(
                "Binance market data failed closed: kind=%s status=%s error=%s",
                exc.kind,
                exc.status_code,
                exc,
            )
            raise


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(collect_binance_market_data(settings))


if __name__ == "__main__":
    main()
