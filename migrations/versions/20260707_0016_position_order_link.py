"""Link live positions to their originating order.

Revision ID: 20260707_0016
Revises: 20260707_0015
Create Date: 2026-07-07
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260707_0016"
down_revision: str | None = "20260707_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "positions",
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id")),
    )
    # One position per live order; paper positions leave order_id NULL and are
    # unaffected. The partial unique index makes fill polling idempotent.
    op.create_index(
        "uq_positions_order",
        "positions",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("order_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_positions_order", table_name="positions")
    op.drop_column("positions", "order_id")
