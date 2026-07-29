\set ON_ERROR_STOP on
\pset pager off
\pset null '<null>'

BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

SELECT
    clock_timestamp() AS audited_at,
    current_database() AS database_name,
    current_setting('server_version') AS postgres_version,
    (SELECT version_num FROM alembic_version) AS alembic_head;

SELECT
    intent.mode,
    intent.release_epoch_id,
    obligation.state,
    count(*) AS obligations,
    min(obligation.next_due_at) AS oldest_next_due_at,
    max(obligation.finalized_at) AS latest_finalized_at
FROM execution_cost_obligations AS obligation
JOIN execution_order_intents AS intent ON intent.id = obligation.intent_id
GROUP BY intent.mode, intent.release_epoch_id, obligation.state
ORDER BY intent.mode, intent.release_epoch_id, obligation.state;

WITH required AS (
    SELECT
        leg.id AS fill_leg_id,
        leg.intent_id,
        leg.mode,
        intent.release_epoch_id,
        horizon.seconds AS horizon_seconds,
        leg.fill_effective_at + horizon.seconds * interval '1 second' AS target_at,
        leg.fill_effective_at
            + (horizon.seconds + runtime.mark_grace_seconds) * interval '1 second'
            AS grace_at
    FROM execution_fill_legs AS leg
    JOIN execution_order_intents AS intent ON intent.id = leg.intent_id
    CROSS JOIN execution_cost_runtime AS runtime
    CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) AS horizon(seconds)
    WHERE leg.fill_effective_at IS NOT NULL
),
reconciled AS (
    SELECT
        required.*,
        mark.id AS mark_id,
        mark.status AS mark_status,
        mark.clob_snapshot_id AS actual_snapshot_id,
        mark.clob_snapshot_hash AS actual_snapshot_hash,
        mark.sealed_at AS mark_sealed_at,
        expected.id AS expected_snapshot_id,
        expected.record_hash AS expected_snapshot_hash,
        expected.first_seen_at AS expected_snapshot_at
    FROM required
    LEFT JOIN execution_postfill_marks AS mark
      ON mark.fill_leg_id = required.fill_leg_id
     AND mark.horizon_seconds = required.horizon_seconds
    LEFT JOIN LATERAL (
        SELECT
            snapshot.id,
            snapshot.record_hash,
            batch.first_seen_at
        FROM execution_fill_legs AS source_leg
        JOIN clob_market_snapshots AS snapshot
          ON snapshot.market_id = source_leg.market_id
        JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
        WHERE source_leg.id = required.fill_leg_id
          AND snapshot.complete
          AND snapshot.eligible
          AND batch.first_seen_at BETWEEN required.target_at AND required.grace_at
          AND CASE
              WHEN source_leg.side = 'buy_yes'::trade_side THEN
                  snapshot.yes_token_id = source_leg.token_id
                  AND snapshot.yes_best_bid IS NOT NULL
                  AND snapshot.yes_best_ask IS NOT NULL
                  AND snapshot.yes_midpoint IS NOT NULL
              ELSE
                  snapshot.no_token_id = source_leg.token_id
                  AND snapshot.no_best_bid IS NOT NULL
                  AND snapshot.no_best_ask IS NOT NULL
                  AND snapshot.no_midpoint IS NOT NULL
          END
        ORDER BY batch.first_seen_at, snapshot.id
        LIMIT 1
    ) AS expected ON true
)
SELECT
    mode,
    release_epoch_id,
    horizon_seconds,
    count(*) AS required_marks,
    count(*) FILTER (WHERE mark_id IS NULL) AS unresolved,
    count(*) FILTER (
        WHERE mark_id IS NULL AND grace_at < clock_timestamp()
    ) AS unresolved_after_grace,
    count(*) FILTER (WHERE mark_status = 'observed') AS observed,
    count(*) FILTER (WHERE mark_status = 'missing') AS missing,
    count(*) FILTER (
        WHERE mark_status = 'observed'
          AND (
              actual_snapshot_id IS DISTINCT FROM expected_snapshot_id
              OR actual_snapshot_hash IS DISTINCT FROM expected_snapshot_hash
          )
    ) AS observed_projection_mismatches,
    count(*) FILTER (
        WHERE mark_status = 'missing' AND expected_snapshot_id IS NOT NULL
    ) AS false_missing_with_eligible_snapshot,
    count(*) FILTER (
        WHERE mark_sealed_at > grace_at
    ) AS materialized_after_grace
