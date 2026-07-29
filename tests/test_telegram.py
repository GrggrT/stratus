from decimal import Decimal

from stratus.config import Settings
from stratus.repositories.analytics import ActualOrderFill
from stratus.repositories.telegram import format_fill_notification_execution
from stratus.telegram_bot import build_router


def test_telegram_router_registers_command_handlers() -> None:
    router = build_router(Settings(telegram_admin_user_id=42), object())

    assert len(router.message.handlers) == 9


def test_fill_notification_distinguishes_limit_from_actual_vwap() -> None:
    line = format_fill_notification_execution(
        requested_limit=Decimal("0.700000"),
        status="partially_filled",
        fill=ActualOrderFill(
            entry_price=Decimal("0.73000000"),
            filled_notional_usd=Decimal("5.748064"),
            fee_usd=Decimal("0.01234"),
            shares=Decimal("7.874060"),
        ),
    )

    assert "fill VWAP 0.730" in line
    assert "requested limit 0.700" in line
    assert "fee $0.0123" in line


def test_fill_notification_does_not_present_unknown_fee_as_zero() -> None:
    line = format_fill_notification_execution(
        requested_limit=Decimal("0.700000"),
        status="partially_filled",
        fill=ActualOrderFill(
            entry_price=Decimal("0.73000000"),
            filled_notional_usd=Decimal("5.748064"),
            fee_usd=None,
            shares=Decimal("7.874060"),
        ),
    )

    assert "fee unknown" in line
    assert "$0.0000" not in line


def test_fill_notification_labels_proven_zero_fill_as_warning() -> None:
    line = format_fill_notification_execution(
        requested_limit=Decimal("0.700000"),
        status="unfilled_maker",
        fill=None,
        filled_notional_hint=Decimal("0"),
    )

    assert line.startswith("WARNING no_fill;")
    assert "execution_evidence_unavailable" not in line


def test_fill_notification_labels_missing_modern_fill_evidence_as_critical() -> None:
    line = format_fill_notification_execution(
        requested_limit=Decimal("0.700000"),
        status="partially_filled",
        fill=None,
        filled_notional_hint=Decimal("4.2000"),
    )

    assert line.startswith("CRITICAL execution_evidence_unavailable;")
    assert "no_fill" not in line


def test_canceled_order_with_nonzero_fill_is_not_mislabeled_no_fill() -> None:
    line = format_fill_notification_execution(
        requested_limit=Decimal("0.700000"),
        status="canceled",
        fill=None,
        filled_notional_hint=Decimal("1.0000"),
    )

    assert line.startswith("CRITICAL execution_evidence_unavailable;")
