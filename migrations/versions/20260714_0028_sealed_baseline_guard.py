"""Protect model-probability baselines referenced by sealed evidence.

Revision ID: 20260714_0028
Revises: 20260714_0027
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0028"
down_revision: str | None = "20260714_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION protect_sealed_model_prob_baseline()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM accuracy_predictions
                WHERE baseline_model_prob_id = OLD.id
                  AND evidence_eligible
            ) THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION
                        'model_prob is an immutable baseline for sealed accuracy evidence';
                END IF;
                IF to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                    RAISE EXCEPTION
                        'model_prob is an immutable baseline for sealed accuracy evidence';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_model_probs_protect_sealed_baseline
        BEFORE UPDATE OR DELETE ON model_probs
        FOR EACH ROW EXECUTE FUNCTION protect_sealed_model_prob_baseline()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_model_probs_protect_sealed_baseline ON model_probs"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_sealed_model_prob_baseline()")
