from __future__ import annotations

import asyncio
import logging
import random
import signal
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.binance_spot import (
    BinanceBookTickerData,
    BinanceGatewayError,
    BinancePayloadError,
)
from stratus.collectors.binance_stream import (
    BinanceBookTickerStream,
    BinanceSdkBookTickerStream,
)
from stratus.config import Settings, get_settings
from stratus.db.models import TradingVenue, VenueMode
from stratus.db.session import session_factory_scope
from stratus.jobs.collect_binance_market_data import collect_binance_market_data
from stratus.repositories.crypto_market_data import (
    crypto_symbol_ids,
    insert_crypto_market_snapshots,
    latest_book_ticker_update_ids,
    open_crypto_data_gap,
    resolve_crypto_data_gaps,
)
from stratus.repositories.venue_runtime_control import (
    get_venue_runtime_control,
    set_venue_kill_switch,
)

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]
_STREAM_MODES = frozenset({VenueMode.SHADOW, VenueMode.TESTNET, VenueMode.LIVE})


@dataclass(frozen=True, slots=True)
class BinanceStreamResult:
    status: str
    snapshots_written: int = 0
    duplicates_skipped: int = 0
    sequence_regressions: int = 0


async def _venue_stream_active(sessions: SessionFactory) -> bool:
    async with sessions() as session:
        control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
    return control.enabled and control.mode in _STREAM_MODES and control.halted_reason is None


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=seconds)
    except TimeoutError:
        return


async def _record_stream_failure(
    sessions: SessionFactory,
    error: BinanceGatewayError,
    *,
    attempt: int,
) -> None:
    async with sessions.begin() as session:
        await open_crypto_data_gap(
            session,
            stream="ws_stream",
            detected_at=datetime.now(UTC),
            details={
                "kind": error.kind,
                "attempt": attempt,
                "error": str(error)[:1000],
            },
        )
        await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)


async def consume_binance_market_stream(
    settings: Settings,
    sessions: SessionFactory,
    stream: BinanceBookTickerStream,
    *,
    stop_event: asyncio.Event,
    flush_interval: float = 0.5,
) -> BinanceStreamResult:
    async with sessions() as session:
        symbol_ids = await crypto_symbol_ids(session, settings.binance_symbols)
        last_update_ids = await latest_book_ticker_update_ids(session, symbol_ids)
    if set(symbol_ids) != set(settings.binance_symbols):
        raise BinancePayloadError(
            "Binance stream cannot start before exchangeInfo bootstrap",
            kind="database_state",
        )

    pending: dict[str, BinanceBookTickerData] = {}
    written = 0
    duplicates = 0
    regressions = 0
    loop = asyncio.get_running_loop()
    last_flush = loop.time()
    last_control_check = loop.time()

    async def flush() -> None:
        nonlocal written, last_flush
        if not pending:
            last_flush = loop.time()
            return
        values = list(pending.values())
        pending.clear()
        async with sessions.begin() as session:
            written += await insert_crypto_market_snapshots(session, values, symbol_ids)
            await resolve_crypto_data_gaps(
                session,
                streams=("ws_stream",),
                resolved_at=datetime.now(UTC),
            )
        last_flush = loop.time()

    messages = stream.messages(
        settings.binance_symbols,
        stop_event=stop_event,
        stale_after=timedelta(milliseconds=settings.binance_market_data_stale_ms),
    )
    async with aclosing(messages):
        async for item in messages:
            previous = last_update_ids.get(item.symbol)
            if previous is not None and item.update_id == previous:
                duplicates += 1
                continue
            if previous is not None and item.update_id < previous:
                regressions += 1
                async with sessions.begin() as session:
                    await open_crypto_data_gap(
                        session,
                        symbol_id=symbol_ids[item.symbol],
                        stream="book_ticker_sequence",
                        detected_at=datetime.now(UTC),
                        expected_cursor=f">={previous}",
                        actual_cursor=str(item.update_id),
                        details={"symbol": item.symbol},
                    )
                    await set_venue_kill_switch(session, TradingVenue.BINANCE_SPOT, True)
                continue
            last_update_ids[item.symbol] = item.update_id
            pending[item.symbol] = item

            now = loop.time()
            if now - last_flush >= flush_interval:
                await flush()
            if now - last_control_check >= 5:
                last_control_check = now
                if not await _venue_stream_active(sessions):
                    break

    await flush()
    if not stop_event.is_set() and await _venue_stream_active(sessions):
        raise BinanceGatewayError(
            "Binance WebSocket stream ended unexpectedly",
            kind="stream_closed",
        )
    return BinanceStreamResult(
        status="stopped" if stop_event.is_set() else "venue_inactive",
        snapshots_written=written,
        duplicates_skipped=duplicates,
        sequence_regressions=regressions,
    )


async def run_binance_market_stream(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
    *,
    stop_event: asyncio.Event | None = None,
    stream: BinanceBookTickerStream | None = None,
) -> None:
    settings = settings or get_settings()
    if not settings.binance_enabled:
        logger.info("Binance market stream disabled by configuration")
        return
    stopped = stop_event or asyncio.Event()

    async with session_factory_scope(settings, session_factory) as sessions:
        attempt = 0
        while not stopped.is_set():
            if not await _venue_stream_active(sessions):
                await _wait_or_stop(stopped, 5)
                continue
            try:
                await collect_binance_market_data(settings, sessions)
                source = stream or BinanceSdkBookTickerStream(stream_url=settings.binance_ws_url)
                await consume_binance_market_stream(
                    settings,
                    sessions,
                    source,
                    stop_event=stopped,
                )
                attempt = 0
            except BinanceGatewayError as exc:
                attempt += 1
                await _record_stream_failure(sessions, exc, attempt=attempt)
                base_delay = min(60.0, float(2 ** min(attempt - 1, 6)))
                delay = base_delay * random.uniform(0.8, 1.2)
                logger.error(
                    "Binance market stream reconnect: attempt=%d delay=%.1fs kind=%s error=%s",
                    attempt,
                    delay,
                    exc.kind,
                    exc,
                )
                await _wait_or_stop(stopped, delay)


async def _run_from_cli(settings: Settings) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop() -> None:
        stopped.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, request_stop)
        except NotImplementedError:
            pass
    await run_binance_market_stream(settings, stop_event=stopped)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(_run_from_cli(settings))


if __name__ == "__main__":
    main()
