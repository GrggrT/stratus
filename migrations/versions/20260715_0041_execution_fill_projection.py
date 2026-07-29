"""Project prospective fills from immutable execution legs.

Revision ID: 20260715_0041
Revises: 20260715_0040
"""

from __future__ import annotations

from alembic import op

revision: str = "20260715_0041"
down_revision: str | None = "20260715_0040"
branch_labels: str | None = None
depends_on: str | None = None


def _fill_projection_sql(*, execution_legs: bool) -> str:
    extra_declarations = (
        """
            intent_row execution_order_intents%ROWTYPE;
            leg_count integer;
            leg_ineligible_count integer;
            leg_missing_fee_count integer;
            leg_total_shares numeric;
            leg_total_notional numeric;
            leg_total_fee numeric;
            leg_vwap numeric;
        """
        if execution_legs
        else ""
    )
    intent_lock = (
        """
            SELECT * INTO intent_row
            FROM execution_order_intents WHERE order_id = NEW.order_id
            FOR UPDATE;
        """
        if execution_legs
        else ""
    )
    execution_reason_checks = (
        """
            ELSIF intent_row.id IS NOT NULL AND leg_ineligible_count <> 0 THEN
                expected_reason := 'execution_evidence_ineligible';
            ELSIF intent_row.id IS NOT NULL AND leg_missing_fee_count <> 0 THEN
                expected_reason := 'execution_fee_unknown';
        """
        if execution_legs
        else ""
    )
    legacy_projection = """
            IF order_row.mode::text = 'live' THEN
                SELECT * INTO position_row
                FROM positions WHERE order_id = order_row.id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'prospective live enrollment position is missing';
                END IF;
                expected_fill_price := position_row.avg_price;
                expected_filled_usd := position_row.size_usd;
                expected_fee_usd := coalesce(position_row.fee_usd, 0);
                expected_size_shares := position_row.size_shares;
            ELSE
                expected_fill_price := order_row.price;
                expected_filled_usd := order_row.filled_usd;
                expected_fee_usd := coalesce(order_row.fee_usd, 0);
                expected_size_shares := order_row.filled_usd / order_row.price;
            END IF;
    """
    execution_projection = """
            IF order_row.mode::text = 'live' THEN
                SELECT * INTO position_row
                FROM positions WHERE order_id = order_row.id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'prospective live enrollment position is missing';
                END IF;
            END IF;

            IF intent_row.id IS NOT NULL THEN
                SELECT
                    count(*)::integer,
                    count(*) FILTER (WHERE NOT eligible)::integer,
                    count(*) FILTER (WHERE fee_usd IS NULL)::integer,
                    round(sum(shares), 6),
                    round(sum(notional_usd), 4),
                    round(sum(fee_usd), 5),
                    round(sum(shares * price) / nullif(sum(shares), 0), 6)
                INTO leg_count, leg_ineligible_count, leg_missing_fee_count,
                     leg_total_shares, leg_total_notional, leg_total_fee, leg_vwap
                FROM execution_fill_legs
                WHERE intent_id = intent_row.id;
                IF leg_count = 0 THEN
                    RAISE EXCEPTION
                        'prospective execution intent has no fill legs';
                END IF;
            ELSE
                leg_count := 0;
            END IF;

            IF leg_count > 0 THEN
                expected_fill_price := leg_vwap;
                expected_filled_usd := leg_total_notional;
                expected_size_shares := leg_total_shares;
                IF leg_missing_fee_count = 0 THEN
                    expected_fee_usd := leg_total_fee;
                ELSIF order_row.mode::text = 'live' THEN
                    expected_fee_usd := coalesce(position_row.fee_usd, 0);
                ELSE
                    expected_fee_usd := coalesce(order_row.fee_usd, 0);
                END IF;
            ELSIF order_row.mode::text = 'live' THEN
                expected_fill_price := position_row.avg_price;
                expected_filled_usd := position_row.size_usd;
                expected_fee_usd := coalesce(position_row.fee_usd, 0);
                expected_size_shares := position_row.size_shares;
            ELSE
                expected_fill_price := order_row.price;
                expected_filled_usd := order_row.filled_usd;
                expected_fee_usd := coalesce(order_row.fee_usd, 0);
                expected_size_shares := order_row.filled_usd / order_row.price;
            END IF;
    """
    projection = execution_projection if execution_legs else legacy_projection
    return f"""
        CREATE OR REPLACE FUNCTION validate_prospective_validation_enrollment_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row prospective_validation_runtime%ROWTYPE;
            order_row orders%ROWTYPE;
            signal_row signals%ROWTYPE;
            market_row markets%ROWTYPE;
            station_row stations%ROWTYPE;
            snapshot_row clob_market_snapshots%ROWTYPE;
            model_row model_probs%ROWTYPE;
            position_row positions%ROWTYPE;
            model_count integer;
            cohort_count integer;
            distinct_cohort_count integer;
            expected_cohort text;
            expected_eligible boolean;
            expected_reason text;
            expected_fill_price numeric;
            expected_filled_usd numeric;
            expected_fee_usd numeric;
            expected_size_shares numeric;
            expected_order_hash text;
            expected_signal_hash text;
            expected_model_hash text;
            expected_record_hash text;
            {extra_declarations}
        BEGIN
            SELECT * INTO runtime_row
            FROM prospective_validation_runtime WHERE id = 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective validation runtime is missing';
            END IF;
            {intent_lock}
            SELECT * INTO order_row FROM orders WHERE id = NEW.order_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective enrollment order is missing';
            END IF;
            SELECT * INTO signal_row FROM signals WHERE id = order_row.signal_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective enrollment signal is missing';
            END IF;
            SELECT * INTO market_row FROM markets WHERE id = signal_row.market_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective enrollment market is missing';
            END IF;
            IF market_row.station_id IS NOT NULL THEN
                SELECT * INTO station_row
                FROM stations WHERE id = market_row.station_id;
            END IF;
            IF signal_row.clob_snapshot_id IS NOT NULL THEN
                SELECT * INTO snapshot_row
                FROM clob_market_snapshots WHERE id = signal_row.clob_snapshot_id;
                IF FOUND AND snapshot_row.market_id IS DISTINCT FROM market_row.id THEN
                    RAISE EXCEPTION
                        'prospective enrollment snapshot belongs to another market';
                END IF;
            END IF;

            SELECT * INTO model_row
            FROM model_probs AS candidate
            WHERE candidate.market_id = signal_row.market_id
              AND candidate.ts <= signal_row.ts
              AND candidate.market_snapshot_id
                  IS NOT DISTINCT FROM signal_row.clob_snapshot_id
              AND candidate.market_anchor_protocol = signal_row.benchmark_protocol
            ORDER BY candidate.ts DESC, candidate.id DESC
            LIMIT 1;
            WITH candidates AS (
                SELECT prospective_validation_pipeline_cohort(components) AS cohort
                FROM model_probs AS candidate
                WHERE candidate.market_id = signal_row.market_id
                  AND candidate.ts <= signal_row.ts
                  AND candidate.market_snapshot_id
                      IS NOT DISTINCT FROM signal_row.clob_snapshot_id
                  AND candidate.market_anchor_protocol = signal_row.benchmark_protocol
            )
            SELECT count(*), count(cohort), count(DISTINCT cohort), min(cohort)
            INTO model_count, cohort_count, distinct_cohort_count, expected_cohort
            FROM candidates;
            IF model_count = 0
               OR cohort_count <> model_count
               OR distinct_cohort_count <> 1 THEN
                expected_cohort := NULL;
            END IF;

            {projection}

            IF order_row.status NOT IN ('filled', 'partially_filled')
               OR order_row.filled_at IS NULL
               OR order_row.filled_usd <= 0 THEN
                RAISE EXCEPTION 'prospective enrollment order is not a terminal fill';
            END IF;
            IF NEW.signal_id IS DISTINCT FROM signal_row.id
               OR NEW.market_id IS DISTINCT FROM market_row.id
               OR NEW.mode::text IS DISTINCT FROM order_row.mode::text
               OR NEW.side::text IS DISTINCT FROM order_row.side::text
               OR NEW.side::text IS DISTINCT FROM signal_row.side::text
               OR NEW.token_id IS DISTINCT FROM order_row.token_id
               OR NEW.market_date IS DISTINCT FROM market_row.market_date
               OR NEW.kind::text IS DISTINCT FROM market_row.kind::text
               OR NEW.benchmark_protocol IS DISTINCT FROM signal_row.benchmark_protocol THEN
                RAISE EXCEPTION 'prospective enrollment source identity mismatch';
            END IF;
            IF NEW.clob_snapshot_id IS DISTINCT FROM snapshot_row.id
               OR NEW.model_prob_id IS DISTINCT FROM model_row.id
               OR NEW.station_icao IS DISTINCT FROM station_row.icao
               OR NEW.pipeline_cohort IS DISTINCT FROM expected_cohort THEN
                RAISE EXCEPTION 'prospective enrollment provenance identity mismatch';
            END IF;
            IF NEW.fill_price IS DISTINCT FROM expected_fill_price
               OR NEW.filled_usd IS DISTINCT FROM expected_filled_usd
               OR NEW.fee_usd IS DISTINCT FROM expected_fee_usd
               OR abs(NEW.size_shares - expected_size_shares) > 0.0000005
               OR NEW.filled_at IS DISTINCT FROM order_row.filled_at THEN
                RAISE EXCEPTION 'prospective enrollment fill arithmetic mismatch';
            END IF;
            IF NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash
               OR NEW.filled_at < runtime_row.opened_at
               OR NEW.filled_at > NEW.enrolled_at THEN
                RAISE EXCEPTION 'prospective enrollment runtime boundary mismatch';
            END IF;

            expected_order_hash := prospective_validation_hash(
                jsonb_build_object(
                    'id', order_row.id,
                    'signal_id', order_row.signal_id,
                    'mode', order_row.mode::text,
                    'token_id', order_row.token_id,
                    'side', order_row.side::text,
                    'price', trim_scale(order_row.price)::text,
                    'size_usd', trim_scale(order_row.size_usd)::text,
                    'filled_usd', trim_scale(order_row.filled_usd)::text,
                    'fee_usd', CASE WHEN order_row.fee_usd IS NULL THEN NULL
                                    ELSE trim_scale(order_row.fee_usd)::text END,
                    'status', order_row.status,
                    'created_at', prospective_validation_iso_utc(order_row.created_at),
                    'filled_at', prospective_validation_iso_utc(order_row.filled_at)
                )
            );
            expected_signal_hash := prospective_validation_hash(
                jsonb_build_object(
                    'id', signal_row.id,
                    'market_id', signal_row.market_id,
                    'ts', prospective_validation_iso_utc(signal_row.ts),
                    'side', signal_row.side::text,
                    'model_prob', trim_scale(signal_row.model_prob)::text,
                    'market_price', trim_scale(signal_row.market_price)::text,
                    'edge', trim_scale(signal_row.edge)::text,
                    'kelly_frac', trim_scale(signal_row.kelly_frac)::text,
                    'size_usd', trim_scale(signal_row.size_usd)::text,
                    'confidence', signal_row.confidence::text,
                    'clob_snapshot_id', signal_row.clob_snapshot_id,
                    'token_id', signal_row.token_id,
                    'benchmark_protocol', signal_row.benchmark_protocol
                )
            );
            IF model_row.id IS NOT NULL THEN
                expected_model_hash := prospective_validation_hash(
                    jsonb_build_object(
                        'id', model_row.id,
                        'market_id', model_row.market_id,
                        'ts', prospective_validation_iso_utc(model_row.ts),
                        'prob', trim_scale(model_row.prob)::text,
                        'sigma', trim_scale(model_row.sigma)::text,
                        'horizon_h', trim_scale(model_row.horizon_h)::text,
                        'components', model_row.components,
                        'market_snapshot_id', model_row.market_snapshot_id,
                        'market_anchor_protocol', model_row.market_anchor_protocol
                    )
                );
            END IF;
            IF NEW.order_record_hash IS DISTINCT FROM expected_order_hash
               OR NEW.signal_record_hash IS DISTINCT FROM expected_signal_hash
               OR NEW.model_record_hash IS DISTINCT FROM expected_model_hash
               OR NEW.snapshot_record_hash IS DISTINCT FROM snapshot_row.record_hash THEN
                RAISE EXCEPTION 'prospective enrollment source hash mismatch';
            END IF;

            IF EXISTS (
                SELECT 1 FROM gamma_outcome_events
                WHERE event_id = market_row.event_id
            ) OR market_row.resolution_event_id IS NOT NULL
               OR market_row.resolved_outcome IS NOT NULL THEN
                expected_reason := 'outcome_already_known';
            {execution_reason_checks}
            ELSIF signal_row.benchmark_protocol IS DISTINCT FROM 'clob-dual-v1' THEN
                expected_reason := 'non_dual_protocol';
            ELSIF snapshot_row.id IS NULL OR NOT snapshot_row.eligible THEN
                expected_reason := 'snapshot_ineligible';
            ELSIF model_row.id IS NULL THEN
                expected_reason := 'model_probability_missing';
            ELSIF cohort_count <> model_count THEN
                expected_reason := 'pipeline_cohort_missing';
            ELSIF distinct_cohort_count <> 1 THEN
                expected_reason := 'model_anchor_ambiguous';
            ELSIF station_row.id IS NULL THEN
                expected_reason := 'station_missing';
            ELSIF order_row.token_id IS DISTINCT FROM signal_row.token_id THEN
                expected_reason := 'token_mismatch';
            ELSE
                expected_reason := 'eligible';
            END IF;
            expected_eligible := expected_reason = 'eligible';
            IF NEW.eligible IS DISTINCT FROM expected_eligible
               OR NEW.eligibility_reason IS DISTINCT FROM expected_reason THEN
                RAISE EXCEPTION 'prospective enrollment eligibility mismatch';
            END IF;

            expected_record_hash := prospective_validation_hash(
                jsonb_build_object(
                    'order_id', NEW.order_id,
                    'signal_id', NEW.signal_id,
                    'model_prob_id', NEW.model_prob_id,
                    'market_id', NEW.market_id,
                    'clob_snapshot_id', NEW.clob_snapshot_id,
                    'mode', NEW.mode::text,
                    'pipeline_cohort', NEW.pipeline_cohort,
                    'station_icao', NEW.station_icao,
                    'market_date', NEW.market_date::text,
                    'kind', NEW.kind::text,
                    'side', NEW.side::text,
                    'token_id', NEW.token_id,
                    'fill_price', trim_scale(NEW.fill_price)::text,
                    'filled_usd', trim_scale(NEW.filled_usd)::text,
                    'fee_usd', trim_scale(NEW.fee_usd)::text,
                    'size_shares', trim_scale(NEW.size_shares)::text,
                    'filled_at', prospective_validation_iso_utc(NEW.filled_at),
                    'enrolled_at', prospective_validation_iso_utc(NEW.enrolled_at),
                    'benchmark_protocol', NEW.benchmark_protocol,
                    'definition_version', NEW.definition_version,
                    'definition_hash', NEW.definition_hash,
                    'order_record_hash', NEW.order_record_hash,
                    'signal_record_hash', NEW.signal_record_hash,
                    'model_record_hash', NEW.model_record_hash,
                    'snapshot_record_hash', NEW.snapshot_record_hash,
                    'eligible', NEW.eligible,
                    'eligibility_reason', NEW.eligibility_reason
                )
            );
            IF NEW.record_hash IS DISTINCT FROM expected_record_hash THEN
                RAISE EXCEPTION 'prospective enrollment record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
    """


