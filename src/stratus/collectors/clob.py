from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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


@dataclass(frozen=True, slots=True)
class StrictBookSnapshot:
    """One independently witnessed CLOB token book.

    Unlike :class:`PriceSnapshot`, this structure retains the exchange timestamp
    and the metadata required to prove that YES and NO were observed as separate
    assets.  A one-sided book is valid evidence, but ``complete`` is false and no
    midpoint is manufactured for it.
    """

    token_id: str
    source_timestamp: str
    source_ts: datetime
    source_hash: str
    tick_size: Decimal
    neg_risk: bool
    payload_sha256: str
    depth_range: Decimal
    bids: tuple[OrderLevel, ...]
    asks: tuple[OrderLevel, ...]
    best_bid: Decimal | None
    best_ask: Decimal | None
    midpoint: Decimal | None
    spread: Decimal | None
    bid_depth_tokens: Decimal
    ask_depth_tokens: Decimal
    bid_depth_usd: Decimal
    ask_depth_usd: Decimal
    total_bid_depth_tokens: Decimal
    total_ask_depth_tokens: Decimal
    total_bid_depth_usd: Decimal
    total_ask_depth_usd: Decimal
    complete: bool


@dataclass(frozen=True, slots=True)
class ClobBooksBatch:
    """Immutable evidence for one successful ``POST /books`` response.

    ``response_body`` is the exact decoded ``httpx.Response.content`` byte
    sequence, captured before JSON parsing.  Header names are normalized to
    lowercase and only evidence/replay-relevant response headers are retained.
    """

    source_url: str
    requested_token_ids: tuple[str, ...]
    request_started_at: datetime
    response_received_at: datetime
    response_body: bytes
    response_sha256: str
    response_bytes: int
    response_headers: tuple[tuple[str, str], ...]
    snapshots: tuple[StrictBookSnapshot, ...]


@dataclass(frozen=True, slots=True)
class BinaryBookPair:
    """A real YES/NO pair; neither side is derived from ``1 - other``."""

    yes_token_id: str
    no_token_id: str
    yes: StrictBookSnapshot
    no: StrictBookSnapshot
    source_skew_seconds: Decimal
    complement_residual: Decimal | None
    binary_yes_probability: Decimal | None
    max_tick_size: Decimal
    eligible: bool
    ineligibility_reasons: tuple[str, ...]


_SELECTED_RESPONSE_HEADERS = (
    "cf-ray",
    "content-encoding",
    "content-length",
    "content-type",
    "date",
    "etag",
    "last-modified",
    "server",
    "x-request-id",
)


async def _single_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


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


def parse_strict_book_snapshot(
    book: dict[str, Any],
    *,
    depth_range: Decimal = Decimal("0.01"),
) -> StrictBookSnapshot:
    """Parse one token book without replacing exchange evidence with local time."""

    token_id = _required_identity(book.get("asset_id"), "asset_id")
    source_timestamp = _required_source_text(book.get("timestamp"), "timestamp", token_id)
    source_ts = _parse_source_timestamp(source_timestamp, token_id)
    try:
        source_hash = _required_identity(book.get("hash"), "source hash")
    except ClobError as exc:
        raise ClobError(f"invalid hash for token {token_id}") from exc
    tick_size = _required_decimal(book.get("tick_size"), "tick_size", token_id)
    if not Decimal(0) < tick_size < Decimal(1):
        raise ClobError(f"invalid tick_size for token {token_id}")
    neg_risk = book.get("neg_risk")
    if not isinstance(neg_risk, bool):
        raise ClobError(f"invalid neg_risk for token {token_id}")
    if depth_range < 0:
        raise ClobError("depth_range must not be negative")

    bids = tuple(_parse_levels(book.get("bids"), "bids"))
    asks = tuple(_parse_levels(book.get("asks"), "asks"))
    best_bid = max((level.price for level in bids), default=None)
    best_ask = min((level.price for level in asks), default=None)
    if best_bid is not None and best_ask is not None and best_bid >= best_ask:
        raise ClobError(f"crossed order book for token {token_id}: {best_bid} >= {best_ask}")

    midpoint = (
        ((best_bid + best_ask) / Decimal(2)).quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
        if best_bid is not None and best_ask is not None
        else None
    )
    spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
    top_bids = (
        tuple(level for level in bids if level.price >= best_bid - depth_range)
        if best_bid is not None
        else ()
    )
    top_asks = (
        tuple(level for level in asks if level.price <= best_ask + depth_range)
        if best_ask is not None
        else ()
    )
    canonical_payload = _canonical_json_bytes(book, token_id)
    return StrictBookSnapshot(
        token_id=token_id,
        source_timestamp=source_timestamp,
        source_ts=source_ts,
        source_hash=source_hash,
        tick_size=tick_size,
        neg_risk=neg_risk,
        payload_sha256=hashlib.sha256(canonical_payload).hexdigest(),
        depth_range=depth_range,
        bids=bids,
        asks=asks,
        best_bid=best_bid,
        best_ask=best_ask,
        midpoint=midpoint,
        spread=spread,
        bid_depth_tokens=_sum_size(top_bids),
        ask_depth_tokens=_sum_size(top_asks),
        bid_depth_usd=_sum_notional(top_bids),
        ask_depth_usd=_sum_notional(top_asks),
        total_bid_depth_tokens=_sum_size(bids),
        total_ask_depth_tokens=_sum_size(asks),
        total_bid_depth_usd=_sum_notional(bids),
        total_ask_depth_usd=_sum_notional(asks),
        complete=best_bid is not None and best_ask is not None,
    )


