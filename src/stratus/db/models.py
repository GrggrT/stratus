from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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


class TradingVenue(enum.StrEnum):
    POLYMARKET = "polymarket"
    BINANCE_SPOT = "binance_spot"


class VenueMode(enum.StrEnum):
    DISABLED = "disabled"
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    TESTNET = "testnet"
    LIVE = "live"


class PositionStatus(enum.StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class StationStage(enum.StrEnum):
    """Operational lifecycle for a configured weather station.

    SHADOW collects weather/market data without creating signals. PAPER may
    create simulated trades. APPROVED is the only stage eligible for live
    orders. DISABLED stays in the registry but is excluded from collection.
    """

    SHADOW = "shadow"
    PAPER = "paper"
    APPROVED = "approved"
    DISABLED = "disabled"


class AccuracyPromotionTrialStatus(enum.StrEnum):
    COLLECTING = "collecting"
    PASSED = "passed"
    FAILED = "failed"
    ACTIVATED = "activated"


class AccuracyPromotionLookStatus(enum.StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class ResolutionReconciliationClassification(enum.StrEnum):
    MATCH = "match"
    BOUNDARY_SENSITIVE = "boundary_sensitive"
    CLEAR_MISMATCH = "clear_mismatch"
    EXACT_TRUTH_GAP = "exact_truth_gap"
    EXACT_SOURCE_MISMATCH = "exact_source_mismatch"
    TRUTH_UNAVAILABLE = "truth_unavailable"


class StationReliabilityStatus(enum.StrEnum):
    """Selection state derived from independent station/day reconciliation units."""

    COLLECTING = "collecting"
    RELIABLE = "reliable"
    WATCH = "watch"
    BLOCKED = "blocked"


class SettlementCaseClassification(enum.StrEnum):
    VENUE_PENDING = "venue_pending"
    SOURCE_MISSING = "source_missing"
    AMBIGUOUS = "ambiguous"
    MANUAL_REVIEW = "manual_review"
    RESOLVED = "resolved"


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
    stage: Mapped[StationStage] = mapped_column(
        db_enum(StationStage, "station_stage"), server_default="shadow"
    )


class GammaOutcomeEvent(Base):
    """Immutable first-seen witness of a complete Gamma event resolution."""

    __tablename__ = "gamma_outcome_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_gamma_outcome_events_event_id"),
        CheckConstraint(
            "source_response_bytes > 0",
            name="ck_gamma_outcome_events_response_nonempty",
        ),
        CheckConstraint(
            "octet_length(source_response_body) = source_response_bytes",
            name="ck_gamma_outcome_events_response_size",
        ),
        CheckConstraint(
            "market_count >= 2",
            name="ck_gamma_outcome_events_market_count",
        ),
        CheckConstraint(
            "first_outcome_at <= fully_resolved_at",
            name="ck_gamma_outcome_events_resolution_order",
        ),
        Index("ix_gamma_outcome_events_first_seen", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128))
    evidence_protocol: Mapped[str] = mapped_column(
        String(32), server_default="gamma-outcome-v1"
    )
    source_url: Mapped[str] = mapped_column(Text)
    source_http_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_etag: Mapped[str | None] = mapped_column(Text)
    source_response_body: Mapped[bytes] = mapped_column(LargeBinary)
    source_response_sha256: Mapped[str] = mapped_column(String(64))
    source_response_bytes: Mapped[int] = mapped_column(BigInteger)
    source_event_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    first_outcome_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fully_resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    market_count: Mapped[int] = mapped_column(Integer)
    market_universe_hash: Mapped[str] = mapped_column(String(64))
    rules_manifest_hash: Mapped[str] = mapped_column(String(64))
    outcome_vector_hash: Mapped[str] = mapped_column(String(64))
    outcome_vector: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class ClobBookBatch(Base):
    """Immutable raw response for one exact dual-token CLOB batch."""

    __tablename__ = "clob_book_batches"
    __table_args__ = (
        CheckConstraint(
            "requested_token_count >= 2 AND requested_token_count % 2 = 0",
            name="ck_clob_book_batches_even_token_count",
        ),
        CheckConstraint(
            "source_response_bytes > 0",
            name="ck_clob_book_batches_response_nonempty",
        ),
        CheckConstraint(
            "octet_length(source_response_body) = source_response_bytes",
            name="ck_clob_book_batches_response_size",
        ),
        CheckConstraint(
            "request_started_at <= response_received_at",
            name="ck_clob_book_batches_request_order",
        ),
        Index("ix_clob_book_batches_first_seen_at", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    evidence_protocol: Mapped[str] = mapped_column(
        String(32), server_default="clob-dual-v1"
    )
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    source_url: Mapped[str] = mapped_column(Text)
    source_http_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_etag: Mapped[str | None] = mapped_column(Text)
    requested_token_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    requested_token_count: Mapped[int] = mapped_column(Integer)
    requested_tokens_sha256: Mapped[str] = mapped_column(String(64))
    source_response_body: Mapped[bytes] = mapped_column(LargeBinary)
    source_response_bytes: Mapped[int] = mapped_column(BigInteger)
    source_response_sha256: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ClobMarketSnapshot(Base):
    """Immutable, side-explicit YES/NO quote pair from one CLOB batch."""

    __tablename__ = "clob_market_snapshots"
    __table_args__ = (
        CheckConstraint(
            "yes_token_id <> no_token_id",
            name="ck_clob_market_snapshots_distinct_tokens",
        ),
        CheckConstraint(
            "yes_tick_size > 0 AND yes_tick_size < 1 "
            "AND no_tick_size > 0 AND no_tick_size < 1",
            name="ck_clob_market_snapshots_tick_range",
        ),
        CheckConstraint(
            "source_skew_ms >= 0",
            name="ck_clob_market_snapshots_skew_nonnegative",
        ),
        UniqueConstraint(
            "batch_id",
            "market_id",
            name="uq_clob_market_snapshots_batch_market",
        ),
        Index("ix_clob_market_snapshots_market_batch", "market_id", "batch_id"),
        Index(
            "ix_clob_market_snapshots_eligible_market",
            "eligible",
            "market_id",
            "batch_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("clob_book_batches.id", ondelete="RESTRICT")
    )
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    condition_id: Mapped[str] = mapped_column(String(66))
    yes_token_id: Mapped[str] = mapped_column(String(80))
    no_token_id: Mapped[str] = mapped_column(String(80))
    yes_source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    no_source_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    yes_source_hash: Mapped[str] = mapped_column(Text)
    no_source_hash: Mapped[str] = mapped_column(Text)
    yes_payload_sha256: Mapped[str] = mapped_column(String(64))
    no_payload_sha256: Mapped[str] = mapped_column(String(64))
    yes_tick_size: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    no_tick_size: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    yes_neg_risk: Mapped[bool] = mapped_column(Boolean)
    no_neg_risk: Mapped[bool] = mapped_column(Boolean)
    yes_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    yes_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    yes_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    yes_bid_depth_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    yes_ask_depth_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    no_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    no_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    no_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    no_bid_depth_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    no_ask_depth_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    source_skew_ms: Mapped[int] = mapped_column(Integer)
    complement_residual: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    binary_yes_probability: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    complete: Mapped[bool] = mapped_column(Boolean)
    eligible: Mapped[bool] = mapped_column(Boolean)
    record_hash: Mapped[str] = mapped_column(String(64))


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
    resolution_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("gamma_outcome_events.id")
    )
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
        Index("ix_forecasts_point_in_time", "available_at", "valid_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    model: Mapped[str] = mapped_column(String(32))
    run_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    run_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_runs.id"))
    ingest_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_ingests.id"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ForecastTrajectory(Base):
    __tablename__ = "forecast_trajectories"
    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "model",
            "run_time",
            "valid_date",
            name="uq_forecast_trajectory_identity",
        ),
        Index(
            "ix_forecast_trajectory_point_in_time",
            "station_id",
            "valid_date",
            "available_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    model: Mapped[str] = mapped_column(String(32))
    run_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_date: Mapped[date] = mapped_column(Date)
    points: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_runs.id"))
    ingest_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_ingests.id"))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )


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


class ObservationRecoveryRuntime(Base):
    """Prospective boundary for observation-recovery evidence."""

    __tablename__ = "observation_recovery_runtime"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    source_protocol: Mapped[str] = mapped_column(String(64))
    source_url_sha256: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    completeness_version: Mapped[str] = mapped_column(String(32))
    retention_policy_version: Mapped[str] = mapped_column(String(32))
    retention_policy_sha256: Mapped[str] = mapped_column(String(64))


class ObservationRecoveryArtifact(Base):
    """Exact immutable source response and its recovery decision."""

    __tablename__ = "observation_recovery_artifacts"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    local_date: Mapped[date] = mapped_column(Date)
    timezone: Mapped[str] = mapped_column(String(64))
    utc_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    utc_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    network: Mapped[str] = mapped_column(String(32))
    source_station_id: Mapped[str] = mapped_column(String(16))
    source_name: Mapped[str] = mapped_column(String(64))
    source_protocol: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(32))
    completeness_version: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    request_method: Mapped[str] = mapped_column(String(8))
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int] = mapped_column(SmallInteger)
    content_type: Mapped[str] = mapped_column(Text)
    request_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    request_manifest_sha256: Mapped[str] = mapped_column(String(64))
    http_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[bytes] = mapped_column(LargeBinary)
    response_sha256: Mapped[str] = mapped_column(String(64))
    response_bytes: Mapped[int] = mapped_column(BigInteger)
    retention_policy_url: Mapped[str] = mapped_column(Text)
    retention_policy_reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True)
    )
    retention_policy_version: Mapped[str] = mapped_column(String(32))
    retention_policy_statement: Mapped[bytes] = mapped_column(LargeBinary)
    retention_policy_sha256: Mapped[str] = mapped_column(String(64))
    storage_permitted: Mapped[bool] = mapped_column(Boolean)
    point_count: Mapped[int] = mapped_column(Integer)
    point_set_sha256: Mapped[str] = mapped_column(String(64))
    sample_n: Mapped[int] = mapped_column(Integer)
    minimum_samples: Mapped[int] = mapped_column(Integer)
    boundary_tolerance_seconds: Mapped[int] = mapped_column(Integer)
    max_permitted_gap_seconds: Mapped[int] = mapped_column(Integer)
    first_observation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    max_gap_seconds: Mapped[int] = mapped_column(Integer)
    validation_status: Mapped[str] = mapped_column(String(16))
    validation_code: Mapped[str] = mapped_column(String(64))
    validation_details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class ObservationRecoveryPoint(Base):
    """One immutable parsed point bound to exact source bytes."""

    __tablename__ = "observation_recovery_points"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    artifact_id: Mapped[int] = mapped_column(
        ForeignKey("observation_recovery_artifacts.id", ondelete="RESTRICT")
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    obs_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    raw_report: Mapped[str] = mapped_column(Text)
    source_row_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    source_row_sha256: Mapped[str] = mapped_column(String(64))
    normalized_point_bytes: Mapped[bytes] = mapped_column(LargeBinary)
    normalized_point_sha256: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class DailyActual(Base):
    __tablename__ = "daily_actuals"

    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"), primary_key=True)
    date: Mapped[date] = mapped_column(Date, primary_key=True)
    kind: Mapped[WeatherKind] = mapped_column(
        db_enum(WeatherKind, "weather_kind"), primary_key=True
    )
    temp_c: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    recovery_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("observation_recovery_artifacts.id", ondelete="RESTRICT")
    )
    source_protocol: Mapped[str | None] = mapped_column(String(64))
    first_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    sample_n: Mapped[int | None] = mapped_column(Integer)
    first_observation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_observation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    max_gap_seconds: Mapped[int | None] = mapped_column(Integer)
    record_hash: Mapped[str | None] = mapped_column(String(64))


