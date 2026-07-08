from decimal import Decimal

import pytest

from stratus.collectors.clob_trader import ClobTradeError, parse_place_response
from stratus.collectors.geoblock import parse_geoblock
from stratus.services.live_execution import (
    LiveOrderRejected,
    evaluate_live_readiness,
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


def test_place_response_parse_accepts_only_explicit_success() -> None:
    placed = parse_place_response(
        {"success": True, "errorMsg": "", "orderID": "0xabc", "status": "live"}
    )
    assert (placed.order_id, placed.status) == ("0xabc", "live")

    with pytest.raises(ClobTradeError, match="rejected"):
        parse_place_response({"success": False, "errorMsg": "not enough balance"})
    with pytest.raises(ClobTradeError, match="missing orderID"):
        parse_place_response({"success": True, "errorMsg": ""})
    with pytest.raises(ClobTradeError, match="invalid order response"):
        parse_place_response("nope")
