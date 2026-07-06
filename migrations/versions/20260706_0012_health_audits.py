"""Add data-quality health audit history.

Revision ID: 20260706_0012
Revises: 20260706_0011
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260706_0012"
down_revision: str | None = "20260706_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "health_audits",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )
    op.create_index("ix_health_audits_ts", "health_audits", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_health_audits_ts", table_name="health_audits")
    op.drop_table("health_audits")
