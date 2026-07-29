from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from stratus.collectors.binance_archive import (
    BinanceArchiveClient,
    BinanceArchiveError,
)

TRADING_DATE = date(2026, 7, 10)
SYMBOL = "BTCUSDT"
INTERVAL = "12h"
ZIP_NAME = f"{SYMBOL}-{INTERVAL}-{TRADING_DATE.isoformat()}.zip"
CSV_NAME = ZIP_NAME.removesuffix(".zip") + ".csv"


def _row(open_time: datetime) -> str:
    open_us = int(open_time.timestamp() * 1_000_000)
    close_us = open_us + 12 * 60 * 60 * 1_000_000 - 1
    return ",".join(
        [
            str(open_us),
            "64000.00000000",
            "64100.00000000",
            "63900.00000000",
            "64050.00000000",
            "1.25000000",
            str(close_us),
            "80062.50000000",
            "123",
            "0.70000000",
            "44835.00000000",
            "0",
        ]
    )


def _archive(*, complete: bool = True, member_name: str = CSV_NAME) -> bytes:
    midnight = datetime.combine(TRADING_DATE, datetime.min.time(), tzinfo=UTC)
    rows = [_row(midnight)]
    if complete:
        rows.append(_row(midnight + timedelta(hours=12)))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member_name, "\n".join(rows) + "\n")
    return buffer.getvalue()


def _http_client(archive: bytes, *, checksum: str | None = None) -> httpx.AsyncClient:
    digest = checksum or hashlib.sha256(archive).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, content=f"{digest}  {ZIP_NAME}\n".encode("ascii"))
        return httpx.Response(200, content=archive)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_verified_archive_parses_microsecond_candles_without_extraction() -> None:
    archive = _archive()
    http_client = _http_client(archive)
    try:
        async with BinanceArchiveClient(
            base_url="https://data.binance.vision/data/spot",
            http_client=http_client,
        ) as client:
            batch = await client.fetch_daily_klines(SYMBOL, INTERVAL, TRADING_DATE)
    finally:
        await http_client.aclose()

    assert batch.checksum_sha256 == hashlib.sha256(archive).hexdigest()
    assert batch.archive_bytes == len(archive)
    assert len(batch.candles) == 2
    assert batch.candles[0].open_time == datetime(2026, 7, 10, tzinfo=UTC)
    assert batch.candles[0].close_time.microsecond == 999_999
    assert batch.candles[1].open_time == datetime(2026, 7, 10, 12, tzinfo=UTC)
    assert all(candle.closed for candle in batch.candles)
    assert all(candle.source == "binance_archive" for candle in batch.candles)


async def test_archive_checksum_mismatch_is_rejected_before_zip_parsing() -> None:
    archive = _archive(member_name="unexpected.csv")
    http_client = _http_client(archive, checksum="0" * 64)
    try:
        async with BinanceArchiveClient(
            base_url="https://data.binance.vision/data/spot",
            http_client=http_client,
        ) as client:
            with pytest.raises(BinanceArchiveError, match="checksum mismatch"):
                await client.fetch_daily_klines(SYMBOL, INTERVAL, TRADING_DATE)
    finally:
        await http_client.aclose()


async def test_archive_rejects_incomplete_daily_dataset() -> None:
    archive = _archive(complete=False)
    http_client = _http_client(archive)
    try:
        async with BinanceArchiveClient(
            base_url="https://data.binance.vision/data/spot",
            http_client=http_client,
        ) as client:
            with pytest.raises(BinanceArchiveError, match="row count mismatch"):
                await client.fetch_daily_klines(SYMBOL, INTERVAL, TRADING_DATE)
    finally:
        await http_client.aclose()
