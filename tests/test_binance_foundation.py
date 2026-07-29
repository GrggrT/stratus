from __future__ import annotations

import pytest
from pydantic import ValidationError

from stratus.config import Settings
from stratus.db.models import TradingVenue, VenueMode
from stratus.repositories.venue_runtime_control import venue_mode_allowed


def test_binance_defaults_are_disabled_and_conservative() -> None:
    settings = Settings(_env_file=None)

    assert settings.binance_enabled is False
    assert settings.binance_symbols == ("BTCUSDT", "ETHUSDT")
    assert settings.binance_api_key is None
    assert settings.binance_archive_insert_batch_size == 2000
    assert settings.binance_max_order_usdt == 25.0
    assert settings.binance_max_symbol_exposure < settings.binance_max_total_exposure
    assert (
        settings.binance_daily_loss_limit
        <= settings.binance_weekly_loss_limit
        <= settings.binance_max_drawdown
    )
    assert settings.binance_live_legal_approved is False
    assert settings.binance_live_tax_approved is False
    assert settings.binance_live_security_approved is False


def test_binance_symbol_configuration_is_normalized() -> None:
    settings = Settings(
        _env_file=None,
        binance_symbols="btcusdt, ethusdt",
        binance_quote_asset="usdt",
        binance_api_key="",
        binance_private_key_file="",
    )

    assert settings.binance_symbols == ("BTCUSDT", "ETHUSDT")
    assert settings.binance_quote_asset == "USDT"
    assert settings.binance_api_key is None
    assert settings.binance_private_key_file is None


def test_binance_symbol_environment_uses_csv_not_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BINANCE_SYMBOLS", "btcusdt, ethusdt")

    settings = Settings(_env_file=None)

    assert settings.binance_symbols == ("BTCUSDT", "ETHUSDT")


@pytest.mark.parametrize(
    "values",
    [
        {"binance_symbols": "BTCUSDT,BTCUSDT"},
        {"binance_symbols": "BTC/USDT"},
        {
            "binance_max_symbol_exposure": 0.2,
            "binance_max_total_exposure": 0.1,
        },
        {
            "binance_daily_loss_limit": 0.05,
            "binance_weekly_loss_limit": 0.03,
        },
        {"binance_archive_insert_batch_size": 2501},
    ],
)
def test_binance_invalid_configuration_fails_closed(values: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **values)


def test_venue_modes_are_isolated_by_product() -> None:
    assert venue_mode_allowed(TradingVenue.POLYMARKET, VenueMode.PAPER)
    assert venue_mode_allowed(TradingVenue.POLYMARKET, VenueMode.LIVE)
    assert not venue_mode_allowed(TradingVenue.POLYMARKET, VenueMode.TESTNET)

    assert venue_mode_allowed(TradingVenue.BINANCE_SPOT, VenueMode.DISABLED)
    assert venue_mode_allowed(TradingVenue.BINANCE_SPOT, VenueMode.SHADOW)
    assert venue_mode_allowed(TradingVenue.BINANCE_SPOT, VenueMode.TESTNET)
    assert venue_mode_allowed(TradingVenue.BINANCE_SPOT, VenueMode.LIVE)
    assert not venue_mode_allowed(TradingVenue.BINANCE_SPOT, VenueMode.PAPER)
