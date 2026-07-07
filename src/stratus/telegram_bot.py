from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, BufferedInputFile, Message

from stratus.config import Settings, get_settings
from stratus.db.models import ExecutionMode
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.markets import verify_market_station
from stratus.repositories.runtime_control import (
    get_runtime_control,
    request_live_confirmation,
    set_kill_switch,
    set_mode,
)
from stratus.repositories.telegram import (
    enqueue_notification,
    enqueue_recent_notifications,
    has_delivery,
    mark_delivery_failed,
    mark_delivery_sent,
    pending_deliveries,
)
from stratus.services.telegram_reports import (
    calculate_live_gate,
    calibration_report,
    daily_report,
    reliability_chart,
    status_report,
)

logger = logging.getLogger(__name__)


def _is_admin(message: Message, settings: Settings) -> bool:
    return (
        settings.telegram_admin_user_id is not None
        and message.from_user is not None
        and message.from_user.id == settings.telegram_admin_user_id
    )


async def _deny_unless_admin(message: Message, settings: Settings) -> bool:
    if _is_admin(message, settings):
        return False
    await message.answer("Unauthorized.")
    return True


def build_router(settings: Settings, session_factory: object) -> Router:
    router = Router()

    @router.message(Command("start", "help"))
    async def help_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        await message.answer(
            "/status — bankroll and exposure\n"
            "/calib — Brier, reliability, live gate\n"
            "/daily — previous-day P&L and CLV\n"
            "/verify <market_id> — approve parsed station\n"
            "/stop /resume — kill switch\n"
            "/mode paper|live — execution mode"
        )

    @router.message(Command("status"))
    async def status_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        async with session_factory() as session:  # type: ignore[operator]
            report = await status_report(session, settings.initial_bankroll_usd)
        await message.answer(report)

    @router.message(Command("calib"))
    async def calibration_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        async with session_factory() as session:  # type: ignore[operator]
            report = await calibration_report(session, settings.initial_bankroll_usd)
            chart = await reliability_chart(session)
        if chart is None:
            await message.answer(report)
        else:
            await message.answer_photo(
                BufferedInputFile(chart, filename="reliability-30d.png"),
                caption=report,
            )

    @router.message(Command("daily"))
    async def daily_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        async with session_factory() as session:  # type: ignore[operator]
            report = await daily_report(session)
        await message.answer(report)

    @router.message(Command("stop"))
    async def stop_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        async with session_factory.begin() as session:  # type: ignore[attr-defined]
            expired = await set_kill_switch(session, True)
        await message.answer(f"Kill switch ON. Expired pending signals: {expired}.")

    @router.message(Command("resume"))
    async def resume_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        try:
            async with session_factory.begin() as session:  # type: ignore[attr-defined]
                await set_kill_switch(session, False)
        except RuntimeError as exc:
            await message.answer(f"Resume blocked: {exc}")
        else:
            await message.answer("Kill switch off.")

    @router.message(Command("verify"))
    async def verify_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        parts = (message.text or "").split()
        if len(parts) != 2 or not parts[1].isdigit():
            await message.answer("Usage: /verify <market_id>")
            return
        market_id = int(parts[1])
        async with session_factory.begin() as session:  # type: ignore[attr-defined]
            _, response = await verify_market_station(session, market_id)
        await message.answer(response)

    @router.message(Command("mode"))
    async def mode_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        parts = (message.text or "").lower().split()
        if len(parts) != 2 or parts[1] not in {"paper", "live"}:
            await message.answer("Usage: /mode paper|live")
            return
        async with session_factory.begin() as session:  # type: ignore[attr-defined]
            if parts[1] == "paper":
                await set_mode(session, ExecutionMode.PAPER)
                response = "Mode set to paper."
            else:
                if not settings.live_legal_approved:
                    response = (
                        "Live mode blocked: Austrian legal classification is not approved."
                    )
                elif not settings.live_tax_approved:
                    response = "Live mode blocked: Austrian tax treatment is not approved."
                else:
                    gate = await calculate_live_gate(
                        session, settings.initial_bankroll_usd
                    )
                    if not gate.passed:
                        response = (
                            "Live mode blocked: validation gate has not passed. Use /calib."
                        )
                    else:
                        control = await get_runtime_control(session)
                        now = datetime.now(UTC)
                        if (
                            control.live_confirmation_until is None
                            or control.live_confirmation_until < now
                        ):
                            deadline = await request_live_confirmation(session)
                            response = (
                                "Gate passed. Repeat /mode live before "
                                f"{deadline:%H:%M:%S} UTC to confirm."
                            )
                        else:
                            response = (
                                "Live mode remains blocked: the live CLOB executor is not deployed."
                            )
        await message.answer(response)

    return router


async def notification_loop(
    bot: Bot, settings: Settings, session_factory: object
) -> None:
    destination = settings.telegram_chat_id or settings.telegram_admin_user_id
    if destination is None:
        raise RuntimeError("TELEGRAM_CHAT_ID or TELEGRAM_ADMIN_USER_ID is required")
    while True:
        try:
            async with session_factory.begin() as session:  # type: ignore[attr-defined]
                local_now = datetime.now(ZoneInfo("Europe/Berlin"))
                if local_now.hour >= 8:
                    daily_key = f"daily:{local_now.date() - timedelta(days=1)}"
                    # The report query is heavy; build it only once per day.
                    if not await has_delivery(session, daily_key):
                        report = await daily_report(session)
                        await enqueue_notification(session, daily_key, report)
                await enqueue_recent_notifications(
                    session, datetime.now(UTC) - timedelta(hours=24)
                )
            async with session_factory() as session:  # type: ignore[operator]
                deliveries = await pending_deliveries(session)
            for delivery in deliveries:
                try:
                    await bot.send_message(destination, delivery.payload)
                except Exception as exc:
                    async with session_factory.begin() as session:  # type: ignore[attr-defined]
                        await mark_delivery_failed(session, delivery.id, str(exc))
                    logger.warning("Telegram delivery %d failed: %s", delivery.id, exc)
                else:
                    async with session_factory.begin() as session:  # type: ignore[attr-defined]
                        await mark_delivery_sent(session, delivery.id, datetime.now(UTC))
        except Exception:
            logger.exception("Telegram notification loop failed")
        await asyncio.sleep(5)


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if settings.telegram_admin_user_id is None:
        raise RuntimeError("TELEGRAM_ADMIN_USER_ID is required")
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, session_factory))
    await bot.set_my_commands(
        [
            BotCommand(command="status", description="Bankroll and exposure"),
            BotCommand(command="calib", description="Calibration and live gate"),
            BotCommand(command="daily", description="Previous-day paper results"),
            BotCommand(command="stop", description="Enable kill switch"),
            BotCommand(command="resume", description="Disable kill switch"),
            BotCommand(command="mode", description="Set paper or live mode"),
        ]
    )
    notifier = asyncio.create_task(notification_loop(bot, settings, session_factory))
    try:
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        notifier.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await notifier
        await bot.session.close()
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