class MarketPrice(Base):
    __tablename__ = "market_prices"
    __table_args__ = (UniqueConstraint("market_id", "ts", name="uq_market_prices_market_time"),)

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
    __table_args__ = (UniqueConstraint("market_id", "ts", name="uq_model_probs_market_time"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prob: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    sigma: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    horizon_h: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    components: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    market_anchor_protocol: Mapped[str] = mapped_column(
        String(32), server_default="legacy-synthetic-yes-v0"
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
        Index(
            "ix_signals_pending_stop_cleanup",
            "id",
            postgresql_where=text("status = 'pending'::signal_status"),
        ),
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
    clob_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    station_reliability_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("station_reliability_snapshots.id", ondelete="RESTRICT")
    )
    token_id: Mapped[str | None] = mapped_column(String(80))
    benchmark_protocol: Mapped[str] = mapped_column(
        String(32), server_default="legacy-synthetic-yes-v0"
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("signal_id", "mode", name="uq_orders_signal_mode"),
        Index(
            "ix_orders_live_open_stop_cleanup",
            "id",
            postgresql_where=text(
                "mode = 'live'::execution_mode "
                "AND status IN ('open', 'pending')"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"))
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    clob_order_id: Mapped[str | None] = mapped_column(String(128))
    token_id: Mapped[str | None] = mapped_column(String(80))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    price: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    size_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    filled_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4), server_default="0")
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 5))
    status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExecutionCostRuntime(Base):
    __tablename__ = "execution_cost_runtime"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    paper_execution_evidence: Mapped[str] = mapped_column(String(32))
    live_reconciliation_evidence: Mapped[str] = mapped_column(String(40))
    postfill_mark_protocol: Mapped[str] = mapped_column(String(32))
    mark_horizons_seconds: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    mark_grace_seconds: Mapped[int] = mapped_column(Integer)
    live_fill_snapshot_max_age_seconds: Mapped[int] = mapped_column(Integer)


class ExecutionOrderIntent(Base):
    __tablename__ = "execution_order_intents"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_execution_order_intents_order"),
        CheckConstraint(
            "(release_epoch_id IS NULL AND predictive_lane_id IS NULL "
            "AND selection_lane_id IS NULL AND execution_lane_id IS NULL "
            "AND operational_lane_id IS NULL AND station_lane_id IS NULL "
            "AND truth_lane_id IS NULL) OR "
            "(release_epoch_id IS NOT NULL AND predictive_lane_id IS NOT NULL "
            "AND selection_lane_id IS NOT NULL AND execution_lane_id IS NOT NULL "
            "AND operational_lane_id IS NOT NULL AND station_lane_id IS NOT NULL "
            "AND truth_lane_id IS NOT NULL)",
            name="ck_execution_order_intents_continuity_attestation",
        ),
        Index("ix_execution_order_intents_market_time", "market_id", "intent_at"),
        Index("ix_execution_order_intents_mode_time", "mode", "intent_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    model_prob_id: Mapped[int] = mapped_column(
        ForeignKey("model_probs.id", ondelete="RESTRICT")
    )
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    decision_clob_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    token_id: Mapped[str] = mapped_column(String(80))
    pipeline_cohort: Mapped[str] = mapped_column(String(128))
    release_epoch_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_pipeline_release_epochs.id", ondelete="RESTRICT")
    )
    predictive_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    selection_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    execution_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    operational_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    station_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    truth_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    signal_record_hash: Mapped[str] = mapped_column(String(64))
    model_record_hash: Mapped[str] = mapped_column(String(64))
    snapshot_record_hash: Mapped[str] = mapped_column(String(64))
    yes_model_probability: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    side_model_probability: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    side_best_bid: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    side_best_ask: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    side_midpoint: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    order_style: Mapped[str] = mapped_column(String(16))
    requested_limit_price: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    requested_shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    requested_notional_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    expected_fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    expected_fee_unavailable_reason: Mapped[str | None] = mapped_column(String(64))
    expected_slippage_per_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    expected_slippage_unavailable_reason: Mapped[str | None] = mapped_column(String(64))
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    intent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ExecutionReconciliationObservation(Base):
    __tablename__ = "execution_reconciliation_observations"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "observation_kind",
            "sdk_method",
            "payload_sha256",
            name="uq_execution_reconciliation_observations_content",
        ),
        Index(
            "ix_execution_reconciliation_observations_order_seen",
            "order_id",
            "first_seen_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    observation_kind: Mapped[str] = mapped_column(String(16))
    sdk_method: Mapped[str] = mapped_column(String(64))
    parser_version: Mapped[str] = mapped_column(String(64))
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    payload_sha256: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ExecutionFillLeg(Base):
    __tablename__ = "execution_fill_legs"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "external_leg_key",
            name="uq_execution_fill_legs_order_external_key",
        ),
        UniqueConstraint(
            "intent_id",
            "leg_sequence",
            name="uq_execution_fill_legs_intent_sequence",
        ),
        Index("ix_execution_fill_legs_market_effective", "market_id", "fill_effective_at"),
        Index("ix_execution_fill_legs_intent", "intent_id", "leg_sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("execution_order_intents.id", ondelete="RESTRICT")
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    reconciliation_observation_id: Mapped[int | None] = mapped_column(
        ForeignKey("execution_reconciliation_observations.id", ondelete="RESTRICT")
    )
    fill_clob_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    token_id: Mapped[str] = mapped_column(String(80))
    source_kind: Mapped[str] = mapped_column(String(24))
    fill_clob_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    external_trade_id: Mapped[str | None] = mapped_column(String(128))
    external_leg_key: Mapped[str] = mapped_column(String(160))
    leg_sequence: Mapped[int] = mapped_column(Integer)
    liquidity_role: Mapped[str] = mapped_column(String(16))
    shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    price: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    notional_usd: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    fee_semantics: Mapped[str] = mapped_column(String(32))
    venue_filled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    fill_effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timestamp_semantics: Mapped[str] = mapped_column(String(32))
    fill_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    fill_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    fill_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    eligible: Mapped[bool] = mapped_column(Boolean)
    eligibility_reason: Mapped[str] = mapped_column(String(64))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ExecutionCostObligation(Base):
    __tablename__ = "execution_cost_obligations"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_execution_cost_obligations_order"),
        UniqueConstraint("intent_id", name="uq_execution_cost_obligations_intent"),
        Index("ix_execution_cost_obligations_due", "state", "next_due_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("execution_order_intents.id", ondelete="RESTRICT")
    )
    terminal_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))


