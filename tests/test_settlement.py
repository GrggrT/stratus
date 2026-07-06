from decimal import Decimal

from stratus.db.models import TradeSide
from stratus.services.settlement import closing_line_value, position_pnl


def test_position_pnl_for_winner_and_loser() -> None:
    assert position_pnl(
        side=TradeSide.BUY_YES,
        resolved_yes=True,
        size_shares=Decimal("20"),
        cost_usd=Decimal("8"),
    ) == Decimal("12.0000")
    assert position_pnl(
        side=TradeSide.BUY_NO,
        resolved_yes=True,
        size_shares=Decimal("20"),
        cost_usd=Decimal("8"),
    ) == Decimal("-8.0000")


def test_closing_line_value_uses_the_traded_side() -> None:
    assert closing_line_value(
        side=TradeSide.BUY_YES,
        yes_midpoint=Decimal("0.65"),
        entry_price=Decimal("0.60"),
    ) == Decimal("0.050000")
    assert closing_line_value(
        side=TradeSide.BUY_NO,
        yes_midpoint=Decimal("0.35"),
        entry_price=Decimal("0.60"),
    ) == Decimal("0.050000")
