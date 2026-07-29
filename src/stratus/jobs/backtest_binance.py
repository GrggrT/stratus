from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.backtesting.engine import (
    BacktestConfig,
    BacktestDataError,
    BacktestResult,
    BacktestStrategy,
    BinanceBacktestEngine,
    OrderStyle,
)
from stratus.backtesting.metrics import (
    BacktestGateDecision,
    BacktestMetrics,
    calculate_backtest_metrics,
    evaluate_holdout_gate,
)
from stratus.backtesting.strategies import BuyAndHoldBenchmark, MovingAverageCrossStrategy
from stratus.collectors.binance_archive import AGG_TRADES_ARCHIVE_KEY
from stratus.config import Settings, get_settings
from stratus.db.models import (
    CryptoAggregateTrade,
    CryptoArchiveImport,
    CryptoCandle,
    CryptoDataGap,
    CryptoSymbol,
    TradingVenue,
    VenueMode,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.crypto_backtest_data import iter_crypto_backtest_events
from stratus.repositories.venue_runtime_control import get_venue_runtime_control

SessionFactory = async_sessionmaker[AsyncSession]
StrategyFactory = Callable[[], BacktestStrategy]


@dataclass(frozen=True, slots=True)
class BinanceBacktestReport:
    baseline: BacktestResult
    baseline_metrics: BacktestMetrics
    double_slippage: BacktestResult
    double_slippage_metrics: BacktestMetrics
    gate: BacktestGateDecision


async def _research_symbol(
    session: AsyncSession,
    *,
    symbol: str,
) -> CryptoSymbol:
    row = await session.scalar(
        select(CryptoSymbol).where(
            CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
            CryptoSymbol.symbol == symbol,
        )
    )
    if row is None:
        raise BacktestDataError(f"Binance backtest symbol is not registered: {symbol}")
    if row.status != "TRADING" or not row.enabled:
        raise BacktestDataError(f"Binance backtest symbol is not enabled for research: {symbol}")
    required = {
        "price_tick": row.price_tick,
        "quantity_step": row.quantity_step,
        "minimum_quantity": row.minimum_quantity,
        "minimum_notional": row.minimum_notional,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise BacktestDataError("Binance symbol filters are incomplete: " + ", ".join(missing))
    return row


def _included_dates(start_time: datetime, end_time: datetime) -> set[date]:
    first = start_time.astimezone(UTC).date()
    last = (end_time.astimezone(UTC) - timedelta(microseconds=1)).date()
    return {first + timedelta(days=offset) for offset in range((last - first).days + 1)}


async def _require_verified_dataset(
    session: AsyncSession,
    *,
    symbol_id: int,
    interval: str,
    start_time: datetime,
    end_time: datetime,
) -> None:
    if interval == AGG_TRADES_ARCHIVE_KEY:
        raise BacktestDataError("Binance backtest kline interval is invalid")
    expected_dates = _included_dates(start_time, end_time)
    rows = (
        await session.execute(
            select(
                CryptoArchiveImport.interval,
                CryptoArchiveImport.trading_date,
                CryptoArchiveImport.status,
                CryptoArchiveImport.checksum_sha256,
                CryptoArchiveImport.row_count,
            ).where(
                CryptoArchiveImport.symbol_id == symbol_id,
                CryptoArchiveImport.interval.in_((interval, AGG_TRADES_ARCHIVE_KEY)),
                CryptoArchiveImport.trading_date.in_(expected_dates),
            )
        )
    ).all()
    imported = {
        (row.interval, row.trading_date): row
        for row in rows
        if row.status == "imported"
        and row.checksum_sha256 is not None
        and len(row.checksum_sha256) == 64
        and row.row_count > 0
    }
    expected = {
        (dataset, trading_date)
        for dataset in (interval, AGG_TRADES_ARCHIVE_KEY)
        for trading_date in expected_dates
    }
    missing = sorted(expected - imported.keys())
    if missing:
        preview = ", ".join(f"{dataset}:{trading_date}" for dataset, trading_date in missing[:5])
        raise BacktestDataError(f"verified Binance backtest manifests are missing: {preview}")
    open_gap = await session.scalar(
        select(CryptoDataGap.id)
        .where(
            CryptoDataGap.symbol_id == symbol_id,
            CryptoDataGap.status == "open",
            CryptoDataGap.stream.in_(
                (f"archive_klines:{interval}", f"archive:{AGG_TRADES_ARCHIVE_KEY}")
            ),
        )
        .limit(1)
    )
    if open_gap is not None:
        raise BacktestDataError("verified Binance backtest dataset has an open data gap")

    coverage_start = datetime.combine(min(expected_dates), time.min, tzinfo=UTC)
    coverage_end = datetime.combine(max(expected_dates) + timedelta(days=1), time.min, tzinfo=UTC)
    trade_date = cast(func.timezone("UTC", CryptoAggregateTrade.trade_time), Date)
    trade_counts = {
        row.trading_date: row.row_count
        for row in (
            await session.execute(
                select(
                    trade_date.label("trading_date"),
                    func.count().label("row_count"),
                )
                .where(
                    CryptoAggregateTrade.symbol_id == symbol_id,
                    CryptoAggregateTrade.trade_time >= coverage_start,
                    CryptoAggregateTrade.trade_time < coverage_end,
                )
                .group_by(trade_date)
            )
        ).all()
    }
    candle_date = cast(func.timezone("UTC", CryptoCandle.close_time), Date)
    candle_counts = {
        row.trading_date: row.row_count
        for row in (
            await session.execute(
                select(
                    candle_date.label("trading_date"),
                    func.count().label("row_count"),
                )
                .where(
                    CryptoCandle.symbol_id == symbol_id,
                    CryptoCandle.interval == interval,
                    CryptoCandle.closed.is_(True),
                    CryptoCandle.close_time >= coverage_start,
                    CryptoCandle.close_time < coverage_end,
                )
                .group_by(candle_date)
            )
        ).all()
    }
    for dataset, trading_date in sorted(expected):
        manifest_count = imported[(dataset, trading_date)].row_count
        actual_count = (
            trade_counts.get(trading_date, 0)
            if dataset == AGG_TRADES_ARCHIVE_KEY
            else candle_counts.get(trading_date, 0)
        )
        if actual_count != manifest_count:
            raise BacktestDataError(
                "verified Binance backtest row count mismatch: "
                f"{dataset}:{trading_date} manifest={manifest_count} actual={actual_count}"
            )


def _require_matching_symbol_filters(config: BacktestConfig, symbol: CryptoSymbol) -> None:
    filters = {
        "price_tick": symbol.price_tick,
        "quantity_step": symbol.quantity_step,
        "minimum_quantity": symbol.minimum_quantity,
        "minimum_notional": symbol.minimum_notional,
    }
    mismatched = [name for name, value in filters.items() if value != getattr(config, name)]
    if mismatched:
        raise BacktestDataError(
            "Binance backtest config does not match persisted symbol filters: "
            + ", ".join(mismatched)
        )


async def _execute_run(
    sessions: SessionFactory,
    *,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    config: BacktestConfig,
    strategy_factory: StrategyFactory,
) -> BacktestResult:
    async with sessions() as session:
        events = iter_crypto_backtest_events(
            session,
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
        )
        return await BinanceBacktestEngine(config, strategy_factory()).run(events)


async def run_binance_backtest(
    *,
    settings: Settings,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    config: BacktestConfig,
    strategy_factory: StrategyFactory,
    session_factory: SessionFactory | None = None,
    bootstrap_samples: int = 2000,
    diversification_passed: bool = False,
    parameter_stability_passed: bool = False,
) -> BinanceBacktestReport:
    if start_time.tzinfo is None or start_time.utcoffset() is None:
        raise ValueError("Binance backtest start_time must be timezone-aware")
    if end_time.tzinfo is None or end_time.utcoffset() is None:
        raise ValueError("Binance backtest end_time must be timezone-aware")
    if start_time >= end_time:
        raise ValueError("Binance backtest start_time must be before end_time")
    if config.entry_slippage_bps * 2 >= Decimal("10000"):
        raise ValueError("double entry slippage stress must remain below 10000 bps")
    if config.exit_slippage_bps * 2 >= Decimal("10000"):
        raise ValueError("double exit slippage stress must remain below 10000 bps")

    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            control = await get_venue_runtime_control(session, TradingVenue.BINANCE_SPOT)
            if not control.enabled or control.mode is not VenueMode.BACKTEST:
                raise RuntimeError("Binance research runner requires enabled backtest venue mode")
            research_symbol = await _research_symbol(session, symbol=symbol)
            _require_matching_symbol_filters(config, research_symbol)
            await _require_verified_dataset(
                session,
                symbol_id=research_symbol.id,
                interval=interval,
                start_time=start_time,
                end_time=end_time,
            )
        baseline = await _execute_run(
            sessions,
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            config=config,
            strategy_factory=strategy_factory,
        )
        stressed_config = replace(
            config,
            entry_slippage_bps=config.entry_slippage_bps * 2,
            exit_slippage_bps=config.exit_slippage_bps * 2,
        )
        stressed = await _execute_run(
            sessions,
            symbol=symbol,
            interval=interval,
            start_time=start_time,
            end_time=end_time,
            config=stressed_config,
            strategy_factory=strategy_factory,
        )
    if baseline.data_hash != stressed.data_hash:
        raise RuntimeError("baseline and slippage stress consumed different market datasets")
    baseline_metrics = calculate_backtest_metrics(
        baseline,
        bootstrap_samples=bootstrap_samples,
    )
    stress_metrics = calculate_backtest_metrics(
        stressed,
        bootstrap_samples=bootstrap_samples,
    )
    gate = evaluate_holdout_gate(
        baseline_metrics,
        stress_metrics,
        diversification_passed=diversification_passed,
        parameter_stability_passed=parameter_stability_passed,
    )
    return BinanceBacktestReport(
        baseline=baseline,
        baseline_metrics=baseline_metrics,
        double_slippage=stressed,
        double_slippage_metrics=stress_metrics,
        gate=gate,
    )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a UTC offset")
    return parsed


def _decimal(value: str) -> Decimal:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise argparse.ArgumentTypeError("value must be a decimal number") from exc


def _json_default(value: object) -> str:
    if isinstance(value, (Decimal, datetime)):
        return str(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run exchange-disconnected Binance Spot research backtest"
    )
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--start", required=True, type=_datetime)
    parser.add_argument("--end", required=True, type=_datetime)
    parser.add_argument("--strategy", choices=("ma_cross", "buy_hold"), default="ma_cross")
    parser.add_argument("--fast-period", type=int, default=12)
    parser.add_argument("--slow-period", type=int, default=48)
    parser.add_argument("--order-style", choices=("maker", "taker"), default="taker")
    parser.add_argument("--maker-offset-bps", type=_decimal, default=Decimal("1"))
    parser.add_argument("--initial-quote", type=_decimal, required=True)
    parser.add_argument("--order-notional", type=_decimal, required=True)
    parser.add_argument("--maker-fee-bps", type=_decimal, required=True)
    parser.add_argument("--taker-fee-bps", type=_decimal, required=True)
    parser.add_argument("--entry-slippage-bps", type=_decimal, required=True)
    parser.add_argument("--exit-slippage-bps", type=_decimal, required=True)
    parser.add_argument("--latency-ms", type=int, default=250)
    parser.add_argument("--order-ttl-seconds", type=int, default=20)
    parser.add_argument("--maker-participation", type=_decimal, default=Decimal("0.05"))
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    arguments = parser.parse_args()
    settings = get_settings()
    style = OrderStyle(arguments.order_style)

    def strategy_factory() -> BacktestStrategy:
        if arguments.strategy == "buy_hold":
            return BuyAndHoldBenchmark()
        return MovingAverageCrossStrategy(
            fast_period=arguments.fast_period,
            slow_period=arguments.slow_period,
            order_style=style,
            maker_offset_bps=arguments.maker_offset_bps,
        )

    async def execute() -> BinanceBacktestReport:
        async with session_factory_scope(settings, None) as sessions:
            async with sessions() as session:
                symbol_row = await _research_symbol(session, symbol=arguments.symbol)
                assert symbol_row.price_tick is not None
                assert symbol_row.quantity_step is not None
                assert symbol_row.minimum_quantity is not None
                assert symbol_row.minimum_notional is not None
                filters = (
                    symbol_row.price_tick,
                    symbol_row.quantity_step,
                    symbol_row.minimum_quantity,
                    symbol_row.minimum_notional,
                )
        config = BacktestConfig(
            initial_quote=arguments.initial_quote,
            order_notional=arguments.order_notional,
            maker_fee_bps=arguments.maker_fee_bps,
            taker_fee_bps=arguments.taker_fee_bps,
            entry_slippage_bps=arguments.entry_slippage_bps,
            exit_slippage_bps=arguments.exit_slippage_bps,
            latency=timedelta(milliseconds=arguments.latency_ms),
            order_ttl=timedelta(seconds=arguments.order_ttl_seconds),
            maker_participation_rate=arguments.maker_participation,
            price_tick=filters[0],
            quantity_step=filters[1],
            minimum_quantity=filters[2],
            minimum_notional=filters[3],
        )
        return await run_binance_backtest(
            settings=settings,
            symbol=arguments.symbol,
            interval=arguments.interval,
            start_time=arguments.start,
            end_time=arguments.end,
            config=config,
            strategy_factory=strategy_factory,
            bootstrap_samples=arguments.bootstrap_samples,
        )

    report = asyncio.run(execute())
    summary = {
        "run_hash": report.baseline.run_hash,
        "data_hash": report.baseline.data_hash,
        "config_hash": report.baseline.config_hash,
        "symbol": report.baseline.symbol,
        "engine_version": report.baseline.engine_version,
        "strategy": report.baseline.strategy_name,
        "strategy_version": report.baseline.strategy_version,
        "started_at": report.baseline.started_at,
        "ended_at": report.baseline.ended_at,
        "event_count": report.baseline.event_count,
        "metrics": asdict(report.baseline_metrics),
        "double_slippage_metrics": asdict(report.double_slippage_metrics),
        "gate": asdict(report.gate),
    }
    print(json.dumps(summary, default=_json_default, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
