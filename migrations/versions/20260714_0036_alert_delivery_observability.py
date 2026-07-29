"""Add fail-observable alert-delivery state without activating Telegram.

Revision ID: 20260714_0036
Revises: 20260714_0035
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0036"
down_revision: str | None = "20260714_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "telegram_deliveries",
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_deliveries",
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_deliveries",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_deliveries",
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "telegram_deliveries",
        sa.Column("dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_index(
        "ix_telegram_deliveries_unacknowledged",
        "telegram_deliveries",
        ["id"],
        unique=False,
        postgresql_where=sa.text("acknowledged_at IS NULL"),
    )
    op.create_index(
        "ix_telegram_deliveries_due",
        "telegram_deliveries",
        ["next_attempt_at", "lease_until", "id"],
        unique=False,
        postgresql_where=sa.text(
            "sent_at IS NULL AND dead_lettered_at IS NULL"
        ),
    )
    op.create_index(
        "ix_telegram_deliveries_dead_letter",
        "telegram_deliveries",
        ["id"],
        unique=False,
        postgresql_where=sa.text("dead_lettered_at IS NOT NULL"),
    )

    op.create_table(
        "notification_delivery_runtime",
        sa.Column("channel", sa.String(length=32), primary_key=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.CheckConstraint(
            "channel ~ '^[a-z][a-z0-9_-]{0,31}$'",
            name="ck_notification_delivery_runtime_channel",
        ),
    )
    # Deliberately no INSERT here.  Absence of a row is the durable
    # ``not_activated`` state and prevents migration-time backlog replay.


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1 FROM notification_delivery_runtime
                ) OR EXISTS (
                    SELECT 1
                    FROM telegram_deliveries
                    WHERE acknowledged_at IS NOT NULL
                       OR last_attempt_at IS NOT NULL
                       OR next_attempt_at IS NOT NULL
                       OR lease_until IS NOT NULL
                       OR dead_lettered_at IS NOT NULL
                ) THEN
                    RAISE EXCEPTION USING
                        MESSAGE = 'refusing 0036 downgrade: alert delivery state exists',
                        HINT = 'export and explicitly reset alert delivery state before downgrade';
                END IF;
            END
            $$;
            """
        )
    )
    op.drop_table("notification_delivery_runtime")
    op.drop_index(
        "ix_telegram_deliveries_dead_letter",
        table_name="telegram_deliveries",
    )
    op.drop_index("ix_telegram_deliveries_due", table_name="telegram_deliveries")
    op.drop_index(
        "ix_telegram_deliveries_unacknowledged",
        table_name="telegram_deliveries",
    )
    op.drop_column("telegram_deliveries", "dead_lettered_at")
    op.drop_column("telegram_deliveries", "lease_until")
    op.drop_column("telegram_deliveries", "next_attempt_at")
    op.drop_column("telegram_deliveries", "last_attempt_at")
    op.drop_column("telegram_deliveries", "acknowledged_at")
