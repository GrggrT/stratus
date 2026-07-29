"""Add bounded-read indexes for the operator accuracy overview.

Revision ID: 20260730_0046
Revises: 20260728_0045
Create Date: 2026-07-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260730_0046"
down_revision: str | None = "20260728_0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_accuracy_predictions_eligible_expert_ts",
        "accuracy_predictions",
        ["expert", "ts"],
        unique=False,
        postgresql_where=sa.text("evidence_eligible IS TRUE"),
    )
    op.create_index(
        "ix_accuracy_predictions_eligible_expert_version_market_ts",
        "accuracy_predictions",
        ["expert", "expert_version", "market_id", "ts"],
        unique=False,
        postgresql_where=sa.text("evidence_eligible IS TRUE"),
    )
    op.create_index(
        "ix_model_skill_window_evaluated",
        "model_skill_reports",
        ["window_days", "evaluated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_model_skill_window_evaluated",
        table_name="model_skill_reports",
    )
    op.drop_index(
        "ix_accuracy_predictions_eligible_expert_version_market_ts",
        table_name="accuracy_predictions",
    )
    op.drop_index(
        "ix_accuracy_predictions_eligible_expert_ts",
        table_name="accuracy_predictions",
    )
