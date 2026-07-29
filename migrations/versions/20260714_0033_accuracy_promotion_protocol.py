"""Add the immutable fixed-look accuracy-promotion protocol.

Revision ID: 20260714_0033
Revises: 20260714_0032
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0033"
down_revision: str | None = "20260714_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROTOCOL_VERSION = "fixed-range-brier-v3"
REGISTRATION_EPOCH = "fixed-range-brier-v3-20260714"
TEST_METHOD = "predictable-range-hoeffding-v1"

TRIAL_STATUS = postgresql.ENUM(
    "collecting",
    "passed",
    "failed",
    "activated",
    name="accuracy_promotion_trial_status",
    create_type=False,
)
LOOK_STATUS = postgresql.ENUM(
    "passed",
    "failed",
    name="accuracy_promotion_look_status",
    create_type=False,
)
WEATHER_KIND = postgresql.ENUM(
    "high",
    "low",
    name="weather_kind",
    create_type=False,
)


def _create_tables() -> None:
    op.create_table(
        "accuracy_promotion_trials",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("protocol_version", sa.String(length=64), nullable=False),
        sa.Column("registration_epoch", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=160), nullable=False),
        sa.Column("experiment_id", sa.String(length=160), nullable=False),
        sa.Column("candidate_id", sa.String(length=160), nullable=False),
        sa.Column("baseline_id", sa.String(length=160), nullable=False),
        sa.Column("pipeline_cohort", sa.String(length=128), nullable=False),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_blocks", sa.SmallInteger(), nullable=False),
        sa.Column("allocated_alpha", sa.Numeric(20, 18), nullable=False),
        sa.Column("minimum_brier_improvement", sa.Numeric(12, 10), nullable=False),
        sa.Column("test_method", sa.String(length=64), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("expert", sa.String(length=64), nullable=False),
        sa.Column("legacy_expert_version", sa.String(length=64), nullable=False),
        sa.Column("expert_version", sa.String(length=64), nullable=False),
        sa.Column("experiment_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("horizon_bucket", sa.String(length=16), nullable=False),
        sa.Column("kind", WEATHER_KIND, nullable=False),
        sa.Column("scope_key", sa.String(length=160), nullable=False),
        sa.Column("family_member_index", sa.Integer(), nullable=False),
        sa.Column("family_size", sa.Integer(), nullable=False),
        sa.Column("family_alpha", sa.Numeric(20, 18), nullable=False),
        sa.Column(
            "status", TRIAL_STATUS, nullable=False, server_default="collecting"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            f"protocol_version = '{PROTOCOL_VERSION}' AND "
            f"registration_epoch = '{REGISTRATION_EPOCH}' AND "
            "family_id = registration_epoch",
            name="ck_accuracy_promotion_trials_frozen_epoch",
        ),
        sa.CheckConstraint(
            "candidate_id <> baseline_id AND starts_at >= registered_at",
            name="ck_accuracy_promotion_trials_definition_order",
        ),
        sa.CheckConstraint(
            f"target_blocks = 60 AND test_method = '{TEST_METHOD}'",
            name="ck_accuracy_promotion_trials_fixed_look",
        ),
        sa.CheckConstraint(
            "family_size BETWEEN 1 AND 5000 AND "
            "family_member_index BETWEEN 1 AND family_size",
            name="ck_accuracy_promotion_trials_family_bounds",
        ),
        sa.CheckConstraint(
            "family_alpha = 0.050000000000000000 AND allocated_alpha > 0 AND "
            "allocated_alpha * family_size <= family_alpha AND "
            "family_alpha - (allocated_alpha * family_size) "
            "< 0.000000000001000000",
            name="ck_accuracy_promotion_trials_bonferroni",
        ),
        sa.CheckConstraint(
            "minimum_brier_improvement BETWEEN 0 AND 1",
            name="ck_accuracy_promotion_trials_minimum_improvement",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "experiment_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_trials_hashes",
        ),
        sa.UniqueConstraint(
            "experiment_id", name="uq_accuracy_promotion_trials_experiment"
        ),
        sa.UniqueConstraint(
            "definition_hash", name="uq_accuracy_promotion_trials_definition"
        ),
        sa.UniqueConstraint(
            "registration_epoch",
            "family_member_index",
            name="uq_accuracy_promotion_trials_family_member",
        ),
    )
    op.create_index(
        "ix_accuracy_promotion_trials_identity",
        "accuracy_promotion_trials",
        [
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
        ],
    )
    op.create_index(
        "ix_accuracy_promotion_trials_status",
        "accuracy_promotion_trials",
        ["status", "created_at"],
    )
    op.create_index(
        "uq_accuracy_promotion_trials_effective_activation",
        "accuracy_promotion_trials",
        [
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'activated'::accuracy_promotion_trial_status"),
    )

    op.create_table(
        "accuracy_promotion_evidence_members",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "trial_id",
            sa.BigInteger(),
            sa.ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "accuracy_prediction_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
        ),
        sa.Column(
            "baseline_model_prob_ids",
            postgresql.ARRAY(sa.BigInteger()),
            nullable=False,
        ),
        sa.Column("market_ids", postgresql.ARRAY(sa.BigInteger()), nullable=False),
        sa.Column(
            "resolution_event_id",
            sa.BigInteger(),
            sa.ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("prediction_ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("independent_block_key", sa.String(length=160), nullable=False),
        sa.Column("resolved_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("candidate_brier", sa.Numeric(12, 10), nullable=False),
        sa.Column("baseline_brier", sa.Numeric(12, 10), nullable=False),
        sa.Column("delta_brier", sa.Numeric(12, 10), nullable=False),
        sa.Column(
            "predictable_improvement_lower", sa.Numeric(30, 24), nullable=False
        ),
        sa.Column(
            "predictable_improvement_upper", sa.Numeric(30, 24), nullable=False
        ),
        sa.Column("predictable_range_squared", sa.Numeric(40, 30), nullable=False),
        sa.Column("resolution_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "cardinality(accuracy_prediction_ids) >= 2 AND "
            "cardinality(accuracy_prediction_ids) = cardinality(baseline_model_prob_ids) "
            "AND cardinality(accuracy_prediction_ids) = cardinality(market_ids)",
            name="ck_accuracy_promotion_members_vector_size",
        ),
        sa.CheckConstraint(
            "candidate_brier BETWEEN 0 AND 1 AND baseline_brier BETWEEN 0 AND 1 "
            "AND delta_brier BETWEEN -1 AND 1 AND "
            "predictable_improvement_lower BETWEEN -1 AND 1 AND "
            "predictable_improvement_upper BETWEEN -1 AND 1 AND "
            "predictable_improvement_lower <= predictable_improvement_upper AND "
            "-delta_brier BETWEEN predictable_improvement_lower "
            "AND predictable_improvement_upper AND predictable_range_squared >= 0",
            name="ck_accuracy_promotion_members_brier",
        ),
        sa.CheckConstraint(
            "resolution_vector_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_members_hashes",
        ),
        sa.UniqueConstraint(
            "trial_id",
            "independent_block_key",
            name="uq_accuracy_promotion_members_trial_block",
        ),
        sa.UniqueConstraint(
            "trial_id",
            "resolution_event_id",
            name="uq_accuracy_promotion_members_trial_event",
        ),
    )
    op.create_index(
        "ix_accuracy_promotion_members_trial_cutoff",
        "accuracy_promotion_evidence_members",
        ["trial_id", "resolved_cutoff", "independent_block_key"],
    )

    op.create_table(
        "accuracy_promotion_looks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "trial_id",
            sa.BigInteger(),
            sa.ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("look_number", sa.SmallInteger(), nullable=False),
        sa.Column("independent_block_count", sa.SmallInteger(), nullable=False),
        sa.Column("evidence_member_count", sa.SmallInteger(), nullable=False),
        sa.Column("mean_delta_brier", sa.Numeric(12, 10), nullable=False),
        sa.Column("wins", sa.SmallInteger(), nullable=False),
        sa.Column("losses", sa.SmallInteger(), nullable=False),
        sa.Column("ties", sa.SmallInteger(), nullable=False),
        sa.Column("nonwins", sa.SmallInteger(), nullable=False),
        sa.Column(
            "sum_predictable_range_squared", sa.Numeric(40, 30), nullable=False
        ),
        sa.Column("confidence_radius", sa.Numeric(30, 24), nullable=False),
        sa.Column("mean_improvement_lcb", sa.Numeric(30, 24), nullable=False),
        sa.Column("one_sided_p_bound", sa.Numeric(30, 24), nullable=False),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("status", LOOK_STATUS, nullable=False),
        sa.Column("evidence_set_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "look_number = 1 AND independent_block_count = 60 AND "
            "evidence_member_count = 60 AND complete AND "
            "wins + nonwins = 60 AND nonwins = losses + ties",
            name="ck_accuracy_promotion_looks_fixed_complete",
        ),
        sa.CheckConstraint(
            "wins >= 0 AND losses >= 0 AND ties >= 0 AND "
            "sum_predictable_range_squared >= 0 AND confidence_radius >= 0 AND "
            "mean_improvement_lcb BETWEEN -2 AND 1 AND "
            "one_sided_p_bound BETWEEN 0 AND 1",
            name="ck_accuracy_promotion_looks_range_bound",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_set_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_looks_hashes",
        ),
        sa.UniqueConstraint("trial_id", name="uq_accuracy_promotion_looks_trial"),
        sa.UniqueConstraint(
            "decision_hash", name="uq_accuracy_promotion_looks_decision"
        ),
    )

    op.create_table(
        "accuracy_promotion_activations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("activation_id", sa.String(length=160), nullable=False),
        sa.Column(
            "trial_id",
            sa.BigInteger(),
            sa.ForeignKey("accuracy_promotion_trials.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "look_id",
            sa.BigInteger(),
            sa.ForeignKey("accuracy_promotion_looks.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("family_id", sa.String(length=160), nullable=False),
        sa.Column("experiment_id", sa.String(length=160), nullable=False),
        sa.Column("candidate_id", sa.String(length=160), nullable=False),
        sa.Column("baseline_id", sa.String(length=160), nullable=False),
        sa.Column("pipeline_cohort", sa.String(length=128), nullable=False),
        sa.Column("expert", sa.String(length=64), nullable=False),
        sa.Column("legacy_expert_version", sa.String(length=64), nullable=False),
        sa.Column("expert_version", sa.String(length=64), nullable=False),
        sa.Column("experiment_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("horizon_bucket", sa.String(length=16), nullable=False),
        sa.Column("kind", WEATHER_KIND, nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("activated_by", sa.String(length=128), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND "
            "experiment_fingerprint ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_accuracy_promotion_activations_hashes",
        ),
        sa.CheckConstraint(
            "length(btrim(activation_id)) > 0 AND "
            "length(btrim(activated_by)) > 0",
            name="ck_accuracy_promotion_activations_identity",
        ),
        sa.UniqueConstraint(
            "activation_id", name="uq_accuracy_promotion_activations_id"
        ),
        sa.UniqueConstraint("trial_id", name="uq_accuracy_promotion_activations_trial"),
        sa.UniqueConstraint("look_id", name="uq_accuracy_promotion_activations_look"),
        sa.UniqueConstraint(
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "station_id",
            "horizon_bucket",
            "kind",
            name="uq_accuracy_promotion_activations_effective_scope",
        ),
    )


def _create_trial_triggers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION accuracy_promotion_hash_parts(VARIADIC parts text[])
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        STRICT
        AS $$
            SELECT encode(
                digest(
                    convert_to(
                        string_agg(
                            octet_length(value)::text || ':' || value,
                            '' ORDER BY ordinal
                        ),
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            )
            FROM unnest(parts) WITH ORDINALITY AS item(value, ordinal)
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION validate_accuracy_promotion_trial_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            family_row record;
            family_count integer;
            expected_scope text;
        BEGIN
            IF NEW.status::text IS DISTINCT FROM 'collecting' THEN
                RAISE EXCEPTION 'accuracy promotion trial must start collecting';
            END IF;
            IF length(btrim(NEW.family_id)) = 0
               OR length(btrim(NEW.experiment_id)) = 0
               OR length(btrim(NEW.candidate_id)) = 0
               OR length(btrim(NEW.baseline_id)) = 0
               OR length(btrim(NEW.pipeline_cohort)) = 0
               OR length(btrim(NEW.expert)) = 0
               OR length(btrim(NEW.legacy_expert_version)) = 0
               OR length(btrim(NEW.expert_version)) = 0
               OR length(btrim(NEW.horizon_bucket)) = 0
               OR length(btrim(NEW.test_method)) = 0 THEN
                RAISE EXCEPTION 'accuracy promotion trial identity cannot be empty';
            END IF;
            IF NEW.experiment_fingerprint !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'accuracy promotion identity requires lowercase SHA-256';
            END IF;
            IF NEW.expert_version IS DISTINCT FROM
               NEW.legacy_expert_version || '-sealed-v2' THEN
                RAISE EXCEPTION
                    'accuracy promotion sealed and legacy expert versions do not match';
            END IF;
            IF NEW.registered_at > clock_timestamp() + interval '5 seconds' THEN
                RAISE EXCEPTION 'accuracy promotion registration cannot be future-dated';
            END IF;
            expected_scope := concat_ws(
                ':', NEW.station_id::text, NEW.horizon_bucket, NEW.kind::text
            );
            IF NEW.scope_key IS DISTINCT FROM expected_scope THEN
                RAISE EXCEPTION 'accuracy promotion scope key mismatch';
            END IF;

            SELECT family_size, family_alpha, allocated_alpha, test_method,
                   protocol_version, registration_epoch
            INTO family_row
            FROM accuracy_promotion_trials
            LIMIT 1;
            IF FOUND AND (
                family_row.family_size IS DISTINCT FROM NEW.family_size
                OR family_row.family_alpha IS DISTINCT FROM NEW.family_alpha
                OR family_row.allocated_alpha IS DISTINCT FROM NEW.allocated_alpha
                OR family_row.test_method IS DISTINCT FROM NEW.test_method
                OR family_row.protocol_version IS DISTINCT FROM NEW.protocol_version
                OR family_row.registration_epoch IS DISTINCT FROM NEW.registration_epoch
            ) THEN
                RAISE EXCEPTION 'accuracy promotion global family definition mismatch';
            END IF;
            IF EXISTS (SELECT 1 FROM accuracy_promotion_evidence_members) THEN
                RAISE EXCEPTION
                    'accuracy promotion global family membership is already frozen';
            END IF;
            SELECT count(*) INTO family_count FROM accuracy_promotion_trials;
            IF family_count >= NEW.family_size THEN
                RAISE EXCEPTION 'accuracy promotion global family is already full';
            END IF;

            NEW.definition_hash := accuracy_promotion_hash_parts(
                'accuracy-promotion-definition-v3',
                NEW.protocol_version,
                NEW.registration_epoch,
                NEW.test_method,
                NEW.family_id,
                NEW.experiment_id,
                NEW.candidate_id,
                NEW.baseline_id,
                NEW.pipeline_cohort,
                to_char(
                    NEW.registered_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                ),
                to_char(
                    NEW.starts_at AT TIME ZONE 'UTC',
                    'YYYY-MM-DD"T"HH24:MI:SS.US"+00:00"'
                ),
                NEW.target_blocks::text,
                trim_scale(NEW.allocated_alpha)::text,
                trim_scale(NEW.minimum_brier_improvement)::text,
                NEW.expert,
                NEW.legacy_expert_version,
                NEW.expert_version,
                NEW.experiment_fingerprint,
                NEW.station_id::text,
                NEW.horizon_bucket,
                NEW.kind::text,
                NEW.scope_key,
                NEW.family_member_index::text,
                NEW.family_size::text,
                trim_scale(NEW.family_alpha)::text
            );
            NEW.created_at := clock_timestamp();
            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'schema', 'accuracy-promotion-trial-record-v1',
                            'definition_hash', NEW.definition_hash,
                            'protocol_version', NEW.protocol_version,
                            'registration_epoch', NEW.registration_epoch,
                            'expert', NEW.expert,
                            'legacy_expert_version', NEW.legacy_expert_version,
                            'expert_version', NEW.expert_version,
                            'experiment_fingerprint', NEW.experiment_fingerprint,
                            'station_id', NEW.station_id,
                            'horizon_bucket', NEW.horizon_bucket,
                            'kind', NEW.kind::text,
                            'scope_key', NEW.scope_key,
                            'family_member_index', NEW.family_member_index,
                            'family_size', NEW.family_size,
                            'family_alpha', NEW.family_alpha,
                            'allocated_alpha', NEW.allocated_alpha,
                            'minimum_brier_improvement',
                                NEW.minimum_brier_improvement,
                            'test_method', NEW.test_method,
                            'created_at', NEW.created_at
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_trials_validate_insert
        BEFORE INSERT ON accuracy_promotion_trials
        FOR EACH ROW EXECUTE FUNCTION validate_accuracy_promotion_trial_insert()
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION guard_accuracy_promotion_trial_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'accuracy promotion trial definition is immutable';
            END IF;
            IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM
               (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION 'accuracy promotion trial definition is immutable';
            END IF;
            IF OLD.status::text = 'collecting'
               AND NEW.status::text IN ('passed', 'failed')
               AND EXISTS (
                    SELECT 1
                    FROM accuracy_promotion_looks
                    WHERE trial_id = OLD.id
                      AND complete
                      AND status::text = NEW.status::text
               ) THEN
                RETURN NEW;
            END IF;
            IF OLD.status::text = 'passed'
               AND NEW.status::text = 'activated'
               AND EXISTS (
                    SELECT 1
                    FROM accuracy_promotion_activations
                    WHERE trial_id = OLD.id
               ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'accuracy promotion trial status is database-derived';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_trials_guard_mutation
        BEFORE UPDATE OR DELETE ON accuracy_promotion_trials
        FOR EACH ROW EXECUTE FUNCTION guard_accuracy_promotion_trial_mutation()
        """
    )


