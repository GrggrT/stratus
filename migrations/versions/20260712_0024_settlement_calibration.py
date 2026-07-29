"""Add settlement-supervised probability calibration.

Revision ID: 20260712_0024
Revises: 20260712_0023
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0024"
down_revision: str | None = "20260712_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settlement_probability_calibrators",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("scope_key", sa.String(length=128), nullable=False, unique=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=True),
        sa.Column("horizon_bucket", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=8), nullable=True),
        sa.Column("alpha", sa.Numeric(12, 8), nullable=False),
        sa.Column("beta", sa.Numeric(12, 8), nullable=False),
        sa.Column("training_blocks", sa.Integer(), nullable=False),
        sa.Column("training_buckets", sa.Integer(), nullable=False),
        sa.Column("oof_baseline_brier", sa.Numeric(12, 8), nullable=True),
        sa.Column("oof_calibrated_brier", sa.Numeric(12, 8), nullable=True),
        sa.Column("oof_baseline_ece", sa.Numeric(12, 8), nullable=True),
        sa.Column("oof_calibrated_ece", sa.Numeric(12, 8), nullable=True),
        sa.Column("delta_brier_ci_low", sa.Numeric(12, 8), nullable=True),
        sa.Column("delta_brier_ci_high", sa.Numeric(12, 8), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("trained_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint("alpha >= 0.05 AND alpha <= 5", name="ck_settlement_cal_alpha"),
        sa.CheckConstraint("beta >= -2 AND beta <= 2", name="ck_settlement_cal_beta"),
        sa.CheckConstraint("training_blocks >= 0", name="ck_settlement_cal_blocks"),
    )
    op.create_index(
        "ix_settlement_calibrator_eligible",
        "settlement_probability_calibrators",
        ["eligible", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_settlement_calibrator_eligible",
        table_name="settlement_probability_calibrators",
    )
    op.drop_table("settlement_probability_calibrators")
