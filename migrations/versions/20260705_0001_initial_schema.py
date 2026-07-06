"""Create the STRATUS core schema and seed the initial stations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260705_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

weather_kind = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)
market_status = postgresql.ENUM(
    "active", "resolved", "blocked", name="market_status", create_type=False
)
trade_side = postgresql.ENUM("buy_yes", "buy_no", name="trade_side", create_type=False)
confidence = postgresql.ENUM("A", "B", "C", name="signal_confidence", create_type=False)
signal_status = postgresql.ENUM(
    "pending", "executed", "skipped", "expired", name="signal_status", create_type=False
)
execution_mode = postgresql.ENUM("paper", "live", name="execution_mode", create_type=False)
position_status = postgresql.ENUM(
    "open", "closed", name="position_status", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    for enum in (
        weather_kind,
        market_status,
        trade_side,
        confidence,
        signal_status,
        execution_mode,
        position_status,
    ):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "stations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("icao", sa.String(4), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("city", sa.String(80), nullable=False),
        sa.Column("lat", sa.Numeric(9, 6), nullable=False),
        sa.Column("lon", sa.Numeric(9, 6), nullable=False),
        sa.Column("tz", sa.String(64), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("icao", name="uq_stations_icao"),
    )
    stations = sa.table(
        "stations",
        sa.column("icao", sa.String()),
        sa.column("name", sa.String()),
        sa.column("city", sa.String()),
        sa.column("lat", sa.Numeric()),
        sa.column("lon", sa.Numeric()),
        sa.column("tz", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    op.bulk_insert(
        stations,
        [
            {
                "icao": "KLGA",
                "name": "LaGuardia Airport",
                "city": "New York City",
                "lat": 40.776900,
                "lon": -73.874000,
                "tz": "America/New_York",
                "active": True,
            },
            {
                "icao": "EGLL",
                "name": "London Heathrow Airport",
                "city": "London",
                "lat": 51.470000,
                "lon": -0.454300,
                "tz": "Europe/London",
                "active": True,
            },
            {
                "icao": "LFPG",
                "name": "Paris Charles de Gaulle Airport",
                "city": "Paris",
                "lat": 49.009700,
                "lon": 2.547900,
                "tz": "Europe/Paris",
                "active": True,
            },
        ],
    )

    op.create_table(
        "markets",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("polymarket_id", sa.String(128), nullable=False),
        sa.Column("event_id", sa.String(128), nullable=False),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=True),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("kind", weather_kind, nullable=False),
        sa.Column("bucket_low_c", sa.Numeric(6, 2), nullable=True),
        sa.Column("bucket_high_c", sa.Numeric(6, 2), nullable=True),
        sa.Column("resolution_source", sa.Text(), nullable=True),
        sa.Column("rules_raw", sa.Text(), nullable=False),
        sa.Column("station_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("status", market_status, server_default="blocked", nullable=False),
        sa.Column("resolved_outcome", sa.Boolean(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("resolves_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("polymarket_id", name="uq_markets_polymarket_id"),
    )
    op.create_index("ix_markets_active_resolution", "markets", ["status", "resolves_at"])

    op.create_table(
        "forecasts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("run_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_date", sa.Date(), nullable=False),
        sa.Column("kind", weather_kind, nullable=False),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "station_id",
            "model",
            "run_time",
            "valid_date",
            "kind",
            name="uq_forecasts_identity",
        ),
    )
    op.create_index("ix_forecasts_lookup", "forecasts", ["station_id", "valid_date", "kind"])

    op.create_table(
        "observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("obs_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("raw_metar", sa.Text(), nullable=False),
        sa.UniqueConstraint("station_id", "obs_time", name="uq_observations_station_time"),
    )

    op.create_table(
        "daily_actuals",
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), primary_key=True),
        sa.Column("date", sa.Date(), primary_key=True),
        sa.Column("kind", weather_kind, primary_key=True),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=False),
    )

    op.create_table(
        "market_prices",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("midpoint", sa.Numeric(8, 6), nullable=False),
        sa.Column("best_bid", sa.Numeric(8, 6), nullable=False),
        sa.Column("best_ask", sa.Numeric(8, 6), nullable=False),
        sa.Column("bid_depth_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("ask_depth_usd", sa.Numeric(14, 4), nullable=False),
        sa.UniqueConstraint("market_id", "ts", name="uq_market_prices_market_time"),
    )

    op.create_table(
        "model_probs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prob", sa.Numeric(8, 6), nullable=False),
        sa.Column("sigma", sa.Numeric(8, 4), nullable=False),
        sa.Column("horizon_h", sa.Numeric(8, 2), nullable=False),
        sa.Column("components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.UniqueConstraint("market_id", "ts", name="uq_model_probs_market_time"),
    )

    op.create_table(
        "calibration",
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), primary_key=True),
        sa.Column("model", sa.String(32), primary_key=True),
        sa.Column("horizon_bucket", sa.String(16), primary_key=True),
        sa.Column("kind", weather_kind, primary_key=True),
        sa.Column("bias_c", sa.Numeric(8, 4), nullable=False),
        sa.Column("sigma_c", sa.Numeric(8, 4), nullable=False),
        sa.Column("emos_a", sa.Numeric(12, 8), nullable=False),
        sa.Column("emos_b", sa.Numeric(12, 8), nullable=False),
        sa.Column("emos_c", sa.Numeric(12, 8), nullable=False),
        sa.Column("emos_d", sa.Numeric(12, 8), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("brier", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )

    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("side", trade_side, nullable=False),
        sa.Column("model_prob", sa.Numeric(8, 6), nullable=False),
        sa.Column("market_price", sa.Numeric(8, 6), nullable=False),
        sa.Column("edge", sa.Numeric(8, 6), nullable=False),
        sa.Column("kelly_frac", sa.Numeric(8, 6), nullable=False),
        sa.Column("size_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("confidence", confidence, nullable=False),
        sa.Column("status", signal_status, nullable=False),
        sa.Column("skip_reason", sa.Text(), nullable=True),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("signal_id", sa.BigInteger(), sa.ForeignKey("signals.id"), nullable=False),
        sa.Column("mode", execution_mode, nullable=False),
        sa.Column("clob_order_id", sa.String(128), nullable=True),
        sa.Column("side", trade_side, nullable=False),
        sa.Column("price", sa.Numeric(8, 6), nullable=False),
        sa.Column("size_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("filled_usd", sa.Numeric(14, 4), server_default="0", nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "positions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("mode", execution_mode, nullable=False),
        sa.Column("side", trade_side, nullable=False),
        sa.Column("avg_price", sa.Numeric(8, 6), nullable=False),
        sa.Column("size_shares", sa.Numeric(18, 6), nullable=False),
        sa.Column("size_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("status", position_status, nullable=False),
        sa.Column("pnl_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("clv", sa.Numeric(8, 6), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "bankroll_log",
        sa.Column("ts", sa.DateTime(timezone=True), primary_key=True),
        sa.Column("mode", execution_mode, primary_key=True),
        sa.Column("balance_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("event", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "bankroll_log",
        "positions",
        "orders",
        "signals",
        "calibration",
        "model_probs",
        "market_prices",
        "daily_actuals",
        "observations",
        "forecasts",
        "markets",
        "stations",
    ):
        op.drop_table(table)

    bind = op.get_bind()
    for enum in reversed(
        (
            weather_kind,
            market_status,
            trade_side,
            confidence,
            signal_status,
            execution_mode,
            position_status,
        )
    ):
        enum.drop(bind, checkfirst=True)

