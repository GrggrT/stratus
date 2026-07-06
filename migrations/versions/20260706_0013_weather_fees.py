"""Store market fees and execution fee amounts.

Revision ID: 20260706_0013
Revises: 20260706_0012
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260706_0013"
down_revision: str | None = "20260706_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column("fees_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "markets",
        sa.Column("fee_rate", sa.Numeric(8, 6), nullable=False, server_default="0"),
    )
    op.add_column(
        "markets",
        sa.Column("fee_exponent", sa.SmallInteger(), nullable=False, server_default="1"),
    )
    op.add_column(
        "markets",
        sa.Column("fee_taker_only", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column("orders", sa.Column("fee_usd", sa.Numeric(14, 5)))
    op.add_column("positions", sa.Column("fee_usd", sa.Numeric(14, 5)))


def downgrade() -> None:
    op.drop_column("positions", "fee_usd")
    op.drop_column("orders", "fee_usd")
    op.drop_column("markets", "fee_taker_only")
    op.drop_column("markets", "fee_exponent")
    op.drop_column("markets", "fee_rate")
    op.drop_column("markets", "fees_enabled")
