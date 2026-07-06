"""Prevent duplicate signals for the same market snapshot."""

from collections.abc import Sequence

from alembic import op

revision: str = "20260706_0007"
down_revision: str | None = "20260706_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint("uq_signals_market_time", "signals", ["market_id", "ts"])


def downgrade() -> None:
    op.drop_constraint("uq_signals_market_time", "signals", type_="unique")

