from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
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


def walk_ask_book(
    book: dict[str, Any],
    *,
    target_usd: Decimal,
    max_price: Decimal,
    fee_rate: Decimal = Decimal(0),
) -> PaperFill | None:
    if target_usd <= 0:
        raise PaperExecutionError("target_usd must be positive")
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
