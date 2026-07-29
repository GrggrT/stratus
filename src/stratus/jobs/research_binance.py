from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.backtesting.engine import (
    BACKTEST_ENGINE_VERSION,
    BacktestConfig,
    BacktestResult,
)
from stratus.backtesting.metrics import BacktestMetrics, backtest_metrics_to_payload
from stratus.backtesting.research import (
    RESEARCH_PROTOCOL_VERSION,
    MovingAverageCandidateSpec,
    ResearchExecutionConfig,
    ResearchExperimentSpec,
    ResearchSelectionPolicy,
)
from stratus.backtesting.research_evaluation import (
    OosSelection,
    ResearchAggregate,
    evaluate_research_holdout,
    select_oos_candidate,
)
from stratus.collectors.binance_archive import BinanceArchiveError
from stratus.config import Settings, get_settings
from stratus.db.models import TradingVenue, VenueMode
from stratus.db.session import session_factory_scope
from stratus.jobs.backtest_binance import BinanceBacktestReport
from stratus.jobs.research_binance_archive import (
    BinanceArchiveResearchWindow,
    run_binance_archive_research_window,
)
from stratus.repositories.crypto_market_data import (
    crypto_symbol_ids,
    record_crypto_archive_stream_verification,
)
from stratus.repositories.crypto_research import (
    ResearchCandidateRecord,
    ResearchExperimentRecord,
    ResearchLedgerError,
    ResearchResultRecord,
    ResearchResultValue,
    append_research_result,
    complete_research_holdout,
    complete_research_oos,
    get_research_experiment,
    list_research_candidates,
    list_research_results,
    register_research_experiment,
    research_symbol_filter_payloads,
    start_research_holdout,
    start_research_oos,
    unlock_research_holdout,
)
from stratus.repositories.venue_runtime_control import get_venue_runtime_control

SessionFactory = async_sessionmaker[AsyncSession]


class ResearchBacktestRunner(Protocol):
    async def __call__(
        self,
        *,
        settings: Settings,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        config: BacktestConfig,
        strategy_factory: Any,
        session_factory: SessionFactory | None = None,
        bootstrap_samples: int = 2000,
    ) -> BinanceBacktestReport: ...


class ResearchArchiveWindowRunner(Protocol):
    async def __call__(
        self,
        *,
        settings: Settings,
        symbol: str,
        interval: str,
        start_time: datetime,
        end_time: datetime,
        config: BacktestConfig,
        candidates: tuple[ResearchCandidateRecord, ...],
        bootstrap_samples: int,
    ) -> BinanceArchiveResearchWindow: ...


@dataclass(frozen=True, slots=True)
class OosResearchRun:
    experiment: ResearchExperimentRecord
    selection: OosSelection


@dataclass(frozen=True, slots=True)
class OosResearchBatchRun:
    experiment: ResearchExperimentRecord
    processed_batches: int
    processed_candidate_windows: int
    completed_result_rows: int
    expected_result_rows: int
    complete: bool
    selection: OosSelection | None


@dataclass(frozen=True, slots=True)
class HoldoutResearchRun:
    experiment: ResearchExperimentRecord
    aggregate: ResearchAggregate


async def register_binance_research(
    *,
    settings: Settings,
    spec: ResearchExperimentSpec,
    session_factory: SessionFactory | None = None,
) -> ResearchExperimentRecord:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            return await register_research_experiment(session, spec)


