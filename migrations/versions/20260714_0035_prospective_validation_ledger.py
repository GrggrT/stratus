"""Add the zero-backfill prospective trade-validation ledger.

Revision ID: 20260714_0035
Revises: 20260714_0034
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0035"
down_revision: str | None = "20260714_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINITION_HASH = "89cdcbe4cee27752b22af47f3be2c7cc83246443a63a54712c5fbce07718b949"
EXECUTION_MODE = postgresql.ENUM("paper", "live", name="execution_mode", create_type=False)
WEATHER_KIND = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)
TRADE_SIDE = postgresql.ENUM("buy_yes", "buy_no", name="trade_side", create_type=False)


def upgrade() -> None:
    # This database-clock watermark is the prospective boundary.  Repository
    # code refuses to enroll a fill observed before it; no historical order is
    # copied into the ledger during this migration.
    op.create_table(
        "prospective_validation_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_prospective_validation_runtime_singleton"),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_prospective_validation_runtime_hash",
        ),
    )
    op.execute(
        "INSERT INTO prospective_validation_runtime "
        "(id, opened_at, definition_version, definition_hash) VALUES "
        f"(1, clock_timestamp(), 'prospective-validation-v1', '{DEFINITION_HASH}')"
    )

    op.create_table(
        "prospective_validation_enrollments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "signal_id",
            sa.BigInteger(),
            sa.ForeignKey("signals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "model_prob_id",
            sa.BigInteger(),
            sa.ForeignKey("model_probs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "market_id",
            sa.BigInteger(),
            sa.ForeignKey("markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "clob_snapshot_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("mode", EXECUTION_MODE, nullable=False),
        sa.Column("pipeline_cohort", sa.String(length=128), nullable=True),
        sa.Column("station_icao", sa.String(length=4), nullable=True),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("kind", WEATHER_KIND, nullable=False),
        sa.Column("side", TRADE_SIDE, nullable=False),
        sa.Column("token_id", sa.String(length=80), nullable=True),
        sa.Column("fill_price", sa.Numeric(8, 6), nullable=False),
        sa.Column("filled_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column("fee_usd", sa.Numeric(14, 5), nullable=False),
        sa.Column("size_shares", sa.Numeric(18, 6), nullable=False),
        sa.Column("filled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("benchmark_protocol", sa.String(length=32), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("order_record_hash", sa.String(length=64), nullable=False),
        sa.Column("signal_record_hash", sa.String(length=64), nullable=False),
        sa.Column("model_record_hash", sa.String(length=64), nullable=True),
        sa.Column("snapshot_record_hash", sa.String(length=64), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_reason", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_prospective_validation_enrollments_order"),
        sa.CheckConstraint(
            "fill_price > 0 AND fill_price < 1 AND filled_usd > 0 "
            "AND fee_usd >= 0 AND size_shares > 0",
            name="ck_prospective_validation_enrollments_fill",
        ),
        sa.CheckConstraint(
            "definition_version = 'prospective-validation-v1'",
            name="ck_prospective_validation_enrollments_protocol",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "order_record_hash ~ '^[0-9a-f]{64}$' AND "
            "signal_record_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$' AND "
            "(model_record_hash IS NULL OR model_record_hash ~ '^[0-9a-f]{64}$') AND "
            "(snapshot_record_hash IS NULL OR snapshot_record_hash ~ '^[0-9a-f]{64}$')",
            name="ck_prospective_validation_enrollments_hashes",
        ),
        sa.CheckConstraint(
            "NOT eligible OR (benchmark_protocol = 'clob-dual-v1' "
            "AND pipeline_cohort IS NOT NULL AND model_prob_id IS NOT NULL "
            "AND model_record_hash IS NOT NULL AND clob_snapshot_id IS NOT NULL "
            "AND snapshot_record_hash IS NOT NULL AND station_icao IS NOT NULL)",
            name="ck_prospective_validation_enrollments_eligible",
        ),
    )
    op.create_index(
        "ix_prospective_validation_enrollments_scope",
        "prospective_validation_enrollments",
        ["mode", "pipeline_cohort", "enrolled_at"],
    )
    op.create_index(
        "ix_prospective_validation_enrollments_block",
        "prospective_validation_enrollments",
        ["pipeline_cohort", "station_icao", "market_date", "kind"],
    )

    op.create_table(
        "prospective_validation_outcomes",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "enrollment_id",
            sa.BigInteger(),
            sa.ForeignKey("prospective_validation_enrollments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "resolution_event_id",
            sa.BigInteger(),
            sa.ForeignKey("gamma_outcome_events.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("gamma_record_hash", sa.String(length=64), nullable=False),
        sa.Column("resolution_vector_hash", sa.String(length=64), nullable=False),
        sa.Column("resolved_yes", sa.Boolean(), nullable=False),
        sa.Column("won", sa.Boolean(), nullable=False),
        sa.Column("market_closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recognized_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pnl_usd", sa.Numeric(14, 4), nullable=False),
        sa.Column(
            "close_snapshot_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("close_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("close_price", sa.Numeric(8, 6), nullable=True),
        sa.Column("clv", sa.Numeric(8, 6), nullable=True),
        sa.Column("skill_pnl_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("luck_pnl_usd", sa.Numeric(14, 4), nullable=True),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("enrollment_id", name="uq_prospective_validation_outcomes_enrollment"),
        sa.CheckConstraint(
            "gamma_record_hash ~ '^[0-9a-f]{64}$' AND "
            "resolution_vector_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_prospective_validation_outcomes_hashes",
        ),
        sa.CheckConstraint(
            "(close_snapshot_id IS NULL AND close_snapshot_hash IS NULL "
            "AND close_price IS NULL AND clv IS NULL AND skill_pnl_usd IS NULL "
            "AND luck_pnl_usd IS NULL) OR "
            "(close_snapshot_id IS NOT NULL AND close_snapshot_hash IS NOT NULL "
            "AND close_snapshot_hash ~ '^[0-9a-f]{64}$' AND close_price IS NOT NULL "
            "AND close_price >= 0 AND close_price <= 1 AND clv IS NOT NULL "
            "AND skill_pnl_usd IS NOT NULL AND luck_pnl_usd IS NOT NULL)",
            name="ck_prospective_validation_outcomes_close",
        ),
    )
    op.create_index(
        "ix_prospective_validation_outcomes_recognized",
        "prospective_validation_outcomes",
        ["recognized_at", "enrollment_id"],
    )

    # The application records canonical SHA-256 witnesses, but the database is
    # also a write boundary.  These helpers reproduce Python's compact,
    # key-sorted JSON and normalized Decimal/datetime representation so a
    # direct SQL writer cannot invent plausible-looking evidence hashes.
    op.execute(
        r"""
        CREATE FUNCTION prospective_validation_canonical_json(value jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE
            result text;
        BEGIN
            CASE jsonb_typeof(value)
                WHEN 'object' THEN
                    SELECT '{' || coalesce(
                        string_agg(
                            to_jsonb(item.key)::text || ':' ||
                            prospective_validation_canonical_json(item.value),
                            ',' ORDER BY item.key
                        ),
                        ''
                    ) || '}'
                    INTO result
                    FROM jsonb_each(value) AS item;
                    RETURN result;
                WHEN 'array' THEN
                    SELECT '[' || coalesce(
                        string_agg(
                            prospective_validation_canonical_json(item.value),
                            ',' ORDER BY item.ordinal
                        ),
                        ''
                    ) || ']'
                    INTO result
                    FROM jsonb_array_elements(value)
                         WITH ORDINALITY AS item(value, ordinal);
                    RETURN result;
                ELSE
                    RETURN value::text;
            END CASE;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION prospective_validation_hash(value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT encode(
                digest(
                    convert_to(prospective_validation_canonical_json(value), 'UTF8'),
                    'sha256'
                ),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION prospective_validation_iso_utc(value timestamptz)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT to_char(
                       value AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS'
                   ) ||
                   CASE
                       WHEN extract(microseconds FROM value)::bigint % 1000000 = 0
                           THEN ''
                       ELSE '.' || to_char(value AT TIME ZONE 'UTC', 'US')
                   END || '+00:00'
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION prospective_validation_pipeline_cohort(components jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE AS $$
        BEGIN
            IF components IS NULL
               OR jsonb_typeof(components) IS DISTINCT FROM 'object'
               OR jsonb_typeof(components->'pipeline') IS DISTINCT FROM 'object' THEN
                RETURN NULL;
            END IF;
            IF jsonb_typeof(components->'pipeline'->'cohort') = 'string'
               AND components->'pipeline'->>'cohort' <> '' THEN
                RETURN components->'pipeline'->>'cohort';
            END IF;
            IF jsonb_typeof(components->'pipeline'->'version') = 'string'
               AND components->'pipeline'->>'version' <> '' THEN
                RETURN components->'pipeline'->>'version';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_prospective_validation_enrollment_insert()
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
        BEGIN
            SELECT * INTO runtime_row
            FROM prospective_validation_runtime WHERE id = 1;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective validation runtime is missing';
            END IF;
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
    )
    op.execute(
        "CREATE TRIGGER trg_prospective_validation_enrollments_validate_insert "
        "BEFORE INSERT ON prospective_validation_enrollments FOR EACH ROW "
        "EXECUTE FUNCTION validate_prospective_validation_enrollment_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_prospective_validation_outcome_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            enrollment_row prospective_validation_enrollments%ROWTYPE;
            event_row gamma_outcome_events%ROWTYPE;
            market_row markets%ROWTYPE;
            close_row clob_market_snapshots%ROWTYPE;
            vector_item jsonb;
            vector_count integer;
            vector_closed_at timestamptz;
            expected_close_id bigint;
            expected_close_price numeric;
            expected_resolved_yes boolean;
            expected_won boolean;
            expected_pnl numeric;
            expected_clv numeric;
            expected_skill numeric;
            expected_luck numeric;
            expected_record_hash text;
        BEGIN
            SELECT * INTO enrollment_row
            FROM prospective_validation_enrollments
            WHERE id = NEW.enrollment_id;
            IF NOT FOUND OR NOT enrollment_row.eligible THEN
                RAISE EXCEPTION 'prospective outcome requires eligible enrollment';
            END IF;
            SELECT * INTO event_row
            FROM gamma_outcome_events WHERE id = NEW.resolution_event_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective outcome Gamma event is missing';
            END IF;
            SELECT * INTO market_row
            FROM markets WHERE id = enrollment_row.market_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'prospective outcome market is missing';
            END IF;
            SELECT count(*), (jsonb_agg(item.value)->0)
            INTO vector_count, vector_item
            FROM jsonb_array_elements(event_row.outcome_vector) AS item(value)
            WHERE (item.value->>'market_id')::bigint = enrollment_row.market_id;
            IF vector_count <> 1 THEN
                RAISE EXCEPTION 'prospective outcome Gamma vector market mismatch';
            END IF;
            expected_resolved_yes := (vector_item->>'outcome_yes')::boolean;
            vector_closed_at := (vector_item->>'closed_at')::timestamptz;
            expected_won :=
                (enrollment_row.side::text = 'buy_yes') = expected_resolved_yes;
            expected_pnl := round(
                CASE WHEN expected_won THEN enrollment_row.size_shares ELSE 0 END
                - enrollment_row.filled_usd - enrollment_row.fee_usd,
                4
            );

            IF NEW.gamma_record_hash IS DISTINCT FROM event_row.record_hash
               OR NEW.resolution_vector_hash
                    IS DISTINCT FROM event_row.outcome_vector_hash
               OR prospective_validation_hash(event_row.outcome_vector)
                    IS DISTINCT FROM event_row.outcome_vector_hash
               OR event_row.event_id IS DISTINCT FROM market_row.event_id
               OR NEW.resolved_yes IS DISTINCT FROM expected_resolved_yes
               OR NEW.won IS DISTINCT FROM expected_won
               OR NEW.market_closed_at IS DISTINCT FROM vector_closed_at
               OR market_row.resolution_event_id IS DISTINCT FROM event_row.id
               OR market_row.resolved_outcome IS DISTINCT FROM expected_resolved_yes THEN
                RAISE EXCEPTION 'prospective outcome Gamma/market identity mismatch';
            END IF;
            IF NEW.recognized_at IS DISTINCT FROM event_row.first_seen_at
               OR NEW.recognized_at < NEW.market_closed_at
               OR NEW.pnl_usd IS DISTINCT FROM expected_pnl THEN
                RAISE EXCEPTION 'prospective outcome payout arithmetic mismatch';
            END IF;

            SELECT snapshot.*
            INTO close_row
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.market_id = enrollment_row.market_id
              AND snapshot.eligible
              AND CASE
                      WHEN enrollment_row.side::text = 'buy_yes'
                          THEN snapshot.yes_midpoint IS NOT NULL
                      ELSE snapshot.no_midpoint IS NOT NULL
                  END
              AND batch.first_seen_at >= enrollment_row.filled_at
              AND batch.first_seen_at <= NEW.market_closed_at
            ORDER BY batch.first_seen_at DESC, snapshot.id DESC
            LIMIT 1;
            IF FOUND THEN
                expected_close_id := close_row.id;
                expected_close_price := CASE
                    WHEN enrollment_row.side::text = 'buy_yes'
                        THEN close_row.yes_midpoint
                    ELSE close_row.no_midpoint
                END;
            END IF;
            IF expected_close_id IS NULL THEN
                IF NEW.close_snapshot_id IS NOT NULL
                   OR NEW.close_snapshot_hash IS NOT NULL
                   OR NEW.close_price IS NOT NULL
                   OR NEW.clv IS NOT NULL
                   OR NEW.skill_pnl_usd IS NOT NULL
                   OR NEW.luck_pnl_usd IS NOT NULL THEN
                    RAISE EXCEPTION 'prospective outcome close evidence must be absent';
                END IF;
            ELSE
                expected_clv := round(
                    expected_close_price - enrollment_row.fill_price,
                    6
                );
                expected_skill := round(
                    enrollment_row.size_shares * expected_clv
                    - enrollment_row.fee_usd,
                    4
                );
                expected_luck := round(expected_pnl - expected_skill, 4);
                IF NEW.close_snapshot_id IS DISTINCT FROM expected_close_id
                   OR NEW.close_snapshot_hash IS DISTINCT FROM close_row.record_hash
                   OR abs(NEW.close_price - expected_close_price) > 0.0000005
                   OR NEW.clv IS DISTINCT FROM expected_clv
                   OR NEW.skill_pnl_usd IS DISTINCT FROM expected_skill
                   OR NEW.luck_pnl_usd IS DISTINCT FROM expected_luck THEN
                    RAISE EXCEPTION
                        'prospective outcome close/CLV arithmetic mismatch';
                END IF;
            END IF;

            expected_record_hash := prospective_validation_hash(
                jsonb_build_object(
                    'enrollment_id', NEW.enrollment_id,
                    'resolution_event_id', NEW.resolution_event_id,
                    'gamma_record_hash', NEW.gamma_record_hash,
                    'resolution_vector_hash', NEW.resolution_vector_hash,
                    'resolved_yes', NEW.resolved_yes,
                    'won', NEW.won,
                    'market_closed_at',
                        prospective_validation_iso_utc(NEW.market_closed_at),
                    'recognized_at',
                        prospective_validation_iso_utc(NEW.recognized_at),
                    'pnl_usd', trim_scale(NEW.pnl_usd)::text,
                    'close_snapshot_id', NEW.close_snapshot_id,
                    'close_snapshot_hash', NEW.close_snapshot_hash,
                    'close_price', CASE WHEN NEW.close_price IS NULL THEN NULL
                                        ELSE trim_scale(NEW.close_price)::text END,
                    'clv', CASE WHEN NEW.clv IS NULL THEN NULL
                                ELSE trim_scale(NEW.clv)::text END,
                    'skill_pnl_usd', CASE WHEN NEW.skill_pnl_usd IS NULL THEN NULL
                                          ELSE trim_scale(NEW.skill_pnl_usd)::text END,
                    'luck_pnl_usd', CASE WHEN NEW.luck_pnl_usd IS NULL THEN NULL
                                         ELSE trim_scale(NEW.luck_pnl_usd)::text END
                )
            );
            IF NEW.record_hash IS DISTINCT FROM expected_record_hash THEN
                RAISE EXCEPTION 'prospective outcome record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_prospective_validation_outcomes_validate_insert "
        "BEFORE INSERT ON prospective_validation_outcomes FOR EACH ROW "
        "EXECUTE FUNCTION validate_prospective_validation_outcome_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION reject_prospective_validation_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'prospective validation evidence is append-only';
        END;
        $$
        """
    )
    for table in (
        "prospective_validation_runtime",
        "prospective_validation_enrollments",
        "prospective_validation_outcomes",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_prospective_validation_mutation()"
        )
    op.execute(
        r"""
        CREATE FUNCTION require_prospective_validation_enrollment()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IN ('filled', 'partially_filled')
               AND NEW.filled_usd > 0
               AND NEW.filled_at IS NOT NULL
               AND NEW.filled_at >= (
                    SELECT opened_at
                    FROM prospective_validation_runtime
                    WHERE id = 1
               )
               AND NOT EXISTS (
                    SELECT 1 FROM prospective_validation_enrollments
                    WHERE order_id = NEW.id
               ) THEN
                RAISE EXCEPTION
                    'post-boundary terminal fill lacks prospective enrollment';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_orders_require_prospective_enrollment "
        "AFTER INSERT OR UPDATE ON orders DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION require_prospective_validation_enrollment()"
    )


def downgrade() -> None:
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM prospective_validation_enrollments)
               OR EXISTS (SELECT 1 FROM prospective_validation_outcomes) THEN
                RAISE EXCEPTION
                    'cannot downgrade prospective validation after evidence exists';
            END IF;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER trg_orders_require_prospective_enrollment ON orders")
    op.execute("DROP FUNCTION require_prospective_validation_enrollment()")
    op.execute(
        "DROP TRIGGER trg_prospective_validation_outcomes_validate_insert "
        "ON prospective_validation_outcomes"
    )
    op.execute("DROP FUNCTION validate_prospective_validation_outcome_insert()")
    op.execute(
        "DROP TRIGGER trg_prospective_validation_enrollments_validate_insert "
        "ON prospective_validation_enrollments"
    )
    op.execute("DROP FUNCTION validate_prospective_validation_enrollment_insert()")
    for table in (
        "prospective_validation_outcomes",
        "prospective_validation_enrollments",
        "prospective_validation_runtime",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION reject_prospective_validation_mutation()")
    op.drop_index(
        "ix_prospective_validation_outcomes_recognized",
        table_name="prospective_validation_outcomes",
    )
    op.drop_table("prospective_validation_outcomes")
    op.drop_index(
        "ix_prospective_validation_enrollments_block",
        table_name="prospective_validation_enrollments",
    )
    op.drop_index(
        "ix_prospective_validation_enrollments_scope",
        table_name="prospective_validation_enrollments",
    )
    op.drop_table("prospective_validation_enrollments")
    op.drop_table("prospective_validation_runtime")
    op.execute("DROP FUNCTION prospective_validation_pipeline_cohort(jsonb)")
    op.execute("DROP FUNCTION prospective_validation_iso_utc(timestamptz)")
    op.execute("DROP FUNCTION prospective_validation_hash(jsonb)")
    op.execute("DROP FUNCTION prospective_validation_canonical_json(jsonb)")
