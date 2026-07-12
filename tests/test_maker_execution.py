"""Maker-simulation decision logic for one pending paper signal."""

from datetime import UTC, datetime
from decimal import Decimal

from stratus.db.models import Market, Signal, TradeSide, WeatherKind
from stratus.jobs.execute_signals import _process_maker_signal
from stratus.repositories.execution import MakerFillValue, RestingPaperOrder

NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def _signal(**overrides: object) -> Signal:
    values: dict[str, object] = {
        "id": 7,
        "market_id": 3,
        "ts": NOW,
        "side": TradeSide.BUY_YES,
        "model_prob": Decimal("0.65"),
        "market_price": Decimal("0.58"),
        "edge": Decimal("0.07"),
        "kelly_frac": Decimal("0.02"),
        "size_usd": Decimal("22"),
    }
    values.update(overrides)
    return Signal(**values)  # type: ignore[arg-type]


def _market(**overrides: object) -> Market:
    values: dict[str, object] = {
        "id": 3,
        "polymarket_id": "pm-1",
        "event_id": "ev-1",
        "market_date": NOW.date(),
        "kind": WeatherKind.HIGH,
        "rules_raw": "rules",
        "resolves_at": NOW,
        "fees_enabled": False,
        "fee_rate": Decimal("0"),
        "fee_exponent": 1,
        "fee_taker_only": True,
    }
    values.update(overrides)
    return Market(**values)  # type: ignore[arg-type]


BOOK = {
    "bids": [{"price": "0.52", "size": "200"}],
    "asks": [{"price": "0.58", "size": "200"}],
}


def test_posts_resting_limit_at_floored_midpoint() -> None:
    outcome = _process_maker_signal(
        _signal(),
        _market(),
        BOOK,
        resting_price=None,
        resting_order_id=None,
        now=NOW,
    )
    assert isinstance(outcome, RestingPaperOrder)
    assert outcome.limit_price == Decimal("0.550")
    assert outcome.size_usd == Decimal("22")


def test_resting_order_waits_until_market_trades_through() -> None:
    waiting = _process_maker_signal(
        _signal(),
        _market(),
        BOOK,  # ask 0.58 stays above our 0.55 bid
        resting_price=Decimal("0.55"),
        resting_order_id=11,
        now=NOW,
    )
    assert waiting is None

    crossed_book = {
        "bids": [{"price": "0.50", "size": "200"}],
        "asks": [{"price": "0.54", "size": "200"}],
    }
    fill = _process_maker_signal(
        _signal(),
        _market(),
        crossed_book,
        resting_price=Decimal("0.55"),
        resting_order_id=11,
        now=NOW,
    )
    assert isinstance(fill, MakerFillValue)
    assert fill.limit_price == Decimal("0.55")
    assert fill.size_shares == Decimal("40")
    assert fill.fee_usd == 0
    assert fill.total_cost_usd == Decimal("22")


def test_thin_traded_depth_does_not_count_as_a_fill() -> None:
    thin_book = {
        "bids": [{"price": "0.50", "size": "200"}],
        # Only $10.80 offered at/below our limit; our order is $22.
        "asks": [{"price": "0.54", "size": "20"}],
    }
    outcome = _process_maker_signal(
        _signal(),
        _market(),
        thin_book,
        resting_price=Decimal("0.55"),
        resting_order_id=11,
        now=NOW,
    )
    assert outcome is None


def test_one_sided_book_neither_posts_nor_fills() -> None:
    one_sided = {"bids": [], "asks": [{"price": "0.58", "size": "200"}]}
    assert (
        _process_maker_signal(
            _signal(),
            _market(),
            one_sided,
            resting_price=None,
            resting_order_id=None,
            now=NOW,
        )
        is None
    )
    assert (
        _process_maker_signal(
            _signal(),
            _market(),
            one_sided,
            resting_price=Decimal("0.55"),
            resting_order_id=11,
            now=NOW,
        )
        is None
    )
