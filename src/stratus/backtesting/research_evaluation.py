from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from stratus.backtesting.metrics import (
    BacktestGateDecision,
    BacktestMetrics,
    backtest_metrics_from_payload,
    calculate_pooled_backtest_metrics,
    evaluate_holdout_gate,
)
from stratus.backtesting.research import ResearchSelectionPolicy
from stratus.repositories.crypto_research import (
    ResearchCandidateRecord,
    ResearchLedgerError,
    ResearchResultRecord,
)


@dataclass(frozen=True, slots=True)
class ResearchAggregate:
    baseline: BacktestMetrics
    double_slippage: BacktestMetrics
    symbol_baseline: dict[str, BacktestMetrics]
    symbol_double_slippage: dict[str, BacktestMetrics]
    maximum_symbol_profit_contribution: Decimal | None
    profitable_partitions: int
    partition_count: int


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_key: str
    aggregate: ResearchAggregate
    neighbor_keys: tuple[str, ...]
    positive_neighbor_keys: tuple[str, ...]
    failures: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class OosSelection:
    selected_candidate_key: str | None
    candidates: tuple[CandidateEvaluation, ...]
    failures: tuple[str, ...]


def aggregate_research_results(
    results: tuple[ResearchResultRecord, ...],
    *,
    expected_symbols: tuple[str, ...],
    bootstrap_samples: int = 2000,
) -> ResearchAggregate:
    if not results:
        raise ResearchLedgerError("research result set is empty")
    candidates = {result.candidate_key for result in results}
    if len(candidates) != 1:
        raise ResearchLedgerError("research aggregate must contain exactly one candidate")
    actual_symbols = {result.symbol for result in results}
    if actual_symbols != set(expected_symbols):
        raise ResearchLedgerError("research aggregate symbol set is incomplete")

    baseline_by_key = {
        _result_pair_key(result): result for result in results if result.stress_case == "baseline"
    }
    stress_by_key = {
        _result_pair_key(result): result
        for result in results
        if result.stress_case == "double_slippage"
    }
    if set(baseline_by_key) != set(stress_by_key):
        raise ResearchLedgerError("research baseline/stress result pairs are incomplete")
    for key, pair_baseline in baseline_by_key.items():
        stress = stress_by_key[key]
        if (
            pair_baseline.data_hash != stress.data_hash
            or pair_baseline.event_count != stress.event_count
        ):
            raise ResearchLedgerError("research baseline/stress pair consumed different data")

    symbol_baseline: dict[str, BacktestMetrics] = {}
    symbol_stress: dict[str, BacktestMetrics] = {}
    for symbol in expected_symbols:
        baseline_rows = tuple(
            result for result in baseline_by_key.values() if result.symbol == symbol
        )
        stress_rows = tuple(result for result in stress_by_key.values() if result.symbol == symbol)
        symbol_baseline[symbol] = _pool_rows(
            baseline_rows,
            bootstrap_samples=bootstrap_samples,
        )
        symbol_stress[symbol] = _pool_rows(
            stress_rows,
            bootstrap_samples=bootstrap_samples,
        )

    baseline_rows = tuple(baseline_by_key.values())
    stress_rows = tuple(stress_by_key.values())
    aggregate_baseline = _pool_rows(baseline_rows, bootstrap_samples=bootstrap_samples)
    aggregate_stress = _pool_rows(stress_rows, bootstrap_samples=bootstrap_samples)
    symbol_pnls = [metrics.net_pnl for metrics in symbol_baseline.values()]
    maximum_contribution = None
    if aggregate_baseline.net_pnl > 0:
        maximum_contribution = max(symbol_pnls) / aggregate_baseline.net_pnl
    profitable_partitions = sum(
        backtest_metrics_from_payload(result.metrics).net_pnl > 0 for result in baseline_rows
    )
    return ResearchAggregate(
        baseline=aggregate_baseline,
        double_slippage=aggregate_stress,
        symbol_baseline=symbol_baseline,
        symbol_double_slippage=symbol_stress,
        maximum_symbol_profit_contribution=maximum_contribution,
        profitable_partitions=profitable_partitions,
        partition_count=len(baseline_rows),
    )


