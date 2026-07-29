from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.backtesting.engine import BacktestConfig, BacktestResult, CompletedCycle
from stratus.backtesting.metrics import (
    BacktestGateDecision,
    calculate_pooled_backtest_metrics,
)
from stratus.backtesting.research import (
    MovingAverageCandidateSpec,
    ResearchExecutionConfig,
    ResearchExperimentSpec,
    ResearchSelectionPolicy,
)
from stratus.collectors.binance_archive_stream import ArchiveDayManifest
from stratus.config import Settings
from stratus.db.models import (
    CryptoAggregateTrade,
    CryptoArchiveImport,
    CryptoResearchResult,
    CryptoSymbol,
    TradingVenue,
    VenueMode,
)
from stratus.jobs.backtest_binance import BinanceBacktestReport
from stratus.jobs.research_binance import (
    register_binance_research,
    run_binance_research_holdout,
    run_binance_research_oos,
    run_binance_research_oos_batch,
    unlock_binance_research_holdout,
)
from stratus.jobs.research_binance_archive import BinanceArchiveResearchWindow
from stratus.repositories.crypto_research import ResearchLedgerError
from stratus.repositories.venue_runtime_control import (
    set_venue_enabled,
    set_venue_mode,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]
SYMBOLS = ("BTCUSDT", "ETHUSDT")
START = datetime(2024, 1, 1, tzinfo=UTC)


def _spec() -> ResearchExperimentSpec:
    return ResearchExperimentSpec(
        name="binance-orchestrator-test",
        symbols=SYMBOLS,
        interval="5m",
        dataset_start=START,
        dataset_end=START + timedelta(days=60),
        minimum_train=timedelta(days=30),
        validation=timedelta(days=10),
        test=timedelta(days=10),
        step=timedelta(days=10),
        final_holdout=timedelta(days=10),
        execution=ResearchExecutionConfig(
            initial_quote_per_symbol=Decimal("1000"),
            order_notional=Decimal("100"),
            maker_fee_bps=Decimal("1"),
            taker_fee_bps=Decimal("10"),
            entry_slippage_bps=Decimal("5"),
            exit_slippage_bps=Decimal("5"),
            latency_ms=250,
            order_ttl_seconds=20,
            maker_participation_rate=Decimal("0.05"),
        ),
        selection=ResearchSelectionPolicy(
            bootstrap_samples=100,
            minimum_oos_cycles=4,
            minimum_symbol_cycles=2,
            minimum_positive_neighbors=2,
        ),
        candidates=tuple(
            MovingAverageCandidateSpec(fast_period, 50) for fast_period in (8, 10, 12)
        ),
    )


async def _setup(session: AsyncSession) -> None:
    await set_venue_enabled(session, TradingVenue.BINANCE_SPOT, True)
    await set_venue_mode(session, TradingVenue.BINANCE_SPOT, VenueMode.BACKTEST)
    for symbol in SYMBOLS:
        session.add(
            CryptoSymbol(
                venue=TradingVenue.BINANCE_SPOT,
                symbol=symbol,
                base_asset=symbol.removesuffix("USDT"),
                quote_asset="USDT",
                status="TRADING",
                enabled=True,
                price_tick=Decimal("0.01"),
                quantity_step=Decimal("0.00001"),
                minimum_quantity=Decimal("0.00001"),
                minimum_notional=Decimal("5"),
                maximum_notional=Decimal("9000000"),
                supports_oco=True,
                supports_oto=True,
                filters={},
                filter_hash=hashlib.sha256(symbol.encode()).hexdigest(),
                fetched_at=datetime.now(UTC),
            )
        )


class FakeResearchRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime, int]] = []

    async def __call__(self, **kwargs: Any) -> BinanceBacktestReport:
        symbol = kwargs["symbol"]
        start = kwargs["start_time"]
        end = kwargs["end_time"]
        config = kwargs["config"]
        strategy = kwargs["strategy_factory"]()
        fast_period = strategy.fast_period
        self.calls.append((symbol, start, end, fast_period))
        baseline_pnl = Decimal("2") if fast_period == 10 else Decimal("1")
        return _report(
            symbol=symbol,
            start=start,
            end=end,
            strategy_name=strategy.name,
            strategy_version=strategy.version,
            config=config,
            baseline_pnl=baseline_pnl,
            bootstrap_samples=kwargs["bootstrap_samples"],
        )


class FakeArchiveWindowRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime, tuple[str, ...]]] = []

    async def __call__(self, **kwargs: Any) -> BinanceArchiveResearchWindow:
        symbol = kwargs["symbol"]
        start = kwargs["start_time"]
        end = kwargs["end_time"]
        config = kwargs["config"]
        candidates = kwargs["candidates"]
        bootstrap_samples = kwargs["bootstrap_samples"]
        self.calls.append(
            (
                symbol,
                start,
                end,
                tuple(candidate.candidate_key for candidate in candidates),
            )
        )
        reports = {}
        for candidate in candidates:
            strategy = candidate.spec.build_strategy()
            baseline_pnl = Decimal("2") if strategy.fast_period == 10 else Decimal("1")
            reports[candidate.candidate_key] = _report(
                symbol=symbol,
                start=start,
                end=end,
                strategy_name=strategy.name,
                strategy_version=strategy.version,
                config=config,
                baseline_pnl=baseline_pnl,
                bootstrap_samples=bootstrap_samples,
            )
        manifests = tuple(
            _manifest(symbol, interval, trading_date)
            for trading_date in _dates(start, end)
            for interval in ("5m", "aggTrades")
        )
        return BinanceArchiveResearchWindow(reports=reports, manifests=manifests)


def _report(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    strategy_name: str,
    strategy_version: str,
    config: BacktestConfig,
    baseline_pnl: Decimal,
    bootstrap_samples: int,
) -> BinanceBacktestReport:
    data_hash = hashlib.sha256(f"{symbol}:{start}:{end}".encode()).hexdigest()
    baseline_cycles = _cycles(symbol, start, baseline_pnl)
    stressed_cycles = _cycles(symbol, start, baseline_pnl / 2)
    baseline = _result(
        symbol=symbol,
        start=start,
        end=end,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        initial_quote=config.initial_quote,
        cycles=baseline_cycles,
        data_hash=data_hash,
        config_hash=config.canonical_hash(),
    )
    stressed = _result(
        symbol=symbol,
        start=start,
        end=end,
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        initial_quote=config.initial_quote,
        cycles=stressed_cycles,
        data_hash=data_hash,
        config_hash=replace(
            config,
            entry_slippage_bps=config.entry_slippage_bps * 2,
            exit_slippage_bps=config.exit_slippage_bps * 2,
        ).canonical_hash(),
    )
    baseline_metrics = calculate_pooled_backtest_metrics(
        tuple(cycle.net_pnl for cycle in baseline_cycles),
        net_pnl=baseline.final_equity - baseline.initial_quote,
        maximum_drawdown=Decimal("0.02"),
        annualized_daily_sharpe=Decimal("2"),
        calmar_ratio=Decimal("2"),
        bootstrap_samples=bootstrap_samples,
    )
    stress_metrics = calculate_pooled_backtest_metrics(
        tuple(cycle.net_pnl for cycle in stressed_cycles),
        net_pnl=stressed.final_equity - stressed.initial_quote,
        maximum_drawdown=Decimal("0.02"),
        annualized_daily_sharpe=Decimal("2"),
        calmar_ratio=Decimal("2"),
        bootstrap_samples=bootstrap_samples,
    )
    return BinanceBacktestReport(
        baseline=baseline,
        baseline_metrics=baseline_metrics,
        double_slippage=stressed,
        double_slippage_metrics=stress_metrics,
        gate=BacktestGateDecision(status="NO_GO", failures=("test",)),
    )