def parse_binary_book_pair(
    snapshots: Iterable[StrictBookSnapshot],
    *,
    yes_token_id: str,
    no_token_id: str,
    max_source_skew: timedelta = timedelta(seconds=2),
) -> BinaryBookPair:
    """Build a pair from explicit YES and NO tokens and apply evidence gates."""

    yes_token_id = _required_identity(yes_token_id, "yes_token_id")
    no_token_id = _required_identity(no_token_id, "no_token_id")
    if yes_token_id == no_token_id:
        raise ClobError("YES and NO token ids must be distinct")
    if max_source_skew < timedelta(0):
        raise ClobError("max_source_skew must not be negative")

    by_token: dict[str, StrictBookSnapshot] = {}
    for snapshot in snapshots:
        if snapshot.token_id in by_token:
            raise ClobError(f"duplicate parsed book for token {snapshot.token_id}")
        by_token[snapshot.token_id] = snapshot
    missing = [token_id for token_id in (yes_token_id, no_token_id) if token_id not in by_token]
    if missing:
        raise ClobError(f"binary book pair is missing tokens: {missing}")

    yes = by_token[yes_token_id]
    no = by_token[no_token_id]
    skew_delta = abs(yes.source_ts - no.source_ts)
    source_skew_seconds = _timedelta_seconds(skew_delta)
    max_tick_size = max(yes.tick_size, no.tick_size)
    complement_residual: Decimal | None = None
    binary_yes_probability: Decimal | None = None
    reasons: list[str] = []
    if not yes.complete:
        reasons.append("yes_book_incomplete")
    if not no.complete:
        reasons.append("no_book_incomplete")
    if skew_delta > max_source_skew:
        reasons.append("source_skew_exceeds_limit")
    if yes.midpoint is not None and no.midpoint is not None:
        midpoint_sum = yes.midpoint + no.midpoint
        complement_residual = abs(midpoint_sum - Decimal(1))
        if midpoint_sum <= 0:
            reasons.append("invalid_midpoint_sum")
        else:
            binary_yes_probability = yes.midpoint / midpoint_sum
        if complement_residual > max_tick_size:
            reasons.append("complement_residual_exceeds_tick")

    return BinaryBookPair(
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        yes=yes,
        no=no,
        source_skew_seconds=source_skew_seconds,
        complement_residual=complement_residual,
        binary_yes_probability=binary_yes_probability,
        max_tick_size=max_tick_size,
        eligible=not reasons,
        ineligibility_reasons=tuple(reasons),
    )


def _canonical_json_bytes(book: dict[str, Any], token_id: str) -> bytes:
    try:
        return _postgres_jsonb_text(book).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ClobError(f"book for token {token_id} is not canonical JSON") from exc


