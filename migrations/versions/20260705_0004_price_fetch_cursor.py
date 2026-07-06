"""Track the last CLOB fetch attempt for adaptive polling."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0004"
down_revision: str | None = "20260705_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "markets", sa.Column("last_price_fetch_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("markets", "last_price_fetch_at")

