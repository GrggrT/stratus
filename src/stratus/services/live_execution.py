from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from typing import Any


class LiveOrderRejected(ValueError):
    pass


# CLOB order statuses that mean no further fills will arrive.
_TERMINAL_ORDER_STATUSES = frozenset({"matched", "canceled", "cancelled"})
# Trade statuses that represent a settled fill (vs. failed/retrying).
_CONFIRMED_TRADE_STATUSES = frozenset({"matched", "mined", "confirmed"})


@dataclass(frozen=True, slots=True)
class TradeFill:
    matched_shares: Decimal
    avg_price: Decimal
    cost_usd: Decimal
    fee_usd: Decimal


def _matched_leg(
    trade: dict[str, Any], clob_order_id: str
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Our (shares, price, fee_rate_bps) in one trade, whether we were the
    taker or one of the makers. Returns None if the order is not in the trade.

    NOTE: the Trade field names (taker_order_id, maker_orders[].order_id /
    matched_amount, fee_rate_bps) follow the documented CLOB schema but have
    not been checked against a live response — confirm on the first real
    trade. The caller falls back to the order's limit price if this yields
    nothing, so a field mismatch degrades gracefully rather than corrupting.
    """
    try:
        if trade.get("taker_order_id") == clob_order_id:
            return (
                Decimal(str(trade["size"])),
                Decimal(str(trade["price"])),
                Decimal(str(trade.get("fee_rate_bps", 0))),
            )
        makers = trade.get("maker_orders")
        if isinstance(makers, list):
            for leg in makers:
                if isinstance(leg, dict) and leg.get("order_id") == clob_order_id:
                    return (
                        Decimal(str(leg["matched_amount"])),
                        Decimal(str(leg["price"])),
                        Decimal(str(leg.get("fee_rate_bps", 0))),
                    )
    except (KeyError, TypeError, InvalidOperation):
        return None
    return None


def aggregate_order_trades(
    trades: list[dict[str, Any]], clob_order_id: str
) -> TradeFill | None:
    """VWAP fill for one order from its confirmed trades. None if there are no
    usable confirmed trades (the poller then falls back to the limit price)."""
    shares = Decimal(0)
    cost = Decimal(0)
    fee = Decimal(0)
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        status = trade.get("status")
        if not isinstance(status, str) or status.lower() not in _CONFIRMED_TRADE_STATUSES:
            continue
        leg = _matched_leg(trade, clob_order_id)
        if leg is None:
            continue
        leg_shares, leg_price, fee_bps = leg
        if leg_shares <= 0 or not Decimal(0) < leg_price < Decimal(1):
            continue
        shares += leg_shares
        cost += leg_price * leg_shares
        fee += fee_bps / Decimal(10000) * leg_price * leg_shares
    if shares <= 0:
        return None
    return TradeFill(
        matched_shares=shares,
        avg_price=(cost / shares).quantize(Decimal("0.000001")),
        cost_usd=cost.quantize(Decimal("0.0001")),
        fee_usd=fee.quantize(Decimal("0.00001")),
    )


@dataclass(frozen=True, slots=True)
class OrderFill:
    matched_shares: Decimal
    avg_price: Decimal
    cost_usd: Decimal
    status: str
    terminal: bool


def parse_order_fill(payload: Any) -> OrderFill | None:
    """Read cumulative fill state from a CLOB get_order response.

    Fail-closed: an unparseable payload returns None (the poller leaves the
    order untouched) rather than inventing a fill. avg_price uses the order's
    limit price — a conservative upper bound on a BUY's actual cost, since
    limit BUYs fill at or below their price.
    """
    if not isinstance(payload, dict):
        return None
    try:
        matched = Decimal(str(payload["size_matched"]))
        price = Decimal(str(payload["price"]))
    except (KeyError, TypeError, InvalidOperation):
        return None
    if matched < 0 or not Decimal(0) < price < Decimal(1):
        return None
    raw_status = payload.get("status")
    status = raw_status.lower() if isinstance(raw_status, str) else "unknown"
    return OrderFill(
        matched_shares=matched,
        avg_price=price,
        cost_usd=(matched * price).quantize(Decimal("0.0001")),
        status=status,
        terminal=status in _TERMINAL_ORDER_STATUSES,
    )


def live_executor_configured(private_key: str | None) -> bool:
    """Whether the trading wallet is wired up. Distinct from readiness: this
    is about deployment, not the per-order legal/geoblock/mode gates."""
    return bool(private_key)


def placed_order_status(clob_status: str) -> str:
    """Map a CLOB placement status to our order lifecycle.

    A successful placement acknowledgement does not contain the authoritative
    cumulative fill quantity, VWAP, or fee evidence.  Keep every acknowledged
    order pollable until the reconciliation path has persisted those values;
    that path alone may move it to a terminal filled/partially-filled state.
    """
    del clob_status
    return "open"


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
