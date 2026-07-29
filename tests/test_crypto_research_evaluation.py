from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.backtesting.metrics import (
    backtest_metrics_to_payload,
    calculate_pooled_backtest_metrics,
)
from stratus.backtesting.research import (
    MovingAverageCandidateSpec,
    ResearchSelectionPolicy,
)
from stratus.backtesting.research_evaluation import (
    aggregate_research_results,
    evaluate_research_holdout,
    select_oos_candidate,
)
from stratus.repositories.crypto_research import (
    ResearchCandidateRecord,
    ResearchLedgerError,
    ResearchResultRecord,
)

START = datetime(2025, 1, 1, tzinfo=UTC)
SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _candidate(identifier: int, fast: int, slow: int) -> ResearchCandidateRecord:
    spec = MovingAverageCandidateSpec(fast, slow)
    return ResearchCandidateRecord(
        id=identifier,
        experiment_id=1,
        candidate_key=spec.candidate_key,
        strategy_name="moving_average_cross",
        strategy_version=spec.strategy_version,
        spec=spec,
    )


def _result(
    candidate: ResearchCandidateRecord,
    *,
    symbol: str,
    stress: str,
    pnl: str,
    phase: str = "oos",
    fold_number: int = 1,
    data_hash: str = "a" * 64,
) -> ResearchResultRecord:
    cycle_pnls = (Decimal(pnl), Decimal(pnl))
    net_pnl = sum(cycle_pnls, start=Decimal("0"))
    metrics = calculate_pooled_backtest_metrics(
        cycle_pnls,
        net_pnl=net_pnl,
        maximum_drawdown=Decimal("0.02"),
        annualized_daily_sharpe=Decimal("2"),
        calmar_ratio=Decimal("2"),
        bootstrap_samples=100,
    )
    suffix = str((candidate.id + (1 if stress == "baseline" else 5)) % 10)
    return ResearchResultRecord(
        id=candidate.id * 100 + len(symbol) + (0 if stress == "baseline" else 1),
        experiment_id=1,
        candidate_id=candidate.id,
        candidate_key=candidate.candidate_key,
        symbol_id=1 if symbol == SYMBOLS[0] else 2,
        symbol=symbol,
        phase=phase,  # type: ignore[arg-type]
        fold_number=fold_number,
        stress_case=stress,  # type: ignore[arg-type]
        window_start=START,
        window_end=START + timedelta(days=30),
        run_hash=suffix * 64,
        data_hash=data_hash,
        config_hash=("b" if stress == "baseline" else "c") * 64,
        event_count=1000,
        metrics=backtest_metrics_to_payload(metrics),
        cycle_net_pnls=cycle_pnls,
    )


def _candidate_results(
    candidate: ResearchCandidateRecord,
    *,
    baseline_pnl: str,
    stress_pnl: str,
) -> tuple[ResearchResultRecord, ...]:
    return tuple(
        _result(candidate, symbol=symbol, stress=stress, pnl=pnl)
        for symbol in SYMBOLS
        for stress, pnl in (
            ("baseline", baseline_pnl),
            ("double_slippage", stress_pnl),
        )
    )


def test_oos_selection_requires_positive_parameter_plateau_and_multi_symbol_results() -> None:
    left = _candidate(1, 8, 50)
    target = _candidate(2, 10, 50)
    right = _candidate(3, 12, 50)
    candidates = (left, target, right)
    results = (
        *_candidate_results(left, baseline_pnl="1", stress_pnl="0.5"),
        *_candidate_results(target, baseline_pnl="2", stress_pnl="1.5"),
        *_candidate_results(right, baseline_pnl="1", stress_pnl="0.5"),
    )
    policy = ResearchSelectionPolicy(
        bootstrap_samples=100,
        minimum_oos_cycles=4,
        minimum_symbol_cycles=2,
        maximum_drawdown=Decimal("0.10"),
        minimum_positive_neighbors=2,
        maximum_symbol_profit_contribution=Decimal("0.80"),
    )

    selection = select_oos_candidate(
        candidates,
        results,
        expected_symbols=SYMBOLS,
        policy=policy,
        bootstrap_samples=100,
    )

    assert selection.selected_candidate_key == target.candidate_key
    selected = next(
        item for item in selection.candidates if item.candidate_key == target.candidate_key
    )
    assert selected.eligible
    assert set(selected.positive_neighbor_keys) == {left.candidate_key, right.candidate_key}
    assert selected.aggregate.maximum_symbol_profit_contribution == Decimal("0.5")


def test_research_aggregate_rejects_mismatched_stress_dataset() -> None:
    candidate = _candidate(1, 10, 50)
    results = list(_candidate_results(candidate, baseline_pnl="1", stress_pnl="0.5"))
    stress_index = next(
        index for index, result in enumerate(results) if result.stress_case == "double_slippage"
    )
    results[stress_index] = replace(results[stress_index], data_hash="f" * 64)

    with pytest.raises(ResearchLedgerError, match="different data"):
        aggregate_research_results(
            tuple(results),
            expected_symbols=SYMBOLS,
            bootstrap_samples=100,
        )


def test_positive_but_small_holdout_remains_no_go() -> None:
    left = _candidate(1, 8, 50)
    target = _candidate(2, 10, 50)
    right = _candidate(3, 12, 50)
    candidates = (left, target, right)
    oos_results = (
        *_candidate_results(left, baseline_pnl="1", stress_pnl="0.5"),
        *_candidate_results(target, baseline_pnl="2", stress_pnl="1.5"),
        *_candidate_results(right, baseline_pnl="1", stress_pnl="0.5"),
    )
    policy = ResearchSelectionPolicy(
        bootstrap_samples=100,
        minimum_oos_cycles=4,
        minimum_symbol_cycles=2,
        minimum_positive_neighbors=2,
    )
    selection = select_oos_candidate(
        candidates,
        oos_results,
        expected_symbols=SYMBOLS,
        policy=policy,
        bootstrap_samples=100,
    )
    selected = next(
        item for item in selection.candidates if item.candidate_key == target.candidate_key
    )
    holdout_results = tuple(
        replace(result, phase="holdout", fold_number=0)
        for result in _candidate_results(target, baseline_pnl="2", stress_pnl="1")
    )

    _, decision = evaluate_research_holdout(
        selected,
        holdout_results,
        expected_symbols=SYMBOLS,
        policy=policy,
        bootstrap_samples=100,
    )

    assert decision.status == "NO_GO"
    assert "completed_cycles<500" in decision.failures
    assert "symbol_or_regime_diversification_not_proven" not in decision.failures
    assert "parameter_stability_not_proven" not in decision.failures
