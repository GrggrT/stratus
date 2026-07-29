from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from stratus.db.models import TradeSide
from stratus.services.execution import taker_fee


@dataclass(frozen=True, slots=True)
class PriceMark:
    yes_midpoint: Decimal
    yes_best_bid: Decimal
    yes_best_ask: Decimal
    no_midpoint: Decimal
    no_best_bid: Decimal
    no_best_ask: Decimal
    ts: datetime


@dataclass(frozen=True, slots=True)
class PositionMark:
    mark_price: Decimal
    mark_at: datetime
    mark_age_seconds: int
    mark_stale: bool
    unrealized_pnl_usd: Decimal
    liquidation_price: Decimal | None
    liquidation_fee_usd: Decimal | None
    liquidation_pnl_usd: Decimal | None
    pnl_change_1m_usd: Decimal | None
    pnl_change_5m_usd: Decimal | None
    pnl_change_60m_usd: Decimal | None


def _side_midpoint(side: TradeSide, mark: PriceMark) -> Decimal:
    return mark.yes_midpoint if side is TradeSide.BUY_YES else mark.no_midpoint


def _side_bid(side: TradeSide, mark: PriceMark) -> Decimal:
    return mark.yes_best_bid if side is TradeSide.BUY_YES else mark.no_best_bid


def _pnl(*, shares: Decimal, cost_usd: Decimal, price: Decimal) -> Decimal:
    return (shares * price - cost_usd).quantize(Decimal("0.0001"))


def calculate_position_mark(
    *,
    side: TradeSide,
    shares: Decimal,
    cost_usd: Decimal,
    latest: PriceMark,
    at: datetime,
    one_minute: PriceMark | None = None,
    five_minutes: PriceMark | None = None,
    sixty_minutes: PriceMark | None = None,
    fees_enabled: bool = False,
    fee_rate: Decimal = Decimal(0),
    fee_exponent: int = 1,
    stale_after_seconds: int = 900,
) -> PositionMark:
    side_midpoint = _side_midpoint(side, latest)
    unrealized = _pnl(shares=shares, cost_usd=cost_usd, price=side_midpoint)
    age_seconds = max(0, round((at - latest.ts).total_seconds()))

    liquidation_candidate = _side_bid(side, latest)
    liquidation_price: Decimal | None = (
        liquidation_candidate
        if Decimal(0) < liquidation_candidate < Decimal(1) and fee_exponent == 1
        else None
    )

    liquidation_fee: Decimal | None = None
    liquidation_pnl: Decimal | None = None
    if liquidation_price is not None:
        liquidation_fee = taker_fee(
            shares,
            liquidation_price,
            fee_rate if fees_enabled else Decimal(0),
        )
        liquidation_pnl = (shares * liquidation_price - liquidation_fee - cost_usd).quantize(
            Decimal("0.0001")
        )

    def change(previous: PriceMark | None) -> Decimal | None:
        if previous is None:
            return None
        previous_pnl = _pnl(
            shares=shares,
            cost_usd=cost_usd,
            price=_side_midpoint(side, previous),
        )
        return (unrealized - previous_pnl).quantize(Decimal("0.0001"))

    return PositionMark(
        mark_price=side_midpoint,
        mark_at=latest.ts,
        mark_age_seconds=age_seconds,
        mark_stale=age_seconds > stale_after_seconds,
        unrealized_pnl_usd=unrealized,
        liquidation_price=liquidation_price,
        liquidation_fee_usd=liquidation_fee,
        liquidation_pnl_usd=liquidation_pnl,
        pnl_change_1m_usd=change(one_minute),
        pnl_change_5m_usd=change(five_minutes),
        pnl_change_60m_usd=change(sixty_minutes),
    )
