"""Add verified Binance Spot aggregate trades.

Revision ID: 20260712_0020
Revises: 20260712_0019
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0020"
down_revision: str | None = "20260712_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "crypto_archive_imports",
        "interval",
        existing_type=sa.String(length=8),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.create_table(
        "crypto_aggregate_trades",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_symbols.id"),
            nullable=False,
        ),
        sa.Column("aggregate_trade_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("first_trade_id", sa.BigInteger(), nullable=False),
        sa.Column("last_trade_id", sa.BigInteger(), nullable=False),
        sa.Column("trade_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("buyer_is_maker", sa.Boolean(), nullable=False),
        sa.Column("best_price_match", sa.Boolean(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="binance_archive",
        ),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "aggregate_trade_id >= 0 AND first_trade_id >= 0 AND last_trade_id >= 0",
            name="ck_crypto_aggregate_trades_nonnegative_ids",
        ),
        sa.CheckConstraint(
            "first_trade_id <= last_trade_id",
            name="ck_crypto_aggregate_trades_trade_id_range",
        ),
        sa.CheckConstraint(
            "price > 0 AND quantity > 0",
            name="ck_crypto_aggregate_trades_positive_values",
        ),
        sa.UniqueConstraint(
            "symbol_id",
            "aggregate_trade_id",
            name="uq_crypto_aggregate_trades_identity",
        ),
    )
    op.create_index(
        "ix_crypto_aggregate_trades_time",
        "crypto_aggregate_trades",
        ["symbol_id", "trade_time", "aggregate_trade_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crypto_aggregate_trades_time",
        table_name="crypto_aggregate_trades",
    )
    op.drop_table("crypto_aggregate_trades")
    op.alter_column(
        "crypto_archive_imports",
        "interval",
        existing_type=sa.String(length=16),
        type_=sa.String(length=8),
        existing_nullable=False,
    )