async def run_binance_research_oos(
    *,
    settings: Settings,
    reference: str,
    session_factory: SessionFactory | None = None,
    runner: ResearchBacktestRunner | None = None,
    archive_runner: ResearchArchiveWindowRunner = run_binance_archive_research_window,
) -> OosResearchRun:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            await _require_backtest_venue(session)
            current = await get_research_experiment(session, reference, for_update=True)
            if current.status in ("registered", "oos_running"):
                experiment = await start_research_oos(session, reference)
            elif current.status in (
                "oos_complete",
                "holdout_unlocked",
                "holdout_running",
                "complete",
            ):
                experiment = current
            else:
                raise ResearchLedgerError(f"cannot run OOS from research status {current.status}")
            candidates = await list_research_candidates(session, experiment.id)
            configs = await _load_symbol_configs(session, experiment)
            existing = await list_research_results(session, experiment.id, phase="oos")
            bootstrap_samples = experiment.selection.bootstrap_samples

        if experiment.status in ("registered", "oos_running"):
            identities = {_result_identity(result) for result in existing}
            if runner is None:
                for fold in experiment.plan.folds:
                    for symbol in experiment.symbols:
                        pending = tuple(
                            candidate
                            for candidate in candidates
                            if not _result_pair(
                                candidate.candidate_key,
                                symbol,
                                "oos",
                                fold.number,
                            )
                            <= identities
                        )
                        if not pending:
                            continue
                        window = await archive_runner(
                            settings=settings,
                            symbol=symbol,
                            interval=experiment.interval,
                            start_time=fold.test.start,
                            end_time=fold.test.end,
                            config=configs[symbol],
                            candidates=pending,
                            bootstrap_samples=bootstrap_samples,
                        )
                        await _persist_archive_window(
                            sessions,
                            reference=experiment.experiment_key,
                            candidates=pending,
                            symbol=symbol,
                            phase="oos",
                            fold_number=fold.number,
                            window_start=fold.test.start,
                            window_end=fold.test.end,
                            config=configs[symbol],
                            window=window,
                        )
                        for candidate in pending:
                            identities.update(
                                _result_pair(
                                    candidate.candidate_key,
                                    symbol,
                                    "oos",
                                    fold.number,
                                )
                            )
            else:
                for candidate in candidates:
                    for fold in experiment.plan.folds:
                        for symbol in experiment.symbols:
                            pair = _result_pair(
                                candidate.candidate_key,
                                symbol,
                                "oos",
                                fold.number,
                            )
                            if pair <= identities:
                                continue
                            report = await runner(
                                settings=settings,
                                symbol=symbol,
                                interval=experiment.interval,
                                start_time=fold.test.start,
                                end_time=fold.test.end,
                                config=configs[symbol],
                                strategy_factory=candidate.spec.build_strategy,
                                session_factory=sessions,
                                bootstrap_samples=bootstrap_samples,
                            )
                            await _persist_report(
                                sessions,
                                reference=experiment.experiment_key,
                                candidate=candidate,
                                symbol=symbol,
                                phase="oos",
                                fold_number=fold.number,
                                window_start=fold.test.start,
                                window_end=fold.test.end,
                                config=configs[symbol],
                                report=report,
                            )
                            identities.update(pair)

        async with sessions.begin() as session:
            experiment = await get_research_experiment(session, reference, for_update=True)
            candidates = await list_research_candidates(session, experiment.id)
            results = await list_research_results(session, experiment.id, phase="oos")
            selection = select_oos_candidate(
                candidates,
                results,
                expected_symbols=experiment.symbols,
                policy=experiment.selection,
                bootstrap_samples=bootstrap_samples,
            )
            if experiment.status == "oos_running":
                experiment = await complete_research_oos(
                    session,
                    experiment.experiment_key,
                    selected_candidate_key=selection.selected_candidate_key,
                    no_go_reasons=selection.failures,
                )
            elif experiment.selected_candidate_key != selection.selected_candidate_key:
                raise ResearchLedgerError(
                    "persisted OOS selection differs from deterministic evaluation"
                )
            return OosResearchRun(experiment=experiment, selection=selection)


