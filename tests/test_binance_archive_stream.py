from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from stratus.backtesting.engine import BacktestConfig
from stratus.backtesting.events import AggregateTradeEvent, CandleEvent
from stratus.backtesting.research import MovingAverageCandidateSpec
from stratus.collectors.binance_archive import BinanceArchiveError
from stratus.collectors.binance_archive_stream import (
    BinanceArchiveEventSource,
    BoundedArchiveCache,
    ResourceSnapshot,
)
from stratus.config import Settings
from stratus.jobs.research_binance_archive import (
    run_binance_archive_research_window,
)
from stratus.repositories.crypto_research import ResearchCandidateRecord

SYMBOL = "BTCUSDT"
INTERVAL = "5m"
TRADING_DATE = date(2026, 7, 10)
START = datetime.combine(TRADING_DATE, datetime.min.time(), tzinfo=UTC)
END = START + timedelta(days=1)


def _zip_file(filename: str, rows: list[str]) -> bytes:
    buffer = io.BytesIO()
    member = filename.removesuffix(".zip") + ".csv"
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr(member, "\n".join(rows) + "\n")
    return buffer.getvalue()


def _kline_rows() -> list[str]:
    rows: list[str] = []
    for index in range(288):
        open_time = START + timedelta(minutes=5 * index)
        open_us = int(open_time.timestamp() * 1_000_000)
        close_us = open_us + 5 * 60 * 1_000_000 - 1
        rows.append(
            ",".join(
                (
                    str(open_us),
                    "100.00",
                    "101.00",
                    "99.00",
                    "100.50",
                    "10.00",
                    str(close_us),
                    "1005.00",
                    "100",
                    "5.00",
                    "502.50",
                    "0",
                )
            )
        )
    return rows


def _trade_rows() -> list[str]:
    rows: list[str] = []
    for index in range(288):
        trade_time = START + timedelta(minutes=5 * index + 2)
        timestamp_us = int(trade_time.timestamp() * 1_000_000)
        rows.append(
            ",".join(
                (
                    str(1000 + index),
                    "100.25",
                    "1.00",
                    str(2000 + index),
                    str(2000 + index),
                    str(timestamp_us),
                    "true" if index % 2 else "false",
                    "true",
                )
            )
        )
    return rows


def _archives() -> dict[str, bytes]:
    day = TRADING_DATE.isoformat()
    kline_name = f"{SYMBOL}-{INTERVAL}-{day}.zip"
    trade_name = f"{SYMBOL}-aggTrades-{day}.zip"
    return {
        kline_name: _zip_file(kline_name, _kline_rows()),
        trade_name: _zip_file(trade_name, _trade_rows()),
    }


