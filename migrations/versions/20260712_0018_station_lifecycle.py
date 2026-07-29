"""Add the 20-station research pool and operational lifecycle.

Revision ID: 20260712_0018
Revises: 20260712_0017
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import insert

revision: str = "20260712_0018"
down_revision: str | None = "20260712_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

station_stage = postgresql.ENUM(
    "shadow", "paper", "approved", "disabled", name="station_stage", create_type=False
)

STATIONS = [
    (
        "KLGA",
        "LaGuardia Airport",
        "New York City",
        40.7769,
        -73.8740,
        "America/New_York",
        "NY_ASOS",
        "LGA",
        "paper",
    ),
    (
        "EGLC",
        "London City Airport",
        "London",
        51.5050,
        0.0550,
        "Europe/London",
        "GB__ASOS",
        "EGLC",
        "paper",
    ),
    (
        "LFPB",
        "Paris-Le Bourget Airport",
        "Paris",
        48.9670,
        2.4280,
        "Europe/Paris",
        "FR__ASOS",
        "LFPB",
        "paper",
    ),
    (
        "RJTT",
        "Tokyo Haneda Airport",
        "Tokyo",
        35.5533,
        139.7811,
        "Asia/Tokyo",
        "JP__ASOS",
        "RJTT",
        "paper",
    ),
    (
        "RKSI",
        "Seoul Incheon Airport",
        "Seoul",
        37.4667,
        126.4500,
        "Asia/Seoul",
        "KR__ASOS",
        "RKSI",
        "paper",
    ),
    (
        "ZSPD",
        "Shanghai Pudong Airport",
        "Shanghai",
        31.1167,
        121.7667,
        "Asia/Shanghai",
        "CN__ASOS",
        "ZSPD",
        "shadow",
    ),
    (
        "KMIA",
        "Miami International Airport",
        "Miami",
        25.7880,
        -80.3169,
        "America/New_York",
        "FL_ASOS",
        "MIA",
        "shadow",
    ),
    (
        "EHAM",
        "Amsterdam Schiphol Airport",
        "Amsterdam",
        52.3154,
        4.7902,
        "Europe/Amsterdam",
        "NL__ASOS",
        "EHAM",
        "shadow",
    ),
    (
        "RKPK",
        "Busan Gimhae Airport",
        "Busan",
        35.1795,
        128.9382,
        "Asia/Seoul",
        "KR__ASOS",
        "RKPK",
        "shadow",
    ),
    (
        "EDDM",
        "Munich Airport",
        "Munich",
        48.3583,
        11.8092,
        "Europe/Berlin",
        "DE__ASOS",
        "EDDM",
        "shadow",
    ),
    (
        "LEMD",
        "Madrid Barajas Airport",
        "Madrid",
        40.4667,
        -3.5556,
        "Europe/Madrid",
        "ES__ASOS",
        "LEMD",
        "shadow",
    ),
    (
        "EFHK",
        "Helsinki Airport",
        "Helsinki",
        60.3172,
        24.9633,
        "Europe/Helsinki",
        "FI__ASOS",
        "EFHK",
        "shadow",
    ),
    (
        "EPWA",
        "Warsaw Chopin Airport",
        "Warsaw",
        52.1628,
        20.9611,
        "Europe/Warsaw",
        "PL__ASOS",
        "EPWA",
        "shadow",
    ),
    (
        "WSSS",
        "Singapore Changi Airport",
        "Singapore",
        1.3667,
        103.9833,
        "Asia/Singapore",
        "SG__ASOS",
        "WSSS",
        "shadow",
    ),
    (
        "RCSS",
        "Taipei Songshan Airport",
        "Taipei",
        25.0694,
        121.5517,
        "Asia/Taipei",
        "TW__ASOS",
        "RCSS",
        "shadow",
    ),
    (
        "WMKK",
        "Kuala Lumpur International Airport",
        "Kuala Lumpur",
        2.7167,
        101.7000,
        "Asia/Kuala_Lumpur",
        "MY__ASOS",
        "WMKK",
        "shadow",
    ),
    (
        "RPLL",
        "Manila Ninoy Aquino Airport",
        "Manila",
        14.5069,
        121.0042,
        "Asia/Manila",
        "PH__ASOS",
        "RPLL",
        "shadow",
    ),
    (
        "NZWN",
        "Wellington Airport",
        "Wellington",
        -41.3272,
        174.8053,
        "Pacific/Auckland",
        "NF__ASOS",
        "NZWN",
        "shadow",
    ),
    (
        "KORD",
        "Chicago O'Hare Airport",
        "Chicago",
        41.9602,
        -87.9316,
        "America/Chicago",
        "IL_ASOS",
        "ORD",
        "shadow",
    ),
    (
        "KLAX",
        "Los Angeles International Airport",
        "Los Angeles",
        33.9382,
        -118.3865,
        "America/Los_Angeles",
        "CA_ASOS",
        "LAX",
        "shadow",
    ),
]


def upgrade() -> None:
    station_stage.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "stations",
        sa.Column("stage", station_stage, nullable=False, server_default="shadow"),
    )
    stations = sa.table(
        "stations",
        sa.column("icao", sa.String()),
        sa.column("name", sa.String()),
        sa.column("city", sa.String()),
        sa.column("lat", sa.Numeric()),
        sa.column("lon", sa.Numeric()),
        sa.column("tz", sa.String()),
        sa.column("iem_network", sa.String()),
        sa.column("iem_station_id", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("stage", station_stage),
    )
    rows = [
        {
            "icao": icao,
            "name": name,
            "city": city,
            "lat": lat,
            "lon": lon,
            "tz": tz,
            "iem_network": network,
            "iem_station_id": source_id,
            "active": True,
            "stage": stage,
        }
        for icao, name, city, lat, lon, tz, network, source_id, stage in STATIONS
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
                "iem_network": statement.excluded.iem_network,
                "iem_station_id": statement.excluded.iem_station_id,
                "active": statement.excluded.active,
                "stage": statement.excluded.stage,
            },
        )
    )


def downgrade() -> None:
    added = [row[0] for row in STATIONS if row[0] not in {"KLGA", "EGLC", "LFPB"}]
    op.execute(
        sa.text("UPDATE stations SET active = false WHERE icao = ANY(:icaos)").bindparams(
            sa.bindparam("icaos", value=added, type_=postgresql.ARRAY(sa.String()))
        )
    )
    op.drop_column("stations", "stage")
    station_stage.drop(op.get_bind(), checkfirst=True)
