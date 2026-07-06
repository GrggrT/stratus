from stratus.config import Settings
from stratus.telegram_bot import build_router


def test_telegram_router_registers_command_handlers() -> None:
    router = build_router(Settings(telegram_admin_user_id=42), object())

    assert len(router.message.handlers) == 8
