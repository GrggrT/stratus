"""Align active stations with current Polymarket resolution rules."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert

revision: str = "20260705_0002"
down_revision: str | None = "20260705_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    stations = sa.table(
        "stations",
        sa.column("icao", sa.String()),
        sa.column("name", sa.String()),
        sa.column("city", sa.String()),
        sa.column("lat", sa.Numeric()),
        sa.column("lon", sa.Numeric()),
        sa.column("tz", sa.String()),
        sa.column("active", sa.Boolean()),
    )
    rows = [
        {
            "icao": "EGLC",
            "name": "London City Airport",
            "city": "London",
            "lat": 51.505,
            "lon": 0.055,
            "tz": "Europe/London",
            "active": True,
        },
        {
            "icao": "LFPB",
            "name": "Paris-Le Bourget Airport",
            "city": "Paris",
            "lat": 48.967,
            "lon": 2.428,
            "tz": "Europe/Paris",
            "active": True,
        },
    ]
    statement = insert(stations).values(rows)
    op.execute(
        statement.on_conflict_do_update(
            index_elements=["icao"],
            set_={
                "name": statement.excluded.name,
                "city": statement.excluded.city,
                "lat": statement.excluded.lat,
                "lon": statement.excluded.lon,
                "tz": statement.excluded.tz,
                "active": statement.excluded.active,
            },
        )
    )
    op.execute(sa.text("UPDATE stations SET active = false WHERE icao IN ('EGLL', 'LFPG')"))


def downgrade() -> None:
    op.execute(sa.text("UPDATE stations SET active = false WHERE icao IN ('EGLC', 'LFPB')"))
    op.execute(sa.text("UPDATE stations SET active = true WHERE icao IN ('EGLL', 'LFPG')"))

