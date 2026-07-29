from datetime import UTC, datetime, timedelta
from decimal import Decimal

from stratus.db.models import TradeSide
from stratus.services.portfolio_marks import PriceMark, calculate_position_mark

NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def _price(
    yes_midpoint: str,
    *,
    yes_bid: str = "0.39",
    yes_ask: str = "0.41",
    no_midpoint: str = "0.57",
    no_bid: str = "0.55",
    no_ask: str = "0.59",
    age_minutes: int = 0,
) -> PriceMark:
    return PriceMark(
        yes_midpoint=Decimal(yes_midpoint),
        yes_best_bid=Decimal(yes_bid),
        yes_best_ask=Decimal(yes_ask),
        no_midpoint=Decimal(no_midpoint),
        no_best_bid=Decimal(no_bid),
        no_best_ask=Decimal(no_ask),
        ts=NOW - timedelta(minutes=age_minutes),
    )


def test_buy_no_mark_liquidation_staleness_and_moves() -> None:
    result = calculate_position_mark(
        side=TradeSide.BUY_NO,
        shares=Decimal("40"),
        cost_usd=Decimal("20"),
        latest=_price("0.40", age_minutes=20),
        at=NOW,
        one_minute=_price("0.45", no_midpoint="0.52", age_minutes=21),
        five_minutes=_price("0.48", no_midpoint="0.49", age_minutes=25),
        sixty_minutes=_price("0.50", no_midpoint="0.47", age_minutes=80),
    )

    # Actual NO midpoint/bid are 0.57/0.55. Synthetic complements of the
    # YES midpoint/ask would be 0.60/0.59 and must never be used.
    assert result.mark_price == Decimal("0.57")
    assert result.unrealized_pnl_usd == Decimal("2.8000")
    assert result.liquidation_price == Decimal("0.55")
    assert result.liquidation_pnl_usd == Decimal("2.0000")
    assert result.pnl_change_1m_usd == Decimal("2.0000")
    assert result.pnl_change_5m_usd == Decimal("3.2000")
    assert result.pnl_change_60m_usd == Decimal("4.0000")
    assert result.mark_stale is True
    assert result.mark_age_seconds == 1200


def test_liquidation_pnl_uses_executable_bid_and_exit_fee() -> None:
    result = calculate_position_mark(
        side=TradeSide.BUY_YES,
        shares=Decimal("100"),
        cost_usd=Decimal("50"),
        latest=_price("0.60", yes_bid="0.58", yes_ask="0.62"),
        at=NOW,
        fees_enabled=True,
        fee_rate=Decimal("0.02"),
    )

    assert result.unrealized_pnl_usd == Decimal("10.0000")
    assert result.liquidation_price == Decimal("0.58")
    assert result.liquidation_fee_usd == Decimal("0.48720")
    assert result.liquidation_pnl_usd == Decimal("7.5128")


def test_unknown_fee_exponent_refuses_to_invent_liquidation_value() -> None:
    result = calculate_position_mark(
        side=TradeSide.BUY_YES,
        shares=Decimal("100"),
        cost_usd=Decimal("50"),
        latest=_price("0.60", yes_bid="0.58", yes_ask="0.62"),
        at=NOW,
        fees_enabled=True,
        fee_rate=Decimal("0.02"),
        fee_exponent=2,
    )
    assert result.liquidation_price is None
    assert result.liquidation_fee_usd is None
    assert result.liquidation_pnl_usd is None
