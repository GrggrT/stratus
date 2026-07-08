from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


class LiveOrderRejected(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class LiveReadiness:
    ready: bool
    reasons: tuple[str, ...]


def evaluate_live_readiness(
    *,
    legal_approved: bool,
    tax_approved: bool,
    private_key_present: bool,
    mode_is_live: bool,
    kill_switch: bool,
    halted_reason: str | None,
    geoblock_blocked: bool | None,
) -> LiveReadiness:
    """Static gates that must all pass before a single live order is signed.

    The legal/tax flags are the operator's personal sign-off (set in .env);
    the runtime never flips them. An unknown geoblock state counts as
    blocked — this check is required before every order, not once per day.
    """
    reasons = []
    if not legal_approved:
        reasons.append("legal_not_approved")
    if not tax_approved:
        reasons.append("tax_not_approved")
    if not private_key_present:
        reasons.append("private_key_missing")
    if not mode_is_live:
        reasons.append("mode_not_live")
    if kill_switch:
        reasons.append("kill_switch")
    if halted_reason:
        reasons.append("project_halted")
    if geoblock_blocked is None:
        reasons.append("geoblock_unknown")
    elif geoblock_blocked:
        reasons.append("geoblock_blocked")
    return LiveReadiness(not reasons, tuple(reasons))


@dataclass(frozen=True, slots=True)
class LiveOrderPlan:
    token_id: str
    price: Decimal
    size_shares: Decimal
    cost_usd: Decimal
    clamped: bool


def plan_live_buy(
    *,
    token_id: str,
    max_price: Decimal,
    size_usd: Decimal,
    tick_size: Decimal,
    min_size_shares: Decimal,
    max_order_usd: Decimal,
) -> LiveOrderPlan:
    """Convert a signal into a GTC limit BUY the CLOB will accept.

    Every adjustment moves the order DOWN (price to the tick below the cap,
    notional to the physical ceiling); anything that cannot be satisfied
    raises instead of being silently corrected upward.
    """
    if not token_id:
        raise LiveOrderRejected("token_id is required")
    if size_usd <= 0:
        raise LiveOrderRejected("order size must be positive")
    if tick_size <= 0:
        raise LiveOrderRejected("tick size must be positive")
    if not Decimal(0) < max_price < Decimal(1):
        raise LiveOrderRejected("max price must be between zero and one")

    effective_usd = min(size_usd, max_order_usd)
    price = (max_price / tick_size).to_integral_value(rounding=ROUND_DOWN) * tick_size
    if price <= 0:
        raise LiveOrderRejected(f"price cap {max_price} rounds to zero at tick {tick_size}")

    size_shares = (effective_usd / price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
    if size_shares < min_size_shares:
        raise LiveOrderRejected(
            f"size {size_shares} shares is below the market minimum {min_size_shares}"
        )

    return LiveOrderPlan(
        token_id=token_id,
        price=price,
        size_shares=size_shares,
        cost_usd=(price * size_shares).quantize(Decimal("0.0001")),
        clamped=effective_usd < size_usd,
    )
