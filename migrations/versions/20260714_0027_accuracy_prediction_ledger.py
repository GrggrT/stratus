"""Seal prospective accuracy predictions as an append-only evidence ledger.

Revision ID: 20260714_0027
Revises: 20260713_0026
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260714_0027"
down_revision: str | None = "20260713_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "accuracy_predictions",
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column("training_cutoff", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column(
            "baseline_model_prob_id",
            sa.BigInteger(),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_accuracy_predictions_baseline_model_prob",
        "accuracy_predictions",
        "model_probs",
        ["baseline_model_prob_id"],
        ["id"],
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column("input_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column("experiment_fingerprint", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column("record_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "accuracy_predictions",
        sa.Column(
            "evidence_eligible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    # Legacy rows remain useful diagnostics but cannot become prospective evidence.
    op.execute(
        """
        UPDATE accuracy_predictions
        SET generated_at = ts,
            experiment_fingerprint = expert_version,
            evidence_eligible = false
        WHERE generated_at IS NULL
        """
    )
    op.alter_column(
        "accuracy_predictions",
        "generated_at",
        nullable=False,
        server_default=sa.text("clock_timestamp()"),
    )
    op.create_check_constraint(
        "ck_accuracy_predictions_sealed_provenance",
        "accuracy_predictions",
        """
        NOT evidence_eligible OR (
            point_in_time_valid
            AND training_cutoff IS NOT NULL
            AND baseline_model_prob_id IS NOT NULL
            AND length(input_hash) = 64
            AND length(experiment_fingerprint) = 64
            AND length(record_hash) = 64
            AND ts <= generated_at
            AND training_cutoff <= generated_at
        )
        """,
    )
    op.create_index(
        "ix_accuracy_predictions_evidence",
        "accuracy_predictions",
        ["expert", "expert_version", "evidence_eligible", "generated_at"],
    )
    op.create_index(
        "ix_accuracy_predictions_baseline",
        "accuracy_predictions",
        ["baseline_model_prob_id"],
    )
    op.execute(
        """
        CREATE FUNCTION validate_accuracy_prediction_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            stored_baseline_probability numeric;
            stored_pipeline_version text;
        BEGIN
            IF NEW.evidence_eligible THEN
                -- Do not trust a client-supplied timestamp for prospective evidence.
                NEW.generated_at := clock_timestamp();
                SELECT prob, components->'pipeline'->>'version'
                INTO stored_baseline_probability, stored_pipeline_version
                FROM model_probs
                WHERE id = NEW.baseline_model_prob_id
                  AND market_id = NEW.market_id
                  AND ts = NEW.ts;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction baseline does not match market/time';
                END IF;
                IF abs(
                    stored_baseline_probability
                    - (NEW.distribution->>'baseline_probability')::numeric
                ) > 0.000001 THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction baseline probability mismatch';
                END IF;
                IF stored_pipeline_version IS DISTINCT FROM
                    NEW.distribution->>'baseline_pipeline_version' THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction pipeline version mismatch';
                END IF;
                IF NOT EXISTS (
                    SELECT 1
                    FROM markets
                    WHERE id = NEW.market_id
                      AND resolves_at > NEW.generated_at
                ) THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction was generated after resolution';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_predictions_validate_insert
        BEFORE INSERT ON accuracy_predictions
        FOR EACH ROW EXECUTE FUNCTION validate_accuracy_prediction_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_accuracy_prediction_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'accuracy_predictions is an append-only evidence ledger';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_predictions_append_only
        BEFORE UPDATE OR DELETE ON accuracy_predictions
        FOR EACH ROW EXECUTE FUNCTION reject_accuracy_prediction_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_accuracy_predictions_append_only ON accuracy_predictions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_accuracy_prediction_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_accuracy_predictions_validate_insert "
        "ON accuracy_predictions"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_accuracy_prediction_insert()")
    op.drop_index("ix_accuracy_predictions_baseline", table_name="accuracy_predictions")
    op.drop_index("ix_accuracy_predictions_evidence", table_name="accuracy_predictions")
    op.execute(
        "ALTER TABLE accuracy_predictions DROP CONSTRAINT IF EXISTS "
        "ck_accuracy_predictions_sealed_provenance"
    )
    op.drop_column("accuracy_predictions", "evidence_eligible")
    op.drop_column("accuracy_predictions", "record_hash")
    op.drop_column("accuracy_predictions", "experiment_fingerprint")
    op.drop_column("accuracy_predictions", "input_hash")
    op.drop_constraint(
        "fk_accuracy_predictions_baseline_model_prob",
        "accuracy_predictions",
        type_="foreignkey",
    )
    op.drop_column("accuracy_predictions", "baseline_model_prob_id")
    op.drop_column("accuracy_predictions", "training_cutoff")
    op.drop_column("accuracy_predictions", "generated_at")