FROM reconciled
GROUP BY mode, release_epoch_id, horizon_seconds
ORDER BY mode, release_epoch_id, horizon_seconds;

WITH required AS (
    SELECT
        leg.id AS fill_leg_id,
        leg.intent_id,
        horizon.seconds AS horizon_seconds
    FROM execution_fill_legs AS leg
    CROSS JOIN execution_cost_runtime AS runtime
    CROSS JOIN LATERAL unnest(runtime.mark_horizons_seconds) AS horizon(seconds)
    WHERE leg.fill_effective_at IS NOT NULL
)
SELECT count(*) AS seals_created_before_required_marks
FROM execution_cost_seals AS seal
JOIN required ON required.intent_id = seal.intent_id
LEFT JOIN execution_postfill_marks AS mark
  ON mark.fill_leg_id = required.fill_leg_id
 AND mark.horizon_seconds = required.horizon_seconds
WHERE mark.id IS NULL OR mark.sealed_at > seal.sealed_at;

SELECT
    (SELECT coalesce(sum(duplicates), 0)
     FROM (
        SELECT count(*) - 1 AS duplicates
        FROM execution_postfill_marks
        GROUP BY fill_leg_id, horizon_seconds
        HAVING count(*) > 1
     ) AS duplicate_marks) AS duplicate_mark_rows,
    (SELECT coalesce(sum(duplicates), 0)
     FROM (
        SELECT count(*) - 1 AS duplicates
        FROM execution_cost_seals
        GROUP BY obligation_id
        HAVING count(*) > 1
     ) AS duplicate_seals) AS duplicate_seal_rows,
    (SELECT count(*) FROM orders WHERE mode = 'live'::execution_mode) AS live_orders,
    (SELECT count(*) FROM positions WHERE mode = 'live'::execution_mode) AS live_positions,
    (SELECT count(*) FROM execution_order_intents
     WHERE mode = 'live'::execution_mode) AS live_intents;

