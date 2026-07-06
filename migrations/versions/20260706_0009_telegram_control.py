"""Add persistent runtime control and Telegram delivery log.

Revision ID: 20260706_0009
Revises: 20260706_0008
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260706_0009"
down_revision: str | None = "20260706_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    execution_mode = postgresql.ENUM(
        "paper", "live", name="execution_mode", create_type=False
    )
    op.create_table(
        "runtime_control",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("kill_switch", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("mode", execution_mode, nullable=False, server_default="paper"),
        sa.Column("live_confirmation_until", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO runtime_control (id, kill_switch, mode) "
            "VALUES (1, false, 'paper')"
        )
    )
    op.create_table(
        "telegram_deliveries",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("telegram_deliveries")
    op.drop_table("runtime_control")
