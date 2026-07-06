"""Store station verification block reasons.

Revision ID: 20260706_0014
Revises: 20260706_0013
Create Date: 2026-07-06
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260706_0014"
down_revision: str | None = "20260706_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "markets",
        sa.Column("station_verification_reason", sa.String(64)),
    )


def downgrade() -> None:
    op.drop_column("markets", "station_verification_reason")