-- BEGIN execution-cost-record-hash-reconciliation
WITH mark_payloads AS MATERIALIZED (
    SELECT
        'mark'::text AS record_kind,
        mark.id AS record_id,
        intent.mode::text AS mode,
        intent.release_epoch_id,
        mark.record_hash AS stored_hash,
        jsonb_build_object(
            'record_kind', 'mark',
            'clob_snapshot_hash', mark.clob_snapshot_hash,
            'clob_snapshot_id', mark.clob_snapshot_id,
            'definition_hash', mark.definition_hash,
            'definition_version', mark.definition_version,
            'fill_leg_id', mark.fill_leg_id,
            'horizon_seconds', mark.horizon_seconds,
            'missing_reason', mark.missing_reason,
            'observed_at', mark.observed_at,
            'offset_seconds', mark.offset_seconds,
            'sealed_at', mark.sealed_at,
            'side_best_ask', mark.side_best_ask,
            'side_best_bid', mark.side_best_bid,
            'side_midpoint', mark.side_midpoint,
            'status', mark.status,
            'target_at', mark.target_at
        ) AS payload
    FROM execution_postfill_marks AS mark
    JOIN execution_fill_legs AS leg ON leg.id = mark.fill_leg_id
    JOIN execution_order_intents AS intent ON intent.id = leg.intent_id
),
seal_payloads AS MATERIALIZED (
    SELECT
        'seal'::text AS record_kind,
        seal.id AS record_id,
        intent.mode::text AS mode,
        intent.release_epoch_id,
        seal.record_hash AS stored_hash,
        jsonb_build_object(
            'record_kind', 'seal',
            'adverse_selection_1800s', seal.adverse_selection_1800s,
            'adverse_selection_300s', seal.adverse_selection_300s,
            'aggregate_role', seal.aggregate_role,
            'definition_hash', seal.definition_hash,
            'definition_version', seal.definition_version,
            'eligibility_reason', seal.eligibility_reason,
            'eligible', seal.eligible,
            'expected_slippage_coverage', seal.expected_slippage_coverage,
            'expected_slippage_per_share', seal.expected_slippage_per_share,
            'fee_drag', seal.fee_drag,
            'fee_per_share', seal.fee_per_share,
            'filled_notional_usd', seal.filled_notional_usd,
            'gross_model_edge', seal.gross_model_edge,
            'intent_id', seal.intent_id,
            'intent_midpoint', seal.intent_midpoint,
            'latency_movement', seal.latency_movement,
            'leg_count', seal.leg_count,
            'maker_improvement', seal.maker_improvement,
            'markout_1800s_coverage', seal.markout_1800s_coverage,
            'markout_300s_coverage', seal.markout_300s_coverage,
            'obligation_id', seal.obligation_id,
            'order_id', seal.order_id,
            'realized_net_edge', seal.realized_net_edge,
            'reason_detail', seal.reason_detail,
            'sealed_at', seal.sealed_at,
            'side_model_probability', seal.side_model_probability,
            'taker_slippage', seal.taker_slippage,
            'taker_spread', seal.taker_spread,
            'total_fee_usd', seal.total_fee_usd,
            'total_shares', seal.total_shares,
            'vwap_fill_price', seal.vwap_fill_price,
            'weighted_fill_midpoint', seal.weighted_fill_midpoint
        ) AS payload
    FROM execution_cost_seals AS seal
    JOIN execution_order_intents AS intent ON intent.id = seal.intent_id
),
hashes AS MATERIALIZED (
    SELECT
        payloads.*,
        encode(
            digest(convert_to(payloads.payload::text, 'UTF8'), 'sha256'),
            'hex'
        ) AS expected_hash,
        execution_cost_hash(payloads.payload) AS function_hash
    FROM (
        SELECT * FROM mark_payloads
        UNION ALL
        SELECT * FROM seal_payloads
    ) AS payloads
),
kind_stats AS (
    SELECT
        record_kind,
        count(*) AS rows_checked,
        count(*) FILTER (
            WHERE stored_hash IS DISTINCT FROM expected_hash
        ) AS invalid_record_hashes,
        count(*) FILTER (
            WHERE function_hash IS DISTINCT FROM expected_hash
        ) AS hash_function_mismatches
    FROM hashes
    GROUP BY record_kind
),
scope_stats AS (
    SELECT
        record_kind,
        mode,
        release_epoch_id,
        count(*) AS rows_checked,
        count(*) FILTER (
            WHERE stored_hash IS DISTINCT FROM expected_hash
        ) AS invalid_record_hashes
    FROM hashes
    GROUP BY record_kind, mode, release_epoch_id
)
SELECT
    expected.record_kind,
    coalesce(stats.rows_checked, 0) AS rows_checked,
    coalesce(stats.invalid_record_hashes, 0) AS invalid_record_hashes,
    coalesce(stats.hash_function_mismatches, 0) AS hash_function_mismatches,
    coalesce(
        stats.invalid_record_hashes = 0
        AND stats.hash_function_mismatches = 0,
        true
    ) AS all_hashes_valid,
    coalesce(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'mode', scoped.mode,
                    'release_epoch_id', scoped.release_epoch_id,
                    'rows_checked', scoped.rows_checked,
                    'invalid_record_hashes', scoped.invalid_record_hashes
                )
                ORDER BY scoped.mode, scoped.release_epoch_id
            )
            FROM scope_stats AS scoped
            WHERE scoped.record_kind = expected.record_kind
        ),
        '[]'::jsonb
    ) AS scopes,
    coalesce(
        (
            SELECT jsonb_agg(
                jsonb_build_object(
                    'id', hashed.record_id,
                    'mode', hashed.mode,
                    'release_epoch_id', hashed.release_epoch_id,
                    'stored_hash', hashed.stored_hash,
                    'expected_hash', hashed.expected_hash,
                    'function_hash', hashed.function_hash
                )
                ORDER BY hashed.record_id
            )
            FROM hashes AS hashed
            WHERE hashed.record_kind = expected.record_kind
              AND (
                  hashed.stored_hash IS DISTINCT FROM hashed.expected_hash
                  OR hashed.function_hash IS DISTINCT FROM hashed.expected_hash
              )
        ),
        '[]'::jsonb
    ) AS mismatches