async def run_binance_research_oos_batch(
    *,
    settings: Settings,
    reference: str,
    candidate_batch_size: int = 1,
    max_batches: int = 1,
    session_factory: SessionFactory | None = None,
    archive_runner: ResearchArchiveWindowRunner = run_binance_archive_research_window,
) -> OosResearchBatchRun:
    if candidate_batch_size <= 0 or max_batches <= 0:
        raise ValueError("OOS batch limits must be positive")
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            await _require_backtest_venue(session)
            current = await get_research_experiment(session, reference, for_update=True)
            if current.status in ("registered", "oos_running"):
                experiment = await start_research_oos(session, reference)
            elif current.status in (
                "oos_complete",
                "holdout_unlocked",
                "holdout_running",
                "complete",
            ):
                experiment = current
            else:
                raise ResearchLedgerError(
                    f"cannot run OOS batch from research status {current.status}"
                )
            candidates = await list_research_candidates(session, experiment.id)
            configs = await _load_symbol_configs(session, experiment)
            existing = await list_research_results(session, experiment.id, phase="oos")
            bootstrap_samples = experiment.selection.bootstrap_samples

        expected_result_rows = (
            len(candidates) * len(experiment.plan.folds) * len(experiment.symbols) * 2
        )
        identities = {_result_identity(result) for result in existing}
        processed_batches = 0
        processed_candidate_windows = 0
        if experiment.status in ("registered", "oos_running"):
            for fold in experiment.plan.folds:
                if processed_batches >= max_batches:
                    break
                for symbol in experiment.symbols:
                    if processed_batches >= max_batches:
                        break
                    pending = tuple(
                        candidate
                        for candidate in candidates
                        if not _result_pair(
                            candidate.candidate_key,
                            symbol,
                            "oos",
                            fold.number,
                        )
                        <= identities
                    )
                    for offset in range(0, len(pending), candidate_batch_size):
                        if processed_batches >= max_batches:
                            break
                        batch = pending[offset : offset + candidate_batch_size]
                        window = await archive_runner(
                            settings=settings,
                            symbol=symbol,
                            interval=experiment.interval,
                            start_time=fold.test.start,
                            end_time=fold.test.end,
                            config=configs[symbol],
                            candidates=batch,
                            bootstrap_samples=bootstrap_samples,
                        )
                        await _persist_archive_window(
                            sessions,
                            reference=experiment.experiment_key,
                            candidates=batch,
                            symbol=symbol,
                            phase="oos",
                            fold_number=fold.number,
                            window_start=fold.test.start,
                            window_end=fold.test.end,
                            config=configs[symbol],
                            window=window,
                        )
                        for candidate in batch:
                            identities.update(
                                _result_pair(
                                    candidate.candidate_key,
                                    symbol,
                                    "oos",
                                    fold.number,
                                )
                            )
                        processed_batches += 1
                        processed_candidate_windows += len(batch)

        complete = len(identities) == expected_result_rows
        if complete:
            outcome = await run_binance_research_oos(
                settings=settings,
                reference=reference,
                session_factory=sessions,
                archive_runner=archive_runner,
            )
            return OosResearchBatchRun(
                experiment=outcome.experiment,
                processed_batches=processed_batches,
                processed_candidate_windows=processed_candidate_windows,
                completed_result_rows=len(identities),
                expected_result_rows=expected_result_rows,
                complete=True,
                selection=outcome.selection,
            )

        async with sessions() as session:
            latest = await get_research_experiment(session, reference)
        return OosResearchBatchRun(
            experiment=latest,
            processed_batches=processed_batches,
            processed_candidate_windows=processed_candidate_windows,
            completed_result_rows=len(identities),
            expected_result_rows=expected_result_rows,
            complete=False,
            selection=None,
        )


async def unlock_binance_research_holdout(
    *,
    settings: Settings,
    reference: str,
    plan_hash: str,
    candidate_set_hash: str,
    session_factory: SessionFactory | None = None,
) -> ResearchExperimentRecord:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            return await unlock_research_holdout(
                session,
                reference,
                plan_hash=plan_hash,
                candidate_set_hash=candidate_set_hash,
            )


