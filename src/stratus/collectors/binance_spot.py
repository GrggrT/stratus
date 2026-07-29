from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, TypeVar, cast

from binance_common.configuration import ConfigurationRestAPI
from binance_common.errors import (
    Error as SdkError,
)
from binance_common.errors import (
    RateLimitBanError as SdkRateLimitBanError,
)
from binance_common.errors import (
    TooManyRequestsError as SdkTooManyRequestsError,
)
from binance_sdk_spot.rest_api.models import KlinesIntervalEnum
from binance_sdk_spot.spot import Spot
from pydantic import BaseModel


class BinanceGatewayError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        kind: str = "gateway",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


class BinanceRateLimited(BinanceGatewayError):
    pass


class BinanceIpBanned(BinanceGatewayError):
    pass


class BinancePayloadError(BinanceGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceSymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    spot_trading_allowed: bool
    price_tick: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    maximum_notional: Decimal | None
    supports_oco: bool
    supports_oto: bool
    filters: dict[str, dict[str, Any]]
    filter_hash: str
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class BinanceCandleData:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    base_volume: Decimal
    quote_volume: Decimal
    trade_count: int
    taker_buy_base_volume: Decimal
    taker_buy_quote_volume: Decimal
    closed: bool
    source: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class BinanceBookTickerData:
    symbol: str
    update_id: int
    event_time: datetime
    received_at: datetime
    best_bid: Decimal
    best_bid_quantity: Decimal
    best_ask: Decimal
    best_ask_quantity: Decimal
    spread_bps: Decimal
    bid_depth_quote: Decimal
    ask_depth_quote: Decimal
    imbalance: Decimal
    source: str
    depth_valid: bool = False


class BinancePublicGateway(Protocol):
    async def ping(self) -> None: ...

    async def server_time(self) -> datetime: ...

    async def exchange_info(self, symbols: tuple[str, ...]) -> list[BinanceSymbolInfo]: ...

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
        server_time: datetime,
    ) -> list[BinanceCandleData]: ...

    async def book_tickers(self, symbols: tuple[str, ...]) -> list[BinanceBookTickerData]: ...

    async def depth_snapshots(
        self, symbols: tuple[str, ...], *, limit: int = 20
    ) -> list[BinanceBookTickerData]: ...


_KLINE_INTERVAL_SECONDS = {
    "1s": 1,
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
    "6h": 6 * 60 * 60,
    "8h": 8 * 60 * 60,
    "12h": 12 * 60 * 60,
    "1d": 24 * 60 * 60,
    "3d": 3 * 24 * 60 * 60,
    "1w": 7 * 24 * 60 * 60,
}


def kline_interval_delta(interval: str) -> timedelta | None:
    seconds = _KLINE_INTERVAL_SECONDS.get(interval)
    return timedelta(seconds=seconds) if seconds is not None else None


def candle_sequence_gaps(
    values: Sequence[BinanceCandleData],
) -> list[tuple[datetime, datetime]]:
    if len(values) < 2:
        return []
    interval = values[0].interval
    if any(value.interval != interval for value in values):
        raise BinancePayloadError("mixed intervals in Binance candle sequence", kind="payload")
    delta = kline_interval_delta(interval)
    if delta is None:
        return []
    ordered = sorted(values, key=lambda value: value.open_time)
    gaps: list[tuple[datetime, datetime]] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        expected = previous.open_time + delta
        if current.open_time != expected:
            gaps.append((expected, current.open_time))
    return gaps


def _first(payload: Mapping[str, object], *keys: str) -> object | None:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _required_text(payload: Mapping[str, object], *keys: str) -> str:
    value = _first(payload, *keys)
    if not isinstance(value, str) or not value:
        raise BinancePayloadError(f"missing text field: {'/'.join(keys)}", kind="payload")
    return value


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise BinancePayloadError(f"invalid decimal field: {field}", kind="payload")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BinancePayloadError(f"invalid decimal field: {field}", kind="payload") from exc
    if not parsed.is_finite():
        raise BinancePayloadError(f"non-finite decimal field: {field}", kind="payload")
    return parsed


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise BinancePayloadError(f"invalid integer field: {field}", kind="payload")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise BinancePayloadError(f"invalid integer field: {field}", kind="payload") from exc
    return parsed


TimestampUnit = Literal["milliseconds", "microseconds", "auto"]


