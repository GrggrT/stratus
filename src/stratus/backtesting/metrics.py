from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from stratus.backtesting.engine import BacktestResult, EquityPoint


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    completed_cycles: int
    profitable_cycles: int
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    profit_factor: Decimal | None
    win_rate: Decimal | None
    mean_net_expectancy: Decimal | None
    bootstrap_lcb_95_expectancy: Decimal | None
    maximum_drawdown: Decimal
    annualized_daily_sharpe: Decimal | None
    calmar_ratio: Decimal | None
    pnl_without_top_five_cycles: Decimal
    top_five_profit_contribution: Decimal | None


@dataclass(frozen=True, slots=True)
class BacktestGateDecision:
    status: str
    failures: tuple[str, ...]


def calculate_backtest_metrics(
    result: BacktestResult,
    *,
    bootstrap_samples: int = 2000,
    bootstrap_seed: int = 0,
) -> BacktestMetrics:
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    pnl_values = [cycle.net_pnl for cycle in result.cycles]
    net_pnl = result.final_equity - result.initial_quote
    maximum_drawdown = result.maximum_drawdown
    sharpe = _annualized_daily_sharpe(result.equity_curve)
    calmar = _calmar_ratio(result, maximum_drawdown)
    return calculate_pooled_backtest_metrics(
        pnl_values,
        net_pnl=net_pnl,
        maximum_drawdown=maximum_drawdown,
        annualized_daily_sharpe=sharpe,
        calmar_ratio=calmar,
        bootstrap_samples=bootstrap_samples,
        bootstrap_seed=bootstrap_seed,
    )


def calculate_pooled_backtest_metrics(
    cycle_net_pnls: Sequence[Decimal],
    *,
    net_pnl: Decimal,
    maximum_drawdown: Decimal,
    annualized_daily_sharpe: Decimal | None,
    calmar_ratio: Decimal | None,
    bootstrap_samples: int = 2000,
    bootstrap_seed: int = 0,
) -> BacktestMetrics:
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")
    values = list(cycle_net_pnls)
    decimal_values = [net_pnl, maximum_drawdown, *values]
    optional_values = [annualized_daily_sharpe, calmar_ratio]
    if any(not value.is_finite() for value in decimal_values):
        raise ValueError("pooled backtest metrics must be finite")
    if any(value is not None and not value.is_finite() for value in optional_values):
        raise ValueError("pooled optional backtest metrics must be finite")
    profitable = [value for value in values if value > 0]
    losing = [value for value in values if value < 0]
    gross_profit = sum(profitable, start=Decimal("0"))
    gross_loss = -sum(losing, start=Decimal("0"))
    count = len(values)
    mean_expectancy = sum(values, start=Decimal("0")) / count if count else None
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    win_rate = Decimal(len(profitable)) / count if count else None
    robust_pnl, contribution = _profit_concentration_values(values)
    lcb = _bootstrap_lcb(values, bootstrap_samples, bootstrap_seed) if values else None
    return BacktestMetrics(
        completed_cycles=count,
        profitable_cycles=len(profitable),
        net_pnl=net_pnl,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=profit_factor,
        win_rate=win_rate,
        mean_net_expectancy=mean_expectancy,
        bootstrap_lcb_95_expectancy=lcb,
        maximum_drawdown=maximum_drawdown,
        annualized_daily_sharpe=annualized_daily_sharpe,
        calmar_ratio=calmar_ratio,
        pnl_without_top_five_cycles=robust_pnl,
        top_five_profit_contribution=contribution,
    )