async def run_binance_research_holdout(
    *,
    settings: Settings,
    reference: str,
    session_factory: SessionFactory | None = None,
    runner: ResearchBacktestRunner | None = None,
    archive_runner: ResearchArchiveWindowRunner = run_binance_archive_research_window,
) -> HoldoutResearchRun:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            await _require_backtest_venue(session)
            current = await get_research_experiment(session, reference, for_update=True)
            if current.status in ("holdout_unlocked", "holdout_running"):
                experiment = await start_research_holdout(session, reference)
            elif current.status == "complete" and current.selected_candidate_key is not None:
                experiment = current
            else:
                raise ResearchLedgerError(
                    "holdout is locked; run OOS and explicitly unlock it first"
                )
            bootstrap_samples = experiment.selection.bootstrap_samples
            candidates = await list_research_candidates(session, experiment.id)
            oos_results = await list_research_results(session, experiment.id, phase="oos")
            oos_selection = select_oos_candidate(
                candidates,
                oos_results,
                expected_symbols=experiment.symbols,
                policy=experiment.selection,
                bootstrap_samples=bootstrap_samples,
            )
            if oos_selection.selected_candidate_key != experiment.selected_candidate_key:
                raise ResearchLedgerError(
                    "holdout candidate differs from deterministic OOS selection"
                )
            selected = next(
                item
                for item in oos_selection.candidates
                if item.candidate_key == experiment.selected_candidate_key
            )
            if not selected.eligible:
                raise ResearchLedgerError("selected OOS candidate is no longer eligible")
            candidate = next(
                item
                for item in candidates
                if item.candidate_key == experiment.selected_candidate_key
            )
            configs = await _load_symbol_configs(session, experiment)
            existing = await list_research_results(session, experiment.id, phase="holdout")

        if experiment.status == "holdout_running":
            identities = {_result_identity(result) for result in existing}
            holdout = experiment.plan.final_holdout
            for symbol in experiment.symbols:
                pair = _result_pair(
                    candidate.candidate_key,
                    symbol,
                    "holdout",
                    0,
                )
                if pair <= identities:
                    continue
                if runner is None:
                    window = await archive_runner(
                        settings=settings,
                        symbol=symbol,
                        interval=experiment.interval,
                        start_time=holdout.start,
                        end_time=holdout.end,
                        config=configs[symbol],
                        candidates=(candidate,),
                        bootstrap_samples=bootstrap_samples,
                    )
                    await _persist_archive_window(
                        sessions,
                        reference=experiment.experiment_key,
                        candidates=(candidate,),
                        symbol=symbol,
                        phase="holdout",
                        fold_number=0,
                        window_start=holdout.start,
                        window_end=holdout.end,
                        config=configs[symbol],
                        window=window,
                    )
                else:
                    report = await runner(
                        settings=settings,
                        symbol=symbol,
                        interval=experiment.interval,
                        start_time=holdout.start,
                        end_time=holdout.end,
                        config=configs[symbol],
                        strategy_factory=candidate.spec.build_strategy,
                        session_factory=sessions,
                        bootstrap_samples=bootstrap_samples,
                    )
                    await _persist_report(
                        sessions,
                        reference=experiment.experiment_key,
                        candidate=candidate,
                        symbol=symbol,
                        phase="holdout",
                        fold_number=0,
                        window_start=holdout.start,
                        window_end=holdout.end,
                        config=configs[symbol],
                        report=report,
                    )
                identities.update(pair)

        async with sessions.begin() as session:
            experiment = await get_research_experiment(session, reference, for_update=True)
            candidates = await list_research_candidates(session, experiment.id)
            oos_results = await list_research_results(session, experiment.id, phase="oos")
            oos_selection = select_oos_candidate(
                candidates,
                oos_results,
                expected_symbols=experiment.symbols,
                policy=experiment.selection,
                bootstrap_samples=bootstrap_samples,
            )
            selected = next(
                item
                for item in oos_selection.candidates
                if item.candidate_key == experiment.selected_candidate_key
            )
            holdout_results = await list_research_results(session, experiment.id, phase="holdout")
            aggregate, decision = evaluate_research_holdout(
                selected,
                holdout_results,
                expected_symbols=experiment.symbols,
                policy=experiment.selection,
                bootstrap_samples=bootstrap_samples,
            )
            if experiment.status == "holdout_running":
                experiment = await complete_research_holdout(
                    session,
                    experiment.experiment_key,
                    decision=decision.status,  # type: ignore[arg-type]
                    reasons=decision.failures,
                )
            elif (
                experiment.decision != decision.status
                or experiment.decision_reasons != decision.failures
            ):
                raise ResearchLedgerError(
                    "persisted holdout decision differs from deterministic evaluation"
                )
            return HoldoutResearchRun(experiment=experiment, aggregate=aggregate)


