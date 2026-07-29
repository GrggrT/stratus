from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Self

from pydantic import Field, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

SUPPORTED_MODELS = frozenset({"ecmwf", "gfs", "icon", "gem"})
SUPPORTED_ENSEMBLE_MODELS = frozenset({"gefs", "ecmwf_ens"})
_RUNTIME_PASSWORD = re.compile(r"[A-Za-z0-9._~-]{24,}")
_DATABASE_HOST = re.compile(r"[A-Za-z0-9.-]+")
_DATABASE_NAME = re.compile(r"[A-Za-z0-9_-]+")
_DATABASE_APPLICATION_NAME = re.compile(r"[A-Za-z0-9._-]{1,63}")


class _WithoutLegacyDatabaseUrl(PydanticBaseSettingsSource):
    """Drop generic DATABASE_URL from non-init settings sources."""

    def __init__(self, wrapped: PydanticBaseSettingsSource) -> None:
        super().__init__(wrapped.settings_cls)
        self._wrapped = wrapped

    def get_field_value(
        self,
        field: FieldInfo,
        field_name: str,
    ) -> tuple[Any, str, bool]:
        return self._wrapped.get_field_value(field, field_name)

    def __call__(self) -> dict[str, Any]:
        values = self._wrapped()
        values.pop("database_url", None)
        return values


