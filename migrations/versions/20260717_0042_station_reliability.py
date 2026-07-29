"""Add append-only station reliability and cooldown evidence.

Revision ID: 20260717_0042
Revises: 20260715_0041
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0042"
down_revision: str | None = "20260715_0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WEATHER_KIND = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)
STATION_RELIABILITY_STATUS = postgresql.ENUM(
    "collecting",
    "reliable",
    "watch",
    "blocked",
    name="station_reliability_status",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    STATION_RELIABILITY_STATUS.create(bind, checkfirst=True)
    op.create_table(
        "station_reliability_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_days", sa.Integer(), nullable=False),
        sa.Column("comparable_units", sa.Integer(), nullable=False),
        sa.Column("match_units", sa.Integer(), nullable=False),
        sa.Column("conflict_units", sa.Integer(), nullable=False),
        sa.Column("exact_source_mismatch_units", sa.Integer(), nullable=False),
        sa.Column("clear_mismatch_units", sa.Integer(), nullable=False),
        sa.Column("exact_truth_gap_units", sa.Integer(), nullable=False),
        sa.Column("boundary_units", sa.Integer(), nullable=False),
        sa.Column("unavailable_units", sa.Integer(), nullable=False),
        sa.Column("reliability_score", sa.Numeric(8, 6), nullable=True),
        sa.Column("reliability_lower_bound", sa.Numeric(8, 6), nullable=True),
        sa.Column("status", STATION_RELIABILITY_STATUS, nullable=False),
        sa.Column("latest_conflict_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_conflict_date", sa.Date(), nullable=True),
        sa.Column("latest_conflict_kind", WEATHER_KIND, nullable=True),
        sa.Column(
            "resolution_cooldown_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "latest_observation_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("observation_local_date", sa.Date(), nullable=False),
        sa.Column(
            "observation_cooldown_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "station_id",
            "evaluated_at",
            name="uq_station_reliability_station_evaluated",
        ),
        sa.CheckConstraint(
            "window_days > 0 AND comparable_units >= 0 AND match_units >= 0 "
            "AND conflict_units >= 0 AND boundary_units >= 0 "
            "AND unavailable_units >= 0 AND exact_source_mismatch_units >= 0 "
            "AND clear_mismatch_units >= 0 AND exact_truth_gap_units >= 0",
            name="ck_station_reliability_nonnegative_counts",
        ),
        sa.CheckConstraint(
            "comparable_units = match_units + conflict_units",
            name="ck_station_reliability_comparable_sum",
        ),
        sa.CheckConstraint(
            "conflict_units = exact_source_mismatch_units + clear_mismatch_units "
            "+ exact_truth_gap_units",
            name="ck_station_reliability_conflict_sum",
        ),
        sa.CheckConstraint(
            "reliability_score IS NULL OR (reliability_score >= 0 AND reliability_score <= 1)",
            name="ck_station_reliability_score_range",
        ),
        sa.CheckConstraint(
            "reliability_lower_bound IS NULL OR "
            "(reliability_lower_bound >= 0 AND reliability_lower_bound <= 1)",
            name="ck_station_reliability_lower_range",
        ),
        sa.CheckConstraint(
            "policy_hash ~ '^[0-9a-f]{64}$' AND "
            "source_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_station_reliability_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reasons) = 'array'",
            name="ck_station_reliability_reasons_array",
        ),
        sa.CheckConstraint(
            "(conflict_units = 0 AND latest_conflict_at IS NULL "
            "AND latest_conflict_date IS NULL AND latest_conflict_kind IS NULL "
            "AND resolution_cooldown_until IS NULL) OR "
            "(conflict_units > 0 AND latest_conflict_at IS NOT NULL "
            "AND latest_conflict_date IS NOT NULL "
            "AND latest_conflict_kind IS NOT NULL "
            "AND resolution_cooldown_until IS NOT NULL)",
            name="ck_station_reliability_conflict_metadata",
        ),
    )
    op.create_index(
        "ix_station_reliability_station_evaluated",
        "station_reliability_snapshots",
        ["station_id", "evaluated_at"],
    )
    op.create_index(
        "ix_station_reliability_status_evaluated",
        "station_reliability_snapshots",
        ["status", "evaluated_at"],
    )
    op.add_column(
        "signals",
        sa.Column("station_reliability_snapshot_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_signals_station_reliability_snapshot",
        "signals",
        "station_reliability_snapshots",
        ["station_reliability_snapshot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_signals_station_reliability_snapshot",
        "signals",
        ["station_reliability_snapshot_id"],
    )
    op.execute(
        """
        CREATE FUNCTION validate_signal_station_reliability_reference()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND NEW.station_reliability_snapshot_id
                   IS DISTINCT FROM OLD.station_reliability_snapshot_id THEN
                RAISE EXCEPTION
                    'signal station reliability reference is immutable';
            END IF;
            IF NEW.station_reliability_snapshot_id IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM station_reliability_snapshots AS srs
                   JOIN markets AS m ON m.station_id = srs.station_id
                   WHERE srs.id = NEW.station_reliability_snapshot_id
                     AND m.id = NEW.market_id
               ) THEN
                RAISE EXCEPTION
                    'signal station reliability reference does not match market station';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_signals_station_reliability_reference
        BEFORE INSERT OR UPDATE ON signals
        FOR EACH ROW EXECUTE FUNCTION validate_signal_station_reliability_reference()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_execution_intent_station_reliability()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            signal_decision_at timestamptz;
            snapshot_evaluated_at timestamptz;
            signal_station_id bigint;
            snapshot_station_id bigint;
            relevant_boundary_at timestamptz;
            relevant_lane_count integer;
        BEGIN
            SELECT s.ts, m.station_id, srs.evaluated_at, srs.station_id
            INTO signal_decision_at, signal_station_id,
                 snapshot_evaluated_at, snapshot_station_id
            FROM signals AS s
            JOIN markets AS m ON m.id = s.market_id
            LEFT JOIN station_reliability_snapshots AS srs
              ON srs.id = s.station_reliability_snapshot_id
            WHERE s.id = NEW.signal_id;

            IF snapshot_evaluated_at IS NULL
               OR snapshot_station_id IS DISTINCT FROM signal_station_id THEN
                RAISE EXCEPTION
                    'execution intent blocked: station reliability provenance is unavailable';
            END IF;

            SELECT count(*), max(ecl.boundary_at)
            INTO relevant_lane_count, relevant_boundary_at
            FROM evidence_continuity_lanes AS ecl
            WHERE ecl.epoch_id = (
                SELECT id FROM probability_pipeline_release_epochs
                ORDER BY id DESC LIMIT 1
            )
              AND ecl.lane_name IN ('selection_policy', 'station_lifecycle')
              AND ecl.action IN ('continue', 'fork');

            IF relevant_lane_count <> 2
               OR relevant_boundary_at IS NULL
               OR signal_decision_at < relevant_boundary_at
               OR snapshot_evaluated_at < relevant_boundary_at THEN
                RAISE EXCEPTION
                    'execution intent blocked: station decision evidence predates active lanes';
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_0042_execution_intent_station_reliability
        BEFORE INSERT ON execution_order_intents
        FOR EACH ROW EXECUTE FUNCTION guard_execution_intent_station_reliability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_station_reliability_snapshot_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'station reliability snapshots are append-only';
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_station_reliability_snapshots_append_only
        BEFORE UPDATE OR DELETE ON station_reliability_snapshots
        FOR EACH ROW EXECUTE FUNCTION reject_station_reliability_snapshot_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM station_reliability_snapshots) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'refusing 0042 downgrade: station reliability evidence exists',
                    HINT = 'export and explicitly reset station reliability evidence first';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_station_reliability_snapshots_append_only "
        "ON station_reliability_snapshots"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_signals_station_reliability_reference ON signals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_0042_execution_intent_station_reliability "
        "ON execution_order_intents"
    )
    op.execute("DROP FUNCTION IF EXISTS guard_execution_intent_station_reliability()")
    op.execute("DROP FUNCTION IF EXISTS validate_signal_station_reliability_reference()")
    op.drop_index("ix_signals_station_reliability_snapshot", table_name="signals")
    op.drop_constraint(
        "fk_signals_station_reliability_snapshot",
        "signals",
        type_="foreignkey",
    )
    op.drop_column("signals", "station_reliability_snapshot_id")
    op.execute("DROP FUNCTION IF EXISTS reject_station_reliability_snapshot_mutation()")
    op.drop_index(
        "ix_station_reliability_status_evaluated",
        table_name="station_reliability_snapshots",
    )
    op.drop_index(
        "ix_station_reliability_station_evaluated",
        table_name="station_reliability_snapshots",
    )
    op.drop_table("station_reliability_snapshots")
    STATION_RELIABILITY_STATUS.drop(op.get_bind(), checkfirst=True)
