from __future__ import annotations

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal

from stratus.backtesting.engine import (
    DecisionAction,
    OrderStyle,
    PortfolioView,
    StrategyDecision,
)
from stratus.backtesting.events import CandleEvent

_BPS = Decimal("10000")


@dataclass(slots=True)
class MovingAverageCrossStrategy:
    fast_period: int
    slow_period: int
    order_style: OrderStyle = OrderStyle.TAKER
    maker_offset_bps: Decimal = Decimal("1")
    name: str = field(init=False, default="moving_average_cross")
    version: str = field(init=False)
    _closes: deque[Decimal] = field(init=False)
    _previous_fast: Decimal | None = field(init=False, default=None)
    _previous_slow: Decimal | None = field(init=False, default=None)

    def __post_init__(self) -> None:
        if self.fast_period < 2:
            raise ValueError("fast_period must be at least 2")
        if self.slow_period <= self.fast_period:
            raise ValueError("slow_period must exceed fast_period")
        if not Decimal("0") <= self.maker_offset_bps < _BPS:
            raise ValueError("maker_offset_bps must be in [0, 10000)")
        self._closes = deque(maxlen=self.slow_period)
        payload = json.dumps(
            {
                "fast_period": self.fast_period,
                "slow_period": self.slow_period,
                "order_style": self.order_style.value,
                "maker_offset_bps": str(self.maker_offset_bps),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        self.version = "macross-v1-" + hashlib.sha256(payload).hexdigest()[:16]

    def on_candle(self, candle: CandleEvent, portfolio: PortfolioView) -> StrategyDecision | None:
        self._closes.append(candle.close_price)
        if len(self._closes) < self.slow_period:
            return None
        closes = tuple(self._closes)
        fast = sum(closes[-self.fast_period :], start=Decimal("0")) / self.fast_period
        slow = sum(closes, start=Decimal("0")) / self.slow_period
        previous_fast = self._previous_fast
        previous_slow = self._previous_slow
        self._previous_fast = fast
        self._previous_slow = slow
        if previous_fast is None or previous_slow is None:
            return None

        crossed_up = previous_fast <= previous_slow and fast > slow
        crossed_down = previous_fast >= previous_slow and fast < slow
        if crossed_up and portfolio.base_quantity == 0:
            return StrategyDecision(
                action=DecisionAction.ENTER_LONG,
                style=self.order_style,
                limit_price=self._limit_price(candle.close_price, is_entry=True),
                reason="fast_ma_crossed_above_slow_ma",
            )
        if crossed_down and portfolio.base_quantity > 0:
            return StrategyDecision(
                action=DecisionAction.EXIT_LONG,
                style=self.order_style,
                limit_price=self._limit_price(candle.close_price, is_entry=False),
                reason="fast_ma_crossed_below_slow_ma",
            )
        return None

    def _limit_price(self, close_price: Decimal, *, is_entry: bool) -> Decimal | None:
        if self.order_style is OrderStyle.TAKER:
            return None
        direction = Decimal("-1") if is_entry else Decimal("1")
        return close_price * (Decimal("1") + direction * self.maker_offset_bps / _BPS)


@dataclass(slots=True)
class BuyAndHoldBenchmark:
    name: str = field(init=False, default="buy_and_hold")
    version: str = field(init=False, default="buy-and-hold-v1")
    _submitted: bool = field(init=False, default=False)

    def on_candle(self, candle: CandleEvent, portfolio: PortfolioView) -> StrategyDecision | None:
        if self._submitted:
            return None
        self._submitted = True
        return StrategyDecision(
            action=DecisionAction.ENTER_LONG,
            style=OrderStyle.TAKER,
            reason="benchmark_initial_entry",
        )