def _create_member_triggers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_accuracy_promotion_member_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            trial_row accuracy_promotion_trials%ROWTYPE;
            canonical_input_ids bigint[];
            input_count integer;
            input_distinct_count integer;
            selected_count integer;
            selected_event_count integer;
            selected_ts_count integer;
            selected_resolution_count integer;
            selected_input_hash_count integer;
            all_valid boolean;
            actual_prediction_ids bigint[];
            actual_baseline_ids bigint[];
            actual_market_ids bigint[];
            selected_event_id text;
            selected_prediction_ts timestamptz;
            selected_resolution_id bigint;
            selected_market_date date;
            earliest_generated_at timestamptz;
            latest_generated_at timestamptz;
            candidate_total numeric;
            baseline_total numeric;
            gamma_market_ids bigint[];
            gamma_market_count integer;
            gamma_vector_hash text;
            gamma_first_outcome_at timestamptz;
            stored_block_count integer;
            expected_block_key text;
            prediction_record_hashes text[];
        BEGIN
            SELECT * INTO trial_row
            FROM accuracy_promotion_trials
            WHERE id = NEW.trial_id;
            IF NOT FOUND OR trial_row.status::text IS DISTINCT FROM 'collecting' THEN
                RAISE EXCEPTION 'accuracy promotion evidence requires a collecting trial';
            END IF;
            IF (SELECT count(*) FROM accuracy_promotion_trials)
               IS DISTINCT FROM trial_row.family_size::bigint THEN
                RAISE EXCEPTION
                    'accuracy promotion global family is not fully preregistered';
            END IF;
            IF EXISTS (
                SELECT 1 FROM accuracy_promotion_looks WHERE trial_id = NEW.trial_id
            ) THEN
                RAISE EXCEPTION 'accuracy promotion evidence is sealed by its fixed look';
            END IF;
            SELECT count(*) INTO stored_block_count
            FROM accuracy_promotion_evidence_members
            WHERE trial_id = NEW.trial_id;
            IF stored_block_count >= trial_row.target_blocks THEN
                RAISE EXCEPTION 'accuracy promotion trial already has its fixed 60 blocks';
            END IF;

            SELECT array_agg(value ORDER BY value), count(*), count(DISTINCT value)
            INTO canonical_input_ids, input_count, input_distinct_count
            FROM unnest(NEW.accuracy_prediction_ids) AS value;
            IF input_count < 2 OR input_count IS DISTINCT FROM input_distinct_count THEN
                RAISE EXCEPTION
                    'accuracy promotion evidence requires distinct full-vector prediction ids';
            END IF;
            NEW.accuracy_prediction_ids := canonical_input_ids;

            SELECT
                count(*),
                count(DISTINCT market.event_id),
                count(DISTINCT prediction.ts),
                count(DISTINCT market.resolution_event_id),
                count(DISTINCT prediction.input_hash),
                bool_and(
                    prediction.evidence_eligible
                    AND prediction.point_in_time_valid
                    AND prediction.record_hash ~ '^[0-9a-f]{64}$'
                    AND prediction.expert = trial_row.expert
                    AND prediction.expert_version = trial_row.expert_version
                    AND prediction.experiment_fingerprint =
                        trial_row.experiment_fingerprint
                    AND prediction.distribution->>'evidence_protocol' = 'sealed-v2'
                    AND COALESCE(
                        NULLIF(
                            prediction.distribution->>'legacy_expert_version', ''
                        ),
                        CASE
                            WHEN prediction.expert_version LIKE '%-sealed-v2'
                            THEN left(
                                prediction.expert_version,
                                length(prediction.expert_version)
                                - length('-sealed-v2')
                            )
                        END
                    ) = trial_row.legacy_expert_version
                    AND prediction.expert_version =
                        trial_row.legacy_expert_version || '-sealed-v2'
                    AND prediction.distribution->>'baseline_pipeline_version' =
                        trial_row.pipeline_cohort
                    AND prediction.distribution->>'horizon_bucket' =
                        trial_row.horizon_bucket
                    AND market.station_id = trial_row.station_id
                    AND market.kind::text = trial_row.kind::text
                    AND market.status::text = 'resolved'
                    AND market.resolved_outcome IS NOT NULL
                    AND market.resolution_event_id IS NOT NULL
                    AND baseline.id = prediction.baseline_model_prob_id
                    AND baseline.market_id = prediction.market_id
                    AND baseline.ts = prediction.ts
                    AND baseline.market_anchor_protocol = 'clob-dual-v1'
                    AND baseline.market_snapshot_id IS NOT NULL
                    AND COALESCE(
                        NULLIF(baseline.components->'pipeline'->>'cohort', ''),
                        NULLIF(baseline.components->'pipeline'->>'version', '')
                    ) = trial_row.pipeline_cohort
                ),
                array_agg(prediction.id ORDER BY prediction.id),
                array_agg(baseline.id ORDER BY market.id),
                array_agg(market.id ORDER BY market.id),
                min(market.event_id),
                min(prediction.ts),
                min(market.resolution_event_id),
                min(market.market_date),
                min(prediction.generated_at),
                max(prediction.generated_at),
                sum(prediction.probability),
                sum(baseline.prob),
                array_agg(prediction.record_hash ORDER BY prediction.id)
            INTO
                selected_count,
                selected_event_count,
                selected_ts_count,
                selected_resolution_count,
                selected_input_hash_count,
                all_valid,
                actual_prediction_ids,
                actual_baseline_ids,
                actual_market_ids,
                selected_event_id,
                selected_prediction_ts,
                selected_resolution_id,
                selected_market_date,
                earliest_generated_at,
                latest_generated_at,
                candidate_total,
                baseline_total,
                prediction_record_hashes
            FROM unnest(canonical_input_ids) AS requested(prediction_id)
            JOIN accuracy_predictions AS prediction
              ON prediction.id = requested.prediction_id
            JOIN markets AS market ON market.id = prediction.market_id
            JOIN model_probs AS baseline
              ON baseline.id = prediction.baseline_model_prob_id;

            IF selected_count IS DISTINCT FROM input_count
               OR selected_event_count IS DISTINCT FROM 1
               OR selected_ts_count IS DISTINCT FROM 1
               OR selected_resolution_count IS DISTINCT FROM 1
               OR selected_input_hash_count IS DISTINCT FROM 1
               OR NOT COALESCE(all_valid, false)
               OR actual_prediction_ids IS DISTINCT FROM canonical_input_ids
               OR abs(candidate_total - 1) > 0.000001
               OR baseline_total < 0.99
               OR baseline_total > 1.01 THEN
                RAISE EXCEPTION
                    'accuracy promotion evidence crosses cohort/fingerprint/scope or is incomplete';
            END IF;

            SELECT
                array_agg((item->>'market_id')::bigint ORDER BY
                          (item->>'market_id')::bigint),
                event.market_count,
                event.outcome_vector_hash,
                event.first_outcome_at
            INTO
                gamma_market_ids,
                gamma_market_count,
                gamma_vector_hash,
                gamma_first_outcome_at
            FROM gamma_outcome_events AS event,
                 LATERAL jsonb_array_elements(event.outcome_vector) AS item
            WHERE event.id = selected_resolution_id
              AND event.event_id = selected_event_id
            GROUP BY event.id;
            IF NOT FOUND
               OR gamma_market_count IS DISTINCT FROM selected_count
               OR gamma_market_ids IS DISTINCT FROM actual_market_ids
               OR earliest_generated_at < trial_row.created_at
               OR earliest_generated_at < trial_row.starts_at
               OR latest_generated_at >= gamma_first_outcome_at
               OR gamma_first_outcome_at <= trial_row.starts_at THEN
                RAISE EXCEPTION
                    'accuracy promotion evidence is not a complete prospective Gamma vector';
            END IF;

            WITH probabilities AS (
                SELECT
                    prediction.probability AS candidate_probability,
                    baseline.prob / baseline_total AS baseline_probability,
                    market.resolved_outcome
                FROM unnest(canonical_input_ids) AS requested(prediction_id)
                JOIN accuracy_predictions AS prediction
                  ON prediction.id = requested.prediction_id
                JOIN markets AS market ON market.id = prediction.market_id
                JOIN model_probs AS baseline
                  ON baseline.id = prediction.baseline_model_prob_id
            ), predictable_anchor AS (
                SELECT avg(
                    power(baseline_probability, 2)
                    - power(candidate_probability, 2)
                ) AS square_difference_mean
                FROM probabilities
            )
            SELECT
                avg(power(candidate_probability -
                    CASE WHEN resolved_outcome THEN 1 ELSE 0 END, 2)),
                avg(power(baseline_probability -
                    CASE WHEN resolved_outcome THEN 1 ELSE 0 END, 2)),
                min(
                    square_difference_mean
                    + 2 * (candidate_probability - baseline_probability)
                      / selected_count
                ),
                max(
                    square_difference_mean
                    + 2 * (candidate_probability - baseline_probability)
                      / selected_count
                )
            INTO
                NEW.candidate_brier,
                NEW.baseline_brier,
                NEW.predictable_improvement_lower,
                NEW.predictable_improvement_upper
            FROM probabilities CROSS JOIN predictable_anchor;

            expected_block_key := concat_ws(
                ':',
                trial_row.station_id::text,
                selected_market_date::text,
                trial_row.kind::text,
                trial_row.horizon_bucket
            );
            NEW.baseline_model_prob_ids := actual_baseline_ids;
            NEW.market_ids := actual_market_ids;
            NEW.resolution_event_id := selected_resolution_id;
            NEW.event_id := selected_event_id;
            NEW.prediction_ts := selected_prediction_ts;
            NEW.independent_block_key := expected_block_key;
            NEW.resolved_cutoff := gamma_first_outcome_at;
            NEW.delta_brier := NEW.candidate_brier - NEW.baseline_brier;
            NEW.predictable_range_squared := power(
                NEW.predictable_improvement_upper
                - NEW.predictable_improvement_lower,
                2
            );
            IF -NEW.delta_brier < NEW.predictable_improvement_lower
               OR -NEW.delta_brier > NEW.predictable_improvement_upper THEN
                RAISE EXCEPTION
                    'observed Brier improvement is outside its predictable range';
            END IF;
            NEW.resolution_vector_hash := gamma_vector_hash;
            NEW.evidence_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'schema', 'accuracy-promotion-block-evidence-v1',
                            'trial_definition_hash', trial_row.definition_hash,
                            'accuracy_prediction_ids', to_jsonb(actual_prediction_ids),
                            'prediction_record_hashes', to_jsonb(prediction_record_hashes),
                            'baseline_model_prob_ids', to_jsonb(actual_baseline_ids),
                            'market_ids', to_jsonb(actual_market_ids),
                            'resolution_event_id', selected_resolution_id,
                            'resolution_vector_hash', gamma_vector_hash,
                            'independent_block_key', expected_block_key,
                            'resolved_cutoff', gamma_first_outcome_at,
                            'candidate_brier', NEW.candidate_brier,
                            'baseline_brier', NEW.baseline_brier,
                            'predictable_improvement_lower',
                                NEW.predictable_improvement_lower,
                            'predictable_improvement_upper',
                                NEW.predictable_improvement_upper,
                            'predictable_range_squared',
                                NEW.predictable_range_squared
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            NEW.recorded_at := clock_timestamp();
            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'schema', 'accuracy-promotion-block-record-v1',
                            'trial_definition_hash', trial_row.definition_hash,
                            'evidence_hash', NEW.evidence_hash,
                            'recorded_at', NEW.recorded_at
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_members_validate_insert
        BEFORE INSERT ON accuracy_promotion_evidence_members
        FOR EACH ROW EXECUTE FUNCTION validate_accuracy_promotion_member_insert()
        """
    )


def _create_look_and_activation_triggers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_accuracy_promotion_look_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            trial_row accuracy_promotion_trials%ROWTYPE;
            member_count integer;
            block_count integer;
            family_count integer;
            stored_mean numeric;
            stored_evidence_hash text;
            stored_wins integer;
            stored_losses integer;
            stored_ties integer;
            stored_range_squared numeric;
            mean_improvement numeric;
            raw_radius numeric;
            positive_gap numeric;
            -- Outward-round transcendental bounds at 18 decimal places.  This
            -- is decision-conservative and reproducible in the Python trust
            -- domain; the NUMERIC(30, 24) columns retain storage headroom.
            bound_scale numeric := 1000000000000000000;
            derived_status text;
        BEGIN
            SELECT * INTO trial_row
            FROM accuracy_promotion_trials
            WHERE id = NEW.trial_id;
            IF NOT FOUND OR trial_row.status::text IS DISTINCT FROM 'collecting' THEN
                RAISE EXCEPTION 'accuracy promotion look requires a collecting trial';
            END IF;
            SELECT count(*) INTO family_count FROM accuracy_promotion_trials;
            IF family_count IS DISTINCT FROM trial_row.family_size THEN
                RAISE EXCEPTION
                    'accuracy promotion global family is not fully preregistered';
            END IF;
            SELECT
                count(*),
                count(DISTINCT independent_block_key),
                avg(delta_brier),
                count(*) FILTER (WHERE delta_brier < 0),
                count(*) FILTER (WHERE delta_brier > 0),
                count(*) FILTER (WHERE delta_brier = 0),
                sum(predictable_range_squared),
                encode(
                    digest(
                        convert_to(
                            string_agg(
                                evidence_hash,
                                '' ORDER BY resolved_cutoff, independent_block_key
                            ),
                            'UTF8'
                        ),
                        'sha256'
                    ),
                    'hex'
                )
            INTO member_count, block_count, stored_mean, stored_wins,
                 stored_losses, stored_ties, stored_range_squared,
                 stored_evidence_hash
            FROM accuracy_promotion_evidence_members
            WHERE trial_id = NEW.trial_id;
            IF member_count IS DISTINCT FROM trial_row.target_blocks::bigint
               OR block_count IS DISTINCT FROM trial_row.target_blocks::bigint THEN
                RAISE EXCEPTION 'accuracy promotion fixed look requires exactly 60 blocks';
            END IF;
            IF NEW.definition_hash IS DISTINCT FROM trial_row.definition_hash THEN
                RAISE EXCEPTION 'accuracy promotion look definition hash mismatch';
            END IF;
            NEW.look_number := 1;
            NEW.independent_block_count := block_count;
            NEW.evidence_member_count := member_count;
            NEW.mean_delta_brier := stored_mean;
            NEW.wins := stored_wins;
            NEW.losses := stored_losses;
            NEW.ties := stored_ties;
            NEW.nonwins := stored_losses + stored_ties;
            NEW.sum_predictable_range_squared := stored_range_squared;
            mean_improvement := -NEW.mean_delta_brier;
            IF stored_range_squared = 0 THEN
                NEW.confidence_radius := 0;
                IF mean_improvement > trial_row.minimum_brier_improvement THEN
                    NEW.one_sided_p_bound := 0;
                ELSE
                    NEW.one_sided_p_bound := 1;
                END IF;
            ELSE
                raw_radius := sqrt(
                    (
                        ln(
                            (1::numeric(100, 80))
                            / trial_row.allocated_alpha::numeric(100, 80)
                        )::numeric(100, 80)
                        * stored_range_squared::numeric(100, 80)
                        / (
                            2::numeric(100, 80)
                            * power(
                                trial_row.target_blocks::numeric(100, 80),
                                2
                            )
                        )
                    )::numeric(100, 80)
                );
                NEW.confidence_radius := ceil(raw_radius * bound_scale)
                    / bound_scale;
                positive_gap := greatest(
                    0,
                    mean_improvement - trial_row.minimum_brier_improvement
                );
                NEW.one_sided_p_bound := least(
                    1,
                    ceil(
                        exp(
                            (
                                -2::numeric(100, 80)
                                * power(
                                    trial_row.target_blocks::numeric(100, 80),
                                    2
                                )
                                * power(positive_gap::numeric(100, 80), 2)
                                / stored_range_squared::numeric(100, 80)
                            )::numeric(100, 80)
                        ) * bound_scale
                    ) / bound_scale
                );
            END IF;
            NEW.mean_improvement_lcb := floor(
                (mean_improvement - NEW.confidence_radius) * bound_scale
            ) / bound_scale;
            NEW.complete := true;
            NEW.evidence_set_hash := stored_evidence_hash;
            NEW.evaluated_at := clock_timestamp();
            IF NEW.mean_improvement_lcb > trial_row.minimum_brier_improvement THEN
                derived_status := 'passed';
            ELSE
                derived_status := 'failed';
            END IF;
            NEW.status := derived_status::accuracy_promotion_look_status;
            NEW.decision_hash := accuracy_promotion_hash_parts(
                'accuracy-promotion-decision-v3',
                trial_row.protocol_version,
                trial_row.registration_epoch,
                trial_row.test_method,
                NEW.definition_hash,
                derived_status,
                trial_row.target_blocks::text,
                trial_row.target_blocks::text,
                NEW.evidence_set_hash,
                trim_scale(NEW.mean_delta_brier)::text,
                NEW.wins::text,
                NEW.losses::text,
                NEW.ties::text,
                NEW.nonwins::text,
                trim_scale(NEW.sum_predictable_range_squared)::text,
                trim_scale(NEW.confidence_radius)::text,
                trim_scale(NEW.mean_improvement_lcb)::text,
                trim_scale(NEW.one_sided_p_bound)::text
            );
            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'schema', 'accuracy-promotion-look-record-v1',
                            'definition_hash', NEW.definition_hash,
                            'decision_hash', NEW.decision_hash,
                            'look_number', NEW.look_number,
                            'independent_block_count', NEW.independent_block_count,
                            'evidence_member_count', NEW.evidence_member_count,
                            'mean_delta_brier', NEW.mean_delta_brier,
                            'wins', NEW.wins,
                            'losses', NEW.losses,
                            'ties', NEW.ties,
                            'nonwins', NEW.nonwins,
                            'sum_predictable_range_squared',
                                NEW.sum_predictable_range_squared,
                            'confidence_radius', NEW.confidence_radius,
                            'mean_improvement_lcb', NEW.mean_improvement_lcb,
                            'one_sided_p_bound', NEW.one_sided_p_bound,
                            'status', derived_status,
                            'evidence_set_hash', NEW.evidence_set_hash,
                            'evaluated_at', NEW.evaluated_at
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_looks_validate_insert
        BEFORE INSERT ON accuracy_promotion_looks
        FOR EACH ROW EXECUTE FUNCTION validate_accuracy_promotion_look_insert()
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION project_accuracy_promotion_look_status()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE accuracy_promotion_trials
            SET status = NEW.status::text::accuracy_promotion_trial_status
            WHERE id = NEW.trial_id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_looks_project_status
        AFTER INSERT ON accuracy_promotion_looks
        FOR EACH ROW EXECUTE FUNCTION project_accuracy_promotion_look_status()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_accuracy_promotion_activation_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            trial_row accuracy_promotion_trials%ROWTYPE;
            look_row accuracy_promotion_looks%ROWTYPE;
        BEGIN
            SELECT * INTO trial_row
            FROM accuracy_promotion_trials
            WHERE id = NEW.trial_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'accuracy promotion activation trial is missing';
            END IF;
            SELECT * INTO look_row
            FROM accuracy_promotion_looks
            WHERE id = NEW.look_id;
            IF NOT FOUND
               OR trial_row.status::text IS DISTINCT FROM 'passed'
               OR look_row.trial_id IS DISTINCT FROM trial_row.id
               OR NOT look_row.complete
               OR look_row.status::text IS DISTINCT FROM 'passed'
               OR look_row.independent_block_count IS DISTINCT FROM trial_row.target_blocks THEN
                RAISE EXCEPTION
                    'accuracy promotion activation requires a passed complete fixed look';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM accuracy_promotion_trials
                WHERE registration_epoch = trial_row.registration_epoch
                  AND status::text = 'collecting'
            ) THEN
                RAISE EXCEPTION
                    'accuracy promotion activation waits for every family trial';
            END IF;
            IF NEW.definition_hash IS DISTINCT FROM trial_row.definition_hash
               OR NEW.decision_hash IS DISTINCT FROM look_row.decision_hash
               OR NEW.family_id IS DISTINCT FROM trial_row.family_id
               OR NEW.experiment_id IS DISTINCT FROM trial_row.experiment_id
               OR NEW.candidate_id IS DISTINCT FROM trial_row.candidate_id
               OR NEW.baseline_id IS DISTINCT FROM trial_row.baseline_id
               OR NEW.pipeline_cohort IS DISTINCT FROM trial_row.pipeline_cohort THEN
                RAISE EXCEPTION 'accuracy promotion activation manifest mismatch';
            END IF;
            IF NEW.activated_at < look_row.evaluated_at
               OR NEW.activated_at > clock_timestamp() + interval '5 seconds'
               OR length(btrim(NEW.activation_id)) = 0
               OR length(btrim(NEW.activated_by)) = 0 THEN
                RAISE EXCEPTION 'accuracy promotion activation audit time/identity is invalid';
            END IF;

            NEW.expert := trial_row.expert;
            NEW.legacy_expert_version := trial_row.legacy_expert_version;
            NEW.expert_version := trial_row.expert_version;
            NEW.experiment_fingerprint := trial_row.experiment_fingerprint;
            NEW.station_id := trial_row.station_id;
            NEW.horizon_bucket := trial_row.horizon_bucket;
            NEW.kind := trial_row.kind;
            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'schema', 'accuracy-promotion-activation-record-v1',
                            'activation_id', NEW.activation_id,
                            'definition_hash', NEW.definition_hash,
                            'decision_hash', NEW.decision_hash,
                            'family_id', NEW.family_id,
                            'experiment_id', NEW.experiment_id,
                            'candidate_id', NEW.candidate_id,
                            'baseline_id', NEW.baseline_id,
                            'pipeline_cohort', NEW.pipeline_cohort,
                            'expert', NEW.expert,
                            'legacy_expert_version', NEW.legacy_expert_version,
                            'expert_version', NEW.expert_version,
                            'experiment_fingerprint', NEW.experiment_fingerprint,
                            'station_id', NEW.station_id,
                            'horizon_bucket', NEW.horizon_bucket,
                            'kind', NEW.kind::text,
                            'activated_at', NEW.activated_at,
                            'activated_by', NEW.activated_by
                        )::text,
                        'UTF8'
                    ),
                    'sha256'
                ),
                'hex'
            );
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_activations_validate_insert
        BEFORE INSERT ON accuracy_promotion_activations
        FOR EACH ROW EXECUTE FUNCTION validate_accuracy_promotion_activation_insert()
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION project_accuracy_promotion_activation_status()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE accuracy_promotion_trials
            SET status = 'activated'::accuracy_promotion_trial_status
            WHERE id = NEW.trial_id;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_accuracy_promotion_activations_project_status
        AFTER INSERT ON accuracy_promotion_activations
        FOR EACH ROW EXECUTE FUNCTION project_accuracy_promotion_activation_status()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION enforce_accuracy_promotion_family_activation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            selected_family text;
            collecting_count integer;
            passing_count integer;
            activation_count integer;
        BEGIN
            SELECT family_id INTO selected_family
            FROM accuracy_promotion_trials
            WHERE id = NEW.trial_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'accuracy promotion family trial is missing';
            END IF;

            SELECT count(*) INTO collecting_count
            FROM accuracy_promotion_trials
            WHERE family_id = selected_family
              AND status::text = 'collecting';
            IF collecting_count > 0 THEN
                RETURN NEW;
            END IF;

            SELECT count(*) INTO passing_count
            FROM accuracy_promotion_looks AS look
            JOIN accuracy_promotion_trials AS trial ON trial.id = look.trial_id
            WHERE trial.family_id = selected_family
              AND look.status::text = 'passed';
            SELECT count(*) INTO activation_count
            FROM accuracy_promotion_activations AS activation
            JOIN accuracy_promotion_trials AS trial ON trial.id = activation.trial_id
            WHERE trial.family_id = selected_family;

            IF activation_count IS DISTINCT FROM passing_count
               OR EXISTS (
                    SELECT 1
                    FROM accuracy_promotion_looks AS look
                    JOIN accuracy_promotion_trials AS trial
                      ON trial.id = look.trial_id
                    LEFT JOIN accuracy_promotion_activations AS activation
                      ON activation.trial_id = trial.id
                     AND activation.look_id = look.id
                    WHERE trial.family_id = selected_family
                      AND look.status::text = 'passed'
                      AND activation.id IS NULL
               )
               OR EXISTS (
                    SELECT 1
                    FROM accuracy_promotion_activations AS activation
                    JOIN accuracy_promotion_trials AS trial
                      ON trial.id = activation.trial_id
                    LEFT JOIN accuracy_promotion_looks AS look
                      ON look.id = activation.look_id
                     AND look.trial_id = trial.id
                     AND look.status::text = 'passed'
                    WHERE trial.family_id = selected_family
                      AND look.id IS NULL
               ) THEN
                RAISE EXCEPTION
                    'passing accuracy promotion family must activate atomically';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    for table in ("accuracy_promotion_looks", "accuracy_promotion_activations"):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER trg_{table}_family_atomic
            AFTER INSERT ON {table}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_accuracy_promotion_family_activation()
            """
        )

    op.execute(
        r"""
        CREATE FUNCTION reject_accuracy_promotion_ledger_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is an append-only accuracy promotion ledger', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in (
        "accuracy_promotion_evidence_members",
        "accuracy_promotion_looks",
        "accuracy_promotion_activations",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_accuracy_promotion_ledger_mutation()
            """
        )


