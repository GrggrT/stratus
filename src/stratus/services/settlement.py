from decimal import Decimal

from stratus.db.models import TradeSide


def position_pnl(
    *,
    side: TradeSide,
    resolved_yes: bool,
    size_shares: Decimal,
    cost_usd: Decimal,
) -> Decimal:
    won = (side is TradeSide.BUY_YES and resolved_yes) or (
        side is TradeSide.BUY_NO and not resolved_yes
    )
    payout = size_shares if won else Decimal(0)
    return (payout - cost_usd).quantize(Decimal("0.0001"))


def closing_line_value(
    *, traded_side_midpoint: Decimal, entry_price: Decimal
) -> Decimal:
    return (traded_side_midpoint - entry_price).quantize(Decimal("0.000001"))
