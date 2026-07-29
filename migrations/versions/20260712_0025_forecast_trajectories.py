"""Store compact point-in-time hourly forecast trajectories.

Revision ID: 20260712_0025
Revises: 20260712_0024
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0025"
down_revision: str | None = "20260712_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "forecast_trajectories",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("model", sa.String(length=32), nullable=False),
        sa.Column("run_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_date", sa.Date(), nullable=False),
        sa.Column("points", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_id", sa.BigInteger(), sa.ForeignKey("forecast_runs.id"), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "station_id",
            "model",
            "run_time",
            "valid_date",
            name="uq_forecast_trajectory_identity",
        ),
    )
    op.create_index(
        "ix_forecast_trajectory_point_in_time",
        "forecast_trajectories",
        ["station_id", "valid_date", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_forecast_trajectory_point_in_time", table_name="forecast_trajectories")
    op.drop_table("forecast_trajectories")
