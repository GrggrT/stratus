from decimal import Decimal

import pytest

from stratus.services.execution import (
    PaperExecutionError,
    taker_fee,
    usable_ask_depth_usd,
    walk_ask_book,
)


def test_usable_ask_depth_counts_only_executable_levels() -> None:
    depth = usable_ask_depth_usd(
        {
            "asks": [
                {"price": "0.50", "size": "20"},
                {"price": "0.51", "size": "10"},
                {"price": "0.60", "size": "1000"},
            ]
        },
        max_price=Decimal("0.51"),
    )

    assert depth == Decimal("15.10")


def test_usable_ask_depth_rejects_malformed_books() -> None:
    with pytest.raises(PaperExecutionError):
        usable_ask_depth_usd({"asks": "invalid"}, max_price=Decimal("0.51"))


def test_walk_ask_book_fills_across_levels_in_price_order() -> None:
    fill = walk_ask_book(
        {
            "asks": [
                {"price": "0.52", "size": "100"},
                {"price": "0.50", "size": "20"},
                {"price": "0.51", "size": "20"},
            ]
        },
        target_usd=Decimal("15.10"),
        max_price=Decimal("0.51"),
    )

    assert fill is not None
    assert fill.filled_usd == Decimal("15.1000")
    assert fill.size_shares == Decimal("30.000000")
    assert fill.avg_price == Decimal("0.503333")


def test_walk_ask_book_returns_a_partial_fill_at_limit() -> None:
    fill = walk_ask_book(
        {
            "asks": [
                {"price": "0.50", "size": "10"},
                {"price": "0.52", "size": "100"},
            ]
        },
        target_usd=Decimal("10"),
        max_price=Decimal("0.51"),
    )

    assert fill is not None
    assert fill.filled_usd == Decimal("5.0000")
    assert fill.size_shares == Decimal("10.000000")
    assert fill.avg_price == Decimal("0.500000")


def test_walk_ask_book_returns_none_without_executable_liquidity() -> None:
    assert (
        walk_ask_book(
            {"asks": [{"price": "0.52", "size": "100"}]},
            target_usd=Decimal("10"),
            max_price=Decimal("0.51"),
        )
        is None
    )


def test_walk_ask_book_rejects_invalid_input() -> None:
    with pytest.raises(PaperExecutionError):
        walk_ask_book(
            {"asks": "invalid"},
            target_usd=Decimal("10"),
            max_price=Decimal("0.51"),
        )


def test_weather_taker_fee_reduces_notional_inside_budget() -> None:
    fill = walk_ask_book(
        {"asks": [{"price": "0.50", "size": "100"}]},
        target_usd=Decimal("50"),
        max_price=Decimal("0.51"),
        fee_rate=Decimal("0.05"),
    )

    assert fill is not None
    assert fill.total_cost_usd == Decimal("50.0000")
    assert fill.filled_usd < Decimal("50")
    assert fill.fee_usd > 0
    assert taker_fee(Decimal("100"), Decimal("0.50"), Decimal("0.05")) == Decimal(
        "1.25000"
    )


def test_best_bid_ask_and_maker_limit() -> None:
    from stratus.services.execution import best_bid_ask, maker_limit_price

    book = {
        "bids": [{"price": "0.52", "size": "100"}, {"price": "0.50", "size": "50"}],
        "asks": [{"price": "0.58", "size": "80"}, {"price": "0.60", "size": "40"}],
    }
    quote = best_bid_ask(book)
    assert quote is not None
    best_bid, best_ask = quote
    assert best_bid == Decimal("0.52")
    assert best_ask == Decimal("0.58")
    # Midpoint 0.55 floors onto the tick and never crosses the ask.
    assert maker_limit_price(best_bid, best_ask) == Decimal("0.550")
    assert maker_limit_price(Decimal("0.5555"), Decimal("0.5556")) == Decimal("0.555")

    assert best_bid_ask({"bids": [], "asks": [{"price": "0.5", "size": "1"}]}) is None
    assert best_bid_ask({"asks": [{"price": "0.5", "size": "1"}]}) is None
    with pytest.raises(PaperExecutionError):
        best_bid_ask(
            {
                "bids": [{"price": "0.6", "size": "1"}],
                "asks": [{"price": "0.5", "size": "1"}],
            }
        )


def test_maker_fill_is_full_size_at_limit_without_taker_fee() -> None:
    from stratus.services.execution import maker_fill

    fill = maker_fill(limit_price=Decimal("0.55"), target_usd=Decimal("22"))
    assert fill.avg_price == Decimal("0.55")
    assert fill.fee_usd == 0
    assert fill.size_shares == Decimal("40")
    assert fill.filled_usd == Decimal("22")
    assert fill.total_cost_usd == Decimal("22")

    # Maker fee only when the market charges makers (fee_taker_only=False).
    with_fee = maker_fill(
        limit_price=Decimal("0.5"),
        target_usd=Decimal("20.1"),
        fee_rate=Decimal("0.02"),
    )
    # fee/share = 0.02*0.5*0.5 = 0.005 -> shares = 20.1/0.505 = 39.80198...
    assert with_fee.size_shares == Decimal("39.801980")
    assert with_fee.fee_usd > 0
    assert with_fee.total_cost_usd <= Decimal("20.1")