def backtest_metrics_to_payload(metrics: BacktestMetrics) -> dict[str, Any]:
    return {
        "completed_cycles": metrics.completed_cycles,
        "profitable_cycles": metrics.profitable_cycles,
        "net_pnl": str(metrics.net_pnl),
        "gross_profit": str(metrics.gross_profit),
        "gross_loss": str(metrics.gross_loss),
        "profit_factor": _optional_decimal_text(metrics.profit_factor),
        "win_rate": _optional_decimal_text(metrics.win_rate),
        "mean_net_expectancy": _optional_decimal_text(metrics.mean_net_expectancy),
        "bootstrap_lcb_95_expectancy": _optional_decimal_text(metrics.bootstrap_lcb_95_expectancy),
        "maximum_drawdown": str(metrics.maximum_drawdown),
        "annualized_daily_sharpe": _optional_decimal_text(metrics.annualized_daily_sharpe),
        "calmar_ratio": _optional_decimal_text(metrics.calmar_ratio),
        "pnl_without_top_five_cycles": str(metrics.pnl_without_top_five_cycles),
        "top_five_profit_contribution": _optional_decimal_text(
            metrics.top_five_profit_contribution
        ),
    }


def backtest_metrics_from_payload(payload: dict[str, Any]) -> BacktestMetrics:
    expected = {
        "completed_cycles",
        "profitable_cycles",
        "net_pnl",
        "gross_profit",
        "gross_loss",
        "profit_factor",
        "win_rate",
        "mean_net_expectancy",
        "bootstrap_lcb_95_expectancy",
        "maximum_drawdown",
        "annualized_daily_sharpe",
        "calmar_ratio",
        "pnl_without_top_five_cycles",
        "top_five_profit_contribution",
    }
    if set(payload) != expected:
        raise ValueError("backtest metrics payload fields are invalid")
    completed_cycles = _payload_integer(payload, "completed_cycles")
    profitable_cycles = _payload_integer(payload, "profitable_cycles")
    if completed_cycles < 0 or not 0 <= profitable_cycles <= completed_cycles:
        raise ValueError("backtest metrics cycle counts are invalid")
    return BacktestMetrics(
        completed_cycles=completed_cycles,
        profitable_cycles=profitable_cycles,
        net_pnl=_payload_decimal(payload, "net_pnl"),
        gross_profit=_payload_decimal(payload, "gross_profit"),
        gross_loss=_payload_decimal(payload, "gross_loss"),
        profit_factor=_payload_optional_decimal(payload, "profit_factor"),
        win_rate=_payload_optional_decimal(payload, "win_rate"),
        mean_net_expectancy=_payload_optional_decimal(payload, "mean_net_expectancy"),
        bootstrap_lcb_95_expectancy=_payload_optional_decimal(
            payload, "bootstrap_lcb_95_expectancy"
        ),
        maximum_drawdown=_payload_decimal(payload, "maximum_drawdown"),
        annualized_daily_sharpe=_payload_optional_decimal(payload, "annualized_daily_sharpe"),
        calmar_ratio=_payload_optional_decimal(payload, "calmar_ratio"),
        pnl_without_top_five_cycles=_payload_decimal(payload, "pnl_without_top_five_cycles"),
        top_five_profit_contribution=_payload_optional_decimal(
            payload, "top_five_profit_contribution"
        ),
    )


