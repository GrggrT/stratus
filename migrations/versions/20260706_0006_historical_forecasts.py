"""Store fixed-lead forecasts used for calibration."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260706_0006"
down_revision: str | None = "20260705_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "historical_forecasts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("model", sa.String(32), nullable=False),
        sa.Column("valid_date", sa.Date(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM("high", "low", name="weather_kind", create_type=False),
            nullable=False,
        ),
        sa.Column("horizon_h", sa.SmallInteger(), nullable=False),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint(
            "station_id",
            "model",
            "valid_date",
            "kind",
            "horizon_h",
            name="uq_historical_forecasts_identity",
        ),
    )
    op.create_index(
        "ix_historical_forecasts_calibration",
        "historical_forecasts",
        ["station_id", "model", "horizon_h", "kind", "valid_date"],
    )


def downgrade() -> None:
    op.drop_table("historical_forecasts")
