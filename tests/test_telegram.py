from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

from stratus.config import Settings
from stratus.repositories.analytics import ActualOrderFill
from stratus.repositories.telegram import format_fill_notification_execution
from stratus.telegram_bot import _bot_commands, build_router


def test_telegram_router_registers_command_handlers() -> None:
    router = build_router(Settings(telegram_admin_user_id=42), object())

    assert len(router.message.handlers) == 9


async def test_telegram_help_describes_resume_as_release_canary_only() -> None:
    router = build_router(Settings(telegram_admin_user_id=42), object())
    handler = next(
        item.callback
        for item in router.message.handlers
        if item.callback.__name__ == "help_handler"
    )
    answer = AsyncMock()
    message = SimpleNamespace(
        text="/help",
        from_user=SimpleNamespace(id=42),
        answer=answer,
    )

    await handler(message)

    help_text = answer.await_args.args[0]
    assert "/stop — engage kill switch" in help_text
    assert "/resume — blocked outside release canary" in help_text
    assert "/stop /resume — kill switch" not in help_text


def test_telegram_command_menu_does_not_offer_direct_resume_or_live_mode() -> None:
    descriptions = {item.command: item.description for item in _bot_commands()}

    assert descriptions["stop"] == "Engage kill switch"
    assert descriptions["resume"] == "Blocked outside release canary"
    assert descriptions["mode"] == "Set PAPER mode"


async def test_telegram_mode_live_is_rejected_before_database_access() -> None:
    router = build_router(Settings(telegram_admin_user_id=42), object())
    handler = next(
        item.callback
        for item in router.message.handlers
        if item.callback.__name__ == "mode_handler"
    )
    answer = AsyncMock()
    message = SimpleNamespace(
        text="/mode live",
        from_user=SimpleNamespace(id=42),
        answer=answer,
    )

    await handler(message)

    answer.assert_awaited_once_with("Live mode is disabled for this PAPER-only release.")


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