def evaluate_holdout_gate(
    metrics: BacktestMetrics,
    slippage_stress_metrics: BacktestMetrics,
    *,
    minimum_cycles: int = 500,
    minimum_symbol_cycles: int = 100,
    symbol_cycles: int | None = None,
    diversification_passed: bool = False,
    parameter_stability_passed: bool = False,
) -> BacktestGateDecision:
    actual_symbol_cycles = metrics.completed_cycles if symbol_cycles is None else symbol_cycles
    failures: list[str] = []
    if metrics.completed_cycles < minimum_cycles:
        failures.append(f"completed_cycles<{minimum_cycles}")
    if actual_symbol_cycles < minimum_symbol_cycles:
        failures.append(f"symbol_cycles<{minimum_symbol_cycles}")
    if metrics.net_pnl <= 0:
        failures.append("holdout_net_pnl<=0")
    if metrics.bootstrap_lcb_95_expectancy is None or metrics.bootstrap_lcb_95_expectancy <= 0:
        failures.append("bootstrap_lcb_95_expectancy<=0")
    if metrics.profit_factor is None or metrics.profit_factor < Decimal("1.20"):
        failures.append("profit_factor<1.20")
    if metrics.annualized_daily_sharpe is None or metrics.annualized_daily_sharpe < Decimal("1.0"):
        failures.append("annualized_daily_sharpe<1.0")
    if metrics.calmar_ratio is None or metrics.calmar_ratio < Decimal("1.0"):
        failures.append("calmar_ratio<1.0")
    if metrics.maximum_drawdown > Decimal("0.08"):
        failures.append("maximum_drawdown>0.08")
    if metrics.pnl_without_top_five_cycles <= 0:
        failures.append("pnl_without_top_five_cycles<=0")
    if (
        metrics.top_five_profit_contribution is None
        or metrics.top_five_profit_contribution >= Decimal("0.25")
    ):
        failures.append("top_five_profit_contribution>=0.25")
    if slippage_stress_metrics.net_pnl <= 0:
        failures.append("double_slippage_stress_net_pnl<=0")
    if not diversification_passed:
        failures.append("symbol_or_regime_diversification_not_proven")
    if not parameter_stability_passed:
        failures.append("parameter_stability_not_proven")
    return BacktestGateDecision(
        status="GO" if not failures else "NO_GO",
        failures=tuple(failures),
    )


def _bootstrap_lcb(values: list[Decimal], samples: int, seed: int) -> Decimal:
    rng = random.Random(seed)
    count = len(values)
    means = [
        sum((rng.choice(values) for _ in range(count)), start=Decimal("0")) / count
        for _ in range(samples)
    ]
    means.sort()
    index = max(0, int(samples * 0.025) - 1)
    return means[index]


def _daily_equity(points: tuple[EquityPoint, ...]) -> list[tuple[date, Decimal]]:
    values: dict[date, Decimal] = {}
    for point in points:
        values[point.event_time.date()] = point.equity
    return sorted(values.items())


def _annualized_daily_sharpe(points: tuple[EquityPoint, ...]) -> Decimal | None:
    daily = _daily_equity(points)
    returns = [
        current[1] / previous[1] - Decimal("1")
        for previous, current in zip(daily, daily[1:], strict=False)
        if previous[1] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns, start=Decimal("0")) / len(returns)
    variance = sum(((value - mean) ** 2 for value in returns), start=Decimal("0")) / (
        len(returns) - 1
    )
    if variance <= 0:
        return None
    return mean / variance.sqrt() * Decimal("365").sqrt()


def _calmar_ratio(result: BacktestResult, maximum_drawdown: Decimal) -> Decimal | None:
    if maximum_drawdown <= 0 or result.final_equity <= 0 or result.initial_quote <= 0:
        return None
    elapsed_seconds = Decimal(
        (result.ended_at - result.started_at).days * 86_400
        + (result.ended_at - result.started_at).seconds
    )
    if elapsed_seconds <= 0:
        return None
    elapsed_days = elapsed_seconds / Decimal("86400")
    total_growth = result.final_equity / result.initial_quote
    annualized_return = (total_growth.ln() * Decimal("365") / elapsed_days).exp() - Decimal("1")
    return annualized_return / maximum_drawdown


def _profit_concentration_values(
    pnl_values: Sequence[Decimal],
) -> tuple[Decimal, Decimal | None]:
    total = sum(pnl_values, start=Decimal("0"))
    top = sorted(pnl_values, reverse=True)[:5]
    top_total = sum(top, start=Decimal("0"))
    robust = total - top_total
    contribution = top_total / total if total > 0 else None
    return robust, contribution


def _optional_decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _payload_integer(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"backtest metrics {key} must be an integer")
    return value


def _payload_decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"backtest metrics {key} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"backtest metrics {key} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"backtest metrics {key} must be finite")
    return parsed


def _payload_optional_decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    if payload[key] is None:
        return None
    return _payload_decimal(payload, key)
