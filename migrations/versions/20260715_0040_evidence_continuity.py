"""Add immutable probability-pipeline release epochs and execution guard.

Revision ID: 20260715_0040
Revises: 20260715_0039
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_0040"
down_revision: str | None = "20260715_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "evidence_continuity_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("definition_version", sa.String(length=48), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_evidence_continuity_runtime_singleton"),
        sa.CheckConstraint(
            "definition_version = 'evidence-continuity-v1' AND "
            "definition_hash = "
            "'6eb2e131b73d7b4e9c9e2fc85aa0b82f518165d33cbcdccafaa4cf566dfece57'",
            name="ck_evidence_continuity_runtime_definition",
        ),
    )
    op.execute(
        "INSERT INTO evidence_continuity_runtime "
        "(id, definition_version, definition_hash) VALUES "
        "(1, 'evidence-continuity-v1', "
        "'6eb2e131b73d7b4e9c9e2fc85aa0b82f518165d33cbcdccafaa4cf566dfece57')"
    )
    op.create_table(
        "probability_pipeline_release_epochs",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("material_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("activation_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("cohort", sa.String(length=192), nullable=False),
        sa.Column("image_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "previous_epoch_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "probability_pipeline_release_epochs.id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("approval_reason", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "previous_epoch_id",
            name="uq_probability_pipeline_release_epochs_previous",
        ),
        sa.UniqueConstraint(
            "plan_hash",
            name="uq_probability_pipeline_release_epochs_plan_hash",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_probability_pipeline_release_epochs_record_hash",
        ),
        sa.CheckConstraint(
            "pipeline_version ~ '^[a-z0-9][a-z0-9._-]{0,127}$'",
            name="ck_probability_pipeline_release_epochs_version",
        ),
        sa.CheckConstraint(
            "material_fingerprint ~ '^[0-9a-f]{16}$' "
            "AND activation_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND image_digest ~ '^[0-9a-f]{64}$' "
            "AND plan_hash ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_probability_pipeline_release_epochs_hashes",
        ),
        sa.CheckConstraint(
            "cohort = pipeline_version || '+' || material_fingerprint || "
            "'+promotions-' || left(activation_manifest_hash, 16) "
            "AND length(cohort) <= 128",
            name="ck_probability_pipeline_release_epochs_cohort",
        ),
        sa.CheckConstraint(
            "approval_reason = btrim(approval_reason) "
            "AND length(approval_reason) BETWEEN 16 AND 2000 "
            "AND approval_reason !~ '[[:cntrl:]]' "
            "AND array_length("
            "regexp_split_to_array(approval_reason, '[[:space:]]+'), 1"
            ") >= 3",
            name="ck_probability_pipeline_release_epochs_reason",
        ),
        sa.CheckConstraint(
            "previous_epoch_id IS NULL OR previous_epoch_id < id",
            name="ck_probability_pipeline_release_epochs_previous_order",
        ),
    )
    op.create_index(
        "ix_probability_pipeline_release_epochs_opened",
        "probability_pipeline_release_epochs",
        ["opened_at", "id"],
    )

    op.create_table(
        "probability_pipeline_release_plans",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "planned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("pipeline_version", sa.String(length=128), nullable=False),
        sa.Column("material_fingerprint", sa.String(length=16), nullable=False),
        sa.Column("activation_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("cohort", sa.String(length=192), nullable=False),
        sa.Column("image_digest", sa.String(length=64), nullable=False),
        sa.Column(
            "previous_epoch_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "probability_pipeline_release_epochs.id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
        sa.Column("release_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("release_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("lane_plans", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("lane_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("reviewed_by", sa.String(length=128), nullable=False),
        sa.Column("approval_reason", sa.Text(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "plan_hash",
            name="uq_probability_pipeline_release_plans_plan_hash",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_probability_pipeline_release_plans_record_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(release_manifest) = 'object' AND "
            "jsonb_typeof(lane_plans) = 'array'",
            name="ck_probability_pipeline_release_plans_manifests",
        ),
        sa.CheckConstraint(
            "release_manifest_hash ~ '^[0-9a-f]{64}$' AND "
            "lane_manifest_hash ~ '^[0-9a-f]{64}$' AND "
            "plan_hash ~ '^[0-9a-f]{64}$' AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_probability_pipeline_release_plans_hashes",
        ),
        sa.CheckConstraint(
            "reviewed_by = btrim(reviewed_by) AND length(reviewed_by) BETWEEN 3 AND 128",
            name="ck_probability_pipeline_release_plans_reviewer",
        ),
        sa.CheckConstraint(
            "approval_reason = btrim(approval_reason) "
            "AND length(approval_reason) BETWEEN 16 AND 2000 "
            "AND approval_reason !~ '[[:cntrl:]]'",
            name="ck_probability_pipeline_release_plans_reason",
        ),
    )
    op.add_column(
        "probability_pipeline_release_epochs",
        sa.Column("plan_id", sa.BigInteger(), nullable=False),
    )
    op.create_foreign_key(
        "fk_probability_pipeline_release_epochs_plan",
        "probability_pipeline_release_epochs",
        "probability_pipeline_release_plans",
        ["plan_id"],
        ["id"],
        ondelete="RESTRICT",
        use_alter=True,
    )
    op.create_unique_constraint(
        "uq_probability_pipeline_release_epochs_plan",
        "probability_pipeline_release_epochs",
        ["plan_id"],
    )
    op.create_table(
        "evidence_continuity_lanes",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(always=True),
            primary_key=True,
        ),
        sa.Column(
            "epoch_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "probability_pipeline_release_epochs.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column("lane_name", sa.String(length=48), nullable=False),
        sa.Column("action", sa.String(length=24), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "parent_lane_id",
            sa.BigInteger(),
            sa.ForeignKey("evidence_continuity_lanes.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("boundary_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "epoch_id",
            "lane_name",
            name="uq_evidence_continuity_lanes_epoch_name",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_evidence_continuity_lanes_record_hash",
        ),
        sa.CheckConstraint(
            "lane_name IN ('predictive_validation', 'selection_policy', "
            "'execution_evidence', 'operational_legacy', 'station_lifecycle', "
            "'official_resolution_truth')",
            name="ck_evidence_continuity_lanes_name",
        ),
        sa.CheckConstraint(
            "action IN ('continue', 'fork', 'freeze', 'not_applicable')",
            name="ck_evidence_continuity_lanes_action",
        ),
        sa.CheckConstraint(
            "identity_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$' AND jsonb_typeof(manifest) = 'object'",
            name="ck_evidence_continuity_lanes_integrity",
        ),
        sa.CheckConstraint(
            "rationale = btrim(rationale) AND length(rationale) BETWEEN 8 AND 1000",
            name="ck_evidence_continuity_lanes_rationale",
        ),
    )
    op.create_index(
        "ix_evidence_continuity_lanes_name_boundary",
        "evidence_continuity_lanes",
        ["lane_name", "boundary_at", "id"],
    )

    # Existing 0038 intents deliberately remain NULL: they are operational
    # legacy evidence, not relabelled 0040 decisions.  The admission trigger
    # below atomically stamps every new intent with the exact epoch and six
    # immutable lane decisions that authorized it.
    intent_lane_columns = (
        "predictive_lane_id",
        "selection_lane_id",
        "execution_lane_id",
        "operational_lane_id",
        "station_lane_id",
        "truth_lane_id",
    )
    op.add_column(
        "execution_order_intents",
        sa.Column("release_epoch_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_execution_order_intents_release_epoch",
        "execution_order_intents",
        "probability_pipeline_release_epochs",
        ["release_epoch_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    for column_name in intent_lane_columns:
        op.add_column(
            "execution_order_intents",
            sa.Column(column_name, sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_execution_order_intents_{column_name}",
            "execution_order_intents",
            "evidence_continuity_lanes",
            [column_name],
            ["id"],
            ondelete="RESTRICT",
        )
    attestation_columns = ("release_epoch_id", *intent_lane_columns)
    all_null = " AND ".join(f"{name} IS NULL" for name in attestation_columns)
    all_present = " AND ".join(f"{name} IS NOT NULL" for name in attestation_columns)
    op.create_check_constraint(
        "ck_execution_order_intents_continuity_attestation",
        "execution_order_intents",
        f"({all_null}) OR ({all_present})",
    )

    op.execute(
        r"""
        CREATE FUNCTION probability_pipeline_release_plan_hash(
            p_pipeline_version text,
            p_material_fingerprint text,
            p_activation_manifest_hash text,
            p_cohort text,
            p_image_digest text,
            p_previous_epoch_id bigint,
            p_approval_reason text
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT encode(
                digest(
                    convert_to(
                        'probability-pipeline-release-plan-v1|'
                        || encode(convert_to(p_pipeline_version, 'UTF8'), 'hex') || '|'
                        || p_material_fingerprint || '|'
                        || p_activation_manifest_hash || '|'
                        || encode(convert_to(p_cohort, 'UTF8'), 'hex') || '|'
                        || p_image_digest || '|'
                        || coalesce(p_previous_epoch_id::text, '-') || '|'
                        || encode(convert_to(p_approval_reason, 'UTF8'), 'hex'),
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION probability_pipeline_release_record_hash(
            p_id bigint,
            p_opened_at timestamptz,
            p_plan_hash text,
            p_previous_record_hash text
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT encode(
                digest(
                    convert_to(
                        'probability-pipeline-release-record-v1|'
                        || p_id::text || '|'
                        || to_char(
                            p_opened_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                        ) || '|'
                        || p_plan_hash || '|'
                        || coalesce(p_previous_record_hash, '-'),
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION evidence_release_plan_hash(
            p_pipeline_version text,
            p_material_fingerprint text,
            p_activation_manifest_hash text,
            p_cohort text,
            p_image_digest text,
            p_previous_epoch_id bigint,
            p_release_manifest_hash text,
            p_lane_manifest_hash text,
            p_reviewed_by text,
            p_approval_reason text
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT encode(
                digest(
                    convert_to(
                        'evidence-release-plan-v2|'
                        || encode(convert_to(p_pipeline_version, 'UTF8'), 'hex') || '|'
                        || p_material_fingerprint || '|'
                        || p_activation_manifest_hash || '|'
                        || encode(convert_to(p_cohort, 'UTF8'), 'hex') || '|'
                        || p_image_digest || '|'
                        || coalesce(p_previous_epoch_id::text, '-') || '|'
                        || p_release_manifest_hash || '|'
                        || p_lane_manifest_hash || '|'
                        || encode(convert_to(p_reviewed_by, 'UTF8'), 'hex') || '|'
                        || encode(convert_to(p_approval_reason, 'UTF8'), 'hex'),
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION evidence_release_plan_record_hash(
            p_id bigint,
            p_planned_at timestamptz,
            p_plan_hash text
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT encode(
                digest(
                    convert_to(
                        'evidence-release-plan-record-v1|'
                        || p_id::text || '|'
                        || to_char(
                            p_planned_at AT TIME ZONE 'UTC',
                            'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                        ) || '|'
                        || p_plan_hash,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION validate_probability_pipeline_release_plan_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_cohort text;
            expected_plan_hash text;
            expected_record_hash text;
            lane_count integer;
            lane_name_count integer;
        BEGIN
            NEW.planned_at := clock_timestamp();
            expected_cohort := NEW.pipeline_version || '+'
                || NEW.material_fingerprint || '+promotions-'
                || left(NEW.activation_manifest_hash, 16);
            IF NEW.cohort IS DISTINCT FROM expected_cohort THEN
                RAISE EXCEPTION 'evidence release plan cohort mismatch';
            END IF;
            IF execution_cost_hash(NEW.release_manifest)
               IS DISTINCT FROM NEW.release_manifest_hash THEN
                RAISE EXCEPTION 'evidence release manifest hash mismatch';
            END IF;
            IF execution_cost_hash(NEW.lane_plans)
               IS DISTINCT FROM NEW.lane_manifest_hash THEN
                RAISE EXCEPTION 'evidence release lane manifest hash mismatch';
            END IF;
            IF NEW.release_manifest::text ~*
               '"[^"]*(secret|password|private[_-]?key|credential|api[_-]?key|bearer)'
               '[^"]*"[[:space:]]*:'
               OR NEW.lane_plans::text ~*
               '"[^"]*(secret|password|private[_-]?key|credential|api[_-]?key|bearer)'
               '[^"]*"[[:space:]]*:' THEN
                RAISE EXCEPTION
                    'evidence release manifest contains a forbidden secret-like key';
            END IF;
            SELECT count(*), count(DISTINCT value->>'lane_name')
            INTO lane_count, lane_name_count
            FROM jsonb_array_elements(NEW.lane_plans);
            IF lane_count <> 6 OR lane_name_count <> 6 OR EXISTS (
                SELECT 1
                FROM jsonb_array_elements(NEW.lane_plans) AS item(value)
                WHERE value->>'lane_name' NOT IN (
                    'predictive_validation', 'selection_policy',
                    'execution_evidence', 'operational_legacy',
                    'station_lifecycle', 'official_resolution_truth'
                )
                   OR value->>'action' NOT IN (
                       'continue', 'fork', 'freeze', 'not_applicable'
                   )
                   OR coalesce(value->>'identity_hash', '') !~ '^[0-9a-f]{64}$'
                   OR jsonb_typeof(value->'manifest') IS DISTINCT FROM 'object'
                   OR execution_cost_hash(value->'manifest')
                      IS DISTINCT FROM value->>'identity_hash'
                   OR length(btrim(coalesce(value->>'rationale', ''))) < 8
            ) THEN
                RAISE EXCEPTION 'evidence release plan lane set is incomplete or invalid';
            END IF;
            expected_plan_hash := evidence_release_plan_hash(
                NEW.pipeline_version,
                NEW.material_fingerprint,
                NEW.activation_manifest_hash,
                NEW.cohort,
                NEW.image_digest,
                NEW.previous_epoch_id,
                NEW.release_manifest_hash,
                NEW.lane_manifest_hash,
                NEW.reviewed_by,
                NEW.approval_reason
            );
            IF NEW.plan_hash IS DISTINCT FROM expected_plan_hash THEN
                RAISE EXCEPTION 'evidence release plan hash mismatch';
            END IF;
            expected_record_hash := evidence_release_plan_record_hash(
                NEW.id,
                NEW.planned_at,
                NEW.plan_hash
            );
            IF NEW.record_hash IS NOT NULL
               AND NEW.record_hash <> repeat('0', 64)
               AND NEW.record_hash <> expected_record_hash THEN
                RAISE EXCEPTION 'evidence release plan record hash mismatch';
            END IF;
            NEW.record_hash := expected_record_hash;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_probability_pipeline_release_plans_validate_insert "
        "BEFORE INSERT ON probability_pipeline_release_plans FOR EACH ROW "
        "EXECUTE FUNCTION validate_probability_pipeline_release_plan_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION validate_probability_pipeline_release_epoch_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            latest_id bigint;
            latest_opened_at timestamptz;
            latest_cohort text;
            latest_record_hash text;
            latest_release_manifest_hash text;
            release_plan probability_pipeline_release_plans%ROWTYPE;
            expected_cohort text;
            expected_record_hash text;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('stratus:probability-pipeline-release-epoch-v1', 0)
            );

            IF NEW.pipeline_version !~ '^[a-z0-9][a-z0-9._-]{0,127}$'
               OR NEW.material_fingerprint !~ '^[0-9a-f]{16}$'
               OR NEW.activation_manifest_hash !~ '^[0-9a-f]{64}$'
               OR NEW.image_digest !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'invalid probability pipeline release identity';
            END IF;
            IF NEW.approval_reason IS DISTINCT FROM btrim(NEW.approval_reason)
               OR length(NEW.approval_reason) NOT BETWEEN 16 AND 2000
               OR NEW.approval_reason ~ '[[:cntrl:]]'
               OR array_length(
                    regexp_split_to_array(NEW.approval_reason, '[[:space:]]+'), 1
                  ) < 3 THEN
                RAISE EXCEPTION 'probability pipeline release reason is not meaningful';
            END IF;

            expected_cohort := NEW.pipeline_version || '+'
                || NEW.material_fingerprint || '+promotions-'
                || left(NEW.activation_manifest_hash, 16);
            IF NEW.cohort IS DISTINCT FROM expected_cohort
               OR length(NEW.cohort) > 128 THEN
                RAISE EXCEPTION 'probability pipeline release cohort mismatch';
            END IF;
            SELECT * INTO release_plan
            FROM probability_pipeline_release_plans
            WHERE id = NEW.plan_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'probability pipeline release plan is unavailable';
            END IF;
            IF release_plan.pipeline_version IS DISTINCT FROM NEW.pipeline_version
               OR release_plan.material_fingerprint IS DISTINCT FROM NEW.material_fingerprint
               OR release_plan.activation_manifest_hash
                  IS DISTINCT FROM NEW.activation_manifest_hash
               OR release_plan.cohort IS DISTINCT FROM NEW.cohort
               OR release_plan.image_digest IS DISTINCT FROM NEW.image_digest
               OR release_plan.previous_epoch_id IS DISTINCT FROM NEW.previous_epoch_id
               OR release_plan.approval_reason IS DISTINCT FROM NEW.approval_reason
               OR release_plan.plan_hash IS DISTINCT FROM NEW.plan_hash THEN
                RAISE EXCEPTION 'probability pipeline release differs from reviewed plan';
            END IF;

            SELECT e.id, e.opened_at, e.cohort, e.record_hash,
                   p.release_manifest_hash
            INTO latest_id, latest_opened_at, latest_cohort, latest_record_hash,
                 latest_release_manifest_hash
            FROM probability_pipeline_release_epochs e
            JOIN probability_pipeline_release_plans p ON p.id = e.plan_id
            ORDER BY e.id DESC
            LIMIT 1;

            IF latest_id IS NULL THEN
                IF NEW.previous_epoch_id IS NOT NULL THEN
                    RAISE EXCEPTION
                        'first probability pipeline release must not cite a previous epoch';
                END IF;
            ELSE
                IF NEW.previous_epoch_id IS DISTINCT FROM latest_id THEN
                    RAISE EXCEPTION 'probability pipeline release must extend the latest epoch';
                END IF;
                IF NEW.cohort = latest_cohort
                   AND NEW.image_digest = (
                       SELECT image_digest
                       FROM probability_pipeline_release_epochs
                       WHERE id = latest_id
                   )
                   AND release_plan.release_manifest_hash = latest_release_manifest_hash
                   AND NOT EXISTS (
                       SELECT 1
                       FROM jsonb_array_elements(release_plan.lane_plans) AS planned(value)
                       LEFT JOIN evidence_continuity_lanes current_lane
                         ON current_lane.epoch_id = latest_id
                        AND current_lane.lane_name = planned.value->>'lane_name'
                       WHERE current_lane.id IS NULL
                          OR current_lane.identity_hash
                             IS DISTINCT FROM planned.value->>'identity_hash'
                          OR current_lane.manifest
                             IS DISTINCT FROM planned.value->'manifest'
                   )
                   THEN
                    RAISE EXCEPTION
                        'probability pipeline release is an exact no-op manifest';
                END IF;
            END IF;

            NEW.opened_at := clock_timestamp();
            IF latest_opened_at IS NOT NULL AND NEW.opened_at < latest_opened_at THEN
                RAISE EXCEPTION 'probability pipeline release clock moved backwards';
            END IF;
            expected_record_hash := probability_pipeline_release_record_hash(
                NEW.id,
                NEW.opened_at,
                NEW.plan_hash,
                latest_record_hash
            );
            IF NEW.record_hash IS NOT NULL
               AND NEW.record_hash <> repeat('0', 64)
               AND NEW.record_hash <> expected_record_hash THEN
                RAISE EXCEPTION 'probability pipeline release record hash mismatch';
            END IF;
            NEW.record_hash := expected_record_hash;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_probability_pipeline_release_epochs_validate_insert "
        "BEFORE INSERT ON probability_pipeline_release_epochs FOR EACH ROW "
        "EXECUTE FUNCTION validate_probability_pipeline_release_epoch_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION reject_probability_pipeline_release_epoch_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'probability_pipeline_release_epochs is append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_probability_pipeline_release_epochs_append_only "
        "BEFORE UPDATE OR DELETE ON probability_pipeline_release_epochs FOR EACH ROW "
        "EXECUTE FUNCTION reject_probability_pipeline_release_epoch_mutation()"
    )

    op.execute(
        r"""
        CREATE FUNCTION evidence_continuity_lane_record_hash(
            p_id bigint,
            p_epoch_id bigint,
            p_lane_name text,
            p_action text,
            p_identity_hash text,
            p_parent_lane_id bigint,
            p_boundary_at timestamptz,
            p_rationale text
        ) RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT prospective_validation_hash(
                jsonb_build_object(
                    'record_kind', 'evidence-continuity-lane-v1',
                    'id', p_id,
                    'epoch_id', p_epoch_id,
                    'lane_name', p_lane_name,
                    'action', p_action,
                    'identity_hash', p_identity_hash,
                    'parent_lane_id', p_parent_lane_id,
                    'boundary_at', prospective_validation_iso_utc(p_boundary_at),
                    'rationale', p_rationale
                )
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION validate_evidence_continuity_lane_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            epoch_row probability_pipeline_release_epochs%ROWTYPE;
            plan_lanes jsonb;
            planned_lane jsonb;
            parent_lane evidence_continuity_lanes%ROWTYPE;
            expected_record_hash text;
            latest_epoch_id bigint;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('stratus:probability-pipeline-release-epoch-v1', 0)
            );
            SELECT * INTO epoch_row
            FROM probability_pipeline_release_epochs
            WHERE id = NEW.epoch_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'evidence continuity lane epoch is unavailable';
            END IF;
            SELECT max(id) INTO latest_epoch_id FROM probability_pipeline_release_epochs;
            IF NEW.epoch_id IS DISTINCT FROM latest_epoch_id THEN
                RAISE EXCEPTION 'evidence continuity lane must belong to the latest epoch';
            END IF;
            SELECT lane_plans INTO plan_lanes
            FROM probability_pipeline_release_plans
            WHERE id = epoch_row.plan_id;
            SELECT value INTO planned_lane
            FROM jsonb_array_elements(plan_lanes) AS item(value)
            WHERE value->>'lane_name' = NEW.lane_name;
            IF planned_lane IS NULL
               OR planned_lane->>'action' IS DISTINCT FROM NEW.action
               OR planned_lane->>'identity_hash' IS DISTINCT FROM NEW.identity_hash
               OR planned_lane->'manifest' IS DISTINCT FROM NEW.manifest
               OR planned_lane->>'rationale' IS DISTINCT FROM NEW.rationale THEN
                RAISE EXCEPTION 'evidence continuity lane differs from reviewed plan';
            END IF;
            IF execution_cost_hash(NEW.manifest)
               IS DISTINCT FROM NEW.identity_hash THEN
                RAISE EXCEPTION 'evidence continuity lane identity hash mismatch';
            END IF;

            IF epoch_row.previous_epoch_id IS NULL THEN
                IF NEW.parent_lane_id IS NOT NULL
                   OR NEW.action NOT IN ('fork', 'freeze', 'not_applicable')
                   OR NEW.boundary_at IS DISTINCT FROM epoch_row.opened_at THEN
                    RAISE EXCEPTION 'first release lane must open a new boundary';
                END IF;
            ELSE
                SELECT * INTO parent_lane
                FROM evidence_continuity_lanes
                WHERE epoch_id = epoch_row.previous_epoch_id
                  AND lane_name = NEW.lane_name;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'evidence continuity parent lane is unavailable';
                END IF;
                IF NEW.parent_lane_id IS DISTINCT FROM parent_lane.id THEN
                    RAISE EXCEPTION 'evidence continuity parent lane mismatch';
                END IF;
                IF NEW.identity_hash = parent_lane.identity_hash THEN
                    IF NEW.action <> 'continue'
                       OR NEW.boundary_at IS DISTINCT FROM parent_lane.boundary_at THEN
                        RAISE EXCEPTION 'compatible lane must continue its parent boundary';
                    END IF;
                ELSIF NEW.action IN ('fork', 'freeze', 'not_applicable') THEN
                    IF NEW.boundary_at IS DISTINCT FROM epoch_row.opened_at THEN
                        RAISE EXCEPTION 'changed lane must use the new epoch boundary';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'changed lane cannot continue incompatible evidence';
                END IF;
            END IF;
            expected_record_hash := evidence_continuity_lane_record_hash(
                NEW.id,
                NEW.epoch_id,
                NEW.lane_name,
                NEW.action,
                NEW.identity_hash,
                NEW.parent_lane_id,
                NEW.boundary_at,
                NEW.rationale
            );
            IF NEW.record_hash IS NOT NULL
               AND NEW.record_hash <> repeat('0', 64)
               AND NEW.record_hash <> expected_record_hash THEN
                RAISE EXCEPTION 'evidence continuity lane record hash mismatch';
            END IF;
            NEW.record_hash := expected_record_hash;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_evidence_continuity_lanes_validate_insert "
        "BEFORE INSERT ON evidence_continuity_lanes FOR EACH ROW "
        "EXECUTE FUNCTION validate_evidence_continuity_lane_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION require_complete_evidence_continuity_epoch()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            lane_count integer;
            lane_name_count integer;
        BEGIN
            SELECT count(*), count(DISTINCT lane_name)
            INTO lane_count, lane_name_count
            FROM evidence_continuity_lanes
            WHERE epoch_id = NEW.id;
            IF lane_count <> 6 OR lane_name_count <> 6 THEN
                RAISE EXCEPTION 'evidence release epoch has an incomplete lane set';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_evidence_release_epoch_complete_lanes "
        "AFTER INSERT ON probability_pipeline_release_epochs DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION require_complete_evidence_continuity_epoch()"
    )
    op.execute(
        r"""
        CREATE FUNCTION reject_evidence_continuity_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'evidence continuity control records are append-only';
        END;
        $$
        """
    )
    for table_name in (
        "evidence_continuity_runtime",
        "probability_pipeline_release_plans",
        "evidence_continuity_lanes",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION reject_evidence_continuity_mutation()"
        )

    op.execute(
        r"""
        CREATE FUNCTION guard_execution_intent_release_epoch()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            active_epoch_id bigint;
            active_cohort text;
            active_image_digest text;
            runtime_image_digest text;
            evidence_opened_at timestamptz;
            signal_decision_at timestamptz;
            model_probability_at timestamptz;
            active_lane_count integer;
            blocked_lane_count integer;
            predictive_lane_id bigint;
            selection_lane_id bigint;
            execution_lane_id bigint;
            operational_lane_id bigint;
            station_lane_id bigint;
            truth_lane_id bigint;
        BEGIN
            -- Serialize intent admission with release activation.  Without the
            -- shared lock an A-cohort intent could validate concurrently and
            -- commit after epoch B had become authoritative.
            PERFORM pg_advisory_xact_lock(
                hashtextextended('stratus:probability-pipeline-release-epoch-v1', 0)
            );
            SELECT id, cohort, image_digest
            INTO active_epoch_id, active_cohort, active_image_digest
            FROM probability_pipeline_release_epochs
            ORDER BY id DESC
            LIMIT 1;
            IF active_cohort IS NULL THEN
                RAISE EXCEPTION
                    'execution intent blocked: probability pipeline release epoch is not activated';
            END IF;
            IF NEW.pipeline_cohort IS DISTINCT FROM active_cohort THEN
                RAISE EXCEPTION
                    'execution intent blocked: pipeline cohort is not the latest release epoch';
            END IF;
            runtime_image_digest := current_setting(
                'stratus.release_image_digest', true
            );
            IF runtime_image_digest IS NULL
               OR runtime_image_digest !~ '^[0-9a-f]{64}$'
               OR runtime_image_digest IS DISTINCT FROM active_image_digest THEN
                RAISE EXCEPTION
                    'execution intent blocked: runtime image is not the latest release epoch';
            END IF;
            SELECT count(*),
                   count(*) FILTER (WHERE action NOT IN ('continue', 'fork')),
                   max(id) FILTER (WHERE lane_name = 'predictive_validation'),
                   max(id) FILTER (WHERE lane_name = 'selection_policy'),
                   max(id) FILTER (WHERE lane_name = 'execution_evidence'),
                   max(id) FILTER (WHERE lane_name = 'operational_legacy'),
                   max(id) FILTER (WHERE lane_name = 'station_lifecycle'),
                   max(id) FILTER (WHERE lane_name = 'official_resolution_truth')
            INTO active_lane_count, blocked_lane_count,
                 predictive_lane_id, selection_lane_id, execution_lane_id,
                 operational_lane_id, station_lane_id, truth_lane_id
            FROM evidence_continuity_lanes
            WHERE epoch_id = active_epoch_id;
            IF active_lane_count <> 6 OR blocked_lane_count <> 0 THEN
                RAISE EXCEPTION
                    'execution intent blocked: release lane set is incomplete or frozen';
            END IF;
            SELECT boundary_at INTO evidence_opened_at
            FROM evidence_continuity_lanes
            WHERE epoch_id = active_epoch_id
              AND lane_name = 'predictive_validation';
            SELECT s.ts, mp.ts INTO signal_decision_at, model_probability_at
            FROM signals s
            JOIN model_probs mp ON mp.id = NEW.model_prob_id
            WHERE s.id = NEW.signal_id;
            IF evidence_opened_at IS NULL
               OR signal_decision_at IS NULL
               OR model_probability_at IS NULL
               OR signal_decision_at < evidence_opened_at
               OR model_probability_at < evidence_opened_at THEN
                RAISE EXCEPTION
                    'execution intent blocked: decision evidence predates release cohort';
            END IF;
            NEW.release_epoch_id := active_epoch_id;
            NEW.predictive_lane_id := predictive_lane_id;
            NEW.selection_lane_id := selection_lane_id;
            NEW.execution_lane_id := execution_lane_id;
            NEW.operational_lane_id := operational_lane_id;
            NEW.station_lane_id := station_lane_id;
            NEW.truth_lane_id := truth_lane_id;
            RETURN NEW;
        END;
        $$
        """
    )
    # Trigger names of the same timing/event fire alphabetically.  The 0040
    # prefix makes the cohort authority fail before the older, more expensive
    # execution-evidence validation without changing existing rows.
    op.execute(
        "CREATE TRIGGER trg_0040_execution_intent_release_epoch "
        "BEFORE INSERT ON execution_order_intents FOR EACH ROW "
        "EXECUTE FUNCTION guard_execution_intent_release_epoch()"
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM probability_pipeline_release_epochs)
               OR EXISTS (SELECT 1 FROM probability_pipeline_release_plans)
               OR EXISTS (SELECT 1 FROM evidence_continuity_lanes) THEN
                RAISE EXCEPTION
                    'refusing 0040 downgrade: release plan, epoch, or lane evidence exists';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER trg_0040_execution_intent_release_epoch "
        "ON execution_order_intents"
    )
    op.execute("DROP FUNCTION guard_execution_intent_release_epoch()")
    op.drop_constraint(
        "ck_execution_order_intents_continuity_attestation",
        "execution_order_intents",
        type_="check",
    )
    for column_name in (
        "predictive_lane_id",
        "selection_lane_id",
        "execution_lane_id",
        "operational_lane_id",
        "station_lane_id",
        "truth_lane_id",
    ):
        op.drop_constraint(
            f"fk_execution_order_intents_{column_name}",
            "execution_order_intents",
            type_="foreignkey",
        )
        op.drop_column("execution_order_intents", column_name)
    op.drop_constraint(
        "fk_execution_order_intents_release_epoch",
        "execution_order_intents",
        type_="foreignkey",
    )
    op.drop_column("execution_order_intents", "release_epoch_id")
    op.execute(
        "DROP TRIGGER trg_evidence_release_epoch_complete_lanes "
        "ON probability_pipeline_release_epochs"
    )
    op.execute("DROP FUNCTION require_complete_evidence_continuity_epoch()")
    op.execute(
        "DROP TRIGGER trg_evidence_continuity_lanes_validate_insert "
        "ON evidence_continuity_lanes"
    )
    op.execute("DROP FUNCTION validate_evidence_continuity_lane_insert()")
    for table_name in (
        "evidence_continuity_runtime",
        "probability_pipeline_release_plans",
        "evidence_continuity_lanes",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_append_only ON {table_name}")
    op.execute("DROP FUNCTION reject_evidence_continuity_mutation()")
    op.execute(
        "DROP TRIGGER trg_probability_pipeline_release_epochs_append_only "
        "ON probability_pipeline_release_epochs"
    )
    op.execute("DROP FUNCTION reject_probability_pipeline_release_epoch_mutation()")
    op.execute(
        "DROP TRIGGER trg_probability_pipeline_release_epochs_validate_insert "
        "ON probability_pipeline_release_epochs"
    )
    op.execute("DROP FUNCTION validate_probability_pipeline_release_epoch_insert()")
    op.execute(
        "DROP TRIGGER trg_probability_pipeline_release_plans_validate_insert "
        "ON probability_pipeline_release_plans"
    )
    op.execute("DROP FUNCTION validate_probability_pipeline_release_plan_insert()")
    op.execute(
        "DROP FUNCTION evidence_continuity_lane_record_hash("
        "bigint, bigint, text, text, text, bigint, timestamptz, text)"
    )
    op.execute(
        "DROP FUNCTION probability_pipeline_release_record_hash(bigint, timestamptz, text, text)"
    )
    op.execute(
        "DROP FUNCTION probability_pipeline_release_plan_hash("
        "text, text, text, text, text, bigint, text)"
    )
    op.execute(
        "DROP FUNCTION evidence_release_plan_record_hash(bigint, timestamptz, text)"
    )
    op.execute(
        "DROP FUNCTION evidence_release_plan_hash("
        "text, text, text, text, text, bigint, text, text, text, text)"
    )
    op.drop_table("evidence_continuity_lanes")
    op.drop_constraint(
        "fk_probability_pipeline_release_epochs_plan",
        "probability_pipeline_release_epochs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_probability_pipeline_release_epochs_plan",
        "probability_pipeline_release_epochs",
        type_="unique",
    )
    op.drop_column("probability_pipeline_release_epochs", "plan_id")
    op.drop_table("probability_pipeline_release_plans")
    op.drop_index(
        "ix_probability_pipeline_release_epochs_opened",
        table_name="probability_pipeline_release_epochs",
    )
    op.drop_table("probability_pipeline_release_epochs")
    op.drop_table("evidence_continuity_runtime")
