from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class CandleEvent:
    symbol: str
    interval: str
    open_time: datetime
    close_time: datetime
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    base_volume: Decimal
    quote_volume: Decimal
    trade_count: int
    closed: bool = True

    @property
    def event_time(self) -> datetime:
        return self.close_time


@dataclass(frozen=True, slots=True)
class AggregateTradeEvent:
    symbol: str
    aggregate_trade_id: int
    price: Decimal
    quantity: Decimal
    first_trade_id: int
    last_trade_id: int
    trade_time: datetime
    buyer_is_maker: bool
    best_price_match: bool

    @property
    def event_time(self) -> datetime:
        return self.trade_time


type MarketEvent = CandleEvent | AggregateTradeEvent


def market_event_priority(event: MarketEvent) -> int:
    # Trades at a timestamp happened before a candle closing at that same timestamp.
    return 0 if isinstance(event, AggregateTradeEvent) else 1


def canonical_market_event(event: MarketEvent) -> bytes:
    if isinstance(event, CandleEvent):
        payload: dict[str, str | int | bool] = {
            "kind": "candle",
            "symbol": event.symbol,
            "interval": event.interval,
            "open_time": event.open_time.isoformat(),
            "close_time": event.close_time.isoformat(),
            "open": str(event.open_price),
            "high": str(event.high_price),
            "low": str(event.low_price),
            "close": str(event.close_price),
            "base_volume": str(event.base_volume),
            "quote_volume": str(event.quote_volume),
            "trade_count": event.trade_count,
            "closed": event.closed,
        }
    else:
        payload = {
            "kind": "agg_trade",
            "symbol": event.symbol,
            "aggregate_trade_id": event.aggregate_trade_id,
            "price": str(event.price),
            "quantity": str(event.quantity),
            "first_trade_id": event.first_trade_id,
            "last_trade_id": event.last_trade_id,
            "trade_time": event.trade_time.isoformat(),
            "buyer_is_maker": event.buyer_is_maker,
            "best_price_match": event.best_price_match,
        }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
