"""Add immutable prospective selection-policy decision ledger.

Revision ID: 20260713_0026
Revises: 20260712_0025
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0026"
down_revision: str | None = "20260712_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "selection_policy_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("policy_name", sa.String(length=64), nullable=False),
        sa.Column("policy_hash", sa.String(length=16), nullable=False),
        sa.Column("max_predicted_edge", sa.Numeric(8, 6), nullable=True),
        sa.Column("predicted_edge", sa.Numeric(8, 6), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("signal_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "order_id",
            "policy_name",
            name="uq_selection_policy_decisions_order_policy",
        ),
    )
    op.create_index(
        "ix_selection_policy_decisions_pipeline",
        "selection_policy_decisions",
        ["pipeline_version", "policy_name", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_selection_policy_decisions_pipeline",
        table_name="selection_policy_decisions",
    )
    op.drop_table("selection_policy_decisions")