def select_oos_candidate(
    candidates: tuple[ResearchCandidateRecord, ...],
    results: tuple[ResearchResultRecord, ...],
    *,
    expected_symbols: tuple[str, ...],
    policy: ResearchSelectionPolicy,
    bootstrap_samples: int = 2000,
) -> OosSelection:
    if not candidates:
        raise ResearchLedgerError("research candidate set is empty")
    results_by_candidate = {
        candidate.candidate_key: tuple(
            result for result in results if result.candidate_key == candidate.candidate_key
        )
        for candidate in candidates
    }
    aggregates = {
        key: aggregate_research_results(
            values,
            expected_symbols=expected_symbols,
            bootstrap_samples=bootstrap_samples,
        )
        for key, values in results_by_candidate.items()
    }
    neighbors = {
        candidate.candidate_key: _candidate_neighbors(candidate, candidates)
        for candidate in candidates
    }
    positive = {
        key: tuple(
            neighbor_key
            for neighbor_key in neighbor_keys
            if _has_positive_core(aggregates[neighbor_key])
        )
        for key, neighbor_keys in neighbors.items()
    }
    evaluations = tuple(
        CandidateEvaluation(
            candidate_key=candidate.candidate_key,
            aggregate=aggregates[candidate.candidate_key],
            neighbor_keys=neighbors[candidate.candidate_key],
            positive_neighbor_keys=positive[candidate.candidate_key],
            failures=_oos_failures(
                aggregates[candidate.candidate_key],
                expected_symbols=expected_symbols,
                policy=policy,
                positive_neighbor_count=len(positive[candidate.candidate_key]),
            ),
        )
        for candidate in candidates
    )
    eligible = [evaluation for evaluation in evaluations if evaluation.eligible]
    if not eligible:
        failures = ["no_oos_candidate_passed"]
        failures.extend(
            f"candidate:{item.candidate_key[:12]}:{','.join(item.failures)}" for item in evaluations
        )
        return OosSelection(
            selected_candidate_key=None,
            candidates=evaluations,
            failures=tuple(failures),
        )
    eligible.sort(key=lambda item: item.candidate_key)
    selected = max(eligible, key=_candidate_score)
    return OosSelection(
        selected_candidate_key=selected.candidate_key,
        candidates=evaluations,
        failures=(),
    )


def evaluate_research_holdout(
    selected_oos: CandidateEvaluation,
    holdout_results: tuple[ResearchResultRecord, ...],
    *,
    expected_symbols: tuple[str, ...],
    policy: ResearchSelectionPolicy,
    bootstrap_samples: int = 2000,
) -> tuple[ResearchAggregate, BacktestGateDecision]:
    aggregate = aggregate_research_results(
        holdout_results,
        expected_symbols=expected_symbols,
        bootstrap_samples=bootstrap_samples,
    )
    if any(result.candidate_key != selected_oos.candidate_key for result in holdout_results):
        raise ResearchLedgerError("holdout includes a non-selected candidate")
    profitable_symbols = all(
        aggregate.symbol_baseline[symbol].net_pnl > 0
        and aggregate.symbol_double_slippage[symbol].net_pnl > 0
        for symbol in expected_symbols
    )
    contribution = aggregate.maximum_symbol_profit_contribution
    diversification_passed = (
        len(expected_symbols) >= 2
        and profitable_symbols
        and contribution is not None
        and contribution <= policy.maximum_symbol_profit_contribution
    )
    parameter_stability_passed = (
        len(selected_oos.positive_neighbor_keys) >= policy.minimum_positive_neighbors
    )
    minimum_symbol_cycles = min(
        metrics.completed_cycles for metrics in aggregate.symbol_baseline.values()
    )
    decision = evaluate_holdout_gate(
        aggregate.baseline,
        aggregate.double_slippage,
        symbol_cycles=minimum_symbol_cycles,
        diversification_passed=diversification_passed,
        parameter_stability_passed=parameter_stability_passed,
    )
    failures = list(decision.failures)
    if aggregate.baseline.maximum_drawdown > policy.maximum_drawdown:
        failures.append(f"maximum_drawdown>{policy.maximum_drawdown}")
    if contribution is None or contribution > policy.maximum_symbol_profit_contribution:
        failures.append(
            f"maximum_symbol_profit_contribution>{policy.maximum_symbol_profit_contribution}"
        )
    deduplicated = tuple(dict.fromkeys(failures))
    return aggregate, BacktestGateDecision(
        status="GO" if not deduplicated else "NO_GO",
        failures=deduplicated,
    )


def _pool_rows(
    rows: tuple[ResearchResultRecord, ...],
    *,
    bootstrap_samples: int,
) -> BacktestMetrics:
    if not rows:
        raise ResearchLedgerError("research metrics partition is empty")
    parsed = [backtest_metrics_from_payload(row.metrics) for row in rows]
    for row, metrics in zip(rows, parsed, strict=True):
        if metrics.completed_cycles != len(row.cycle_net_pnls):
            raise ResearchLedgerError("research cycle count differs from stored PnLs")
    cycle_pnls = tuple(pnl for row in rows for pnl in row.cycle_net_pnls)
    sharpe = _conservative_optional(tuple(metrics.annualized_daily_sharpe for metrics in parsed))
    calmar = _conservative_optional(tuple(metrics.calmar_ratio for metrics in parsed))
    return calculate_pooled_backtest_metrics(
        cycle_pnls,
        net_pnl=sum((metrics.net_pnl for metrics in parsed), start=Decimal("0")),
        maximum_drawdown=max(metrics.maximum_drawdown for metrics in parsed),
        annualized_daily_sharpe=sharpe,
        calmar_ratio=calmar,
        bootstrap_samples=bootstrap_samples,
    )


