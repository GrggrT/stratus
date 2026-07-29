"""Freeze Binance symbol filters in each research contract.

Revision ID: 20260712_0023
Revises: 20260712_0022
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0023"
down_revision: str | None = "20260712_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "crypto_research_experiments",
        sa.Column("symbol_filters", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "crypto_research_experiments",
        sa.Column("symbol_filters_hash", sa.String(length=64), nullable=True),
    )
    bind = op.get_bind()
    experiment_count = bind.scalar(sa.text("SELECT count(*) FROM crypto_research_experiments"))
    if experiment_count:
        raise RuntimeError(
            "migration 0023 refuses to infer historical Binance symbol filters; "
            "archive or migrate existing research experiments explicitly"
        )
    op.alter_column(
        "crypto_research_experiments",
        "symbol_filters",
        existing_type=postgresql.JSONB(),
        nullable=False,
    )
    op.alter_column(
        "crypto_research_experiments",
        "symbol_filters_hash",
        existing_type=sa.String(length=64),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("crypto_research_experiments", "symbol_filters_hash")
    op.drop_column("crypto_research_experiments", "symbol_filters")