def _postgres_jsonb_text(value: Any) -> str:
    """Render the deterministic JSONB text used by the evidence DB trigger.

    CLOB book values are strings, booleans, arrays and objects. PostgreSQL
    JSONB orders object keys by UTF-8 byte length and then byte value, and its
    textual form places one space after separators.
    """

    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSON object keys must be strings")
        keys = sorted(value, key=lambda key: (len(key.encode("utf-8")), key.encode("utf-8")))
        return (
            "{"
            + ", ".join(
                f"{json.dumps(key, ensure_ascii=False)}: {_postgres_jsonb_text(value[key])}"
                for key in keys
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _required_identity(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ClobError(f"invalid {field}")
    return value


def _required_source_text(value: Any, field: str, token_id: str) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ClobError(f"invalid {field} for token {token_id}")
    text = str(value)
    if not text or text != text.strip():
        raise ClobError(f"invalid {field} for token {token_id}")
    return text


def _required_decimal(value: Any, field: str, token_id: str) -> Decimal:
    if isinstance(value, bool):
        raise ClobError(f"invalid {field} for token {token_id}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ClobError(f"invalid {field} for token {token_id}") from exc
    if not parsed.is_finite():
        raise ClobError(f"invalid {field} for token {token_id}")
    return parsed


def _parse_source_timestamp(value: str, token_id: str) -> datetime:
    try:
        timestamp_value = Decimal(value)
        if not timestamp_value.is_finite() or timestamp_value < 0:
            raise InvalidOperation
        divisor = Decimal(1000) if timestamp_value > Decimal("10000000000") else Decimal(1)
        seconds = timestamp_value / divisor
        whole_seconds = int(seconds)
        microseconds = int((seconds - whole_seconds) * Decimal(1_000_000))
        return datetime.fromtimestamp(whole_seconds, tz=UTC) + timedelta(microseconds=microseconds)
    except (InvalidOperation, ValueError, TypeError, OSError, OverflowError) as exc:
        raise ClobError(f"invalid order book timestamp for token {token_id}") from exc


def _sum_size(levels: Iterable[OrderLevel]) -> Decimal:
    return sum((level.size for level in levels), start=Decimal(0)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def _sum_notional(levels: Iterable[OrderLevel]) -> Decimal:
    return sum((level.price * level.size for level in levels), start=Decimal(0)).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def _timedelta_seconds(value: timedelta) -> Decimal:
    total_microseconds = (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    return Decimal(total_microseconds) / Decimal(1_000_000)


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
        max_response_bytes: int = 25_000_000,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        self.books_url = books_url
        self.batch_size = batch_size
        self.max_response_bytes = max_response_bytes
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

    async def fetch_book_batch(self, token_ids: list[str]) -> ClobBooksBatch:
        """Fetch one strict batch and reject any token-universe ambiguity."""

        if self._client is None:
            raise RuntimeError("ClobClient must be used as an async context manager")
        requested_token_ids = _validate_requested_token_ids(token_ids)
        if len(requested_token_ids) > self.batch_size:
            raise ClobError(f"strict CLOB batch exceeds configured batch_size {self.batch_size}")
        return await self._fetch_strict_batch(requested_token_ids)

    async def fetch_book_batches(self, token_ids: list[str]) -> tuple[ClobBooksBatch, ...]:
        """Fetch strict chunks while preserving each response as separate evidence."""

        if self._client is None:
            raise RuntimeError("ClobClient must be used as an async context manager")
        requested_token_ids = _validate_requested_token_ids(token_ids)
        batches: list[ClobBooksBatch] = []
        for start in range(0, len(requested_token_ids), self.batch_size):
            batches.append(
                await self._fetch_strict_batch(requested_token_ids[start : start + self.batch_size])
            )
        return tuple(batches)

    async def _fetch_batch(self, token_ids: list[str]) -> list[dict[str, Any]]:
        assert self._client is not None
        body = [{"token_id": token_id} for token_id in token_ids]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                _response, response_body = await self._bounded_post(body)
                payload = json.loads(response_body, parse_constant=_reject_json_constant)
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

    async def _bounded_post(
        self,
        body: list[dict[str, str]],
    ) -> tuple[httpx.Response, bytes]:
        """Stream one response and stop before buffering an unbounded body."""

        assert self._client is not None
        async with self._client.stream(
            "POST",
            self.books_url,
            json=body,
            headers={"Accept-Encoding": "identity"},
        ) as response:
            response.raise_for_status()
            content_encoding = response.headers.get("content-encoding", "identity")
            if content_encoding.strip().lower() not in {"", "identity"}:
                raise ClobError(
                    "CLOB ignored Accept-Encoding: identity; compressed responses "
                    "are rejected before decoding"
                )
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    parsed_length = int(declared_length)
                except ValueError as exc:
                    raise ClobError("CLOB returned an invalid Content-Length") from exc
                if parsed_length < 0 or parsed_length > self.max_response_bytes:
                    raise ClobError(
                        f"CLOB response exceeds configured byte limit {self.max_response_bytes}"
                    )
            response_body = bytearray()
            chunks = (
                _single_chunk(response.content)
                if response.is_stream_consumed
                else response.aiter_raw()
            )
            async for chunk in chunks:
                if len(response_body) + len(chunk) > self.max_response_bytes:
                    raise ClobError(
                        f"CLOB response exceeds configured byte limit {self.max_response_bytes}"
                    )
                response_body.extend(chunk)
        return response, bytes(response_body)

    async def _fetch_strict_batch(
        self,
        token_ids: tuple[str, ...],
    ) -> ClobBooksBatch:
        assert self._client is not None
        body = [{"token_id": token_id} for token_id in token_ids]
        last_error: Exception | None = None
        for attempt in range(3):
            request_started_at = datetime.now(UTC)
            try:
                response, response_body = await self._bounded_post(body)
                response_received_at = datetime.now(UTC)
                payload = json.loads(response_body, parse_constant=_reject_json_constant)
                snapshots = _parse_exact_book_universe(payload, token_ids)
                return ClobBooksBatch(
                    source_url=str(response.request.url),
                    requested_token_ids=token_ids,
                    request_started_at=request_started_at,
                    response_received_at=response_received_at,
                    response_body=response_body,
                    response_sha256=hashlib.sha256(response_body).hexdigest(),
                    response_bytes=len(response_body),
                    response_headers=tuple(
                        (header, response.headers[header])
                        for header in _SELECTED_RESPONSE_HEADERS
                        if header in response.headers
                    ),
                    snapshots=snapshots,
                )
            except (httpx.HTTPError, ValueError, ClobError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise ClobError(
            f"strict CLOB books request failed after 3 attempts: {last_error}"
        ) from last_error


def _validate_requested_token_ids(token_ids: list[str]) -> tuple[str, ...]:
    if not token_ids:
        raise ClobError("strict CLOB batch requires at least one token id")
    validated = tuple(_required_identity(token_id, "requested token id") for token_id in token_ids)
    if len(set(validated)) != len(validated):
        raise ClobError("strict CLOB batch contains duplicate requested token ids")
    return validated


def _parse_exact_book_universe(
    payload: Any,
    requested_token_ids: tuple[str, ...],
) -> tuple[StrictBookSnapshot, ...]:
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ClobError("CLOB returned an invalid books payload")

    books_by_token: dict[str, dict[str, Any]] = {}
    duplicate_ids: set[str] = set()
    for raw_book in payload:
        token_id = _required_identity(raw_book.get("asset_id"), "response asset_id")
        if token_id in books_by_token:
            duplicate_ids.add(token_id)
        books_by_token[token_id] = raw_book
    if duplicate_ids:
        raise ClobError(f"CLOB returned duplicate token ids: {sorted(duplicate_ids)}")

    requested = set(requested_token_ids)
    received = set(books_by_token)
    if requested != received:
        missing = sorted(requested - received)
        extra = sorted(received - requested)
        raise ClobError(f"CLOB token universe mismatch: missing={missing} extra={extra}")
    return tuple(
        parse_strict_book_snapshot(books_by_token[token_id]) for token_id in requested_token_ids
    )


def _reject_json_constant(value: str) -> None:
    raise ClobError(f"CLOB returned non-standard JSON constant {value}")
