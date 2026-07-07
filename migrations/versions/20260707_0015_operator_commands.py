"""Add the operator command queue.

Revision ID: 20260707_0015
Revises: 20260706_0014
Create Date: 2026-07-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260707_0015"
down_revision: str | None = "20260706_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operator_commands",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("request_id", sa.String(length=64), nullable=False, unique=True),
        sa.Column("command", sa.String(length=32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.String(length=16), nullable=False, server_default="pending"
        ),
        sa.Column("detail", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("executed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_operator_commands_status", "operator_commands", ["status", "id"]
    )


def downgrade() -> None:
    op.drop_index("ix_operator_commands_status", table_name="operator_commands")
    op.drop_table("operator_commands")