async def get_binance_research_status(
    *,
    settings: Settings,
    reference: str,
    session_factory: SessionFactory | None = None,
) -> tuple[
    ResearchExperimentRecord,
    tuple[ResearchCandidateRecord, ...],
    tuple[ResearchResultRecord, ...],
]:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            experiment = await get_research_experiment(session, reference)
            candidates = await list_research_candidates(session, experiment.id)
            results = await list_research_results(session, experiment.id)
            return experiment, candidates, results


async def _load_symbol_configs(
    session: AsyncSession,
    experiment: ResearchExperimentRecord,
) -> dict[str, BacktestConfig]:
    current_filters = await research_symbol_filter_payloads(session, experiment.symbols)
    if current_filters != experiment.symbol_filters:
        raise ResearchLedgerError(
            "Binance symbol filters changed after research registration; create a new experiment"
        )
    configs: dict[str, BacktestConfig] = {}
    for symbol in experiment.symbols:
        filters = experiment.symbol_filters[symbol]
        configs[symbol] = experiment.execution.build_backtest_config(
            price_tick=_filter_decimal(filters, "price_tick"),
            quantity_step=_filter_decimal(filters, "quantity_step"),
            minimum_quantity=_filter_decimal(filters, "minimum_quantity"),
            minimum_notional=_filter_decimal(filters, "minimum_notional"),
        )
    return configs


async def _require_backtest_venue(session: AsyncSession) -> None:
    control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
    if not control.enabled or control.mode is not VenueMode.BACKTEST:
        raise ResearchLedgerError(
            "Binance research requires enabled binance_spot/backtest venue mode"
        )


def _filter_decimal(filters: dict[str, str | None], field: str) -> Decimal:
    value = filters[field]
    if value is None:
        raise ResearchLedgerError(f"registered research filter is missing: {field}")
    return Decimal(value)


async def _persist_report(
    sessions: SessionFactory,
    *,
    reference: str,
    candidate: ResearchCandidateRecord,
    symbol: str,
    phase: str,
    fold_number: int,
    window_start: datetime,
    window_end: datetime,
    config: BacktestConfig,
    report: BinanceBacktestReport,
) -> None:
    async with sessions.begin() as session:
        await _append_report(
            session,
            reference=reference,
            candidate=candidate,
            symbol=symbol,
            phase=phase,
            fold_number=fold_number,
            window_start=window_start,
            window_end=window_end,
            config=config,
            report=report,
        )


