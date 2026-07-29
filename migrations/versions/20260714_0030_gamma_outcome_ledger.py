"""Add an immutable, source-witnessed Gamma outcome event ledger.

Revision ID: 20260714_0030
Revises: 20260714_0029
Create Date: 2026-07-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260714_0030"
down_revision: str | None = "20260714_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Kept on downgrade because an extension can be shared by other schemas.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "gamma_outcome_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column(
            "evidence_protocol",
            sa.String(length=32),
            nullable=False,
            server_default="gamma-outcome-v1",
        ),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_http_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_etag", sa.Text(), nullable=True),
        sa.Column("source_response_body", sa.LargeBinary(), nullable=False),
        sa.Column("source_response_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_response_bytes", sa.BigInteger(), nullable=False),
        sa.Column("source_event_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("first_outcome_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fully_resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("market_count", sa.Integer(), nullable=False),
        sa.Column("market_universe_hash", sa.String(length=64), nullable=False),
        sa.Column("rules_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome_vector_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "outcome_vector",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "source_response_bytes > 0",
            name="ck_gamma_outcome_events_response_nonempty",
        ),
        sa.CheckConstraint(
            "octet_length(source_response_body) = source_response_bytes",
            name="ck_gamma_outcome_events_response_size",
        ),
        sa.CheckConstraint(
            "market_count >= 2",
            name="ck_gamma_outcome_events_market_count",
        ),
        sa.CheckConstraint(
            "first_outcome_at <= fully_resolved_at",
            name="ck_gamma_outcome_events_resolution_order",
        ),
        sa.UniqueConstraint("event_id", name="uq_gamma_outcome_events_event_id"),
    )
    op.create_index(
        "ix_gamma_outcome_events_first_seen",
        "gamma_outcome_events",
        ["first_seen_at"],
    )
    op.add_column(
        "markets",
        sa.Column("resolution_event_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_markets_resolution_event",
        "markets",
        "gamma_outcome_events",
        ["resolution_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_markets_resolution_event_id",
        "markets",
        ["resolution_event_id"],
    )
    op.add_column(
        "positions",
        sa.Column("market_closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("resolution_event_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_forecast_evaluations_resolution_event",
        "forecast_evaluations",
        "gamma_outcome_events",
        ["resolution_event_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "forecast_evaluations",
        sa.Column("resolution_vector_hash", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_forecast_evaluations_resolution_event_id",
        "forecast_evaluations",
        ["resolution_event_id"],
    )

    op.execute(
        r"""
        CREATE FUNCTION validate_gamma_outcome_event_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            item jsonb;
            item_closed_at timestamptz;
            vector_first_outcome_at timestamptz;
            vector_fully_resolved_at timestamptz;
            expected_market_count integer;
            distinct_market_count integer;
            distinct_external_count integer;
            winner_count integer := 0;
        BEGIN
            -- The database, not a client/API clock, attests first observation.
            NEW.first_seen_at := clock_timestamp();

            IF NEW.evidence_protocol IS DISTINCT FROM 'gamma-outcome-v1' THEN
                RAISE EXCEPTION 'Gamma outcome evidence protocol mismatch';
            END IF;
            IF NEW.event_id IS NULL OR btrim(NEW.event_id) = '' THEN
                RAISE EXCEPTION 'Gamma outcome event id is missing';
            END IF;
            PERFORM pg_advisory_xact_lock(
                hashtextextended(NEW.event_id, 71020260714::bigint)
            );
            IF NEW.source_url IS NULL OR btrim(NEW.source_url) = '' THEN
                RAISE EXCEPTION 'Gamma outcome source URL is missing';
            END IF;
            IF NEW.source_response_body IS NULL
               OR NEW.source_response_bytes IS NULL
               OR NEW.source_response_bytes <= 0 THEN
                RAISE EXCEPTION 'Gamma outcome source response is empty';
            END IF;
            IF NEW.market_count IS NULL OR NEW.market_count < 2 THEN
                RAISE EXCEPTION 'Gamma outcome event must contain at least two markets';
            END IF;
            IF NEW.source_response_sha256 IS NULL
               OR NEW.source_response_sha256 !~ '^[0-9a-f]{64}$'
               OR NEW.market_universe_hash IS NULL
               OR NEW.market_universe_hash !~ '^[0-9a-f]{64}$'
               OR NEW.rules_manifest_hash IS NULL
               OR NEW.rules_manifest_hash !~ '^[0-9a-f]{64}$'
               OR NEW.outcome_vector_hash IS NULL
               OR NEW.outcome_vector_hash !~ '^[0-9a-f]{64}$'
               OR NEW.record_hash IS NULL
               OR NEW.record_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'Gamma outcome evidence hashes must be lowercase SHA-256';
            END IF;
            IF octet_length(NEW.source_response_body) <> NEW.source_response_bytes
               OR encode(digest(NEW.source_response_body, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.source_response_sha256 THEN
                RAISE EXCEPTION
                    'Gamma outcome source bytes do not match stored size/hash';
            END IF;
            IF jsonb_typeof(NEW.outcome_vector) IS DISTINCT FROM 'array'
               OR jsonb_array_length(NEW.outcome_vector) <> NEW.market_count THEN
                RAISE EXCEPTION 'Gamma outcome vector count does not match market_count';
            END IF;

            FOR item IN SELECT value FROM jsonb_array_elements(NEW.outcome_vector)
            LOOP
                IF jsonb_typeof(item) IS DISTINCT FROM 'object'
                   OR NOT (item ?& ARRAY[
                       'market_id',
                       'polymarket_id',
                       'condition_id',
                       'question_id',
                       'yes_token_id',
                       'no_token_id',
                       'outcome_yes',
                       'yes_price',
                       'no_price',
                       'closed_at',
                       'uma_resolution_status',
                       'resolved_by',
                       'rules_hash'
                   ])
                   OR jsonb_typeof(item->'market_id') IS DISTINCT FROM 'number'
                   OR (item->>'market_id') !~ '^[1-9][0-9]*$'
                   OR jsonb_typeof(item->'polymarket_id') IS DISTINCT FROM 'string'
                   OR NULLIF(item->>'polymarket_id', '') IS NULL
                   OR NOT (
                       item->'condition_id' = 'null'::jsonb
                       OR jsonb_typeof(item->'condition_id') = 'string'
                   )
                   OR NOT (
                       item->'question_id' = 'null'::jsonb
                       OR jsonb_typeof(item->'question_id') = 'string'
                   )
                   OR jsonb_typeof(item->'yes_token_id') IS DISTINCT FROM 'string'
                   OR NULLIF(item->>'yes_token_id', '') IS NULL
                   OR jsonb_typeof(item->'no_token_id') IS DISTINCT FROM 'string'
                   OR NULLIF(item->>'no_token_id', '') IS NULL
                   OR jsonb_typeof(item->'outcome_yes') IS DISTINCT FROM 'boolean'
                   OR jsonb_typeof(item->'yes_price') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(item->'no_price') IS DISTINCT FROM 'string'
                   OR jsonb_typeof(item->'closed_at') IS DISTINCT FROM 'string'
                   OR NULLIF(item->>'closed_at', '') IS NULL
                   OR (item->>'closed_at') !~
                      'T.*(Z|[+-][0-9]{2}:[0-9]{2})$'
                   OR jsonb_typeof(item->'uma_resolution_status') IS DISTINCT FROM 'string'
                   OR item->>'uma_resolution_status' IS DISTINCT FROM 'resolved'
                   OR NOT (
                       item->'resolved_by' = 'null'::jsonb
                       OR jsonb_typeof(item->'resolved_by') = 'string'
                   )
                   OR jsonb_typeof(item->'rules_hash') IS DISTINCT FROM 'string'
                   OR (item->>'rules_hash') !~ '^[0-9a-f]{64}$' THEN
                    RAISE EXCEPTION 'Gamma outcome vector item has invalid types or identity';
                END IF;

                IF NOT (
                    (item->>'outcome_yes' = 'true'
                     AND item->>'yes_price' = '1'
                     AND item->>'no_price' = '0')
                    OR
                    (item->>'outcome_yes' = 'false'
                     AND item->>'yes_price' = '0'
                     AND item->>'no_price' = '1')
                ) THEN
                    RAISE EXCEPTION 'Gamma outcome vector contains a non-binary payout';
                END IF;

                BEGIN
                    item_closed_at := (item->>'closed_at')::timestamptz;
                EXCEPTION
                    WHEN invalid_datetime_format OR datetime_field_overflow THEN
                        RAISE EXCEPTION 'Gamma outcome vector contains invalid closed_at';
                END;
                IF vector_first_outcome_at IS NULL
                   OR item_closed_at < vector_first_outcome_at THEN
                    vector_first_outcome_at := item_closed_at;
                END IF;
                IF vector_fully_resolved_at IS NULL
                   OR item_closed_at > vector_fully_resolved_at THEN
                    vector_fully_resolved_at := item_closed_at;
                END IF;
                IF (item->>'outcome_yes')::boolean THEN
                    winner_count := winner_count + 1;
                END IF;

                IF NOT EXISTS (
                    SELECT 1
                    FROM markets AS m
                    WHERE m.id = (item->>'market_id')::bigint
                      AND m.event_id = NEW.event_id
                      AND m.polymarket_id = item->>'polymarket_id'
                      AND (
                          m.condition_id IS NULL
                          OR m.condition_id = item->>'condition_id'
                      )
                      AND (
                          m.yes_token_id IS NULL
                          OR m.yes_token_id = item->>'yes_token_id'
                      )
                      AND (
                          m.no_token_id IS NULL
                          OR m.no_token_id = item->>'no_token_id'
                      )
                      AND encode(
                          digest(convert_to(m.rules_raw, 'UTF8'), 'sha256'),
                          'hex'
                      ) = item->>'rules_hash'
                ) THEN
                    RAISE EXCEPTION
                        'Gamma outcome vector does not match persisted market identity/rules';
                END IF;
            END LOOP;

            SELECT count(*),
                   count(DISTINCT (value->>'market_id')::bigint),
                   count(DISTINCT value->>'polymarket_id')
            INTO expected_market_count,
                 distinct_market_count,
                 distinct_external_count
            FROM jsonb_array_elements(NEW.outcome_vector);
            IF distinct_market_count <> NEW.market_count
               OR distinct_external_count <> NEW.market_count THEN
                RAISE EXCEPTION 'Gamma outcome vector contains duplicate market identity';
            END IF;

            SELECT count(*)
            INTO expected_market_count
            FROM markets
            WHERE event_id = NEW.event_id;
            IF expected_market_count <> NEW.market_count THEN
                RAISE EXCEPTION
                    'Gamma outcome vector is not the complete persisted market universe';
            END IF;
            IF winner_count <> 1 THEN
                RAISE EXCEPTION 'Gamma outcome event must contain exactly one YES winner';
            END IF;
            IF NEW.first_outcome_at IS DISTINCT FROM vector_first_outcome_at
               OR NEW.fully_resolved_at IS DISTINCT FROM vector_fully_resolved_at THEN
                RAISE EXCEPTION
                    'Gamma outcome event timestamps do not match the outcome vector';
            END IF;
            IF NEW.fully_resolved_at > NEW.first_seen_at THEN
                RAISE EXCEPTION 'Gamma outcome event cannot be witnessed before resolution';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_gamma_outcome_events_validate_insert
        BEFORE INSERT ON gamma_outcome_events
        FOR EACH ROW EXECUTE FUNCTION validate_gamma_outcome_event_insert()
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_gamma_outcome_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'gamma_outcome_events is an append-only evidence ledger';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_gamma_outcome_events_append_only
        BEFORE UPDATE OR DELETE ON gamma_outcome_events
        FOR EACH ROW EXECUTE FUNCTION reject_gamma_outcome_event_mutation()
        """
    )

    op.execute(
        """
        CREATE FUNCTION validate_market_gamma_outcome_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            ledger_event_id text;
            vector_item jsonb;
            old_event_lock_key bigint;
            new_event_lock_key bigint;
            existing_ledger_id bigint;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(OLD.event_id, 71020260714::bigint)
                );
                IF OLD.resolution_event_id IS NOT NULL OR EXISTS (
                    SELECT 1
                    FROM gamma_outcome_events
                    WHERE event_id = OLD.event_id
                ) THEN
                    RAISE EXCEPTION
                        'market identity/projection is sealed by Gamma outcome evidence';
                END IF;
                RETURN OLD;
            END IF;

            IF TG_OP = 'INSERT' THEN
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(NEW.event_id, 71020260714::bigint)
                );
                IF EXISTS (
                    SELECT 1
                    FROM gamma_outcome_events
                    WHERE event_id = NEW.event_id
                ) THEN
                    RAISE EXCEPTION
                        'cannot add a market to an immutable Gamma outcome event';
                END IF;
            ELSIF TG_OP = 'UPDATE'
                  AND NEW.event_id IS DISTINCT FROM OLD.event_id THEN
                old_event_lock_key := hashtextextended(
                    OLD.event_id, 71020260714::bigint
                );
                new_event_lock_key := hashtextextended(
                    NEW.event_id, 71020260714::bigint
                );
                IF old_event_lock_key <= new_event_lock_key THEN
                    PERFORM pg_advisory_xact_lock(old_event_lock_key);
                    IF new_event_lock_key <> old_event_lock_key THEN
                        PERFORM pg_advisory_xact_lock(new_event_lock_key);
                    END IF;
                ELSE
                    PERFORM pg_advisory_xact_lock(new_event_lock_key);
                    PERFORM pg_advisory_xact_lock(old_event_lock_key);
                END IF;
                IF EXISTS (
                    SELECT 1
                    FROM gamma_outcome_events
                    WHERE event_id IN (OLD.event_id, NEW.event_id)
                ) THEN
                    RAISE EXCEPTION
                        'cannot move a market into or out of an immutable Gamma outcome event';
                END IF;
            ELSIF TG_OP = 'UPDATE' THEN
                -- Serialize every same-event mutation with ledger insertion.
                -- Once a ledger exists, the only allowed transition from a
                -- legacy unlinked row is its exact projection linkage.
                PERFORM pg_advisory_xact_lock(
                    hashtextextended(OLD.event_id, 71020260714::bigint)
                );
                SELECT id
                INTO existing_ledger_id
                FROM gamma_outcome_events
                WHERE event_id = OLD.event_id;
                IF existing_ledger_id IS NOT NULL
                   AND OLD.resolution_event_id IS NULL
                   AND (
                       NEW.resolution_event_id IS DISTINCT FROM existing_ledger_id
                       OR NEW.polymarket_id IS DISTINCT FROM OLD.polymarket_id
                       OR NEW.event_id IS DISTINCT FROM OLD.event_id
                       OR NEW.condition_id IS DISTINCT FROM OLD.condition_id
                       OR NEW.yes_token_id IS DISTINCT FROM OLD.yes_token_id
                       OR NEW.no_token_id IS DISTINCT FROM OLD.no_token_id
                       OR NEW.station_id IS DISTINCT FROM OLD.station_id
                       OR NEW.market_date IS DISTINCT FROM OLD.market_date
                       OR NEW.kind IS DISTINCT FROM OLD.kind
                       OR NEW.bucket_low_c IS DISTINCT FROM OLD.bucket_low_c
                       OR NEW.bucket_high_c IS DISTINCT FROM OLD.bucket_high_c
                       OR NEW.resolution_source IS DISTINCT FROM OLD.resolution_source
                       OR NEW.rules_raw IS DISTINCT FROM OLD.rules_raw
                       OR NEW.resolves_at IS DISTINCT FROM OLD.resolves_at
                       OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   ) THEN
                    RAISE EXCEPTION
                        'market identity/projection is sealed by Gamma outcome evidence';
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' AND OLD.resolution_event_id IS NOT NULL THEN
                IF NEW.resolution_event_id IS DISTINCT FROM OLD.resolution_event_id
                   OR NEW.polymarket_id IS DISTINCT FROM OLD.polymarket_id
                   OR NEW.event_id IS DISTINCT FROM OLD.event_id
                   OR NEW.condition_id IS DISTINCT FROM OLD.condition_id
                   OR NEW.yes_token_id IS DISTINCT FROM OLD.yes_token_id
                   OR NEW.no_token_id IS DISTINCT FROM OLD.no_token_id
                   OR NEW.station_id IS DISTINCT FROM OLD.station_id
                   OR NEW.market_date IS DISTINCT FROM OLD.market_date
                   OR NEW.kind IS DISTINCT FROM OLD.kind
                   OR NEW.bucket_low_c IS DISTINCT FROM OLD.bucket_low_c
                   OR NEW.bucket_high_c IS DISTINCT FROM OLD.bucket_high_c
                   OR NEW.resolution_source IS DISTINCT FROM OLD.resolution_source
                   OR NEW.rules_raw IS DISTINCT FROM OLD.rules_raw
                   OR NEW.resolves_at IS DISTINCT FROM OLD.resolves_at
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at
                   OR NEW.resolved_outcome IS DISTINCT FROM OLD.resolved_outcome
                   OR NEW.status IS DISTINCT FROM OLD.status THEN
                    RAISE EXCEPTION
                        'market identity/projection is sealed by Gamma outcome evidence';
                END IF;
            END IF;

            IF NEW.resolution_event_id IS NOT NULL THEN
                SELECT event_id
                INTO ledger_event_id
                FROM gamma_outcome_events
                WHERE id = NEW.resolution_event_id;
                IF NOT FOUND OR ledger_event_id IS DISTINCT FROM NEW.event_id THEN
                    RAISE EXCEPTION
                        'market resolution event does not match market event identity';
                END IF;

                SELECT value
                INTO vector_item
                FROM gamma_outcome_events AS goe,
                     LATERAL jsonb_array_elements(goe.outcome_vector)
                WHERE goe.id = NEW.resolution_event_id
                  AND (value->>'market_id')::bigint = NEW.id
                  AND value->>'polymarket_id' = NEW.polymarket_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'market is absent from Gamma outcome vector';
                END IF;
                IF (
                    NEW.condition_id IS NOT NULL
                    AND NEW.condition_id IS DISTINCT FROM vector_item->>'condition_id'
                ) OR (
                    NEW.yes_token_id IS NOT NULL
                    AND NEW.yes_token_id IS DISTINCT FROM vector_item->>'yes_token_id'
                ) OR (
                    NEW.no_token_id IS NOT NULL
                    AND NEW.no_token_id IS DISTINCT FROM vector_item->>'no_token_id'
                ) OR encode(
                    digest(convert_to(NEW.rules_raw, 'UTF8'), 'sha256'),
                    'hex'
                ) IS DISTINCT FROM vector_item->>'rules_hash' THEN
                    RAISE EXCEPTION
                        'market identity/rules do not match Gamma outcome vector';
                END IF;
                IF NEW.resolved_outcome IS NULL
                   OR NEW.resolved_outcome IS DISTINCT FROM
                      (vector_item->>'outcome_yes')::boolean THEN
                    RAISE EXCEPTION
                        'market resolved outcome does not match Gamma outcome vector';
                END IF;
                IF NEW.status::text IS DISTINCT FROM 'resolved' THEN
                    RAISE EXCEPTION
                        'market linked to Gamma outcome evidence must be resolved';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_markets_validate_gamma_outcome_projection
        BEFORE INSERT OR UPDATE OR DELETE ON markets
        FOR EACH ROW EXECUTE FUNCTION validate_market_gamma_outcome_projection()
        """
    )

    op.execute(
        """
        CREATE FUNCTION enforce_complete_gamma_outcome_projection()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            -- Deferred until transaction end so the normal atomic sequence
            -- (ledger insert, then exact market projections) is allowed, while
            -- a directly committed orphan ledger is impossible.
            IF (
                SELECT count(*)
                FROM markets
                WHERE event_id = NEW.event_id
            ) <> NEW.market_count OR EXISTS (
                SELECT 1
                FROM markets AS m
                LEFT JOIN LATERAL (
                    SELECT value
                    FROM jsonb_array_elements(NEW.outcome_vector)
                    WHERE (value->>'market_id')::bigint = m.id
                      AND value->>'polymarket_id' = m.polymarket_id
                ) AS vector_row ON true
                WHERE m.event_id = NEW.event_id
                  AND (
                      vector_row.value IS NULL
                      OR m.resolution_event_id IS DISTINCT FROM NEW.id
                      OR m.status::text IS DISTINCT FROM 'resolved'
                      OR m.resolved_outcome IS DISTINCT FROM
                         (vector_row.value->>'outcome_yes')::boolean
                  )
            ) THEN
                RAISE EXCEPTION
                    'Gamma outcome ledger requires a complete atomic market projection';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_gamma_outcome_projection_complete
        AFTER INSERT ON gamma_outcome_events
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION enforce_complete_gamma_outcome_projection()
        """
    )

    op.execute(
        """
        CREATE FUNCTION validate_forecast_evaluation_gamma_outcome()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            ledger_event_id text;
            ledger_vector_hash text;
            ledger_first_outcome_at timestamptz;
            market_event_id text;
            market_external_id text;
            market_resolution_event_id bigint;
            vector_item jsonb;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.resolution_event_id IS NOT NULL
                   OR OLD.resolution_vector_hash IS NOT NULL THEN
                    RAISE EXCEPTION
                        'attested forecast evaluation outcome provenance is immutable';
                END IF;
                RETURN OLD;
            END IF;
            IF TG_OP = 'UPDATE'
               AND OLD.resolution_event_id IS NOT NULL
               AND to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD) THEN
                RAISE EXCEPTION
                    'attested forecast evaluation outcome provenance is immutable';
            END IF;
            IF NEW.resolution_event_id IS NULL THEN
                IF NEW.resolution_vector_hash IS NOT NULL THEN
                    RAISE EXCEPTION
                        'forecast evaluation resolution hash requires an outcome event';
                END IF;
                RETURN NEW;
            END IF;
            IF NEW.resolution_vector_hash IS NULL
               OR NEW.resolution_vector_hash !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION
                    'forecast evaluation resolution hash must be lowercase SHA-256';
            END IF;

            SELECT event_id, outcome_vector_hash, first_outcome_at
            INTO ledger_event_id, ledger_vector_hash, ledger_first_outcome_at
            FROM gamma_outcome_events
            WHERE id = NEW.resolution_event_id;
            IF NOT FOUND
               OR ledger_vector_hash IS DISTINCT FROM NEW.resolution_vector_hash THEN
                RAISE EXCEPTION
                    'forecast evaluation does not match Gamma outcome evidence';
            END IF;

            SELECT event_id, polymarket_id, resolution_event_id
            INTO market_event_id, market_external_id, market_resolution_event_id
            FROM markets
            WHERE id = NEW.market_id;
            IF NOT FOUND
               OR market_event_id IS DISTINCT FROM ledger_event_id
               OR market_resolution_event_id IS DISTINCT FROM NEW.resolution_event_id THEN
                RAISE EXCEPTION
                    'forecast evaluation market does not match Gamma outcome event';
            END IF;

            SELECT value
            INTO vector_item
            FROM gamma_outcome_events AS goe,
                 LATERAL jsonb_array_elements(goe.outcome_vector)
            WHERE goe.id = NEW.resolution_event_id
              AND (value->>'market_id')::bigint = NEW.market_id
              AND value->>'polymarket_id' = market_external_id;
            IF NOT FOUND
               OR NEW.resolved_outcome IS DISTINCT FROM
                  (vector_item->>'outcome_yes')::boolean THEN
                RAISE EXCEPTION
                    'forecast evaluation outcome does not match Gamma outcome vector';
            END IF;
            IF NEW.decision_at >= ledger_first_outcome_at THEN
                RAISE EXCEPTION
                    'forecast evaluation decision is not pre-outcome evidence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_forecast_evaluations_validate_gamma_outcome
        BEFORE INSERT OR UPDATE OR DELETE ON forecast_evaluations
        FOR EACH ROW EXECUTE FUNCTION validate_forecast_evaluation_gamma_outcome()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_forecast_evaluations_validate_gamma_outcome "
        "ON forecast_evaluations"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_forecast_evaluation_gamma_outcome()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_gamma_outcome_projection_complete "
        "ON gamma_outcome_events"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_complete_gamma_outcome_projection()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_markets_validate_gamma_outcome_projection ON markets"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_market_gamma_outcome_projection()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_gamma_outcome_events_append_only "
        "ON gamma_outcome_events"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_gamma_outcome_event_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_gamma_outcome_events_validate_insert "
        "ON gamma_outcome_events"
    )
    op.execute("DROP FUNCTION IF EXISTS validate_gamma_outcome_event_insert()")

    op.drop_index(
        "ix_forecast_evaluations_resolution_event_id",
        table_name="forecast_evaluations",
    )
    op.drop_column("forecast_evaluations", "resolution_vector_hash")
    op.drop_constraint(
        "fk_forecast_evaluations_resolution_event",
        "forecast_evaluations",
        type_="foreignkey",
    )
    op.drop_column("forecast_evaluations", "resolution_event_id")
    op.drop_column("positions", "market_closed_at")
    op.drop_index("ix_markets_resolution_event_id", table_name="markets")
    op.drop_constraint(
        "fk_markets_resolution_event",
        "markets",
        type_="foreignkey",
    )
    op.drop_column("markets", "resolution_event_id")
    op.drop_index("ix_gamma_outcome_events_first_seen", table_name="gamma_outcome_events")
    op.drop_table("gamma_outcome_events")
