from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from stratus.config import Settings
from stratus.jobs.estimate_binance_dataset import estimate_binance_dataset


async def test_dataset_estimate_uses_monthly_head_without_downloading(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        length = "1000" if "aggTrades" in request.url.path else "100"
        return httpx.Response(200, headers={"Content-Length": length})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await estimate_binance_dataset(
            Settings(binance_market_data_retries=0),
            start_month=date(2025, 1, 1),
            end_month=date(2025, 2, 1),
            symbols=("ETHUSDT", "BTCUSDT"),
            storage_path=tmp_path,
            http_client=client,
        )

    assert result.symbols == ("BTCUSDT", "ETHUSDT")
    assert result.file_count == 8
    assert result.archive_bytes == 4400
    assert result.projected_postgresql_low_bytes == 52_800
    assert result.projected_postgresql_high_bytes == 88_000
    assert result.storage_status == "SUFFICIENT_FOR_HIGH_PROJECTION"
    assert len(result.groups) == 4
    assert all(method == "HEAD" for method, _ in seen)
    assert all("monthly" in url for _, url in seen)


async def test_dataset_estimate_fails_closed_without_content_length(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RuntimeError, match="Content-Length"):
            await estimate_binance_dataset(
                Settings(binance_market_data_retries=0),
                start_month=date(2025, 1, 1),
                end_month=date(2025, 1, 1),
                symbols=("BTCUSDT",),
                storage_path=tmp_path,
                http_client=client,
            )