async def _persist_archive_window(
    sessions: SessionFactory,
    *,
    reference: str,
    candidates: tuple[ResearchCandidateRecord, ...],
    symbol: str,
    phase: str,
    fold_number: int,
    window_start: datetime,
    window_end: datetime,
    config: BacktestConfig,
    window: BinanceArchiveResearchWindow,
) -> None:
    expected_keys = {candidate.candidate_key for candidate in candidates}
    if set(window.reports) != expected_keys:
        raise ResearchLedgerError("archive research window returned a different candidate set")
    expected_dates = _included_dates(window_start, window_end)
    expected_manifests = {
        (trading_date, interval)
        for trading_date in expected_dates
        for interval in ("5m", "aggTrades")
    }
    actual_manifests = {(manifest.trading_date, manifest.interval) for manifest in window.manifests}
    if actual_manifests != expected_manifests or any(
        manifest.symbol != symbol for manifest in window.manifests
    ):
        raise ResearchLedgerError("archive research manifest set is incomplete")

    async with sessions.begin() as session:
        symbol_ids = await crypto_symbol_ids(session, (symbol,))
        symbol_id = symbol_ids.get(symbol)
        if symbol_id is None:
            raise ResearchLedgerError("archive research symbol metadata is missing")
        verified_at = datetime.now(UTC)
        for manifest in window.manifests:
            await record_crypto_archive_stream_verification(
                session,
                symbol_id=symbol_id,
                interval=manifest.interval,
                trading_date=manifest.trading_date,
                source_url=manifest.source_url,
                checksum_sha256=manifest.checksum_sha256,
                archive_bytes=manifest.archive_bytes,
                row_count=manifest.row_count,
                verified_at=verified_at,
            )
        for candidate in candidates:
            await _append_report(
                session,
                reference=reference,
                candidate=candidate,
                symbol=symbol,
                phase=phase,
                fold_number=fold_number,
                window_start=window_start,
                window_end=window_end,
                config=config,
                report=window.reports[candidate.candidate_key],
            )


async def _append_report(
    session: AsyncSession,
    *,
    reference: str,
    candidate: ResearchCandidateRecord,
    symbol: str,
    phase: str,
    fold_number: int,
    window_start: datetime,
    window_end: datetime,
    config: BacktestConfig,
    report: BinanceBacktestReport,
) -> None:
    _validate_report(
        report,
        candidate,
        symbol,
        config=config,
        window_start=window_start,
        window_end=window_end,
    )
    values = (
        (
            "baseline",
            _result_value(
                report.baseline,
                report.baseline_metrics,
                phase=phase,
                fold_number=fold_number,
                stress_case="baseline",
                window_start=window_start,
                window_end=window_end,
            ),
        ),
        (
            "double_slippage",
            _result_value(
                report.double_slippage,
                report.double_slippage_metrics,
                phase=phase,
                fold_number=fold_number,
                stress_case="double_slippage",
                window_start=window_start,
                window_end=window_end,
            ),
        ),
    )
    for _, value in values:
        await append_research_result(
            session,
            reference=reference,
            candidate_key=candidate.candidate_key,
            symbol=symbol,
            value=value,
        )


def _result_value(
    result: BacktestResult,
    metrics: BacktestMetrics,
    *,
    phase: str,
    fold_number: int,
    stress_case: str,
    window_start: datetime,
    window_end: datetime,
) -> ResearchResultValue:
    return ResearchResultValue(
        phase=phase,  # type: ignore[arg-type]
        fold_number=fold_number,
        stress_case=stress_case,  # type: ignore[arg-type]
        window_start=window_start,
        window_end=window_end,
        run_hash=result.run_hash,
        data_hash=result.data_hash,
        config_hash=result.config_hash,
        event_count=result.event_count,
        metrics=backtest_metrics_to_payload(metrics),
        cycle_net_pnls=tuple(cycle.net_pnl for cycle in result.cycles),
    )


