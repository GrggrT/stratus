from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Protocol

from stratus.backtesting.events import (
    AggregateTradeEvent,
    CandleEvent,
    MarketEvent,
    canonical_market_event,
    market_event_priority,
)

_BPS = Decimal("10000")
BACKTEST_ENGINE_VERSION = "binance-event-sim-v1"


class DecisionAction(StrEnum):
    ENTER_LONG = "enter_long"
    EXIT_LONG = "exit_long"


class OrderStyle(StrEnum):
    MAKER = "maker"
    TAKER = "taker"


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class SimulatedOrderStatus(StrEnum):
    FILLED = "filled"
    EXPIRED = "expired"
    PARTIAL_EXPIRED = "partial_expired"
    CANCELLED = "cancelled"
    PARTIAL_CANCELLED = "partial_cancelled"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    action: DecisionAction
    style: OrderStyle
    reason: str
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.style is OrderStyle.MAKER and self.limit_price is None:
            raise ValueError("maker strategy decision requires limit_price")
        if self.style is OrderStyle.TAKER and self.limit_price is not None:
            raise ValueError("taker strategy decision cannot set limit_price")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError("strategy limit_price must be positive")
        if not self.reason:
            raise ValueError("strategy decision reason is required")


@dataclass(frozen=True, slots=True)
class PortfolioView:
    event_time: datetime
    quote_balance: Decimal
    base_quantity: Decimal
    average_entry_price: Decimal | None
    last_trade_price: Decimal | None
    pending_side: OrderSide | None


