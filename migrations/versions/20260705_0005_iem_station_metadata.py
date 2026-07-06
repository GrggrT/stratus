"""Add explicit IEM source identifiers to stations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260705_0005"
down_revision: str | None = "20260705_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("stations", sa.Column("iem_network", sa.String(32), nullable=True))
    op.add_column("stations", sa.Column("iem_station_id", sa.String(16), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE stations
            SET iem_network = CASE icao
                    WHEN 'KLGA' THEN 'NY_ASOS'
                    WHEN 'EGLC' THEN 'GB__ASOS'
                    WHEN 'LFPB' THEN 'FR__ASOS'
                END,
                iem_station_id = CASE icao
                    WHEN 'KLGA' THEN 'LGA'
                    WHEN 'EGLC' THEN 'EGLC'
                    WHEN 'LFPB' THEN 'LFPB'
                END
            WHERE icao IN ('KLGA', 'EGLC', 'LFPB')
            """
        )
    )


def downgrade() -> None:
    op.drop_column("stations", "iem_station_id")
    op.drop_column("stations", "iem_network")

