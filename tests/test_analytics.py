from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.db.models import (
    ExecutionMode,
    SignalConfidence,
    TradeSide,
    WeatherKind,
)
from stratus.repositories.analytics import actual_order_fill, resolved_trade_outcomes


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, ...]]:
        return self._rows


class _Session:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self._rows = rows

    async def execute(self, _statement: object) -> _Rows:
        return _Rows(self._rows)


def _resolved_row(*, order_fee: Decimal | None) -> tuple[object, ...]:
    now = datetime(2026, 7, 15, 12, tzinfo=UTC)
    return (
        7,
        TradeSide.BUY_YES,
        Decimal("0.700000"),
        Decimal("5.7481"),
        order_fee,
        1,
        Decimal("0.73000000"),
        Decimal("5.748064"),
        None,
        Decimal("7.874060"),
        Decimal("0.700000"),
        Decimal("0.700000"),
        Decimal("0.100000"),
        SignalConfidence.A,
        now - timedelta(hours=3),
        Decimal("0.600000"),
        Decimal("0.600000"),
        {"pipeline": {"version": "test-v1"}},
        WeatherKind.HIGH,
        date(2026, 7, 15),
        now,
        True,
        "KLGA",
    )


def test_actual_order_fill_prefers_immutable_leg_aggregate() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.700000"),
        order_filled_usd=Decimal("5.511842"),
        order_fee_usd=Decimal("0.12"),
        execution_intent_id=1,
        leg_vwap_fill_price=Decimal("0.73000000"),
        leg_filled_notional_usd=Decimal("5.748064"),
        leg_fee_usd=Decimal("0.01000000"),
        leg_total_shares=Decimal("7.874060"),
    )

    assert fill is not None
    assert fill.entry_price == Decimal("0.73000000")
    assert fill.filled_notional_usd == Decimal("5.748064")
    assert fill.fee_usd == Decimal("0.01000000")
    assert fill.shares == Decimal("7.874060")


def test_actual_order_fill_falls_back_for_legacy_order_without_legs() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.625"),
        order_filled_usd=Decimal("5.0000"),
        order_fee_usd=Decimal("0.012"),
        execution_intent_id=None,
        leg_vwap_fill_price=None,
        leg_filled_notional_usd=None,
        leg_fee_usd=None,
        leg_total_shares=None,
    )

    assert fill is not None
    assert fill.entry_price == Decimal("0.625")
    assert fill.filled_notional_usd == Decimal("5.0000")
    assert fill.fee_usd == Decimal("0.012")
    assert fill.shares == Decimal("8")


def test_actual_order_fill_uses_known_order_fee_when_leg_fee_is_unavailable() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.700000"),
        order_filled_usd=Decimal("5.7481"),
        order_fee_usd=Decimal("0.01234"),
        execution_intent_id=1,
        leg_vwap_fill_price=Decimal("0.73000000"),
        leg_filled_notional_usd=Decimal("5.748064"),
        leg_fee_usd=None,
        leg_total_shares=Decimal("7.874060"),
    )

    assert fill is not None
    assert fill.fee_usd == Decimal("0.01234")


def test_actual_order_fill_preserves_unknown_fee_for_fail_closed_scoring() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.700000"),
        order_filled_usd=Decimal("5.7481"),
        order_fee_usd=None,
        execution_intent_id=1,
        leg_vwap_fill_price=Decimal("0.73000000"),
        leg_filled_notional_usd=Decimal("5.748064"),
        leg_fee_usd=None,
        leg_total_shares=Decimal("7.874060"),
    )

    assert fill is not None
    assert fill.fee_usd is None


def test_actual_order_fill_fails_closed_for_incomplete_leg_aggregate() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.625"),
        order_filled_usd=Decimal("5.0000"),
        order_fee_usd=None,
        execution_intent_id=1,
        leg_vwap_fill_price=None,
        leg_filled_notional_usd=Decimal("5.0000"),
        leg_fee_usd=None,
        leg_total_shares=Decimal("8"),
    )

    assert fill is None


def test_actual_order_fill_does_not_treat_modern_missing_legs_as_legacy() -> None:
    fill = actual_order_fill(
        order_price=Decimal("0.625"),
        order_filled_usd=Decimal("5.0000"),
        order_fee_usd=Decimal("0.01"),
        execution_intent_id=42,
        leg_vwap_fill_price=None,
        leg_filled_notional_usd=None,
        leg_fee_usd=None,
        leg_total_shares=None,
    )

    assert fill is None


@pytest.mark.asyncio
async def test_resolved_trade_outcome_scores_known_order_fee_fallback() -> None:
    outcomes = await resolved_trade_outcomes(  # type: ignore[arg-type]
        _Session([_resolved_row(order_fee=Decimal("0.01234"))]),
        ExecutionMode.PAPER,
    )

    assert len(outcomes) == 1
    assert outcomes[0].cost_usd == 5.7604
    assert outcomes[0].pnl_usd == 2.1137


@pytest.mark.asyncio
async def test_resolved_trade_outcome_excludes_unknown_fee_from_net_scoring() -> None:
    outcomes = await resolved_trade_outcomes(  # type: ignore[arg-type]
        _Session([_resolved_row(order_fee=None)]),
        ExecutionMode.PAPER,
    )

    assert outcomes == []
