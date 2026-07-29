"""Add zero-backfill immutable forecast-source provenance.

Revision ID: 20260715_0037
Revises: 20260714_0036
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_0037"
down_revision: str | None = "20260714_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINITION_HASH = "d2e2d65be7dfca58c10c25b37707ec09b0941ef67039f5d8951cf1083810fe34"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "forecast_provenance_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_forecast_provenance_runtime_singleton"),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_provenance_runtime_hash",
        ),
    )
    # This database-clock watermark is the prospective boundary.  No legacy
    # forecast is copied into either evidence table by this migration.
    op.execute(
        "INSERT INTO forecast_provenance_runtime "
        "(id, opened_at, definition_version, definition_hash) VALUES "
        f"(1, clock_timestamp(), 'forecast-provenance-v1', '{DEFINITION_HASH}')"
    )

    op.create_table(
        "forecast_ingests",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "run_id",
            sa.BigInteger(),
            sa.ForeignKey("forecast_runs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_model", sa.String(length=64), nullable=False),
        sa.Column("collector", sa.String(length=64), nullable=False),
        sa.Column("collection_protocol", sa.String(length=32), nullable=False),
        sa.Column("pipeline_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("upstream_init_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("upstream_init_verified", sa.Boolean(), nullable=False),
        sa.Column("availability_semantics", sa.String(length=96), nullable=False),
        sa.Column("normalized_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("normalized_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_response_count", sa.SmallInteger(), nullable=False),
        sa.Column("response_set_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "collection_protocol IN ('prospective-http-v1', 'historical-backfill-v1')",
            name="ck_forecast_ingests_protocol",
        ),
        sa.CheckConstraint(
            "expected_response_count BETWEEN 1 AND 32",
            name="ck_forecast_ingests_response_count",
        ),
        sa.CheckConstraint(
            "NOT upstream_init_verified OR "
            "(upstream_init_time IS NOT NULL AND run_id IS NOT NULL)",
            name="ck_forecast_ingests_verified_init",
        ),
        sa.CheckConstraint(
            "normalized_manifest_sha256 ~ '^[0-9a-f]{64}$' AND "
            "response_set_sha256 ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_ingests_hashes",
        ),
    )
    op.create_index(
        "ix_forecast_ingests_station_seen",
        "forecast_ingests",
        ["station_id", "first_seen_at"],
    )
    op.create_index("ix_forecast_ingests_run", "forecast_ingests", ["run_id"])

    op.create_table(
        "forecast_source_responses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ingest_id",
            sa.BigInteger(),
            sa.ForeignKey("forecast_ingests.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.SmallInteger(), nullable=False),
        sa.Column("request_method", sa.String(length=8), nullable=False),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("http_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "ingest_id",
            "sequence_no",
            name="uq_forecast_source_responses_ingest_sequence",
        ),
        sa.CheckConstraint("sequence_no >= 0", name="ck_forecast_source_responses_sequence"),
        sa.CheckConstraint(
            "response_received_at >= request_started_at",
            name="ck_forecast_source_responses_timing",
        ),
        sa.CheckConstraint("response_bytes > 0", name="ck_forecast_source_responses_bytes"),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' AND "
            "response_sha256 ~ '^[0-9a-f]{64}$' AND "
            "evidence_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_source_responses_hashes",
        ),
    )

    op.add_column("forecasts", sa.Column("ingest_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_forecasts_ingest_id",
        "forecasts",
        "forecast_ingests",
        ["ingest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_forecasts_ingest_id", "forecasts", ["ingest_id"])
    op.add_column(
        "forecast_trajectories",
        sa.Column("ingest_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_forecast_trajectories_ingest_id",
        "forecast_trajectories",
        "forecast_ingests",
        ["ingest_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_forecast_trajectories_ingest_id",
        "forecast_trajectories",
        ["ingest_id"],
    )

    # Canonical compact key-sorted JSON is shared by Python and PostgreSQL,
    # making every digest independently verifiable at the database boundary.
    op.execute(
        r"""
        CREATE FUNCTION forecast_provenance_canonical_json(value jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE result text;
        BEGIN
            CASE jsonb_typeof(value)
                WHEN 'object' THEN
                    SELECT '{' || coalesce(
                        string_agg(
                            to_jsonb(item.key)::text || ':' ||
                            forecast_provenance_canonical_json(item.value),
                            ',' ORDER BY item.key
                        ), '') || '}'
                    INTO result FROM jsonb_each(value) AS item;
                    RETURN result;
                WHEN 'array' THEN
                    SELECT '[' || coalesce(
                        string_agg(
                            forecast_provenance_canonical_json(item.value),
                            ',' ORDER BY item.ordinal
                        ), '') || ']'
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
        CREATE FUNCTION forecast_provenance_hash(value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT encode(
                digest(
                    convert_to(forecast_provenance_canonical_json(value), 'UTF8'),
                    'sha256'
                ), 'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION forecast_provenance_iso_utc(value timestamptz)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT to_char(value AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS') ||
                   CASE
                       WHEN extract(microseconds FROM value)::bigint % 1000000 = 0
                           THEN '.000000'
                       ELSE '.' || to_char(value AT TIME ZONE 'UTC', 'US')
                   END || 'Z'
        $$
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_ingest_validate_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            expected_hash text;
            run_init timestamptz;
        BEGIN
            NEW.first_seen_at := clock_timestamp();
            IF NEW.request_model IS NULL OR btrim(NEW.request_model) = ''
               OR NEW.collector IS NULL OR btrim(NEW.collector) = ''
               OR NEW.pipeline_fingerprint IS NULL OR btrim(NEW.pipeline_fingerprint) = ''
               OR NEW.availability_semantics IS NULL
               OR btrim(NEW.availability_semantics) = '' THEN
                RAISE EXCEPTION 'forecast ingest identity is incomplete';
            END IF;
            IF NEW.normalized_manifest IS NULL
               OR jsonb_typeof(NEW.normalized_manifest) IS DISTINCT FROM 'object'
               OR NEW.normalized_manifest->>'schema'
                  IS DISTINCT FROM 'forecast-normalized-manifest-v1'
               OR jsonb_typeof(NEW.normalized_manifest->'forecasts')
                  IS DISTINCT FROM 'array'
               OR jsonb_typeof(NEW.normalized_manifest->'trajectories')
                  IS DISTINCT FROM 'array'
               OR (NEW.normalized_manifest->>'station_id')::bigint
                  IS DISTINCT FROM NEW.station_id
               OR (NEW.normalized_manifest->>'forecast_count')::integer
                  IS DISTINCT FROM jsonb_array_length(NEW.normalized_manifest->'forecasts')
               OR (NEW.normalized_manifest->>'trajectory_count')::integer
                  IS DISTINCT FROM jsonb_array_length(NEW.normalized_manifest->'trajectories')
               OR (
                    jsonb_array_length(NEW.normalized_manifest->'forecasts') +
                    jsonb_array_length(NEW.normalized_manifest->'trajectories')
                  ) = 0 THEN
                RAISE EXCEPTION 'forecast ingest normalized manifest is invalid';
            END IF;
            IF forecast_provenance_hash(NEW.normalized_manifest)
               IS DISTINCT FROM NEW.normalized_manifest_sha256 THEN
                RAISE EXCEPTION 'forecast ingest normalized manifest hash mismatch';
            END IF;
            IF NEW.run_id IS NOT NULL THEN
                SELECT init_time INTO STRICT run_init
                FROM forecast_runs WHERE id = NEW.run_id;
                IF NEW.upstream_init_verified
                   AND run_init IS DISTINCT FROM NEW.upstream_init_time THEN
                    RAISE EXCEPTION 'verified upstream initialization disagrees with run';
                END IF;
            END IF;
            expected_hash := forecast_provenance_hash(jsonb_build_object(
                'availability_semantics', NEW.availability_semantics,
                'collection_protocol', NEW.collection_protocol,
                'collector', NEW.collector,
                'expected_response_count', NEW.expected_response_count,
                'normalized_manifest_sha256', NEW.normalized_manifest_sha256,
                'pipeline_fingerprint', NEW.pipeline_fingerprint,
                'request_model', NEW.request_model,
                'response_set_sha256', NEW.response_set_sha256,
                'run_id', NEW.run_id,
                'station_id', NEW.station_id,
                'upstream_init_time', CASE WHEN NEW.upstream_init_time IS NULL
                    THEN NULL ELSE forecast_provenance_iso_utc(NEW.upstream_init_time) END,
                'upstream_init_verified', NEW.upstream_init_verified
            ));
            IF NEW.record_hash IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'forecast ingest record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_ingest_validate_insert "
        "BEFORE INSERT ON forecast_ingests FOR EACH ROW "
        "EXECUTE FUNCTION forecast_ingest_validate_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_source_response_validate_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent forecast_ingests%ROWTYPE;
            expected_request_hash text;
            expected_evidence_hash text;
            expected_record_hash text;
        BEGIN
            SELECT * INTO STRICT parent FROM forecast_ingests WHERE id = NEW.ingest_id;
            IF NEW.sequence_no < 0 OR NEW.sequence_no >= parent.expected_response_count THEN
                RAISE EXCEPTION 'forecast source response sequence is outside ingest contract';
            END IF;
            IF NEW.request_method IS NULL OR btrim(NEW.request_method) = ''
               OR NEW.source_url IS NULL OR btrim(NEW.source_url) = '' THEN
                RAISE EXCEPTION 'forecast source request identity is incomplete';
            END IF;
            IF NEW.response_received_at < NEW.request_started_at
               OR NEW.response_received_at > parent.first_seen_at THEN
                RAISE EXCEPTION 'forecast source response timing is invalid';
            END IF;
            IF octet_length(NEW.response_body) <> NEW.response_bytes
               OR encode(digest(NEW.response_body, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.response_sha256 THEN
                RAISE EXCEPTION 'forecast source response bytes do not match size/hash';
            END IF;
            expected_request_hash := forecast_provenance_hash(jsonb_build_object(
                'request_method', NEW.request_method,
                'source_url', NEW.source_url
            ));
            IF NEW.request_sha256 IS DISTINCT FROM expected_request_hash THEN
                RAISE EXCEPTION 'forecast source request hash mismatch';
            END IF;
            expected_evidence_hash := forecast_provenance_hash(jsonb_build_object(
                'etag', NEW.etag,
                'http_date', CASE WHEN NEW.http_date IS NULL THEN NULL
                    ELSE forecast_provenance_iso_utc(NEW.http_date) END,
                'request_method', NEW.request_method,
                'request_sha256', NEW.request_sha256,
                'request_started_at', forecast_provenance_iso_utc(NEW.request_started_at),
                'response_bytes', NEW.response_bytes,
                'response_received_at', forecast_provenance_iso_utc(NEW.response_received_at),
                'response_sha256', NEW.response_sha256,
                'source_url', NEW.source_url
            ));
            IF NEW.evidence_hash IS DISTINCT FROM expected_evidence_hash THEN
                RAISE EXCEPTION 'forecast source evidence hash mismatch';
            END IF;
            expected_record_hash := forecast_provenance_hash(jsonb_build_object(
                'evidence_hash', NEW.evidence_hash,
                'ingest_record_hash', parent.record_hash
            ));
            IF NEW.record_hash IS DISTINCT FROM expected_record_hash THEN
                RAISE EXCEPTION 'forecast source response record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_source_response_validate_insert "
        "BEFORE INSERT ON forecast_source_responses FOR EACH ROW "
        "EXECUTE FUNCTION forecast_source_response_validate_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_ingest_verify_response_set()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actual_count integer;
            actual_hash text;
        BEGIN
            SELECT count(*), forecast_provenance_hash(
                       coalesce(jsonb_agg(evidence_hash ORDER BY sequence_no), '[]'::jsonb)
                   )
            INTO actual_count, actual_hash
            FROM forecast_source_responses WHERE ingest_id = NEW.id;
            IF actual_count IS DISTINCT FROM NEW.expected_response_count
               OR actual_hash IS DISTINCT FROM NEW.response_set_sha256 THEN
                RAISE EXCEPTION 'forecast ingest source response set is incomplete or mismatched';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_forecast_ingest_verify_response_set "
        "AFTER INSERT ON forecast_ingests DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION forecast_ingest_verify_response_set()"
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_provenance_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table_name in ("forecast_ingests", "forecast_source_responses"):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION forecast_provenance_reject_mutation()"
        )

    op.execute(
        r"""
        CREATE FUNCTION forecast_run_provenance_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM forecast_ingests
                WHERE run_id = OLD.id AND collection_protocol = 'prospective-http-v1'
            ) OR EXISTS (
                SELECT 1
                FROM forecasts AS value
                JOIN forecast_ingests AS ingest ON ingest.id = value.ingest_id
                WHERE value.run_id = OLD.id
                  AND ingest.collection_protocol = 'prospective-http-v1'
            ) OR EXISTS (
                SELECT 1
                FROM forecast_trajectories AS value
                JOIN forecast_ingests AS ingest ON ingest.id = value.ingest_id
                WHERE value.run_id = OLD.id
                  AND ingest.collection_protocol = 'prospective-http-v1'
            ) THEN
                RAISE EXCEPTION 'forecast run cited by prospective provenance is immutable';
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
        "CREATE TRIGGER trg_forecast_run_provenance_guard "
        "BEFORE UPDATE OR DELETE ON forecast_runs FOR EACH ROW "
        "EXECUTE FUNCTION forecast_run_provenance_guard()"
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_materialization_validate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            boundary timestamptz;
            ingest forecast_ingests%ROWTYPE;
            child_run_init timestamptz;
            child_run_model text;
        BEGIN
            SELECT opened_at INTO STRICT boundary
            FROM forecast_provenance_runtime WHERE id = 1;
            IF NEW.ingest_id IS NULL THEN
                IF NEW.fetched_at < boundary THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION 'prospective forecast requires immutable ingest provenance';
            END IF;
            SELECT * INTO STRICT ingest FROM forecast_ingests WHERE id = NEW.ingest_id;
            IF ingest.station_id IS DISTINCT FROM NEW.station_id THEN
                RAISE EXCEPTION 'forecast station disagrees with ingest';
            END IF;
            IF ingest.collection_protocol = 'prospective-http-v1' THEN
                IF NEW.run_id IS NULL THEN
                    RAISE EXCEPTION 'prospective forecast requires a run id';
                END IF;
                IF NEW.available_at IS DISTINCT FROM ingest.first_seen_at THEN
                    RAISE EXCEPTION 'forecast availability must equal database first-seen';
                END IF;
                IF ingest.run_id IS NOT NULL AND NEW.run_id IS DISTINCT FROM ingest.run_id THEN
                    RAISE EXCEPTION 'forecast run disagrees with single-run ingest';
                END IF;
                SELECT run.init_time, registry.model
                INTO STRICT child_run_init, child_run_model
                FROM forecast_runs AS run
                JOIN forecast_model_registry AS registry ON registry.id = run.registry_id
                WHERE run.id = NEW.run_id;
                IF child_run_init IS DISTINCT FROM NEW.run_time
                   OR NOT (
                       NEW.model = child_run_model
                       OR NEW.model LIKE child_run_model || '\_m%' ESCAPE '\'
                   ) THEN
                    RAISE EXCEPTION 'forecast model/run identity is inconsistent';
                END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(ingest.normalized_manifest->'forecasts') AS item
                WHERE (item->>'station_id')::bigint = NEW.station_id
                  AND item->>'model' = NEW.model
                  AND (item->>'run_time')::timestamptz = NEW.run_time
                  AND (item->>'valid_date')::date = NEW.valid_date
                  AND item->>'kind' = NEW.kind::text
                  AND (item->>'temp_c')::numeric = NEW.temp_c
            ) THEN
                RAISE EXCEPTION 'forecast value is absent from immutable normalized manifest';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_materialization_validate "
        "BEFORE INSERT OR UPDATE ON forecasts FOR EACH ROW "
        "EXECUTE FUNCTION forecast_materialization_validate()"
    )

    op.execute(
        r"""
        CREATE FUNCTION forecast_trajectory_materialization_validate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            boundary timestamptz;
            ingest forecast_ingests%ROWTYPE;
            child_run_init timestamptz;
            child_run_model text;
        BEGIN
            SELECT opened_at INTO STRICT boundary
            FROM forecast_provenance_runtime WHERE id = 1;
            IF NEW.ingest_id IS NULL THEN
                IF NEW.fetched_at < boundary THEN
                    RETURN NEW;
                END IF;
                RAISE EXCEPTION
                    'prospective forecast trajectory requires immutable ingest provenance';
            END IF;
            SELECT * INTO STRICT ingest FROM forecast_ingests WHERE id = NEW.ingest_id;
            IF ingest.station_id IS DISTINCT FROM NEW.station_id THEN
                RAISE EXCEPTION 'forecast trajectory station disagrees with ingest';
            END IF;
            IF ingest.collection_protocol = 'prospective-http-v1' THEN
                IF NEW.run_id IS NULL THEN
                    RAISE EXCEPTION 'prospective forecast trajectory requires a run id';
                END IF;
                IF NEW.available_at IS DISTINCT FROM ingest.first_seen_at THEN
                    RAISE EXCEPTION
                        'forecast trajectory availability must equal database first-seen';
                END IF;
                IF ingest.run_id IS NOT NULL AND NEW.run_id IS DISTINCT FROM ingest.run_id THEN
                    RAISE EXCEPTION 'forecast trajectory run disagrees with single-run ingest';
                END IF;
                SELECT run.init_time, registry.model
                INTO STRICT child_run_init, child_run_model
                FROM forecast_runs AS run
                JOIN forecast_model_registry AS registry ON registry.id = run.registry_id
                WHERE run.id = NEW.run_id;
                IF child_run_init IS DISTINCT FROM NEW.run_time
                   OR NOT (
                       NEW.model = child_run_model
                       OR NEW.model LIKE child_run_model || '\_m%' ESCAPE '\'
                   ) THEN
                    RAISE EXCEPTION 'forecast trajectory model/run identity is inconsistent';
                END IF;
            END IF;
            IF NOT EXISTS (
                SELECT 1
                FROM jsonb_array_elements(ingest.normalized_manifest->'trajectories') AS item
                WHERE (item->>'station_id')::bigint = NEW.station_id
                  AND item->>'model' = NEW.model
                  AND (item->>'run_time')::timestamptz = NEW.run_time
                  AND (item->>'valid_date')::date = NEW.valid_date
                  AND (
                      SELECT jsonb_agg(
                          jsonb_build_object(
                              'local_time', point.value->>'local_time',
                              'temp_c', (point.value->>'temp_c')::numeric
                          ) ORDER BY point.ordinal
                      )
                      FROM jsonb_array_elements(item->'points')
                           WITH ORDINALITY AS point(value, ordinal)
                  ) = NEW.points
            ) THEN
                RAISE EXCEPTION 'forecast trajectory is absent from immutable normalized manifest';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_forecast_trajectory_materialization_validate "
        "BEFORE INSERT OR UPDATE ON forecast_trajectories FOR EACH ROW "
        "EXECUTE FUNCTION forecast_trajectory_materialization_validate()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            r"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM forecast_ingests)
                   OR EXISTS (SELECT 1 FROM forecasts WHERE ingest_id IS NOT NULL)
                   OR EXISTS (SELECT 1 FROM forecast_trajectories WHERE ingest_id IS NOT NULL) THEN
                    RAISE EXCEPTION USING
                        MESSAGE = 'refusing 0037 downgrade: forecast provenance state exists',
                        HINT = 'export and explicitly reset forecast provenance before downgrade';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(
        "DROP TRIGGER trg_forecast_trajectory_materialization_validate "
        "ON forecast_trajectories"
    )
    op.execute("DROP TRIGGER trg_forecast_materialization_validate ON forecasts")
    op.execute("DROP TRIGGER trg_forecast_run_provenance_guard ON forecast_runs")
    op.execute(
        "DROP TRIGGER trg_forecast_source_responses_append_only "
        "ON forecast_source_responses"
    )
    op.execute("DROP TRIGGER trg_forecast_ingests_append_only ON forecast_ingests")
    op.execute(
        "DROP TRIGGER trg_forecast_ingest_verify_response_set ON forecast_ingests"
    )
    op.execute(
        "DROP TRIGGER trg_forecast_source_response_validate_insert "
        "ON forecast_source_responses"
    )
    op.execute("DROP TRIGGER trg_forecast_ingest_validate_insert ON forecast_ingests")
    op.execute("DROP FUNCTION forecast_trajectory_materialization_validate()")
    op.execute("DROP FUNCTION forecast_materialization_validate()")
    op.execute("DROP FUNCTION forecast_run_provenance_guard()")
    op.execute("DROP FUNCTION forecast_provenance_reject_mutation()")
    op.execute("DROP FUNCTION forecast_ingest_verify_response_set()")
    op.execute("DROP FUNCTION forecast_source_response_validate_insert()")
    op.execute("DROP FUNCTION forecast_ingest_validate_insert()")
    op.execute("DROP FUNCTION forecast_provenance_iso_utc(timestamptz)")
    op.execute("DROP FUNCTION forecast_provenance_hash(jsonb)")
    op.execute("DROP FUNCTION forecast_provenance_canonical_json(jsonb)")
    op.drop_index("ix_forecast_trajectories_ingest_id", table_name="forecast_trajectories")
    op.drop_constraint(
        "fk_forecast_trajectories_ingest_id",
        "forecast_trajectories",
        type_="foreignkey",
    )
    op.drop_column("forecast_trajectories", "ingest_id")
    op.drop_index("ix_forecasts_ingest_id", table_name="forecasts")
    op.drop_constraint("fk_forecasts_ingest_id", "forecasts", type_="foreignkey")
    op.drop_column("forecasts", "ingest_id")
    op.drop_table("forecast_source_responses")
    op.drop_index("ix_forecast_ingests_run", table_name="forecast_ingests")
    op.drop_index("ix_forecast_ingests_station_seen", table_name="forecast_ingests")
    op.drop_table("forecast_ingests")
    op.drop_table("forecast_provenance_runtime")
