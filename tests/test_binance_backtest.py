from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.backtesting.engine import (
    BACKTEST_ENGINE_VERSION,
    BacktestConfig,
    BacktestDataError,
    BinanceBacktestEngine,
    DecisionAction,
    OrderStyle,
    PortfolioView,
    SimulatedOrderStatus,
    StrategyDecision,
)
from stratus.backtesting.events import AggregateTradeEvent, CandleEvent, MarketEvent
from stratus.backtesting.metrics import calculate_backtest_metrics, evaluate_holdout_gate
from stratus.backtesting.windows import build_expanding_walk_forward_plan

SYMBOL = "BTCUSDT"
START = datetime(2026, 7, 10, tzinfo=UTC)


def _config(**changes: object) -> BacktestConfig:
    config = BacktestConfig(
        initial_quote=Decimal("1000"),
        order_notional=Decimal("100"),
        maker_fee_bps=Decimal("0"),
        taker_fee_bps=Decimal("10"),
        entry_slippage_bps=Decimal("5"),
        exit_slippage_bps=Decimal("5"),
        latency=timedelta(seconds=1),
        order_ttl=timedelta(seconds=5),
        maker_participation_rate=Decimal("0.5"),
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
        liquidate_at_end=True,
    )
    return replace(config, **changes)


def _trade(
    aggregate_id: int,
    seconds: str,
    *,
    price: str = "100",
    quantity: str = "1",
    buyer_is_maker: bool = True,
) -> AggregateTradeEvent:
    event_time = START + timedelta(seconds=float(seconds))
    return AggregateTradeEvent(
        symbol=SYMBOL,
        aggregate_trade_id=aggregate_id,
        price=Decimal(price),
        quantity=Decimal(quantity),
        first_trade_id=1000 + aggregate_id,
        last_trade_id=1000 + aggregate_id,
        trade_time=event_time,
        buyer_is_maker=buyer_is_maker,
        best_price_match=True,
    )


def _candle(seconds: int, *, close: str = "100", closed: bool = True) -> CandleEvent:
    close_time = START + timedelta(seconds=seconds)
    price = Decimal(close)
    return CandleEvent(
        symbol=SYMBOL,
        interval="5m",
        open_time=close_time - timedelta(minutes=5),
        close_time=close_time,
        open_price=price,
        high_price=price + Decimal("1"),
        low_price=price - Decimal("1"),
        close_price=price,
        base_volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=100,
        closed=closed,
    )


async def _events(values: list[MarketEvent]) -> AsyncIterator[MarketEvent]:
    for value in values:
        yield value


class ScriptedStrategy:
    name = "scripted"
    version = "scripted-v1"

    def __init__(self, decisions: list[StrategyDecision | None]) -> None:
        self.decisions = iter(decisions)

    def on_candle(self, candle: CandleEvent, portfolio: PortfolioView) -> StrategyDecision | None:
        return next(self.decisions, None)


def _taker_cycle_strategy() -> ScriptedStrategy:
    return ScriptedStrategy(
        [
            StrategyDecision(
                action=DecisionAction.ENTER_LONG,
                style=OrderStyle.TAKER,
                reason="test_entry",
            ),
            StrategyDecision(
                action=DecisionAction.EXIT_LONG,
                style=OrderStyle.TAKER,
                reason="test_exit",
            ),
        ]
    )


async def test_taker_fill_uses_only_post_decision_trade_after_latency_and_costs() -> None:
    values: list[MarketEvent] = [
        _trade(1, "9"),
        _trade(2, "10"),
        _candle(10),
        _trade(3, "10.5"),
        _trade(4, "11"),
        _candle(20, close="110"),
        _trade(5, "21", price="110", buyer_is_maker=False),
    ]

    result = await BinanceBacktestEngine(_config(), _taker_cycle_strategy()).run(_events(values))

    assert len(result.fills) == 2
    entry, exit_fill = result.fills
    assert entry.event_time == START + timedelta(seconds=11)
    assert entry.price == Decimal("100.05")
    assert exit_fill.event_time == START + timedelta(seconds=21)
    assert exit_fill.price == Decimal("109.945")
    assert entry.fee_quote > 0
    assert exit_fill.fee_quote > 0
    assert result.final_base_quantity == 0
    assert len(result.cycles) == 1
    assert result.cycles[0].net_pnl > 0
    assert result.final_equity - result.initial_quote == result.cycles[0].net_pnl


