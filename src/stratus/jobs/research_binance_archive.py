from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

import httpx

from stratus.backtesting.engine import BacktestConfig
from stratus.backtesting.matrix import BacktestMatrixEntry, run_backtest_matrix
from stratus.backtesting.metrics import calculate_backtest_metrics, evaluate_holdout_gate
from stratus.collectors.binance_archive_stream import (
    ArchiveDayManifest,
    BinanceArchiveEventSource,
    BoundedArchiveCache,
    ResourceProbe,
)
from stratus.config import Settings
from stratus.jobs.backtest_binance import BinanceBacktestReport
from stratus.repositories.crypto_research import (
    ResearchCandidateRecord,
    ResearchLedgerError,
)


@dataclass(frozen=True, slots=True)
class BinanceArchiveResearchWindow:
    reports: dict[str, BinanceBacktestReport]
    manifests: tuple[ArchiveDayManifest, ...]


async def run_binance_archive_research_window(
    *,
    settings: Settings,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    config: BacktestConfig,
    candidates: tuple[ResearchCandidateRecord, ...],
    bootstrap_samples: int,
    http_client: httpx.AsyncClient | None = None,
    resource_probe: ResourceProbe | None = None,
) -> BinanceArchiveResearchWindow:
    if not candidates:
        raise ValueError("archive research window requires at least one candidate")
    stressed_config = replace(
        config,
        entry_slippage_bps=config.entry_slippage_bps * 2,
        exit_slippage_bps=config.exit_slippage_bps * 2,
    )
    entries = tuple(
        entry
        for candidate in candidates
        for entry in (
            BacktestMatrixEntry(
                key=_matrix_key(candidate.candidate_key, "baseline"),
                config=config,
                strategy_factory=candidate.spec.build_strategy,
            ),
            BacktestMatrixEntry(
                key=_matrix_key(candidate.candidate_key, "double_slippage"),
                config=stressed_config,
                strategy_factory=candidate.spec.build_strategy,
            ),
        )
    )
    async with BoundedArchiveCache(
        root=settings.binance_research_cache_dir,
        cache_max_bytes=settings.binance_research_cache_max_bytes,
        min_free_bytes=settings.binance_research_min_free_bytes,
        max_archive_bytes=settings.binance_research_max_archive_bytes,
        min_available_memory_bytes=(settings.binance_research_min_available_memory_bytes),
        min_available_commit_bytes=(settings.binance_research_min_available_commit_bytes),
        timeout_seconds=settings.binance_archive_timeout_seconds,
        retries=settings.binance_market_data_retries,
        resource_wait_seconds=settings.binance_research_resource_wait_seconds,
        resource_poll_seconds=settings.binance_research_resource_poll_seconds,
        http_client=http_client,
        resource_probe=resource_probe,
    ) as cache:
        source = BinanceArchiveEventSource(
            cache,
            base_url=settings.binance_archive_base_url,
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            max_uncompressed_bytes=(settings.binance_research_max_uncompressed_bytes),
            resource_check_events=settings.binance_research_resource_check_events,
            resource_wait_seconds=settings.binance_research_resource_wait_seconds,
            resource_poll_seconds=settings.binance_research_resource_poll_seconds,
        )
        matrix = await run_backtest_matrix(entries, source.events())
        manifests = source.manifests

    data_hashes = {result.data_hash for result in matrix.values()}
    event_counts = {result.event_count for result in matrix.values()}
    if len(data_hashes) != 1 or len(event_counts) != 1:
        raise ResearchLedgerError(
            "archive research matrix entries consumed different event streams"
        )
    reports: dict[str, BinanceBacktestReport] = {}
    for candidate in candidates:
        baseline = matrix[_matrix_key(candidate.candidate_key, "baseline")]
        stressed = matrix[_matrix_key(candidate.candidate_key, "double_slippage")]
        baseline_metrics = calculate_backtest_metrics(
            baseline,
            bootstrap_samples=bootstrap_samples,
        )
        stress_metrics = calculate_backtest_metrics(
            stressed,
            bootstrap_samples=bootstrap_samples,
        )
        reports[candidate.candidate_key] = BinanceBacktestReport(
            baseline=baseline,
            baseline_metrics=baseline_metrics,
            double_slippage=stressed,
            double_slippage_metrics=stress_metrics,
            gate=evaluate_holdout_gate(
                baseline_metrics,
                stress_metrics,
                diversification_passed=False,
                parameter_stability_passed=False,
            ),
        )
    return BinanceArchiveResearchWindow(reports=reports, manifests=manifests)


def _matrix_key(candidate_key: str, stress_case: str) -> str:
    return f"{candidate_key}:{stress_case}"
