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
from stratus.db.session import create_engine, create_session_factory, verify_runtime_engine
from stratus.repositories.markets import verify_market_station
from stratus.repositories.runtime_control import (
    set_kill_switch,
    set_mode,
)
from stratus.repositories.telegram import (
    activate_notification_channel,
    claim_deliveries,
    enqueue_notification,
    enqueue_recent_notifications,
    has_delivery,
    mark_delivery_failed,
    mark_delivery_sent,
    record_notification_heartbeat,
)
from stratus.services.telegram_reports import (
    calibration_report,
    daily_report,
    live_equity_chart,
    live_report,
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
            "/live — live P&L, ROI, and CLV retrospective\n"
            "/verify <market_id> — approve parsed station\n"
            "/stop — engage kill switch\n"
            "/resume — blocked outside release canary\n"
            "/mode paper — execution mode (LIVE is disabled)"
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

    @router.message(Command("live"))
    async def live_handler(message: Message) -> None:
        if await _deny_unless_admin(message, settings):
            return
        async with session_factory() as session:  # type: ignore[operator]
            report = await live_report(session)
            chart = await live_equity_chart(session)
        if chart is None:
            await message.answer(report)
        else:
            await message.answer_photo(
                BufferedInputFile(chart, filename="live-equity.png"),
                caption=report,
            )

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
        await message.answer(
            "Resume blocked: Polymarket PAPER may resume only through an "
            "active DB-clock release-canary safety lease."
        )

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
            await message.answer("Usage: /mode paper (LIVE is disabled in this PAPER-only release)")
            return
        if parts[1] == "live":
            await message.answer("Live mode is disabled for this PAPER-only release.")
            return
        async with session_factory.begin() as session:  # type: ignore[attr-defined]
            await set_mode(session, ExecutionMode.PAPER)
        await message.answer("Mode set to paper.")

    return router


async def notification_loop(bot: Bot, settings: Settings, session_factory: object) -> None:
    destination = settings.telegram_chat_id or settings.telegram_admin_user_id
    if destination is None:
        raise RuntimeError("TELEGRAM_CHAT_ID or TELEGRAM_ADMIN_USER_ID is required")
    # Starting the explicitly configured Telegram process is the activation
    # action.  Commit its DB-clock watermark before any producer transaction so
    # every pre-existing row remains local-only, including the legacy backlog.
    async with session_factory.begin() as session:  # type: ignore[attr-defined]
        await activate_notification_channel(session)
        await record_notification_heartbeat(session)
    while True:
        try:
            async with session_factory.begin() as session:  # type: ignore[attr-defined]
                await record_notification_heartbeat(session)
                local_now = datetime.now(ZoneInfo("Europe/Berlin"))
                if local_now.hour >= 8:
                    daily_key = f"daily:{local_now.date() - timedelta(days=1)}"
                    # The report query is heavy; build it only once per day.
                    if not await has_delivery(session, daily_key):
                        report = await daily_report(session)
                        await enqueue_notification(session, daily_key, report)
                await enqueue_recent_notifications(session, datetime.now(UTC) - timedelta(hours=24))
            # The claim transaction commits the lease before network I/O.
            async with session_factory.begin() as session:  # type: ignore[attr-defined]
                deliveries = await claim_deliveries(
                    session,
                    limit=5,
                    lease_seconds=120,
                )
            for delivery in deliveries:
                try:
                    await bot.send_message(destination, delivery.payload)
                except Exception as exc:
                    async with session_factory.begin() as session:  # type: ignore[attr-defined]
                        recorded = await mark_delivery_failed(
                            session,
                            delivery.id,
                            str(exc),
                            delivery.lease_until,
                        )
                    if not recorded:
                        logger.warning(
                            "Telegram delivery %d failure arrived after its lease changed",
                            delivery.id,
                        )
                    logger.warning("Telegram delivery %d failed: %s", delivery.id, exc)
                else:
                    async with session_factory.begin() as session:  # type: ignore[attr-defined]
                        recorded = await mark_delivery_sent(
                            session,
                            delivery.id,
                            delivery.lease_until,
                        )
                    if not recorded:
                        logger.warning(
                            "Telegram delivery %d send confirmation arrived "
                            "after its lease changed",
                            delivery.id,
                        )
                # Keep burst delivery below Telegram's practical per-chat rate.
                await asyncio.sleep(0.05)
        except Exception:
            logger.exception("Telegram notification loop failed")
        await asyncio.sleep(5)


def _bot_commands() -> list[BotCommand]:
    return [
        BotCommand(command="status", description="Bankroll and exposure"),
        BotCommand(command="calib", description="Calibration and live gate"),
        BotCommand(command="daily", description="Previous-day paper results"),
        BotCommand(command="live", description="Live P&L, ROI, and CLV"),
        BotCommand(command="stop", description="Engage kill switch"),
        BotCommand(command="resume", description="Blocked outside release canary"),
        BotCommand(command="mode", description="Set PAPER mode"),
    ]


async def run_bot(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required")
    if settings.telegram_admin_user_id is None:
        raise RuntimeError("TELEGRAM_ADMIN_USER_ID is required")
    engine = create_engine(settings)
    await verify_runtime_engine(engine)
    session_factory = create_session_factory(engine)
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(settings, session_factory))
    await bot.set_my_commands(_bot_commands())
    notifier = asyncio.create_task(notification_loop(bot, settings, session_factory))
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
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