class ExecutionPostfillMark(Base):
    __tablename__ = "execution_postfill_marks"
    __table_args__ = (
        UniqueConstraint(
            "fill_leg_id",
            "horizon_seconds",
            name="uq_execution_postfill_marks_leg_horizon",
        ),
        Index("ix_execution_postfill_marks_target", "target_at", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    fill_leg_id: Mapped[int] = mapped_column(
        ForeignKey("execution_fill_legs.id", ondelete="RESTRICT")
    )
    horizon_seconds: Mapped[int] = mapped_column(Integer)
    target_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    clob_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    clob_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    missing_reason: Mapped[str | None] = mapped_column(String(64))
    side_best_bid: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    side_best_ask: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    side_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offset_seconds: Mapped[int | None] = mapped_column(Integer)
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ExecutionCostSeal(Base):
    __tablename__ = "execution_cost_seals"
    __table_args__ = (
        UniqueConstraint("obligation_id", name="uq_execution_cost_seals_obligation"),
        UniqueConstraint("intent_id", name="uq_execution_cost_seals_intent"),
        UniqueConstraint("order_id", name="uq_execution_cost_seals_order"),
        Index("ix_execution_cost_seals_mode_scope", "eligible", "sealed_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    obligation_id: Mapped[int] = mapped_column(
        ForeignKey("execution_cost_obligations.id", ondelete="RESTRICT")
    )
    intent_id: Mapped[int] = mapped_column(
        ForeignKey("execution_order_intents.id", ondelete="RESTRICT")
    )
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    eligible: Mapped[bool] = mapped_column(Boolean)
    eligibility_reason: Mapped[str] = mapped_column(String(64))
    reason_detail: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    aggregate_role: Mapped[str] = mapped_column(String(16))
    leg_count: Mapped[int] = mapped_column(Integer)
    total_shares: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    filled_notional_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    vwap_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    total_fee_usd: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    fee_per_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    intent_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    side_model_probability: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    weighted_fill_midpoint: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    gross_model_edge: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    latency_movement: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    taker_spread: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    taker_slippage: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    maker_improvement: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    fee_drag: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    realized_net_edge: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    expected_slippage_per_share: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    expected_slippage_coverage: Mapped[str] = mapped_column(String(24))
    adverse_selection_300s: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    markout_300s_coverage: Mapped[str] = mapped_column(String(24))
    adverse_selection_1800s: Mapped[Decimal | None] = mapped_column(Numeric(12, 10))
    markout_1800s_coverage: Mapped[str] = mapped_column(String(24))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    sealed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class SelectionPolicyDecision(Base):
    """Immutable shadow-policy decision captured after fill, before resolution.

    A versioned policy definition is recorded beside the decision so later
    analytics cannot silently reinterpret an old order with changed code.
    """

    __tablename__ = "selection_policy_decisions"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "policy_name",
            name="uq_selection_policy_decisions_order_policy",
        ),
        Index(
            "ix_selection_policy_decisions_pipeline",
            "pipeline_version",
            "policy_name",
            "decided_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    policy_name: Mapped[str] = mapped_column(String(64))
    policy_hash: Mapped[str] = mapped_column(String(16))
    max_predicted_edge: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    predicted_edge: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    accepted: Mapped[bool] = mapped_column(Boolean)
    pipeline_version: Mapped[str] = mapped_column(String(128))
    signal_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (
        Index(
            "uq_positions_order",
            "order_id",
            unique=True,
            postgresql_where=text("order_id IS NOT NULL"),
        ),
    )

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
    market_closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    benchmark_protocol: Mapped[str] = mapped_column(
        String(32), server_default="legacy-synthetic-yes-v0"
    )
    token_id: Mapped[str | None] = mapped_column(String(80))
    clv_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    clv_snapshot_hash: Mapped[str | None] = mapped_column(String(64))


class ProspectiveValidationRuntime(Base):
    """Immutable database-clock boundary for the zero-backfill ledger."""

    __tablename__ = "prospective_validation_runtime"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))


class ProspectiveValidationEnrollment(Base):
    """One immutable order fill admitted after the prospective watermark."""

    __tablename__ = "prospective_validation_enrollments"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"))
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="RESTRICT"))
    model_prob_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_probs.id", ondelete="RESTRICT")
    )
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id", ondelete="RESTRICT"))
    clob_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    mode: Mapped[ExecutionMode] = mapped_column(db_enum(ExecutionMode, "execution_mode"))
    pipeline_cohort: Mapped[str | None] = mapped_column(String(128))
    station_icao: Mapped[str | None] = mapped_column(String(4))
    market_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    side: Mapped[TradeSide] = mapped_column(db_enum(TradeSide, "trade_side"))
    token_id: Mapped[str | None] = mapped_column(String(80))
    fill_price: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    filled_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    fee_usd: Mapped[Decimal] = mapped_column(Numeric(14, 5))
    size_shares: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    filled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    benchmark_protocol: Mapped[str] = mapped_column(String(32))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))
    order_record_hash: Mapped[str] = mapped_column(String(64))
    signal_record_hash: Mapped[str] = mapped_column(String(64))
    model_record_hash: Mapped[str | None] = mapped_column(String(64))
    snapshot_record_hash: Mapped[str | None] = mapped_column(String(64))
    eligible: Mapped[bool] = mapped_column(Boolean)
    eligibility_reason: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ProspectiveValidationOutcome(Base):
    """Immutable Gamma-attested resolution of one enrolled order fill."""

    __tablename__ = "prospective_validation_outcomes"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(
        ForeignKey("prospective_validation_enrollments.id", ondelete="RESTRICT")
    )
    resolution_event_id: Mapped[int] = mapped_column(
        ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT")
    )
    gamma_record_hash: Mapped[str] = mapped_column(String(64))
    resolution_vector_hash: Mapped[str] = mapped_column(String(64))
    resolved_yes: Mapped[bool] = mapped_column(Boolean)
    won: Mapped[bool] = mapped_column(Boolean)
    market_closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recognized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pnl_usd: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    close_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    close_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    close_price: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    clv: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    skill_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    luck_pnl_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    record_hash: Mapped[str] = mapped_column(String(64))


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
    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_runtime_control_singleton_id",
        ),
    )

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
    __table_args__ = (
        Index(
            "ix_telegram_deliveries_unacknowledged",
            "id",
            postgresql_where=text("acknowledged_at IS NULL"),
        ),
        Index(
            "ix_telegram_deliveries_due",
            "next_attempt_at",
            "lease_until",
            "id",
            postgresql_where=text("sent_at IS NULL AND dead_lettered_at IS NULL"),
        ),
        Index(
            "ix_telegram_deliveries_dead_letter",
            "id",
            postgresql_where=text("dead_lettered_at IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    payload: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NotificationDeliveryRuntime(Base):
    __tablename__ = "notification_delivery_runtime"
    __table_args__ = (
        CheckConstraint(
            "channel ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name="ck_notification_delivery_runtime_channel",
        ),
    )

    channel: Mapped[str] = mapped_column(String(32), primary_key=True)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))


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
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


class OperatorCommand(Base):
    """Immutable audit queue for console-initiated operations.

    Rows are only ever inserted and status-transitioned (pending ->
    succeeded/rejected/failed); nothing deletes them.
    """

    __tablename__ = "operator_commands"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'rejected', 'failed') "
            "AND ((status = 'pending' AND detail IS NULL "
            "AND executed_at IS NULL) OR (status IN ("
            "'succeeded', 'rejected', 'failed') AND detail IS NOT NULL "
            "AND btrim(detail) <> '' AND executed_at IS NOT NULL "
            "AND executed_at >= created_at))",
            name="ck_operator_commands_lifecycle",
        ),
        Index("ix_operator_commands_status", "status", "id"),
    )

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


