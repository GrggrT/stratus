from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from stratus.backtesting.engine import (
    BACKTEST_ENGINE_VERSION,
    BacktestConfig,
    BacktestStrategy,
    OrderStyle,
)
from stratus.backtesting.strategies import MovingAverageCrossStrategy
from stratus.backtesting.windows import (
    WalkForwardPlan,
    build_expanding_walk_forward_plan,
    walk_forward_plan_payload,
)

_BPS = Decimal("10000")
RESEARCH_PROTOCOL_VERSION = "binance-walk-forward-v1"


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _require_payload_keys(payload: dict[str, Any], expected: set[str]) -> None:
    if set(payload) != expected:
        raise ValueError("research payload fields are invalid")


def _payload_decimal(payload: dict[str, Any], key: str) -> Decimal:
    value = payload[key]
    if not isinstance(value, str):
        raise ValueError(f"research payload {key} must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"research payload {key} is invalid") from exc
    if not parsed.is_finite():
        raise ValueError(f"research payload {key} must be finite")
    return parsed


def _payload_int(payload: dict[str, Any], key: str) -> int:
    value = payload[key]
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"research payload {key} must be an integer")
    return value


def _payload_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ValueError(f"research payload {key} must be a boolean")
    return value


def canonical_payload_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ResearchExecutionConfig:
    initial_quote_per_symbol: Decimal
    order_notional: Decimal
    maker_fee_bps: Decimal
    taker_fee_bps: Decimal
    entry_slippage_bps: Decimal
    exit_slippage_bps: Decimal
    latency_ms: int
    order_ttl_seconds: int
    maker_participation_rate: Decimal
    liquidate_at_end: bool = True

    def __post_init__(self) -> None:
        decimal_values = (
            self.initial_quote_per_symbol,
            self.order_notional,
            self.maker_fee_bps,
            self.taker_fee_bps,
            self.entry_slippage_bps,
            self.exit_slippage_bps,
            self.maker_participation_rate,
        )
        if any(not value.is_finite() for value in decimal_values):
            raise ValueError("research execution decimals must be finite")
        for field_name in (
            "initial_quote_per_symbol",
            "order_notional",
            "maker_fee_bps",
            "taker_fee_bps",
            "entry_slippage_bps",
            "exit_slippage_bps",
            "maker_participation_rate",
        ):
            value = getattr(self, field_name)
            object.__setattr__(self, field_name, Decimal(_decimal_text(value)))
        if self.initial_quote_per_symbol <= 0 or self.order_notional <= 0:
            raise ValueError("research quote balances must be positive")
        costs = (
            self.maker_fee_bps,
            self.taker_fee_bps,
            self.entry_slippage_bps,
            self.exit_slippage_bps,
        )
        if any(value < 0 or value >= _BPS for value in costs):
            raise ValueError("research fee/slippage bps must be in [0, 10000)")
        if self.entry_slippage_bps == 0 and self.exit_slippage_bps == 0:
            raise ValueError("research slippage stress must be non-zero")
        if self.latency_ms < 0 or self.order_ttl_seconds <= 0:
            raise ValueError("research latency/TTL are invalid")
        if not Decimal("0") < self.maker_participation_rate <= Decimal("1"):
            raise ValueError("research maker participation must be in (0, 1]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "initial_quote_per_symbol": _decimal_text(self.initial_quote_per_symbol),
            "order_notional": _decimal_text(self.order_notional),
            "maker_fee_bps": _decimal_text(self.maker_fee_bps),
            "taker_fee_bps": _decimal_text(self.taker_fee_bps),
            "entry_slippage_bps": _decimal_text(self.entry_slippage_bps),
            "exit_slippage_bps": _decimal_text(self.exit_slippage_bps),
            "latency_ms": self.latency_ms,
            "order_ttl_seconds": self.order_ttl_seconds,
            "maker_participation_rate": _decimal_text(self.maker_participation_rate),
            "liquidate_at_end": self.liquidate_at_end,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ResearchExecutionConfig:
        _require_payload_keys(
            payload,
            {
                "initial_quote_per_symbol",
                "order_notional",
                "maker_fee_bps",
                "taker_fee_bps",
                "entry_slippage_bps",
                "exit_slippage_bps",
                "latency_ms",
                "order_ttl_seconds",
                "maker_participation_rate",
                "liquidate_at_end",
            },
        )
        return cls(
            initial_quote_per_symbol=_payload_decimal(payload, "initial_quote_per_symbol"),
            order_notional=_payload_decimal(payload, "order_notional"),
            maker_fee_bps=_payload_decimal(payload, "maker_fee_bps"),
            taker_fee_bps=_payload_decimal(payload, "taker_fee_bps"),
            entry_slippage_bps=_payload_decimal(payload, "entry_slippage_bps"),
            exit_slippage_bps=_payload_decimal(payload, "exit_slippage_bps"),
            latency_ms=_payload_int(payload, "latency_ms"),
            order_ttl_seconds=_payload_int(payload, "order_ttl_seconds"),
            maker_participation_rate=_payload_decimal(payload, "maker_participation_rate"),
            liquidate_at_end=_payload_bool(payload, "liquidate_at_end"),
        )

    def canonical_hash(self) -> str:
        return canonical_payload_hash(self.to_payload())

    def build_backtest_config(
        self,
        *,
        price_tick: Decimal,
        quantity_step: Decimal,
        minimum_quantity: Decimal,
        minimum_notional: Decimal,
    ) -> BacktestConfig:
        return BacktestConfig(
            initial_quote=self.initial_quote_per_symbol,
            order_notional=self.order_notional,
            maker_fee_bps=self.maker_fee_bps,
            taker_fee_bps=self.taker_fee_bps,
            entry_slippage_bps=self.entry_slippage_bps,
            exit_slippage_bps=self.exit_slippage_bps,
            latency=timedelta(milliseconds=self.latency_ms),
            order_ttl=timedelta(seconds=self.order_ttl_seconds),
            maker_participation_rate=self.maker_participation_rate,
            price_tick=price_tick,
            quantity_step=quantity_step,
            minimum_quantity=minimum_quantity,
            minimum_notional=minimum_notional,
            liquidate_at_end=self.liquidate_at_end,
        )


@dataclass(frozen=True, slots=True)
class ResearchSelectionPolicy:
    bootstrap_samples: int = 2000
    minimum_oos_cycles: int = 300
    minimum_symbol_cycles: int = 50
    maximum_drawdown: Decimal = Decimal("0.10")
    minimum_positive_neighbors: int = 2
    maximum_symbol_profit_contribution: Decimal = Decimal("0.80")

    def __post_init__(self) -> None:
        if not self.maximum_drawdown.is_finite():
            raise ValueError("research maximum_drawdown must be finite")
        if not self.maximum_symbol_profit_contribution.is_finite():
            raise ValueError("symbol contribution cap must be finite")
        object.__setattr__(
            self,
            "maximum_drawdown",
            Decimal(_decimal_text(self.maximum_drawdown)),
        )
        object.__setattr__(
            self,
            "maximum_symbol_profit_contribution",
            Decimal(_decimal_text(self.maximum_symbol_profit_contribution)),
        )
        if self.bootstrap_samples < 100:
            raise ValueError("research bootstrap_samples must be at least 100")
        if self.minimum_oos_cycles < 1 or self.minimum_symbol_cycles < 1:
            raise ValueError("research cycle thresholds must be positive")
        if self.minimum_positive_neighbors < 0:
            raise ValueError("minimum_positive_neighbors must be non-negative")
        if not Decimal("0") < self.maximum_drawdown < Decimal("1"):
            raise ValueError("research maximum_drawdown must be in (0, 1)")
        if not Decimal("0") < self.maximum_symbol_profit_contribution <= Decimal("1"):
            raise ValueError("symbol contribution cap must be in (0, 1]")

    def to_payload(self) -> dict[str, Any]:
        return {
            "bootstrap_samples": self.bootstrap_samples,
            "minimum_oos_cycles": self.minimum_oos_cycles,
            "minimum_symbol_cycles": self.minimum_symbol_cycles,
            "maximum_drawdown": _decimal_text(self.maximum_drawdown),
            "minimum_positive_neighbors": self.minimum_positive_neighbors,
            "maximum_symbol_profit_contribution": _decimal_text(
                self.maximum_symbol_profit_contribution
            ),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ResearchSelectionPolicy:
        _require_payload_keys(
            payload,
            {
                "bootstrap_samples",
                "minimum_oos_cycles",
                "minimum_symbol_cycles",
                "maximum_drawdown",
                "minimum_positive_neighbors",
                "maximum_symbol_profit_contribution",
            },
        )
        return cls(
            bootstrap_samples=_payload_int(payload, "bootstrap_samples"),
            minimum_oos_cycles=_payload_int(payload, "minimum_oos_cycles"),
            minimum_symbol_cycles=_payload_int(payload, "minimum_symbol_cycles"),
            maximum_drawdown=_payload_decimal(payload, "maximum_drawdown"),
            minimum_positive_neighbors=_payload_int(payload, "minimum_positive_neighbors"),
            maximum_symbol_profit_contribution=_payload_decimal(
                payload, "maximum_symbol_profit_contribution"
            ),
        )


@dataclass(frozen=True, slots=True)
class MovingAverageCandidateSpec:
    fast_period: int
    slow_period: int
    order_style: OrderStyle = OrderStyle.TAKER
    maker_offset_bps: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not self.maker_offset_bps.is_finite():
            raise ValueError("maker_offset_bps must be finite")
        object.__setattr__(
            self,
            "maker_offset_bps",
            Decimal(_decimal_text(self.maker_offset_bps)),
        )
        self.build_strategy()

    def to_payload(self) -> dict[str, Any]:
        return {
            "strategy_name": "moving_average_cross",
            "fast_period": self.fast_period,
            "slow_period": self.slow_period,
            "order_style": self.order_style.value,
            "maker_offset_bps": _decimal_text(self.maker_offset_bps),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MovingAverageCandidateSpec:
        _require_payload_keys(
            payload,
            {
                "strategy_name",
                "fast_period",
                "slow_period",
                "order_style",
                "maker_offset_bps",
            },
        )
        if payload.get("strategy_name") != "moving_average_cross":
            raise ValueError("unsupported research strategy")
        order_style = payload["order_style"]
        if not isinstance(order_style, str):
            raise ValueError("research payload order_style must be a string")
        return cls(
            fast_period=_payload_int(payload, "fast_period"),
            slow_period=_payload_int(payload, "slow_period"),
            order_style=OrderStyle(order_style),
            maker_offset_bps=_payload_decimal(payload, "maker_offset_bps"),
        )

    @property
    def candidate_key(self) -> str:
        return canonical_payload_hash(self.to_payload())

    @property
    def strategy_version(self) -> str:
        return self.build_strategy().version

    def build_strategy(self) -> BacktestStrategy:
        return MovingAverageCrossStrategy(
            fast_period=self.fast_period,
            slow_period=self.slow_period,
            order_style=self.order_style,
            maker_offset_bps=self.maker_offset_bps,
        )


@dataclass(frozen=True, slots=True)
class ResearchExperimentSpec:
    name: str
    symbols: tuple[str, ...]
    interval: str
    dataset_start: datetime
    dataset_end: datetime
    minimum_train: timedelta
    validation: timedelta
    test: timedelta
    step: timedelta
    final_holdout: timedelta
    execution: ResearchExecutionConfig
    selection: ResearchSelectionPolicy
    candidates: tuple[MovingAverageCandidateSpec, ...]

    def __post_init__(self) -> None:
        if not self.name or self.name != self.name.strip() or len(self.name) > 128:
            raise ValueError("research experiment name is invalid")
        if not self.symbols or len(set(self.symbols)) != len(self.symbols):
            raise ValueError("research symbols must be non-empty and unique")
        if any(
            not symbol.isascii() or not symbol.isalnum() or symbol != symbol.upper()
            for symbol in self.symbols
        ):
            raise ValueError("research symbols must be uppercase Binance identifiers")
        object.__setattr__(self, "symbols", tuple(sorted(self.symbols)))
        if self.interval != "5m":
            raise ValueError("research protocol v1 requires the 5m interval")
        if not self.candidates:
            raise ValueError("research candidate set is empty")
        if len(self.candidates) > 256:
            raise ValueError("research candidate set exceeds 256 entries")
        keys = [candidate.candidate_key for candidate in self.candidates]
        if len(set(keys)) != len(keys):
            raise ValueError("research candidate set contains duplicates")
        _ = self.plan

    @property
    def plan(self) -> WalkForwardPlan:
        return build_expanding_walk_forward_plan(
            dataset_start=self.dataset_start,
            dataset_end=self.dataset_end,
            minimum_train=self.minimum_train,
            validation=self.validation,
            test=self.test,
            step=self.step,
            final_holdout=self.final_holdout,
        )

    @property
    def candidate_set_hash(self) -> str:
        payloads = [
            candidate.to_payload()
            for candidate in sorted(self.candidates, key=lambda item: item.candidate_key)
        ]
        return canonical_payload_hash(payloads)

    @property
    def experiment_key(self) -> str:
        payload = {
            "name": self.name,
            "symbols": list(self.symbols),
            "interval": self.interval,
            "dataset_start": self.dataset_start.astimezone(UTC).isoformat(),
            "dataset_end": self.dataset_end.astimezone(UTC).isoformat(),
            "plan_hash": self.plan.plan_hash,
            "candidate_set_hash": self.candidate_set_hash,
            "execution_config_hash": self.execution.canonical_hash(),
            "selection_policy": self.selection.to_payload(),
            "engine_version": BACKTEST_ENGINE_VERSION,
            "protocol_version": RESEARCH_PROTOCOL_VERSION,
        }
        return canonical_payload_hash(payload)

    def plan_payload(self) -> dict[str, Any]:
        return walk_forward_plan_payload(self.plan)