FROM (VALUES ('mark'::text), ('seal'::text)) AS expected(record_kind)
LEFT JOIN kind_stats AS stats USING (record_kind)
ORDER BY expected.record_kind;
-- END execution-cost-record-hash-reconciliation

-- BEGIN execution-cost-trigger-integrity
WITH expected(
    relation_name,
    trigger_name,
    function_signature,
    trigger_type
) AS (
    VALUES
        (
            'execution_postfill_marks',
            'trg_execution_postfill_marks_validate_insert',
            'execution_cost_validate_mark_insert()',
            7
        ),
        (
            'execution_postfill_marks',
            'trg_execution_postfill_marks_append_only',
            'execution_cost_reject_mutation()',
            27
        ),
        (
            'execution_cost_seals',
            'trg_execution_cost_seals_validate_insert',
            'execution_cost_validate_seal_insert()',
            7
        ),
        (
            'execution_cost_seals',
            'trg_execution_cost_seals_append_only',
            'execution_cost_reject_mutation()',
            27
        )
)
SELECT
    expected.relation_name,
    expected.trigger_name,
    trigger_row.oid IS NOT NULL AS present,
    trigger_row.tgenabled,
    coalesce(trigger_row.tgenabled IN ('O', 'A'), false) AS enabled,
    coalesce(
        trigger_row.tgfoid = to_regprocedure(expected.function_signature),
        false
    ) AS function_matches,
    coalesce(trigger_row.tgtype = expected.trigger_type, false) AS event_matches,
    pg_get_triggerdef(trigger_row.oid, true) AS observed_definition
FROM expected
LEFT JOIN pg_trigger AS trigger_row
  ON trigger_row.tgrelid = to_regclass(expected.relation_name)
 AND trigger_row.tgname = expected.trigger_name
 AND NOT trigger_row.tgisinternal
ORDER BY expected.relation_name, expected.trigger_name;
-- END execution-cost-trigger-integrity

-- BEGIN execution-cost-hash-function-integrity
SELECT
    procedure_row.oid IS NOT NULL AS present,
    language_row.lanname AS language,
    procedure_row.provolatile = 'i' AS immutable,
    procedure_row.proisstrict AS strict,
    NOT procedure_row.prosecdef AS invoker_security,
    execution_cost_hash('{"a": 1, "bb": true}'::jsonb)
        = encode(
            digest(
                convert_to('{"a": 1, "bb": true}'::jsonb::text, 'UTF8'),
                'sha256'
            ),
            'hex'
        ) AS known_vector_matches_inline
FROM (
    SELECT to_regprocedure('execution_cost_hash(jsonb)') AS oid
) AS expected
LEFT JOIN pg_proc AS procedure_row ON procedure_row.oid = expected.oid
LEFT JOIN pg_language AS language_row
  ON language_row.oid = procedure_row.prolang;
-- END execution-cost-hash-function-integrity

SELECT
    job_id,
    status,
    count(*) AS runs,
    min(scheduled_at) AS first_scheduled_at,
    max(scheduled_at) AS last_scheduled_at
FROM job_runs
WHERE job_id IN (
    'execution-markouts',
    'execution-cost-finalization',
    'execution-cost-maintenance'
)
  AND scheduled_at >= clock_timestamp() - interval '24 hours'
GROUP BY job_id, status
ORDER BY job_id, status;

SELECT
    obligation.id AS obligation_id,
    intent.mode,
    intent.release_epoch_id,
    obligation.state,
    obligation.next_due_at,
    obligation.finalized_at,
    count(DISTINCT leg.id) AS effective_legs,
    count(DISTINCT mark.id) AS terminal_marks
FROM execution_cost_obligations AS obligation
JOIN execution_order_intents AS intent ON intent.id = obligation.intent_id
LEFT JOIN execution_fill_legs AS leg
  ON leg.intent_id = obligation.intent_id
 AND leg.fill_effective_at IS NOT NULL
LEFT JOIN execution_postfill_marks AS mark ON mark.fill_leg_id = leg.id
WHERE obligation.state <> 'sealed'
GROUP BY
    obligation.id,
    intent.mode,
    intent.release_epoch_id,
    obligation.state,
    obligation.next_due_at,
    obligation.finalized_at
ORDER BY obligation.next_due_at NULLS FIRST, obligation.id;

COMMIT;
