from __future__ import annotations

from decimal import Decimal

from stratus.db.models import ClobMarketSnapshot, TradeSide

CLOB_DUAL_PROTOCOL = "clob-dual-v1"
LEGACY_MARKET_PROTOCOL = "legacy-synthetic-yes-v0"


def traded_token_id(snapshot: ClobMarketSnapshot, side: TradeSide) -> str:
    return snapshot.yes_token_id if side is TradeSide.BUY_YES else snapshot.no_token_id


def traded_midpoint(
    snapshot: ClobMarketSnapshot, side: TradeSide
) -> Decimal | None:
    return snapshot.yes_midpoint if side is TradeSide.BUY_YES else snapshot.no_midpoint


def traded_best_bid(
    snapshot: ClobMarketSnapshot, side: TradeSide
) -> Decimal | None:
    return snapshot.yes_best_bid if side is TradeSide.BUY_YES else snapshot.no_best_bid


def traded_best_ask(
    snapshot: ClobMarketSnapshot, side: TradeSide
) -> Decimal | None:
    return snapshot.yes_best_ask if side is TradeSide.BUY_YES else snapshot.no_best_ask
