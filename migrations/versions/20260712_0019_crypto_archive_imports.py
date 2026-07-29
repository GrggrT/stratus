"""Add a verified Binance public-archive import manifest.

Revision ID: 20260712_0019
Revises: 20260712_0018
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260712_0019"
down_revision: str | None = "20260712_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_archive_imports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "symbol_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_symbols.id"),
            nullable=False,
        ),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64)),
        sa.Column("archive_bytes", sa.BigInteger(), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "symbol_id",
            "interval",
            "trading_date",
            name="uq_crypto_archive_imports_identity",
        ),
    )
    op.create_index(
        "ix_crypto_archive_imports_status",
        "crypto_archive_imports",
        ["status", "trading_date"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_crypto_archive_imports_status",
        table_name="crypto_archive_imports",
    )
    op.drop_table("crypto_archive_imports")
