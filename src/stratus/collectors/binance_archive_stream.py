from __future__ import annotations

import asyncio
import csv
import ctypes
import hashlib
import io
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Protocol

import anyio
import httpx

from stratus.backtesting.events import (
    AggregateTradeEvent,
    CandleEvent,
    MarketEvent,
)
from stratus.collectors.binance_archive import (
    AGG_TRADES_ARCHIVE_KEY,
    BinanceAggregateTradeData,
    BinanceArchiveError,
    parse_archive_aggregate_trade_row,
)
from stratus.collectors.binance_spot import (
    BinanceCandleData,
    BinancePayloadError,
    candle_sequence_gaps,
    kline_interval_delta,
    parse_kline,
)

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_FILENAME = re.compile(r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    disk_free_bytes: int
    available_memory_bytes: int | None
    available_commit_bytes: int | None


class ResourceProbe(Protocol):
    def __call__(self, path: Path) -> ResourceSnapshot: ...


@dataclass(frozen=True, slots=True)
class CachedArchive:
    path: Path
    source_url: str
    checksum_sha256: str
    archive_bytes: int


@dataclass(frozen=True, slots=True)
class ArchiveDayManifest:
    symbol: str
    interval: str
    trading_date: date
    source_url: str
    checksum_sha256: str
    archive_bytes: int
    row_count: int


class BoundedArchiveCache:
    def __init__(
        self,
        *,
        root: Path,
        cache_max_bytes: int,
        min_free_bytes: int,
        max_archive_bytes: int,
        min_available_memory_bytes: int,
        min_available_commit_bytes: int,
        timeout_seconds: int,
        retries: int,
        resource_wait_seconds: float = 0,
        resource_poll_seconds: float = 5,
        http_client: httpx.AsyncClient | None = None,
        resource_probe: ResourceProbe | None = None,
    ) -> None:
        if cache_max_bytes <= 0 or min_free_bytes <= 0 or max_archive_bytes <= 0:
            raise ValueError("bounded archive byte limits must be positive")
        if cache_max_bytes >= min_free_bytes:
            raise ValueError("bounded cache must remain below the free-disk reserve")
        if min_available_memory_bytes <= 0:
            raise ValueError("bounded archive memory reserve must be positive")
        if min_available_commit_bytes <= 0:
            raise ValueError("bounded archive commit reserve must be positive")
        if timeout_seconds <= 0 or retries < 0:
            raise ValueError("bounded archive transport settings are invalid")
        if resource_wait_seconds < 0 or resource_poll_seconds <= 0:
            raise ValueError("bounded archive resource wait settings are invalid")
        self.root = root
        self.cache_max_bytes = cache_max_bytes
        self.min_free_bytes = min_free_bytes
        self.max_archive_bytes = max_archive_bytes
        self.min_available_memory_bytes = min_available_memory_bytes
        self.min_available_commit_bytes = min_available_commit_bytes
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.resource_wait_seconds = resource_wait_seconds
        self.resource_poll_seconds = resource_poll_seconds
        self._client = http_client
        self._owns_client = http_client is None
        self._resource_probe = resource_probe or system_resource_snapshot
        self._run_dir: Path | None = None
        self._files: dict[Path, int] = {}
        self._used_bytes = 0

    async def __aenter__(self) -> BoundedArchiveCache:
        self._run_dir = await asyncio.to_thread(_create_run_dir, self.root)
        try:
            if self._client is None:
                self._client = httpx.AsyncClient(
                    timeout=httpx.Timeout(float(self.timeout_seconds)),
                    follow_redirects=False,
                    limits=httpx.Limits(max_connections=2),
                )
            await self._require_or_wait()
            return self
        except BaseException:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
            await asyncio.to_thread(shutil.rmtree, self._run_dir, True)
            self._run_dir = None
            raise

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        if self._run_dir is not None:
            await asyncio.to_thread(shutil.rmtree, self._run_dir, True)
        self._run_dir = None
        self._files.clear()
        self._used_bytes = 0

    async def require_resources(self, *, additional_bytes: int = 0) -> None:
        if additional_bytes < 0:
            raise ValueError("additional cache bytes must be non-negative")
        if self._run_dir is None:
            raise RuntimeError("bounded archive cache is not open")
        snapshot = await asyncio.to_thread(self._resource_probe, self._run_dir)
        if self._used_bytes + additional_bytes > self.cache_max_bytes:
            raise BinanceArchiveError(
                "Binance research cache quota would be exceeded",
                kind="resource_limit",
            )
        if snapshot.disk_free_bytes - additional_bytes < self.min_free_bytes:
            raise BinanceArchiveError(
                "Binance research stopped at the free-disk reserve",
                kind="resource_limit",
            )
        if (
            snapshot.available_memory_bytes is not None
            and snapshot.available_memory_bytes < self.min_available_memory_bytes
        ):
            raise BinanceArchiveError(
                "Binance research stopped at the available-memory reserve",
                kind="resource_pressure",
            )
        if (
            snapshot.available_commit_bytes is not None
            and snapshot.available_commit_bytes < self.min_available_commit_bytes
        ):
            raise BinanceArchiveError(
                "Binance research stopped at the available-commit reserve",
                kind="resource_pressure",
            )

    async def wait_for_resources(
        self,
        *,
        max_wait_seconds: float,
        poll_seconds: float,
        additional_bytes: int = 0,
    ) -> None:
        if max_wait_seconds <= 0 or poll_seconds <= 0:
            raise ValueError("resource wait settings must be positive")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_wait_seconds
        while True:
            try:
                await self.require_resources(additional_bytes=additional_bytes)
                return
            except BinanceArchiveError as exc:
                if exc.kind != "resource_pressure":
                    raise
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise
                await asyncio.sleep(min(poll_seconds, remaining))

    async def _require_or_wait(self, *, additional_bytes: int = 0) -> None:
        if self.resource_wait_seconds > 0:
            await self.wait_for_resources(
                max_wait_seconds=self.resource_wait_seconds,
                poll_seconds=self.resource_poll_seconds,
                additional_bytes=additional_bytes,
            )
        else:
            await self.require_resources(additional_bytes=additional_bytes)

    async def fetch(self, source_url: str, filename: str) -> CachedArchive:
        if self._client is None or self._run_dir is None:
            raise RuntimeError("bounded archive cache is not open")
        if not _FILENAME.fullmatch(filename):
            raise ValueError("bounded archive filename is invalid")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            part_path = self._run_dir / f"{filename}.part"
            final_path = self._run_dir / filename
            await asyncio.to_thread(_unlink_if_exists, part_path)
            try:
                expected = await self._fetch_checksum(source_url, filename)
                archive_bytes, actual = await self._download_archive(
                    source_url,
                    part_path,
                )
                if actual != expected:
                    raise BinanceArchiveError(
                        f"Binance archive checksum mismatch: expected={expected} actual={actual}",
                        kind="checksum",
                    )
                await asyncio.to_thread(part_path.replace, final_path)
                self._files[final_path] = archive_bytes
                self._used_bytes += archive_bytes
                return CachedArchive(
                    path=final_path,
                    source_url=source_url,
                    checksum_sha256=actual,
                    archive_bytes=archive_bytes,
                )
            except BinanceArchiveError as exc:
                last_error = exc
                await asyncio.to_thread(_unlink_if_exists, part_path)
                if exc.kind in {
                    "checksum",
                    "not_found",
                    "resource_limit",
                    "resource_pressure",
                    "size_limit",
                }:
                    raise
            except (httpx.HTTPError, OSError, ValueError) as exc:
                last_error = exc
                await asyncio.to_thread(_unlink_if_exists, part_path)
            if attempt < self.retries:
                await asyncio.sleep(2**attempt)
        raise BinanceArchiveError(
            f"Binance bounded archive download failed: {source_url}: {last_error}",
            kind="transport",
        ) from last_error

    async def release(self, archive: CachedArchive) -> None:
        size = self._files.pop(archive.path, None)
        if size is None:
            return
        await asyncio.to_thread(_unlink_if_exists, archive.path)
        self._used_bytes -= size

    async def _fetch_checksum(self, source_url: str, filename: str) -> str:
        assert self._client is not None
        response = await self._client.get(source_url + ".CHECKSUM")
        if response.status_code == 404:
            raise BinanceArchiveError(
                f"Binance archive checksum not found: {source_url}",
                kind="not_found",
                status_code=404,
            )
        response.raise_for_status()
        if len(response.content) > 4096:
            raise BinanceArchiveError(
                "Binance archive checksum response is too large",
                kind="checksum",
            )
        return _parse_checksum(response.content, filename)

    async def _download_archive(self, source_url: str, path: Path) -> tuple[int, str]:
        assert self._client is not None
        hasher = hashlib.sha256()
        total = 0
        async with self._client.stream("GET", source_url) as response:
            if response.status_code == 404:
                raise BinanceArchiveError(
                    f"Binance archive not found: {source_url}",
                    kind="not_found",
                    status_code=404,
                )
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            expected_size = int(content_length) if content_length is not None else 0
            if expected_size > self.max_archive_bytes:
                raise BinanceArchiveError(
                    "Binance research archive exceeds per-file limit",
                    kind="size_limit",
                )
            await self._require_or_wait(additional_bytes=expected_size)
            async with await anyio.open_file(path, "wb") as target:
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > self.max_archive_bytes:
                        raise BinanceArchiveError(
                            "Binance research archive exceeds per-file limit",
                            kind="size_limit",
                        )
                    if self._used_bytes + total > self.cache_max_bytes:
                        raise BinanceArchiveError(
                            "Binance research cache quota would be exceeded",
                            kind="resource_limit",
                        )
                    hasher.update(chunk)
                    await target.write(chunk)
        if total <= 0:
            raise BinanceArchiveError("Binance research archive is empty", kind="transport")
        await self._require_or_wait()
        return total, hasher.hexdigest()


class BinanceArchiveEventSource:
    def __init__(
        self,
        cache: BoundedArchiveCache,
        *,
        base_url: str,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        max_uncompressed_bytes: int,
        resource_check_events: int = 10_000,
        resource_wait_seconds: int = 300,
        resource_poll_seconds: int = 5,
    ) -> None:
        if start_time.tzinfo is None or start_time.utcoffset() is None:
            raise ValueError("archive stream start_time must be timezone-aware")
        if end_time.tzinfo is None or end_time.utcoffset() is None:
            raise ValueError("archive stream end_time must be timezone-aware")
        if start_time >= end_time:
            raise ValueError("archive stream start_time must be before end_time")
        if interval != "5m":
            raise ValueError("bounded Binance research requires 5m candles")
        if max_uncompressed_bytes <= 0:
            raise ValueError("archive stream uncompressed limit must be positive")
        if resource_check_events <= 0:
            raise ValueError("archive stream resource-check interval must be positive")
        if resource_wait_seconds <= 0 or resource_poll_seconds <= 0:
            raise ValueError("archive stream resource wait settings must be positive")
        self.cache = cache
        self.base_url = base_url.rstrip("/")
        self.symbol = symbol
        self.interval = interval
        self.start_time = start_time
        self.end_time = end_time
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.resource_check_events = resource_check_events
        self.resource_wait_seconds = resource_wait_seconds
        self.resource_poll_seconds = resource_poll_seconds
        self._manifests: list[ArchiveDayManifest] = []
        self._has_iterated = False

    @property
    def manifests(self) -> tuple[ArchiveDayManifest, ...]:
        return tuple(self._manifests)

    async def events(self) -> AsyncIterator[MarketEvent]:
        if self._has_iterated:
            raise RuntimeError("a BinanceArchiveEventSource can only be consumed once")
        self._has_iterated = True
        sequence = _TradeSequenceState()
        for trading_date in _included_dates(self.start_time, self.end_time):
            await self._wait_for_resources()
            kline_name = f"{self.symbol}-{self.interval}-{trading_date.isoformat()}.zip"
            trade_name = f"{self.symbol}-{AGG_TRADES_ARCHIVE_KEY}-{trading_date.isoformat()}.zip"
            kline_url = f"{self.base_url}/daily/klines/{self.symbol}/{self.interval}/{kline_name}"
            trade_url = f"{self.base_url}/daily/{AGG_TRADES_ARCHIVE_KEY}/{self.symbol}/{trade_name}"
            kline_archive = await self.cache.fetch(kline_url, kline_name)
            trade_archive: CachedArchive | None = None
            try:
                trade_archive = await self.cache.fetch(trade_url, trade_name)
                candles = await asyncio.to_thread(
                    _parse_candles,
                    kline_archive.path,
                    kline_name.removesuffix(".zip") + ".csv",
                    self.symbol,
                    self.interval,
                    trading_date,
                    self.max_uncompressed_bytes,
                )
                candle_index = 0
                day_trade_count = 0
                received_at = datetime.now(UTC)
                for trade in _iter_aggregate_trades(
                    trade_archive.path,
                    trade_name.removesuffix(".zip") + ".csv",
                    symbol=self.symbol,
                    trading_date=trading_date,
                    received_at=received_at,
                    max_uncompressed_bytes=self.max_uncompressed_bytes,
                    sequence=sequence,
                ):
                    day_trade_count += 1
                    if day_trade_count % self.resource_check_events == 0:
                        await self._wait_for_resources()
                    trade_event = _trade_event(trade)
                    while (
                        candle_index < len(candles)
                        and candles[candle_index].close_time < trade_event.trade_time
                    ):
                        candle_event = _candle_event(candles[candle_index])
                        if self._included(candle_event.event_time):
                            yield candle_event
                        candle_index += 1
                    if self._included(trade_event.event_time):
                        yield trade_event
                while candle_index < len(candles):
                    candle_event = _candle_event(candles[candle_index])
                    if self._included(candle_event.event_time):
                        yield candle_event
                    candle_index += 1
                if day_trade_count <= 0:
                    raise BinanceArchiveError(
                        "Binance aggTrades CSV is empty",
                        kind="archive_payload",
                    )
                self._manifests.extend(
                    (
                        _manifest(
                            kline_archive,
                            symbol=self.symbol,
                            interval=self.interval,
                            trading_date=trading_date,
                            row_count=len(candles),
                        ),
                        _manifest(
                            trade_archive,
                            symbol=self.symbol,
                            interval=AGG_TRADES_ARCHIVE_KEY,
                            trading_date=trading_date,
                            row_count=day_trade_count,
                        ),
                    )
                )
            finally:
                if trade_archive is not None:
                    await self.cache.release(trade_archive)
                await self.cache.release(kline_archive)

    def _included(self, event_time: datetime) -> bool:
        return self.start_time <= event_time < self.end_time

    async def _wait_for_resources(self) -> None:
        await self.cache.wait_for_resources(
            max_wait_seconds=self.resource_wait_seconds,
            poll_seconds=self.resource_poll_seconds,
        )


@dataclass(slots=True)
class _TradeSequenceState:
    aggregate_trade_id: int | None = None
    last_trade_id: int | None = None
    trade_time: datetime | None = None


def _iter_aggregate_trades(
    archive_path: Path,
    expected_member: str,
    *,
    symbol: str,
    trading_date: date,
    received_at: datetime,
    max_uncompressed_bytes: int,
    sequence: _TradeSequenceState,
) -> Iterator[BinanceAggregateTradeData]:
    day_start = datetime.combine(trading_date, time.min, tzinfo=UTC)
    day_end = day_start + timedelta(days=1)
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            member = _validated_member(bundle, expected_member, max_uncompressed_bytes)
            with (
                bundle.open(member) as source,
                io.TextIOWrapper(source, encoding="utf-8", newline="") as csv_source,
            ):
                for row_number, row in enumerate(csv.reader(csv_source), start=1):
                    if not row:
                        continue
                    trade = parse_archive_aggregate_trade_row(
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
                    if (
                        sequence.aggregate_trade_id is not None
                        and trade.aggregate_trade_id != sequence.aggregate_trade_id + 1
                    ):
                        raise BinanceArchiveError(
                            "Binance aggTrades aggregate ID discontinuity at row "
                            f"{row_number}: expected={sequence.aggregate_trade_id + 1} "
                            f"actual={trade.aggregate_trade_id}",
                            kind="sequence_gap",
                        )
                    if (
                        sequence.last_trade_id is not None
                        and trade.first_trade_id != sequence.last_trade_id + 1
                    ):
                        raise BinanceArchiveError(
                            "Binance aggTrades underlying trade ID discontinuity at row "
                            f"{row_number}: expected={sequence.last_trade_id + 1} "
                            f"actual={trade.first_trade_id}",
                            kind="sequence_gap",
                        )
                    if sequence.trade_time is not None and trade.trade_time < sequence.trade_time:
                        raise BinanceArchiveError(
                            f"Binance aggTrades timestamp regression at row {row_number}",
                            kind="sequence_gap",
                        )
                    sequence.aggregate_trade_id = trade.aggregate_trade_id
                    sequence.last_trade_id = trade.last_trade_id
                    sequence.trade_time = trade.trade_time
                    yield trade
    except BinanceArchiveError:
        raise
    except UnicodeDecodeError as exc:
        raise BinanceArchiveError(
            "Binance archive CSV is not UTF-8",
            kind="archive_layout",
        ) from exc
    except csv.Error as exc:
        raise BinanceArchiveError("invalid Binance archive CSV", kind="archive_layout") from exc
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise BinanceArchiveError(
            "invalid Binance ZIP archive",
            kind="archive_layout",
        ) from exc


def _parse_candles(
    archive_path: Path,
    expected_member: str,
    symbol: str,
    interval: str,
    trading_date: date,
    max_uncompressed_bytes: int,
) -> list[BinanceCandleData]:
    received_at = datetime.now(UTC)
    server_time = datetime.combine(trading_date + timedelta(days=1), time.min, tzinfo=UTC)
    candles: list[BinanceCandleData] = []
    try:
        with zipfile.ZipFile(archive_path) as bundle:
            member = _validated_member(bundle, expected_member, max_uncompressed_bytes)
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
            "Binance archive CSV is not UTF-8",
            kind="archive_layout",
        ) from exc
    except csv.Error as exc:
        raise BinanceArchiveError("invalid Binance archive CSV", kind="archive_layout") from exc
    except (zipfile.BadZipFile, RuntimeError, OSError) as exc:
        raise BinanceArchiveError(
            "invalid Binance ZIP archive",
            kind="archive_layout",
        ) from exc
    _validate_complete_candle_day(candles, interval)
    return candles


def _validate_complete_candle_day(
    candles: list[BinanceCandleData],
    interval: str,
) -> None:
    if not candles:
        raise BinanceArchiveError("Binance archive CSV is empty", kind="archive_payload")
    open_times = [candle.open_time for candle in candles]
    if len(set(open_times)) != len(open_times):
        raise BinanceArchiveError(
            "Binance archive contains duplicate candles",
            kind="archive_payload",
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
            "Binance daily archive row count mismatch: "
            f"expected={expected_rows} actual={len(candles)}",
            kind="archive_payload",
        )


def _validated_member(
    bundle: zipfile.ZipFile,
    expected_member: str,
    max_uncompressed_bytes: int,
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
    if member.file_size > max_uncompressed_bytes:
        raise BinanceArchiveError(
            "Binance archive exceeds uncompressed byte limit",
            kind="size_limit",
        )
    return member


def _trade_event(value: BinanceAggregateTradeData) -> AggregateTradeEvent:
    return AggregateTradeEvent(
        symbol=value.symbol,
        aggregate_trade_id=value.aggregate_trade_id,
        price=value.price,
        quantity=value.quantity,
        first_trade_id=value.first_trade_id,
        last_trade_id=value.last_trade_id,
        trade_time=value.trade_time,
        buyer_is_maker=value.buyer_is_maker,
        best_price_match=value.best_price_match,
    )


def _candle_event(value: BinanceCandleData) -> CandleEvent:
    return CandleEvent(
        symbol=value.symbol,
        interval=value.interval,
        open_time=value.open_time,
        close_time=value.close_time,
        open_price=value.open_price,
        high_price=value.high_price,
        low_price=value.low_price,
        close_price=value.close_price,
        base_volume=value.base_volume,
        quote_volume=value.quote_volume,
        trade_count=value.trade_count,
        closed=value.closed,
    )


def _manifest(
    archive: CachedArchive,
    *,
    symbol: str,
    interval: str,
    trading_date: date,
    row_count: int,
) -> ArchiveDayManifest:
    return ArchiveDayManifest(
        symbol=symbol,
        interval=interval,
        trading_date=trading_date,
        source_url=archive.source_url,
        checksum_sha256=archive.checksum_sha256,
        archive_bytes=archive.archive_bytes,
        row_count=row_count,
    )


def _included_dates(start_time: datetime, end_time: datetime) -> tuple[date, ...]:
    first = start_time.astimezone(UTC).date()
    last = (end_time.astimezone(UTC) - timedelta(microseconds=1)).date()
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def _parse_checksum(payload: bytes, filename: str) -> str:
    try:
        line = payload.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise BinanceArchiveError(
            "Binance archive checksum is not ASCII",
            kind="checksum",
        ) from exc
    parts = line.split()
    if not parts or not _SHA256.fullmatch(parts[0]):
        raise BinanceArchiveError(
            "Binance archive checksum file is invalid",
            kind="checksum",
        )
    if len(parts) > 1 and parts[-1].lstrip("*") != filename:
        raise BinanceArchiveError(
            "Binance archive checksum filename mismatch",
            kind="checksum",
        )
    return parts[0].lower()


def _create_run_dir(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="run-", dir=root))


def _unlink_if_exists(path: Path) -> None:
    path.unlink(missing_ok=True)


def system_resource_snapshot(path: Path) -> ResourceSnapshot:
    available_memory, available_commit = _available_memory_resources()
    return ResourceSnapshot(
        disk_free_bytes=shutil.disk_usage(path).free,
        available_memory_bytes=available_memory,
        available_commit_bytes=available_commit,
    )


def _available_memory_resources() -> tuple[int | None, int | None]:
    if os.name == "nt":
        win_dll = getattr(ctypes, "WinDLL", None)
        get_last_error = getattr(ctypes, "get_last_error", None)
        if win_dll is None or get_last_error is None:
            raise OSError("Windows memory APIs are unavailable")
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        kernel32 = win_dll("kernel32", use_last_error=True)
        query = kernel32.GlobalMemoryStatusEx
        query.argtypes = [ctypes.POINTER(_MemoryStatusEx)]
        query.restype = ctypes.c_int
        if not query(ctypes.byref(status)):
            raise OSError(get_last_error(), "GlobalMemoryStatusEx failed")
        return int(status.ullAvailPhys), int(status.ullAvailPageFile)
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        values: dict[str, int] = {}
        for line in meminfo.read_text(encoding="ascii").splitlines():
            key, separator, remainder = line.partition(":")
            if separator and key in {"MemAvailable", "CommitLimit", "Committed_AS"}:
                values[key] = int(remainder.split()[0]) * 1024
        available_commit = None
        if "CommitLimit" in values and "Committed_AS" in values:
            available_commit = max(0, values["CommitLimit"] - values["Committed_AS"])
        return values.get("MemAvailable"), available_commit
    return None, None


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong),
        ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]
