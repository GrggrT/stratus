"""Add immutable dual-token CLOB market evidence and prospective provenance.

Revision ID: 20260714_0031
Revises: 20260714_0030
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0031"
down_revision: str | None = "20260714_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_PROTOCOL = "legacy-synthetic-yes-v0"
DUAL_PROTOCOL = "clob-dual-v1"


def _add_snapshot_fk(table: str, column: str, constraint: str) -> None:
    op.add_column(table, sa.Column(column, sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        constraint,
        table,
        "clob_market_snapshots",
        [column],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(f"ix_{table}_{column}", table, [column])


def upgrade() -> None:
    # Migration 0030 creates pgcrypto. Keep this idempotent for independently
    # restored schemas and never remove the shared extension on downgrade.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "clob_book_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "evidence_protocol",
            sa.String(length=32),
            nullable=False,
            server_default=DUAL_PROTOCOL,
        ),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_http_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_etag", sa.Text(), nullable=True),
        sa.Column(
            "requested_token_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("requested_token_count", sa.Integer(), nullable=False),
        sa.Column("requested_tokens_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_response_body", sa.LargeBinary(), nullable=False),
        sa.Column("source_response_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_response_sha256", sa.String(length=64), nullable=False),
        # Computed by PostgreSQL from the server-attested first_seen time and
        # all source identity fields in the BEFORE INSERT trigger.
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "requested_token_count >= 2 AND requested_token_count % 2 = 0",
            name="ck_clob_book_batches_even_token_count",
        ),
        sa.CheckConstraint(
            "source_response_bytes > 0",
            name="ck_clob_book_batches_response_nonempty",
        ),
        sa.CheckConstraint(
            "octet_length(source_response_body) = source_response_bytes",
            name="ck_clob_book_batches_response_size",
        ),
        sa.CheckConstraint(
            "request_started_at <= response_received_at",
            name="ck_clob_book_batches_request_order",
        ),
    )
    op.create_index(
        "ix_clob_book_batches_first_seen_at",
        "clob_book_batches",
        ["first_seen_at"],
    )

    op.create_table(
        "clob_market_snapshots",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "batch_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_book_batches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "market_id",
            sa.BigInteger(),
            sa.ForeignKey("markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("condition_id", sa.String(length=66), nullable=False),
        sa.Column("yes_token_id", sa.String(length=80), nullable=False),
        sa.Column("no_token_id", sa.String(length=80), nullable=False),
        sa.Column("yes_source_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("no_source_timestamp", sa.DateTime(timezone=True), nullable=False),
        # These are opaque source identifiers, not STRATUS SHA-256 values.
        sa.Column("yes_source_hash", sa.Text(), nullable=False),
        sa.Column("no_source_hash", sa.Text(), nullable=False),
        sa.Column("yes_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("no_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("yes_tick_size", sa.Numeric(10, 8), nullable=False),
        sa.Column("no_tick_size", sa.Numeric(10, 8), nullable=False),
        sa.Column("yes_neg_risk", sa.Boolean(), nullable=False),
        sa.Column("no_neg_risk", sa.Boolean(), nullable=False),
        sa.Column("yes_best_bid", sa.Numeric(10, 8), nullable=True),
        sa.Column("yes_best_ask", sa.Numeric(10, 8), nullable=True),
        sa.Column("yes_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("yes_bid_depth_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("yes_ask_depth_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("no_best_bid", sa.Numeric(10, 8), nullable=True),
        sa.Column("no_best_ask", sa.Numeric(10, 8), nullable=True),
        sa.Column("no_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("no_bid_depth_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("no_ask_depth_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("source_skew_ms", sa.Integer(), nullable=False),
        sa.Column("complement_residual", sa.Numeric(10, 8), nullable=True),
        sa.Column("binary_yes_probability", sa.Numeric(10, 8), nullable=True),
        sa.Column("complete", sa.Boolean(), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "yes_token_id <> no_token_id",
            name="ck_clob_market_snapshots_distinct_tokens",
        ),
        sa.CheckConstraint(
            "yes_tick_size > 0 AND yes_tick_size < 1 "
            "AND no_tick_size > 0 AND no_tick_size < 1",
            name="ck_clob_market_snapshots_tick_range",
        ),
        sa.CheckConstraint(
            "source_skew_ms >= 0",
            name="ck_clob_market_snapshots_skew_nonnegative",
        ),
        sa.UniqueConstraint(
            "batch_id",
            "market_id",
            name="uq_clob_market_snapshots_batch_market",
        ),
    )
    op.create_index(
        "ix_clob_market_snapshots_market_batch",
        "clob_market_snapshots",
        ["market_id", "batch_id"],
    )
    op.create_index(
        "ix_clob_market_snapshots_eligible_market",
        "clob_market_snapshots",
        ["eligible", "market_id", "batch_id"],
    )

    _add_snapshot_fk(
        "model_probs",
        "market_snapshot_id",
        "fk_model_probs_market_snapshot",
    )
    op.add_column(
        "model_probs",
        sa.Column(
            "market_anchor_protocol",
            sa.String(length=32),
            nullable=False,
            server_default=LEGACY_PROTOCOL,
        ),
    )
    _add_snapshot_fk(
        "signals",
        "clob_snapshot_id",
        "fk_signals_clob_snapshot",
    )
    op.add_column("signals", sa.Column("token_id", sa.String(length=80), nullable=True))
    op.add_column(
        "signals",
        sa.Column(
            "benchmark_protocol",
            sa.String(length=32),
            nullable=False,
            server_default=LEGACY_PROTOCOL,
        ),
    )
    op.add_column("orders", sa.Column("token_id", sa.String(length=80), nullable=True))
    op.add_column(
        "positions",
        sa.Column(
            "benchmark_protocol",
            sa.String(length=32),
            nullable=False,
            server_default=LEGACY_PROTOCOL,
        ),
    )
    op.add_column("positions", sa.Column("token_id", sa.String(length=80), nullable=True))
    _add_snapshot_fk(
        "positions",
        "clv_snapshot_id",
        "fk_positions_clv_snapshot",
    )
    op.add_column(
        "positions",
        sa.Column("clv_snapshot_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column(
            "market_benchmark_protocol",
            sa.String(length=32),
            nullable=False,
            server_default=LEGACY_PROTOCOL,
        ),
    )
    _add_snapshot_fk(
        "forecast_evaluations",
        "market_snapshot_id",
        "fk_forecast_evaluations_market_snapshot",
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("market_snapshot_hash", sa.String(length=64), nullable=True),
    )
    _add_snapshot_fk(
        "forecast_evaluations",
        "close_snapshot_id",
        "fk_forecast_evaluations_close_snapshot",
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("close_snapshot_hash", sa.String(length=64), nullable=True),
    )

    _create_validation_functions_and_triggers()


def _create_validation_functions_and_triggers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION clob_payload_timestamp(payload jsonb)
        RETURNS timestamptz
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            raw_value text;
            numeric_value numeric;
        BEGIN
            raw_value := payload->>'timestamp';
            IF raw_value IS NULL OR raw_value !~ '^[0-9]+$' THEN
                RAISE EXCEPTION 'CLOB payload timestamp is invalid';
            END IF;
            numeric_value := raw_value::numeric;
            IF numeric_value > 10000000000 THEN
                numeric_value := numeric_value / 1000;
            END IF;
            RETURN to_timestamp(numeric_value::double precision);
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION clob_payload_best(payload jsonb, side_name text)
        RETURNS numeric
        LANGUAGE plpgsql
        IMMUTABLE
        STRICT
        AS $$
        DECLARE
            levels jsonb;
            result numeric;
        BEGIN
            IF side_name NOT IN ('bids', 'asks') THEN
                RAISE EXCEPTION 'unknown CLOB book side';
            END IF;
            levels := payload->side_name;
            IF jsonb_typeof(levels) IS DISTINCT FROM 'array' THEN
                RAISE EXCEPTION 'CLOB book side is not an array';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM jsonb_array_elements(levels) AS entry(value)
                WHERE jsonb_typeof(value) IS DISTINCT FROM 'object'
                   OR value->>'price' IS NULL
                   OR value->>'size' IS NULL
                   OR value->>'price' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
                   OR value->>'size' !~ '^(0|[1-9][0-9]*)(\.[0-9]+)?$'
                   OR (value->>'price')::numeric <= 0
                   OR (value->>'price')::numeric >= 1
                   OR (value->>'size')::numeric <= 0
            ) THEN
                RAISE EXCEPTION 'CLOB book contains an invalid level';
            END IF;
            IF side_name = 'bids' THEN
                SELECT max((value->>'price')::numeric)
                INTO result
                FROM jsonb_array_elements(levels) AS entry(value);
            ELSE
                SELECT min((value->>'price')::numeric)
                INTO result
                FROM jsonb_array_elements(levels) AS entry(value);
            END IF;
            RETURN result;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION clob_payload_depth(
            payload jsonb,
            side_name text,
            best_price numeric
        )
        RETURNS numeric
        LANGUAGE plpgsql
        IMMUTABLE
        AS $$
        DECLARE
            result numeric;
        BEGIN
            IF best_price IS NULL THEN
                RETURN NULL;
            END IF;
            IF side_name = 'bids' THEN
                SELECT round(sum(
                    (value->>'price')::numeric * (value->>'size')::numeric
                ), 6)
                INTO result
                FROM jsonb_array_elements(payload->'bids') AS entry(value)
                WHERE (value->>'price')::numeric >= best_price - 0.01;
            ELSIF side_name = 'asks' THEN
                SELECT round(sum(
                    (value->>'price')::numeric * (value->>'size')::numeric
                ), 6)
                INTO result
                FROM jsonb_array_elements(payload->'asks') AS entry(value)
                WHERE (value->>'price')::numeric <= best_price + 0.01;
            ELSE
                RAISE EXCEPTION 'unknown CLOB book side';
            END IF;
            RETURN result;
        END;
        $$
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_clob_book_batch_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            raw_payload jsonb;
            sorted_tokens jsonb;
            canonical_tokens text;
            token_count integer;
            distinct_token_count integer;
            response_count integer;
            response_distinct_count integer;
        BEGIN
            NEW.first_seen_at := clock_timestamp();
            IF NEW.evidence_protocol IS DISTINCT FROM 'clob-dual-v1' THEN
                RAISE EXCEPTION 'CLOB batch evidence protocol mismatch';
            END IF;
            IF NEW.source_url IS NULL OR btrim(NEW.source_url) = '' THEN
                RAISE EXCEPTION 'CLOB batch source URL is missing';
            END IF;
            IF NEW.request_started_at IS NULL
               OR NEW.response_received_at IS NULL
               OR NEW.request_started_at > NEW.response_received_at
               OR NEW.response_received_at > NEW.first_seen_at THEN
                RAISE EXCEPTION 'CLOB batch request/response times are invalid';
            END IF;
            IF jsonb_typeof(NEW.requested_token_ids) IS DISTINCT FROM 'array'
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(NEW.requested_token_ids) AS item(value)
                    WHERE jsonb_typeof(value) IS DISTINCT FROM 'string'
                       OR NULLIF(btrim(value #>> '{}'), '') IS NULL
               ) THEN
                RAISE EXCEPTION 'CLOB requested token vector is invalid';
            END IF;

            SELECT count(*),
                   count(DISTINCT token),
                   jsonb_agg(to_jsonb(token) ORDER BY token COLLATE "C"),
                   string_agg(token, E'\n' ORDER BY token COLLATE "C")
            INTO token_count, distinct_token_count, sorted_tokens, canonical_tokens
            FROM jsonb_array_elements_text(NEW.requested_token_ids) AS item(token);
            IF token_count IS DISTINCT FROM NEW.requested_token_count
               OR token_count < 2
               OR token_count % 2 <> 0
               OR distinct_token_count <> token_count
               OR NEW.requested_token_ids IS DISTINCT FROM sorted_tokens THEN
                RAISE EXCEPTION 'CLOB requested token vector must be sorted and unique';
            END IF;
            IF NEW.requested_tokens_sha256 IS NULL
               OR NEW.requested_tokens_sha256 !~ '^[0-9a-f]{64}$'
               OR encode(digest(convert_to(canonical_tokens, 'UTF8'), 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.requested_tokens_sha256 THEN
                RAISE EXCEPTION 'CLOB requested token hash mismatch';
            END IF;
            IF NEW.source_response_body IS NULL
               OR NEW.source_response_bytes IS NULL
               OR NEW.source_response_bytes <= 0
               OR octet_length(NEW.source_response_body) <> NEW.source_response_bytes
               OR NEW.source_response_sha256 IS NULL
               OR NEW.source_response_sha256 !~ '^[0-9a-f]{64}$'
               OR encode(digest(NEW.source_response_body, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.source_response_sha256 THEN
                RAISE EXCEPTION 'CLOB response bytes do not match stored size/hash';
            END IF;
            BEGIN
                raw_payload := convert_from(NEW.source_response_body, 'UTF8')::jsonb;
            EXCEPTION WHEN OTHERS THEN
                RAISE EXCEPTION 'CLOB response body is not valid UTF-8 JSON';
            END;
            IF jsonb_typeof(raw_payload) IS DISTINCT FROM 'array'
               OR EXISTS (
                    SELECT 1
                    FROM jsonb_array_elements(raw_payload) AS item(value)
                    WHERE jsonb_typeof(value) IS DISTINCT FROM 'object'
                       OR jsonb_typeof(value->'asset_id') IS DISTINCT FROM 'string'
                       OR NULLIF(btrim(value->>'asset_id'), '') IS NULL
               ) THEN
                RAISE EXCEPTION 'CLOB response is not a valid book array';
            END IF;
            SELECT count(*), count(DISTINCT value->>'asset_id')
            INTO response_count, response_distinct_count
            FROM jsonb_array_elements(raw_payload) AS item(value);
            IF response_count <> token_count OR response_distinct_count <> token_count
               OR EXISTS (
                    (SELECT token
                     FROM jsonb_array_elements_text(NEW.requested_token_ids) AS req(token)
                     EXCEPT
                     SELECT value->>'asset_id'
                     FROM jsonb_array_elements(raw_payload) AS response(value))
                    UNION ALL
                    (SELECT value->>'asset_id'
                     FROM jsonb_array_elements(raw_payload) AS response(value)
                     EXCEPT
                     SELECT token
                     FROM jsonb_array_elements_text(NEW.requested_token_ids) AS req(token))
               ) THEN
                RAISE EXCEPTION 'CLOB response token universe differs from request';
            END IF;
            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'protocol', NEW.evidence_protocol,
                            'request_started_at', NEW.request_started_at,
                            'response_received_at', NEW.response_received_at,
                            'first_seen_at', NEW.first_seen_at,
                            'source_url', NEW.source_url,
                            'source_http_date', NEW.source_http_date,
                            'source_etag', NEW.source_etag,
                            'requested_tokens_sha256', NEW.requested_tokens_sha256,
                            'requested_token_count', NEW.requested_token_count,
                            'source_response_sha256', NEW.source_response_sha256,
                            'source_response_bytes', NEW.source_response_bytes
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
        CREATE TRIGGER trg_clob_book_batches_validate_insert
        BEFORE INSERT ON clob_book_batches
        FOR EACH ROW EXECUTE FUNCTION validate_clob_book_batch_insert()
        """
    )

    _create_snapshot_validation_trigger()
    op.execute(
        """
        CREATE FUNCTION enforce_complete_clob_batch_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            projected_count integer;
            projected_distinct_count integer;
        BEGIN
            SELECT count(*), count(DISTINCT token_id)
            INTO projected_count, projected_distinct_count
            FROM (
                SELECT yes_token_id AS token_id
                FROM clob_market_snapshots WHERE batch_id = NEW.id
                UNION ALL
                SELECT no_token_id AS token_id
                FROM clob_market_snapshots WHERE batch_id = NEW.id
            ) AS projected;
            IF projected_count <> NEW.requested_token_count
               OR projected_distinct_count <> NEW.requested_token_count
               OR EXISTS (
                    (SELECT token
                     FROM jsonb_array_elements_text(NEW.requested_token_ids) AS req(token)
                     EXCEPT
                     (SELECT yes_token_id
                      FROM clob_market_snapshots WHERE batch_id = NEW.id
                      UNION
                      SELECT no_token_id
                      FROM clob_market_snapshots WHERE batch_id = NEW.id))
               )
               OR EXISTS (
                    (SELECT yes_token_id AS token
                     FROM clob_market_snapshots WHERE batch_id = NEW.id
                     UNION
                     SELECT no_token_id
                     FROM clob_market_snapshots WHERE batch_id = NEW.id)
                    EXCEPT
                    SELECT token
                    FROM jsonb_array_elements_text(NEW.requested_token_ids) AS req(token)
               ) THEN
                RAISE EXCEPTION
                    'CLOB batch requires one complete committed snapshot projection';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_clob_batch_projection_complete
        AFTER INSERT ON clob_book_batches
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_complete_clob_batch_projection()
        """
    )
    _create_provenance_triggers()

    op.execute(
        """
        CREATE FUNCTION reject_clob_evidence_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is an append-only evidence ledger', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in ("clob_book_batches", "clob_market_snapshots"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION reject_clob_evidence_mutation()
            """
        )


