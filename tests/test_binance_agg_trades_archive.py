from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from stratus.collectors.binance_archive import BinanceArchiveClient, BinanceArchiveError

TRADING_DATE = date(2026, 7, 10)
SYMBOL = "BTCUSDT"
ZIP_NAME = f"{SYMBOL}-aggTrades-{TRADING_DATE.isoformat()}.zip"
CSV_NAME = ZIP_NAME.removesuffix(".zip") + ".csv"
MIDNIGHT = datetime.combine(TRADING_DATE, datetime.min.time(), tzinfo=UTC)


def _row(
    aggregate_trade_id: int,
    first_trade_id: int,
    last_trade_id: int,
    trade_time: datetime,
    *,
    buyer_is_maker: str = "False",
    best_price_match: str = "True",
) -> str:
    timestamp_us = int(trade_time.timestamp() * 1_000_000)
    return ",".join(
        [
            str(aggregate_trade_id),
            "64000.12500000",
            "0.00125000",
            str(first_trade_id),
            str(last_trade_id),
            str(timestamp_us),
            buyer_is_maker,
            best_price_match,
        ]
    )


def _archive(rows: list[str], *, member_name: str = CSV_NAME) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member_name, "\n".join(rows) + "\n")
    return buffer.getvalue()


def _http_client(archive: bytes) -> httpx.AsyncClient:
    digest = hashlib.sha256(archive).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, content=f"{digest}  {ZIP_NAME}\n".encode("ascii"))
        return httpx.Response(200, content=archive)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _fetch(archive: bytes):  # type: ignore[no-untyped-def]
    http_client = _http_client(archive)
    try:
        async with BinanceArchiveClient(
            base_url="https://data.binance.vision/data/spot",
            http_client=http_client,
        ) as client:
            return await client.fetch_daily_agg_trades(SYMBOL, TRADING_DATE)
    finally:
        await http_client.aclose()


async def test_verified_agg_trades_archive_parses_microseconds_and_booleans() -> None:
    archive = _archive(
        [
            _row(100, 1000, 1001, MIDNIGHT + timedelta(microseconds=250160)),
            _row(
                101,
                1002,
                1004,
                MIDNIGHT + timedelta(seconds=1, microseconds=873446),
                buyer_is_maker="True",
                best_price_match="False",
            ),
        ]
    )

    batch = await _fetch(archive)

    assert batch.checksum_sha256 == hashlib.sha256(archive).hexdigest()
    assert batch.source_url.endswith(f"/daily/aggTrades/{SYMBOL}/{ZIP_NAME}")
    assert len(batch.trades) == 2
    assert batch.trades[0].trade_time == MIDNIGHT + timedelta(microseconds=250160)
    assert batch.trades[0].buyer_is_maker is False
    assert batch.trades[0].best_price_match is True
    assert batch.trades[1].buyer_is_maker is True
    assert batch.trades[1].best_price_match is False


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                _row(100, 1000, 1000, MIDNIGHT),
                _row(102, 1001, 1001, MIDNIGHT + timedelta(seconds=1)),
            ],
            "aggregate ID discontinuity",
        ),
        (
            [
                _row(100, 1000, 1000, MIDNIGHT),
                _row(101, 1002, 1002, MIDNIGHT + timedelta(seconds=1)),
            ],
            "underlying trade ID discontinuity",
        ),
        (
            [
                _row(100, 1000, 1000, MIDNIGHT + timedelta(seconds=2)),
                _row(101, 1001, 1001, MIDNIGHT + timedelta(seconds=1)),
            ],
            "timestamp regression",
        ),
    ],
)
async def test_agg_trades_archive_rejects_sequence_defects(rows: list[str], message: str) -> None:
    with pytest.raises(BinanceArchiveError, match=message) as error:
        await _fetch(_archive(rows))

    assert error.value.kind == "sequence_gap"


async def test_agg_trades_archive_rejects_rows_outside_requested_utc_day() -> None:
    with pytest.raises(BinanceArchiveError, match="outside") as error:
        await _fetch(_archive([_row(100, 1000, 1000, MIDNIGHT - timedelta(microseconds=1))]))

    assert error.value.kind == "archive_payload"