def _validate_report(
    report: BinanceBacktestReport,
    candidate: ResearchCandidateRecord,
    symbol: str,
    *,
    config: BacktestConfig,
    window_start: datetime,
    window_end: datetime,
) -> None:
    expected_stress_config = replace(
        config,
        entry_slippage_bps=config.entry_slippage_bps * 2,
        exit_slippage_bps=config.exit_slippage_bps * 2,
    )
    pairs = (
        (report.baseline, report.baseline_metrics, config.canonical_hash()),
        (
            report.double_slippage,
            report.double_slippage_metrics,
            expected_stress_config.canonical_hash(),
        ),
    )
    for result, metrics, expected_config_hash in pairs:
        if result.symbol != symbol:
            raise ResearchLedgerError("research runner returned a different symbol")
        if result.engine_version != BACKTEST_ENGINE_VERSION:
            raise ResearchLedgerError("research runner returned a different engine version")
        if result.strategy_name != candidate.strategy_name:
            raise ResearchLedgerError("research runner returned a different strategy")
        if result.strategy_version != candidate.strategy_version:
            raise ResearchLedgerError("research runner returned a different strategy version")
        if result.config_hash != expected_config_hash:
            raise ResearchLedgerError("research runner returned a different config hash")
        if not window_start <= result.started_at <= result.ended_at < window_end:
            raise ResearchLedgerError("research runner returned events outside the contract window")
        if result.event_count <= 0:
            raise ResearchLedgerError("research runner returned no events")
        if metrics.completed_cycles != len(result.cycles):
            raise ResearchLedgerError("research runner cycle metric is inconsistent")
        if metrics.net_pnl != result.final_equity - result.initial_quote:
            raise ResearchLedgerError("research runner net PnL metric is inconsistent")
    if report.baseline.data_hash != report.double_slippage.data_hash:
        raise ResearchLedgerError("research baseline/stress consumed different data")
    if report.baseline.event_count != report.double_slippage.event_count:
        raise ResearchLedgerError("research baseline/stress event counts differ")
    if report.baseline.run_hash == report.double_slippage.run_hash:
        raise ResearchLedgerError("research slippage stress did not change the run hash")


def _result_identity(result: ResearchResultRecord) -> tuple[str, str, str, int, str]:
    return (
        result.candidate_key,
        result.symbol,
        result.phase,
        result.fold_number,
        result.stress_case,
    )


def _result_pair(
    candidate_key: str,
    symbol: str,
    phase: str,
    fold_number: int,
) -> set[tuple[str, str, str, int, str]]:
    return {
        (candidate_key, symbol, phase, fold_number, "baseline"),
        (candidate_key, symbol, phase, fold_number, "double_slippage"),
    }


def _included_dates(start_time: datetime, end_time: datetime) -> set[date]:
    first = start_time.astimezone(UTC).date()
    last = (end_time.astimezone(UTC) - timedelta(microseconds=1)).date()
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("research timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("research timestamp must include a UTC offset")
    return parsed


def _parse_days(payload: dict[str, Any], key: str) -> timedelta:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"research {key} must be a positive integer")
    return timedelta(days=value)


def research_spec_from_payload(payload: dict[str, Any]) -> ResearchExperimentSpec:
    expected = {
        "name",
        "symbols",
        "interval",
        "dataset_start",
        "dataset_end",
        "windows_days",
        "execution",
        "selection",
        "candidates",
    }
    if set(payload) != expected:
        raise ValueError("research specification fields are invalid")
    name = payload["name"]
    symbols = payload["symbols"]
    interval = payload["interval"]
    windows = payload["windows_days"]
    execution = payload["execution"]
    selection = payload["selection"]
    candidates = payload["candidates"]
    if not isinstance(name, str) or not isinstance(interval, str):
        raise ValueError("research name and interval must be strings")
    if not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols):
        raise ValueError("research symbols must be a string list")
    if not isinstance(windows, dict):
        raise ValueError("research windows_days must be an object")
    window_keys = {"minimum_train", "validation", "test", "step", "final_holdout"}
    if set(windows) != window_keys:
        raise ValueError("research windows_days fields are invalid")
    if not isinstance(execution, dict) or not isinstance(selection, dict):
        raise ValueError("research execution/selection must be objects")
    if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
        raise ValueError("research candidates must be an object list")
    return ResearchExperimentSpec(
        name=name,
        symbols=tuple(symbols),
        interval=interval,
        dataset_start=_parse_timestamp(payload["dataset_start"]),
        dataset_end=_parse_timestamp(payload["dataset_end"]),
        minimum_train=_parse_days(windows, "minimum_train"),
        validation=_parse_days(windows, "validation"),
        test=_parse_days(windows, "test"),
        step=_parse_days(windows, "step"),
        final_holdout=_parse_days(windows, "final_holdout"),
        execution=ResearchExecutionConfig.from_payload(execution),
        selection=ResearchSelectionPolicy.from_payload(selection),
        candidates=tuple(MovingAverageCandidateSpec.from_payload(item) for item in candidates),
    )


