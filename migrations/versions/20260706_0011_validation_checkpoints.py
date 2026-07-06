"""Persist 30/60-day gate checkpoints and terminal halt reason.

Revision ID: 20260706_0011
Revises: 20260706_0010
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260706_0011"
down_revision: str | None = "20260706_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("runtime_control", sa.Column("halted_reason", sa.Text()))
    op.create_table(
        "validation_checkpoints",
        sa.Column("checkpoint_day", sa.SmallInteger(), primary_key=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("validation_checkpoints")
    op.drop_column("runtime_control", "halted_reason")