def _runtime_database_role_suffix(database_name: str) -> str:
    return hashlib.md5(  # noqa: S324 - deterministic identifier, not security
        database_name.encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()


def runtime_database_group_role_name(database_name: str) -> str:
    return f"stratus_runtime_{_runtime_database_role_suffix(database_name)}"


def runtime_database_role_name(database_name: str) -> str:
    suffix = _runtime_database_role_suffix(database_name)
    return f"stratus_app_{suffix}"


class MigrationSettings(BaseSettings):
    """Owner credential surface imported only by Alembic."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    migration_database_url: str


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    # Generic DATABASE_URL is deliberately ignored: old host environments
    # commonly contain the owner DSN. Runtime may accept only this explicit,
    # narrowly named override (tests can still pass database_url directly).
    database_url: str = Field(
        default="",
        validation_alias="STRATUS_RUNTIME_DATABASE_URL",
    )
    stratus_runtime_password_file: Path = Path(
        ".secrets/stratus_runtime_password"
    )
    stratus_database_host: str = "127.0.0.1"
    stratus_database_port: int = Field(default=5432, ge=1, le=65535)
    stratus_database_name: str = "stratus"
    # Per-connection runtime PostgreSQL controls. Zero preserves PostgreSQL's
    # unlimited timeout for legitimate long-running worker analytics; the API
    # service supplies a strict profile beneath the Cockpit's three-second
    # HTTP budget.
    stratus_database_application_name: str = "stratus-runtime"
    stratus_database_statement_timeout_ms: int = Field(
        default=0,
        ge=0,
        le=300_000,
    )
    stratus_database_lock_timeout_ms: int = Field(
        default=0,
        ge=0,
        le=300_000,
    )
    stratus_database_idle_transaction_timeout_ms: int = Field(
        default=0,
        ge=0,
        le=3_600_000,
    )
    stratus_database_pool_size: int = Field(default=5, ge=1, le=32)
    stratus_database_max_overflow: int = Field(default=10, ge=0, le=64)
    stratus_database_pool_timeout_seconds: float = Field(
        default=30.0,
        ge=0.05,
        le=300.0,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del settings_cls
        return (
            init_settings,
            _WithoutLegacyDatabaseUrl(env_settings),
            _WithoutLegacyDatabaseUrl(dotenv_settings),
            _WithoutLegacyDatabaseUrl(file_secret_settings),
        )
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
    target_cities: tuple[str, ...] = (
        "New York City",
        "London",
        "Paris",
        "Tokyo",
        "Seoul",
        "Shanghai",
        "Miami",
        "Amsterdam",
        "Busan",
        "Munich",
        "Madrid",
        "Helsinki",
        "Warsaw",
        "Singapore",
        "Taipei",
        "Kuala Lumpur",
        "Manila",
        "Wellington",
        "Chicago",
        "Los Angeles",
    )
    aviation_weather_metar_url: str = "https://aviationweather.gov/api/data/metar"
    aviation_weather_user_agent: str = "STRATUS/0.1"
    nws_base_url: str = "https://api.weather.gov"
    nws_user_agent: str = "STRATUS/0.1 weather research"
    nws_stations: tuple[str, ...] = ("KLGA",)
    observation_lookback_hours: int = Field(default=6, ge=1, le=360)
    clob_books_url: str = "https://clob.polymarket.com/books"
    # Strict price evidence is collected as inseparable YES/NO pairs.
    clob_batch_size: int = Field(default=50, ge=2, le=100)
    # --- Live executor (Polymarket CLOB) ---
    clob_api_url: str = "https://clob.polymarket.com"
    geoblock_url: str = "https://polymarket.com/api/geoblock"
    polygon_chain_id: int = 137
    polygon_private_key: str | None = None
    # Funder address and signature type for Polymarket proxy wallets;
    # leave unset when the key is a plain EOA trading for itself.
    polymarket_funder: str | None = None
    polymarket_signature_type: int | None = Field(default=None, ge=0, le=2)
    # Physical per-order ceiling for live orders, independent of Kelly and
    # exposure limits. Orders are clamped down to this, never up.
    live_max_order_usd: float = Field(default=25.0, gt=0)
    # --- Binance Spot foundation. Runtime mode remains persisted in Postgres. ---
    binance_enabled: bool = False
    binance_rest_url: str = "https://api.binance.com"
    binance_market_data_rest_url: str = "https://data-api.binance.vision"
    binance_ws_url: str = "wss://stream.binance.com:9443"
    binance_ws_api_url: str = "wss://ws-api.binance.com:443/ws-api/v3"
    binance_testnet_rest_url: str = "https://testnet.binance.vision"
    binance_testnet_ws_url: str = "wss://stream.testnet.binance.vision/ws"
    binance_symbols: Annotated[tuple[str, ...], NoDecode] = ("BTCUSDT", "ETHUSDT")
    binance_quote_asset: str = "USDT"
    binance_api_key: str | None = None
    binance_private_key_file: str | None = None
    binance_market_data_timeout_ms: int = Field(default=5000, ge=500, le=30_000)
    binance_market_data_retries: int = Field(default=2, ge=0, le=5)
    binance_exchange_info_ttl_minutes: int = Field(default=360, ge=5, le=1440)
    binance_kline_interval: str = "1m"
    binance_kline_limit: int = Field(default=3, ge=2, le=1000)
    binance_depth_limit: int = 20
    binance_archive_base_url: str = "https://data.binance.vision/data/spot"
    binance_archive_timeout_seconds: int = Field(default=60, ge=5, le=300)
    binance_archive_max_bytes: int = Field(default=50_000_000, ge=1_000_000)
    binance_archive_max_uncompressed_bytes: int = Field(default=200_000_000, ge=1_000_000)
    # Aggregate-trade rows bind 11 values each. Keep well below asyncpg's
    # 32,767-query-argument ceiling even when SQLAlchemy emits one INSERT.
    binance_archive_insert_batch_size: int = Field(default=2000, ge=100, le=2500)
    binance_archive_lookback_days: int = Field(default=30, ge=1, le=3650)
    binance_research_cache_dir: Path = Path(".stratus-cache/binance-research")
    binance_research_cache_max_bytes: int = Field(
        default=10_000_000_000,
        ge=1_000_000_000,
        le=50_000_000_000,
    )
    binance_research_min_free_bytes: int = Field(
        default=70_000_000_000,
        ge=10_000_000_000,
    )
    binance_research_max_archive_bytes: int = Field(
        default=500_000_000,
        ge=10_000_000,
        le=2_000_000_000,
    )
    binance_research_max_uncompressed_bytes: int = Field(
        default=5_000_000_000,
        ge=100_000_000,
        le=20_000_000_000,
    )
    binance_research_min_available_memory_bytes: int = Field(
        default=1_000_000_000,
        ge=250_000_000,
    )
    binance_research_min_available_commit_bytes: int = Field(
        default=1_000_000_000,
        ge=250_000_000,
    )
    binance_research_resource_check_events: int = Field(
        default=10_000,
        ge=1_000,
        le=1_000_000,
    )
    binance_research_resource_wait_seconds: int = Field(
        default=300,
        ge=10,
        le=3600,
    )
    binance_research_resource_poll_seconds: int = Field(
        default=5,
        ge=1,
        le=60,
    )
    binance_recv_window_ms: int = Field(default=5000, ge=100, le=60_000)
    binance_max_clock_drift_ms: int = Field(default=500, ge=10, le=10_000)
    binance_reconcile_seconds: int = Field(default=30, ge=5, le=3600)
    binance_market_data_stale_ms: int = Field(default=2000, ge=100, le=60_000)
    binance_entry_ttl_seconds: int = Field(default=20, ge=1, le=300)
    binance_min_net_edge_bps: float | None = Field(default=None, gt=0)
    binance_min_absolute_edge_usdt: float = Field(default=0.01, gt=0)
    binance_max_order_usdt: float = Field(default=25.0, gt=0)
    binance_max_risk_fraction: float = Field(default=0.001, gt=0, le=0.1)
    binance_max_symbol_exposure: float = Field(default=0.03, gt=0, le=1.0)
    binance_max_total_exposure: float = Field(default=0.05, gt=0, le=1.0)
    binance_daily_loss_limit: float = Field(default=0.01, gt=0, le=1.0)
    binance_weekly_loss_limit: float = Field(default=0.03, gt=0, le=1.0)
    binance_max_drawdown: float = Field(default=0.06, gt=0, le=1.0)
    binance_live_legal_approved: bool = False
    binance_live_tax_approved: bool = False
    binance_live_security_approved: bool = False
    iem_asos_url: str = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
    iem_bootstrap_years: int = Field(default=3, ge=1, le=10)
    iem_request_delay_seconds: float = Field(default=12.0, ge=1.0, le=60.0)
    # Bounded, evidence-preserving recovery of already-closed local days.
    # Recovered points never enter the live observation table.
    daily_actual_recovery_enabled: bool = True
    daily_actual_recovery_lookback_days: int = Field(default=3, ge=1, le=14)
    daily_actual_recovery_max_requests_per_run: int = Field(default=20, ge=1, le=100)
    daily_actual_recovery_retry_hours: int = Field(default=20, ge=12, le=72)
    daily_actual_recovery_local_hour: int = Field(default=4, ge=0, le=23)
    daily_actual_recovery_max_response_bytes: int = Field(
        default=5_000_000, ge=10_000, le=50_000_000
    )
    daily_actual_min_samples: int = Field(default=20, ge=4, le=288)
    daily_actual_boundary_tolerance_minutes: int = Field(default=90, ge=15, le=360)
    daily_actual_max_gap_minutes: int = Field(default=120, ge=15, le=360)
    # Hash of the reviewed IEM retention policy statement documented in
    # OBSERVATION_RECOVERY_PROTOCOL.md. The remote URL itself is deliberately
    # excluded from release-manifest output.
    iem_recovery_terms_policy_hash: str = Field(
        default="bdd514125aeb91bbde8a4728063b8862010c821d05427572954b239085cc7535",
        pattern=r"^[0-9a-f]{64}$",
    )
    open_meteo_previous_runs_url: str = "https://previous-runs-api.open-meteo.com/v1/forecast"
    previous_runs_bootstrap_years: int = Field(default=3, ge=1, le=5)
    previous_run_horizon_days: tuple[int, ...] = (1, 2, 3)
    previous_runs_concurrency: int = Field(default=2, ge=1, le=4)
    calibration_window_days: int = Field(default=180, ge=30, le=730)
    calibration_min_samples: int = Field(default=60, ge=20, le=500)
    initial_bankroll_usd: float = Field(default=1080.0, gt=0)
    market_exposure_fraction: float = Field(default=0.05, gt=0, le=0.5)
    station_day_exposure_fraction: float = Field(default=0.12, gt=0, le=0.75)
    exposure_hard_stop_multiplier: float = Field(default=1.25, gt=1, le=5)
    # --- Anti-luck trading policy (docs/EDGE_VALIDATION_PLAN.md) ---
    # Edge must clear execution costs: half-spread plus this buffer (fees are
    # already priced into the effective-price edge).
    edge_cost_buffer: float = Field(default=0.02, ge=0, le=0.2)
    signal_min_edge: float = Field(default=0.05, ge=0, le=0.5)
    # Adverse-selection ceiling (docs/FORECAST_REVIEW_2026-07-21.md, E3): the
    # largest model-vs-market disagreements lost money in paper, so reject a
    # signal whose edge exceeds this. Active counterpart of the shadow
    # edge-cap-Xc policies; tighten toward 0.15/0.10 once a fresh cohort
    # corroborates. 1.0 disables the ceiling.
    signal_max_edge: float = Field(default=0.20, gt=0, le=1.0)
    max_spread: float = Field(default=0.04, gt=0, le=0.5)
    kelly_multiplier: float = Field(default=0.25, gt=0, le=1.0)
    # Cap on potential payout per position as a bankroll fraction.
    max_win_fraction: float = Field(default=0.02, gt=0, le=0.5)
    tail_price_low: float = Field(default=0.10, ge=0, le=0.5)
    tail_price_high: float = Field(default=0.90, ge=0.5, le=1.0)
    # Trading gate: calibration rows need at least this many samples before
    # signals on that station/kind/horizon execute. 0 disables the gate.
    min_traded_calibration_samples: int = Field(default=30, ge=0, le=1000)
    # Station reliability is evaluated on independent station/local-day/kind
    # units. Missing licensed exact truth is deliberately excluded: only a
    # comparable source conflict can lower the score or start a cooldown.
    station_reliability_window_days: int = Field(default=180, ge=30, le=730)
    station_reliability_min_samples: int = Field(default=20, ge=1, le=1000)
    station_reliability_min_lower_bound: float = Field(
        default=0.80,
        ge=0.50,
        le=1.0,
    )
    station_resolution_cooldown_hours: int = Field(default=48, ge=1, le=720)
    station_observation_stale_minutes: int = Field(default=90, ge=15, le=1440)
    station_reliability_snapshot_max_age_minutes: int = Field(
        default=45,
        ge=10,
        le=240,
    )
    # Shrink model probability toward the market: w = n / (n + prior). The
    # model earns independence with calibration sample size. 0 disables.
    shrinkage_prior_samples: int = Field(default=30, ge=0, le=1000)
    # Explicit cohort boundary for probability-quality validation. Change this
    # whenever a material probability architecture or calibration policy is
    # deployed; old trades remain reportable but cannot validate the new model.
    probability_pipeline_version: str = "stratus-accuracy-v4-20260713"
    # Immutable image witness recorded when a probability release epoch is
    # explicitly activated. Local development may use the all-zero sentinel;
    # production deployment must inject the candidate image id without the
    # ``sha256:`` prefix.
    release_image_digest: str = "0" * 64
    accuracy_stack_min_samples: int = Field(default=30, ge=20, le=1000)
    # A fitted stack remains a shadow forecast until it proves incremental
    # Brier skill on future independent station/day/kind/horizon blocks.
    accuracy_stack_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_stack_l2: float = Field(default=0.05, ge=0, le=10)
    accuracy_stack_learning_rate: float = Field(default=0.2, gt=0, le=1)
    accuracy_challenger_min_samples: int = Field(default=60, ge=30, le=1000)
    accuracy_qrf_trees: int = Field(default=32, ge=8, le=256)
    accuracy_nonlinear_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_trajectory_samples: int = Field(default=512, ge=100, le=5000)
    accuracy_trajectory_rho: float = Field(default=0.85, ge=0, lt=1)
    accuracy_trajectory_min_models: int = Field(default=2, ge=2, le=10)
    accuracy_trajectory_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_nowcast_min_observed_hours: int = Field(default=3, ge=2, le=12)
    accuracy_nowcast_lookback_hours: int = Field(default=6, ge=3, le=24)
    accuracy_nowcast_bias_half_life_hours: float = Field(default=6.0, gt=0, le=24)
    accuracy_nowcast_max_bias_c: float = Field(default=4.0, gt=0, le=10)
    accuracy_nowcast_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_conformal_coverage: float = Field(default=0.90, gt=0.5, lt=1)
    accuracy_conformal_min_samples: int = Field(default=120, ge=60, le=5000)
    accuracy_conformal_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_conformal_max_scale: float = Field(default=2.5, ge=1, le=10)
    accuracy_hierarchical_prior_samples: int = Field(default=60, ge=0, le=1000)
    accuracy_hierarchical_min_peer_stations: int = Field(default=3, ge=2, le=50)
    accuracy_hierarchical_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_regime_recent_days: int = Field(default=30, ge=14, le=90)
    accuracy_regime_baseline_days: int = Field(default=90, ge=30, le=365)
    accuracy_regime_min_recent: int = Field(default=20, ge=10, le=180)
    accuracy_regime_min_baseline: int = Field(default=40, ge=20, le=500)
    accuracy_regime_min_shift_c: float = Field(default=0.25, ge=0.05, le=5)
    accuracy_regime_max_adjustment_c: float = Field(default=2.0, gt=0, le=5)
    accuracy_regime_bootstrap_samples: int = Field(default=512, ge=100, le=5000)
    accuracy_regime_min_blocks: int = Field(default=60, ge=30, le=1000)
    accuracy_regional_shadow_enabled: bool = True
    open_meteo_single_runs_base_url: str = "https://single-runs-api.open-meteo.com/v1/forecast"
    accuracy_single_runs_backfill_days: int = Field(default=7, ge=1, le=365)
    accuracy_single_runs_max_requests: int = Field(default=2000, ge=1, le=10000)
    settlement_calibrator_min_blocks: int = Field(default=60, ge=30, le=1000)
    settlement_calibrator_l2: float = Field(default=0.05, ge=0, le=10)
    settlement_calibrator_ece_tolerance: float = Field(default=0.01, ge=0, le=0.2)
    # Paper execution: maker simulation posts a limit at the midpoint and
    # only fills when later snapshots trade through it, instead of paying
    # the taker ask. TTL bounds how long a resting paper order may wait.
    paper_maker_execution: bool = True
    paper_order_ttl_minutes: int = Field(default=30, ge=1, le=240)
    # Accounting timezone for daily/weekly loss limits. America/New_York
    # aligns the reset with the venue day (operator decision, 2026-07-06).
    risk_timezone: str = "America/New_York"
    signal_retention_days: int = Field(default=30, ge=7, le=365)
    skipped_signal_heartbeat_minutes: int = Field(default=60, ge=5, le=1440)
    # Immutable dual-token evidence is never deleted by the application.
    # These limits therefore gate capacity/expansion and provide enough lead
    # time to add an attested archive before the local evidence budget fills.
    clob_ledger_capacity_budget_bytes: int = Field(
        default=20_000_000_000,
        ge=1_000_000_000,
    )
    clob_ledger_capacity_warning_fraction: float = Field(
        default=0.75,
        gt=0,
        lt=1,
    )
    clob_ledger_capacity_projection_days: int = Field(default=30, ge=7, le=365)
    clob_ledger_capacity_min_window_hours: int = Field(default=20, ge=12, le=168)
    # Price collection may only use a recent, complete capacity measurement.
    # The hourly audit leaves a 30-minute scheduler/maintenance grace by default.
    clob_ledger_capacity_sample_ttl_minutes: int = Field(default=90, ge=15, le=360)
    # Preserve enough actual filesystem headroom for PostgreSQL checkpoints,
    # temporary files, migrations, and an operator-controlled archive/export.
    clob_ledger_capacity_min_free_bytes: int = Field(
        default=5_000_000_000,
        ge=500_000_000,
    )
    # One accepted HTTP response is capped before full buffering. The larger
    # write reserve covers the raw body, semantic rows, indexes, WAL and a
    # concurrent PostgreSQL checkpoint between capacity samples.
    clob_response_max_bytes: int = Field(default=25_000_000, ge=1_000_000)
    clob_ledger_capacity_write_reserve_bytes: int = Field(
        default=250_000_000,
        ge=10_000_000,
    )
    # Optional path in the worker's filesystem that refers to the PostgreSQL
    # data volume. When absent, PostgreSQL's data_directory is used. Docker
    # Compose mounts the named volume read-only and sets this explicitly.
    clob_ledger_capacity_path: str | None = None
    # Optional, explicit and bounded operational exclusion for collector SLO.
    # All three values must be configured before the maintenance begins.
    health_maintenance_starts_at: datetime | None = None
    health_maintenance_ends_at: datetime | None = None
    health_maintenance_reason: str | None = None
    observation_batch_size: int = Field(default=10, ge=1, le=50)
    live_gate_min_stations: int = Field(default=3, ge=1, le=20)
    live_gate_station_samples: int = Field(default=100, ge=20, le=1000)
    live_gate_station_brier: float = Field(default=0.18, gt=0, le=1)
    live_gate_independent_samples: int = Field(default=100, ge=30, le=1000)
    trading_mode: str = "paper"
    telegram_bot_token: str | None = None
    telegram_admin_user_id: int | None = None
    telegram_chat_id: int | None = None
    live_legal_approved: bool = False
    live_tax_approved: bool = False
    api_host: str = "0.0.0.0"
    api_port: int = Field(default=8000, ge=1, le=65535)
    # When set, every /api/v1 monitoring endpoint requires this value in the
    # X-API-Key header. Mandatory before the API is ever exposed beyond
    # loopback; optional while it stays on 127.0.0.1.
    operator_api_key: str | None = None
    # Offline official-resolution imports are fail-closed. The API accepts
    # only a hash-addressed relative reference beneath this root and reads the
    # bytes itself; a missing root keeps the mutation endpoint disabled.
    official_resolution_artifact_root: Path | None = None
    official_resolution_artifact_max_bytes: int = Field(
        default=1_000_000,
        ge=1_024,
        le=10_000_000,
    )
    log_level: str = "INFO"

    @field_validator("stratus_database_host")
    @classmethod
    def validate_database_host(cls, value: str) -> str:
        if not _DATABASE_HOST.fullmatch(value):
            raise ValueError("stratus_database_host must be a hostname or IPv4 address")
        return value

    @field_validator("stratus_database_name")
    @classmethod
    def validate_database_name(cls, value: str) -> str:
        if not _DATABASE_NAME.fullmatch(value):
            raise ValueError("stratus_database_name contains unsafe URL characters")
        return value

    @field_validator("stratus_database_application_name")
    @classmethod
    def validate_database_application_name(cls, value: str) -> str:
        if not _DATABASE_APPLICATION_NAME.fullmatch(value):
            raise ValueError(
                "stratus_database_application_name must contain 1-63 safe characters"
            )
        return value

    def resolved_database_url(self) -> str:
        """Resolve the runtime-only DSN without retaining the secret in settings."""
        if self.database_url:
            return self.database_url
        try:
            raw_password = self.stratus_runtime_password_file.read_text(
                encoding="utf-8"
            )
        except OSError as exc:
            raise RuntimeError(
                "STRATUS runtime database password file is missing or unreadable"
            ) from exc
        password = raw_password.rstrip("\r\n")
        if not _RUNTIME_PASSWORD.fullmatch(password):
            raise RuntimeError(
                "STRATUS runtime database password must contain at least 24 "
                "URL-safe unreserved characters"
            )
        role = runtime_database_role_name(self.stratus_database_name)
        return (
            f"postgresql+asyncpg://{role}:{password}@"
            f"{self.stratus_database_host}:{self.stratus_database_port}/"
            f"{self.stratus_database_name}"
        )

    @field_validator("official_resolution_artifact_root", mode="before")
    @classmethod
    def parse_optional_artifact_root(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("release_image_digest", mode="before")
    @classmethod
    def normalize_release_image_digest(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized.startswith("sha256:"):
                normalized = normalized.removeprefix("sha256:")
            return normalized
        return value

    @field_validator("release_image_digest")
    @classmethod
    def validate_release_image_digest(cls, value: str) -> str:
        if len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value
        ):
            raise ValueError("release image digest must be 64 lowercase hexadecimal characters")
        return value

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

    @field_validator("binance_symbols", mode="before")
    @classmethod
    def parse_binance_symbols(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(item.strip().upper() for item in value.split(",") if item.strip())
        return value

    @field_validator("binance_symbols")
    @classmethod
    def validate_binance_symbols(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("at least one Binance symbol is required")
        if len(set(value)) != len(value):
            raise ValueError("Binance symbols must be unique")
        invalid = [
            symbol
            for symbol in value
            if symbol != symbol.upper()
            or not symbol.isascii()
            or not symbol.isalnum()
            or not 5 <= len(symbol) <= 32
        ]
        if invalid:
            raise ValueError(f"invalid Binance symbols: {', '.join(invalid)}")
        return value

    @field_validator("binance_quote_asset", mode="before")
    @classmethod
    def normalize_binance_quote_asset(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("binance_quote_asset")
    @classmethod
    def validate_binance_quote_asset(cls, value: str) -> str:
        if not value.isascii() or not value.isalnum() or not 2 <= len(value) <= 16:
            raise ValueError("binance_quote_asset must be an ASCII asset code")
        return value

    @field_validator("binance_kline_interval")
    @classmethod
    def validate_binance_kline_interval(cls, value: str) -> str:
        allowed = {
            "1s",
            "1m",
            "3m",
            "5m",
            "15m",
            "30m",
            "1h",
            "2h",
            "4h",
            "6h",
            "8h",
            "12h",
            "1d",
            "3d",
            "1w",
            "1M",
        }
        if value not in allowed:
            raise ValueError("unsupported Binance kline interval")
        return value

    @field_validator("binance_depth_limit")
    @classmethod
    def validate_binance_depth_limit(cls, value: int) -> int:
        if value not in {5, 10, 20, 50, 100, 500, 1000, 5000}:
            raise ValueError("unsupported Binance depth limit")
        return value

    @model_validator(mode="after")
    def validate_binance_risk_ladder(self) -> Self:
        if self.binance_research_cache_max_bytes >= self.binance_research_min_free_bytes:
            raise ValueError("Binance research cache must remain below the free-disk reserve")
        if self.binance_max_symbol_exposure > self.binance_max_total_exposure:
            raise ValueError("binance_max_symbol_exposure cannot exceed total exposure")
        if not (
            self.binance_daily_loss_limit
            <= self.binance_weekly_loss_limit
            <= self.binance_max_drawdown
        ):
            raise ValueError("Binance loss limits must satisfy daily <= weekly <= drawdown")
        return self

    @model_validator(mode="after")
    def validate_health_maintenance_window(self) -> Self:
        values = (
            self.health_maintenance_starts_at,
            self.health_maintenance_ends_at,
            self.health_maintenance_reason,
        )
        if all(value is None for value in values):
            return self
        if any(value is None for value in values):
            raise ValueError("maintenance start, end, and reason must be configured together")
        starts_at = self.health_maintenance_starts_at
        ends_at = self.health_maintenance_ends_at
        reason = self.health_maintenance_reason
        assert starts_at is not None and ends_at is not None and reason is not None
        if (
            starts_at.tzinfo is None
            or starts_at.utcoffset() is None
            or ends_at.tzinfo is None
            or ends_at.utcoffset() is None
        ):
            raise ValueError("maintenance timestamps must be timezone-aware")
        if ends_at <= starts_at:
            raise ValueError("maintenance end must be after start")
        if ends_at - starts_at > timedelta(hours=2):
            raise ValueError("maintenance window cannot exceed two hours")
        if not reason.strip():
            raise ValueError("maintenance reason is required")
        return self

    @model_validator(mode="after")
    def validate_clob_capacity_reserves(self) -> Self:
        if self.clob_response_max_bytes >= self.clob_ledger_capacity_write_reserve_bytes:
            raise ValueError("CLOB response limit must be below the write reserve")
        if self.clob_ledger_capacity_write_reserve_bytes >= self.clob_ledger_capacity_budget_bytes:
            raise ValueError("CLOB write reserve must be below the ledger budget")
        if (
            self.clob_ledger_capacity_write_reserve_bytes
            >= self.clob_ledger_capacity_min_free_bytes
        ):
            raise ValueError("CLOB write reserve must be below the free-disk reserve")
        return self

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

    @field_validator(
        "telegram_admin_user_id",
        "telegram_chat_id",
        "operator_api_key",
        "polygon_private_key",
        "polymarket_funder",
        "polymarket_signature_type",
        "binance_api_key",
        "binance_private_key_file",
        "clob_ledger_capacity_path",
        "health_maintenance_starts_at",
        "health_maintenance_ends_at",
        "health_maintenance_reason",
        mode="before",
    )
    @classmethod
    def parse_optional_value(cls, value: object) -> object:
        return None if value == "" else value


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_migration_settings() -> MigrationSettings:
    # BaseSettings supplies this required field from MIGRATION_DATABASE_URL.
    return MigrationSettings()  # type: ignore[call-arg]
