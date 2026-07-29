from decimal import Decimal

import pytest

from stratus.collectors.clob_trader import (
    ClobPlacementOutcomeUnknown,
    ClobVenueRejected,
    parse_place_response,
)
from stratus.collectors.geoblock import parse_geoblock
from stratus.services.live_execution import (
    LiveOrderRejected,
    aggregate_order_trades,
    evaluate_live_readiness,
    parse_order_fill,
    placed_order_status,
    plan_live_buy,
)


def _readiness(**overrides: object) -> tuple[bool, tuple[str, ...]]:
    defaults: dict[str, object] = {
        "legal_approved": True,
        "tax_approved": True,
        "private_key_present": True,
        "mode_is_live": True,
        "kill_switch": False,
        "halted_reason": None,
        "geoblock_blocked": False,
    }
    defaults.update(overrides)
    result = evaluate_live_readiness(**defaults)  # type: ignore[arg-type]
    return result.ready, result.reasons


def test_live_readiness_requires_every_gate() -> None:
    assert _readiness() == (True, ())
    assert _readiness(legal_approved=False) == (False, ("legal_not_approved",))
    assert _readiness(tax_approved=False) == (False, ("tax_not_approved",))
    assert _readiness(private_key_present=False) == (False, ("private_key_missing",))
    assert _readiness(mode_is_live=False) == (False, ("mode_not_live",))
    assert _readiness(kill_switch=True) == (False, ("kill_switch",))
    assert _readiness(halted_reason="gate failed") == (False, ("project_halted",))
    assert _readiness(geoblock_blocked=True) == (False, ("geoblock_blocked",))
    # Unknown geoblock state fails closed.
    assert _readiness(geoblock_blocked=None) == (False, ("geoblock_unknown",))


def test_plan_live_buy_rounds_down_and_respects_the_ceiling() -> None:
    plan = plan_live_buy(
        token_id="token-1",
        max_price=Decimal("0.537"),
        size_usd=Decimal("100"),
        tick_size=Decimal("0.01"),
        min_size_shares=Decimal("5"),
        max_order_usd=Decimal("25"),
    )

    # Price snaps DOWN to the tick, never up past the cap.
    assert plan.price == Decimal("0.53")
    # Notional is clamped to the physical ceiling, shares round down to 0.01.
    assert plan.clamped is True
    assert plan.size_shares == Decimal("47.16")
    assert plan.cost_usd == Decimal("24.9948")
    assert plan.cost_usd <= Decimal("25")


def test_plan_live_buy_rejects_undersized_and_invalid_orders() -> None:
    common = {
        "token_id": "token-1",
        "tick_size": Decimal("0.01"),
        "min_size_shares": Decimal("5"),
        "max_order_usd": Decimal("25"),
    }
    with pytest.raises(LiveOrderRejected, match="below the market minimum"):
        plan_live_buy(max_price=Decimal("0.53"), size_usd=Decimal("1"), **common)
    with pytest.raises(LiveOrderRejected, match="must be positive"):
        plan_live_buy(max_price=Decimal("0.53"), size_usd=Decimal("0"), **common)
    with pytest.raises(LiveOrderRejected, match="between zero and one"):
        plan_live_buy(max_price=Decimal("1.01"), size_usd=Decimal("10"), **common)
    with pytest.raises(LiveOrderRejected, match="rounds to zero"):
        plan_live_buy(
            token_id="token-1",
            max_price=Decimal("0.005"),
            size_usd=Decimal("10"),
            tick_size=Decimal("0.01"),
            min_size_shares=Decimal("5"),
            max_order_usd=Decimal("25"),
        )


def test_geoblock_parse_fails_closed() -> None:
    ok = parse_geoblock({"blocked": False, "country": "AT"})
    assert (ok.blocked, ok.country) == (False, "AT")
    assert parse_geoblock({"blocked": True, "country": "US"}).blocked is True
    # Anything malformed is treated as blocked.
    assert parse_geoblock({"country": "AT"}).blocked is True
    assert parse_geoblock({"blocked": "false"}).blocked is True
    assert parse_geoblock(None).blocked is True
    assert parse_geoblock([]).blocked is True


