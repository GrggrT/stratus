"""Store CLOB condition and outcome token identifiers on markets."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0003"
down_revision: str | None = "20260705_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("markets", sa.Column("condition_id", sa.String(66), nullable=True))
    op.add_column("markets", sa.Column("yes_token_id", sa.String(80), nullable=True))
    op.add_column("markets", sa.Column("no_token_id", sa.String(80), nullable=True))


def downgrade() -> None:
    op.drop_column("markets", "no_token_id")
    op.drop_column("markets", "yes_token_id")
    op.drop_column("markets", "condition_id")

