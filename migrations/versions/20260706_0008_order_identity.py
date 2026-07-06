"""Prevent duplicate execution of a signal in one mode.

Revision ID: 20260706_0008
Revises: 20260706_0007
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260706_0008"
down_revision: str | None = "20260706_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_orders_signal_mode", "orders", ["signal_id", "mode"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_orders_signal_mode", "orders", type_="unique")
