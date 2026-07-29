"""Add prospective, append-only observation recovery evidence.

Revision ID: 20260717_0043
Revises: 20260717_0042
Create Date: 2026-07-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260717_0043"
down_revision: str | None = "20260717_0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINITION_HASH = "15c64e764eff5778d8a156f09c0404e728a5427be22a06d458558db790411b59"
RETENTION_POLICY_HASH = "bdd514125aeb91bbde8a4728063b8862010c821d05427572954b239085cc7535"
IEM_ASOS_URL_HASH = "7d7cff72adb53f6a374175396a1f86eeab9863c5c26d38bf1aa49adf0bdc5891"
WEATHER_KIND = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "observation_recovery_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("source_protocol", sa.String(length=64), nullable=False),
        sa.Column("source_url_sha256", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("completeness_version", sa.String(length=32), nullable=False),
        sa.Column("retention_policy_version", sa.String(length=32), nullable=False),
        sa.Column("retention_policy_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_observation_recovery_runtime_singleton"),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "source_url_sha256 ~ '^[0-9a-f]{64}$' AND "
            "retention_policy_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_observation_recovery_runtime_hash",
        ),
    )
    # Database-clock, zero-backfill boundary.  No legacy observation or daily
    # actual is copied into the recovery ledger by this migration.
    op.execute(
        "INSERT INTO observation_recovery_runtime "
        "(id, opened_at, definition_version, definition_hash, source_protocol, "
        "source_url_sha256, parser_version, completeness_version, retention_policy_version, "
        "retention_policy_sha256) VALUES "
        f"(1, clock_timestamp(), 'observation-recovery-v1', '{DEFINITION_HASH}', "
        f"'iem-asos-recovery-v1', '{IEM_ASOS_URL_HASH}', "
        "'iem-csv-evidence-v2', 'daily-extremes-v1', "
        f"'iem-retention-review-v1', '{RETENTION_POLICY_HASH}')"
    )

    op.create_table(
        "observation_recovery_artifacts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("utc_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("utc_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("network", sa.String(length=32), nullable=False),
        sa.Column("source_station_id", sa.String(length=16), nullable=False),
        sa.Column("source_name", sa.String(length=64), nullable=False),
        sa.Column("source_protocol", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("completeness_version", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("request_method", sa.String(length=8), nullable=False),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column(
            "request_manifest",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("request_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("http_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("response_body", sa.LargeBinary(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=False),
        sa.Column("retention_policy_url", sa.Text(), nullable=False),
        sa.Column("retention_policy_reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_policy_version", sa.String(length=32), nullable=False),
        sa.Column("retention_policy_statement", sa.LargeBinary(), nullable=False),
        sa.Column("retention_policy_sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_permitted", sa.Boolean(), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("point_set_sha256", sa.String(length=64), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("minimum_samples", sa.Integer(), nullable=False),
        sa.Column("boundary_tolerance_seconds", sa.Integer(), nullable=False),
        sa.Column("max_permitted_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("first_observation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observation_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_gap_seconds", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validation_code", sa.String(length=64), nullable=False),
        sa.Column(
            "validation_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "record_hash", name="uq_observation_recovery_artifacts_record_hash"
        ),
        sa.UniqueConstraint(
            "station_id",
            "local_date",
            "source_protocol",
            "request_manifest_sha256",
            "response_sha256",
            name="uq_observation_recovery_artifacts_source_identity",
        ),
        sa.CheckConstraint(
            "response_received_at >= request_started_at",
            name="ck_observation_recovery_artifact_request_timing",
        ),
        sa.CheckConstraint(
            "http_status BETWEEN 200 AND 299 AND response_bytes > 0 "
            "AND point_count > 0 AND sample_n = point_count "
            "AND minimum_samples > 0 AND boundary_tolerance_seconds >= 0 "
            "AND max_permitted_gap_seconds > 0 AND max_gap_seconds >= 0 AND "
            "last_observation_at >= first_observation_at",
            name="ck_observation_recovery_artifact_counts",
        ),
        sa.CheckConstraint(
            "validation_status IN ('accepted', 'quarantined')",
            name="ck_observation_recovery_artifact_status",
        ),
        sa.CheckConstraint(
            "storage_permitted AND ((validation_status = 'accepted' "
            "AND validation_code = 'complete') OR validation_status = 'quarantined')",
            name="ck_observation_recovery_artifact_acceptance",
        ),
        sa.CheckConstraint(
            "request_manifest_sha256 ~ '^[0-9a-f]{64}$' AND "
            "response_sha256 ~ '^[0-9a-f]{64}$' AND "
            "retention_policy_sha256 ~ '^[0-9a-f]{64}$' AND "
            "point_set_sha256 ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_observation_recovery_artifact_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_manifest) = 'object' AND "
            "jsonb_typeof(validation_details) = 'object'",
            name="ck_observation_recovery_artifact_details",
        ),
    )
    op.create_index(
        "ix_observation_recovery_artifacts_station_date",
        "observation_recovery_artifacts",
        ["station_id", "local_date", "source_protocol", "first_seen_at"],
    )
    op.create_index(
        "uq_observation_recovery_artifacts_accepted",
        "observation_recovery_artifacts",
        ["station_id", "local_date", "source_protocol"],
        unique=True,
        postgresql_where=sa.text("validation_status = 'accepted'"),
    )

    op.create_table(
        "observation_recovery_points",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "artifact_id",
            sa.BigInteger(),
            sa.ForeignKey("observation_recovery_artifacts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("obs_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temp_c", sa.Numeric(6, 2), nullable=False),
        sa.Column("raw_report", sa.Text(), nullable=False),
        sa.Column("source_row_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("source_row_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalized_point_bytes", sa.LargeBinary(), nullable=False),
        sa.Column("normalized_point_sha256", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "artifact_id",
            "sequence_no",
            name="uq_observation_recovery_points_artifact_sequence",
        ),
        sa.UniqueConstraint(
            "record_hash", name="uq_observation_recovery_points_record_hash"
        ),
        sa.CheckConstraint(
            "sequence_no >= 0", name="ck_observation_recovery_points_sequence"
        ),
        sa.CheckConstraint(
            "source_row_sha256 ~ '^[0-9a-f]{64}$' AND "
            "normalized_point_sha256 ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_observation_recovery_points_hashes",
        ),
    )
    op.create_index(
        "ix_observation_recovery_points_artifact_time",
        "observation_recovery_points",
        ["artifact_id", "obs_time"],
    )

    op.add_column(
        "daily_actuals",
        sa.Column("recovery_artifact_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_daily_actuals_recovery_artifact",
        "daily_actuals",
        "observation_recovery_artifacts",
        ["recovery_artifact_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column(
        "daily_actuals", sa.Column("source_protocol", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "daily_actuals",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Existing rows remain NULL.  Every row inserted after this prospective
    # boundary receives a database-clock availability witness, including the
    # ordinary live finalizer path.
    op.alter_column(
        "daily_actuals",
        "first_seen_at",
        server_default=sa.text("clock_timestamp()"),
    )
    op.add_column("daily_actuals", sa.Column("sample_n", sa.Integer(), nullable=True))
    op.add_column(
        "daily_actuals",
        sa.Column("first_observation_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "daily_actuals",
        sa.Column("last_observation_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "daily_actuals", sa.Column("max_gap_seconds", sa.Integer(), nullable=True)
    )
    op.add_column(
        "daily_actuals", sa.Column("record_hash", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "ix_daily_actuals_recovery_artifact",
        "daily_actuals",
        ["recovery_artifact_id"],
    )
    op.create_check_constraint(
        "ck_daily_actuals_recovery_metadata",
        "daily_actuals",
        "(recovery_artifact_id IS NULL AND source_protocol IS NULL "
        "AND sample_n IS NULL "
        "AND first_observation_at IS NULL AND last_observation_at IS NULL "
        "AND max_gap_seconds IS NULL AND record_hash IS NULL) OR "
        "(recovery_artifact_id IS NOT NULL AND source_protocol IS NOT NULL "
        "AND first_seen_at IS NOT NULL AND sample_n > 0 "
        "AND first_observation_at IS NOT NULL AND last_observation_at IS NOT NULL "
        "AND max_gap_seconds >= 0 AND record_hash ~ '^[0-9a-f]{64}$')",
    )

    # Previous-runs forecasts are also training inputs.  Preserve legacy rows
    # as pre-boundary NULLs, while every new row gets a PostgreSQL availability
    # witness and becomes immutable under the active predictive cohort.
    op.add_column(
        "historical_forecasts",
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.alter_column(
        "historical_forecasts",
        "first_seen_at",
        server_default=sa.text("clock_timestamp()"),
    )
    op.create_index(
        "ix_historical_forecasts_first_seen",
        "historical_forecasts",
        ["first_seen_at"],
    )

    op.execute(
        r"""
        CREATE FUNCTION observation_recovery_artifact_validate_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            boundary timestamptz;
            expected_source_protocol text;
            expected_source_url_hash text;
            expected_parser_version text;
            expected_completeness_version text;
            expected_policy_version text;
            expected_policy_hash text;
            station_timezone text;
            station_icao text;
            station_network text;
            station_source_id text;
            local_start timestamptz;
            local_end timestamptz;
            expected_record_hash text;
        BEGIN
            SELECT opened_at, source_protocol, source_url_sha256, parser_version,
                   completeness_version, retention_policy_version,
                   retention_policy_sha256
            INTO STRICT boundary, expected_source_protocol,
                 expected_source_url_hash, expected_parser_version,
                 expected_completeness_version,
                 expected_policy_version, expected_policy_hash
            FROM observation_recovery_runtime WHERE id = 1;
            SELECT tz, icao, iem_network, iem_station_id
            INTO STRICT station_timezone, station_icao,
                 station_network, station_source_id
            FROM stations WHERE id = NEW.station_id;
            NEW.first_seen_at := clock_timestamp();
            IF NEW.response_received_at < boundary
               OR NEW.response_received_at > NEW.first_seen_at
               OR NEW.retention_policy_reviewed_at > NEW.first_seen_at THEN
                RAISE EXCEPTION
                    'observation recovery evidence predates or exceeds runtime boundary';
            END IF;
            IF btrim(NEW.source_name) = '' OR btrim(NEW.source_protocol) = ''
               OR btrim(NEW.source_url) = '' OR btrim(NEW.timezone) = ''
               OR btrim(NEW.content_type) = ''
               OR btrim(NEW.network) = '' OR btrim(NEW.source_station_id) = ''
               OR btrim(NEW.retention_policy_url) = ''
               OR btrim(NEW.retention_policy_version) = ''
               OR btrim(NEW.parser_version) = ''
               OR btrim(NEW.completeness_version) = ''
               OR btrim(NEW.request_method) = '' OR btrim(NEW.validation_code) = '' THEN
                RAISE EXCEPTION 'observation recovery artifact identity is incomplete';
            END IF;
            IF NOT NEW.storage_permitted
               OR NEW.source_protocol IS DISTINCT FROM expected_source_protocol
               OR encode(digest(convert_to(NEW.source_url, 'UTF8'), 'sha256'), 'hex')
                  IS DISTINCT FROM expected_source_url_hash
               OR NEW.parser_version IS DISTINCT FROM expected_parser_version
               OR NEW.completeness_version IS DISTINCT FROM expected_completeness_version
               OR NEW.retention_policy_version IS DISTINCT FROM expected_policy_version
               OR NEW.retention_policy_sha256 IS DISTINCT FROM expected_policy_hash THEN
                RAISE EXCEPTION
                    'observation recovery source authorization policy mismatch';
            END IF;
            IF octet_length(NEW.response_body) IS DISTINCT FROM NEW.response_bytes
               OR encode(digest(NEW.response_body, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.response_sha256
               OR octet_length(NEW.retention_policy_statement) = 0
               OR encode(digest(NEW.retention_policy_statement, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.retention_policy_sha256 THEN
                RAISE EXCEPTION 'observation recovery exact bytes do not match size/hash';
            END IF;
            IF forecast_provenance_hash(NEW.request_manifest)
                  IS DISTINCT FROM NEW.request_manifest_sha256
               OR NEW.request_manifest->>'schema_version'
                  IS DISTINCT FROM 'stratus.iem-request.v1'
               OR (NEW.request_manifest->>'station_id')::bigint
                  IS DISTINCT FROM NEW.station_id
               OR upper(NEW.request_manifest->>'icao') IS DISTINCT FROM upper(station_icao)
               OR NEW.request_manifest->>'network' IS DISTINCT FROM NEW.network
               OR upper(NEW.request_manifest->>'source_station_id')
                  IS DISTINCT FROM upper(NEW.source_station_id)
               OR NEW.request_manifest->>'source_url' IS DISTINCT FROM NEW.source_url
               OR jsonb_typeof(NEW.request_manifest->'params') IS DISTINCT FROM 'array'
               OR (NEW.request_manifest->>'start')::date > NEW.utc_start::date
               OR (NEW.request_manifest->>'end')::date
                  <= (NEW.utc_end - interval '1 microsecond')::date THEN
                RAISE EXCEPTION 'observation recovery request manifest mismatch';
            END IF;
            IF NEW.network IS DISTINCT FROM station_network
               OR upper(NEW.source_station_id) IS DISTINCT FROM upper(station_source_id) THEN
                RAISE EXCEPTION 'observation recovery station source mapping mismatch';
            END IF;
            local_start := NEW.local_date::timestamp AT TIME ZONE station_timezone;
            local_end := (NEW.local_date + 1)::timestamp AT TIME ZONE station_timezone;
            IF NEW.timezone IS DISTINCT FROM station_timezone
               OR NEW.utc_start IS DISTINCT FROM local_start
               OR NEW.utc_end IS DISTINCT FROM local_end
               OR NEW.response_received_at < NEW.utc_end THEN
                RAISE EXCEPTION 'observation recovery local-day identity mismatch';
            END IF;
            IF NEW.validation_status = 'accepted' AND (
                NEW.sample_n < NEW.minimum_samples
                OR NEW.first_observation_at < local_start
                OR NEW.last_observation_at >= local_end
                OR extract(epoch FROM NEW.first_observation_at - local_start)
                   > NEW.boundary_tolerance_seconds
                OR extract(epoch FROM local_end - NEW.last_observation_at)
                   > NEW.boundary_tolerance_seconds
                OR NEW.max_gap_seconds > NEW.max_permitted_gap_seconds
            ) THEN
                RAISE EXCEPTION
                    'accepted observation recovery artifact fails persisted thresholds';
            END IF;
            expected_record_hash := forecast_provenance_hash(jsonb_build_object(
                'boundary_tolerance_seconds', NEW.boundary_tolerance_seconds,
                'completeness_version', NEW.completeness_version,
                'etag', NEW.etag,
                'first_observation_at', forecast_provenance_iso_utc(NEW.first_observation_at),
                'http_date', CASE WHEN NEW.http_date IS NULL THEN NULL
                    ELSE forecast_provenance_iso_utc(NEW.http_date) END,
                'http_status', NEW.http_status,
                'content_type', NEW.content_type,
                'last_observation_at', forecast_provenance_iso_utc(NEW.last_observation_at),
                'local_date', NEW.local_date::text,
                'max_gap_seconds', NEW.max_gap_seconds,
                'max_permitted_gap_seconds', NEW.max_permitted_gap_seconds,
                'minimum_samples', NEW.minimum_samples,
                'network', NEW.network,
                'point_count', NEW.point_count,
                'point_set_sha256', NEW.point_set_sha256,
                'parser_version', NEW.parser_version,
                'request_method', NEW.request_method,
                'request_manifest_sha256', NEW.request_manifest_sha256,
                'request_started_at', forecast_provenance_iso_utc(NEW.request_started_at),
                'response_bytes', NEW.response_bytes,
                'response_received_at', forecast_provenance_iso_utc(NEW.response_received_at),
                'response_sha256', NEW.response_sha256,
                'sample_n', NEW.sample_n,
                'source_name', NEW.source_name,
                'source_protocol', NEW.source_protocol,
                'source_station_id', NEW.source_station_id,
                'source_url', NEW.source_url,
                'station_id', NEW.station_id,
                'storage_permitted', NEW.storage_permitted,
                'retention_policy_reviewed_at',
                    forecast_provenance_iso_utc(NEW.retention_policy_reviewed_at),
                'retention_policy_sha256', NEW.retention_policy_sha256,
                'retention_policy_url', NEW.retention_policy_url,
                'retention_policy_version', NEW.retention_policy_version,
                'timezone', NEW.timezone,
                'utc_end', forecast_provenance_iso_utc(NEW.utc_end),
                'utc_start', forecast_provenance_iso_utc(NEW.utc_start),
                'validation_code', NEW.validation_code,
                'validation_details', NEW.validation_details,
                'validation_status', NEW.validation_status
            ));
            IF NEW.record_hash IS DISTINCT FROM expected_record_hash THEN
                RAISE EXCEPTION 'observation recovery artifact record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_observation_recovery_artifact_validate "
        "BEFORE INSERT ON observation_recovery_artifacts FOR EACH ROW "
        "EXECUTE FUNCTION observation_recovery_artifact_validate_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION observation_recovery_point_validate_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent observation_recovery_artifacts%ROWTYPE;
            expected_normalized_bytes bytea;
            expected_hash text;
        BEGIN
            SELECT * INTO STRICT parent
            FROM observation_recovery_artifacts WHERE id = NEW.artifact_id;
            IF NEW.sequence_no >= parent.point_count
               OR NEW.obs_time < parent.utc_start
               OR NEW.obs_time >= parent.utc_end THEN
                RAISE EXCEPTION 'observation recovery point is outside artifact contract';
            END IF;
            expected_normalized_bytes := convert_to(
                forecast_provenance_canonical_json(jsonb_build_object(
                    'obs_time', forecast_provenance_iso_utc(NEW.obs_time),
                    'raw_report', NEW.raw_report,
                    'temp_c', NEW.temp_c::text
                )), 'UTF8'
            );
            IF NEW.normalized_point_bytes IS DISTINCT FROM expected_normalized_bytes
               OR octet_length(NEW.source_row_bytes) = 0
               OR position(NEW.source_row_bytes IN parent.response_body) = 0
               OR position(convert_to(NEW.raw_report, 'UTF8') IN NEW.source_row_bytes) = 0
               OR encode(digest(NEW.source_row_bytes, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.source_row_sha256
               OR encode(digest(NEW.normalized_point_bytes, 'sha256'), 'hex')
                  IS DISTINCT FROM NEW.normalized_point_sha256
               OR btrim(NEW.raw_report) = '' THEN
                RAISE EXCEPTION 'observation recovery normalized point is invalid';
            END IF;
            expected_hash := forecast_provenance_hash(jsonb_build_object(
                'artifact_record_hash', parent.record_hash,
                'obs_time', forecast_provenance_iso_utc(NEW.obs_time),
                'raw_report', NEW.raw_report,
                'sequence_no', NEW.sequence_no,
                'normalized_point_sha256', NEW.normalized_point_sha256,
                'source_row_sha256', NEW.source_row_sha256,
                'temp_c', NEW.temp_c::text
            ));
            IF NEW.record_hash IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'observation recovery point record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_observation_recovery_point_validate "
        "BEFORE INSERT ON observation_recovery_points FOR EACH ROW "
        "EXECUTE FUNCTION observation_recovery_point_validate_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION observation_recovery_verify_point_set()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actual_count integer;
            distinct_time_count integer;
            actual_hash text;
            actual_first timestamptz;
            actual_last timestamptz;
            actual_max_gap_seconds integer;
            projected_count integer;
            projected_kind_count integer;
        BEGIN
            WITH ordered AS (
                SELECT obs_time, lag(obs_time) OVER (ORDER BY obs_time) AS previous_time
                FROM observation_recovery_points
                WHERE artifact_id = NEW.id
            ), aggregate_points AS (
                SELECT count(*) AS actual_count,
                       count(DISTINCT obs_time) AS distinct_time_count,
                       min(obs_time) AS actual_first,
                       max(obs_time) AS actual_last,
                       coalesce(max(extract(epoch FROM obs_time - previous_time)), 0)::integer
                           AS actual_max_gap_seconds
                FROM ordered
            ), aggregate_hash AS (
                SELECT forecast_provenance_hash(
                    coalesce(jsonb_agg(jsonb_build_object(
                        'obs_time', forecast_provenance_iso_utc(obs_time),
                        'raw_report', raw_report,
                        'sequence_no', sequence_no,
                        'normalized_point_sha256', normalized_point_sha256,
                        'source_row_sha256', source_row_sha256,
                        'temp_c', temp_c::text
                    ) ORDER BY sequence_no), '[]'::jsonb)
                ) AS actual_hash
                FROM observation_recovery_points WHERE artifact_id = NEW.id
            )
            SELECT p.actual_count, p.distinct_time_count, h.actual_hash,
                   p.actual_first, p.actual_last, p.actual_max_gap_seconds
            INTO actual_count, distinct_time_count, actual_hash,
                 actual_first, actual_last, actual_max_gap_seconds
            FROM aggregate_points p CROSS JOIN aggregate_hash h;
            IF actual_count IS DISTINCT FROM NEW.point_count
               OR distinct_time_count IS DISTINCT FROM actual_count
               OR NEW.sample_n IS DISTINCT FROM actual_count
               OR actual_hash IS DISTINCT FROM NEW.point_set_sha256 THEN
                RAISE EXCEPTION 'observation recovery point set is incomplete or mismatched';
            END IF;
            IF actual_first IS DISTINCT FROM NEW.first_observation_at
               OR actual_last IS DISTINCT FROM NEW.last_observation_at
               OR actual_max_gap_seconds IS DISTINCT FROM NEW.max_gap_seconds THEN
                RAISE EXCEPTION 'observation recovery persisted coverage disagrees with points';
            END IF;
            SELECT count(*), count(DISTINCT kind)
            INTO projected_count, projected_kind_count
            FROM daily_actuals WHERE recovery_artifact_id = NEW.id;
            IF (NEW.validation_status = 'accepted'
                    AND (projected_count <> 2 OR projected_kind_count <> 2))
               OR (NEW.validation_status = 'quarantined' AND projected_count <> 0) THEN
                RAISE EXCEPTION 'observation recovery actual projection is incomplete or invalid';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_observation_recovery_verify_point_set "
        "AFTER INSERT ON observation_recovery_artifacts DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION observation_recovery_verify_point_set()"
    )
    op.execute(
        r"""
        CREATE FUNCTION daily_actual_recovery_validate()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            parent observation_recovery_artifacts%ROWTYPE;
            expected_temp numeric(6,2);
            expected_hash text;
        BEGIN
            IF NEW.recovery_artifact_id IS NULL THEN
                NEW.first_seen_at := clock_timestamp();
                RETURN NEW;
            END IF;
            SELECT * INTO STRICT parent FROM observation_recovery_artifacts
            WHERE id = NEW.recovery_artifact_id;
            IF parent.validation_status <> 'accepted'
               OR parent.station_id IS DISTINCT FROM NEW.station_id
               OR parent.local_date IS DISTINCT FROM NEW.date
               OR parent.source_protocol IS DISTINCT FROM NEW.source_protocol
               OR parent.first_seen_at IS DISTINCT FROM NEW.first_seen_at
               OR parent.sample_n IS DISTINCT FROM NEW.sample_n
               OR parent.first_observation_at IS DISTINCT FROM NEW.first_observation_at
               OR parent.last_observation_at IS DISTINCT FROM NEW.last_observation_at
               OR parent.max_gap_seconds IS DISTINCT FROM NEW.max_gap_seconds THEN
                RAISE EXCEPTION 'daily actual recovery provenance disagrees with artifact';
            END IF;
            SELECT CASE NEW.kind
                WHEN 'high'::weather_kind THEN max(temp_c)
                ELSE min(temp_c)
            END INTO expected_temp
            FROM observation_recovery_points WHERE artifact_id = parent.id;
            IF NEW.temp_c IS DISTINCT FROM expected_temp THEN
                RAISE EXCEPTION 'daily actual recovery extreme disagrees with point set';
            END IF;
            expected_hash := forecast_provenance_hash(jsonb_build_object(
                'artifact_record_hash', parent.record_hash,
                'date', NEW.date::text,
                'first_observation_at', forecast_provenance_iso_utc(NEW.first_observation_at),
                'first_seen_at', forecast_provenance_iso_utc(NEW.first_seen_at),
                'kind', NEW.kind::text,
                'last_observation_at', forecast_provenance_iso_utc(NEW.last_observation_at),
                'max_gap_seconds', NEW.max_gap_seconds,
                'sample_n', NEW.sample_n,
                'source_protocol', NEW.source_protocol,
                'station_id', NEW.station_id,
                'temp_c', NEW.temp_c::text
            ));
            IF NEW.record_hash IS DISTINCT FROM expected_hash THEN
                RAISE EXCEPTION 'daily actual recovery record hash mismatch';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_daily_actual_recovery_validate "
        "BEFORE INSERT ON daily_actuals FOR EACH ROW "
        "EXECUTE FUNCTION daily_actual_recovery_validate()"
    )
    op.execute(
        r"""
        CREATE FUNCTION historical_forecast_attest_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.first_seen_at := clock_timestamp();
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_historical_forecast_attest_insert "
        "BEFORE INSERT ON historical_forecasts FOR EACH ROW "
        "EXECUTE FUNCTION historical_forecast_attest_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION historical_forecast_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'historical forecast training evidence is append-only';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_historical_forecast_append_only "
        "BEFORE UPDATE OR DELETE ON historical_forecasts FOR EACH ROW "
        "EXECUTE FUNCTION historical_forecast_reject_mutation()"
    )
    op.execute(
        r"""
        CREATE FUNCTION daily_actual_recovery_verify_pair()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE pair_count integer;
        BEGIN
            IF NEW.recovery_artifact_id IS NULL THEN
                RETURN NULL;
            END IF;
            SELECT count(*) INTO pair_count FROM daily_actuals
            WHERE station_id = NEW.station_id AND date = NEW.date
              AND recovery_artifact_id = NEW.recovery_artifact_id;
            IF pair_count <> 2 OR NOT EXISTS (
                SELECT 1 FROM daily_actuals WHERE station_id = NEW.station_id
                AND date = NEW.date AND kind = 'high'::weather_kind
                AND recovery_artifact_id = NEW.recovery_artifact_id
            ) OR NOT EXISTS (
                SELECT 1 FROM daily_actuals WHERE station_id = NEW.station_id
                AND date = NEW.date AND kind = 'low'::weather_kind
                AND recovery_artifact_id = NEW.recovery_artifact_id
            ) THEN
                RAISE EXCEPTION 'recovered daily actuals require an atomic high/low pair';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_daily_actual_recovery_verify_pair "
        "AFTER INSERT ON daily_actuals DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION daily_actual_recovery_verify_pair()"
    )
    op.execute(
        r"""
        CREATE FUNCTION observation_recovery_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only observation recovery evidence', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table_name in (
        "observation_recovery_artifacts",
        "observation_recovery_points",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only BEFORE UPDATE OR DELETE "
            f"ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION observation_recovery_reject_mutation()"
        )
    op.execute(
        r"""
        CREATE FUNCTION daily_actual_recovery_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.first_seen_at IS NOT NULL
               OR OLD.recovery_artifact_id IS NOT NULL THEN
                RAISE EXCEPTION 'prospective daily actual evidence is append-only';
            END IF;
            IF TG_OP = 'UPDATE' AND (
                NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
                OR NEW.recovery_artifact_id IS DISTINCT FROM OLD.recovery_artifact_id
                OR NEW.source_protocol IS DISTINCT FROM OLD.source_protocol
                OR NEW.sample_n IS DISTINCT FROM OLD.sample_n
                OR NEW.first_observation_at IS DISTINCT FROM OLD.first_observation_at
                OR NEW.last_observation_at IS DISTINCT FROM OLD.last_observation_at
                OR NEW.max_gap_seconds IS DISTINCT FROM OLD.max_gap_seconds
                OR NEW.record_hash IS DISTINCT FROM OLD.record_hash
            ) THEN
                RAISE EXCEPTION 'daily actual provenance cannot be retrofitted';
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_daily_actual_recovery_append_only "
        "BEFORE UPDATE OR DELETE ON daily_actuals FOR EACH ROW "
        "EXECUTE FUNCTION daily_actual_recovery_reject_mutation()"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM observation_recovery_artifacts)
               OR EXISTS (SELECT 1 FROM daily_actuals WHERE first_seen_at IS NOT NULL)
               OR EXISTS (
                   SELECT 1 FROM historical_forecasts WHERE first_seen_at IS NOT NULL
               ) THEN
                RAISE EXCEPTION USING
                    MESSAGE = 'refusing 0043 downgrade: observation recovery evidence exists',
                    HINT = 'export and explicitly reset recovery evidence first';
            END IF;
        END
        $$
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_daily_actual_recovery_append_only ON daily_actuals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_historical_forecast_append_only "
        "ON historical_forecasts"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_historical_forecast_attest_insert "
        "ON historical_forecasts"
    )
    op.execute("DROP FUNCTION IF EXISTS historical_forecast_reject_mutation()")
    op.execute("DROP FUNCTION IF EXISTS historical_forecast_attest_insert()")
    op.drop_index(
        "ix_historical_forecasts_first_seen",
        table_name="historical_forecasts",
    )
    op.drop_column("historical_forecasts", "first_seen_at")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_daily_actual_recovery_verify_pair ON daily_actuals"
    )
    op.execute("DROP FUNCTION IF EXISTS daily_actual_recovery_reject_mutation()")
    op.execute("DROP FUNCTION IF EXISTS daily_actual_recovery_verify_pair()")
    op.execute("DROP TRIGGER IF EXISTS trg_daily_actual_recovery_validate ON daily_actuals")
    op.execute("DROP FUNCTION IF EXISTS daily_actual_recovery_validate()")
    op.drop_index("ix_daily_actuals_recovery_artifact", table_name="daily_actuals")
    op.drop_constraint(
        "ck_daily_actuals_recovery_metadata", "daily_actuals", type_="check"
    )
    op.drop_constraint(
        "fk_daily_actuals_recovery_artifact", "daily_actuals", type_="foreignkey"
    )
    for column in (
        "record_hash",
        "max_gap_seconds",
        "last_observation_at",
        "first_observation_at",
        "sample_n",
        "first_seen_at",
        "source_protocol",
        "recovery_artifact_id",
    ):
        op.drop_column("daily_actuals", column)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_observation_recovery_verify_point_set "
        "ON observation_recovery_artifacts"
    )
    for table_name in (
        "observation_recovery_points",
        "observation_recovery_artifacts",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_observation_recovery_point_validate "
        "ON observation_recovery_points"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_observation_recovery_artifact_validate "
        "ON observation_recovery_artifacts"
    )
    op.execute("DROP FUNCTION IF EXISTS observation_recovery_reject_mutation()")
    op.execute("DROP FUNCTION IF EXISTS observation_recovery_verify_point_set()")
    op.execute("DROP FUNCTION IF EXISTS observation_recovery_point_validate_insert()")
    op.execute("DROP FUNCTION IF EXISTS observation_recovery_artifact_validate_insert()")
    op.drop_index(
        "ix_observation_recovery_points_artifact_time",
        table_name="observation_recovery_points",
    )
    op.drop_table("observation_recovery_points")
    op.drop_index(
        "uq_observation_recovery_artifacts_accepted",
        table_name="observation_recovery_artifacts",
    )
    op.drop_index(
        "ix_observation_recovery_artifacts_station_date",
        table_name="observation_recovery_artifacts",
    )
    op.drop_table("observation_recovery_artifacts")
    op.drop_table("observation_recovery_runtime")