def _client(archives: dict[str, bytes]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", maxsplit=1)[-1]
        if name.endswith(".CHECKSUM"):
            archive_name = name.removesuffix(".CHECKSUM")
            archive = archives[archive_name]
            digest = hashlib.sha256(archive).hexdigest()
            return httpx.Response(
                200,
                content=f"{digest}  {archive_name}\n".encode("ascii"),
            )
        archive = archives[name]
        return httpx.Response(
            200,
            headers={"Content-Length": str(len(archive))},
            content=archive,
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _resources(path: Path) -> ResourceSnapshot:
    return ResourceSnapshot(
        disk_free_bytes=100_000_000_000,
        available_memory_bytes=100_000_000_000,
        available_commit_bytes=100_000_000_000,
    )


async def test_bounded_archive_stream_preserves_every_event_and_cleans_cache(
    tmp_path: Path,
) -> None:
    archives = _archives()
    client = _client(archives)
    cache_root = tmp_path / "cache"
    try:
        async with BoundedArchiveCache(
            root=cache_root,
            cache_max_bytes=10_000_000,
            min_free_bytes=20_000_000,
            max_archive_bytes=5_000_000,
            min_available_memory_bytes=10_000_000,
            min_available_commit_bytes=10_000_000,
            timeout_seconds=5,
            retries=0,
            http_client=client,
            resource_probe=_resources,
        ) as cache:
            source = BinanceArchiveEventSource(
                cache,
                base_url="https://data.binance.vision/data/spot",
                symbol=SYMBOL,
                interval=INTERVAL,
                start_time=START,
                end_time=END,
                max_uncompressed_bytes=10_000_000,
            )
            events = [event async for event in source.events()]
            manifests = source.manifests
    finally:
        await client.aclose()

    assert len(events) == 576
    assert all(
        isinstance(events[index], AggregateTradeEvent)
        and isinstance(events[index + 1], CandleEvent)
        for index in range(0, len(events), 2)
    )
    trades = [event for event in events if isinstance(event, AggregateTradeEvent)]
    assert [trade.aggregate_trade_id for trade in trades] == list(range(1000, 1288))
    assert [trade.first_trade_id for trade in trades] == list(range(2000, 2288))
    assert [manifest.interval for manifest in manifests] == [INTERVAL, "aggTrades"]
    assert [manifest.row_count for manifest in manifests] == [288, 288]
    assert cache_root.exists()
    assert not list(cache_root.iterdir())


async def test_bounded_archive_cache_fails_before_network_below_disk_reserve(
    tmp_path: Path,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    def low_resources(path: Path) -> ResourceSnapshot:
        return ResourceSnapshot(
            disk_free_bytes=5_000_000,
            available_memory_bytes=100_000_000,
            available_commit_bytes=100_000_000,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_root = tmp_path / "cache"
    try:
        with pytest.raises(BinanceArchiveError, match="free-disk reserve"):
            async with BoundedArchiveCache(
                root=cache_root,
                cache_max_bytes=10_000_000,
                min_free_bytes=20_000_000,
                max_archive_bytes=5_000_000,
                min_available_memory_bytes=10_000_000,
                min_available_commit_bytes=10_000_000,
                timeout_seconds=5,
                retries=0,
                http_client=client,
                resource_probe=low_resources,
            ):
                pass
    finally:
        await client.aclose()

    assert requests == 0
    assert cache_root.exists()
    assert not list(cache_root.iterdir())


async def test_bounded_archive_cache_fails_before_network_below_memory_reserve(
    tmp_path: Path,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    def low_resources(path: Path) -> ResourceSnapshot:
        return ResourceSnapshot(
            disk_free_bytes=100_000_000,
            available_memory_bytes=5_000_000,
            available_commit_bytes=100_000_000,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_root = tmp_path / "cache"
    try:
        with pytest.raises(BinanceArchiveError, match="available-memory reserve"):
            async with BoundedArchiveCache(
                root=cache_root,
                cache_max_bytes=10_000_000,
                min_free_bytes=20_000_000,
                max_archive_bytes=5_000_000,
                min_available_memory_bytes=10_000_000,
                min_available_commit_bytes=10_000_000,
                timeout_seconds=5,
                retries=0,
                http_client=client,
                resource_probe=low_resources,
            ):
                pass
    finally:
        await client.aclose()

    assert requests == 0
    assert cache_root.exists()
    assert not list(cache_root.iterdir())


async def test_bounded_archive_cache_fails_before_network_below_commit_reserve(
    tmp_path: Path,
) -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    def low_resources(path: Path) -> ResourceSnapshot:
        return ResourceSnapshot(
            disk_free_bytes=100_000_000,
            available_memory_bytes=100_000_000,
            available_commit_bytes=5_000_000,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    cache_root = tmp_path / "cache"
    try:
        with pytest.raises(BinanceArchiveError, match="available-commit reserve"):
            async with BoundedArchiveCache(
                root=cache_root,
                cache_max_bytes=10_000_000,
                min_free_bytes=20_000_000,
                max_archive_bytes=5_000_000,
                min_available_memory_bytes=10_000_000,
                min_available_commit_bytes=10_000_000,
                timeout_seconds=5,
                retries=0,
                http_client=client,
                resource_probe=low_resources,
            ):
                pass
    finally:
        await client.aclose()

    assert requests == 0
    assert cache_root.exists()
    assert not list(cache_root.iterdir())


async def test_bounded_archive_cache_waits_for_transient_resource_pressure(
    tmp_path: Path,
) -> None:
    high = ResourceSnapshot(
        disk_free_bytes=100_000_000,
        available_memory_bytes=100_000_000,
        available_commit_bytes=100_000_000,
    )
    low = ResourceSnapshot(
        disk_free_bytes=100_000_000,
        available_memory_bytes=5_000_000,
        available_commit_bytes=100_000_000,
    )
    snapshots = iter((high, low, high))
    calls = 0

    def changing_resources(path: Path) -> ResourceSnapshot:
        nonlocal calls
        calls += 1
        return next(snapshots)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        async with BoundedArchiveCache(
            root=tmp_path / "cache",
            cache_max_bytes=10_000_000,
            min_free_bytes=20_000_000,
            max_archive_bytes=5_000_000,
            min_available_memory_bytes=10_000_000,
            min_available_commit_bytes=10_000_000,
            timeout_seconds=5,
            retries=0,
            http_client=client,
            resource_probe=changing_resources,
        ) as cache:
            await cache.wait_for_resources(
                max_wait_seconds=1,
                poll_seconds=0.001,
            )
    finally:
        await client.aclose()

    assert calls == 3


async def test_bounded_archive_cache_waits_during_initial_preflight(
    tmp_path: Path,
) -> None:
    snapshots = iter(
        (
            ResourceSnapshot(
                disk_free_bytes=100_000_000,
                available_memory_bytes=5_000_000,
                available_commit_bytes=100_000_000,
            ),
            ResourceSnapshot(
                disk_free_bytes=100_000_000,
                available_memory_bytes=100_000_000,
                available_commit_bytes=100_000_000,
            ),
        )
    )
    calls = 0

    def changing_resources(path: Path) -> ResourceSnapshot:
        nonlocal calls
        calls += 1
        return next(snapshots)

    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    try:
        async with BoundedArchiveCache(
            root=tmp_path / "cache",
            cache_max_bytes=10_000_000,
            min_free_bytes=20_000_000,
            max_archive_bytes=5_000_000,
            min_available_memory_bytes=10_000_000,
            min_available_commit_bytes=10_000_000,
            timeout_seconds=5,
            retries=0,
            resource_wait_seconds=1,
            resource_poll_seconds=0.001,
            http_client=client,
            resource_probe=changing_resources,
        ):
            pass
    finally:
        await client.aclose()

    assert calls == 2


async def test_archive_window_runs_baseline_and_stress_from_one_bounded_stream(
    tmp_path: Path,
) -> None:
    archives = _archives()
    client = _client(archives)
    spec = MovingAverageCandidateSpec(2, 3)
    candidate = ResearchCandidateRecord(
        id=1,
        experiment_id=1,
        candidate_key=spec.candidate_key,
        strategy_name="moving_average_cross",
        strategy_version=spec.strategy_version,
        spec=spec,
    )
    config = BacktestConfig(
        initial_quote=Decimal("1000"),
        order_notional=Decimal("100"),
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("10"),
        entry_slippage_bps=Decimal("5"),
        exit_slippage_bps=Decimal("5"),
        latency=timedelta(milliseconds=250),
        order_ttl=timedelta(seconds=20),
        maker_participation_rate=Decimal("0.05"),
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
    )
    settings = Settings(
        binance_market_data_retries=0,
        binance_research_cache_dir=tmp_path / "research-cache",
        binance_research_cache_max_bytes=1_000_000_000,
        binance_research_min_free_bytes=10_000_000_000,
        binance_research_max_archive_bytes=10_000_000,
        binance_research_max_uncompressed_bytes=100_000_000,
        binance_research_min_available_memory_bytes=250_000_000,
    )

    try:
        window = await run_binance_archive_research_window(
            settings=settings,
            symbol=SYMBOL,
            interval=INTERVAL,
            start_time=START,
            end_time=END,
            config=config,
            candidates=(candidate,),
            bootstrap_samples=100,
            http_client=client,
            resource_probe=_resources,
        )
    finally:
        await client.aclose()

    report = window.reports[candidate.candidate_key]
    assert report.baseline.event_count == 576
    assert report.double_slippage.event_count == 576
    assert report.baseline.data_hash == report.double_slippage.data_hash
    assert report.baseline.config_hash != report.double_slippage.config_hash
    assert report.baseline.run_hash != report.double_slippage.run_hash
    assert len(window.manifests) == 2
    assert not list(settings.binance_research_cache_dir.iterdir())
