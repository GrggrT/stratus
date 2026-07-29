"""Add venue isolation and the Binance market-data foundation.

Revision ID: 20260712_0017
Revises: 20260707_0016
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0017"
down_revision: str | None = "20260707_0016"
branch_labels = None
depends_on = None

trading_venue = postgresql.ENUM(
    "polymarket", "binance_spot", name="trading_venue", create_type=False
)
venue_mode = postgresql.ENUM(
    "disabled",
    "backtest",
    "paper",
    "shadow",
    "testnet",
    "live",
    name="venue_mode",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    trading_venue.create(bind, checkfirst=True)
    venue_mode.create(bind, checkfirst=True)

    op.create_table(
        "venue_runtime_control",
        sa.Column("venue", trading_venue, primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", venue_mode, nullable=False, server_default="disabled"),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("halted_reason", sa.Text()),
        sa.Column("live_confirmation_until", sa.DateTime(timezone=True)),
        sa.Column("strategy_version", sa.String(length=64)),
        sa.Column(
            "risk_config_version",
            sa.String(length=64),
            nullable=False,
            server_default="legacy",
        ),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO venue_runtime_control "
            "(venue, enabled, mode, kill_switch, halted_reason, "
            "live_confirmation_until, risk_config_version, updated_at) "
            "SELECT 'polymarket'::trading_venue, true, "
            "CASE WHEN mode::text = 'live' THEN 'live'::venue_mode "
            "ELSE 'paper'::venue_mode END, "
            "kill_switch, halted_reason, live_confirmation_until, 'legacy', updated_at "
            "FROM runtime_control WHERE id = 1 "
            "ON CONFLICT (venue) DO NOTHING"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO venue_runtime_control "
            "(venue, enabled, mode, kill_switch, risk_config_version) "
            "VALUES ('binance_spot', false, 'disabled', true, 'v1-conservative') "
            "ON CONFLICT (venue) DO NOTHING"
        )
    )

    op.create_table(
        "crypto_symbols",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("venue", trading_venue, nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("base_asset", sa.String(length=16), nullable=False),
        sa.Column("quote_asset", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("price_tick", sa.Numeric(38, 18)),
        sa.Column("quantity_step", sa.Numeric(38, 18)),
        sa.Column("minimum_quantity", sa.Numeric(38, 18)),
        sa.Column("minimum_notional", sa.Numeric(38, 18)),
        sa.Column("maximum_notional", sa.Numeric(38, 18)),
        sa.Column("supports_oco", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("supports_oto", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "filters",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("filter_hash", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("venue", "symbol", name="uq_crypto_symbols_venue_symbol"),
    )
    op.create_index(
        "ix_crypto_symbols_enabled_status",
        "crypto_symbols",
        ["enabled", "status"],
    )

    op.create_table(
        "crypto_candles",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_symbols.id"),
            nullable=False,
        ),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("open_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("close_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("high_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("low_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("close_price", sa.Numeric(38, 18), nullable=False),
        sa.Column("base_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("trade_count", sa.BigInteger(), nullable=False),
        sa.Column("taker_buy_base_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("taker_buy_quote_volume", sa.Numeric(38, 18), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=32), nullable=False, server_default="binance"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "symbol_id", "interval", "open_time", name="uq_crypto_candles_identity"
        ),
    )
    op.create_index(
        "ix_crypto_candles_lookup",
        "crypto_candles",
        ["symbol_id", "interval", "open_time"],
    )

    op.create_table(
        "crypto_market_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_symbols.id"),
            nullable=False,
        ),
        sa.Column("snapshot_type", sa.String(length=32), nullable=False),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("best_bid", sa.Numeric(38, 18), nullable=False),
        sa.Column("best_bid_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("best_ask", sa.Numeric(38, 18), nullable=False),
        sa.Column("best_ask_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("spread_bps", sa.Numeric(16, 8), nullable=False),
        sa.Column("bid_depth_quote", sa.Numeric(38, 18)),
        sa.Column("ask_depth_quote", sa.Numeric(38, 18)),
        sa.Column("imbalance", sa.Numeric(12, 8)),
        sa.Column("depth_valid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint(
            "symbol_id",
            "snapshot_type",
            "update_id",
            name="uq_crypto_market_snapshots_identity",
        ),
    )
    op.create_index(
        "ix_crypto_market_snapshots_time",
        "crypto_market_snapshots",
        ["symbol_id", "event_time"],
    )

    op.create_table(
        "crypto_data_gaps",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("symbol_id", sa.BigInteger(), sa.ForeignKey("crypto_symbols.id")),
        sa.Column("stream", sa.String(length=64), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expected_cursor", sa.String(length=64)),
        sa.Column("actual_cursor", sa.String(length=64)),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_crypto_data_gaps_status",
        "crypto_data_gaps",
        ["status", "detected_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_crypto_data_gaps_status", table_name="crypto_data_gaps")
    op.drop_table("crypto_data_gaps")
    op.drop_index("ix_crypto_market_snapshots_time", table_name="crypto_market_snapshots")
    op.drop_table("crypto_market_snapshots")
    op.drop_index("ix_crypto_candles_lookup", table_name="crypto_candles")
    op.drop_table("crypto_candles")
    op.drop_index("ix_crypto_symbols_enabled_status", table_name="crypto_symbols")
    op.drop_table("crypto_symbols")
    op.drop_table("venue_runtime_control")

    bind = op.get_bind()
    venue_mode.drop(bind, checkfirst=True)
    trading_venue.drop(bind, checkfirst=True)