async def test_maker_fill_requires_correct_aggressor_and_can_expire_partial() -> None:
    strategy = ScriptedStrategy(
        [
            StrategyDecision(
                action=DecisionAction.ENTER_LONG,
                style=OrderStyle.MAKER,
                limit_price=Decimal("100"),
                reason="maker_entry",
            ),
            StrategyDecision(
                action=DecisionAction.EXIT_LONG,
                style=OrderStyle.MAKER,
                limit_price=Decimal("110"),
                reason="maker_exit",
            ),
            None,
        ]
    )
    values: list[MarketEvent] = [
        _trade(1, "9"),
        _candle(10),
        _trade(2, "11", price="99", buyer_is_maker=False),
        _trade(3, "12", price="99", buyer_is_maker=True),
        _trade(4, "13", price="99", buyer_is_maker=True),
        _candle(20, close="110"),
        _trade(5, "21", price="111", buyer_is_maker=True),
        _trade(6, "22", price="111", buyer_is_maker=False),
        _candle(26, close="111"),
    ]

    result = await BinanceBacktestEngine(
        _config(latency=timedelta(0), liquidate_at_end=False), strategy
    ).run(_events(values))

    assert [order.status for order in result.orders] == [
        SimulatedOrderStatus.FILLED,
        SimulatedOrderStatus.PARTIAL_EXPIRED,
    ]
    assert [fill.quantity for fill in result.fills] == [
        Decimal("0.5"),
        Decimal("0.5"),
        Decimal("0.5"),
    ]
    assert all(fill.price == Decimal("100") for fill in result.fills[:2])
    assert result.fills[2].price == Decimal("110")
    assert result.final_base_quantity == Decimal("0.5")
    assert not result.cycles


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ([_candle(10, closed=False)], "open candle"),
        ([_trade(1, "1"), _trade(3, "2")], "sequence has a gap"),
        ([_candle(10), _trade(1, "10")], "causal order"),
    ],
)
async def test_backtest_rejects_noncausal_or_incomplete_market_data(
    values: list[MarketEvent], message: str
) -> None:
    with pytest.raises(BacktestDataError, match=message):
        await BinanceBacktestEngine(_config(), ScriptedStrategy([None])).run(_events(values))


async def test_backtest_hashes_are_reproducible_and_gate_fails_closed() -> None:
    values: list[MarketEvent] = [
        _trade(1, "9"),
        _candle(10),
        _trade(2, "11"),
        _candle(20, close="110"),
        _trade(3, "21", price="110", buyer_is_maker=False),
    ]
    first = await BinanceBacktestEngine(_config(), _taker_cycle_strategy()).run(_events(values))
    second = await BinanceBacktestEngine(_config(), _taker_cycle_strategy()).run(_events(values))
    stressed = await BinanceBacktestEngine(
        _config(entry_slippage_bps=Decimal("10"), exit_slippage_bps=Decimal("10")),
        _taker_cycle_strategy(),
    ).run(_events(values))

    assert first.data_hash == second.data_hash == stressed.data_hash
    assert first.engine_version == BACKTEST_ENGINE_VERSION
    assert first.config_hash == second.config_hash
    assert first.run_hash == second.run_hash
    assert stressed.config_hash != first.config_hash
    assert stressed.run_hash != first.run_hash

    metrics = calculate_backtest_metrics(first, bootstrap_samples=100)
    stress_metrics = calculate_backtest_metrics(stressed, bootstrap_samples=100)
    gate = evaluate_holdout_gate(metrics, stress_metrics)
    assert gate.status == "NO_GO"
    assert "completed_cycles<500" in gate.failures
    assert "symbol_or_regime_diversification_not_proven" in gate.failures
    assert "parameter_stability_not_proven" in gate.failures


async def test_maximum_drawdown_observes_intracandle_aggregate_trades() -> None:
    strategy = ScriptedStrategy(
        [
            StrategyDecision(
                action=DecisionAction.ENTER_LONG,
                style=OrderStyle.TAKER,
                reason="drawdown_entry",
            )
        ]
    )
    values: list[MarketEvent] = [
        _trade(1, "9", price="100"),
        _candle(10, close="100"),
        _trade(2, "11", price="100"),
        _trade(3, "12", price="50"),
        _trade(4, "13", price="100"),
    ]

    result = await BinanceBacktestEngine(_config(), strategy).run(_events(values))
    metrics = calculate_backtest_metrics(result, bootstrap_samples=100)

    assert result.maximum_drawdown > Decimal("0.04")
    assert metrics.maximum_drawdown == result.maximum_drawdown
    assert len(result.equity_curve) < len(values)


def test_walk_forward_plan_keeps_final_holdout_untouched_and_is_reproducible() -> None:
    arguments = {
        "dataset_start": datetime(2024, 1, 1, tzinfo=UTC),
        "dataset_end": datetime(2025, 1, 1, tzinfo=UTC),
        "minimum_train": timedelta(days=120),
        "validation": timedelta(days=30),
        "test": timedelta(days=30),
        "step": timedelta(days=30),
        "final_holdout": timedelta(days=60),
    }

    first = build_expanding_walk_forward_plan(**arguments)
    second = build_expanding_walk_forward_plan(**arguments)

    assert first.plan_hash == second.plan_hash
    assert len(first.folds) >= 2
    assert all(fold.test.end <= first.final_holdout.start for fold in first.folds)
    assert first.folds[-1].test.end <= first.final_holdout.start
    assert first.final_holdout.end == arguments["dataset_end"]


def test_walk_forward_plan_rejects_overlapping_test_windows() -> None:
    with pytest.raises(ValueError, match="overlapping test windows"):
        build_expanding_walk_forward_plan(
            dataset_start=datetime(2024, 1, 1, tzinfo=UTC),
            dataset_end=datetime(2025, 1, 1, tzinfo=UTC),
            minimum_train=timedelta(days=120),
            validation=timedelta(days=30),
            test=timedelta(days=30),
            step=timedelta(days=15),
            final_holdout=timedelta(days=60),
        )
