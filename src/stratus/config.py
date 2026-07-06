from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_MODELS = frozenset({"ecmwf", "gfs", "icon", "gem"})
SUPPORTED_ENSEMBLE_MODELS = frozenset({"gefs", "ecmwf_ens"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://stratus:stratus@localhost:5432/stratus"
    open_meteo_base_url: str = "https://api.open-meteo.com/v1/forecast"
    open_meteo_forecast_days: int = Field(default=10, ge=1, le=16)
    open_meteo_concurrency: int = Field(default=4, ge=1, le=16)
    forecast_models: tuple[str, ...] = ("ecmwf", "gfs", "icon", "gem")
    open_meteo_ensemble_url: str = "https://ensemble-api.open-meteo.com/v1/ensemble"
    open_meteo_ensemble_forecast_days: int = Field(default=10, ge=1, le=35)
    open_meteo_ensemble_concurrency: int = Field(default=2, ge=1, le=6)
    ensemble_models: tuple[str, ...] = ("gefs", "ecmwf_ens")
    gamma_events_url: str = "https://gamma-api.polymarket.com/events"
    gamma_weather_tag_id: int = 84
    target_cities: tuple[str, ...] = ("New York City", "London", "Paris")
    aviation_weather_metar_url: str = "https://aviationweather.gov/api/data/metar"
    aviation_weather_user_agent: str = "STRATUS/0.1"
    nws_base_url: str = "https://api.weather.gov"
    nws_user_agent: str = "STRATUS/0.1 weather research"
    nws_stations: tuple[str, ...] = ("KLGA",)
    observation_lookback_hours: int = Field(default=6, ge=1, le=360)
    clob_books_url: str = "https://clob.polymarket.com/books"
    clob_batch_size: int = Field(default=50, ge=1, le=100)
    iem_asos_url: str = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    iem_bootstrap_years: int = Field(default=3, ge=1, le=10)
    iem_request_delay_seconds: float = Field(default=12.0, ge=1.0, le=60.0)
    open_meteo_previous_runs_url: str = (
        "https://previous-runs-api.open-meteo.com/v1/forecast"
    )
    previous_runs_bootstrap_years: int = Field(default=3, ge=1, le=5)
    previous_run_horizon_days: tuple[int, ...] = (1, 2, 3)
    previous_runs_concurrency: int = Field(default=2, ge=1, le=4)
    calibration_window_days: int = Field(default=180, ge=30, le=730)
    calibration_min_samples: int = Field(default=60, ge=20, le=500)
    initial_bankroll_usd: float = Field(default=1080.0, gt=0)
    # Accounting timezone for daily/weekly loss limits. UTC keeps historical
    # behaviour; America/New_York aligns the reset with the venue day.
    risk_timezone: str = "UTC"
    signal_retention_days: int = Field(default=30, ge=7, le=365)
    trading_mode: str = "paper"
    telegram_bot_token: str | None = None
    telegram_admin_user_id: int | None = None
    telegram_chat_id: int | None = None
    live_legal_approved: bool = False
    live_tax_approved: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    log_level: str = "INFO"

    @field_validator("forecast_models", mode="before")
    @classmethod
    def parse_models(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

    @field_validator("forecast_models")
    @classmethod
    def validate_models(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - SUPPORTED_MODELS
        if unknown:
            raise ValueError(f"unsupported forecast models: {', '.join(sorted(unknown))}")
        if not value:
            raise ValueError("at least one forecast model is required")
        return value

    @field_validator("ensemble_models", mode="before")
    @classmethod
    def parse_ensemble_models(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

    @field_validator("ensemble_models")
    @classmethod
    def validate_ensemble_models(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = set(value) - SUPPORTED_ENSEMBLE_MODELS
        if unknown:
            raise ValueError(f"unsupported ensemble models: {', '.join(sorted(unknown))}")
        if not value:
            raise ValueError("at least one ensemble model is required")
        return value

    @field_validator("target_cities", mode="before")
    @classmethod
    def parse_target_cities(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip() for item in value.split(",") if item.strip())
        return value

    @field_validator("nws_stations", mode="before")
    @classmethod
    def parse_nws_stations(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().upper() for item in value.split(",") if item.strip())
        return value

    @field_validator("previous_run_horizon_days", mode="before")
    @classmethod
    def parse_previous_run_horizons(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(int(item.strip()) for item in value.split(",") if item.strip())
        return value

    @field_validator("previous_run_horizon_days")
    @classmethod
    def validate_previous_run_horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if not value or any(day < 1 or day > 7 for day in value):
            raise ValueError("previous-run horizons must be between 1 and 7 days")
        if len(set(value)) != len(value):
            raise ValueError("previous-run horizons must be unique")
        return value

    @field_validator("risk_timezone")
    @classmethod
    def validate_risk_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown risk timezone: {value}") from exc
        return value

    @field_validator("trading_mode")
    @classmethod
    def validate_trading_mode(cls, value: str) -> str:
        normalized = value.lower()
        if normalized not in {"paper", "live"}:
            raise ValueError("trading_mode must be paper or live")
        return normalized

    @field_validator("telegram_admin_user_id", "telegram_chat_id", mode="before")
    @classmethod
    def parse_optional_telegram_id(cls, value: object) -> object:
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