class BacktestStrategy(Protocol):
    name: str
    version: str

    def on_candle(
        self, candle: CandleEvent, portfolio: PortfolioView
    ) -> StrategyDecision | None: ...


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_quote: Decimal
    order_notional: Decimal
    maker_fee_bps: Decimal
    taker_fee_bps: Decimal
    entry_slippage_bps: Decimal
    exit_slippage_bps: Decimal
    latency: timedelta
    order_ttl: timedelta
    maker_participation_rate: Decimal
    price_tick: Decimal
    quantity_step: Decimal
    minimum_quantity: Decimal
    minimum_notional: Decimal
    liquidate_at_end: bool = True

    def __post_init__(self) -> None:
        positive = {
            "initial_quote": self.initial_quote,
            "order_notional": self.order_notional,
            "price_tick": self.price_tick,
            "quantity_step": self.quantity_step,
            "minimum_quantity": self.minimum_quantity,
            "minimum_notional": self.minimum_notional,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError("backtest values must be positive: " + ", ".join(invalid))
        costs = {
            "maker_fee_bps": self.maker_fee_bps,
            "taker_fee_bps": self.taker_fee_bps,
            "entry_slippage_bps": self.entry_slippage_bps,
            "exit_slippage_bps": self.exit_slippage_bps,
        }
        invalid_costs = [name for name, value in costs.items() if value < 0 or value >= _BPS]
        if invalid_costs:
            raise ValueError(
                "backtest bps values must be in [0, 10000): " + ", ".join(invalid_costs)
            )
        if self.latency < timedelta(0):
            raise ValueError("backtest latency must be non-negative")
        if self.order_ttl <= timedelta(0):
            raise ValueError("backtest order_ttl must be positive")
        if not Decimal("0") < self.maker_participation_rate <= Decimal("1"):
            raise ValueError("maker_participation_rate must be in (0, 1]")

    def canonical_hash(self) -> str:
        payload = {
            "initial_quote": str(self.initial_quote),
            "order_notional": str(self.order_notional),
            "maker_fee_bps": str(self.maker_fee_bps),
            "taker_fee_bps": str(self.taker_fee_bps),
            "entry_slippage_bps": str(self.entry_slippage_bps),
            "exit_slippage_bps": str(self.exit_slippage_bps),
            "latency_microseconds": _timedelta_microseconds(self.latency),
            "order_ttl_microseconds": _timedelta_microseconds(self.order_ttl),
            "maker_participation_rate": str(self.maker_participation_rate),
            "price_tick": str(self.price_tick),
            "quantity_step": str(self.quantity_step),
            "minimum_quantity": str(self.minimum_quantity),
            "minimum_notional": str(self.minimum_notional),
            "liquidate_at_end": self.liquidate_at_end,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    order_id: int
    symbol: str
    side: OrderSide
    style: OrderStyle
    event_time: datetime
    quantity: Decimal
    price: Decimal
    gross_quote: Decimal
    fee_quote: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    order_id: int
    symbol: str
    side: OrderSide
    style: OrderStyle
    created_at: datetime
    completed_at: datetime
    requested_quantity: Decimal
    filled_quantity: Decimal
    limit_price: Decimal | None
    status: SimulatedOrderStatus
    reason: str


@dataclass(frozen=True, slots=True)
class CompletedCycle:
    symbol: str
    opened_at: datetime
    closed_at: datetime
    entry_quantity: Decimal
    entry_vwap: Decimal
    exit_vwap: Decimal
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    return_fraction: Decimal
    entry_reason: str
    exit_reason: str


@dataclass(frozen=True, slots=True)
class EquityPoint:
    event_time: datetime
    equity: Decimal


@dataclass(frozen=True, slots=True)
class BacktestResult:
    symbol: str
    engine_version: str
    strategy_name: str
    strategy_version: str
    started_at: datetime
    ended_at: datetime
    initial_quote: Decimal
    final_quote: Decimal
    final_base_quantity: Decimal
    final_mark_price: Decimal
    final_equity: Decimal
    maximum_drawdown: Decimal
    fills: tuple[SimulatedFill, ...]
    orders: tuple[SimulatedOrder, ...]
    cycles: tuple[CompletedCycle, ...]
    equity_curve: tuple[EquityPoint, ...]
    data_hash: str
    config_hash: str
    run_hash: str
    event_count: int


@dataclass(slots=True)
class _PendingOrder:
    order_id: int
    symbol: str
    side: OrderSide
    style: OrderStyle
    created_at: datetime
    eligible_at: datetime
    expires_at: datetime
    requested_quantity: Decimal
    remaining_quantity: Decimal
    limit_price: Decimal | None
    reason: str


@dataclass(slots=True)
class _CycleAccumulator:
    opened_at: datetime
    entry_reason: str
    entry_quantity: Decimal = Decimal("0")
    entry_gross: Decimal = Decimal("0")
    entry_fees: Decimal = Decimal("0")
    exit_quantity: Decimal = Decimal("0")
    exit_gross: Decimal = Decimal("0")
    exit_fees: Decimal = Decimal("0")
    exit_reason: str = ""


class BacktestDataError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PreparedMarketEvent:
    event: MarketEvent
    canonical_bytes: bytes


class BinanceBacktestEngine:
    def __init__(self, config: BacktestConfig, strategy: BacktestStrategy) -> None:
        self.config = config
        self.strategy = strategy
        self._has_run = False
        self._symbol: str | None = None
        self._quote = config.initial_quote
        self._base = Decimal("0")
        self._position_cost = Decimal("0")
        self._last_trade_price: Decimal | None = None
        self._pending: _PendingOrder | None = None
        self._next_order_id = 1
        self._fills: list[SimulatedFill] = []
        self._orders: list[SimulatedOrder] = []
        self._cycles: list[CompletedCycle] = []
        self._equity: list[EquityPoint] = []
        self._peak_equity = config.initial_quote
        self._maximum_drawdown = Decimal("0")
        self._cycle: _CycleAccumulator | None = None
        self._last_event: MarketEvent | None = None
        self._last_trade_id: int | None = None
        self._last_underlying_trade_id: int | None = None
        self._last_candle_close: dict[str, datetime] = {}

    async def run(
        self,
        events: AsyncIterable[MarketEvent | PreparedMarketEvent],
    ) -> BacktestResult:
        if self._has_run:
            raise RuntimeError("a BinanceBacktestEngine instance can only run once")
        self._has_run = True
        data_hasher = hashlib.sha256()
        started_at: datetime | None = None
        event_count = 0
        async for value in events:
            if isinstance(value, PreparedMarketEvent):
                event = value.event
                canonical_bytes = value.canonical_bytes
            else:
                event = value
                canonical_bytes = canonical_market_event(event)
            self._validate_event(event)
            data_hasher.update(canonical_bytes)
            event_count += 1
            started_at = started_at or event.event_time
            self._expire_pending(event.event_time)
            if isinstance(event, AggregateTradeEvent):
                self._last_trade_price = event.price
                if self._base > 0:
                    self._observe_equity(event.price)
                self._process_trade(event)
            else:
                self._process_candle(event)
                self._record_equity(event.event_time, event.close_price)
            self._last_event = event

        if self._last_event is None or started_at is None or self._symbol is None:
            raise BacktestDataError("backtest dataset is empty")
        ended_at = self._last_event.event_time
        self._cancel_pending(ended_at)
        if self.config.liquidate_at_end and self._base > 0 and self._last_trade_price is not None:
            self._force_liquidation(ended_at)
        final_mark = self._last_trade_price
        if final_mark is None and isinstance(self._last_event, CandleEvent):
            final_mark = self._last_event.close_price
        if final_mark is None:
            raise BacktestDataError("backtest dataset has no usable mark price")
        self._record_equity(ended_at, final_mark)
        final_equity = self._quote + self._base * final_mark
        config_hash = self.config.canonical_hash()
        data_hash = data_hasher.hexdigest()
        run_payload = (
            f"{data_hash}:{config_hash}:{BACKTEST_ENGINE_VERSION}:"
            f"{self.strategy.name}:{self.strategy.version}"
        ).encode()
        run_hash = hashlib.sha256(run_payload).hexdigest()
        return BacktestResult(
            symbol=self._symbol,
            engine_version=BACKTEST_ENGINE_VERSION,
            strategy_name=self.strategy.name,
            strategy_version=self.strategy.version,
            started_at=started_at,
            ended_at=ended_at,
            initial_quote=self.config.initial_quote,
            final_quote=self._quote,
            final_base_quantity=self._base,
            final_mark_price=final_mark,
            final_equity=final_equity,
            maximum_drawdown=self._maximum_drawdown,
            fills=tuple(self._fills),
            orders=tuple(self._orders),
            cycles=tuple(self._cycles),
            equity_curve=tuple(self._equity),
            data_hash=data_hash,
            config_hash=config_hash,
            run_hash=run_hash,
            event_count=event_count,
        )

    def _validate_event(self, event: MarketEvent) -> None:
        if event.event_time.tzinfo is None or event.event_time.utcoffset() is None:
            raise BacktestDataError("backtest event timestamps must be timezone-aware")
        if self._symbol is None:
            self._symbol = event.symbol
        elif event.symbol != self._symbol:
            raise BacktestDataError("a backtest run may contain exactly one symbol")
        if self._last_event is not None:
            previous_key = (
                self._last_event.event_time,
                market_event_priority(self._last_event),
            )
            current_key = (event.event_time, market_event_priority(event))
            if current_key < previous_key:
                raise BacktestDataError("backtest events are not in causal order")
        if isinstance(event, CandleEvent):
            if not event.closed:
                raise BacktestDataError("backtest cannot consume an open candle")
            if event.open_time.tzinfo is None or event.open_time.utcoffset() is None:
                raise BacktestDataError("backtest candle timestamps must be timezone-aware")
            if event.close_time < event.open_time:
                raise BacktestDataError("backtest candle closes before it opens")
            previous_close = self._last_candle_close.get(event.interval)
            if previous_close is not None and event.close_time <= previous_close:
                raise BacktestDataError("backtest candle sequence is duplicated or regressed")
            self._last_candle_close[event.interval] = event.close_time
            if min(event.open_price, event.high_price, event.low_price, event.close_price) <= 0:
                raise BacktestDataError("backtest candle prices must be positive")
        else:
            if event.price <= 0 or event.quantity <= 0:
                raise BacktestDataError("backtest aggregate trade values must be positive")
            if (
                self._last_trade_id is not None
                and event.aggregate_trade_id != self._last_trade_id + 1
            ):
                raise BacktestDataError("backtest aggregate trade sequence has a gap")
            if (
                self._last_underlying_trade_id is not None
                and event.first_trade_id != self._last_underlying_trade_id + 1
            ):
                raise BacktestDataError("backtest underlying trade sequence has a gap")
            if event.first_trade_id > event.last_trade_id:
                raise BacktestDataError("backtest aggregate trade ID range is inverted")
            self._last_trade_id = event.aggregate_trade_id
            self._last_underlying_trade_id = event.last_trade_id

    def _process_candle(self, candle: CandleEvent) -> None:
        average_entry = self._position_cost / self._base if self._base > 0 else None
        decision = self.strategy.on_candle(
            candle,
            PortfolioView(
                event_time=candle.event_time,
                quote_balance=self._quote,
                base_quantity=self._base,
                average_entry_price=average_entry,
                last_trade_price=self._last_trade_price,
                pending_side=self._pending.side if self._pending is not None else None,
            ),
        )
        if decision is None:
            return
        desired_side = (
            OrderSide.BUY if decision.action is DecisionAction.ENTER_LONG else OrderSide.SELL
        )
        if self._pending is not None:
            if self._pending.side is desired_side:
                return
            self._cancel_pending(candle.event_time)
        if desired_side is OrderSide.BUY and self._base >= self.config.minimum_quantity:
            return
        if desired_side is OrderSide.SELL and self._base < self.config.minimum_quantity:
            return
        self._create_order(candle, decision, desired_side)

    def _create_order(
        self,
        candle: CandleEvent,
        decision: StrategyDecision,
        side: OrderSide,
    ) -> None:
        reference_price = decision.limit_price or self._last_trade_price or candle.close_price
        if side is OrderSide.BUY:
            fee_bps = (
                self.config.maker_fee_bps
                if decision.style is OrderStyle.MAKER
                else self.config.taker_fee_bps
            )
            slip_bps = (
                Decimal("0")
                if decision.style is OrderStyle.MAKER
                else self.config.entry_slippage_bps
            )
            estimated_price = reference_price * (Decimal("1") + slip_bps / _BPS)
            quote_budget = min(self.config.order_notional, self._quote)
            quantity = quote_budget / (estimated_price * (Decimal("1") + fee_bps / _BPS))
        else:
            quantity = self._base
        quantity = _round_down(quantity, self.config.quantity_step)
        limit_price = decision.limit_price
        if limit_price is not None:
            limit_price = _round_price(limit_price, self.config.price_tick, side)
        if (
            quantity < self.config.minimum_quantity
            or quantity * reference_price < self.config.minimum_notional
        ):
            self._orders.append(
                SimulatedOrder(
                    order_id=self._next_order_id,
                    symbol=candle.symbol,
                    side=side,
                    style=decision.style,
                    created_at=candle.event_time,
                    completed_at=candle.event_time,
                    requested_quantity=quantity,
                    filled_quantity=Decimal("0"),
                    limit_price=limit_price,
                    status=SimulatedOrderStatus.REJECTED,
                    reason=decision.reason,
                )
            )
            self._next_order_id += 1
            return
        self._pending = _PendingOrder(
            order_id=self._next_order_id,
            symbol=candle.symbol,
            side=side,
            style=decision.style,
            created_at=candle.event_time,
            eligible_at=candle.event_time + self.config.latency,
            expires_at=candle.event_time + self.config.order_ttl,
            requested_quantity=quantity,
            remaining_quantity=quantity,
            limit_price=limit_price,
            reason=decision.reason,
        )
        self._next_order_id += 1

    def _process_trade(self, trade: AggregateTradeEvent) -> None:
        order = self._pending
        if order is None or trade.trade_time < order.eligible_at:
            return
        if order.style is OrderStyle.MAKER:
            assert order.limit_price is not None
            eligible = (
                order.side is OrderSide.BUY
                and trade.buyer_is_maker
                and trade.price <= order.limit_price
            ) or (
                order.side is OrderSide.SELL
                and not trade.buyer_is_maker
                and trade.price >= order.limit_price
            )
            if not eligible:
                return
            quantity = _round_down(
                trade.quantity * self.config.maker_participation_rate,
                self.config.quantity_step,
            )
            quantity = min(quantity, order.remaining_quantity)
            price = order.limit_price
            fee_bps = self.config.maker_fee_bps
        else:
            quantity = order.remaining_quantity
            slippage_bps = (
                self.config.entry_slippage_bps
                if order.side is OrderSide.BUY
                else self.config.exit_slippage_bps
            )
            direction = Decimal("1") if order.side is OrderSide.BUY else Decimal("-1")
            price = trade.price * (Decimal("1") + direction * slippage_bps / _BPS)
            fee_bps = self.config.taker_fee_bps
        if quantity <= 0:
            return
        if order.side is OrderSide.BUY:
            affordable = self._quote / (price * (Decimal("1") + fee_bps / _BPS))
            quantity = min(quantity, _round_down(affordable, self.config.quantity_step))
        else:
            quantity = min(quantity, self._base)
        quantity = _round_down(quantity, self.config.quantity_step)
        if (
            quantity < self.config.minimum_quantity
            or quantity * price < self.config.minimum_notional
        ):
            self._complete_pending(trade.trade_time, SimulatedOrderStatus.REJECTED)
            return
        self._apply_fill(order, trade.trade_time, quantity, price, fee_bps)
        order.remaining_quantity -= quantity
        if order.remaining_quantity < self.config.quantity_step:
            order.remaining_quantity = Decimal("0")
            self._complete_pending(trade.trade_time, SimulatedOrderStatus.FILLED)

    def _apply_fill(
        self,
        order: _PendingOrder,
        event_time: datetime,
        quantity: Decimal,
        price: Decimal,
        fee_bps: Decimal,
    ) -> None:
        gross = quantity * price
        fee = gross * fee_bps / _BPS
        if order.side is OrderSide.BUY:
            if self._cycle is None:
                self._cycle = _CycleAccumulator(
                    opened_at=event_time,
                    entry_reason=order.reason,
                )
            self._quote -= gross + fee
            self._base += quantity
            self._position_cost += gross + fee
            self._cycle.entry_quantity += quantity
            self._cycle.entry_gross += gross
            self._cycle.entry_fees += fee
        else:
            if self._cycle is None or self._base <= 0:
                raise RuntimeError("simulated sell fill without an open cycle")
            base_before = self._base
            allocated_cost = self._position_cost * quantity / base_before
            self._quote += gross - fee
            self._base -= quantity
            self._position_cost -= allocated_cost
            self._cycle.exit_quantity += quantity
            self._cycle.exit_gross += gross
            self._cycle.exit_fees += fee
            self._cycle.exit_reason = order.reason
        self._fills.append(
            SimulatedFill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                style=order.style,
                event_time=event_time,
                quantity=quantity,
                price=price,
                gross_quote=gross,
                fee_quote=fee,
                reason=order.reason,
            )
        )
        self._record_equity(event_time, price)
        if order.side is OrderSide.SELL and self._base < self.config.quantity_step:
            self._base = Decimal("0")
            self._position_cost = Decimal("0")
            self._complete_cycle(event_time)

    def _complete_cycle(self, event_time: datetime) -> None:
        cycle = self._cycle
        if cycle is None or cycle.entry_quantity <= 0 or cycle.exit_quantity <= 0:
            raise RuntimeError("cannot complete an empty simulated cycle")
        entry_vwap = cycle.entry_gross / cycle.entry_quantity
        exit_vwap = cycle.exit_gross / cycle.exit_quantity
        gross_pnl = cycle.exit_gross - cycle.entry_gross
        fees = cycle.entry_fees + cycle.exit_fees
        net_pnl = gross_pnl - fees
        invested = cycle.entry_gross + cycle.entry_fees
        self._cycles.append(
            CompletedCycle(
                symbol=self._symbol or "",
                opened_at=cycle.opened_at,
                closed_at=event_time,
                entry_quantity=cycle.entry_quantity,
                entry_vwap=entry_vwap,
                exit_vwap=exit_vwap,
                gross_pnl=gross_pnl,
                fees=fees,
                net_pnl=net_pnl,
                return_fraction=net_pnl / invested,
                entry_reason=cycle.entry_reason,
                exit_reason=cycle.exit_reason,
            )
        )
        self._cycle = None

    def _expire_pending(self, event_time: datetime) -> None:
        if self._pending is not None and event_time >= self._pending.expires_at:
            filled = self._pending.requested_quantity - self._pending.remaining_quantity
            status = (
                SimulatedOrderStatus.PARTIAL_EXPIRED if filled > 0 else SimulatedOrderStatus.EXPIRED
            )
            self._complete_pending(event_time, status)

    def _cancel_pending(self, event_time: datetime) -> None:
        if self._pending is None:
            return
        filled = self._pending.requested_quantity - self._pending.remaining_quantity
        status = (
            SimulatedOrderStatus.PARTIAL_CANCELLED if filled > 0 else SimulatedOrderStatus.CANCELLED
        )
        self._complete_pending(event_time, status)

    def _complete_pending(self, event_time: datetime, status: SimulatedOrderStatus) -> None:
        order = self._pending
        if order is None:
            return
        self._orders.append(
            SimulatedOrder(
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side,
                style=order.style,
                created_at=order.created_at,
                completed_at=event_time,
                requested_quantity=order.requested_quantity,
                filled_quantity=order.requested_quantity - order.remaining_quantity,
                limit_price=order.limit_price,
                status=status,
                reason=order.reason,
            )
        )
        self._pending = None

    def _force_liquidation(self, event_time: datetime) -> None:
        assert self._symbol is not None
        assert self._last_trade_price is not None
        quantity = _round_down(self._base, self.config.quantity_step)
        if quantity <= 0:
            return
        order = _PendingOrder(
            order_id=self._next_order_id,
            symbol=self._symbol,
            side=OrderSide.SELL,
            style=OrderStyle.TAKER,
            created_at=event_time,
            eligible_at=event_time,
            expires_at=event_time,
            requested_quantity=quantity,
            remaining_quantity=quantity,
            limit_price=None,
            reason="forced_end_liquidation",
        )
        self._pending = order
        self._next_order_id += 1
        price = self._last_trade_price * (Decimal("1") - self.config.exit_slippage_bps / _BPS)
        self._apply_fill(
            order,
            event_time,
            quantity,
            price,
            self.config.taker_fee_bps,
        )
        order.remaining_quantity = Decimal("0")
        self._complete_pending(event_time, SimulatedOrderStatus.FILLED)

    def _record_equity(self, event_time: datetime, mark_price: Decimal) -> None:
        equity = self._observe_equity(mark_price)
        point = EquityPoint(event_time=event_time, equity=equity)
        if self._equity and self._equity[-1].event_time == event_time:
            self._equity[-1] = point
        else:
            self._equity.append(point)

    def _observe_equity(self, mark_price: Decimal) -> Decimal:
        equity = self._quote + self._base * mark_price
        if equity > self._peak_equity:
            self._peak_equity = equity
        elif self._peak_equity > 0:
            drawdown = (self._peak_equity - equity) / self._peak_equity
            self._maximum_drawdown = max(self._maximum_drawdown, drawdown)
        return equity


def _round_down(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _round_price(value: Decimal, tick: Decimal, side: OrderSide) -> Decimal:
    rounding = ROUND_DOWN if side is OrderSide.BUY else ROUND_CEILING
    return (value / tick).to_integral_value(rounding=rounding) * tick


def _timedelta_microseconds(value: timedelta) -> int:
    return value.days * 86_400_000_000 + value.seconds * 1_000_000 + value.microseconds
