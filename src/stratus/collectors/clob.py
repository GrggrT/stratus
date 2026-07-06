from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


class ClobError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OrderLevel:
    price: Decimal
    size: Decimal


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    token_id: str
    ts: datetime
    midpoint: Decimal
    best_bid: Decimal
    best_ask: Decimal
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal


def parse_price_snapshot(
    book: dict[str, Any],
    *,
    collected_at: datetime,
    depth_range: Decimal = Decimal("0.01"),
) -> PriceSnapshot | None:
    token_id = book.get("asset_id")
    raw_timestamp = book.get("timestamp")
    if not isinstance(token_id, str) or not token_id or raw_timestamp is None:
        raise ClobError("order book is missing asset_id or timestamp")
    bids = _parse_levels(book.get("bids"), "bids")
    asks = _parse_levels(book.get("asks"), "asks")
    if not bids or not asks:
        return None

    best_bid = max(level.price for level in bids)
    best_ask = min(level.price for level in asks)
    if best_bid >= best_ask:
        raise ClobError(f"crossed order book for token {token_id}: {best_bid} >= {best_ask}")
    try:
        timestamp_value = int(str(raw_timestamp))
        divisor = 1000 if timestamp_value > 10_000_000_000 else 1
        datetime.fromtimestamp(timestamp_value / divisor, tz=UTC)
    except (ValueError, TypeError, OSError) as exc:
        raise ClobError(f"invalid order book timestamp for token {token_id}") from exc

    bid_depth = sum(
        (
            (Decimal(1) - level.price) * level.size
            for level in bids
            if level.price >= best_bid - depth_range
        ),
        start=Decimal(0),
    )
    ask_depth = sum(
        (level.price * level.size for level in asks if level.price <= best_ask + depth_range),
        start=Decimal(0),
    )
    return PriceSnapshot(
        token_id=token_id,
        ts=collected_at,
        midpoint=((best_bid + best_ask) / Decimal(2)).quantize(Decimal("0.000001")),
        best_bid=best_bid,
        best_ask=best_ask,
        bid_depth_usd=bid_depth.quantize(Decimal("0.0001")),
        ask_depth_usd=ask_depth.quantize(Decimal("0.0001")),
    )


def _parse_levels(value: Any, field: str) -> list[OrderLevel]:
    if not isinstance(value, list):
        raise ClobError(f"order book is missing {field}")
    levels: list[OrderLevel] = []
    for item in value:
        if not isinstance(item, dict):
            raise ClobError(f"order book {field} contains a non-object level")
        try:
            price = Decimal(str(item["price"]))
            size = Decimal(str(item["size"]))
        except (KeyError, InvalidOperation) as exc:
            raise ClobError(f"order book {field} contains an invalid level") from exc
        if not Decimal(0) < price < Decimal(1) or size <= 0:
            raise ClobError(f"order book {field} contains an out-of-range level")
        levels.append(OrderLevel(price=price, size=size))
    return levels


class ClobClient:
    def __init__(
        self,
        *,
        books_url: str,
        batch_size: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.books_url = books_url
        self.batch_size = batch_size
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> ClobClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_books(self, token_ids: list[str]) -> list[dict[str, Any]]:
        if self._client is None:
            raise RuntimeError("ClobClient must be used as an async context manager")
        books: list[dict[str, Any]] = []
        for start in range(0, len(token_ids), self.batch_size):
            books.extend(await self._fetch_batch(token_ids[start : start + self.batch_size]))
        return books

    async def _fetch_batch(self, token_ids: list[str]) -> list[dict[str, Any]]:
        assert self._client is not None
        body = [{"token_id": token_id} for token_id in token_ids]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(self.books_url, json=body)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list) or not all(
                    isinstance(item, dict) for item in payload
                ):
                    raise ClobError("CLOB returned an invalid books payload")
                return payload
            except (httpx.HTTPError, ValueError, ClobError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise ClobError(f"CLOB books request failed after 3 attempts: {last_error}") from last_error