def _create_snapshot_validation_trigger() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_clob_market_snapshot_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            batch_protocol text;
            batch_first_seen timestamptz;
            batch_tokens jsonb;
            raw_payload jsonb;
            yes_payload jsonb;
            no_payload jsonb;
            market_condition text;
            market_yes_token text;
            market_no_token text;
            parsed_yes_bid numeric;
            parsed_yes_ask numeric;
            parsed_no_bid numeric;
            parsed_no_ask numeric;
            expected_complete boolean;
            expected_eligible boolean;
            expected_skew integer;
            expected_residual numeric;
            expected_binary_yes numeric;
            expected_yes_midpoint numeric;
            expected_no_midpoint numeric;
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.market_id::text, 71020260715::bigint)
            );
            SELECT evidence_protocol,
                   first_seen_at,
                   requested_token_ids,
                   convert_from(source_response_body, 'UTF8')::jsonb
            INTO batch_protocol, batch_first_seen, batch_tokens, raw_payload
            FROM clob_book_batches
            WHERE id = NEW.batch_id;
            IF NOT FOUND OR batch_protocol IS DISTINCT FROM 'clob-dual-v1' THEN
                RAISE EXCEPTION 'CLOB snapshot batch is missing or has wrong protocol';
            END IF;
            IF NOT batch_tokens ? NEW.yes_token_id
               OR NOT batch_tokens ? NEW.no_token_id THEN
                RAISE EXCEPTION 'CLOB snapshot tokens were not requested in its batch';
            END IF;

            SELECT condition_id, yes_token_id, no_token_id
            INTO market_condition, market_yes_token, market_no_token
            FROM markets
            WHERE id = NEW.market_id
            FOR KEY SHARE;
            IF NOT FOUND
               OR market_condition IS NULL
               OR market_condition IS DISTINCT FROM NEW.condition_id
               OR market_yes_token IS DISTINCT FROM NEW.yes_token_id
               OR market_no_token IS DISTINCT FROM NEW.no_token_id
               OR NEW.yes_token_id = NEW.no_token_id THEN
                RAISE EXCEPTION 'CLOB snapshot does not match frozen market token identity';
            END IF;

            SELECT value INTO yes_payload
            FROM jsonb_array_elements(raw_payload) AS item(value)
            WHERE value->>'asset_id' = NEW.yes_token_id;
            SELECT value INTO no_payload
            FROM jsonb_array_elements(raw_payload) AS item(value)
            WHERE value->>'asset_id' = NEW.no_token_id;
            IF yes_payload IS NULL OR no_payload IS NULL THEN
                RAISE EXCEPTION 'CLOB snapshot token payload is absent from raw response';
            END IF;
            IF yes_payload->>'market' IS DISTINCT FROM NEW.condition_id
               OR no_payload->>'market' IS DISTINCT FROM NEW.condition_id THEN
                RAISE EXCEPTION 'CLOB snapshot payload condition identity mismatch';
            END IF;
            IF jsonb_typeof(yes_payload->'neg_risk') IS DISTINCT FROM 'boolean'
               OR jsonb_typeof(no_payload->'neg_risk') IS DISTINCT FROM 'boolean'
               OR yes_payload->>'hash' IS NULL
               OR NULLIF(btrim(yes_payload->>'hash'), '') IS NULL
               OR no_payload->>'hash' IS NULL
               OR NULLIF(btrim(no_payload->>'hash'), '') IS NULL
               OR yes_payload->>'tick_size' IS NULL
               OR no_payload->>'tick_size' IS NULL THEN
                RAISE EXCEPTION 'CLOB snapshot payload metadata is incomplete';
            END IF;
            IF NEW.yes_source_hash IS DISTINCT FROM yes_payload->>'hash'
               OR NEW.no_source_hash IS DISTINCT FROM no_payload->>'hash'
               OR NEW.yes_payload_sha256 !~ '^[0-9a-f]{64}$'
               OR NEW.no_payload_sha256 !~ '^[0-9a-f]{64}$'
               OR NEW.yes_payload_sha256 IS DISTINCT FROM encode(
                    digest(convert_to(yes_payload::text, 'UTF8'), 'sha256'), 'hex'
               )
               OR NEW.no_payload_sha256 IS DISTINCT FROM encode(
                    digest(convert_to(no_payload::text, 'UTF8'), 'sha256'), 'hex'
               ) THEN
                RAISE EXCEPTION 'CLOB snapshot source/payload hash mismatch';
            END IF;
            IF NEW.yes_source_timestamp IS DISTINCT FROM clob_payload_timestamp(yes_payload)
               OR NEW.no_source_timestamp IS DISTINCT FROM clob_payload_timestamp(no_payload)
               OR NEW.yes_source_timestamp > batch_first_seen + interval '5 seconds'
               OR NEW.no_source_timestamp > batch_first_seen + interval '5 seconds' THEN
                RAISE EXCEPTION 'CLOB snapshot source timestamp mismatch';
            END IF;
            IF NEW.yes_tick_size IS DISTINCT FROM (yes_payload->>'tick_size')::numeric
               OR NEW.no_tick_size IS DISTINCT FROM (no_payload->>'tick_size')::numeric
               OR NEW.yes_tick_size <= 0 OR NEW.yes_tick_size >= 1
               OR NEW.no_tick_size <= 0 OR NEW.no_tick_size >= 1
               OR NEW.yes_neg_risk IS DISTINCT FROM (yes_payload->>'neg_risk')::boolean
               OR NEW.no_neg_risk IS DISTINCT FROM (no_payload->>'neg_risk')::boolean THEN
                RAISE EXCEPTION 'CLOB snapshot tick/neg-risk metadata mismatch';
            END IF;

            parsed_yes_bid := clob_payload_best(yes_payload, 'bids');
            parsed_yes_ask := clob_payload_best(yes_payload, 'asks');
            parsed_no_bid := clob_payload_best(no_payload, 'bids');
            parsed_no_ask := clob_payload_best(no_payload, 'asks');
            IF NEW.yes_best_bid IS DISTINCT FROM round(parsed_yes_bid, 8)
               OR NEW.yes_best_ask IS DISTINCT FROM round(parsed_yes_ask, 8)
               OR NEW.no_best_bid IS DISTINCT FROM round(parsed_no_bid, 8)
               OR NEW.no_best_ask IS DISTINCT FROM round(parsed_no_ask, 8)
               OR NEW.yes_bid_depth_usd IS DISTINCT FROM
                    clob_payload_depth(yes_payload, 'bids', parsed_yes_bid)
               OR NEW.yes_ask_depth_usd IS DISTINCT FROM
                    clob_payload_depth(yes_payload, 'asks', parsed_yes_ask)
               OR NEW.no_bid_depth_usd IS DISTINCT FROM
                    clob_payload_depth(no_payload, 'bids', parsed_no_bid)
               OR NEW.no_ask_depth_usd IS DISTINCT FROM
                    clob_payload_depth(no_payload, 'asks', parsed_no_ask) THEN
                RAISE EXCEPTION 'CLOB snapshot quote/depth does not match raw payload';
            END IF;
            IF (parsed_yes_bid IS NOT NULL AND parsed_yes_ask IS NOT NULL
                AND parsed_yes_bid >= parsed_yes_ask)
               OR (parsed_no_bid IS NOT NULL AND parsed_no_ask IS NOT NULL
                   AND parsed_no_bid >= parsed_no_ask) THEN
                RAISE EXCEPTION 'CLOB snapshot contains a crossed book';
            END IF;
            expected_yes_midpoint := CASE
                WHEN parsed_yes_bid IS NOT NULL AND parsed_yes_ask IS NOT NULL
                THEN round((parsed_yes_bid + parsed_yes_ask) / 2, 8)
                ELSE NULL END;
            expected_no_midpoint := CASE
                WHEN parsed_no_bid IS NOT NULL AND parsed_no_ask IS NOT NULL
                THEN round((parsed_no_bid + parsed_no_ask) / 2, 8)
                ELSE NULL END;
            IF NEW.yes_midpoint IS DISTINCT FROM expected_yes_midpoint
               OR NEW.no_midpoint IS DISTINCT FROM expected_no_midpoint THEN
                RAISE EXCEPTION 'CLOB snapshot midpoint arithmetic mismatch';
            END IF;

            expected_complete := NEW.yes_midpoint IS NOT NULL
                                 AND NEW.no_midpoint IS NOT NULL;
            expected_skew := round(abs(extract(epoch FROM
                NEW.yes_source_timestamp - NEW.no_source_timestamp)) * 1000)::integer;
            expected_residual := CASE WHEN expected_complete THEN
                round(abs(NEW.yes_midpoint + NEW.no_midpoint - 1), 8)
                ELSE NULL END;
            expected_binary_yes := CASE WHEN expected_complete THEN
                round(NEW.yes_midpoint / (NEW.yes_midpoint + NEW.no_midpoint), 8)
                ELSE NULL END;
            expected_eligible := expected_complete
                                 AND expected_skew <= 2000
                                 AND expected_residual <= greatest(
                                     NEW.yes_tick_size, NEW.no_tick_size
                                 );
            IF NEW.complete IS DISTINCT FROM expected_complete
               OR NEW.source_skew_ms IS DISTINCT FROM expected_skew
               OR NEW.complement_residual IS DISTINCT FROM expected_residual
               OR NEW.binary_yes_probability IS DISTINCT FROM expected_binary_yes
               OR NEW.eligible IS DISTINCT FROM expected_eligible THEN
                RAISE EXCEPTION 'CLOB snapshot completeness/eligibility arithmetic mismatch';
            END IF;

            NEW.record_hash := encode(
                digest(
                    convert_to(
                        jsonb_build_object(
                            'batch_id', NEW.batch_id,
                            'batch_first_seen_at', batch_first_seen,
                            'market_id', NEW.market_id,
                            'condition_id', NEW.condition_id,
                            'yes_token_id', NEW.yes_token_id,
                            'no_token_id', NEW.no_token_id,
                            'yes_source_timestamp', NEW.yes_source_timestamp,
                            'no_source_timestamp', NEW.no_source_timestamp,
                            'yes_source_hash', NEW.yes_source_hash,
                            'no_source_hash', NEW.no_source_hash,
                            'yes_payload_sha256', NEW.yes_payload_sha256,
                            'no_payload_sha256', NEW.no_payload_sha256,
                            'yes_tick_size', NEW.yes_tick_size,
                            'no_tick_size', NEW.no_tick_size,
                            'yes_neg_risk', NEW.yes_neg_risk,
                            'no_neg_risk', NEW.no_neg_risk,
                            'yes_best_bid', NEW.yes_best_bid,
                            'yes_best_ask', NEW.yes_best_ask,
                            'yes_midpoint', NEW.yes_midpoint,
                            'yes_bid_depth_usd', NEW.yes_bid_depth_usd,
                            'yes_ask_depth_usd', NEW.yes_ask_depth_usd,
                            'no_best_bid', NEW.no_best_bid,
                            'no_best_ask', NEW.no_best_ask,
                            'no_midpoint', NEW.no_midpoint,
                            'no_bid_depth_usd', NEW.no_bid_depth_usd,
                            'no_ask_depth_usd', NEW.no_ask_depth_usd,
                            'source_skew_ms', NEW.source_skew_ms,
                            'complement_residual', NEW.complement_residual,
                            'binary_yes_probability', NEW.binary_yes_probability,
                            'complete', NEW.complete,
                            'eligible', NEW.eligible
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
        CREATE TRIGGER trg_clob_market_snapshots_validate_insert
        BEFORE INSERT ON clob_market_snapshots
        FOR EACH ROW EXECUTE FUNCTION validate_clob_market_snapshot_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION protect_market_clob_token_identity()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(OLD.id::text, 71020260715::bigint)
                );
                IF EXISTS (
                    SELECT 1 FROM clob_market_snapshots WHERE market_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'market token identity is sealed by CLOB evidence';
                END IF;
                RETURN OLD;
            END IF;
            IF NEW.condition_id IS DISTINCT FROM OLD.condition_id
               OR NEW.yes_token_id IS DISTINCT FROM OLD.yes_token_id
               OR NEW.no_token_id IS DISTINCT FROM OLD.no_token_id THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(OLD.id::text, 71020260715::bigint)
                );
                IF EXISTS (
                    SELECT 1 FROM clob_market_snapshots WHERE market_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'market token identity is sealed by CLOB evidence';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_markets_protect_clob_token_identity
        BEFORE UPDATE OR DELETE ON markets
        FOR EACH ROW EXECUTE FUNCTION protect_market_clob_token_identity()
        """
    )


def _create_provenance_triggers() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_model_prob_clob_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            snapshot_market_id bigint;
            snapshot_eligible boolean;
            snapshot_first_seen timestamptz;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.market_anchor_protocol = 'clob-dual-v1'
               AND (
                   NEW.market_anchor_protocol IS DISTINCT FROM OLD.market_anchor_protocol
                   OR NEW.market_snapshot_id IS DISTINCT FROM OLD.market_snapshot_id
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.ts IS DISTINCT FROM OLD.ts
               ) THEN
                RAISE EXCEPTION 'dual CLOB model probability provenance is immutable';
            END IF;
            IF NEW.market_anchor_protocol = 'legacy-synthetic-yes-v0' THEN
                IF NEW.market_snapshot_id IS NOT NULL THEN
                    RAISE EXCEPTION 'legacy model probability cannot claim dual CLOB evidence';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.market_anchor_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR NEW.market_snapshot_id IS NULL THEN
                RAISE EXCEPTION 'model probability CLOB protocol/provenance is invalid';
            END IF;
            SELECT snapshot.market_id, snapshot.eligible, batch.first_seen_at
            INTO snapshot_market_id, snapshot_eligible, snapshot_first_seen
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.id = NEW.market_snapshot_id;
            IF NOT FOUND
               OR NOT snapshot_eligible
               OR snapshot_market_id IS DISTINCT FROM NEW.market_id
               OR NEW.ts < snapshot_first_seen THEN
                RAISE EXCEPTION 'model probability does not match eligible CLOB snapshot';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_model_probs_validate_clob_provenance
        BEFORE INSERT OR UPDATE ON model_probs
        FOR EACH ROW EXECUTE FUNCTION validate_model_prob_clob_provenance()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_signal_clob_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            snapshot_market_id bigint;
            snapshot_eligible boolean;
            snapshot_first_seen timestamptz;
            expected_token text;
            expected_ask numeric;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.benchmark_protocol = 'clob-dual-v1'
               AND (
                   NEW.benchmark_protocol IS DISTINCT FROM OLD.benchmark_protocol
                   OR NEW.clob_snapshot_id IS DISTINCT FROM OLD.clob_snapshot_id
                   OR NEW.token_id IS DISTINCT FROM OLD.token_id
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.ts IS DISTINCT FROM OLD.ts
                   OR NEW.side IS DISTINCT FROM OLD.side
                   OR NEW.market_price IS DISTINCT FROM OLD.market_price
               ) THEN
                RAISE EXCEPTION 'dual CLOB signal provenance is immutable';
            END IF;
            IF NEW.benchmark_protocol = 'legacy-synthetic-yes-v0' THEN
                IF NEW.clob_snapshot_id IS NOT NULL THEN
                    RAISE EXCEPTION 'legacy signal cannot claim dual CLOB evidence';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.benchmark_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR NEW.clob_snapshot_id IS NULL
               OR NEW.token_id IS NULL THEN
                RAISE EXCEPTION 'signal CLOB protocol/provenance is invalid';
            END IF;
            SELECT snapshot.market_id,
                   snapshot.eligible,
                   batch.first_seen_at,
                   CASE WHEN NEW.side::text = 'buy_yes'
                        THEN snapshot.yes_token_id ELSE snapshot.no_token_id END,
                   CASE WHEN NEW.side::text = 'buy_yes'
                        THEN snapshot.yes_best_ask ELSE snapshot.no_best_ask END
            INTO snapshot_market_id,
                 snapshot_eligible,
                 snapshot_first_seen,
                 expected_token,
                 expected_ask
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.id = NEW.clob_snapshot_id;
            IF NOT FOUND
               OR NOT snapshot_eligible
               OR snapshot_market_id IS DISTINCT FROM NEW.market_id
               OR NEW.ts < snapshot_first_seen
               OR NEW.token_id IS DISTINCT FROM expected_token
               OR NEW.market_price IS DISTINCT FROM expected_ask THEN
                RAISE EXCEPTION 'signal does not match eligible side-specific CLOB quote';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_signals_validate_clob_provenance
        BEFORE INSERT OR UPDATE ON signals
        FOR EACH ROW EXECUTE FUNCTION validate_signal_clob_provenance()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_order_clob_token()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            signal_protocol text;
            signal_token text;
            signal_side text;
        BEGIN
            SELECT benchmark_protocol, token_id, side::text
            INTO signal_protocol, signal_token, signal_side
            FROM signals
            WHERE id = NEW.signal_id;
            IF signal_protocol = 'clob-dual-v1'
               AND (
                   NEW.token_id IS NULL
                   OR NEW.token_id IS DISTINCT FROM signal_token
                   OR NEW.side::text IS DISTINCT FROM signal_side
               ) THEN
                RAISE EXCEPTION 'order token/side does not match dual CLOB signal';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_orders_validate_clob_token
        BEFORE INSERT OR UPDATE ON orders
        FOR EACH ROW EXECUTE FUNCTION validate_order_clob_token()
        """
    )

    _create_position_provenance_trigger()
    _create_evaluation_provenance_trigger()


def _create_position_provenance_trigger() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_position_clob_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            market_yes_token text;
            market_no_token text;
            expected_token text;
            snapshot_market_id bigint;
            snapshot_eligible boolean;
            snapshot_hash text;
            snapshot_first_seen timestamptz;
            snapshot_midpoint numeric;
            order_token text;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.benchmark_protocol = 'clob-dual-v1'
               AND (
                   NEW.benchmark_protocol IS DISTINCT FROM OLD.benchmark_protocol
                   OR NEW.token_id IS DISTINCT FROM OLD.token_id
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.side IS DISTINCT FROM OLD.side
               ) THEN
                RAISE EXCEPTION 'dual CLOB position identity provenance is immutable';
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.clv_snapshot_id IS NOT NULL
               AND (
                   NEW.clv_snapshot_id IS DISTINCT FROM OLD.clv_snapshot_id
                   OR NEW.clv_snapshot_hash IS DISTINCT FROM OLD.clv_snapshot_hash
                   OR NEW.clv IS DISTINCT FROM OLD.clv
                   OR NEW.market_closed_at IS DISTINCT FROM OLD.market_closed_at
               ) THEN
                RAISE EXCEPTION 'dual CLOB position CLV provenance is immutable';
            END IF;
            IF NEW.benchmark_protocol = 'legacy-synthetic-yes-v0' THEN
                IF NEW.clv_snapshot_id IS NOT NULL OR NEW.clv_snapshot_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'legacy position cannot claim dual CLOB CLV evidence';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.benchmark_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR NEW.token_id IS NULL THEN
                RAISE EXCEPTION 'position CLOB protocol/token is invalid';
            END IF;
            SELECT yes_token_id, no_token_id
            INTO market_yes_token, market_no_token
            FROM markets WHERE id = NEW.market_id;
            expected_token := CASE WHEN NEW.side::text = 'buy_yes'
                                   THEN market_yes_token ELSE market_no_token END;
            IF NEW.token_id IS DISTINCT FROM expected_token THEN
                RAISE EXCEPTION 'position token does not match market side';
            END IF;
            IF NEW.order_id IS NOT NULL THEN
                SELECT token_id INTO order_token FROM orders WHERE id = NEW.order_id;
                IF order_token IS DISTINCT FROM NEW.token_id THEN
                    RAISE EXCEPTION 'position token does not match its order';
                END IF;
            END IF;
            IF NEW.clv IS NULL THEN
                IF NEW.clv_snapshot_id IS NOT NULL OR NEW.clv_snapshot_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'position CLV snapshot requires a CLV value';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.clv_snapshot_id IS NULL
               OR NEW.clv_snapshot_hash IS NULL
               OR NEW.clv_snapshot_hash !~ '^[0-9a-f]{64}$'
               OR NEW.market_closed_at IS NULL THEN
                RAISE EXCEPTION 'dual position CLV requires complete snapshot provenance';
            END IF;
            SELECT snapshot.market_id,
                   snapshot.eligible,
                   snapshot.record_hash,
                   batch.first_seen_at,
                   CASE WHEN NEW.side::text = 'buy_yes'
                        THEN snapshot.yes_midpoint ELSE snapshot.no_midpoint END
            INTO snapshot_market_id,
                 snapshot_eligible,
                 snapshot_hash,
                 snapshot_first_seen,
                 snapshot_midpoint
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.id = NEW.clv_snapshot_id;
            IF NOT FOUND
               OR NOT snapshot_eligible
               OR snapshot_market_id IS DISTINCT FROM NEW.market_id
               OR snapshot_hash IS DISTINCT FROM NEW.clv_snapshot_hash
               OR snapshot_first_seen > NEW.market_closed_at
               OR round(snapshot_midpoint - NEW.avg_price, 6) IS DISTINCT FROM NEW.clv THEN
                RAISE EXCEPTION 'position CLV does not match immutable dual CLOB snapshot';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_positions_validate_clob_provenance
        BEFORE INSERT OR UPDATE ON positions
        FOR EACH ROW EXECUTE FUNCTION validate_position_clob_provenance()
        """
    )


def _create_evaluation_provenance_trigger() -> None:
    op.execute(
        r"""
        CREATE FUNCTION validate_forecast_evaluation_clob_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            model_market_id bigint;
            model_decision_at timestamptz;
            anchored_model_probability numeric;
            model_snapshot_id bigint;
            model_protocol text;
            snapshot_batch_id bigint;
            snapshot_market_id bigint;
            snapshot_eligible boolean;
            snapshot_hash text;
            snapshot_first_seen timestamptz;
            snapshot_bid numeric;
            snapshot_ask numeric;
            benchmark_market_count integer;
            benchmark_snapshot_count bigint;
            benchmark_probability_total numeric;
            benchmark_target_probability numeric;
            benchmark_complete boolean := false;
            expected_model_brier numeric;
            expected_market_probability numeric;
            expected_market_total numeric;
            expected_market_overround numeric;
            expected_market_brier numeric;
            close_market_id bigint;
            close_eligible boolean;
            close_hash text;
            close_first_seen timestamptz;
            close_probability numeric;
            market_resolves_at timestamptz;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.market_benchmark_protocol = 'clob-dual-v1'
               AND (
                   NEW.market_benchmark_protocol IS DISTINCT FROM
                       OLD.market_benchmark_protocol
                   OR NEW.market_snapshot_id IS DISTINCT FROM OLD.market_snapshot_id
                   OR NEW.market_snapshot_hash IS DISTINCT FROM OLD.market_snapshot_hash
                   OR NEW.close_snapshot_id IS DISTINCT FROM OLD.close_snapshot_id
                   OR NEW.close_snapshot_hash IS DISTINCT FROM OLD.close_snapshot_hash
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.model_prob_id IS DISTINCT FROM OLD.model_prob_id
                   OR NEW.decision_at IS DISTINCT FROM OLD.decision_at
               ) THEN
                RAISE EXCEPTION 'dual CLOB evaluation provenance is immutable';
            END IF;
            IF NEW.market_benchmark_protocol = 'legacy-synthetic-yes-v0' THEN
                IF NEW.market_snapshot_id IS NOT NULL
                   OR NEW.market_snapshot_hash IS NOT NULL
                   OR NEW.close_snapshot_id IS NOT NULL
                   OR NEW.close_snapshot_hash IS NOT NULL THEN
                    RAISE EXCEPTION 'legacy evaluation cannot claim dual CLOB evidence';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.market_benchmark_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR NEW.market_snapshot_id IS NULL
               OR NEW.market_snapshot_hash IS NULL
               OR NEW.market_snapshot_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'forecast evaluation CLOB protocol/provenance is invalid';
            END IF;
            SELECT market_id, ts, prob, market_snapshot_id, market_anchor_protocol
            INTO model_market_id,
                 model_decision_at,
                 anchored_model_probability,
                 model_snapshot_id,
                 model_protocol
            FROM model_probs WHERE id = NEW.model_prob_id;
            IF NOT FOUND
               OR model_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR model_market_id IS DISTINCT FROM NEW.market_id
               OR model_decision_at IS DISTINCT FROM NEW.decision_at
               OR anchored_model_probability IS DISTINCT FROM NEW.model_probability
               OR model_snapshot_id IS DISTINCT FROM NEW.market_snapshot_id THEN
                RAISE EXCEPTION 'evaluation differs from anchored model probability';
            END IF;
            expected_model_brier := round(
                (NEW.model_probability - CASE WHEN NEW.resolved_outcome THEN 1 ELSE 0 END)
                *
                (NEW.model_probability - CASE WHEN NEW.resolved_outcome THEN 1 ELSE 0 END),
                8
            );
            IF NEW.model_brier IS DISTINCT FROM expected_model_brier THEN
                RAISE EXCEPTION 'forecast evaluation model Brier arithmetic mismatch';
            END IF;

            SELECT snapshot.batch_id,
                   snapshot.market_id,
                   snapshot.eligible,
                   snapshot.record_hash,
                   batch.first_seen_at,
                   snapshot.yes_best_bid,
                   snapshot.yes_best_ask
            INTO snapshot_batch_id,
                 snapshot_market_id,
                 snapshot_eligible,
                 snapshot_hash,
                 snapshot_first_seen,
                 snapshot_bid,
                 snapshot_ask
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.id = NEW.market_snapshot_id;
            IF NOT FOUND
               OR NOT snapshot_eligible
               OR snapshot_market_id IS DISTINCT FROM NEW.market_id
               OR snapshot_hash IS DISTINCT FROM NEW.market_snapshot_hash
               OR snapshot_first_seen > NEW.decision_at
               OR NEW.market_snapshot_at IS DISTINCT FROM snapshot_first_seen
               OR NEW.executable_bid IS DISTINCT FROM snapshot_bid
               OR NEW.executable_ask IS DISTINCT FROM snapshot_ask THEN
                RAISE EXCEPTION 'evaluation does not match immutable horizon CLOB snapshot';
            END IF;

            -- A formal market comparison is all-or-nothing.  The immutable
            -- Gamma resolution vector defines the exact event market universe;
            -- every member must have an eligible snapshot in the model anchor's
            -- exact raw-response batch, observed no more than 30 minutes before
            -- the decision.  No latest-row or cross-request synthesis is allowed.
            IF NEW.resolution_event_id IS NOT NULL THEN
                SELECT outcome.market_count,
                       count(snapshot.id) FILTER (
                           WHERE snapshot.eligible
                             AND snapshot.binary_yes_probability IS NOT NULL
                             AND batch.first_seen_at <= NEW.decision_at
                             AND batch.first_seen_at >=
                                 NEW.decision_at - interval '30 minutes'
                       ),
                       sum(snapshot.binary_yes_probability) FILTER (
                           WHERE snapshot.eligible
                             AND snapshot.binary_yes_probability IS NOT NULL
                             AND batch.first_seen_at <= NEW.decision_at
                             AND batch.first_seen_at >=
                                 NEW.decision_at - interval '30 minutes'
                       ),
                       max(snapshot.binary_yes_probability) FILTER (
                           WHERE snapshot.eligible
                             AND snapshot.binary_yes_probability IS NOT NULL
                             AND batch.first_seen_at <= NEW.decision_at
                             AND batch.first_seen_at >=
                                 NEW.decision_at - interval '30 minutes'
                             AND (item.value->>'market_id')::bigint = NEW.market_id
                       )
                INTO benchmark_market_count,
                     benchmark_snapshot_count,
                     benchmark_probability_total,
                     benchmark_target_probability
                FROM gamma_outcome_events AS outcome
                CROSS JOIN LATERAL jsonb_array_elements(outcome.outcome_vector)
                    AS item(value)
                LEFT JOIN clob_market_snapshots AS snapshot
                  ON snapshot.batch_id = snapshot_batch_id
                 AND snapshot.market_id = (item.value->>'market_id')::bigint
                LEFT JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
                WHERE outcome.id = NEW.resolution_event_id
                GROUP BY outcome.market_count;
                benchmark_complete := FOUND
                    AND benchmark_snapshot_count = benchmark_market_count
                    AND benchmark_target_probability IS NOT NULL
                    AND benchmark_probability_total > 0;
            END IF;

            IF benchmark_complete THEN
                IF NEW.horizon_market_probability IS NULL
                   OR NEW.event_probability_total IS NULL
                   OR NEW.event_overround IS NULL
                   OR NEW.market_brier IS NULL THEN
                    RAISE EXCEPTION
                        'complete fresh vector requires all market benchmark fields';
                END IF;
                expected_market_probability := round(
                    benchmark_target_probability / benchmark_probability_total,
                    6
                );
                expected_market_total := round(benchmark_probability_total, 6);
                expected_market_overround := round(
                    benchmark_probability_total - 1,
                    6
                );
                expected_market_brier := round(
                    (
                        benchmark_target_probability / benchmark_probability_total
                        - CASE WHEN NEW.resolved_outcome THEN 1 ELSE 0 END
                    )
                    *
                    (
                        benchmark_target_probability / benchmark_probability_total
                        - CASE WHEN NEW.resolved_outcome THEN 1 ELSE 0 END
                    ),
                    8
                );
                IF NEW.horizon_market_probability IS DISTINCT FROM
                       expected_market_probability
                   OR NEW.event_probability_total IS DISTINCT FROM
                       expected_market_total
                   OR NEW.event_overround IS DISTINCT FROM expected_market_overround
                   OR NEW.market_brier IS DISTINCT FROM expected_market_brier THEN
                    RAISE EXCEPTION
                        'forecast evaluation market benchmark arithmetic mismatch';
                END IF;
            ELSIF NEW.horizon_market_probability IS NOT NULL
               OR NEW.event_probability_total IS NOT NULL
               OR NEW.event_overround IS NOT NULL
               OR NEW.market_brier IS NOT NULL THEN
                RAISE EXCEPTION
                    'market benchmark fields require a complete fresh Gamma same-batch vector';
            END IF;

            IF NEW.pre_resolution_close IS NULL THEN
                IF NEW.close_snapshot_id IS NOT NULL
                   OR NEW.close_snapshot_hash IS NOT NULL
                   OR NEW.close_snapshot_at IS NOT NULL THEN
                    RAISE EXCEPTION 'evaluation close snapshot requires a close value';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.close_snapshot_id IS NULL
               OR NEW.close_snapshot_hash IS NULL
               OR NEW.close_snapshot_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'evaluation close value lacks dual CLOB provenance';
            END IF;
            SELECT resolves_at INTO market_resolves_at
            FROM markets WHERE id = NEW.market_id;
            SELECT snapshot.market_id,
                   snapshot.eligible,
                   snapshot.record_hash,
                   batch.first_seen_at,
                   snapshot.binary_yes_probability
            INTO close_market_id,
                 close_eligible,
                 close_hash,
                 close_first_seen,
                 close_probability
            FROM clob_market_snapshots AS snapshot
            JOIN clob_book_batches AS batch ON batch.id = snapshot.batch_id
            WHERE snapshot.id = NEW.close_snapshot_id;
            IF NOT FOUND
               OR NOT close_eligible
               OR close_market_id IS DISTINCT FROM NEW.market_id
               OR close_hash IS DISTINCT FROM NEW.close_snapshot_hash
               OR close_first_seen > market_resolves_at
               OR NEW.close_snapshot_at IS DISTINCT FROM close_first_seen
               OR NEW.pre_resolution_close IS DISTINCT FROM close_probability THEN
                RAISE EXCEPTION 'evaluation close does not match immutable dual CLOB snapshot';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_forecast_evaluations_validate_clob_provenance
        BEFORE INSERT OR UPDATE ON forecast_evaluations
        FOR EACH ROW EXECUTE FUNCTION validate_forecast_evaluation_clob_provenance()
        """
    )


def downgrade() -> None:
    # Once source evidence or prospective references exist, dropping this
    # schema would silently destroy attestation. Roll forward or restore the
    # pre-migration physical backup instead.
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM clob_book_batches)
               OR EXISTS (SELECT 1 FROM clob_market_snapshots)
               OR EXISTS (
                    SELECT 1 FROM model_probs
                    WHERE market_anchor_protocol = 'clob-dual-v1'
                       OR market_snapshot_id IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1 FROM signals
                    WHERE benchmark_protocol = 'clob-dual-v1'
                       OR clob_snapshot_id IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1 FROM positions
                    WHERE benchmark_protocol = 'clob-dual-v1'
                       OR clv_snapshot_id IS NOT NULL
               )
               OR EXISTS (
                    SELECT 1 FROM forecast_evaluations
                    WHERE market_benchmark_protocol = 'clob-dual-v1'
                       OR market_snapshot_id IS NOT NULL
                       OR close_snapshot_id IS NOT NULL
               ) THEN
                RAISE EXCEPTION
                    'cannot downgrade 0031 after dual-token CLOB evidence exists';
            END IF;
        END;
        $$
        """
    )

    trigger_functions = (
        ("trg_forecast_evaluations_validate_clob_provenance", "forecast_evaluations"),
        ("trg_positions_validate_clob_provenance", "positions"),
        ("trg_orders_validate_clob_token", "orders"),
        ("trg_signals_validate_clob_provenance", "signals"),
        ("trg_model_probs_validate_clob_provenance", "model_probs"),
        ("trg_markets_protect_clob_token_identity", "markets"),
        ("trg_clob_batch_projection_complete", "clob_book_batches"),
        ("trg_clob_market_snapshots_append_only", "clob_market_snapshots"),
        ("trg_clob_book_batches_append_only", "clob_book_batches"),
        ("trg_clob_market_snapshots_validate_insert", "clob_market_snapshots"),
        ("trg_clob_book_batches_validate_insert", "clob_book_batches"),
    )
    for trigger, table in trigger_functions:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
    for function in (
        "validate_forecast_evaluation_clob_provenance()",
        "validate_position_clob_provenance()",
        "validate_order_clob_token()",
        "validate_signal_clob_provenance()",
        "validate_model_prob_clob_provenance()",
        "protect_market_clob_token_identity()",
        "enforce_complete_clob_batch_projection()",
        "reject_clob_evidence_mutation()",
        "validate_clob_market_snapshot_insert()",
        "validate_clob_book_batch_insert()",
        "clob_payload_depth(jsonb, text, numeric)",
        "clob_payload_best(jsonb, text)",
        "clob_payload_timestamp(jsonb)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {function}")

    for table, column, constraint in (
        ("forecast_evaluations", "close_snapshot_id", "fk_forecast_evaluations_close_snapshot"),
        (
            "forecast_evaluations",
            "market_snapshot_id",
            "fk_forecast_evaluations_market_snapshot",
        ),
        ("positions", "clv_snapshot_id", "fk_positions_clv_snapshot"),
        ("signals", "clob_snapshot_id", "fk_signals_clob_snapshot"),
        ("model_probs", "market_snapshot_id", "fk_model_probs_market_snapshot"),
    ):
        op.drop_index(f"ix_{table}_{column}", table_name=table)
        op.drop_constraint(constraint, table, type_="foreignkey")

    op.drop_column("forecast_evaluations", "close_snapshot_hash")
    op.drop_column("forecast_evaluations", "close_snapshot_id")
    op.drop_column("forecast_evaluations", "market_snapshot_hash")
    op.drop_column("forecast_evaluations", "market_snapshot_id")
    op.drop_column("forecast_evaluations", "market_benchmark_protocol")
    op.drop_column("positions", "clv_snapshot_hash")
    op.drop_column("positions", "clv_snapshot_id")
    op.drop_column("positions", "token_id")
    op.drop_column("positions", "benchmark_protocol")
    op.drop_column("orders", "token_id")
    op.drop_column("signals", "benchmark_protocol")
    op.drop_column("signals", "token_id")
    op.drop_column("signals", "clob_snapshot_id")
    op.drop_column("model_probs", "market_anchor_protocol")
    op.drop_column("model_probs", "market_snapshot_id")

    op.drop_index(
        "ix_clob_market_snapshots_eligible_market",
        table_name="clob_market_snapshots",
    )
    op.drop_index(
        "ix_clob_market_snapshots_market_batch",
        table_name="clob_market_snapshots",
    )
    op.drop_table("clob_market_snapshots")
    op.drop_index("ix_clob_book_batches_first_seen_at", table_name="clob_book_batches")
    op.drop_table("clob_book_batches")
