from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.backtesting.events import AggregateTradeEvent, CandleEvent, MarketEvent
from stratus.db.models import (
    CryptoAggregateTrade,
    CryptoCandle,
    CryptoSymbol,
    TradingVenue,
)


async def iter_crypto_backtest_events(
    session: AsyncSession,
    *,
    symbol: str,
    interval: str,
    start_time: datetime,
    end_time: datetime,
    trade_batch_size: int = 10_000,
) -> AsyncIterator[MarketEvent]:
    if start_time.tzinfo is None or start_time.utcoffset() is None:
        raise ValueError("backtest start_time must be timezone-aware")
    if end_time.tzinfo is None or end_time.utcoffset() is None:
        raise ValueError("backtest end_time must be timezone-aware")
    if start_time >= end_time:
        raise ValueError("backtest start_time must be before end_time")
    if trade_batch_size < 100:
        raise ValueError("trade_batch_size must be at least 100")

    symbol_id = await session.scalar(
        select(CryptoSymbol.id).where(
            CryptoSymbol.venue == TradingVenue.BINANCE_SPOT,
            CryptoSymbol.symbol == symbol,
        )
    )
    if symbol_id is None:
        raise ValueError(f"Binance backtest symbol is not registered: {symbol}")

    candle_rows = (
        (
            await session.execute(
                select(CryptoCandle)
                .where(
                    CryptoCandle.symbol_id == symbol_id,
                    CryptoCandle.interval == interval,
                    CryptoCandle.closed.is_(True),
                    CryptoCandle.close_time >= start_time,
                    CryptoCandle.close_time < end_time,
                )
                .order_by(CryptoCandle.close_time, CryptoCandle.open_time)
            )
        )
        .scalars()
        .all()
    )
    candles = [
        CandleEvent(
            symbol=symbol,
            interval=row.interval,
            open_time=row.open_time,
            close_time=row.close_time,
            open_price=row.open_price,
            high_price=row.high_price,
            low_price=row.low_price,
            close_price=row.close_price,
            base_volume=row.base_volume,
            quote_volume=row.quote_volume,
            trade_count=row.trade_count,
            closed=row.closed,
        )
        for row in candle_rows
    ]

    trade_statement = (
        select(CryptoAggregateTrade)
        .where(
            CryptoAggregateTrade.symbol_id == symbol_id,
            CryptoAggregateTrade.trade_time >= start_time,
            CryptoAggregateTrade.trade_time < end_time,
        )
        .order_by(
            CryptoAggregateTrade.trade_time,
            CryptoAggregateTrade.aggregate_trade_id,
        )
        .execution_options(yield_per=trade_batch_size)
    )
    candle_index = 0
    trade_rows = await session.stream_scalars(trade_statement)
    async for row in trade_rows:
        trade = AggregateTradeEvent(
            symbol=symbol,
            aggregate_trade_id=row.aggregate_trade_id,
            price=row.price,
            quantity=row.quantity,
            first_trade_id=row.first_trade_id,
            last_trade_id=row.last_trade_id,
            trade_time=row.trade_time,
            buyer_is_maker=row.buyer_is_maker,
            best_price_match=row.best_price_match,
        )
        while candle_index < len(candles) and candles[candle_index].event_time < trade.event_time:
            yield candles[candle_index]
            candle_index += 1
        yield trade

    while candle_index < len(candles):
        yield candles[candle_index]
        candle_index += 1