def _obligation_validation_sql(*, lock_intent: bool) -> str:
    intent_lock = " FOR UPDATE" if lock_intent else ""
    return f"""
        CREATE OR REPLACE FUNCTION execution_cost_validate_obligation_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            intent_row execution_order_intents%ROWTYPE;
            order_row orders%ROWTYPE;
        BEGIN
            NEW.created_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT intent_row
            FROM execution_order_intents WHERE id = NEW.intent_id{intent_lock};
            SELECT * INTO STRICT order_row FROM orders WHERE id = NEW.order_id;
            IF intent_row.order_id IS DISTINCT FROM NEW.order_id
               OR NEW.created_at < intent_row.intent_at THEN
                RAISE EXCEPTION 'execution cost obligation identity mismatch';
            END IF;
            IF NEW.state IS DISTINCT FROM 'pending'
               OR NEW.next_due_at IS NULL
               OR NEW.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'new execution cost obligation must begin pending';
            END IF;
            IF NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution cost obligation definition mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
    """


_FREEZE_FILL_LEGS_SQL = r"""
    CREATE FUNCTION execution_cost_reject_late_fill_leg_insert()
    RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE
        intent_order_id bigint;
    BEGIN
        -- Every terminal projection takes this same intent-row lock before it
        -- reads the leg set.  A concurrent append therefore either completes
        -- before projection or observes the durable freeze and is rejected.
        SELECT order_id INTO STRICT intent_order_id
        FROM execution_order_intents
        WHERE id = NEW.intent_id
        FOR UPDATE;
        IF EXISTS (
            SELECT 1 FROM execution_cost_obligations
            WHERE intent_id = NEW.intent_id OR order_id = intent_order_id
        ) OR EXISTS (
            SELECT 1 FROM prospective_validation_enrollments
            WHERE order_id = intent_order_id
        ) OR EXISTS (
            SELECT 1 FROM execution_cost_seals
            WHERE intent_id = NEW.intent_id OR order_id = intent_order_id
        ) THEN
            RAISE EXCEPTION
                'execution fill leg set is frozen after terminal projection';
        END IF;
        RETURN NEW;
    END;
    $$
"""


def upgrade() -> None:
    # Function-only, zero-backfill correction: existing immutable enrollments
    # are not rewritten. New post-0038 fills project from their atomic legs.
    op.execute(_fill_projection_sql(execution_legs=True))
    op.execute(_obligation_validation_sql(lock_intent=True))
    op.execute(_FREEZE_FILL_LEGS_SQL)
    op.execute(
        "CREATE TRIGGER trg_execution_fill_legs_freeze_projection_insert "
        "BEFORE INSERT ON execution_fill_legs FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_reject_late_fill_leg_insert()"
    )


def downgrade() -> None:
    # Restore the pre-0041 validation projection without touching evidence.
    op.execute(
        "DROP TRIGGER trg_execution_fill_legs_freeze_projection_insert ON execution_fill_legs"
    )
    op.execute("DROP FUNCTION execution_cost_reject_late_fill_leg_insert()")
    op.execute(_obligation_validation_sql(lock_intent=False))
    op.execute(_fill_projection_sql(execution_legs=False))
