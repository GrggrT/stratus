"""Add durable resolution reconciliation, settlement lifecycle, and capacity samples.

Revision ID: 20260714_0034
Revises: 20260714_0033
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0034"
down_revision: str | None = "20260714_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WEATHER_KIND = postgresql.ENUM(
    "high", "low", name="weather_kind", create_type=False
)
RECONCILIATION_CLASSIFICATION = postgresql.ENUM(
    "match",
    "boundary_sensitive",
    "clear_mismatch",
    "exact_truth_gap",
    "exact_source_mismatch",
    "truth_unavailable",
    name="resolution_reconciliation_classification",
    create_type=False,
)
SETTLEMENT_CLASSIFICATION = postgresql.ENUM(
    "venue_pending",
    "source_missing",
    "ambiguous",
    "manual_review",
    "resolved",
    name="settlement_case_classification",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    RECONCILIATION_CLASSIFICATION.create(bind, checkfirst=True)
    SETTLEMENT_CLASSIFICATION.create(bind, checkfirst=True)

    op.create_table(
        "resolution_reconciliations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "market_id",
            sa.BigInteger(),
            sa.ForeignKey("markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("kind", WEATHER_KIND, nullable=False),
        sa.Column(
            "resolution_event_id",
            sa.BigInteger(),
            sa.ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "settlement_actual_id",
            sa.BigInteger(),
            sa.ForeignKey("settlement_actuals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "classification", RECONCILIATION_CLASSIFICATION, nullable=False
        ),
        sa.Column("resolved_yes", sa.Boolean(), nullable=False),
        sa.Column("metar_temp_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("metar_implied_yes", sa.Boolean(), nullable=True),
        sa.Column("exact_temp_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("exact_implied_yes", sa.Boolean(), nullable=True),
        sa.Column("boundary_margin_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("exact_truth_gap_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("rules_hash", sa.String(length=64), nullable=False),
        sa.Column("resolution_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("metar_value_hash", sa.String(length=64), nullable=True),
        sa.Column("exact_value_hash", sa.String(length=64), nullable=True),
        sa.Column("exact_source_hash", sa.String(length=64), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "market_id", name="uq_resolution_reconciliations_market"
        ),
        sa.CheckConstraint(
            "rules_hash ~ '^[0-9a-f]{64}$' AND "
            "resolution_vector_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_required_hashes",
        ),
        sa.CheckConstraint(
            "metar_value_hash IS NULL OR metar_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_metar_hash",
        ),
        sa.CheckConstraint(
            "exact_value_hash IS NULL OR exact_value_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_exact_value_hash",
        ),
        sa.CheckConstraint(
            "exact_source_hash IS NULL OR exact_source_hash ~ '^[0-9a-f]{64}$'",
            name="ck_resolution_reconciliations_exact_source_hash",
        ),
        sa.CheckConstraint(
            "boundary_margin_c IS NULL OR boundary_margin_c >= 0",
            name="ck_resolution_reconciliations_boundary_margin",
        ),
        sa.CheckConstraint(
            "exact_truth_gap_c IS NULL OR exact_truth_gap_c >= 0",
            name="ck_resolution_reconciliations_truth_gap",
        ),
    )
    op.create_index(
        "ix_resolution_reconciliations_classification_checked",
        "resolution_reconciliations",
        ["classification", "checked_at"],
    )
    op.create_index(
        "ix_resolution_reconciliations_event",
        "resolution_reconciliations",
        ["event_id"],
    )

    op.create_table(
        "settlement_cases",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("classification", SETTLEMENT_CLASSIFICATION, nullable=False),
        sa.Column(
            "previous_classification", SETTLEMENT_CLASSIFICATION, nullable=True
        ),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("event_resolves_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "classification_changed_at", sa.DateTime(timezone=True), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("transition_count", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_response_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "resolution_event_id",
            sa.BigInteger(),
            sa.ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("resolution_vector_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("event_id", name="uq_settlement_cases_event"),
        sa.CheckConstraint(
            "attempt_count >= 1", name="ck_settlement_cases_attempts"
        ),
        sa.CheckConstraint(
            "transition_count >= 1", name="ck_settlement_cases_transitions"
        ),
        sa.CheckConstraint(
            "source_response_sha256 IS NULL OR "
            "source_response_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_source_hash",
        ),
        sa.CheckConstraint(
            "resolution_vector_hash IS NULL OR "
            "resolution_vector_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_vector_hash",
        ),
        sa.CheckConstraint(
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_settlement_cases_record_hash",
        ),
        sa.CheckConstraint(
            "(classification = 'resolved'::settlement_case_classification AND "
            "resolved_at IS NOT NULL AND resolution_event_id IS NOT NULL AND "
            "resolution_vector_hash IS NOT NULL) OR "
            "(classification <> 'resolved'::settlement_case_classification AND "
            "resolved_at IS NULL AND resolution_event_id IS NULL AND "
            "resolution_vector_hash IS NULL)",
            name="ck_settlement_cases_resolved_evidence",
        ),
    )
    op.create_index(
        "ix_settlement_cases_classification_checked",
        "settlement_cases",
        ["classification", "last_checked_at"],
    )

    # Current-state projections remain convenient for jobs and the operator API,
    # but every superseded evidence version is retained independently.  This is
    # deliberately generic so the pre-existing settlement_actuals table receives
    # the same database-enforced history guarantee as the two projections added
    # by this migration.
    op.create_table(
        "evidence_version_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_record_hash", sa.String(length=64), nullable=True),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "version",
            name="uq_evidence_version_history_entity_version",
        ),
        sa.CheckConstraint(
            "entity_type IN ('resolution_reconciliation', "
            "'settlement_case', 'settlement_actual')",
            name="ck_evidence_version_history_entity_type",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_evidence_version_history_version",
        ),
        sa.CheckConstraint(
            "snapshot_sha256 ~ '^[0-9a-f]{64}$' AND "
            "(source_record_hash IS NULL OR "
            "source_record_hash ~ '^[0-9a-f]{64}$')",
            name="ck_evidence_version_history_hashes",
        ),
    )
    op.create_index(
        "ix_evidence_version_history_entity",
        "evidence_version_history",
        ["entity_type", "entity_id", "version"],
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_resolution_reconciliation_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            ledger_event_id text;
            ledger_vector_hash text;
            vector_item jsonb;
            market_event_id text;
            market_external_id text;
            market_station_id bigint;
            market_date_value date;
            market_kind text;
            market_resolution_event_id bigint;
            market_resolved_outcome boolean;
            market_status text;
            actual_station_id bigint;
            actual_date date;
            actual_kind text;
            actual_value_c numeric;
            actual_is_final boolean;
            actual_source_hash text;
            contract_event_id text;
            contract_rules_hash text;
        BEGIN
            IF TG_OP = 'UPDATE' AND (
                NEW.market_id IS DISTINCT FROM OLD.market_id
                OR NEW.event_id IS DISTINCT FROM OLD.event_id
                OR NEW.station_id IS DISTINCT FROM OLD.station_id
                OR NEW.market_date IS DISTINCT FROM OLD.market_date
                OR NEW.kind IS DISTINCT FROM OLD.kind
                OR NEW.resolution_event_id IS DISTINCT FROM OLD.resolution_event_id
                OR NEW.resolution_vector_hash
                   IS DISTINCT FROM OLD.resolution_vector_hash
                OR NEW.resolved_yes IS DISTINCT FROM OLD.resolved_yes
                OR NEW.rules_hash IS DISTINCT FROM OLD.rules_hash
            ) THEN
                RAISE EXCEPTION
                    'resolution reconciliation attested identity is immutable';
            END IF;

            SELECT goe.event_id, goe.outcome_vector_hash,
                   m.event_id, m.polymarket_id, m.station_id, m.market_date,
                   m.kind::text, m.resolution_event_id, m.resolved_outcome,
                   m.status::text
            INTO ledger_event_id, ledger_vector_hash,
                 market_event_id, market_external_id, market_station_id,
                 market_date_value, market_kind, market_resolution_event_id,
                 market_resolved_outcome, market_status
            FROM gamma_outcome_events AS goe
            CROSS JOIN markets AS m
            WHERE goe.id = NEW.resolution_event_id
              AND m.id = NEW.market_id;

            IF NOT FOUND
               OR ledger_event_id IS DISTINCT FROM NEW.event_id
               OR ledger_vector_hash IS DISTINCT FROM NEW.resolution_vector_hash
               OR market_event_id IS DISTINCT FROM NEW.event_id
               OR market_resolution_event_id IS DISTINCT FROM NEW.resolution_event_id
               OR market_station_id IS DISTINCT FROM NEW.station_id
               OR market_date_value IS DISTINCT FROM NEW.market_date
               OR market_kind IS DISTINCT FROM NEW.kind::text
               OR market_resolved_outcome IS DISTINCT FROM NEW.resolved_yes
               OR market_status IS DISTINCT FROM 'resolved' THEN
                RAISE EXCEPTION
                    'resolution reconciliation does not match Gamma/market evidence';
            END IF;

            SELECT value
            INTO vector_item
            FROM gamma_outcome_events AS goe,
                 LATERAL jsonb_array_elements(goe.outcome_vector)
            WHERE goe.id = NEW.resolution_event_id
              AND (value->>'market_id')::bigint = NEW.market_id
              AND value->>'polymarket_id' = market_external_id;
            IF NOT FOUND
               OR (vector_item->>'outcome_yes')::boolean
                  IS DISTINCT FROM NEW.resolved_yes
               OR vector_item->>'rules_hash' IS DISTINCT FROM NEW.rules_hash THEN
                RAISE EXCEPTION
                    'resolution reconciliation outcome/rules do not match Gamma vector';
            END IF;

            IF NEW.settlement_actual_id IS NULL THEN
                IF NEW.exact_temp_c IS NOT NULL
                   OR NEW.exact_implied_yes IS NOT NULL
                   OR NEW.exact_value_hash IS NOT NULL
                   OR NEW.exact_source_hash IS NOT NULL THEN
                    RAISE EXCEPTION
                        'resolution reconciliation exact truth lacks settlement evidence';
                END IF;
            ELSE
                SELECT sa.station_id, sa.date, sa.kind::text, sa.value_c,
                       sa.is_final, sa.source_response_hash,
                       rc.event_id, rc.rules_hash
                INTO actual_station_id, actual_date, actual_kind, actual_value_c,
                     actual_is_final, actual_source_hash,
                     contract_event_id, contract_rules_hash
                FROM settlement_actuals AS sa
                JOIN resolution_contracts AS rc ON rc.id = sa.contract_id
                WHERE sa.id = NEW.settlement_actual_id;
                IF NOT FOUND
                   OR actual_is_final IS DISTINCT FROM true
                   OR actual_station_id IS DISTINCT FROM NEW.station_id
                   OR actual_date IS DISTINCT FROM NEW.market_date
                   OR actual_kind IS DISTINCT FROM NEW.kind::text
                   OR actual_value_c IS DISTINCT FROM NEW.exact_temp_c
                   OR contract_event_id IS DISTINCT FROM NEW.event_id
                   OR contract_rules_hash IS DISTINCT FROM NEW.rules_hash
                   OR NEW.exact_value_hash IS NULL
                   OR (
                       actual_source_hash ~ '^[0-9a-f]{64}$'
                       AND actual_source_hash IS DISTINCT FROM NEW.exact_source_hash
                   )
                   OR (
                       (actual_source_hash IS NULL
                        OR actual_source_hash !~ '^[0-9a-f]{64}$')
                       AND NEW.exact_source_hash IS NOT NULL
                    ) THEN
                    RAISE EXCEPTION
                        'exact truth does not match final settlement evidence';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_resolution_reconciliations_validate_evidence
        BEFORE INSERT OR UPDATE ON resolution_reconciliations
        FOR EACH ROW EXECUTE FUNCTION validate_resolution_reconciliation_evidence()
        """
    )

    op.execute(
        """
        CREATE FUNCTION validate_settlement_case_evidence()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            ledger_event_id text;
            ledger_vector_hash text;
            ledger_source_url text;
            ledger_source_hash text;
            ledger_resolved_at timestamptz;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.classification::text = 'resolved'
               AND (
                   NEW.classification::text IS DISTINCT FROM 'resolved'
                   OR NEW.event_id IS DISTINCT FROM OLD.event_id
                   OR NEW.resolution_event_id
                      IS DISTINCT FROM OLD.resolution_event_id
                   OR NEW.resolution_vector_hash
                      IS DISTINCT FROM OLD.resolution_vector_hash
                   OR NEW.source_url IS DISTINCT FROM OLD.source_url
                   OR NEW.source_response_sha256
                      IS DISTINCT FROM OLD.source_response_sha256
                   OR NEW.resolved_at IS DISTINCT FROM OLD.resolved_at
               ) THEN
                RAISE EXCEPTION
                    'resolved settlement case terminal evidence is immutable';
            END IF;

            IF NEW.resolution_event_id IS NULL THEN
                RETURN NEW;
            END IF;

            SELECT event_id, outcome_vector_hash, source_url,
                   source_response_sha256, fully_resolved_at
            INTO ledger_event_id, ledger_vector_hash, ledger_source_url,
                 ledger_source_hash, ledger_resolved_at
            FROM gamma_outcome_events
            WHERE id = NEW.resolution_event_id;
            IF NOT FOUND
               OR NEW.classification::text IS DISTINCT FROM 'resolved'
               OR ledger_event_id IS DISTINCT FROM NEW.event_id
               OR ledger_vector_hash IS DISTINCT FROM NEW.resolution_vector_hash
               OR ledger_source_url IS DISTINCT FROM NEW.source_url
               OR ledger_source_hash IS DISTINCT FROM NEW.source_response_sha256
               OR ledger_resolved_at IS DISTINCT FROM NEW.resolved_at
               OR EXISTS (
                   SELECT 1
                   FROM markets
                   WHERE event_id = NEW.event_id
                     AND (
                         resolution_event_id IS DISTINCT FROM NEW.resolution_event_id
                         OR status::text IS DISTINCT FROM 'resolved'
                         OR resolved_outcome IS NULL
                     )
               ) THEN
                RAISE EXCEPTION
                    'settlement case does not match immutable Gamma event evidence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_settlement_cases_validate_evidence
        BEFORE INSERT OR UPDATE ON settlement_cases
        FOR EACH ROW EXECUTE FUNCTION validate_settlement_case_evidence()
        """
    )

    op.execute(
        """
        CREATE FUNCTION archive_evidence_projection_version()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            entity_type_value text;
            snapshot_value jsonb;
            next_version integer;
        BEGIN
            IF TG_TABLE_NAME = 'resolution_reconciliations' THEN
                entity_type_value := 'resolution_reconciliation';
            ELSIF TG_TABLE_NAME = 'settlement_cases' THEN
                entity_type_value := 'settlement_case';
            ELSIF TG_TABLE_NAME = 'settlement_actuals' THEN
                entity_type_value := 'settlement_actual';
            ELSE
                RAISE EXCEPTION 'unsupported evidence projection table: %', TG_TABLE_NAME;
            END IF;

            IF TG_OP = 'DELETE' THEN
                IF TG_TABLE_NAME <> 'settlement_actuals'
                   OR coalesce((to_jsonb(OLD)->>'is_final')::boolean, false) THEN
                    RAISE EXCEPTION '% current evidence projection cannot be deleted',
                        entity_type_value;
                END IF;
                RETURN OLD;
            END IF;

            -- A mutable preliminary actual becomes evidence only once final.
            -- Replacing a row that was already final must always retain OLD.
            IF TG_TABLE_NAME = 'settlement_actuals'
               AND NOT coalesce(
                   (to_jsonb(OLD)->>'is_final')::boolean,
                   false
               ) THEN
                RETURN NEW;
            END IF;

            -- Polling heartbeats must not turn the audit trail into a second
            -- copy of the scheduler log.  Archive only when the evidence or
            -- its interpretation changes; hashes/timestamps derived from a
            -- heartbeat alone are deliberately ignored.
            IF TG_TABLE_NAME = 'resolution_reconciliations'
               AND (
                   to_jsonb(NEW) - ARRAY['checked_at', 'record_hash']
               ) IS NOT DISTINCT FROM (
                   to_jsonb(OLD) - ARRAY['checked_at', 'record_hash']
               ) THEN
                RETURN NEW;
            END IF;
            IF TG_TABLE_NAME = 'settlement_cases'
               AND (
                   to_jsonb(NEW) - ARRAY[
                       'last_checked_at', 'attempt_count', 'record_hash'
                   ]
               ) IS NOT DISTINCT FROM (
                   to_jsonb(OLD) - ARRAY[
                       'last_checked_at', 'attempt_count', 'record_hash'
                   ]
               ) THEN
                RETURN NEW;
            END IF;

            PERFORM pg_advisory_xact_lock(
                hashtextextended(
                    entity_type_value || ':' || OLD.id::text,
                    34020260714::bigint
                )
            );
            snapshot_value := to_jsonb(OLD);
            SELECT coalesce(max(version), 0) + 1
            INTO next_version
            FROM evidence_version_history
            WHERE entity_type = entity_type_value
              AND entity_id = OLD.id;

            INSERT INTO evidence_version_history (
                entity_type,
                entity_id,
                version,
                superseded_at,
                snapshot,
                snapshot_sha256,
                source_record_hash
            ) VALUES (
                entity_type_value,
                OLD.id,
                next_version,
                clock_timestamp(),
                snapshot_value,
                encode(
                    digest(convert_to(snapshot_value::text, 'UTF8'), 'sha256'),
                    'hex'
                ),
                snapshot_value->>'record_hash'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    for table_name in (
        "resolution_reconciliations",
        "settlement_cases",
        "settlement_actuals",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_archive_version
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION archive_evidence_projection_version()
            """
        )

    op.execute(
        """
        CREATE FUNCTION reject_evidence_version_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'evidence version history is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_evidence_version_history_append_only
        BEFORE UPDATE OR DELETE ON evidence_version_history
        FOR EACH ROW EXECUTE FUNCTION reject_evidence_version_history_mutation()
        """
    )

    op.create_table(
        "storage_capacity_samples",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("database_bytes", sa.BigInteger(), nullable=False),
        sa.Column("clob_book_batches_bytes", sa.BigInteger(), nullable=False),
        sa.Column("clob_market_snapshots_bytes", sa.BigInteger(), nullable=False),
        sa.Column("clob_batch_count", sa.BigInteger(), nullable=False),
        sa.Column("clob_snapshot_count", sa.BigInteger(), nullable=False),
        sa.Column("oldest_batch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("newest_batch_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("free_disk_bytes", sa.BigInteger(), nullable=True),
        sa.Column("retention_policy_version", sa.String(length=64), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "captured_at", name="uq_storage_capacity_samples_captured"
        ),
        sa.CheckConstraint(
            "database_bytes >= 0 AND clob_book_batches_bytes >= 0 AND "
            "clob_market_snapshots_bytes >= 0",
            name="ck_storage_capacity_samples_bytes",
        ),
        sa.CheckConstraint(
            "clob_batch_count >= 0 AND clob_snapshot_count >= 0",
            name="ck_storage_capacity_samples_counts",
        ),
        sa.CheckConstraint(
            "free_disk_bytes IS NULL OR free_disk_bytes >= 0",
            name="ck_storage_capacity_samples_free_disk",
        ),
        sa.CheckConstraint(
            "oldest_batch_at IS NULL OR newest_batch_at IS NULL OR "
            "oldest_batch_at <= newest_batch_at",
            name="ck_storage_capacity_samples_batch_order",
        ),
    )
    op.create_index(
        "ix_storage_capacity_samples_captured",
        "storage_capacity_samples",
        ["captured_at"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_storage_capacity_sample_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'storage capacity samples are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_storage_capacity_samples_append_only
        BEFORE UPDATE OR DELETE ON storage_capacity_samples
        FOR EACH ROW EXECUTE FUNCTION reject_storage_capacity_sample_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_storage_capacity_samples_append_only "
        "ON storage_capacity_samples"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_storage_capacity_sample_mutation()")
    op.drop_index(
        "ix_storage_capacity_samples_captured",
        table_name="storage_capacity_samples",
    )
    op.drop_table("storage_capacity_samples")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_settlement_actuals_archive_version "
        "ON settlement_actuals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_settlement_cases_archive_version "
        "ON settlement_cases"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_resolution_reconciliations_archive_version "
        "ON resolution_reconciliations"
    )
    op.execute("DROP FUNCTION IF EXISTS archive_evidence_projection_version()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_settlement_cases_validate_evidence "
        "ON settlement_cases"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_settlement_case_evidence()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_resolution_reconciliations_validate_evidence "
        "ON resolution_reconciliations"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS validate_resolution_reconciliation_evidence()"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_evidence_version_history_append_only "
        "ON evidence_version_history"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_evidence_version_history_mutation()")
    op.drop_index(
        "ix_evidence_version_history_entity",
        table_name="evidence_version_history",
    )
    op.drop_table("evidence_version_history")
    op.drop_index(
        "ix_settlement_cases_classification_checked", table_name="settlement_cases"
    )
    op.drop_table("settlement_cases")
    op.drop_index(
        "ix_resolution_reconciliations_event",
        table_name="resolution_reconciliations",
    )
    op.drop_index(
        "ix_resolution_reconciliations_classification_checked",
        table_name="resolution_reconciliations",
    )
    op.drop_table("resolution_reconciliations")

    bind = op.get_bind()
    SETTLEMENT_CLASSIFICATION.drop(bind, checkfirst=True)
    RECONCILIATION_CLASSIFICATION.drop(bind, checkfirst=True)
