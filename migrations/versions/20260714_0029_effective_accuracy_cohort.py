"""Bind sealed evidence to the effective probability pipeline cohort.

Revision ID: 20260714_0029
Revises: 20260714_0028
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0029"
down_revision: str | None = "20260714_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _validation_function(pipeline_expression: str, *, hardened: bool) -> str:
    payload_validation = (
        """
                IF NEW.distribution->>'evidence_protocol' IS DISTINCT FROM 'sealed-v2' THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction evidence protocol mismatch';
                END IF;
                IF jsonb_typeof(
                    NEW.distribution->'baseline_probability'
                ) IS DISTINCT FROM 'number' THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction baseline probability missing';
                END IF;
                IF jsonb_typeof(
                    NEW.distribution->'baseline_pipeline_version'
                ) IS DISTINCT FROM 'string'
                   OR NULLIF(
                       NEW.distribution->>'baseline_pipeline_version', ''
                   ) IS NULL THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction pipeline version missing';
                END IF;
                IF jsonb_typeof(
                    NEW.distribution->'normalized_baseline_probability'
                ) IS DISTINCT FROM 'number'
                   OR (NEW.distribution->>'normalized_baseline_probability')::numeric < 0
                   OR (NEW.distribution->>'normalized_baseline_probability')::numeric > 1 THEN
                    RAISE EXCEPTION
                        'sealed accuracy prediction normalized baseline missing';
                END IF;
        """
        if hardened
        else ""
    )
    pipeline_mismatch = (
        "stored_pipeline_version IS NULL OR stored_pipeline_version IS DISTINCT FROM"
        if hardened
        else "stored_pipeline_version IS DISTINCT FROM"
    )
    return f"""
        CREATE OR REPLACE FUNCTION validate_accuracy_prediction_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            stored_baseline_probability numeric;
            stored_pipeline_version text;
        BEGIN
            IF NEW.evidence_eligible THEN
                NEW.generated_at := clock_timestamp();
                {payload_validation}
                SELECT prob, {pipeline_expression}
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
                IF {pipeline_mismatch}
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


def upgrade() -> None:
    op.execute(
        _validation_function(
            "COALESCE("
            "CASE WHEN jsonb_typeof(components->'pipeline'->'cohort') = 'string' "
            "THEN NULLIF(components->'pipeline'->>'cohort', '') END, "
            "CASE WHEN jsonb_typeof(components->'pipeline'->'version') = 'string' "
            "THEN NULLIF(components->'pipeline'->>'version', '') END"
            ")",
            hardened=True,
        )
    )


def downgrade() -> None:
    op.execute(
        _validation_function(
            "components->'pipeline'->>'version'",
            hardened=False,
        )
    )