def _cycles(
    symbol: str,
    start: datetime,
    pnl: Decimal,
) -> tuple[CompletedCycle, ...]:
    return tuple(
        CompletedCycle(
            symbol=symbol,
            opened_at=start + timedelta(minutes=index * 2),
            closed_at=start + timedelta(minutes=index * 2 + 1),
            entry_quantity=Decimal("1"),
            entry_vwap=Decimal("100"),
            exit_vwap=Decimal("100") + pnl,
            gross_pnl=pnl,
            fees=Decimal("0"),
            net_pnl=pnl,
            return_fraction=pnl / Decimal("100"),
            entry_reason="test_entry",
            exit_reason="test_exit",
        )
        for index in range(2)
    )


def _result(
    *,
    symbol: str,
    start: datetime,
    end: datetime,
    strategy_name: str,
    strategy_version: str,
    initial_quote: Decimal,
    cycles: tuple[CompletedCycle, ...],
    data_hash: str,
    config_hash: str,
) -> BacktestResult:
    net_pnl = sum((cycle.net_pnl for cycle in cycles), start=Decimal("0"))
    run_hash = hashlib.sha256(f"{data_hash}:{config_hash}:{strategy_version}".encode()).hexdigest()
    return BacktestResult(
        symbol=symbol,
        engine_version="binance-event-sim-v1",
        strategy_name=strategy_name,
        strategy_version=strategy_version,
        started_at=start,
        ended_at=end - timedelta(microseconds=1),
        initial_quote=initial_quote,
        final_quote=initial_quote + net_pnl,
        final_base_quantity=Decimal("0"),
        final_mark_price=Decimal("100"),
        final_equity=initial_quote + net_pnl,
        maximum_drawdown=Decimal("0.02"),
        fills=(),
        orders=(),
        cycles=cycles,
        equity_curve=(),
        data_hash=data_hash,
        config_hash=config_hash,
        run_hash=run_hash,
        event_count=1000,
    )


def _dates(start: datetime, end: datetime) -> tuple[date, ...]:
    first = start.date()
    last = (end - timedelta(microseconds=1)).date()
    return tuple(first + timedelta(days=offset) for offset in range((last - first).days + 1))


def _manifest(
    symbol: str,
    interval: str,
    trading_date: date,
) -> ArchiveDayManifest:
    identity = f"{symbol}:{interval}:{trading_date}"
    return ArchiveDayManifest(
        symbol=symbol,
        interval=interval,
        trading_date=trading_date,
        source_url=f"https://data.binance.vision/{identity}.zip",
        checksum_sha256=hashlib.sha256(identity.encode()).hexdigest(),
        archive_bytes=1000,
        row_count=288 if interval == "5m" else 1000,
    )