def _conservative_optional(values: tuple[Decimal | None, ...]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return min(value for value in values if value is not None)


def _result_pair_key(result: ResearchResultRecord) -> tuple[str, int, str]:
    return result.symbol, result.fold_number, result.phase


def _candidate_neighbors(
    target: ResearchCandidateRecord,
    candidates: tuple[ResearchCandidateRecord, ...],
) -> tuple[str, ...]:
    compatible = tuple(
        candidate
        for candidate in candidates
        if candidate.spec.order_style is target.spec.order_style
        and candidate.spec.maker_offset_bps == target.spec.maker_offset_bps
    )
    neighbors: set[str] = set()
    same_slow = sorted(
        (
            candidate
            for candidate in compatible
            if candidate.spec.slow_period == target.spec.slow_period
        ),
        key=lambda candidate: candidate.spec.fast_period,
    )
    same_fast = sorted(
        (
            candidate
            for candidate in compatible
            if candidate.spec.fast_period == target.spec.fast_period
        ),
        key=lambda candidate: candidate.spec.slow_period,
    )
    for line in (same_slow, same_fast):
        index = line.index(target)
        if index > 0:
            neighbors.add(line[index - 1].candidate_key)
        if index + 1 < len(line):
            neighbors.add(line[index + 1].candidate_key)
    return tuple(sorted(neighbors))


def _has_positive_core(aggregate: ResearchAggregate) -> bool:
    lcb = aggregate.baseline.bootstrap_lcb_95_expectancy
    return (
        aggregate.baseline.net_pnl > 0
        and aggregate.double_slippage.net_pnl > 0
        and lcb is not None
        and lcb > 0
    )


def _oos_failures(
    aggregate: ResearchAggregate,
    *,
    expected_symbols: tuple[str, ...],
    policy: ResearchSelectionPolicy,
    positive_neighbor_count: int,
) -> tuple[str, ...]:
    failures: list[str] = []
    metrics = aggregate.baseline
    if metrics.completed_cycles < policy.minimum_oos_cycles:
        failures.append(f"completed_cycles<{policy.minimum_oos_cycles}")
    for symbol in expected_symbols:
        if aggregate.symbol_baseline[symbol].completed_cycles < policy.minimum_symbol_cycles:
            failures.append(f"{symbol}:cycles<{policy.minimum_symbol_cycles}")
        if aggregate.symbol_baseline[symbol].net_pnl <= 0:
            failures.append(f"{symbol}:net_pnl<=0")
        if aggregate.symbol_double_slippage[symbol].net_pnl <= 0:
            failures.append(f"{symbol}:double_slippage_net_pnl<=0")
    if metrics.net_pnl <= 0:
        failures.append("oos_net_pnl<=0")
    if aggregate.double_slippage.net_pnl <= 0:
        failures.append("oos_double_slippage_net_pnl<=0")
    if metrics.bootstrap_lcb_95_expectancy is None or metrics.bootstrap_lcb_95_expectancy <= 0:
        failures.append("oos_bootstrap_lcb_95_expectancy<=0")
    if metrics.maximum_drawdown > policy.maximum_drawdown:
        failures.append(f"maximum_drawdown>{policy.maximum_drawdown}")
    contribution = aggregate.maximum_symbol_profit_contribution
    if len(expected_symbols) < 2:
        failures.append("multi_symbol_diversification_not_proven")
    if contribution is None or contribution > policy.maximum_symbol_profit_contribution:
        failures.append(
            f"maximum_symbol_profit_contribution>{policy.maximum_symbol_profit_contribution}"
        )
    if positive_neighbor_count < policy.minimum_positive_neighbors:
        failures.append(f"positive_neighbors<{policy.minimum_positive_neighbors}")
    return tuple(failures)


def _candidate_score(evaluation: CandidateEvaluation) -> tuple[Decimal, Decimal, Decimal]:
    lcb = evaluation.aggregate.baseline.bootstrap_lcb_95_expectancy
    if lcb is None:
        raise ResearchLedgerError("eligible research candidate has no bootstrap LCB")
    return (
        lcb,
        evaluation.aggregate.double_slippage.net_pnl,
        evaluation.aggregate.baseline.net_pnl,
    )
