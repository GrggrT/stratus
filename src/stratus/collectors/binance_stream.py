from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from binance_common.configuration import ConfigurationWebSocketStreams
from binance_common.errors import Error as SdkError
from binance_sdk_spot.spot import Spot
from pydantic import BaseModel

from stratus.collectors.binance_spot import (
    BinanceBookTickerData,
    BinanceGatewayError,
    BinancePayloadError,
    parse_book_ticker,
)


class BinanceBookTickerStream(Protocol):
    def messages(
        self,
        symbols: tuple[str, ...],
        *,
        stop_event: asyncio.Event,
        stale_after: timedelta,
    ) -> AsyncGenerator[BinanceBookTickerData, None]: ...


def _message_payload(value: object) -> dict[str, object]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise BinancePayloadError("invalid Binance WebSocket payload", kind="payload")


class BinanceSdkBookTickerStream:
    """Bounded async stream around the callback-based official Binance SDK."""

    def __init__(self, *, stream_url: str, queue_size: int = 5000) -> None:
        self._client = Spot(
            config_ws_streams=ConfigurationWebSocketStreams(
                stream_url=stream_url.rstrip("/"),
                reconnect_delay=1000,
                compression=False,
            )
        )
        self._queue_size = queue_size

    async def messages(
        self,
        symbols: tuple[str, ...],
        *,
        stop_event: asyncio.Event,
        stale_after: timedelta,
    ) -> AsyncGenerator[BinanceBookTickerData, None]:
        if not symbols:
            raise BinancePayloadError(
                "bookTicker requires at least one symbol", kind="configuration"
            )
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[BinanceBookTickerData | BinanceGatewayError] = asyncio.Queue(
            maxsize=self._queue_size
        )
        expected = set(symbols)
        handles: list[Any] = []
        connection: Any = None

        def enqueue(value: BinanceBookTickerData | BinanceGatewayError) -> None:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                value = BinanceGatewayError(
                    "Binance WebSocket consumer queue overflow",
                    kind="queue_overflow",
                )
            queue.put_nowait(value)

        def on_message(value: object) -> None:
            try:
                parsed = parse_book_ticker(
                    _message_payload(value),
                    received_at=datetime.now(UTC),
                    source="binance_ws",
                )
                if parsed.symbol not in expected:
                    raise BinancePayloadError(
                        f"unexpected Binance WebSocket symbol: {parsed.symbol}",
                        kind="payload",
                    )
                loop.call_soon_threadsafe(enqueue, parsed)
            except BinanceGatewayError as exc:
                loop.call_soon_threadsafe(enqueue, exc)
            except Exception as exc:
                loop.call_soon_threadsafe(
                    enqueue,
                    BinanceGatewayError(str(exc), kind="stream_callback"),
                )

        try:
            try:
                connection = await self._client.websocket_streams.create_connection()  # type: ignore[no-untyped-call]
                if connection is None:
                    raise BinanceGatewayError(
                        "Binance SDK returned no WebSocket connection",
                        kind="stream_connect",
                    )
                for symbol in symbols:
                    handle = await connection.book_ticker(symbol=symbol.lower())
                    handle.on("message", on_message)
                    handles.append(handle)
            except SdkError as exc:
                raise BinanceGatewayError(str(exc), kind="stream_connect") from exc
            except Exception as exc:
                raise BinanceGatewayError(str(exc), kind="stream_connect") from exc

            stale_seconds = max(stale_after.total_seconds(), 0.1)
            startup_seconds = max(10.0, stale_seconds * 3)
            started_at = loop.time()
            last_by_symbol: dict[str, float] = {}
            while not stop_event.is_set():
                now = loop.time()
                if len(last_by_symbol) < len(expected):
                    timeout = startup_seconds - (now - started_at)
                    if timeout <= 0:
                        missing = sorted(expected.difference(last_by_symbol))
                        raise BinanceGatewayError(
                            f"Binance WebSocket startup stale; missing={missing}",
                            kind="stale_stream",
                        )
                else:
                    stale = sorted(
                        symbol
                        for symbol, received_at in last_by_symbol.items()
                        if now - received_at > stale_seconds
                    )
                    if stale:
                        raise BinanceGatewayError(
                            f"Binance WebSocket stale symbols: {stale}",
                            kind="stale_stream",
                        )
                    timeout = min(
                        stale_seconds - max(now - value for value in last_by_symbol.values()),
                        1.0,
                    )
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=max(timeout, 0.05))
                except TimeoutError:
                    continue
                if isinstance(item, BinanceGatewayError):
                    raise item
                last_by_symbol[item.symbol] = loop.time()
                yield item
        finally:
            if handles:
                await asyncio.gather(
                    *(handle.unsubscribe() for handle in handles),
                    return_exceptions=True,
                )
            try:
                if connection is not None:
                    await connection.close_connection(close_session=True)
                else:
                    await self._client.websocket_streams.close_connection(close_session=True)
            except Exception:
                pass