class PaperCanarySafetyLease(Base):
    """Short DB-clock lease authorizing one release PAPER canary."""

    __tablename__ = "paper_canary_safety_leases"
    __table_args__ = (
        CheckConstraint(
            "lease_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND acquire_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND resume_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND watchdog_stop_request_id ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND direct_stop_request_id ~ "
            "'^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$' "
            "AND release_request_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$'",
            name="ck_paper_canary_lease_safe_identifiers",
        ),
        CheckConstraint(
            "nonce_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_paper_canary_lease_nonce_sha256",
        ),
        CheckConstraint(
            "btrim(resume_actor) <> '' "
            "AND char_length(resume_actor) <= 64 "
            "AND btrim(resume_reason) <> '' "
            "AND char_length(resume_reason) <= 2000",
            name="ck_paper_canary_lease_resume_identity",
        ),
        CheckConstraint(
            "acquire_request_id <> resume_request_id "
            "AND watchdog_stop_request_id NOT IN "
            "(acquire_request_id, resume_request_id) "
            "AND direct_stop_request_id NOT IN "
            "(acquire_request_id, resume_request_id, watchdog_stop_request_id) "
            "AND release_request_id NOT IN ("
            "acquire_request_id, resume_request_id, "
            "watchdog_stop_request_id, direct_stop_request_id)",
            name="ck_paper_canary_lease_distinct_requests",
        ),
        CheckConstraint(
            "heartbeat_at >= acquired_at "
            "AND watchdog_at >= acquired_at "
            "AND expires_at > heartbeat_at "
            "AND expires_at <= heartbeat_at + interval '30 seconds'",
            name="ck_paper_canary_lease_bounded_time",
        ),
        CheckConstraint(
            "state IN ("
            "'active', 'released', 'expired_stopped', 'expired_safe', "
            "'revoked_stopped')",
            name="ck_paper_canary_lease_state",
        ),
        CheckConstraint(
            "(state = 'active' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NULL) "
            "OR (state = 'released' AND released_at IS NOT NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'expired_stopped' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'revoked_stopped' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NOT NULL) "
            "OR (state = 'expired_safe' AND released_at IS NULL "
            " AND release_request_id IS NOT NULL AND stop_command_id IS NULL)",
            name="ck_paper_canary_lease_terminal_shape",
        ),
        Index(
            "uq_paper_canary_single_active_lease",
            "state",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index(
            "ix_paper_canary_active_expiry",
            "expires_at",
            "id",
            postgresql_where=text("state = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    lease_id: Mapped[str] = mapped_column(String(64), unique=True)
    run_id: Mapped[str] = mapped_column(String(64), unique=True)
    acquire_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    resume_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    resume_actor: Mapped[str] = mapped_column(String(64))
    resume_reason: Mapped[str] = mapped_column(Text)
    watchdog_stop_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    direct_stop_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    release_request_id: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_sha256: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(24), server_default="active")
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    watchdog_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_command_id: Mapped[int | None] = mapped_column(
        ForeignKey("operator_commands.id", ondelete="RESTRICT"),
    )


class HealthAudit(Base):
    __tablename__ = "health_audits"
    __table_args__ = (Index("ix_health_audits_ts", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


class StorageCapacitySample(Base):
    """Point-in-time storage footprint used by retention and capacity policy."""

    __tablename__ = "storage_capacity_samples"
    __table_args__ = (
        CheckConstraint(
            "database_bytes >= 0 AND clob_book_batches_bytes >= 0 AND "
            "clob_market_snapshots_bytes >= 0",
            name="ck_storage_capacity_samples_bytes",
        ),
        CheckConstraint(
            "clob_batch_count >= 0 AND clob_snapshot_count >= 0",
            name="ck_storage_capacity_samples_counts",
        ),
        CheckConstraint(
            "free_disk_bytes IS NULL OR free_disk_bytes >= 0",
            name="ck_storage_capacity_samples_free_disk",
        ),
        CheckConstraint(
            "oldest_batch_at IS NULL OR newest_batch_at IS NULL OR "
            "oldest_batch_at <= newest_batch_at",
            name="ck_storage_capacity_samples_batch_order",
        ),
        UniqueConstraint("captured_at", name="uq_storage_capacity_samples_captured"),
        Index("ix_storage_capacity_samples_captured", "captured_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    database_bytes: Mapped[int] = mapped_column(BigInteger)
    clob_book_batches_bytes: Mapped[int] = mapped_column(BigInteger)
    clob_market_snapshots_bytes: Mapped[int] = mapped_column(BigInteger)
    clob_batch_count: Mapped[int] = mapped_column(BigInteger)
    clob_snapshot_count: Mapped[int] = mapped_column(BigInteger)
    oldest_batch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    newest_batch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    free_disk_bytes: Mapped[int | None] = mapped_column(BigInteger)
    retention_policy_version: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )


class VenueRuntimeControl(Base):
    """Independent execution state for each external trading venue."""

    __tablename__ = "venue_runtime_control"

    venue: Mapped[TradingVenue] = mapped_column(
        db_enum(TradingVenue, "trading_venue"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    mode: Mapped[VenueMode] = mapped_column(
        db_enum(VenueMode, "venue_mode"), server_default="disabled"
    )
    kill_switch: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    halted_reason: Mapped[str | None] = mapped_column(Text)
    live_confirmation_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    strategy_version: Mapped[str | None] = mapped_column(String(64))
    risk_config_version: Mapped[str] = mapped_column(String(64), server_default="legacy")
    last_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CryptoSymbol(Base):
    __tablename__ = "crypto_symbols"
    __table_args__ = (
        UniqueConstraint("venue", "symbol", name="uq_crypto_symbols_venue_symbol"),
        Index("ix_crypto_symbols_enabled_status", "enabled", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    venue: Mapped[TradingVenue] = mapped_column(db_enum(TradingVenue, "trading_venue"))
    symbol: Mapped[str] = mapped_column(String(32))
    base_asset: Mapped[str] = mapped_column(String(16))
    quote_asset: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    price_tick: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    quantity_step: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    minimum_quantity: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    minimum_notional: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    maximum_notional: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    supports_oco: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    supports_oto: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    filters: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'{}'::jsonb"),
    )
    filter_hash: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CryptoCandle(Base):
    __tablename__ = "crypto_candles"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "interval",
            "open_time",
            name="uq_crypto_candles_identity",
        ),
        Index("ix_crypto_candles_lookup", "symbol_id", "interval", "open_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("crypto_symbols.id"))
    interval: Mapped[str] = mapped_column(String(8))
    open_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    close_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    high_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    low_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    close_price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    base_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quote_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    trade_count: Mapped[int] = mapped_column(BigInteger)
    taker_buy_base_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    taker_buy_quote_volume: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    closed: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    source: Mapped[str] = mapped_column(String(32), server_default="binance")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CryptoAggregateTrade(Base):
    __tablename__ = "crypto_aggregate_trades"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "aggregate_trade_id",
            name="uq_crypto_aggregate_trades_identity",
        ),
        CheckConstraint(
            "aggregate_trade_id >= 0 AND first_trade_id >= 0 AND last_trade_id >= 0",
            name="ck_crypto_aggregate_trades_nonnegative_ids",
        ),
        CheckConstraint(
            "first_trade_id <= last_trade_id",
            name="ck_crypto_aggregate_trades_trade_id_range",
        ),
        CheckConstraint(
            "price > 0 AND quantity > 0",
            name="ck_crypto_aggregate_trades_positive_values",
        ),
        Index(
            "ix_crypto_aggregate_trades_time",
            "symbol_id",
            "trade_time",
            "aggregate_trade_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("crypto_symbols.id"))
    aggregate_trade_id: Mapped[int] = mapped_column(BigInteger)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    first_trade_id: Mapped[int] = mapped_column(BigInteger)
    last_trade_id: Mapped[int] = mapped_column(BigInteger)
    trade_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    buyer_is_maker: Mapped[bool] = mapped_column(Boolean)
    best_price_match: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(32), server_default="binance_archive")
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CryptoArchiveImport(Base):
    __tablename__ = "crypto_archive_imports"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "interval",
            "trading_date",
            name="uq_crypto_archive_imports_identity",
        ),
        Index("ix_crypto_archive_imports_status", "status", "trading_date"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("crypto_symbols.id"))
    # Kline imports use their interval (for example, "1m"); non-kline
    # datasets use a stable archive key such as "aggTrades".
    interval: Mapped[str] = mapped_column(String(16))
    trading_date: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    archive_bytes: Mapped[int] = mapped_column(BigInteger)
    row_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CryptoMarketSnapshot(Base):
    __tablename__ = "crypto_market_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "symbol_id",
            "snapshot_type",
            "update_id",
            name="uq_crypto_market_snapshots_identity",
        ),
        Index("ix_crypto_market_snapshots_time", "symbol_id", "event_time"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int] = mapped_column(ForeignKey("crypto_symbols.id"))
    snapshot_type: Mapped[str] = mapped_column(String(32))
    update_id: Mapped[int] = mapped_column(BigInteger)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    best_bid: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    best_bid_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    best_ask: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    best_ask_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18))
    spread_bps: Mapped[Decimal] = mapped_column(Numeric(16, 8))
    bid_depth_quote: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    ask_depth_quote: Mapped[Decimal | None] = mapped_column(Numeric(38, 18))
    imbalance: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    depth_valid: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class CryptoDataGap(Base):
    __tablename__ = "crypto_data_gaps"
    __table_args__ = (Index("ix_crypto_data_gaps_status", "status", "detected_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    symbol_id: Mapped[int | None] = mapped_column(ForeignKey("crypto_symbols.id"))
    stream: Mapped[str] = mapped_column(String(64))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expected_cursor: Mapped[str | None] = mapped_column(String(64))
    actual_cursor: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default="open")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'{}'::jsonb"),
    )


class CryptoResearchExperiment(Base):
    __tablename__ = "crypto_research_experiments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('registered', 'oos_running', 'oos_complete', "
            "'holdout_unlocked', 'holdout_running', 'complete', 'failed')",
            name="ck_crypto_research_experiment_status",
        ),
        CheckConstraint(
            "dataset_start < dataset_end AND holdout_start < holdout_end",
            name="ck_crypto_research_experiment_windows",
        ),
        CheckConstraint(
            "decision IS NULL OR decision IN ('GO', 'NO_GO')",
            name="ck_crypto_research_experiment_decision",
        ),
        Index("ix_crypto_research_experiments_status", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    experiment_key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[str] = mapped_column(String(24), server_default="registered")
    symbols: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    interval: Mapped[str] = mapped_column(String(8))
    dataset_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    dataset_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    holdout_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    holdout_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    plan: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    plan_hash: Mapped[str] = mapped_column(String(64))
    candidate_set_hash: Mapped[str] = mapped_column(String(64))
    symbol_filters: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    symbol_filters_hash: Mapped[str] = mapped_column(String(64))
    execution_config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    execution_config_hash: Mapped[str] = mapped_column(String(64))
    selection_policy: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    engine_version: Mapped[str] = mapped_column(String(64))
    protocol_version: Mapped[str] = mapped_column(String(64))
    selected_candidate_key: Mapped[str | None] = mapped_column(String(64))
    holdout_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision: Mapped[str | None] = mapped_column(String(16))
    decision_reasons: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'[]'::jsonb"),
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CryptoResearchCandidate(Base):
    __tablename__ = "crypto_research_candidates"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "candidate_key",
            name="uq_crypto_research_candidates_identity",
        ),
        Index("ix_crypto_research_candidates_experiment", "experiment_id", "candidate_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("crypto_research_experiments.id"))
    candidate_key: Mapped[str] = mapped_column(String(64))
    strategy_name: Mapped[str] = mapped_column(String(64))
    strategy_version: Mapped[str] = mapped_column(String(64))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    registered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CryptoResearchResult(Base):
    __tablename__ = "crypto_research_results"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "candidate_id",
            "symbol_id",
            "phase",
            "fold_number",
            "stress_case",
            name="uq_crypto_research_results_identity",
        ),
        CheckConstraint(
            "phase IN ('oos', 'holdout')",
            name="ck_crypto_research_result_phase",
        ),
        CheckConstraint(
            "stress_case IN ('baseline', 'double_slippage')",
            name="ck_crypto_research_result_stress",
        ),
        CheckConstraint(
            "fold_number >= 0 AND window_start < window_end",
            name="ck_crypto_research_result_window",
        ),
        Index(
            "ix_crypto_research_results_lookup",
            "experiment_id",
            "phase",
            "fold_number",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    experiment_id: Mapped[int] = mapped_column(ForeignKey("crypto_research_experiments.id"))
    candidate_id: Mapped[int] = mapped_column(ForeignKey("crypto_research_candidates.id"))
    symbol_id: Mapped[int] = mapped_column(ForeignKey("crypto_symbols.id"))
    phase: Mapped[str] = mapped_column(String(16))
    fold_number: Mapped[int] = mapped_column(SmallInteger)
    stress_case: Mapped[str] = mapped_column(String(24))
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_hash: Mapped[str] = mapped_column(String(64))
    data_hash: Mapped[str] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64))
    event_count: Mapped[int] = mapped_column(BigInteger)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    cycle_net_pnls: Mapped[list[str]] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvidenceContinuityRuntime(Base):
    """Immutable revision-0040 database-clock control-plane boundary."""

    __tablename__ = "evidence_continuity_runtime"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(48))
    definition_hash: Mapped[str] = mapped_column(String(64))


class ProbabilityPipelineReleaseEpoch(Base):
    """Immutable activation chain for one exact probability-pipeline cohort."""

    __tablename__ = "probability_pipeline_release_epochs"
    __table_args__ = (
        UniqueConstraint(
            "previous_epoch_id",
            name="uq_probability_pipeline_release_epochs_previous",
        ),
        UniqueConstraint(
            "plan_hash",
            name="uq_probability_pipeline_release_epochs_plan_hash",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_probability_pipeline_release_epochs_record_hash",
        ),
        CheckConstraint(
            "pipeline_version ~ '^[a-z0-9][a-z0-9._-]{0,127}$'",
            name="ck_probability_pipeline_release_epochs_version",
        ),
        CheckConstraint(
            "material_fingerprint ~ '^[0-9a-f]{16}$' "
            "AND activation_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND image_digest ~ '^[0-9a-f]{64}$' "
            "AND plan_hash ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_probability_pipeline_release_epochs_hashes",
        ),
        CheckConstraint(
            "cohort = pipeline_version || '+' || material_fingerprint || "
            "'+promotions-' || left(activation_manifest_hash, 16) "
            "AND length(cohort) <= 128",
            name="ck_probability_pipeline_release_epochs_cohort",
        ),
        CheckConstraint(
            "approval_reason = btrim(approval_reason) "
            "AND length(approval_reason) BETWEEN 16 AND 2000 "
            "AND approval_reason !~ '[[:cntrl:]]' "
            "AND array_length("
            "regexp_split_to_array(approval_reason, '[[:space:]]+'), 1"
            ") >= 3",
            name="ck_probability_pipeline_release_epochs_reason",
        ),
        CheckConstraint(
            "previous_epoch_id IS NULL OR previous_epoch_id < id",
            name="ck_probability_pipeline_release_epochs_previous_order",
        ),
        Index(
            "ix_probability_pipeline_release_epochs_opened",
            "opened_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    pipeline_version: Mapped[str] = mapped_column(String(128))
    material_fingerprint: Mapped[str] = mapped_column(String(16))
    activation_manifest_hash: Mapped[str] = mapped_column(String(64))
    cohort: Mapped[str] = mapped_column(String(192))
    image_digest: Mapped[str] = mapped_column(String(64))
    plan_id: Mapped[int] = mapped_column(
        ForeignKey(
            "probability_pipeline_release_plans.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_probability_pipeline_release_epochs_plan",
        ),
        unique=True,
    )
    previous_epoch_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_pipeline_release_epochs.id", ondelete="RESTRICT")
    )
    approval_reason: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ProbabilityPipelineReleasePlanRecord(Base):
    """Immutable reviewed manifest preview consumed by one release epoch."""

    __tablename__ = "probability_pipeline_release_plans"
    __table_args__ = (
        UniqueConstraint(
            "plan_hash",
            name="uq_probability_pipeline_release_plans_plan_hash",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_probability_pipeline_release_plans_record_hash",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    planned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pipeline_version: Mapped[str] = mapped_column(String(128))
    material_fingerprint: Mapped[str] = mapped_column(String(16))
    activation_manifest_hash: Mapped[str] = mapped_column(String(64))
    cohort: Mapped[str] = mapped_column(String(192))
    image_digest: Mapped[str] = mapped_column(String(64))
    previous_epoch_id: Mapped[int | None] = mapped_column(
        ForeignKey("probability_pipeline_release_epochs.id", ondelete="RESTRICT")
    )
    release_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    release_manifest_hash: Mapped[str] = mapped_column(String(64))
    lane_plans: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    lane_manifest_hash: Mapped[str] = mapped_column(String(64))
    reviewed_by: Mapped[str] = mapped_column(String(128))
    approval_reason: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class EvidenceContinuityLane(Base):
    """One immutable reviewed continuity decision for one release epoch."""

    __tablename__ = "evidence_continuity_lanes"
    __table_args__ = (
        UniqueConstraint(
            "epoch_id",
            "lane_name",
            name="uq_evidence_continuity_lanes_epoch_name",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_evidence_continuity_lanes_record_hash",
        ),
        Index(
            "ix_evidence_continuity_lanes_name_boundary",
            "lane_name",
            "boundary_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    epoch_id: Mapped[int] = mapped_column(
        ForeignKey("probability_pipeline_release_epochs.id", ondelete="RESTRICT")
    )
    lane_name: Mapped[str] = mapped_column(String(48))
    action: Mapped[str] = mapped_column(String(24))
    identity_hash: Mapped[str] = mapped_column(String(64))
    parent_lane_id: Mapped[int | None] = mapped_column(
        ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT")
    )
    boundary_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    manifest: Mapped[dict[str, Any]] = mapped_column(JSONB)
    rationale: Mapped[str] = mapped_column(Text)
    record_hash: Mapped[str] = mapped_column(String(64))


class ResolutionContract(Base):
    """Immutable interpretation of one Polymarket event's settlement rules."""

    __tablename__ = "resolution_contracts"
    __table_args__ = (
        UniqueConstraint("event_id", "rules_hash", name="uq_resolution_contract_event_rules"),
        CheckConstraint("statistic IN ('high', 'low')", name="ck_resolution_statistic"),
        CheckConstraint("source_unit IN ('C', 'F')", name="ck_resolution_source_unit"),
        CheckConstraint("decimal_places BETWEEN 0 AND 3", name="ck_resolution_decimal_places"),
        Index("ix_resolution_contract_active", "event_id", "retired_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128))
    rules_hash: Mapped[str] = mapped_column(String(64))
    source_family: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    station_code: Mapped[str] = mapped_column(String(16))
    local_timezone: Mapped[str] = mapped_column(String(64))
    statistic: Mapped[str] = mapped_column(String(8))
    source_unit: Mapped[str] = mapped_column(String(4))
    decimal_places: Mapped[int] = mapped_column(SmallInteger)
    finalization_rule: Mapped[str] = mapped_column(String(64))
    license_status: Mapped[str] = mapped_column(String(32))
    storage_permitted: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    license_url: Mapped[str | None] = mapped_column(Text)
    rules_raw: Mapped[str] = mapped_column(Text)
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OfficialResolutionEvidenceRuntime(Base):
    """Singleton boundary and protocol activation for official source evidence."""

    __tablename__ = "official_resolution_evidence_runtime"
    __table_args__ = (
        CheckConstraint(
            "id = 1",
            name="ck_official_resolution_evidence_runtime_singleton",
        ),
        CheckConstraint(
            "definition_version = 'official-resolution-evidence-v1' "
            "AND license_protocol = 'official-resolution-license-v1' "
            "AND response_protocol = 'official-resolution-source-v1' "
            "AND extraction_protocol = 'official-resolution-extraction-v1'",
            name="ck_official_resolution_evidence_runtime_protocol",
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_evidence_runtime_hash",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(48))
    definition_hash: Mapped[str] = mapped_column(String(64))
    license_protocol: Mapped[str] = mapped_column(String(48))
    response_protocol: Mapped[str] = mapped_column(String(48))
    extraction_protocol: Mapped[str] = mapped_column(String(48))


class OfficialResolutionLicenseAttestation(Base):
    """Immutable, contract-scoped licence and adapter configuration review."""

    __tablename__ = "official_resolution_license_attestations"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "attestation_version",
            name="uq_official_resolution_license_contract_version",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_license_record_hash",
        ),
        CheckConstraint(
            "attestation_protocol = 'official-resolution-license-v1'",
            name="ck_official_resolution_license_protocol",
        ),
        CheckConstraint(
            "license_status = 'approved' AND commercial_use_permitted "
            "AND value_retention_permitted AND raw_response_retention_permitted",
            name="ck_official_resolution_license_authority",
        ),
        CheckConstraint(
            "settlement_source_url ~ '^https://' AND endpoint_url ~ '^https://' "
            "AND license_url ~ '^https://'",
            name="ck_official_resolution_license_urls",
        ),
        CheckConstraint(
            "expires_at > valid_from",
            name="ck_official_resolution_license_validity",
        ),
        CheckConstraint(
            "jsonb_typeof(parser_config) = 'object'",
            name="ck_official_resolution_license_parser_config",
        ),
        CheckConstraint(
            "max_response_age_seconds BETWEEN 1 AND 86400",
            name="ck_official_resolution_license_response_age",
        ),
        CheckConstraint(
            "terms_sha256 ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND policy_hash ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_license_hashes",
        ),
        CheckConstraint(
            "length(btrim(attestation_version)) > 0 "
            "AND length(btrim(grantee)) > 0 "
            "AND length(btrim(source_family)) > 0 "
            "AND length(btrim(reviewer)) > 0 "
            "AND length(btrim(adapter_name)) > 0 "
            "AND length(btrim(adapter_version)) > 0 "
            "AND length(btrim(rationale)) > 0",
            name="ck_official_resolution_license_required_text",
        ),
        Index(
            "ix_official_resolution_license_contract_time",
            "contract_id",
            "attested_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("resolution_contracts.id", ondelete="RESTRICT")
    )
    attestation_version: Mapped[str] = mapped_column(String(64))
    attestation_protocol: Mapped[str] = mapped_column(String(48))
    grantee: Mapped[str] = mapped_column(Text)
    source_family: Mapped[str] = mapped_column(String(32))
    settlement_source_url: Mapped[str] = mapped_column(Text)
    endpoint_url: Mapped[str] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32))
    commercial_use_permitted: Mapped[bool] = mapped_column(Boolean)
    value_retention_permitted: Mapped[bool] = mapped_column(Boolean)
    raw_response_retention_permitted: Mapped[bool] = mapped_column(Boolean)
    license_url: Mapped[str] = mapped_column(Text)
    terms_sha256: Mapped[str] = mapped_column(String(64))
    reviewer: Mapped[str] = mapped_column(String(128))
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    adapter_name: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    parser_config: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    parser_config_hash: Mapped[str] = mapped_column(String(64))
    max_response_age_seconds: Mapped[int] = mapped_column(Integer)
    policy_hash: Mapped[str] = mapped_column(String(64))
    rationale: Mapped[str] = mapped_column(Text)
    attested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class OfficialResolutionSourceResponse(Base):
    """Raw immutable bytes collected under one approved source attestation."""

    __tablename__ = "official_resolution_source_responses"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            "request_sha256",
            name="uq_official_resolution_response_contract_request",
        ),
        UniqueConstraint(
            "contract_id",
            "response_sha256",
            name="uq_official_resolution_response_contract_body",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_response_record_hash",
        ),
        CheckConstraint(
            "response_protocol = 'official-resolution-source-v1'",
            name="ck_official_resolution_response_protocol",
        ),
        CheckConstraint(
            "(acquisition_protocol = 'licensed-http-v1' "
            "AND request_method = 'GET' AND http_status = 200) OR "
            "(acquisition_protocol = 'authorized-artifact-v1' "
            "AND request_method = 'IMPORT' AND http_status IS NULL)",
            name="ck_official_resolution_response_acquisition",
        ),
        CheckConstraint(
            "jsonb_typeof(request_manifest) = 'object' "
            "AND jsonb_typeof(response_headers) = 'object'",
            name="ck_official_resolution_response_metadata",
        ),
        CheckConstraint(
            "response_bytes > 0",
            name="ck_official_resolution_response_bytes",
        ),
        CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND license_policy_hash ~ '^[0-9a-f]{64}$' "
            "AND response_sha256 ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_response_hashes",
        ),
        Index(
            "ix_official_resolution_response_contract_seen",
            "contract_id",
            "first_seen_at",
        ),
        Index(
            "ix_official_resolution_response_license",
            "license_attestation_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    license_attestation_id: Mapped[int] = mapped_column(
        ForeignKey(
            "official_resolution_license_attestations.id",
            ondelete="RESTRICT",
        )
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("resolution_contracts.id", ondelete="RESTRICT")
    )
    response_protocol: Mapped[str] = mapped_column(String(48))
    acquisition_protocol: Mapped[str] = mapped_column(String(48))
    source_family: Mapped[str] = mapped_column(String(32))
    settlement_source_url: Mapped[str] = mapped_column(Text)
    endpoint_url: Mapped[str] = mapped_column(Text)
    adapter_name: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    parser_config_hash: Mapped[str] = mapped_column(String(64))
    license_policy_hash: Mapped[str] = mapped_column(String(64))
    request_method: Mapped[str] = mapped_column(String(8))
    request_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    request_sha256: Mapped[str] = mapped_column(String(64))
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    http_status: Mapped[int | None] = mapped_column(SmallInteger)
    http_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_modified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(128))
    response_headers: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    response_body: Mapped[bytes] = mapped_column(LargeBinary)
    response_sha256: Mapped[str] = mapped_column(String(64))
    response_bytes: Mapped[int] = mapped_column(BigInteger)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class OfficialResolutionExactActual(Base):
    """Final exact value extracted from one immutable licensed response."""

    __tablename__ = "official_resolution_exact_actuals"
    __table_args__ = (
        UniqueConstraint(
            "contract_id",
            name="uq_official_resolution_exact_actual_contract",
        ),
        UniqueConstraint(
            "response_id",
            name="uq_official_resolution_exact_actual_response",
        ),
        UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_exact_actual_record_hash",
        ),
        CheckConstraint(
            "extraction_protocol = 'official-resolution-extraction-v1'",
            name="ck_official_resolution_exact_actual_protocol",
        ),
        CheckConstraint(
            "source_unit IN ('C', 'F') AND decimal_places BETWEEN 0 AND 3 "
            "AND is_final",
            name="ck_official_resolution_exact_actual_value",
        ),
        CheckConstraint(
            "jsonb_typeof(extraction_manifest) = 'object' "
            "AND length(btrim(extraction_locator)) > 0",
            name="ck_official_resolution_exact_actual_extraction",
        ),
        CheckConstraint(
            "rules_hash ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND extraction_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND source_response_sha256 ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_exact_actual_hashes",
        ),
        Index(
            "ix_official_resolution_exact_station_date",
            "station_id",
            "market_date",
            "kind",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    response_id: Mapped[int] = mapped_column(
        ForeignKey("official_resolution_source_responses.id", ondelete="RESTRICT")
    )
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("resolution_contracts.id", ondelete="RESTRICT")
    )
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id", ondelete="RESTRICT"))
    event_id: Mapped[str] = mapped_column(String(128))
    rules_hash: Mapped[str] = mapped_column(String(64))
    source_family: Mapped[str] = mapped_column(String(32))
    settlement_source_url: Mapped[str] = mapped_column(Text)
    endpoint_url: Mapped[str] = mapped_column(Text)
    station_code: Mapped[str] = mapped_column(String(16))
    market_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    source_value: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    source_unit: Mapped[str] = mapped_column(String(4))
    value_c: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    decimal_places: Mapped[int] = mapped_column(SmallInteger)
    finalization_rule: Mapped[str] = mapped_column(String(64))
    is_final: Mapped[bool] = mapped_column(Boolean)
    source_published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    adapter_name: Mapped[str] = mapped_column(String(64))
    adapter_version: Mapped[str] = mapped_column(String(64))
    parser_config_hash: Mapped[str] = mapped_column(String(64))
    extraction_locator: Mapped[str] = mapped_column(Text)
    extraction_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    extraction_manifest_hash: Mapped[str] = mapped_column(String(64))
    source_response_sha256: Mapped[str] = mapped_column(String(64))
    extraction_protocol: Mapped[str] = mapped_column(String(48))
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class SettlementActual(Base):
    """Exact finalized value from the event's licensed resolution source."""

    __tablename__ = "settlement_actuals"
    __table_args__ = (
        UniqueConstraint("contract_id", name="uq_settlement_actual_contract"),
        UniqueConstraint(
            "official_exact_actual_id",
            name="uq_settlement_actual_official_exact",
        ),
        CheckConstraint("source_unit IN ('C', 'F')", name="ck_settlement_actual_unit"),
        Index("ix_settlement_actual_station_date", "station_id", "date", "kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    contract_id: Mapped[int] = mapped_column(ForeignKey("resolution_contracts.id"))
    official_exact_actual_id: Mapped[int | None] = mapped_column(
        ForeignKey("official_resolution_exact_actuals.id", ondelete="RESTRICT")
    )
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    source_value: Mapped[Decimal] = mapped_column(Numeric(8, 3))
    source_unit: Mapped[str] = mapped_column(String(4))
    value_c: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    is_final: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    source_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    source_response_hash: Mapped[str | None] = mapped_column(String(64))
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )


class ResolutionReconciliation(Base):
    """Durable comparison of venue outcome, METAR truth, and exact source truth."""

    __tablename__ = "resolution_reconciliations"
    __table_args__ = (
        UniqueConstraint("market_id", name="uq_resolution_reconciliations_market"),
        CheckConstraint(
            "rules_hash ~ '^[0-9a-f]{64}$' AND "
            "resolution_vector_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_required_hashes",
        ),
        CheckConstraint(
            "metar_value_hash IS NULL OR metar_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_metar_hash",
        ),
        CheckConstraint(
            "exact_value_hash IS NULL OR exact_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_exact_value_hash",
        ),
        CheckConstraint(
            "exact_source_hash IS NULL OR exact_source_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_exact_source_hash",
        ),
        CheckConstraint(
            "boundary_margin_c IS NULL OR boundary_margin_c >= 0",
            name="ck_resolution_reconciliations_boundary_margin",
        ),
        CheckConstraint(
            "exact_truth_gap_c IS NULL OR exact_truth_gap_c >= 0",
            name="ck_resolution_reconciliations_truth_gap",
        ),
        Index(
            "ix_resolution_reconciliations_classification_checked",
            "classification",
            "checked_at",
        ),
        Index("ix_resolution_reconciliations_event", "event_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(
        ForeignKey("markets.id", ondelete="RESTRICT")
    )
    event_id: Mapped[str] = mapped_column(String(128))
    station_id: Mapped[int | None] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    market_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    resolution_event_id: Mapped[int] = mapped_column(
        ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT")
    )
    settlement_actual_id: Mapped[int | None] = mapped_column(
        ForeignKey("settlement_actuals.id", ondelete="RESTRICT")
    )
    classification: Mapped[ResolutionReconciliationClassification] = mapped_column(
        db_enum(
            ResolutionReconciliationClassification,
            "resolution_reconciliation_classification",
        )
    )
    resolved_yes: Mapped[bool] = mapped_column(Boolean)
    metar_temp_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    metar_implied_yes: Mapped[bool | None] = mapped_column(Boolean)
    exact_temp_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    exact_implied_yes: Mapped[bool | None] = mapped_column(Boolean)
    boundary_margin_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    exact_truth_gap_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    rules_hash: Mapped[str] = mapped_column(String(64))
    resolution_vector_hash: Mapped[str] = mapped_column(String(64))
    metar_value_hash: Mapped[str | None] = mapped_column(String(64))
    exact_value_hash: Mapped[str | None] = mapped_column(String(64))
    exact_source_hash: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class StationReliabilitySnapshot(Base):
    """Append-only station selection evidence evaluated under one policy."""

    __tablename__ = "station_reliability_snapshots"
    __table_args__ = (
        CheckConstraint(
            "window_days > 0 AND comparable_units >= 0 AND match_units >= 0 "
            "AND conflict_units >= 0 AND boundary_units >= 0 "
            "AND unavailable_units >= 0 AND exact_source_mismatch_units >= 0 "
            "AND clear_mismatch_units >= 0 AND exact_truth_gap_units >= 0",
            name="ck_station_reliability_nonnegative_counts",
        ),
        CheckConstraint(
            "comparable_units = match_units + conflict_units",
            name="ck_station_reliability_comparable_sum",
        ),
        CheckConstraint(
            "conflict_units = exact_source_mismatch_units + clear_mismatch_units "
            "+ exact_truth_gap_units",
            name="ck_station_reliability_conflict_sum",
        ),
        CheckConstraint(
            "reliability_score IS NULL OR "
            "(reliability_score >= 0 AND reliability_score <= 1)",
            name="ck_station_reliability_score_range",
        ),
        CheckConstraint(
            "reliability_lower_bound IS NULL OR "
            "(reliability_lower_bound >= 0 AND reliability_lower_bound <= 1)",
            name="ck_station_reliability_lower_range",
        ),
        CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$' AND "
            "source_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_station_reliability_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(reasons) = 'array'",
            name="ck_station_reliability_reasons_array",
        ),
        CheckConstraint(
            "(conflict_units = 0 AND latest_conflict_at IS NULL "
            "AND latest_conflict_date IS NULL AND latest_conflict_kind IS NULL "
            "AND resolution_cooldown_until IS NULL) OR "
            "(conflict_units > 0 AND latest_conflict_at IS NOT NULL "
            "AND latest_conflict_date IS NOT NULL "
            "AND latest_conflict_kind IS NOT NULL "
            "AND resolution_cooldown_until IS NOT NULL)",
            name="ck_station_reliability_conflict_metadata",
        ),
        UniqueConstraint(
            "station_id",
            "evaluated_at",
            name="uq_station_reliability_station_evaluated",
        ),
        Index(
            "ix_station_reliability_station_evaluated",
            "station_id",
            "evaluated_at",
        ),
        Index(
            "ix_station_reliability_status_evaluated",
            "status",
            "evaluated_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_days: Mapped[int] = mapped_column(Integer)
    comparable_units: Mapped[int] = mapped_column(Integer)
    match_units: Mapped[int] = mapped_column(Integer)
    conflict_units: Mapped[int] = mapped_column(Integer)
    exact_source_mismatch_units: Mapped[int] = mapped_column(Integer)
    clear_mismatch_units: Mapped[int] = mapped_column(Integer)
    exact_truth_gap_units: Mapped[int] = mapped_column(Integer)
    boundary_units: Mapped[int] = mapped_column(Integer)
    unavailable_units: Mapped[int] = mapped_column(Integer)
    reliability_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    reliability_lower_bound: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    status: Mapped[StationReliabilityStatus] = mapped_column(
        db_enum(StationReliabilityStatus, "station_reliability_status")
    )
    latest_conflict_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latest_conflict_date: Mapped[date | None] = mapped_column(Date)
    latest_conflict_kind: Mapped[WeatherKind | None] = mapped_column(
        db_enum(WeatherKind, "weather_kind")
    )
    resolution_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    latest_observation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    observation_local_date: Mapped[date] = mapped_column(Date)
    observation_cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reasons: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'[]'::jsonb")
    )
    policy_hash: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class SettlementCase(Base):
    """Current durable lifecycle projection for one venue settlement event."""

    __tablename__ = "settlement_cases"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_settlement_cases_event"),
        CheckConstraint("attempt_count >= 1", name="ck_settlement_cases_attempts"),
        CheckConstraint("transition_count >= 1", name="ck_settlement_cases_transitions"),
        CheckConstraint(
            "source_response_sha256 IS NULL OR "
            "source_response_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_source_hash",
        ),
        CheckConstraint(
            "resolution_vector_hash IS NULL OR "
            "resolution_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_vector_hash",
        ),
        CheckConstraint(
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_record_hash",
        ),
        CheckConstraint(
            "(classification = 'resolved'::settlement_case_classification AND "
            "resolved_at IS NOT NULL AND resolution_event_id IS NOT NULL AND "
            "resolution_vector_hash IS NOT NULL) OR "
            "(classification <> 'resolved'::settlement_case_classification AND "
            "resolved_at IS NULL AND resolution_event_id IS NULL AND "
            "resolution_vector_hash IS NULL)",
            name="ck_settlement_cases_resolved_evidence",
        ),
        Index(
            "ix_settlement_cases_classification_checked",
            "classification",
            "last_checked_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(128))
    classification: Mapped[SettlementCaseClassification] = mapped_column(
        db_enum(SettlementCaseClassification, "settlement_case_classification")
    )
    previous_classification: Mapped[SettlementCaseClassification | None] = mapped_column(
        db_enum(SettlementCaseClassification, "settlement_case_classification")
    )
    reason_code: Mapped[str] = mapped_column(String(64))
    event_resolves_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    classification_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer)
    transition_count: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(Text)
    source_response_sha256: Mapped[str | None] = mapped_column(String(64))
    resolution_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT")
    )
    resolution_vector_hash: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    record_hash: Mapped[str] = mapped_column(String(64))


class EvidenceVersionHistory(Base):
    """Immutable superseded versions of current evidence projections."""

    __tablename__ = "evidence_version_history"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "version",
            name="uq_evidence_version_history_entity_version",
        ),
        CheckConstraint(
            "entity_type IN ('resolution_reconciliation', "
            "'settlement_case', 'settlement_actual')",
            name="ck_evidence_version_history_entity_type",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_evidence_version_history_version",
        ),
        CheckConstraint(
            "snapshot_sha256 ~ '^[0-9a-f]{64}$' AND "
            "(source_record_hash IS NULL OR "
            "source_record_hash ~ '^[0-9a-f]{64}$')",
            name="ck_evidence_version_history_hashes",
        ),
        Index(
            "ix_evidence_version_history_entity",
            "entity_type",
            "entity_id",
            "version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer)
    superseded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    snapshot_sha256: Mapped[str] = mapped_column(String(64))
    source_record_hash: Mapped[str | None] = mapped_column(String(64))


class ForecastModelRegistry(Base):
    __tablename__ = "forecast_model_registry"
    __table_args__ = (
        UniqueConstraint("provider", "model", "model_version", name="uq_forecast_model_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(64))
    region: Mapped[str] = mapped_column(String(64))
    resolution_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    run_frequency_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    expected_latency_minutes: Mapped[int | None] = mapped_column(Integer)
    source_url: Mapped[str] = mapped_column(Text)
    license_status: Mapped[str] = mapped_column(String(32))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    shadow_only: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    introduced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'{}'::jsonb"),
    )


class ForecastRun(Base):
    __tablename__ = "forecast_runs"
    __table_args__ = (
        UniqueConstraint("registry_id", "init_time", name="uq_forecast_run_identity"),
        CheckConstraint("available_at >= init_time", name="ck_forecast_run_available_after_init"),
        Index("ix_forecast_runs_available", "registry_id", "available_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    registry_id: Mapped[int] = mapped_column(ForeignKey("forecast_model_registry.id"))
    init_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    upstream_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSON().with_variant(JSONB(), "postgresql"),
        server_default=text("'{}'::jsonb"),
    )


class ForecastProvenanceRuntime(Base):
    __tablename__ = "forecast_provenance_runtime"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    definition_version: Mapped[str] = mapped_column(String(32))
    definition_hash: Mapped[str] = mapped_column(String(64))


class ForecastIngest(Base):
    __tablename__ = "forecast_ingests"
    __table_args__ = (
        Index("ix_forecast_ingests_station_seen", "station_id", "first_seen_at"),
        Index("ix_forecast_ingests_run", "run_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("forecast_runs.id", ondelete="RESTRICT")
    )
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    request_model: Mapped[str] = mapped_column(String(64))
    collector: Mapped[str] = mapped_column(String(64))
    collection_protocol: Mapped[str] = mapped_column(String(32))
    pipeline_fingerprint: Mapped[str] = mapped_column(String(128))
    upstream_init_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    upstream_init_verified: Mapped[bool] = mapped_column(Boolean)
    availability_semantics: Mapped[str] = mapped_column(String(96))
    normalized_manifest: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql")
    )
    normalized_manifest_sha256: Mapped[str] = mapped_column(String(64))
    expected_response_count: Mapped[int] = mapped_column(SmallInteger)
    response_set_sha256: Mapped[str] = mapped_column(String(64))
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class ForecastSourceResponse(Base):
    __tablename__ = "forecast_source_responses"
    __table_args__ = (
        UniqueConstraint(
            "ingest_id",
            "sequence_no",
            name="uq_forecast_source_responses_ingest_sequence",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    ingest_id: Mapped[int] = mapped_column(
        ForeignKey("forecast_ingests.id", ondelete="RESTRICT")
    )
    sequence_no: Mapped[int] = mapped_column(SmallInteger)
    request_method: Mapped[str] = mapped_column(String(8))
    request_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_url: Mapped[str] = mapped_column(Text)
    request_sha256: Mapped[str] = mapped_column(String(64))
    http_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(Text)
    response_body: Mapped[bytes] = mapped_column(LargeBinary)
    response_sha256: Mapped[str] = mapped_column(String(64))
    response_bytes: Mapped[int] = mapped_column(BigInteger)
    evidence_hash: Mapped[str] = mapped_column(String(64))
    record_hash: Mapped[str] = mapped_column(String(64))


class ForecastEvaluation(Base):
    __tablename__ = "forecast_evaluations"
    __table_args__ = (
        UniqueConstraint("model_prob_id", name="uq_forecast_evaluation_model_prob"),
        Index("ix_forecast_evaluations_sample", "independent_sample_key", "decision_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    model_prob_id: Mapped[int] = mapped_column(ForeignKey("model_probs.id"))
    decision_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    independent_sample_key: Mapped[str] = mapped_column(String(128))
    model_probability: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    horizon_market_probability: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    market_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executable_bid: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    executable_ask: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    pre_resolution_close: Mapped[Decimal | None] = mapped_column(Numeric(8, 6))
    close_snapshot_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_probability_total: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    event_overround: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    resolved_outcome: Mapped[bool] = mapped_column(Boolean)
    resolution_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("gamma_outcome_events.id")
    )
    resolution_vector_hash: Mapped[str | None] = mapped_column(String(64))
    model_brier: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    market_brier: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
    market_benchmark_protocol: Mapped[str] = mapped_column(
        String(32), server_default="legacy-synthetic-yes-v0"
    )
    market_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    market_snapshot_hash: Mapped[str | None] = mapped_column(String(64))
    close_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT")
    )
    close_snapshot_hash: Mapped[str | None] = mapped_column(String(64))


class AccuracyPrediction(Base):
    __tablename__ = "accuracy_predictions"
    __table_args__ = (
        UniqueConstraint(
            "market_id", "ts", "expert", "expert_version", name="uq_accuracy_prediction"
        ),
        CheckConstraint(
            """
            NOT evidence_eligible OR (
                point_in_time_valid
                AND training_cutoff IS NOT NULL
                AND baseline_model_prob_id IS NOT NULL
                AND length(input_hash) = 64
                AND length(experiment_fingerprint) = 64
                AND length(record_hash) = 64
                AND ts <= generated_at
                AND training_cutoff <= generated_at
            )
            """,
            name="ck_accuracy_predictions_sealed_provenance",
        ),
        Index("ix_accuracy_predictions_market_ts", "market_id", "ts"),
        Index(
            "ix_accuracy_predictions_evidence",
            "expert",
            "expert_version",
            "evidence_eligible",
            "generated_at",
        ),
        Index(
            "ix_accuracy_predictions_eligible_expert_ts",
            "expert",
            "ts",
            postgresql_where=text("evidence_eligible IS TRUE"),
        ),
        Index(
            "ix_accuracy_predictions_eligible_expert_version_market_ts",
            "expert",
            "expert_version",
            "market_id",
            "ts",
            postgresql_where=text("evidence_eligible IS TRUE"),
        ),
        Index("ix_accuracy_predictions_baseline", "baseline_model_prob_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    market_id: Mapped[int] = mapped_column(ForeignKey("markets.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expert: Mapped[str] = mapped_column(String(64))
    expert_version: Mapped[str] = mapped_column(String(64))
    probability: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    mean_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    sigma_c: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    training_sample_n: Mapped[int] = mapped_column(Integer)
    point_in_time_valid: Mapped[bool] = mapped_column(Boolean)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    training_cutoff: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_model_prob_id: Mapped[int | None] = mapped_column(ForeignKey("model_probs.id"))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    experiment_fingerprint: Mapped[str | None] = mapped_column(String(128))
    record_hash: Mapped[str | None] = mapped_column(String(64))
    evidence_eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    distribution: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )


class AccuracyPromotionTrial(Base):
    """Immutable definition and derived lifecycle of one confirmatory trial."""

    __tablename__ = "accuracy_promotion_trials"
    __table_args__ = (
        CheckConstraint(
            "protocol_version = 'fixed-range-brier-v3' AND "
            "registration_epoch = 'fixed-range-brier-v3-20260714' AND "
            "family_id = registration_epoch",
            name="ck_accuracy_promotion_trials_frozen_epoch",
        ),
        CheckConstraint(
            "candidate_id <> baseline_id AND starts_at >= registered_at",
            name="ck_accuracy_promotion_trials_definition_order",
        ),
        CheckConstraint(
            "target_blocks = 60 AND "
            "test_method = 'predictable-range-hoeffding-v1'",
            name="ck_accuracy_promotion_trials_fixed_look",
        ),
        CheckConstraint(
            "family_size BETWEEN 1 AND 5000 AND "
            "family_member_index BETWEEN 1 AND family_size",
            name="ck_accuracy_promotion_trials_family_bounds",
        ),
        CheckConstraint(
            "family_alpha = 0.050000000000000000 AND allocated_alpha > 0 AND "
            "allocated_alpha * family_size <= family_alpha AND "
            "family_alpha - (allocated_alpha * family_size) "
            "< 0.000000000001000000",
            name="ck_accuracy_promotion_trials_bonferroni",
        ),
        CheckConstraint(
            "minimum_brier_improvement BETWEEN 0 AND 1",
            name="ck_accuracy_promotion_trials_minimum_improvement",
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "experiment_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_trials_hashes",
        ),
        UniqueConstraint(
            "experiment_id", name="uq_accuracy_promotion_trials_experiment"
        ),
        UniqueConstraint(
            "definition_hash", name="uq_accuracy_promotion_trials_definition"
        ),
        UniqueConstraint(
            "registration_epoch",
            "family_member_index",
            name="uq_accuracy_promotion_trials_family_member",
        ),
        Index(
            "ix_accuracy_promotion_trials_identity",
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
        ),
        Index("ix_accuracy_promotion_trials_status", "status", "created_at"),
        Index(
            "uq_accuracy_promotion_trials_effective_activation",
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
            unique=True,
            postgresql_where=text(
                "status = 'activated'::accuracy_promotion_trial_status"
            ),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    protocol_version: Mapped[str] = mapped_column(String(64))
    registration_epoch: Mapped[str] = mapped_column(String(64))
    family_id: Mapped[str] = mapped_column(String(160))
    experiment_id: Mapped[str] = mapped_column(String(160))
    candidate_id: Mapped[str] = mapped_column(String(160))
    baseline_id: Mapped[str] = mapped_column(String(160))
    pipeline_cohort: Mapped[str] = mapped_column(String(128))
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_blocks: Mapped[int] = mapped_column(SmallInteger)
    allocated_alpha: Mapped[Decimal] = mapped_column(Numeric(20, 18))
    minimum_brier_improvement: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    test_method: Mapped[str] = mapped_column(String(64))
    definition_hash: Mapped[str] = mapped_column(String(64))
    expert: Mapped[str] = mapped_column(String(64))
    legacy_expert_version: Mapped[str] = mapped_column(String(64))
    expert_version: Mapped[str] = mapped_column(String(64))
    experiment_fingerprint: Mapped[str] = mapped_column(String(64))
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    horizon_bucket: Mapped[str] = mapped_column(String(16))
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    scope_key: Mapped[str] = mapped_column(String(160))
    family_member_index: Mapped[int] = mapped_column(Integer)
    family_size: Mapped[int] = mapped_column(Integer)
    family_alpha: Mapped[Decimal] = mapped_column(Numeric(20, 18))
    status: Mapped[AccuracyPromotionTrialStatus] = mapped_column(
        db_enum(AccuracyPromotionTrialStatus, "accuracy_promotion_trial_status"),
        server_default="collecting",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class AccuracyPromotionEvidenceMember(Base):
    """One complete, immutable event-vector score for an independent block."""

    __tablename__ = "accuracy_promotion_evidence_members"
    __table_args__ = (
        CheckConstraint(
            "cardinality(accuracy_prediction_ids) >= 2 AND "
            "cardinality(accuracy_prediction_ids) = "
            "cardinality(baseline_model_prob_ids) AND "
            "cardinality(accuracy_prediction_ids) = cardinality(market_ids)",
            name="ck_accuracy_promotion_members_vector_size",
        ),
        CheckConstraint(
            "candidate_brier BETWEEN 0 AND 1 AND baseline_brier BETWEEN 0 AND 1 "
            "AND delta_brier BETWEEN -1 AND 1 AND "
            "predictable_improvement_lower BETWEEN -1 AND 1 AND "
            "predictable_improvement_upper BETWEEN -1 AND 1 AND "
            "predictable_improvement_lower <= predictable_improvement_upper AND "
            "-delta_brier BETWEEN predictable_improvement_lower "
            "AND predictable_improvement_upper AND predictable_range_squared >= 0",
            name="ck_accuracy_promotion_members_brier",
        ),
        CheckConstraint(
            "resolution_vector_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_members_hashes",
        ),
        UniqueConstraint(
            "trial_id",
            "independent_block_key",
            name="uq_accuracy_promotion_members_trial_block",
        ),
        UniqueConstraint(
            "trial_id",
            "resolution_event_id",
            name="uq_accuracy_promotion_members_trial_event",
        ),
        Index(
            "ix_accuracy_promotion_members_trial_cutoff",
            "trial_id",
            "resolved_cutoff",
            "independent_block_key",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trial_id: Mapped[int] = mapped_column(
        ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT")
    )
    accuracy_prediction_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    baseline_model_prob_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    market_ids: Mapped[list[int]] = mapped_column(ARRAY(BigInteger))
    resolution_event_id: Mapped[int] = mapped_column(
        ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT")
    )
    event_id: Mapped[str] = mapped_column(String(128))
    prediction_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    independent_block_key: Mapped[str] = mapped_column(String(160))
    resolved_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    candidate_brier: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    baseline_brier: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    delta_brier: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    predictable_improvement_lower: Mapped[Decimal] = mapped_column(Numeric(30, 24))
    predictable_improvement_upper: Mapped[Decimal] = mapped_column(Numeric(30, 24))
    predictable_range_squared: Mapped[Decimal] = mapped_column(Numeric(40, 30))
    resolution_vector_hash: Mapped[str] = mapped_column(String(64))
    evidence_hash: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class AccuracyPromotionLook(Base):
    """The sole fixed statistical look for a promotion trial."""

    __tablename__ = "accuracy_promotion_looks"
    __table_args__ = (
        CheckConstraint(
            "look_number = 1 AND independent_block_count = 60 AND "
            "evidence_member_count = 60 AND complete AND "
            "wins + nonwins = 60 AND nonwins = losses + ties",
            name="ck_accuracy_promotion_looks_fixed_complete",
        ),
        CheckConstraint(
            "wins >= 0 AND losses >= 0 AND ties >= 0 AND "
            "sum_predictable_range_squared >= 0 AND confidence_radius >= 0 AND "
            "mean_improvement_lcb BETWEEN -2 AND 1 AND "
            "one_sided_p_bound BETWEEN 0 AND 1",
            name="ck_accuracy_promotion_looks_range_bound",
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_set_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_looks_hashes",
        ),
        UniqueConstraint("trial_id", name="uq_accuracy_promotion_looks_trial"),
        UniqueConstraint(
            "decision_hash", name="uq_accuracy_promotion_looks_decision"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trial_id: Mapped[int] = mapped_column(
        ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT")
    )
    definition_hash: Mapped[str] = mapped_column(String(64))
    decision_hash: Mapped[str] = mapped_column(String(64))
    look_number: Mapped[int] = mapped_column(SmallInteger)
    independent_block_count: Mapped[int] = mapped_column(SmallInteger)
    evidence_member_count: Mapped[int] = mapped_column(SmallInteger)
    mean_delta_brier: Mapped[Decimal] = mapped_column(Numeric(12, 10))
    wins: Mapped[int] = mapped_column(SmallInteger)
    losses: Mapped[int] = mapped_column(SmallInteger)
    ties: Mapped[int] = mapped_column(SmallInteger)
    nonwins: Mapped[int] = mapped_column(SmallInteger)
    sum_predictable_range_squared: Mapped[Decimal] = mapped_column(Numeric(40, 30))
    confidence_radius: Mapped[Decimal] = mapped_column(Numeric(30, 24))
    mean_improvement_lcb: Mapped[Decimal] = mapped_column(Numeric(30, 24))
    one_sided_p_bound: Mapped[Decimal] = mapped_column(Numeric(30, 24))
    complete: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[AccuracyPromotionLookStatus] = mapped_column(
        db_enum(AccuracyPromotionLookStatus, "accuracy_promotion_look_status")
    )
    evidence_set_hash: Mapped[str] = mapped_column(String(64))
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("clock_timestamp()")
    )
    record_hash: Mapped[str] = mapped_column(String(64))


class AccuracyPromotionActivation(Base):
    """Immutable effective activation bound to a passing fixed look."""

    __tablename__ = "accuracy_promotion_activations"
    __table_args__ = (
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND "
            "experiment_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_activations_hashes",
        ),
        CheckConstraint(
            "length(btrim(activation_id)) > 0 AND "
            "length(btrim(activated_by)) > 0",
            name="ck_accuracy_promotion_activations_identity",
        ),
        UniqueConstraint(
            "activation_id", name="uq_accuracy_promotion_activations_id"
        ),
        UniqueConstraint("trial_id", name="uq_accuracy_promotion_activations_trial"),
        UniqueConstraint("look_id", name="uq_accuracy_promotion_activations_look"),
        UniqueConstraint(
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
            name="uq_accuracy_promotion_activations_effective_scope",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    activation_id: Mapped[str] = mapped_column(String(160))
    trial_id: Mapped[int] = mapped_column(
        ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT")
    )
    look_id: Mapped[int] = mapped_column(
        ForeignKey("accuracy_promotion_looks.id", ondelete="RESTRICT")
    )
    definition_hash: Mapped[str] = mapped_column(String(64))
    decision_hash: Mapped[str] = mapped_column(String(64))
    family_id: Mapped[str] = mapped_column(String(160))
    experiment_id: Mapped[str] = mapped_column(String(160))
    candidate_id: Mapped[str] = mapped_column(String(160))
    baseline_id: Mapped[str] = mapped_column(String(160))
    pipeline_cohort: Mapped[str] = mapped_column(String(128))
    expert: Mapped[str] = mapped_column(String(64))
    legacy_expert_version: Mapped[str] = mapped_column(String(64))
    expert_version: Mapped[str] = mapped_column(String(64))
    experiment_fingerprint: Mapped[str] = mapped_column(String(64))
    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="RESTRICT")
    )
    horizon_bucket: Mapped[str] = mapped_column(String(16))
    kind: Mapped[WeatherKind] = mapped_column(db_enum(WeatherKind, "weather_kind"))
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    activated_by: Mapped[str] = mapped_column(String(128))
    record_hash: Mapped[str] = mapped_column(String(64))


class AccuracyStackWeight(Base):
    __tablename__ = "accuracy_stack_weights"
    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "horizon_bucket",
            "kind",
            "expert",
            "valid_from",
            name="uq_accuracy_stack_weight_version",
        ),
        CheckConstraint("weight >= 0 AND weight <= 1", name="ck_accuracy_weight_range"),
        Index(
            "ix_accuracy_stack_weights_current",
            "station_id",
            "horizon_bucket",
            "kind",
            "valid_to",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    horizon_bucket: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    expert: Mapped[str] = mapped_column(String(64))
    weight: Mapped[Decimal] = mapped_column(Numeric(10, 8))
    sample_n: Mapped[int] = mapped_column(Integer)
    effective_sample_n: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    out_of_fold_brier: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SettlementProbabilityCalibrator(Base):
    __tablename__ = "settlement_probability_calibrators"
    __table_args__ = (
        CheckConstraint("alpha >= 0.05 AND alpha <= 5", name="ck_settlement_cal_alpha"),
        CheckConstraint("beta >= -2 AND beta <= 2", name="ck_settlement_cal_beta"),
        CheckConstraint("training_blocks >= 0", name="ck_settlement_cal_blocks"),
        Index("ix_settlement_calibrator_eligible", "eligible", "updated_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    scope_key: Mapped[str] = mapped_column(String(128), unique=True)
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"))
    horizon_bucket: Mapped[str | None] = mapped_column(String(16))
    kind: Mapped[str | None] = mapped_column(String(8))
    alpha: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    beta: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    training_blocks: Mapped[int] = mapped_column(Integer)
    training_buckets: Mapped[int] = mapped_column(Integer)
    oof_baseline_brier: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    oof_calibrated_brier: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    oof_baseline_ece: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    oof_calibrated_ece: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    delta_brier_ci_low: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    delta_brier_ci_high: Mapped[Decimal | None] = mapped_column(Numeric(12, 8))
    eligible: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    trained_through: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ModelSkillReport(Base):
    __tablename__ = "model_skill_reports"
    __table_args__ = (
        Index(
            "ix_model_skill_latest",
            "station_id",
            "horizon_bucket",
            "kind",
            "evaluated_at",
        ),
        Index(
            "ix_model_skill_window_evaluated",
            "window_days",
            "evaluated_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    report_key: Mapped[str] = mapped_column(String(160), unique=True)
    registry_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_model_registry.id"))
    station_id: Mapped[int] = mapped_column(ForeignKey("stations.id"))
    horizon_bucket: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    window_days: Mapped[int] = mapped_column(SmallInteger)
    sample_n: Mapped[int] = mapped_column(Integer)
    coverage: Mapped[Decimal] = mapped_column(Numeric(8, 6))
    p95_latency_minutes: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    brier: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    crps: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    delta_brier: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    delta_crps: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    ci_low: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    ci_high: Mapped[Decimal | None] = mapped_column(Numeric(10, 8))
    eligible: Mapped[bool] = mapped_column(Boolean)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )


class ModelDriftEvent(Base):
    __tablename__ = "model_drift_events"
    __table_args__ = (Index("ix_model_drift_detected", "detected_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    event_key: Mapped[str] = mapped_column(String(160), unique=True)
    registry_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_model_registry.id"))
    station_id: Mapped[int | None] = mapped_column(ForeignKey("stations.id"))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_days: Mapped[int] = mapped_column(SmallInteger)
    metric: Mapped[str] = mapped_column(String(32))
    baseline_value: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    recent_value: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    delta: Mapped[Decimal] = mapped_column(Numeric(12, 8))
    severity: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), server_default=text("'{}'::jsonb")
    )