def upgrade() -> None:
    bind = op.get_bind()
    postgresql.ENUM(
        "collecting",
        "passed",
        "failed",
        "activated",
        name="accuracy_promotion_trial_status",
    ).create(bind, checkfirst=True)
    postgresql.ENUM(
        "passed",
        "failed",
        name="accuracy_promotion_look_status",
    ).create(bind, checkfirst=True)
    _create_tables()
    _create_trial_triggers()
    _create_member_triggers()
    _create_look_and_activation_triggers()


def downgrade() -> None:
    # The registered family definition is itself prospective evidence. An
    # epoch/protocol change must be a new migration, never a rollback that
    # silently makes the same experiment identifiers reusable.
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM accuracy_promotion_trials)
               OR EXISTS (SELECT 1 FROM accuracy_promotion_evidence_members)
               OR EXISTS (SELECT 1 FROM accuracy_promotion_looks)
               OR EXISTS (SELECT 1 FROM accuracy_promotion_activations) THEN
                RAISE EXCEPTION
                    'cannot downgrade accuracy promotion protocol after evidence exists';
            END IF;
        END;
        $$
        """
    )

    for table in (
        "accuracy_promotion_activations",
        "accuracy_promotion_looks",
        "accuracy_promotion_evidence_members",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_accuracy_promotion_ledger_mutation()")

    for table in ("accuracy_promotion_activations", "accuracy_promotion_looks"):
        op.execute(f"DROP TRIGGER trg_{table}_family_atomic ON {table}")
    op.execute("DROP FUNCTION enforce_accuracy_promotion_family_activation()")

    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_activations_project_status "
        "ON accuracy_promotion_activations"
    )
    op.execute("DROP FUNCTION project_accuracy_promotion_activation_status()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_activations_validate_insert "
        "ON accuracy_promotion_activations"
    )
    op.execute("DROP FUNCTION validate_accuracy_promotion_activation_insert()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_looks_project_status "
        "ON accuracy_promotion_looks"
    )
    op.execute("DROP FUNCTION project_accuracy_promotion_look_status()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_looks_validate_insert "
        "ON accuracy_promotion_looks"
    )
    op.execute("DROP FUNCTION validate_accuracy_promotion_look_insert()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_members_validate_insert "
        "ON accuracy_promotion_evidence_members"
    )
    op.execute("DROP FUNCTION validate_accuracy_promotion_member_insert()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_trials_guard_mutation "
        "ON accuracy_promotion_trials"
    )
    op.execute("DROP FUNCTION guard_accuracy_promotion_trial_mutation()")
    op.execute(
        "DROP TRIGGER trg_accuracy_promotion_trials_validate_insert "
        "ON accuracy_promotion_trials"
    )
    op.execute("DROP FUNCTION validate_accuracy_promotion_trial_insert()")
    op.execute("DROP FUNCTION accuracy_promotion_hash_parts(VARIADIC text[])")

    op.drop_table("accuracy_promotion_activations")
    op.drop_table("accuracy_promotion_looks")
    op.drop_index(
        "ix_accuracy_promotion_members_trial_cutoff",
        table_name="accuracy_promotion_evidence_members",
    )
    op.drop_table("accuracy_promotion_evidence_members")
    op.drop_index(
        "uq_accuracy_promotion_trials_effective_activation",
        table_name="accuracy_promotion_trials",
    )
    op.drop_index(
        "ix_accuracy_promotion_trials_status",
        table_name="accuracy_promotion_trials",
    )
    op.drop_index(
        "ix_accuracy_promotion_trials_identity",
        table_name="accuracy_promotion_trials",
    )
    op.drop_table("accuracy_promotion_trials")
    LOOK_STATUS.drop(op.get_bind(), checkfirst=True)
    TRIAL_STATUS.drop(op.get_bind(), checkfirst=True)
