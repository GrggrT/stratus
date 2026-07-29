from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any


class PaperExecutionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperFill:
    avg_price: Decimal
    filled_usd: Decimal
    size_shares: Decimal
    fee_usd: Decimal
    total_cost_usd: Decimal


def taker_fee(
    shares: Decimal, price: Decimal, fee_rate: Decimal
) -> Decimal:
    if fee_rate <= 0:
        return Decimal(0)
    return (shares * fee_rate * price * (Decimal(1) - price)).quantize(
        Decimal("0.00001")
    )


def executable_ask_levels(
    book: dict[str, Any], *, max_price: Decimal
) -> list[tuple[Decimal, Decimal]]:
    if not Decimal(0) < max_price < Decimal(1):
        raise PaperExecutionError("max_price must be between zero and one")
    raw_asks = book.get("asks")
    if not isinstance(raw_asks, list):
        raise PaperExecutionError("order book is missing asks")

    levels: list[tuple[Decimal, Decimal]] = []
    for item in raw_asks:
        if not isinstance(item, dict):
            raise PaperExecutionError("ask level must be an object")
        try:
            price = Decimal(str(item["price"]))
            shares = Decimal(str(item["size"]))
        except (KeyError, InvalidOperation) as exc:
            raise PaperExecutionError("ask level is invalid") from exc
        if not Decimal(0) < price < Decimal(1) or shares <= 0:
            raise PaperExecutionError("ask level is out of range")
        if price <= max_price:
            levels.append((price, shares))
    return levels


def usable_ask_depth_usd(book: dict[str, Any], *, max_price: Decimal) -> Decimal:
    """Notional (price x size) purchasable at or below max_price.

    Depth measured on the book the order actually executes against, unlike the
    generation-side proxy derived from the YES snapshot.
    """
    return sum(
        (price * shares for price, shares in executable_ask_levels(book, max_price=max_price)),
        start=Decimal(0),
    )


def walk_ask_book(
    book: dict[str, Any],
    *,
    target_usd: Decimal,
    max_price: Decimal,
    fee_rate: Decimal = Decimal(0),
) -> PaperFill | None:
    if target_usd <= 0:
        raise PaperExecutionError("target_usd must be positive")
    levels = executable_ask_levels(book, max_price=max_price)

    remaining = target_usd
    filled_usd = Decimal(0)
    filled_shares = Decimal(0)
    filled_fee = Decimal(0)
    for price, available_shares in sorted(levels):
        fee_per_share = fee_rate * price * (Decimal(1) - price)
        cash_per_share = price + fee_per_share
        level_shares = min(available_shares, remaining / cash_per_share)
        if level_shares <= 0:
            break
        level_usd = price * level_shares
        level_fee = fee_per_share * level_shares
        filled_usd += level_usd
        filled_shares += level_shares
        filled_fee += level_fee
        remaining -= level_usd + level_fee
        if remaining <= Decimal("0.0000001"):
            break

    if filled_usd == 0:
        return None
    fee_usd = filled_fee.quantize(Decimal("0.00001"))
    return PaperFill(
        avg_price=(filled_usd / filled_shares).quantize(Decimal("0.000001")),
        filled_usd=filled_usd.quantize(Decimal("0.0001")),
        size_shares=filled_shares.quantize(Decimal("0.000001")),
        fee_usd=fee_usd,
        total_cost_usd=(filled_usd + fee_usd).quantize(Decimal("0.0001")),
    )


TICK = Decimal("0.001")


def best_bid_ask(book: dict[str, Any]) -> tuple[Decimal, Decimal] | None:
    """Best bid/ask of the executed-side book. None when either side is empty
    (no maker quote can be formed against a one-sided book)."""
    best: dict[str, Decimal] = {}
    for side, pick in (("bids", max), ("asks", min)):
        raw = book.get(side)
        if not isinstance(raw, list) or not raw:
            return None
        prices = []
        for item in raw:
            if not isinstance(item, dict):
                raise PaperExecutionError(f"{side} level must be an object")
            try:
                price = Decimal(str(item["price"]))
            except (KeyError, InvalidOperation) as exc:
                raise PaperExecutionError(f"{side} level is invalid") from exc
            if not Decimal(0) < price < Decimal(1):
                raise PaperExecutionError(f"{side} level is out of range")
            prices.append(price)
        best[side] = pick(prices)
    if best["bids"] >= best["asks"]:
        raise PaperExecutionError("book is crossed")
    return best["bids"], best["asks"]


def maker_limit_price(best_bid: Decimal, best_ask: Decimal) -> Decimal | None:
    """Passive limit for the paper maker: the midpoint floored to the tick.
    Never crosses the ask; None when the book leaves no room for a bid."""
    midpoint = (best_bid + best_ask) / 2
    limit = midpoint.quantize(TICK, rounding=ROUND_FLOOR)
    limit = min(limit, best_ask)
    if limit <= 0:
        return None
    return limit


def maker_fill(
    *,
    limit_price: Decimal,
    target_usd: Decimal,
    fee_rate: Decimal = Decimal(0),
) -> PaperFill:
    """Deterministic full fill at our own limit once the market trades
    through it. Maker-side fees only (zero on taker-only fee markets)."""
    fee_per_share = fee_rate * limit_price * (Decimal(1) - limit_price)
    shares = (target_usd / (limit_price + fee_per_share)).quantize(
        Decimal("0.000001")
    )
    filled_usd = (shares * limit_price).quantize(Decimal("0.0001"))
    fee_usd = (shares * fee_per_share).quantize(Decimal("0.00001"))
    return PaperFill(
        avg_price=limit_price,
        filled_usd=filled_usd,
        size_shares=shares,
        fee_usd=fee_usd,
        total_cost_usd=(filled_usd + fee_usd).quantize(Decimal("0.0001")),
    )