def test_parse_order_fill_reads_cumulative_state() -> None:
    partial = parse_order_fill(
        {"size_matched": "40", "price": "0.53", "status": "LIVE"}
    )
    assert partial is not None
    assert partial.matched_shares == Decimal("40")
    assert partial.avg_price == Decimal("0.53")
    assert partial.cost_usd == Decimal("21.2000")
    assert partial.terminal is False

    matched = parse_order_fill(
        {"size_matched": "47.16", "price": "0.53", "status": "MATCHED"}
    )
    assert matched is not None and matched.terminal is True

    canceled = parse_order_fill(
        {"size_matched": "0", "price": "0.53", "status": "CANCELED"}
    )
    assert canceled is not None and canceled.terminal is True


def test_parse_order_fill_fails_closed() -> None:
    assert parse_order_fill(None) is None
    assert parse_order_fill({"size_matched": "10"}) is None  # no price
    assert parse_order_fill({"price": "0.5"}) is None  # no matched
    assert parse_order_fill({"size_matched": "-1", "price": "0.5"}) is None
    assert parse_order_fill({"size_matched": "10", "price": "1.5"}) is None
    assert parse_order_fill({"size_matched": "x", "price": "0.5"}) is None


def test_successful_placement_stays_pollable_until_fill_evidence_is_recorded() -> None:
    assert placed_order_status("live") == "open"
    assert placed_order_status("matched") == "open"


def test_aggregate_order_trades_computes_vwap_across_taker_and_maker_legs() -> None:
    trades = [
        # We were the taker on this trade.
        {"status": "MATCHED", "taker_order_id": "0xme", "size": "30", "price": "0.50",
         "fee_rate_bps": "10"},
        # We were a maker on this one; our leg is inside maker_orders.
        {"status": "CONFIRMED", "taker_order_id": "0xother",
         "maker_orders": [
             {"order_id": "0xme", "matched_amount": "10", "price": "0.60", "fee_rate_bps": "10"},
             {"order_id": "0xsomeoneelse", "matched_amount": "5", "price": "0.61"},
         ]},
        # Failed trade is ignored.
        {"status": "FAILED", "taker_order_id": "0xme", "size": "100", "price": "0.90"},
    ]

    fill = aggregate_order_trades(trades, "0xme")

    assert fill is not None
    # 30@0.50 + 10@0.60 = 21.0 over 40 shares -> VWAP 0.525.
    assert fill.matched_shares == Decimal("40")
    assert fill.avg_price == Decimal("0.525000")
    assert fill.cost_usd == Decimal("21.0000")
    # fees: 10bps*(0.50*30) + 10bps*(0.60*10) = 0.015 + 0.006 = 0.021.
    assert fill.fee_usd == Decimal("0.02100")


def test_aggregate_order_trades_returns_none_without_matches() -> None:
    assert aggregate_order_trades([], "0xme") is None
    assert aggregate_order_trades(
        [{"status": "MATCHED", "taker_order_id": "0xother", "size": "5", "price": "0.5"}],
        "0xme",
    ) is None
    # Malformed entries are skipped, not crashed on.
    assert aggregate_order_trades(["nope", {"status": "MATCHED"}], "0xme") is None


def test_place_response_parse_accepts_only_explicit_success() -> None:
    placed = parse_place_response(
        {"success": True, "errorMsg": "", "orderID": "0xabc", "status": "live"}
    )
    assert (placed.order_id, placed.status) == ("0xabc", "live")

    with pytest.raises(ClobVenueRejected, match="rejected"):
        parse_place_response({"success": False, "errorMsg": "not enough balance"})
    with pytest.raises(ClobPlacementOutcomeUnknown, match="missing orderID"):
        parse_place_response({"success": True, "errorMsg": ""})
    with pytest.raises(ClobPlacementOutcomeUnknown, match="invalid order response"):
        parse_place_response("nope")
    with pytest.raises(ClobPlacementOutcomeUnknown, match="unambiguous placement result"):
        parse_place_response({"orderID": "0xmaybe"})
    with pytest.raises(ClobPlacementOutcomeUnknown, match="unambiguous placement result"):
        parse_place_response(
            {"success": True, "errorMsg": "contradictory", "orderID": "0xmaybe"}
        )
    with pytest.raises(ClobPlacementOutcomeUnknown, match="unambiguous placement result"):
        parse_place_response(
            {"success": False, "errorMsg": "rejected", "orderID": "0xmaybe"}
        )
