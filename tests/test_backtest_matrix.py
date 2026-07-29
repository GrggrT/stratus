from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import stratus.backtesting.matrix as matrix_module
from stratus.backtesting.engine import (
    BacktestConfig,
    BinanceBacktestEngine,
)
from stratus.backtesting.events import AggregateTradeEvent, CandleEvent, MarketEvent
from stratus.backtesting.matrix import BacktestMatrixEntry, run_backtest_matrix
from stratus.backtesting.strategies import BuyAndHoldBenchmark
from stratus.collectors.binance_archive import BinanceArchiveError

START = datetime(2026, 7, 10, tzinfo=UTC)
SYMBOL = "BTCUSDT"


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_quote=Decimal("1000"),
        order_notional=Decimal("100"),
        maker_fee_bps=Decimal("1"),
        taker_fee_bps=Decimal("10"),
        entry_slippage_bps=Decimal("5"),
        exit_slippage_bps=Decimal("5"),
        latency=timedelta(0),
        order_ttl=timedelta(seconds=20),
        maker_participation_rate=Decimal("0.05"),
        price_tick=Decimal("0.01"),
        quantity_step=Decimal("0.001"),
        minimum_quantity=Decimal("0.001"),
        minimum_notional=Decimal("5"),
    )


def _events() -> list[MarketEvent]:
    candle = CandleEvent(
        symbol=SYMBOL,
        interval="5m",
        open_time=START,
        close_time=START + timedelta(minutes=5),
        open_price=Decimal("100"),
        high_price=Decimal("101"),
        low_price=Decimal("99"),
        close_price=Decimal("100"),
        base_volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        trade_count=100,
    )
    return [
        AggregateTradeEvent(
            symbol=SYMBOL,
            aggregate_trade_id=1,
            price=Decimal("100"),
            quantity=Decimal("1"),
            first_trade_id=1001,
            last_trade_id=1001,
            trade_time=START + timedelta(minutes=1),
            buyer_is_maker=False,
            best_price_match=True,
        ),
        candle,
        AggregateTradeEvent(
            symbol=SYMBOL,
            aggregate_trade_id=2,
            price=Decimal("110"),
            quantity=Decimal("1"),
            first_trade_id=1002,
            last_trade_id=1002,
            trade_time=START + timedelta(minutes=6),
            buyer_is_maker=False,
            best_price_match=True,
        ),
    ]


async def _stream(values: list[MarketEvent]) -> AsyncIterator[MarketEvent]:
    for value in values:
        yield value


async def test_backtest_matrix_matches_independent_engines_and_reads_source_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = _events()
    baseline_config = _config()
    stress_config = replace(
        baseline_config,
        entry_slippage_bps=Decimal("10"),
        exit_slippage_bps=Decimal("10"),
    )
    reads = 0
    canonicalizations = 0
    canonical_market_event = matrix_module.canonical_market_event

    def counted_canonicalization(event: MarketEvent) -> bytes:
        nonlocal canonicalizations
        canonicalizations += 1
        return canonical_market_event(event)

    monkeypatch.setattr(
        matrix_module,
        "canonical_market_event",
        counted_canonicalization,
    )

    async def counted_stream() -> AsyncIterator[MarketEvent]:
        nonlocal reads
        for value in values:
            reads += 1
            yield value

    matrix = await run_backtest_matrix(
        (
            BacktestMatrixEntry("baseline", baseline_config, BuyAndHoldBenchmark),
            BacktestMatrixEntry("stress", stress_config, BuyAndHoldBenchmark),
        ),
        counted_stream(),
    )
    independent_baseline = await BinanceBacktestEngine(baseline_config, BuyAndHoldBenchmark()).run(
        _stream(values)
    )
    independent_stress = await BinanceBacktestEngine(stress_config, BuyAndHoldBenchmark()).run(
        _stream(values)
    )

    assert reads == len(values)
    assert canonicalizations == len(values)
    assert matrix["baseline"] == independent_baseline
    assert matrix["stress"] == independent_stress
    assert matrix["baseline"].data_hash == matrix["stress"].data_hash
    assert matrix["baseline"].run_hash != matrix["stress"].run_hash


async def test_backtest_matrix_unwraps_single_producer_failure() -> None:
    async def failing_stream() -> AsyncIterator[MarketEvent]:
        if False:
            yield _events()[0]
        raise BinanceArchiveError("resource pressure", kind="resource_pressure")

    with pytest.raises(BinanceArchiveError, match="resource pressure"):
        await run_backtest_matrix(
            (BacktestMatrixEntry("baseline", _config(), BuyAndHoldBenchmark),),
            failing_stream(),
        )
