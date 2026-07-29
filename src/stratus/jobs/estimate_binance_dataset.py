from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from stratus.config import Settings, get_settings

_POSTGRESQL_LOW_MULTIPLIER = 12
_POSTGRESQL_HIGH_MULTIPLIER = 20


@dataclass(frozen=True, slots=True)
class BinanceArchiveHead:
    symbol: str
    dataset: str
    month: str
    source_url: str
    archive_bytes: int


@dataclass(frozen=True, slots=True)
class BinanceArchiveGroupEstimate:
    symbol: str
    dataset: str
    file_count: int
    archive_bytes: int


@dataclass(frozen=True, slots=True)
class BinanceDatasetEstimate:
    estimate_version: str
    checked_at: datetime
    start_month: str
    end_month: str
    symbols: tuple[str, ...]
    file_count: int
    archive_bytes: int
    projected_postgresql_low_bytes: int
    projected_postgresql_high_bytes: int
    projection_basis: str
    storage_path: str
    storage_free_bytes: int
    storage_status: str
    groups: tuple[BinanceArchiveGroupEstimate, ...]


async def estimate_binance_dataset(
    settings: Settings,
    *,
    start_month: date,
    end_month: date,
    symbols: tuple[str, ...] | None = None,
    storage_path: Path = Path("."),
    http_client: httpx.AsyncClient | None = None,
) -> BinanceDatasetEstimate:
    months = _month_starts(start_month, end_month)
    selected_symbols = tuple(sorted(symbols or settings.binance_symbols))
    if not selected_symbols:
        raise ValueError("Binance dataset estimate requires at least one symbol")
    requests = tuple(
        (symbol, dataset, month, _archive_url(settings, symbol, dataset, month))
        for month in months
        for symbol in selected_symbols
        for dataset in ("aggTrades", "klines-5m")
    )
    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(
        timeout=httpx.Timeout(float(settings.binance_archive_timeout_seconds)),
        follow_redirects=False,
        limits=httpx.Limits(max_connections=8),
    )
    semaphore = asyncio.Semaphore(8)
    try:
        heads = await asyncio.gather(
            *(
                _head_archive(
                    client,
                    semaphore,
                    symbol=symbol,
                    dataset=dataset,
                    month=month,
                    source_url=source_url,
                    retries=settings.binance_market_data_retries,
                )
                for symbol, dataset, month, source_url in requests
            )
        )
    finally:
        if owns_client:
            await client.aclose()

    archive_bytes = sum(item.archive_bytes for item in heads)
    low = archive_bytes * _POSTGRESQL_LOW_MULTIPLIER
    high = archive_bytes * _POSTGRESQL_HIGH_MULTIPLIER
    resolved_storage, free = await asyncio.to_thread(_storage_free, storage_path)
    if free >= high:
        status = "SUFFICIENT_FOR_HIGH_PROJECTION"
    elif free >= low:
        status = "MARGINAL_BETWEEN_PROJECTIONS"
    else:
        status = "INSUFFICIENT_FOR_POSTGRESQL_PLAN"
    return BinanceDatasetEstimate(
        estimate_version="binance-monthly-head-v1",
        checked_at=datetime.now(UTC),
        start_month=months[0].strftime("%Y-%m"),
        end_month=months[-1].strftime("%Y-%m"),
        symbols=selected_symbols,
        file_count=len(heads),
        archive_bytes=archive_bytes,
        projected_postgresql_low_bytes=low,
        projected_postgresql_high_bytes=high,
        projection_basis=(
            "planning range only: 12x-20x compressed archives for parsed rows, "
            "PostgreSQL heap, primary/identity, sequence and time indexes"
        ),
        storage_path=str(resolved_storage),
        storage_free_bytes=free,
        storage_status=status,
        groups=_group_heads(tuple(heads)),
    )


async def _head_archive(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    *,
    symbol: str,
    dataset: str,
    month: date,
    source_url: str,
    retries: int,
) -> BinanceArchiveHead:
    last_error: Exception | None = None
    async with semaphore:
        for attempt in range(retries + 1):
            try:
                response = await client.head(source_url)
                response.raise_for_status()
                content_length = response.headers.get("content-length")
                if content_length is None or int(content_length) <= 0:
                    raise ValueError("archive HEAD has no positive Content-Length")
                return BinanceArchiveHead(
                    symbol=symbol,
                    dataset=dataset,
                    month=month.strftime("%Y-%m"),
                    source_url=source_url,
                    archive_bytes=int(content_length),
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt < retries:
                    await asyncio.sleep(2**attempt)
        raise RuntimeError(f"Binance archive HEAD failed: {source_url}: {last_error}")


def _archive_url(
    settings: Settings,
    symbol: str,
    dataset: str,
    month: date,
) -> str:
    month_text = month.strftime("%Y-%m")
    base = settings.binance_archive_base_url.rstrip("/")
    if dataset == "aggTrades":
        return f"{base}/monthly/aggTrades/{symbol}/{symbol}-aggTrades-{month_text}.zip"
    if dataset == "klines-5m":
        return f"{base}/monthly/klines/{symbol}/5m/{symbol}-5m-{month_text}.zip"
    raise ValueError(f"unsupported Binance estimate dataset: {dataset}")


def _month_starts(start_month: date, end_month: date) -> tuple[date, ...]:
    if start_month.day != 1 or end_month.day != 1:
        raise ValueError("Binance estimate months must use the first day")
    if start_month > end_month:
        raise ValueError("Binance estimate start_month must not exceed end_month")
    values: list[date] = []
    current = start_month
    while current <= end_month:
        values.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return tuple(values)


def _group_heads(
    heads: tuple[BinanceArchiveHead, ...],
) -> tuple[BinanceArchiveGroupEstimate, ...]:
    groups: dict[tuple[str, str], list[BinanceArchiveHead]] = {}
    for head in heads:
        groups.setdefault((head.symbol, head.dataset), []).append(head)
    return tuple(
        BinanceArchiveGroupEstimate(
            symbol=symbol,
            dataset=dataset,
            file_count=len(values),
            archive_bytes=sum(value.archive_bytes for value in values),
        )
        for (symbol, dataset), values in sorted(groups.items())
    )


def _storage_free(storage_path: Path) -> tuple[Path, int]:
    resolved = storage_path.resolve()
    return resolved, shutil.disk_usage(resolved).free


def _month(value: str) -> date:
    try:
        return date.fromisoformat(value + "-01")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("month must use YYYY-MM") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Estimate official Binance research archives without downloading them"
    )
    parser.add_argument("--start-month", type=_month, required=True)
    parser.add_argument("--end-month", type=_month, required=True)
    parser.add_argument("--storage-path", type=Path, default=Path("."))
    arguments = parser.parse_args()
    result = asyncio.run(
        estimate_binance_dataset(
            get_settings(),
            start_month=arguments.start_month,
            end_month=arguments.end_month,
            storage_path=arguments.storage_path,
        )
    )
    print(json.dumps(asdict(result), default=str, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
