from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from stratus.db.base import Base


class WeatherKind(enum.StrEnum):
    HIGH = "high"
    LOW = "low"


class MarketStatus(enum.StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    BLOCKED = "blocked"


class TradeSide(enum.StrEnum):
    BUY_YES = "buy_yes"
    BUY_NO = "buy_no"


class SignalConfidence(enum.StrEnum):
    A = "A"
    B = "B"
    C = "C"


class SignalStatus(enum.StrEnum):
    PENDING = "pending"
    EXECUTED = "executed"
    SKIPPED = "skipped"
    EXPIRED = "expired"


class ExecutionMode(enum.StrEnum):
    PAPER = "paper"
    LIVE = "live"


class PositionStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


def db_enum(enum_type: type[enum.Enum], name: str) -> Enum:
    return Enum(
        enum_type,
        name=name,
        values_callable=lambda members: [item.value for item in members],
    )


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    icao: Mapped[str] = mapped_column(String(4), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    city: Mapped[str] = mapped_column(String(80))
    lat: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    lon: Mapped[Decimal] = mapped_column(Numeric(9, 6))
    tz: Mapped[str] = mapped_column(String(64))
    iem_network: Mapped[str | None] = mapped_column(String(32))
    iem_station_id: Mapped[str | None] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Market(Base):
    __tablename__ = "markets"
    __table_args__ = (Index("ix_markets_active_resolution", "status", "resolves_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    polymarket_id: Mapped[str] = mapped_column(String(128), unique=True)
    event_id: Mapped[str] = mapped_column(String(128))
    condition_id: Mapped[str | None] = mapped_column(String(66))
    yes_token_id: Mapped[str | None] = mapped_column(String(80))
    no_token_id: Mapped[str | None] = mapped_column(String(80))
    last_price_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"))
    market_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    bucket_low_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    bucket_high_c: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    resolution_source: Mapped[str | None] = mapped_column(Text)
    rules_raw: Mapped[str] = mapped_column(Text)
    station_verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    station_verification_reason: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[MarketStatus] = mapped_column(
        db_enum(MarketStatus, "market_status"), server_default="blocked"
    )
    resolved_outcome: Mapped[bool | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolves_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fees_enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    fee_rate: Mapped[Decimal] = mapped_column(Numeric(8, 6), server_default="0")
    fee_exponent: Mapped[int] = mapped_column(SmallInteger, server_default="1")
    fee_taker_only: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "model",
            "run_time",
            "valid_date",
            "kind",
            name="uq_forecasts_identity",
        ),
        Index("ix_forecasts_lookup", "station_id", "valid_date", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    model: Mapped[str] = mapped_column(String(32))
    run_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HistoricalForecast(Base):
    __tablename__ = "historical_forecasts"
    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "model",
            "valid_date",
            "kind",
            "horizon_h",
            name="uq_historical_forecasts_identity",
        ),
        Index(
            "ix_historical_forecasts_calibration",
            "station_id",
            "model",
            "horizon_h",
            "kind",
            "valid_date",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    model: Mapped[str] = mapped_column(String(32))
    valid_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    horizon_h: Mapped[int] = mapped_column(SmallInteger)
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("station_id", "obs_time", name="uq_observations_station_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    obs_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    raw_metar: Mapped[str] = mapped_column(Text)


class DailyActual(Base):
    __tablename__ = "daily_actuals"

    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[WeatherKind] = mapped_column(
        db_enum(WeatherKind, "weather_kind"), primary_key=True
    )
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (
        UniqueConstraint("market_id", "ts", name="uq_market_prices_market_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    midpoint: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    best_bid: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    best_ask: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    bid_depth_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    ask_depth_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))


class ModelProb(Base):
    __tablename__ = "model_probs"
    __table_args__ = (
        UniqueConstraint("market_id", "ts", name="uq_model_probs_market_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prob: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    sigma: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    horizon_h: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    components: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )


class Calibration(Base):
    __tablename__ = "calibration"

    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), primary_key=True)
    model: Mapped[str] = mapped_column(String(32), primary_key=True)
    horizon_bucket: Mapped[str] = mapped_column(String(16), primary_key=True)
    kind: Mapped[WeatherKind] = mapped_column(
        db_enum(WeatherKind, "weather_kind"), primary_key=True
    )
    bias_c: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    sigma_c: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    emos_a: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    emos_b: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    emos_c: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    emos_d: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    sample_n: Mapped[int] = mapped_column(Integer)
    brier: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (
        UniqueConstraint("market_id", "ts", name="uq_signals_market_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    model_prob: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    market_price: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    edge: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    kelly_frac: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    size_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    confidence: Mapped[SignalConfidence] = mapped_column(
        db_enum(SignalConfidence, "signal_confidence")
    )
    status: Mapped[SignalStatus] = mapped_column(db_enum(SignalStatus, "signal_status"))
    skip_reason: Mapped[str | None] = mapped_column(Text)


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("signal_id", "mode", name="uq_orders_signal_mode"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    clob_order_id: Mapped[str | None] = mapped_column(String(128))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    price: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    size_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    filled_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default="0")
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 5))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    # Live positions link 1:1 to their order; paper positions leave this NULL.
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"))
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    avg_price: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    size_shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    size_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 5))
    status: Mapped[PositionStatus] = mapped_column(db_enum(PositionStatus, "position_status"))
    pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    clv: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BankrollLog(Base):
    __tablename__ = "bankroll_log"

    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    mode: Mapped[ExecutionMode] = mapped_column(
        db_enum(ExecutionMode, "execution_mode"), primary_key=True
    )
    balance_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    event: Mapped[str] = mapped_column(Text)


class RuntimeControl(Base):
    __tablename__ = "runtime_control"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    kill_switch: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    mode: Mapped[ExecutionMode] = mapped_column(
        db_enum(ExecutionMode, "execution_mode"), server_default="paper"
    )
    live_confirmation_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    halted_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TelegramDelivery(Base):
    __tablename__ = "telegram_deliveries"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "scheduled_at", name="uq_job_runs_schedule"),
        Index("ix_job_runs_finished", "finished_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64))
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)


class ValidationCheckpoint(Base):
    __tablename__ = "validation_checkpoints"

    checkpoint_day: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    eligible: Mapped[bool] = mapped_column(Boolean)
    passed: Mapped[bool] = mapped_column(Boolean)
    metrics: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )


class OperatorCommand(Base):
    """Immutable audit queue for console-initiated operations.

    Rows are only ever inserted and status-transitioned (pending ->
    succeeded/rejected/failed); nothing deletes them.
    """

    __tablename__ = "operator_commands"
    __table_args__ = (Index("ix_operator_commands_status", "status", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), unique=True)
    command: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
    actor: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class HealthAudit(Base):
    __tablename__ = "health_audits"
    __table_args__ = (Index("ix_health_audits_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    issues: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
