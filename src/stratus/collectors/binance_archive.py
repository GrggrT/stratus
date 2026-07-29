from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx

from stratus.collectors.binance_spot import (
    BinanceCandleData,
    BinanceGatewayError,
    BinancePayloadError,
    candle_sequence_gaps,
    kline_interval_delta,
    parse_kline,
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_NONNEGATIVE_INTEGER = re.compile(r"^[0-9]+$")
AGG_TRADES_ARCHIVE_KEY = "aggTrades"


class BinanceArchiveError(BinanceGatewayError):
    pass


@dataclass(frozen=True, slots=True)
class BinanceArchiveBatch:
    symbol: str
    interval: str
    trading_date: date
    source_url: str
    checksum_sha256: str
    archive_bytes: int
    candles: list[BinanceCandleData]


@dataclass(frozen=True, slots=True)
class BinanceAggregateTradeData:
    symbol: str
    aggregate_trade_id: int
    price: Decimal
    quantity: Decimal
    first_trade_id: int
    last_trade_id: int
    trade_time: datetime
    buyer_is_maker: bool
    best_price_match: bool
    source: str
    received_at: datetime


@dataclass(frozen=True, slots=True)
class BinanceAggTradesArchiveBatch:
    symbol: str
    trading_date: date
    source_url: str
    checksum_sha256: str
    archive_bytes: int
    trades: list[BinanceAggregateTradeData]


def parse_archive_aggregate_trade_row(
    row: list[str],
    *,
    symbol: str,
    received_at: datetime,
    row_number: int,
) -> BinanceAggregateTradeData:
    if len(row) != 8:
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} must contain exactly 8 fields",
            kind="archive_payload",
        )
    aggregate_trade_id = _archive_integer(row[0], "aggregate_trade_id", row_number)
    price = _archive_decimal(row[1], "price", row_number)
    quantity = _archive_decimal(row[2], "quantity", row_number)
    first_trade_id = _archive_integer(row[3], "first_trade_id", row_number)
    last_trade_id = _archive_integer(row[4], "last_trade_id", row_number)
    trade_time = _archive_timestamp(row[5], row_number)
    buyer_is_maker = _archive_boolean(row[6], "buyer_is_maker", row_number)
    best_price_match = _archive_boolean(row[7], "best_price_match", row_number)
    if price <= 0 or quantity <= 0:
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} price and quantity must be positive",
            kind="archive_payload",
        )
    if first_trade_id > last_trade_id:
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} has an inverted trade ID range",
            kind="archive_payload",
        )
    return BinanceAggregateTradeData(
        symbol=symbol,
        aggregate_trade_id=aggregate_trade_id,
        price=price,
        quantity=quantity,
        first_trade_id=first_trade_id,
        last_trade_id=last_trade_id,
        trade_time=trade_time,
        buyer_is_maker=buyer_is_maker,
        best_price_match=best_price_match,
        source="binance_archive",
        received_at=received_at,
    )


class BinanceArchiveGateway(Protocol):
    async def fetch_daily_klines(
        self,
        symbol: str,
        interval: str,
        trading_date: date,
    ) -> BinanceArchiveBatch: ...


class BinanceAggTradesArchiveGateway(Protocol):
    async def fetch_daily_agg_trades(
        self,
        symbol: str,
        trading_date: date,
    ) -> BinanceAggTradesArchiveBatch: ...


class BinanceArchiveClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int = 60,
        max_archive_bytes: int = 50_000_000,
        max_uncompressed_bytes: int = 200_000_000,
        retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_archive_bytes = max_archive_bytes
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.retries = retries
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> BinanceArchiveClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(float(self.timeout_seconds)),
                follow_redirects=False,
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_daily_klines(
        self,
        symbol: str,
        interval: str,
        trading_date: date,
    ) -> BinanceArchiveBatch:
        if self._client is None:
            raise RuntimeError("BinanceArchiveClient must be used as an async context manager")
        if trading_date >= datetime.now(UTC).date():
            raise BinanceArchiveError(
                "Binance daily archive date must be before today UTC",
                kind="configuration",
            )
        filename = f"{symbol}-{interval}-{trading_date.isoformat()}.zip"
        source_url = f"{self.base_url}/daily/klines/{symbol}/{interval}/{filename}"
        archive, actual_checksum = await self._download_verified_archive(source_url, filename)
        candles = await asyncio.to_thread(
            self._parse_archive,
            archive,
            filename.removesuffix(".zip") + ".csv",
            symbol,
            interval,
            trading_date,
        )
        return BinanceArchiveBatch(
            symbol=symbol,
            interval=interval,
            trading_date=trading_date,
            source_url=source_url,
            checksum_sha256=actual_checksum,
            archive_bytes=len(archive),
            candles=candles,
        )

    async def fetch_daily_agg_trades(
        self,
        symbol: str,
        trading_date: date,
    ) -> BinanceAggTradesArchiveBatch:
        if self._client is None:
            raise RuntimeError("BinanceArchiveClient must be used as an async context manager")
        if trading_date >= datetime.now(UTC).date():
            raise BinanceArchiveError(
                "Binance daily archive date must be before today UTC",
                kind="configuration",
            )
        filename = f"{symbol}-{AGG_TRADES_ARCHIVE_KEY}-{trading_date.isoformat()}.zip"
        source_url = f"{self.base_url}/daily/{AGG_TRADES_ARCHIVE_KEY}/{symbol}/{filename}"
        archive, actual_checksum = await self._download_verified_archive(source_url, filename)
        trades = await asyncio.to_thread(
            self._parse_agg_trades_archive,
            archive,
            filename.removesuffix(".zip") + ".csv",
            symbol,
            trading_date,
        )
        return BinanceAggTradesArchiveBatch(
            symbol=symbol,
            trading_date=trading_date,
            source_url=source_url,
            checksum_sha256=actual_checksum,
            archive_bytes=len(archive),
            trades=trades,
        )

    async def _download_verified_archive(self, source_url: str, filename: str) -> tuple[bytes, str]:
        archive, checksum_payload = await asyncio.gather(
            self._download(source_url, max_bytes=self.max_archive_bytes),
            self._download(source_url + ".CHECKSUM", max_bytes=4096),
        )
        expected_checksum = self._parse_checksum(checksum_payload, filename)
        actual_checksum = hashlib.sha256(archive).hexdigest()
        if actual_checksum != expected_checksum:
            raise BinanceArchiveError(
                "Binance archive checksum mismatch: "
                f"expected={expected_checksum} actual={actual_checksum}",
                kind="checksum",
            )
        return archive, actual_checksum

    async def _download(self, url: str, *, max_bytes: int) -> bytes:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                async with self._client.stream("GET", url) as response:
                    response.raise_for_status()
                    content_length = response.headers.get("content-length")
                    if content_length is not None and int(content_length) > max_bytes:
                        raise BinanceArchiveError(
                            f"Binance archive response exceeds byte limit: {url}",
                            kind="size_limit",
                        )
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > max_bytes:
                            raise BinanceArchiveError(
                                f"Binance archive response exceeds byte limit: {url}",
                                kind="size_limit",
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
            except BinanceArchiveError:
                raise
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if exc.response.status_code == 404:
                    raise BinanceArchiveError(
                        f"Binance archive not found: {url}",
                        kind="not_found",
                        status_code=404,
                    ) from exc
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
            if attempt < self.retries:
                await asyncio.sleep(2**attempt)
        raise BinanceArchiveError(
            f"Binance archive download failed: {url}: {last_error}",
            kind="transport",
        ) from last_error

    @staticmethod
    def _parse_checksum(payload: bytes, filename: str) -> str:
        try:
            line = payload.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise BinanceArchiveError(
                "Binance archive checksum is not ASCII", kind="checksum"
            ) from exc
        parts = line.split()
        if not parts or not _SHA256.fullmatch(parts[0]):
            raise BinanceArchiveError("Binance archive checksum file is invalid", kind="checksum")
        if len(parts) > 1 and parts[-1].lstrip("*") != filename:
            raise BinanceArchiveError("Binance archive checksum filename mismatch", kind="checksum")
        return parts[0].lower()

    def _parse_archive(
        self,
        archive: bytes,
        expected_member: str,
        symbol: str,
        interval: str,
        trading_date: date,
    ) -> list[BinanceCandleData]:
        received_at = datetime.now(UTC)
        server_time = datetime.combine(trading_date + timedelta(days=1), time.min, tzinfo=UTC)
        candles: list[BinanceCandleData] = []
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                member = self._validated_csv_member(bundle, expected_member)
                with (
                    bundle.open(member) as source,
                    io.TextIOWrapper(source, encoding="utf-8", newline="") as csv_source,
                ):
                    for row_number, row in enumerate(csv.reader(csv_source), start=1):
                        if not row:
                            continue
                        try:
                            candle = parse_kline(
                                symbol,
                                interval,
                                row,
                                server_time=server_time,
                                received_at=received_at,
                                source="binance_archive",
                                time_unit="auto",
                            )
                        except BinancePayloadError as exc:
                            raise BinanceArchiveError(
                                f"invalid Binance archive row {row_number}: {exc}",
                                kind="archive_payload",
                            ) from exc
                        if candle.open_time.date() != trading_date:
                            raise BinanceArchiveError(
                                f"Binance archive row {row_number} is outside {trading_date}",
                                kind="archive_payload",
                            )
                        candles.append(candle)
        except BinanceArchiveError:
            raise
        except UnicodeDecodeError as exc:
            raise BinanceArchiveError(
                "Binance archive CSV is not UTF-8", kind="archive_layout"
            ) from exc
        except csv.Error as exc:
            raise BinanceArchiveError("invalid Binance archive CSV", kind="archive_layout") from exc
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise BinanceArchiveError("invalid Binance ZIP archive", kind="archive_layout") from exc
        self._validate_complete_day(candles, interval)
        return candles

    def _parse_agg_trades_archive(
        self,
        archive: bytes,
        expected_member: str,
        symbol: str,
        trading_date: date,
    ) -> list[BinanceAggregateTradeData]:
        day_start = datetime.combine(trading_date, time.min, tzinfo=UTC)
        day_end = day_start + timedelta(days=1)
        received_at = datetime.now(UTC)
        trades: list[BinanceAggregateTradeData] = []
        previous_aggregate_id: int | None = None
        previous_last_trade_id: int | None = None
        previous_time: datetime | None = None
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                member = self._validated_csv_member(bundle, expected_member)
                with (
                    bundle.open(member) as source,
                    io.TextIOWrapper(source, encoding="utf-8", newline="") as csv_source,
                ):
                    for row_number, row in enumerate(csv.reader(csv_source), start=1):
                        if not row:
                            continue
                        trade = self._parse_agg_trade_row(
                            row,
                            symbol=symbol,
                            received_at=received_at,
                            row_number=row_number,
                        )
                        if not day_start <= trade.trade_time < day_end:
                            raise BinanceArchiveError(
                                f"Binance aggTrades row {row_number} is outside {trading_date}",
                                kind="archive_payload",
                            )
                        if previous_aggregate_id is not None:
                            expected_aggregate_id = previous_aggregate_id + 1
                            if trade.aggregate_trade_id != expected_aggregate_id:
                                raise BinanceArchiveError(
                                    "Binance aggTrades aggregate ID discontinuity at row "
                                    f"{row_number}: expected={expected_aggregate_id} "
                                    f"actual={trade.aggregate_trade_id}",
                                    kind="sequence_gap",
                                )
                        if previous_last_trade_id is not None:
                            expected_trade_id = previous_last_trade_id + 1
                            if trade.first_trade_id != expected_trade_id:
                                raise BinanceArchiveError(
                                    "Binance aggTrades underlying trade ID discontinuity at row "
                                    f"{row_number}: expected={expected_trade_id} "
                                    f"actual={trade.first_trade_id}",
                                    kind="sequence_gap",
                                )
                        if previous_time is not None and trade.trade_time < previous_time:
                            raise BinanceArchiveError(
                                f"Binance aggTrades timestamp regression at row {row_number}",
                                kind="sequence_gap",
                            )
                        trades.append(trade)
                        previous_aggregate_id = trade.aggregate_trade_id
                        previous_last_trade_id = trade.last_trade_id
                        previous_time = trade.trade_time
        except BinanceArchiveError:
            raise
        except UnicodeDecodeError as exc:
            raise BinanceArchiveError(
                "Binance archive CSV is not UTF-8", kind="archive_layout"
            ) from exc
        except csv.Error as exc:
            raise BinanceArchiveError("invalid Binance archive CSV", kind="archive_layout") from exc
        except (zipfile.BadZipFile, RuntimeError) as exc:
            raise BinanceArchiveError("invalid Binance ZIP archive", kind="archive_layout") from exc
        if not trades:
            raise BinanceArchiveError("Binance aggTrades CSV is empty", kind="archive_payload")
        return trades

    @staticmethod
    def _parse_agg_trade_row(
        row: list[str],
        *,
        symbol: str,
        received_at: datetime,
        row_number: int,
    ) -> BinanceAggregateTradeData:
        return parse_archive_aggregate_trade_row(
            row,
            symbol=symbol,
            received_at=received_at,
            row_number=row_number,
        )

    def _validated_csv_member(
        self, bundle: zipfile.ZipFile, expected_member: str
    ) -> zipfile.ZipInfo:
        members = [item for item in bundle.infolist() if not item.is_dir()]
        if len(members) != 1 or members[0].filename != expected_member:
            raise BinanceArchiveError(
                "Binance archive must contain exactly the expected CSV",
                kind="archive_layout",
            )
        member = members[0]
        if member.flag_bits & 0x1:
            raise BinanceArchiveError(
                "encrypted Binance archives are not accepted",
                kind="archive_layout",
            )
        if member.file_size > self.max_uncompressed_bytes:
            raise BinanceArchiveError(
                "Binance archive exceeds uncompressed byte limit",
                kind="size_limit",
            )
        return member

    @staticmethod
    def _validate_complete_day(candles: list[BinanceCandleData], interval: str) -> None:
        if not candles:
            raise BinanceArchiveError("Binance archive CSV is empty", kind="archive_payload")
        open_times = [candle.open_time for candle in candles]
        if len(set(open_times)) != len(open_times):
            raise BinanceArchiveError(
                "Binance archive contains duplicate candles", kind="archive_payload"
            )
        gaps = candle_sequence_gaps(candles)
        if gaps:
            raise BinanceArchiveError(
                f"Binance archive has candle gaps: {gaps[:3]}",
                kind="archive_payload",
            )
        delta = kline_interval_delta(interval)
        if delta is None or delta > timedelta(days=1):
            return
        interval_seconds = int(delta.total_seconds())
        if 86_400 % interval_seconds:
            return
        expected_rows = 86_400 // interval_seconds
        if len(candles) != expected_rows:
            raise BinanceArchiveError(
                f"Binance daily archive row count mismatch: "
                f"expected={expected_rows} actual={len(candles)}",
                kind="archive_payload",
            )


def _archive_integer(value: str, field: str, row_number: int) -> int:
    if not _NONNEGATIVE_INTEGER.fullmatch(value):
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} has invalid {field}",
            kind="archive_payload",
        )
    return int(value)


def _archive_decimal(value: str, field: str, row_number: int) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} has invalid {field}",
            kind="archive_payload",
        ) from exc
    if not parsed.is_finite():
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} has non-finite {field}",
            kind="archive_payload",
        )
    return parsed


def _archive_boolean(value: str, field: str, row_number: int) -> bool:
    normalized = value.lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise BinanceArchiveError(
        f"Binance aggTrades row {row_number} has invalid {field}",
        kind="archive_payload",
    )


def _archive_timestamp(value: str, row_number: int) -> datetime:
    timestamp = _archive_integer(value, "timestamp", row_number)
    divisor = 1_000_000 if timestamp >= 100_000_000_000_000 else 1000
    seconds, remainder = divmod(timestamp, divisor)
    microseconds = remainder if divisor == 1_000_000 else remainder * 1000
    try:
        return datetime.fromtimestamp(seconds, UTC) + timedelta(microseconds=microseconds)
    except (OverflowError, OSError, ValueError) as exc:
        raise BinanceArchiveError(
            f"Binance aggTrades row {row_number} has invalid timestamp",
            kind="archive_payload",
        ) from exc
