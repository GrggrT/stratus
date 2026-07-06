"""Add scheduler run history for collector uptime.

Revision ID: 20260706_0010
Revises: 20260706_0009
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260706_0010"
down_revision: str | None = "20260706_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text()),
        sa.UniqueConstraint("job_id", "scheduled_at", name="uq_job_runs_schedule"),
    )
    op.create_index("ix_job_runs_finished", "job_runs", ["finished_at"])


def downgrade() -> None:
    op.drop_index("ix_job_runs_finished", table_name="job_runs")
    op.drop_table("job_runs")