async def test_orchestrator_resumes_oos_and_never_opens_holdout_implicitly(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        await _setup(session)
    settings = Settings(database_url=migrated_database_url, binance_enabled=True)
    spec = _spec()
    runner = FakeResearchRunner()
    await register_binance_research(
        settings=settings,
        spec=spec,
        session_factory=db_sessions,
    )

    async with db_sessions.begin() as session:
        await session.execute(
            update(CryptoSymbol)
            .where(CryptoSymbol.symbol == "ETHUSDT")
            .values(price_tick=Decimal("0.10"))
        )
    with pytest.raises(ResearchLedgerError, match="filters changed"):
        await run_binance_research_oos(
            settings=settings,
            reference=spec.experiment_key,
            session_factory=db_sessions,
            runner=runner,
        )
    assert not runner.calls
    async with db_sessions.begin() as session:
        await session.execute(
            update(CryptoSymbol)
            .where(CryptoSymbol.symbol == "ETHUSDT")
            .values(price_tick=Decimal("0.01"))
        )

    with pytest.raises(ResearchLedgerError, match="holdout is locked"):
        await run_binance_research_holdout(
            settings=settings,
            reference=spec.experiment_key,
            session_factory=db_sessions,
            runner=runner,
        )
    assert not runner.calls

    oos = await run_binance_research_oos(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        runner=runner,
    )
    selected = next(candidate for candidate in spec.candidates if candidate.fast_period == 10)
    assert oos.experiment.status == "oos_complete"
    assert oos.selection.selected_candidate_key == selected.candidate_key
    assert len(runner.calls) == 6

    await run_binance_research_oos(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        runner=runner,
    )
    assert len(runner.calls) == 6

    with pytest.raises(ResearchLedgerError, match="holdout is locked"):
        await run_binance_research_holdout(
            settings=settings,
            reference=spec.experiment_key,
            session_factory=db_sessions,
            runner=runner,
        )
    assert len(runner.calls) == 6

    await unlock_binance_research_holdout(
        settings=settings,
        reference=spec.experiment_key,
        plan_hash=spec.plan.plan_hash,
        candidate_set_hash=spec.candidate_set_hash,
        session_factory=db_sessions,
    )
    holdout = await run_binance_research_holdout(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        runner=runner,
    )
    assert len(runner.calls) == 8
    assert holdout.experiment.status == "complete"
    assert holdout.experiment.decision == "NO_GO"
    assert "completed_cycles<500" in holdout.experiment.decision_reasons

    repeated = await run_binance_research_holdout(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        runner=runner,
    )
    assert len(runner.calls) == 8
    assert repeated.experiment.decision_reasons == holdout.experiment.decision_reasons


async def test_default_archive_orchestrator_persists_only_manifests_and_results(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        await _setup(session)
    settings = Settings(database_url=migrated_database_url, binance_enabled=True)
    spec = _spec()
    archive_runner = FakeArchiveWindowRunner()
    await register_binance_research(
        settings=settings,
        spec=spec,
        session_factory=db_sessions,
    )

    outcome = await run_binance_research_oos(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        archive_runner=archive_runner,
    )

    assert outcome.experiment.status == "oos_complete"
    assert len(archive_runner.calls) == len(SYMBOLS)
    assert all(len(candidate_keys) == 3 for *_, candidate_keys in archive_runner.calls)
    async with db_sessions() as session:
        aggregate_trade_count = await session.scalar(select(func.count(CryptoAggregateTrade.id)))
        manifests = (await session.scalars(select(CryptoArchiveImport))).all()
        result_count = await session.scalar(select(func.count(CryptoResearchResult.id)))
    assert aggregate_trade_count == 0
    assert result_count == 12
    assert len(manifests) == 40
    assert {manifest.status for manifest in manifests} == {"stream_verified"}

    await run_binance_research_oos(
        settings=settings,
        reference=spec.experiment_key,
        session_factory=db_sessions,
        archive_runner=archive_runner,
    )
    assert len(archive_runner.calls) == len(SYMBOLS)


async def test_archive_oos_batches_are_bounded_resumable_and_finalize_once(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    async with db_sessions.begin() as session:
        await _setup(session)
    settings = Settings(database_url=migrated_database_url, binance_enabled=True)
    spec = _spec()
    archive_runner = FakeArchiveWindowRunner()
    await register_binance_research(
        settings=settings,
        spec=spec,
        session_factory=db_sessions,
    )

    first = await run_binance_research_oos_batch(
        settings=settings,
        reference=spec.experiment_key,
        candidate_batch_size=1,
        max_batches=1,
        session_factory=db_sessions,
        archive_runner=archive_runner,
    )
    assert first.experiment.status == "oos_running"
    assert first.processed_batches == 1
    assert first.processed_candidate_windows == 1
    assert first.completed_result_rows == 2
    assert first.expected_result_rows == 12
    assert first.complete is False
    assert first.selection is None

    second = await run_binance_research_oos_batch(
        settings=settings,
        reference=spec.experiment_key,
        candidate_batch_size=1,
        max_batches=2,
        session_factory=db_sessions,
        archive_runner=archive_runner,
    )
    assert second.experiment.status == "oos_running"
    assert second.completed_result_rows == 6
    assert second.complete is False

    final = await run_binance_research_oos_batch(
        settings=settings,
        reference=spec.experiment_key,
        candidate_batch_size=1,
        max_batches=3,
        session_factory=db_sessions,
        archive_runner=archive_runner,
    )
    assert final.experiment.status == "oos_complete"
    assert final.completed_result_rows == final.expected_result_rows == 12
    assert final.complete is True
    assert final.selection is not None
    assert len(archive_runner.calls) == 6
    assert all(len(candidate_keys) == 1 for *_, candidate_keys in archive_runner.calls)