def _json_default(value: object) -> object:
    if isinstance(value, (datetime, Decimal)):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _status_payload(
    experiment: ResearchExperimentRecord,
    candidates: tuple[ResearchCandidateRecord, ...],
    results: tuple[ResearchResultRecord, ...],
) -> dict[str, Any]:
    return {
        "experiment": asdict(experiment),
        "candidate_count": len(candidates),
        "result_count": len(results),
        "results_by_phase": {
            phase: sum(result.phase == phase for result in results) for phase in ("oos", "holdout")
        },
    }


def _validated_spec_payload(spec: ResearchExperimentSpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "experiment_key": spec.experiment_key,
        "protocol_version": RESEARCH_PROTOCOL_VERSION,
        "plan_hash": spec.plan.plan_hash,
        "candidate_set_hash": spec.candidate_set_hash,
        "symbols": spec.symbols,
        "interval": spec.interval,
        "fold_count": len(spec.plan.folds),
        "candidate_count": len(spec.candidates),
        "expected_oos_result_rows": (
            len(spec.candidates) * len(spec.plan.folds) * len(spec.symbols) * 2
        ),
        "holdout_start": spec.plan.final_holdout.start,
        "holdout_end": spec.plan.final_holdout.end,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run immutable Binance walk-forward strategy research"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("validate-spec", "register"):
        child = subparsers.add_parser(command)
        child.add_argument("--spec", type=Path, required=True)
    for command in ("run-holdout", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--experiment", required=True)
    run_oos = subparsers.add_parser("run-oos")
    run_oos.add_argument("--experiment", required=True)
    run_oos.add_argument("--candidate-batch-size", type=int, default=1)
    run_oos.add_argument("--max-batches", type=int, default=1)
    unlock = subparsers.add_parser("unlock-holdout")
    unlock.add_argument("--experiment", required=True)
    unlock.add_argument("--plan-hash", required=True)
    unlock.add_argument("--candidate-set-hash", required=True)
    arguments = parser.parse_args()
    settings = get_settings()

    async def execute() -> dict[str, Any]:
        if arguments.command in ("validate-spec", "register"):
            raw = json.loads(arguments.spec.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("research specification root must be an object")
            spec = research_spec_from_payload(raw)
            if arguments.command == "validate-spec":
                return _validated_spec_payload(spec)
            return asdict(
                await register_binance_research(
                    settings=settings,
                    spec=spec,
                )
            )
        if arguments.command == "run-oos":
            return asdict(
                await run_binance_research_oos_batch(
                    settings=settings,
                    reference=arguments.experiment,
                    candidate_batch_size=arguments.candidate_batch_size,
                    max_batches=arguments.max_batches,
                )
            )
        if arguments.command == "unlock-holdout":
            return asdict(
                await unlock_binance_research_holdout(
                    settings=settings,
                    reference=arguments.experiment,
                    plan_hash=arguments.plan_hash,
                    candidate_set_hash=arguments.candidate_set_hash,
                )
            )
        if arguments.command == "run-holdout":
            return asdict(
                await run_binance_research_holdout(
                    settings=settings,
                    reference=arguments.experiment,
                )
            )
        experiment, candidates, results = await get_binance_research_status(
            settings=settings,
            reference=arguments.experiment,
        )
        return _status_payload(experiment, candidates, results)

    try:
        result = asyncio.run(execute())
    except BinanceArchiveError as exc:
        print(
            json.dumps(
                {"status": "blocked", "kind": exc.kind, "error": str(exc)},
                sort_keys=True,
            )
        )
        raise SystemExit(2) from None
    print(json.dumps(result, default=_json_default, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