def _timestamp_value(value: object, field: str, *, time_unit: TimestampUnit) -> datetime:
    timestamp = _integer(value, field)
    if timestamp < 0:
        raise BinancePayloadError(f"negative timestamp field: {field}", kind="payload")
    divisor = 1000
    if time_unit == "microseconds" or (time_unit == "auto" and timestamp >= 100_000_000_000_000):
        divisor = 1_000_000
    seconds, remainder = divmod(timestamp, divisor)
    microseconds = remainder if divisor == 1_000_000 else remainder * 1000
    try:
        return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=microseconds)
    except (OverflowError, OSError, ValueError) as exc:
        raise BinancePayloadError(f"invalid timestamp field: {field}", kind="payload") from exc


def _timestamp_ms(value: object, field: str) -> datetime:
    return _timestamp_value(value, field, time_unit="milliseconds")


def _as_mapping(value: object, field: str) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    raise BinancePayloadError(f"invalid object field: {field}", kind="payload")


def _as_sequence(value: object, field: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise BinancePayloadError(f"invalid list field: {field}", kind="payload")
    return list(value)


def _filter_payload(value: object) -> dict[str, Any]:
    payload = _as_mapping(value, "filters[]")
    actual = payload.get("actual_instance")
    if actual is not None:
        payload = _as_mapping(actual, "filters[].actual_instance")
    payload.pop("additional_properties", None)
    return {key: item for key, item in payload.items() if item is not None}


def _text_list(value: object) -> list[str]:
    if value is None:
        return []
    return [item for item in _as_sequence(value, "symbol metadata") if isinstance(item, str)]


def parse_symbol_info(payload: Mapping[str, object], *, fetched_at: datetime) -> BinanceSymbolInfo:
    raw_filters = _as_sequence(_first(payload, "filters"), "filters")
    filters: dict[str, dict[str, Any]] = {}
    for raw_filter in raw_filters:
        item = _filter_payload(raw_filter)
        filter_type = _required_text(item, "filter_type", "filterType")
        if filter_type in filters:
            raise BinancePayloadError(f"duplicate Binance filter: {filter_type}", kind="payload")
        filters[filter_type] = item

    try:
        price_filter = filters["PRICE_FILTER"]
        lot_size = filters["LOT_SIZE"]
    except KeyError as exc:
        raise BinancePayloadError(
            f"missing critical Binance filter: {exc.args[0]}", kind="payload"
        ) from exc
    notional = filters.get("NOTIONAL") or filters.get("MIN_NOTIONAL")
    if notional is None:
        raise BinancePayloadError("missing critical Binance notional filter", kind="payload")

    price_tick = _decimal(_first(price_filter, "tick_size", "tickSize"), "tickSize")
    quantity_step = _decimal(_first(lot_size, "step_size", "stepSize"), "stepSize")
    minimum_quantity = _decimal(_first(lot_size, "min_qty", "minQty"), "minQty")
    minimum_notional = _decimal(_first(notional, "min_notional", "minNotional"), "minNotional")
    raw_maximum_notional = _first(notional, "max_notional", "maxNotional")
    maximum_notional = (
        _decimal(raw_maximum_notional, "maxNotional") if raw_maximum_notional is not None else None
    )
    if (
        price_tick <= 0
        or quantity_step <= 0
        or minimum_quantity <= 0
        or minimum_notional <= 0
        or (maximum_notional is not None and maximum_notional <= 0)
    ):
        raise BinancePayloadError("Binance filters must be positive", kind="payload")

    filters["_SYMBOL_METADATA"] = {
        "order_types": _text_list(_first(payload, "order_types", "orderTypes")),
        "permissions": _text_list(_first(payload, "permissions")),
        "default_stp_mode": _first(
            payload,
            "default_self_trade_prevention_mode",
            "defaultSelfTradePreventionMode",
        ),
        "allowed_stp_modes": _text_list(
            _first(
                payload,
                "allowed_self_trade_prevention_modes",
                "allowedSelfTradePreventionModes",
            )
        ),
        "iceberg_allowed": _first(payload, "iceberg_allowed", "icebergAllowed") is True,
        "quote_order_qty_market_allowed": _first(
            payload,
            "quote_order_qty_market_allowed",
            "quoteOrderQtyMarketAllowed",
        )
        is True,
        "cancel_replace_allowed": _first(payload, "cancel_replace_allowed", "cancelReplaceAllowed")
        is True,
        "amend_allowed": _first(payload, "amend_allowed", "amendAllowed") is True,
        "trailing_stop_allowed": _first(payload, "allow_trailing_stop", "allowTrailingStop")
        is True,
    }
    raw_rate_limits = _first(payload, "exchange_rate_limits")
    if raw_rate_limits is not None:
        rate_limits = []
        for value in _as_sequence(raw_rate_limits, "exchange_rate_limits"):
            item = _as_mapping(value, "exchange_rate_limits[]")
            item.pop("additional_properties", None)
            rate_limits.append({key: value for key, value in item.items() if value is not None})
        filters["_EXCHANGE_RATE_LIMITS"] = {"limits": rate_limits}

    filter_json = json.dumps(
        filters,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return BinanceSymbolInfo(
        symbol=_required_text(payload, "symbol"),
        base_asset=_required_text(payload, "base_asset", "baseAsset"),
        quote_asset=_required_text(payload, "quote_asset", "quoteAsset"),
        status=_required_text(payload, "status"),
        spot_trading_allowed=(
            _first(payload, "is_spot_trading_allowed", "isSpotTradingAllowed") is True
        ),
        price_tick=price_tick,
        quantity_step=quantity_step,
        minimum_quantity=minimum_quantity,
        minimum_notional=minimum_notional,
        maximum_notional=maximum_notional,
        supports_oco=_first(payload, "oco_allowed", "ocoAllowed") is True,
        supports_oto=_first(payload, "oto_allowed", "otoAllowed") is True,
        filters=filters,
        filter_hash=hashlib.sha256(filter_json.encode("ascii")).hexdigest(),
        fetched_at=fetched_at,
    )


def parse_kline(
    symbol: str,
    interval: str,
    payload: Sequence[object],
    *,
    server_time: datetime,
    received_at: datetime,
    source: str = "binance_rest",
    time_unit: TimestampUnit = "milliseconds",
) -> BinanceCandleData:
    values = _as_sequence(payload, "kline")
    if len(values) < 11:
        raise BinancePayloadError("Binance kline has fewer than 11 fields", kind="payload")
    open_time = _timestamp_value(values[0], "open_time", time_unit=time_unit)
    close_time = _timestamp_value(values[6], "close_time", time_unit=time_unit)
    open_price = _decimal(values[1], "open")
    high_price = _decimal(values[2], "high")
    low_price = _decimal(values[3], "low")
    close_price = _decimal(values[4], "close")
    base_volume = _decimal(values[5], "volume")
    quote_volume = _decimal(values[7], "quote_volume")
    trade_count = _integer(values[8], "trade_count")
    taker_buy_base_volume = _decimal(values[9], "taker_buy_base_volume")
    taker_buy_quote_volume = _decimal(values[10], "taker_buy_quote_volume")
    if close_time < open_time:
        raise BinancePayloadError("Binance kline closes before it opens", kind="payload")
    if min(open_price, high_price, low_price, close_price) <= 0:
        raise BinancePayloadError("Binance kline prices must be positive", kind="payload")
    if low_price > min(open_price, close_price) or high_price < max(open_price, close_price):
        raise BinancePayloadError("Binance kline OHLC bounds are inconsistent", kind="payload")
    if high_price < low_price:
        raise BinancePayloadError("Binance kline high is below low", kind="payload")
    if (
        min(
            base_volume,
            quote_volume,
            taker_buy_base_volume,
            taker_buy_quote_volume,
        )
        < 0
        or trade_count < 0
    ):
        raise BinancePayloadError(
            "Binance kline volumes/count must be non-negative", kind="payload"
        )
    return BinanceCandleData(
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=close_time,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
        base_volume=base_volume,
        quote_volume=quote_volume,
        trade_count=trade_count,
        taker_buy_base_volume=taker_buy_base_volume,
        taker_buy_quote_volume=taker_buy_quote_volume,
        closed=close_time < server_time,
        source=source,
        received_at=received_at,
    )


def parse_book_ticker(
    payload: Mapping[str, object],
    *,
    received_at: datetime,
    source: str = "binance_rest",
) -> BinanceBookTickerData:
    symbol = _required_text(payload, "symbol", "s")
    best_bid = _decimal(_first(payload, "bid_price", "bidPrice", "b"), "bid_price")
    bid_quantity = _decimal(_first(payload, "bid_qty", "bidQty", "B"), "bid_qty")
    best_ask = _decimal(_first(payload, "ask_price", "askPrice", "a"), "ask_price")
    ask_quantity = _decimal(_first(payload, "ask_qty", "askQty", "A"), "ask_qty")
    if best_bid <= 0 or best_ask <= 0 or bid_quantity < 0 or ask_quantity < 0:
        raise BinancePayloadError("Binance book ticker values are invalid", kind="payload")
    if best_ask < best_bid:
        raise BinancePayloadError("Binance book ticker is crossed", kind="payload")

    update_value = _first(payload, "update_id", "u")
    update_id = (
        _integer(update_value, "update_id")
        if update_value is not None
        else int(received_at.timestamp() * 1_000_000)
    )
    if update_id < 0:
        raise BinancePayloadError("Binance book ticker update id is negative", kind="payload")
    event_value = _first(payload, "event_time", "E")
    event_time = (
        _timestamp_ms(event_value, "event_time") if event_value is not None else received_at
    )
    midpoint = (best_bid + best_ask) / 2
    spread_bps = (best_ask - best_bid) / midpoint * Decimal("10000")
    bid_depth = best_bid * bid_quantity
    ask_depth = best_ask * ask_quantity
    total_depth = bid_depth + ask_depth
    imbalance = (bid_depth - ask_depth) / total_depth if total_depth > 0 else Decimal("0")
    return BinanceBookTickerData(
        symbol=symbol,
        update_id=update_id,
        event_time=event_time,
        received_at=received_at,
        best_bid=best_bid,
        best_bid_quantity=bid_quantity,
        best_ask=best_ask,
        best_ask_quantity=ask_quantity,
        spread_bps=spread_bps,
        bid_depth_quote=bid_depth,
        ask_depth_quote=ask_depth,
        imbalance=imbalance,
        source=source,
        depth_valid=False,
    )


def _depth_levels(value: object, side: str) -> list[tuple[Decimal, Decimal]]:
    levels: list[tuple[Decimal, Decimal]] = []
    for index, raw_level in enumerate(_as_sequence(value, side)):
        level = _as_sequence(raw_level, f"{side}[{index}]")
        if len(level) < 2:
            raise BinancePayloadError(f"invalid Binance {side} level", kind="payload")
        price = _decimal(level[0], f"{side}[{index}].price")
        quantity = _decimal(level[1], f"{side}[{index}].quantity")
        if price <= 0 or quantity < 0:
            raise BinancePayloadError(f"invalid Binance {side} level", kind="payload")
        if quantity > 0:
            levels.append((price, quantity))
    if not levels:
        raise BinancePayloadError(f"Binance depth has no {side}", kind="payload")
    return levels


def parse_depth_snapshot(
    symbol: str,
    payload: Mapping[str, object],
    *,
    received_at: datetime,
) -> BinanceBookTickerData:
    update_id = _integer(_first(payload, "last_update_id", "lastUpdateId"), "last_update_id")
    bids = _depth_levels(_first(payload, "bids"), "bids")
    asks = _depth_levels(_first(payload, "asks"), "asks")
    if any(left[0] < right[0] for left, right in zip(bids, bids[1:], strict=False)):
        raise BinancePayloadError("Binance depth bids are not descending", kind="payload")
    if any(left[0] > right[0] for left, right in zip(asks, asks[1:], strict=False)):
        raise BinancePayloadError("Binance depth asks are not ascending", kind="payload")
    best_bid, best_bid_quantity = bids[0]
    best_ask, best_ask_quantity = asks[0]
    if best_ask < best_bid:
        raise BinancePayloadError("Binance depth book is crossed", kind="payload")
    bid_depth = sum((price * quantity for price, quantity in bids), Decimal("0"))
    ask_depth = sum((price * quantity for price, quantity in asks), Decimal("0"))
    total_depth = bid_depth + ask_depth
    midpoint = (best_bid + best_ask) / 2
    return BinanceBookTickerData(
        symbol=symbol,
        update_id=update_id,
        event_time=received_at,
        received_at=received_at,
        best_bid=best_bid,
        best_bid_quantity=best_bid_quantity,
        best_ask=best_ask,
        best_ask_quantity=best_ask_quantity,
        spread_bps=(best_ask - best_bid) / midpoint * Decimal("10000"),
        bid_depth_quote=bid_depth,
        ask_depth_quote=ask_depth,
        imbalance=((bid_depth - ask_depth) / total_depth if total_depth > 0 else Decimal("0")),
        source="binance_depth_rest",
        depth_valid=True,
    )


T = TypeVar("T")


class BinanceSdkPublicGateway:
    """Async boundary around Binance's synchronous generated Spot REST SDK."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_ms: int = 5000,
        retries: int = 2,
    ) -> None:
        configuration = ConfigurationRestAPI(
            base_path=base_url.rstrip("/"),
            timeout=timeout_ms,
            retries=retries,
            backoff=1000,
            keep_alive=True,
            compression=True,
        )
        self._client = Spot(config_rest_api=configuration)

    async def _call(self, operation: Callable[[], T]) -> T:
        try:
            return await asyncio.to_thread(operation)
        except SdkRateLimitBanError as exc:
            raise BinanceIpBanned(
                str(exc), kind="ip_ban", status_code=getattr(exc, "status_code", 418)
            ) from exc
        except SdkTooManyRequestsError as exc:
            raise BinanceRateLimited(
                str(exc),
                kind="rate_limit",
                status_code=getattr(exc, "status_code", 429),
            ) from exc
        except SdkError as exc:
            raise BinanceGatewayError(
                str(exc),
                kind="sdk",
                status_code=getattr(exc, "status_code", None),
            ) from exc
        except Exception as exc:  # SDK can surface transport-library exceptions.
            raise BinanceGatewayError(str(exc), kind="transport") from exc

    async def ping(self) -> None:
        def operation() -> None:
            self._client.rest_api.ping()

        await self._call(operation)

    async def server_time(self) -> datetime:
        def operation() -> object:
            response = self._client.rest_api.time()
            return cast(object, response.data())

        data = await self._call(operation)
        return _timestamp_ms(getattr(data, "server_time", None), "server_time")

    async def exchange_info(self, symbols: tuple[str, ...]) -> list[BinanceSymbolInfo]:
        def operation() -> object:
            response = self._client.rest_api.exchange_info(symbols=list(symbols))
            return cast(object, response.data())

        data = await self._call(operation)
        fetched_at = datetime.now(UTC)
        values = _as_sequence(getattr(data, "symbols", None), "exchange_info.symbols")
        rate_limits = _as_sequence(getattr(data, "rate_limits", None), "exchange_info.rate_limits")
        parsed = []
        for value in values:
            payload = _as_mapping(value, "exchange_info.symbol")
            payload["exchange_rate_limits"] = rate_limits
            parsed.append(parse_symbol_info(payload, fetched_at=fetched_at))
        self._require_symbols(symbols, [item.symbol for item in parsed], "exchangeInfo")
        return parsed

    async def klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int,
        server_time: datetime,
    ) -> list[BinanceCandleData]:
        try:
            interval_value = KlinesIntervalEnum(interval)
        except ValueError as exc:
            raise BinancePayloadError(
                f"unsupported Binance kline interval: {interval}", kind="configuration"
            ) from exc

        def operation() -> object:
            response = self._client.rest_api.klines(
                symbol=symbol,
                interval=interval_value,
                limit=limit,
            )
            return cast(object, response.data())

        data = await self._call(operation)
        received_at = datetime.now(UTC)
        return [
            parse_kline(
                symbol,
                interval,
                _as_sequence(value, "klines[]"),
                server_time=server_time,
                received_at=received_at,
            )
            for value in _as_sequence(data, "klines")
        ]

    async def book_tickers(self, symbols: tuple[str, ...]) -> list[BinanceBookTickerData]:
        def operation() -> object:
            response = self._client.rest_api.ticker_book_ticker(symbols=list(symbols))
            return cast(object, response.data())

        data = await self._call(operation)
        actual = getattr(data, "actual_instance", None)
        values = _as_sequence(actual, "book_ticker")
        received_at = datetime.now(UTC)
        parsed = [
            parse_book_ticker(
                _as_mapping(value, "book_ticker[]"),
                received_at=received_at,
            )
            for value in values
        ]
        self._require_symbols(symbols, [item.symbol for item in parsed], "bookTicker")
        return parsed

    async def depth_snapshots(
        self, symbols: tuple[str, ...], *, limit: int = 20
    ) -> list[BinanceBookTickerData]:
        if limit not in {5, 10, 20, 50, 100, 500, 1000, 5000}:
            raise BinancePayloadError(
                f"unsupported Binance depth limit: {limit}", kind="configuration"
            )
        values: list[BinanceBookTickerData] = []
        for symbol in symbols:

            def operation(symbol_value: str = symbol) -> object:
                response = self._client.rest_api.depth(symbol=symbol_value, limit=limit)
                return cast(object, response.data())

            data = await self._call(operation)
            values.append(
                parse_depth_snapshot(
                    symbol,
                    _as_mapping(data, "depth"),
                    received_at=datetime.now(UTC),
                )
            )
        self._require_symbols(symbols, [item.symbol for item in values], "depth")
        return values

    @staticmethod
    def _require_symbols(expected: tuple[str, ...], actual: list[str], endpoint: str) -> None:
        missing = sorted(set(expected).difference(actual))
        unexpected = sorted(set(actual).difference(expected))
        duplicates = sorted(symbol for symbol in set(actual) if actual.count(symbol) > 1)
        if missing or unexpected or duplicates:
            raise BinancePayloadError(
                f"Binance {endpoint} symbol mismatch; missing={missing} "
                f"unexpected={unexpected} duplicates={duplicates}",
                kind="payload",
            )
