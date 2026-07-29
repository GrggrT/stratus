"""Add immutable official-resolution source and exact-actual evidence.

Revision ID: 20260715_0039
Revises: 20260715_0038
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_0039"
down_revision: str | None = "20260715_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINITION_HASH = "c078c2c72a0547907f7fc7cbe3f09bf656c6c560043c347f925528743c2340e4"
WEATHER_KIND = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "official_resolution_evidence_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_version", sa.String(length=48), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("license_protocol", sa.String(length=48), nullable=False),
        sa.Column("response_protocol", sa.String(length=48), nullable=False),
        sa.Column("extraction_protocol", sa.String(length=48), nullable=False),
        sa.CheckConstraint(
            "id = 1",
            name="ck_official_resolution_evidence_runtime_singleton",
        ),
        sa.CheckConstraint(
            "definition_version = 'official-resolution-evidence-v1' "
            "AND license_protocol = 'official-resolution-license-v1' "
            "AND response_protocol = 'official-resolution-source-v1' "
            "AND extraction_protocol = 'official-resolution-extraction-v1'",
            name="ck_official_resolution_evidence_runtime_protocol",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_evidence_runtime_hash",
        ),
    )
    # The database clock opens a new prospective cohort. Existing contracts,
    # settlement_actuals and reconciliation rows remain untouched and null.
    op.execute(
        "INSERT INTO official_resolution_evidence_runtime ("
        "id, opened_at, definition_version, definition_hash, license_protocol, "
        "response_protocol, extraction_protocol) VALUES ("
        f"1, clock_timestamp(), 'official-resolution-evidence-v1', '{DEFINITION_HASH}', "
        "'official-resolution-license-v1', 'official-resolution-source-v1', "
        "'official-resolution-extraction-v1')"
    )

    op.create_table(
        "official_resolution_license_attestations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "contract_id",
            sa.BigInteger(),
            sa.ForeignKey("resolution_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attestation_version", sa.String(length=64), nullable=False),
        sa.Column("attestation_protocol", sa.String(length=48), nullable=False),
        sa.Column("grantee", sa.Text(), nullable=False),
        sa.Column("source_family", sa.String(length=32), nullable=False),
        sa.Column("settlement_source_url", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("license_status", sa.String(length=32), nullable=False),
        sa.Column("commercial_use_permitted", sa.Boolean(), nullable=False),
        sa.Column("value_retention_permitted", sa.Boolean(), nullable=False),
        sa.Column("raw_response_retention_permitted", sa.Boolean(), nullable=False),
        sa.Column("license_url", sa.Text(), nullable=False),
        sa.Column("terms_sha256", sa.String(length=64), nullable=False),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_name", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("parser_config", postgresql.JSONB(), nullable=False),
        sa.Column("parser_config_hash", sa.String(length=64), nullable=False),
        sa.Column("max_response_age_seconds", sa.Integer(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "attested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "contract_id",
            "attestation_version",
            name="uq_official_resolution_license_contract_version",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_license_record_hash",
        ),
        sa.CheckConstraint(
            "attestation_protocol = 'official-resolution-license-v1'",
            name="ck_official_resolution_license_protocol",
        ),
        sa.CheckConstraint(
            "license_status = 'approved' AND commercial_use_permitted "
            "AND value_retention_permitted AND raw_response_retention_permitted",
            name="ck_official_resolution_license_authority",
        ),
        sa.CheckConstraint(
            "settlement_source_url ~ '^https://' AND endpoint_url ~ '^https://' "
            "AND license_url ~ '^https://'",
            name="ck_official_resolution_license_urls",
        ),
        sa.CheckConstraint(
            "expires_at > valid_from",
            name="ck_official_resolution_license_validity",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parser_config) = 'object'",
            name="ck_official_resolution_license_parser_config",
        ),
        sa.CheckConstraint(
            "max_response_age_seconds BETWEEN 1 AND 86400",
            name="ck_official_resolution_license_response_age",
        ),
        sa.CheckConstraint(
            "terms_sha256 ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND policy_hash ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_license_hashes",
        ),
        sa.CheckConstraint(
            "length(btrim(attestation_version)) > 0 "
            "AND length(btrim(grantee)) > 0 "
            "AND length(btrim(source_family)) > 0 "
            "AND length(btrim(reviewer)) > 0 "
            "AND length(btrim(adapter_name)) > 0 "
            "AND length(btrim(adapter_version)) > 0 "
            "AND length(btrim(rationale)) > 0",
            name="ck_official_resolution_license_required_text",
        ),
    )
    op.create_index(
        "ix_official_resolution_license_contract_time",
        "official_resolution_license_attestations",
        ["contract_id", "attested_at"],
    )

    op.create_table(
        "official_resolution_source_responses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "license_attestation_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "official_resolution_license_attestations.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.BigInteger(),
            sa.ForeignKey("resolution_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("response_protocol", sa.String(length=48), nullable=False),
        sa.Column("acquisition_protocol", sa.String(length=48), nullable=False),
        sa.Column("source_family", sa.String(length=32), nullable=False),
        sa.Column("settlement_source_url", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("adapter_name", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("parser_config_hash", sa.String(length=64), nullable=False),
        sa.Column("license_policy_hash", sa.String(length=64), nullable=False),
        sa.Column("request_method", sa.String(length=8), nullable=False),
        sa.Column("request_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.SmallInteger(), nullable=True),
        sa.Column("http_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modified", sa.DateTime(timezone=True), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("response_headers", postgresql.JSONB(), nullable=False),
        sa.Column("response_body", sa.LargeBinary(), nullable=False),
        sa.Column("response_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "contract_id",
            "request_sha256",
            name="uq_official_resolution_response_contract_request",
        ),
        sa.UniqueConstraint(
            "contract_id",
            "response_sha256",
            name="uq_official_resolution_response_contract_body",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_response_record_hash",
        ),
        sa.CheckConstraint(
            "response_protocol = 'official-resolution-source-v1'",
            name="ck_official_resolution_response_protocol",
        ),
        sa.CheckConstraint(
            "(acquisition_protocol = 'licensed-http-v1' "
            "AND request_method = 'GET' AND http_status = 200) OR "
            "(acquisition_protocol = 'authorized-artifact-v1' "
            "AND request_method = 'IMPORT' AND http_status IS NULL)",
            name="ck_official_resolution_response_acquisition",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_manifest) = 'object' "
            "AND jsonb_typeof(response_headers) = 'object'",
            name="ck_official_resolution_response_metadata",
        ),
        sa.CheckConstraint(
            "response_bytes > 0",
            name="ck_official_resolution_response_bytes",
        ),
        sa.CheckConstraint(
            "request_sha256 ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND license_policy_hash ~ '^[0-9a-f]{64}$' "
            "AND response_sha256 ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_response_hashes",
        ),
    )
    op.create_index(
        "ix_official_resolution_response_contract_seen",
        "official_resolution_source_responses",
        ["contract_id", "first_seen_at"],
    )
    op.create_index(
        "ix_official_resolution_response_license",
        "official_resolution_source_responses",
        ["license_attestation_id"],
    )

    op.create_table(
        "official_resolution_exact_actuals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "response_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "official_resolution_source_responses.id",
                ondelete="RESTRICT",
            ),
            nullable=False,
        ),
        sa.Column(
            "contract_id",
            sa.BigInteger(),
            sa.ForeignKey("resolution_contracts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "station_id",
            sa.BigInteger(),
            sa.ForeignKey("stations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("rules_hash", sa.String(length=64), nullable=False),
        sa.Column("source_family", sa.String(length=32), nullable=False),
        sa.Column("settlement_source_url", sa.Text(), nullable=False),
        sa.Column("endpoint_url", sa.Text(), nullable=False),
        sa.Column("station_code", sa.String(length=16), nullable=False),
        sa.Column("market_date", sa.Date(), nullable=False),
        sa.Column("kind", WEATHER_KIND, nullable=False),
        sa.Column("source_value", sa.Numeric(8, 3), nullable=False),
        sa.Column("source_unit", sa.String(length=4), nullable=False),
        sa.Column("value_c", sa.Numeric(8, 4), nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("finalization_rule", sa.String(length=64), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("adapter_name", sa.String(length=64), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("parser_config_hash", sa.String(length=64), nullable=False),
        sa.Column("extraction_locator", sa.Text(), nullable=False),
        sa.Column("extraction_manifest", postgresql.JSONB(), nullable=False),
        sa.Column("extraction_manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("source_response_sha256", sa.String(length=64), nullable=False),
        sa.Column("extraction_protocol", sa.String(length=48), nullable=False),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "contract_id",
            name="uq_official_resolution_exact_actual_contract",
        ),
        sa.UniqueConstraint(
            "response_id",
            name="uq_official_resolution_exact_actual_response",
        ),
        sa.UniqueConstraint(
            "record_hash",
            name="uq_official_resolution_exact_actual_record_hash",
        ),
        sa.CheckConstraint(
            "extraction_protocol = 'official-resolution-extraction-v1'",
            name="ck_official_resolution_exact_actual_protocol",
        ),
        sa.CheckConstraint(
            "source_unit IN ('C', 'F') AND decimal_places BETWEEN 0 AND 3 "
            "AND is_final",
            name="ck_official_resolution_exact_actual_value",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(extraction_manifest) = 'object' "
            "AND length(btrim(extraction_locator)) > 0",
            name="ck_official_resolution_exact_actual_extraction",
        ),
        sa.CheckConstraint(
            "rules_hash ~ '^[0-9a-f]{64}$' "
            "AND parser_config_hash ~ '^[0-9a-f]{64}$' "
            "AND extraction_manifest_hash ~ '^[0-9a-f]{64}$' "
            "AND source_response_sha256 ~ '^[0-9a-f]{64}$' "
            "AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_official_resolution_exact_actual_hashes",
        ),
    )
    op.create_index(
        "ix_official_resolution_exact_station_date",
        "official_resolution_exact_actuals",
        ["station_id", "market_date", "kind"],
    )

    op.add_column(
        "settlement_actuals",
        sa.Column("official_exact_actual_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_settlement_actuals_official_exact_actual",
        "settlement_actuals",
        "official_resolution_exact_actuals",
        ["official_exact_actual_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_settlement_actual_official_exact",
        "settlement_actuals",
        ["official_exact_actual_id"],
    )

    op.execute(
        r"""
        CREATE FUNCTION official_resolution_canonical_json(value jsonb)
        RETURNS text LANGUAGE plpgsql IMMUTABLE STRICT AS $$
        DECLARE result text;
        BEGIN
            CASE jsonb_typeof(value)
                WHEN 'object' THEN
                    SELECT '{' || coalesce(
                        string_agg(
                            to_jsonb(item.key)::text || ':' ||
                            official_resolution_canonical_json(item.value),
                            ',' ORDER BY item.key
                        ), '') || '}'
                    INTO result FROM jsonb_each(value) AS item;
                    RETURN result;
                WHEN 'array' THEN
                    SELECT '[' || coalesce(
                        string_agg(
                            official_resolution_canonical_json(item.value),
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
        CREATE FUNCTION official_resolution_hash(value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT encode(
                digest(
                    convert_to(official_resolution_canonical_json(value), 'UTF8'),
                    'sha256'
                ), 'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_utc_iso(value timestamptz)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT to_char(
                       value AT TIME ZONE 'UTC',
                       CASE
                           WHEN value = date_trunc('second', value)
                               THEN 'YYYY-MM-DD"T"HH24:MI:SS'
                           ELSE 'YYYY-MM-DD"T"HH24:MI:SS.US'
                       END
                   ) || '+00:00'
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_validate_runtime_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'official resolution evidence runtime is immutable';
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_validate_license_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            contract_row resolution_contracts%ROWTYPE;
            expected_policy_hash text;
        BEGIN
            SELECT * INTO contract_row
            FROM resolution_contracts
            WHERE id = NEW.contract_id;
            IF NOT FOUND OR contract_row.retired_at IS NOT NULL THEN
                RAISE EXCEPTION 'official resolution licence requires an active contract';
            END IF;

            NEW.attested_at := clock_timestamp();
            IF NEW.attestation_protocol IS DISTINCT FROM 'official-resolution-license-v1'
               OR NEW.source_family IS DISTINCT FROM contract_row.source_family
               OR NEW.settlement_source_url IS DISTINCT FROM contract_row.source_url THEN
                RAISE EXCEPTION 'official resolution licence contract binding mismatch';
            END IF;
            IF NEW.license_status IS DISTINCT FROM 'approved'
               OR NEW.commercial_use_permitted IS DISTINCT FROM true
               OR NEW.value_retention_permitted IS DISTINCT FROM true
               OR NEW.raw_response_retention_permitted IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'official resolution licence authority is insufficient';
            END IF;
            IF NEW.reviewed_at > NEW.attested_at
               OR NEW.valid_from >= NEW.expires_at
               OR NEW.expires_at <= NEW.attested_at THEN
                RAISE EXCEPTION 'official resolution licence validity is invalid';
            END IF;
            IF jsonb_typeof(NEW.parser_config) IS DISTINCT FROM 'object'
               OR NEW.parser_config_hash IS DISTINCT FROM
                    official_resolution_hash(NEW.parser_config) THEN
                RAISE EXCEPTION 'official resolution parser config hash mismatch';
            END IF;
            IF NEW.parser_config ->> 'station_code_literal'
                    IS DISTINCT FROM contract_row.station_code
               OR NEW.parser_config ->> 'source_unit_literal'
                    IS DISTINCT FROM contract_row.source_unit
               OR NEW.parser_config ->> 'decimal_places_literal'
                    IS DISTINCT FROM contract_row.decimal_places::text
               OR NEW.parser_config ->> 'finalization_rule_literal'
                    IS DISTINCT FROM contract_row.finalization_rule THEN
                RAISE EXCEPTION
                    'official resolution parser config contract binding mismatch';
            END IF;
            expected_policy_hash := official_resolution_hash(jsonb_build_object(
                'protocol', 'official-resolution-license-v1',
                'license_id', NEW.attestation_version,
                'grantee', NEW.grantee,
                'source_family', NEW.source_family,
                'endpoint_url', NEW.endpoint_url,
                'adapter_name', NEW.adapter_name,
                'adapter_version', NEW.adapter_version,
                'parser_config', NEW.parser_config,
                'parser_config_sha256', NEW.parser_config_hash,
                'contract_bindings', jsonb_build_array(jsonb_build_object(
                    'event_id', contract_row.event_id,
                    'rules_hash', contract_row.rules_hash,
                    'source_url', contract_row.source_url,
                    'station_code', contract_row.station_code
                )),
                'terms_url', NEW.license_url,
                'terms_sha256', NEW.terms_sha256,
                'valid_from', official_resolution_utc_iso(NEW.valid_from),
                'expires_at', official_resolution_utc_iso(NEW.expires_at),
                'storage_permitted', true,
                'commercial_use_permitted', NEW.commercial_use_permitted,
                'max_response_age_seconds', NEW.max_response_age_seconds
            ));
            IF NEW.policy_hash IS DISTINCT FROM expected_policy_hash THEN
                RAISE EXCEPTION 'official resolution licence policy hash mismatch';
            END IF;
            NEW.record_hash := official_resolution_hash(jsonb_build_object(
                'record_kind', 'license_attestation',
                'adapter_name', NEW.adapter_name,
                'adapter_version', NEW.adapter_version,
                'attestation_protocol', NEW.attestation_protocol,
                'attestation_version', NEW.attestation_version,
                'attested_at', NEW.attested_at,
                'commercial_use_permitted', NEW.commercial_use_permitted,
                'contract_id', NEW.contract_id,
                'endpoint_url', NEW.endpoint_url,
                'expires_at', NEW.expires_at,
                'grantee', NEW.grantee,
                'license_status', NEW.license_status,
                'license_url', NEW.license_url,
                'parser_config', NEW.parser_config,
                'parser_config_hash', NEW.parser_config_hash,
                'max_response_age_seconds', NEW.max_response_age_seconds,
                'policy_hash', NEW.policy_hash,
                'rationale', NEW.rationale,
                'raw_response_retention_permitted',
                    NEW.raw_response_retention_permitted,
                'reviewed_at', NEW.reviewed_at,
                'reviewer', NEW.reviewer,
                'settlement_source_url', NEW.settlement_source_url,
                'source_family', NEW.source_family,
                'terms_sha256', NEW.terms_sha256,
                'valid_from', NEW.valid_from,
                'value_retention_permitted', NEW.value_retention_permitted
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_validate_response_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_opened_at timestamptz;
            contract_row resolution_contracts%ROWTYPE;
            licence_row official_resolution_license_attestations%ROWTYPE;
            existing_row official_resolution_source_responses%ROWTYPE;
        BEGIN
            SELECT opened_at INTO runtime_opened_at
            FROM official_resolution_evidence_runtime WHERE id = 1;
            SELECT * INTO contract_row
            FROM resolution_contracts WHERE id = NEW.contract_id;
            SELECT * INTO licence_row
            FROM official_resolution_license_attestations
            WHERE id = NEW.license_attestation_id;
            IF runtime_opened_at IS NULL OR contract_row.id IS NULL
               OR licence_row.id IS NULL THEN
                RAISE EXCEPTION 'official resolution response authority is unavailable';
            END IF;

            NEW.first_seen_at := clock_timestamp();
            IF NEW.first_seen_at < runtime_opened_at THEN
                RAISE EXCEPTION 'official resolution response predates cohort boundary';
            END IF;
            IF licence_row.contract_id IS DISTINCT FROM NEW.contract_id
               OR licence_row.source_family IS DISTINCT FROM NEW.source_family
               OR licence_row.settlement_source_url
                    IS DISTINCT FROM NEW.settlement_source_url
               OR licence_row.endpoint_url IS DISTINCT FROM NEW.endpoint_url
               OR licence_row.adapter_name IS DISTINCT FROM NEW.adapter_name
               OR licence_row.adapter_version IS DISTINCT FROM NEW.adapter_version
               OR licence_row.parser_config_hash
                    IS DISTINCT FROM NEW.parser_config_hash
               OR licence_row.policy_hash IS DISTINCT FROM NEW.license_policy_hash
               OR contract_row.source_family IS DISTINCT FROM NEW.source_family
               OR contract_row.source_url IS DISTINCT FROM NEW.settlement_source_url THEN
                RAISE EXCEPTION 'official resolution response binding mismatch';
            END IF;
            IF licence_row.license_status IS DISTINCT FROM 'approved'
               OR licence_row.commercial_use_permitted IS DISTINCT FROM true
               OR licence_row.value_retention_permitted IS DISTINCT FROM true
               OR licence_row.raw_response_retention_permitted IS DISTINCT FROM true
               OR licence_row.reviewed_at > NEW.request_started_at
               OR licence_row.attested_at > NEW.request_started_at
               OR licence_row.valid_from > NEW.request_started_at
               OR licence_row.expires_at <= NEW.retrieved_at
               OR NEW.retrieved_at - NEW.response_received_at >
                    make_interval(secs => licence_row.max_response_age_seconds) THEN
                RAISE EXCEPTION 'official resolution response licence is not valid at retrieval';
            END IF;
            IF NEW.response_protocol IS DISTINCT FROM 'official-resolution-source-v1'
               OR NOT (
                    (NEW.acquisition_protocol = 'licensed-http-v1'
                     AND NEW.request_method = 'GET'
                     AND NEW.http_status = 200)
                    OR
                    (NEW.acquisition_protocol = 'authorized-artifact-v1'
                     AND NEW.request_method = 'IMPORT'
                     AND NEW.http_status IS NULL)
               ) THEN
                RAISE EXCEPTION 'official resolution response protocol mismatch';
            END IF;
            IF NEW.source_published_at > NEW.response_received_at
               OR NEW.request_started_at > NEW.response_received_at
               OR NEW.response_received_at > NEW.retrieved_at
               OR NEW.retrieved_at > NEW.first_seen_at THEN
                RAISE EXCEPTION 'official resolution response timestamp order is invalid';
            END IF;
            IF jsonb_typeof(NEW.request_manifest) IS DISTINCT FROM 'object'
               OR NEW.request_sha256 IS DISTINCT FROM
                    official_resolution_hash(NEW.request_manifest) THEN
                RAISE EXCEPTION 'official resolution request manifest hash mismatch';
            END IF;
            IF NEW.response_body IS NULL OR NEW.response_bytes <= 0
               OR octet_length(NEW.response_body) IS DISTINCT FROM NEW.response_bytes
               OR encode(digest(NEW.response_body, 'sha256'), 'hex')
                    IS DISTINCT FROM NEW.response_sha256 THEN
                RAISE EXCEPTION 'official resolution response body hash mismatch';
            END IF;
            IF NEW.acquisition_protocol = 'authorized-artifact-v1'
               AND (
                    jsonb_typeof(NEW.request_manifest -> 'relative_path')
                        IS DISTINCT FROM 'string'
                    OR length(btrim(NEW.request_manifest ->> 'relative_path')) = 0
                    OR NEW.request_manifest ->> 'relative_path'
                        IS DISTINCT FROM btrim(
                            NEW.request_manifest ->> 'relative_path'
                        )
                    OR left(NEW.request_manifest ->> 'relative_path', 1) = '/'
                    OR strpos(NEW.request_manifest ->> 'relative_path', ':') > 0
                    OR NEW.request_manifest ->> 'relative_path' ~ '(^|/)\.\.?(/|$)'
                    OR NEW.request_manifest ->> 'relative_path' ~ '//'
                    OR strpos(NEW.request_manifest ->> 'relative_path', chr(92)) > 0
               ) THEN
                RAISE EXCEPTION
                    'official resolution artifact path is not normalized relative';
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended(
                NEW.contract_id::text || ':' || NEW.request_sha256,
                39020260715::bigint
            ));
            SELECT * INTO existing_row
            FROM official_resolution_source_responses
            WHERE contract_id = NEW.contract_id
              AND (request_sha256 = NEW.request_sha256
                   OR response_sha256 = NEW.response_sha256)
            LIMIT 1;
            IF FOUND THEN
                IF existing_row.license_attestation_id
                        IS NOT DISTINCT FROM NEW.license_attestation_id
                   AND existing_row.response_protocol
                        IS NOT DISTINCT FROM NEW.response_protocol
                   AND existing_row.acquisition_protocol
                        IS NOT DISTINCT FROM NEW.acquisition_protocol
                   AND existing_row.source_family IS NOT DISTINCT FROM NEW.source_family
                   AND existing_row.settlement_source_url
                        IS NOT DISTINCT FROM NEW.settlement_source_url
                   AND existing_row.endpoint_url IS NOT DISTINCT FROM NEW.endpoint_url
                   AND existing_row.adapter_name IS NOT DISTINCT FROM NEW.adapter_name
                   AND existing_row.adapter_version IS NOT DISTINCT FROM NEW.adapter_version
                   AND existing_row.parser_config_hash
                        IS NOT DISTINCT FROM NEW.parser_config_hash
                   AND existing_row.license_policy_hash
                        IS NOT DISTINCT FROM NEW.license_policy_hash
                   AND existing_row.request_method IS NOT DISTINCT FROM NEW.request_method
                   AND existing_row.request_manifest IS NOT DISTINCT FROM NEW.request_manifest
                   AND existing_row.request_sha256 IS NOT DISTINCT FROM NEW.request_sha256
                   AND (
                        NEW.acquisition_protocol = 'authorized-artifact-v1'
                        OR (
                            existing_row.request_started_at
                                IS NOT DISTINCT FROM NEW.request_started_at
                            AND existing_row.response_received_at
                                IS NOT DISTINCT FROM NEW.response_received_at
                            AND existing_row.retrieved_at
                                IS NOT DISTINCT FROM NEW.retrieved_at
                        )
                   )
                   AND existing_row.source_published_at
                        IS NOT DISTINCT FROM NEW.source_published_at
                   AND existing_row.http_status IS NOT DISTINCT FROM NEW.http_status
                   AND existing_row.http_date IS NOT DISTINCT FROM NEW.http_date
                   AND existing_row.last_modified IS NOT DISTINCT FROM NEW.last_modified
                   AND existing_row.etag IS NOT DISTINCT FROM NEW.etag
                   AND existing_row.content_type IS NOT DISTINCT FROM NEW.content_type
                   AND existing_row.response_headers
                        IS NOT DISTINCT FROM NEW.response_headers
                   AND existing_row.response_body IS NOT DISTINCT FROM NEW.response_body
                   AND existing_row.response_sha256
                        IS NOT DISTINCT FROM NEW.response_sha256
                   AND existing_row.response_bytes IS NOT DISTINCT FROM NEW.response_bytes THEN
                    RETURN NULL;
                END IF;
                RAISE EXCEPTION 'official resolution response identity conflict';
            END IF;

            NEW.record_hash := official_resolution_hash(jsonb_build_object(
                'record_kind', 'source_response',
                'adapter_name', NEW.adapter_name,
                'adapter_version', NEW.adapter_version,
                'acquisition_protocol', NEW.acquisition_protocol,
                'content_type', NEW.content_type,
                'contract_id', NEW.contract_id,
                'endpoint_url', NEW.endpoint_url,
                'etag', NEW.etag,
                'first_seen_at', NEW.first_seen_at,
                'http_date', NEW.http_date,
                'http_status', NEW.http_status,
                'last_modified', NEW.last_modified,
                'license_attestation_id', NEW.license_attestation_id,
                'license_policy_hash', NEW.license_policy_hash,
                'parser_config_hash', NEW.parser_config_hash,
                'request_manifest', NEW.request_manifest,
                'request_method', NEW.request_method,
                'request_sha256', NEW.request_sha256,
                'request_started_at', NEW.request_started_at,
                'response_bytes', NEW.response_bytes,
                'response_headers', NEW.response_headers,
                'response_protocol', NEW.response_protocol,
                'response_received_at', NEW.response_received_at,
                'response_sha256', NEW.response_sha256,
                'retrieved_at', NEW.retrieved_at,
                'settlement_source_url', NEW.settlement_source_url,
                'source_family', NEW.source_family,
                'source_published_at', NEW.source_published_at
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_validate_exact_actual_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_opened_at timestamptz;
            contract_row resolution_contracts%ROWTYPE;
            response_row official_resolution_source_responses%ROWTYPE;
            station_icao text;
            expected_value_c numeric;
            existing_row official_resolution_exact_actuals%ROWTYPE;
        BEGIN
            SELECT opened_at INTO runtime_opened_at
            FROM official_resolution_evidence_runtime WHERE id = 1;
            SELECT * INTO contract_row
            FROM resolution_contracts WHERE id = NEW.contract_id;
            SELECT * INTO response_row
            FROM official_resolution_source_responses WHERE id = NEW.response_id;
            SELECT icao INTO station_icao FROM stations WHERE id = NEW.station_id;
            IF runtime_opened_at IS NULL OR contract_row.id IS NULL
               OR response_row.id IS NULL OR station_icao IS NULL THEN
                RAISE EXCEPTION 'official exact actual evidence chain is unavailable';
            END IF;

            NEW.extracted_at := clock_timestamp();
            IF response_row.first_seen_at < runtime_opened_at
               OR NEW.extracted_at < response_row.first_seen_at THEN
                RAISE EXCEPTION 'official exact actual predates its evidence boundary';
            END IF;
            IF response_row.contract_id IS DISTINCT FROM NEW.contract_id
               OR response_row.source_family IS DISTINCT FROM NEW.source_family
               OR response_row.settlement_source_url
                    IS DISTINCT FROM NEW.settlement_source_url
               OR response_row.endpoint_url IS DISTINCT FROM NEW.endpoint_url
               OR response_row.adapter_name IS DISTINCT FROM NEW.adapter_name
               OR response_row.adapter_version IS DISTINCT FROM NEW.adapter_version
               OR response_row.parser_config_hash
                    IS DISTINCT FROM NEW.parser_config_hash
               OR response_row.response_sha256
                    IS DISTINCT FROM NEW.source_response_sha256
               OR response_row.source_published_at
                    IS DISTINCT FROM NEW.source_published_at THEN
                RAISE EXCEPTION 'official exact actual response binding mismatch';
            END IF;
            IF contract_row.event_id IS DISTINCT FROM NEW.event_id
               OR contract_row.rules_hash IS DISTINCT FROM NEW.rules_hash
               OR contract_row.source_family IS DISTINCT FROM NEW.source_family
               OR contract_row.source_url IS DISTINCT FROM NEW.settlement_source_url
               OR contract_row.station_code IS DISTINCT FROM NEW.station_code
               OR contract_row.statistic IS DISTINCT FROM NEW.kind::text
               OR contract_row.source_unit IS DISTINCT FROM NEW.source_unit
               OR contract_row.decimal_places IS DISTINCT FROM NEW.decimal_places
               OR contract_row.finalization_rule
                    IS DISTINCT FROM NEW.finalization_rule
               OR station_icao IS DISTINCT FROM NEW.station_code THEN
                RAISE EXCEPTION 'official exact actual contract binding mismatch';
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM markets
                WHERE event_id = NEW.event_id
                  AND station_id = NEW.station_id
                  AND market_date = NEW.market_date
                  AND kind::text = NEW.kind::text
            ) OR EXISTS (
                SELECT 1 FROM markets
                WHERE event_id = NEW.event_id
                  AND (station_id IS DISTINCT FROM NEW.station_id
                       OR market_date IS DISTINCT FROM NEW.market_date
                       OR kind::text IS DISTINCT FROM NEW.kind::text)
            ) THEN
                RAISE EXCEPTION 'official exact actual market scope mismatch';
            END IF;
            IF NEW.source_value IS DISTINCT FROM
                    round(NEW.source_value, NEW.decimal_places) THEN
                RAISE EXCEPTION 'official exact actual exceeds contract precision';
            END IF;
            expected_value_c := CASE NEW.source_unit
                WHEN 'C' THEN round(NEW.source_value, 4)
                WHEN 'F' THEN round((NEW.source_value - 32) * 5 / 9, 4)
                ELSE NULL
            END;
            IF expected_value_c IS NULL
               OR NEW.value_c IS DISTINCT FROM expected_value_c
               OR NEW.is_final IS DISTINCT FROM true THEN
                RAISE EXCEPTION 'official exact actual value conversion mismatch';
            END IF;
            IF NEW.extraction_protocol
                    IS DISTINCT FROM 'official-resolution-extraction-v1'
               OR jsonb_typeof(NEW.extraction_manifest) IS DISTINCT FROM 'object'
               OR NEW.extraction_manifest_hash IS DISTINCT FROM
                    official_resolution_hash(NEW.extraction_manifest) THEN
                RAISE EXCEPTION 'official exact actual extraction manifest mismatch';
            END IF;

            PERFORM pg_advisory_xact_lock(hashtextextended(
                NEW.contract_id::text,
                39120260715::bigint
            ));
            SELECT * INTO existing_row
            FROM official_resolution_exact_actuals
            WHERE contract_id = NEW.contract_id;
            IF FOUND THEN
                IF existing_row.response_id IS NOT DISTINCT FROM NEW.response_id
                   AND existing_row.station_id IS NOT DISTINCT FROM NEW.station_id
                   AND existing_row.event_id IS NOT DISTINCT FROM NEW.event_id
                   AND existing_row.rules_hash IS NOT DISTINCT FROM NEW.rules_hash
                   AND existing_row.source_family IS NOT DISTINCT FROM NEW.source_family
                   AND existing_row.settlement_source_url
                        IS NOT DISTINCT FROM NEW.settlement_source_url
                   AND existing_row.endpoint_url IS NOT DISTINCT FROM NEW.endpoint_url
                   AND existing_row.station_code IS NOT DISTINCT FROM NEW.station_code
                   AND existing_row.market_date IS NOT DISTINCT FROM NEW.market_date
                   AND existing_row.kind IS NOT DISTINCT FROM NEW.kind
                   AND existing_row.source_value IS NOT DISTINCT FROM NEW.source_value
                   AND existing_row.source_unit IS NOT DISTINCT FROM NEW.source_unit
                   AND existing_row.value_c IS NOT DISTINCT FROM NEW.value_c
                   AND existing_row.decimal_places
                        IS NOT DISTINCT FROM NEW.decimal_places
                   AND existing_row.finalization_rule
                        IS NOT DISTINCT FROM NEW.finalization_rule
                   AND existing_row.is_final IS NOT DISTINCT FROM NEW.is_final
                   AND existing_row.source_published_at
                        IS NOT DISTINCT FROM NEW.source_published_at
                   AND existing_row.adapter_name IS NOT DISTINCT FROM NEW.adapter_name
                   AND existing_row.adapter_version IS NOT DISTINCT FROM NEW.adapter_version
                   AND existing_row.parser_config_hash
                        IS NOT DISTINCT FROM NEW.parser_config_hash
                   AND existing_row.extraction_locator
                        IS NOT DISTINCT FROM NEW.extraction_locator
                   AND existing_row.extraction_manifest
                        IS NOT DISTINCT FROM NEW.extraction_manifest
                   AND existing_row.extraction_manifest_hash
                        IS NOT DISTINCT FROM NEW.extraction_manifest_hash
                   AND existing_row.source_response_sha256
                        IS NOT DISTINCT FROM NEW.source_response_sha256
                   AND existing_row.extraction_protocol
                        IS NOT DISTINCT FROM NEW.extraction_protocol THEN
                    RETURN NULL;
                END IF;
                RAISE EXCEPTION 'official exact actual conflicts with sealed contract truth';
            END IF;

            NEW.record_hash := official_resolution_hash(jsonb_build_object(
                'record_kind', 'exact_actual',
                'adapter_name', NEW.adapter_name,
                'adapter_version', NEW.adapter_version,
                'contract_id', NEW.contract_id,
                'decimal_places', NEW.decimal_places,
                'endpoint_url', NEW.endpoint_url,
                'event_id', NEW.event_id,
                'extracted_at', NEW.extracted_at,
                'extraction_locator', NEW.extraction_locator,
                'extraction_manifest', NEW.extraction_manifest,
                'extraction_manifest_hash', NEW.extraction_manifest_hash,
                'extraction_protocol', NEW.extraction_protocol,
                'finalization_rule', NEW.finalization_rule,
                'is_final', NEW.is_final,
                'kind', NEW.kind::text,
                'market_date', NEW.market_date,
                'parser_config_hash', NEW.parser_config_hash,
                'response_id', NEW.response_id,
                'rules_hash', NEW.rules_hash,
                'settlement_source_url', NEW.settlement_source_url,
                'source_family', NEW.source_family,
                'source_published_at', NEW.source_published_at,
                'source_response_sha256', NEW.source_response_sha256,
                'source_unit', NEW.source_unit,
                'source_value', NEW.source_value,
                'station_code', NEW.station_code,
                'station_id', NEW.station_id,
                'value_c', NEW.value_c
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION official_resolution_validate_settlement_actual_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            exact_row official_resolution_exact_actuals%ROWTYPE;
            response_hash text;
            response_retrieved_at timestamptz;
            response_record_hash text;
            response_license_id bigint;
            expected_provenance jsonb;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'settlement actual projection is immutable after 0039';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'settlement actual projection is immutable after 0039';
            END IF;
            IF NEW.official_exact_actual_id IS NULL THEN
                RAISE EXCEPTION
                    'post-0039 settlement actual requires official exact evidence';
            END IF;
            SELECT * INTO exact_row
            FROM official_resolution_exact_actuals
            WHERE id = NEW.official_exact_actual_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'settlement actual projection does not match official exact evidence';
            END IF;
            SELECT response_sha256, retrieved_at, record_hash,
                   license_attestation_id
            INTO response_hash, response_retrieved_at, response_record_hash,
                 response_license_id
            FROM official_resolution_source_responses
            WHERE id = exact_row.response_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'settlement actual projection does not match official exact evidence';
            END IF;
            expected_provenance := jsonb_build_object(
                'evidence_protocol', 'official-resolution-evidence-v1',
                'official_exact_actual_id', exact_row.id,
                'official_exact_record_hash', exact_row.record_hash,
                'official_source_response_id', exact_row.response_id,
                'official_source_response_record_hash', response_record_hash,
                'official_license_attestation_id', response_license_id
            );
            IF exact_row.contract_id IS DISTINCT FROM NEW.contract_id
               OR exact_row.station_id IS DISTINCT FROM NEW.station_id
               OR exact_row.market_date IS DISTINCT FROM NEW.date
               OR exact_row.kind IS DISTINCT FROM NEW.kind
               OR exact_row.source_value IS DISTINCT FROM NEW.source_value
               OR exact_row.source_unit IS DISTINCT FROM NEW.source_unit
               OR exact_row.value_c IS DISTINCT FROM NEW.value_c
               OR exact_row.is_final IS DISTINCT FROM NEW.is_final
               OR exact_row.source_published_at
                    IS DISTINCT FROM NEW.source_published_at
               OR response_hash IS DISTINCT FROM NEW.source_response_hash
               OR NEW.retrieved_at IS DISTINCT FROM response_retrieved_at
               OR NEW.provenance IS DISTINCT FROM expected_provenance THEN
                RAISE EXCEPTION
                    'settlement actual projection does not match official exact evidence';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )

    op.execute(
        "CREATE TRIGGER trg_official_resolution_runtime_immutable "
        "BEFORE UPDATE OR DELETE ON official_resolution_evidence_runtime "
        "FOR EACH ROW EXECUTE FUNCTION official_resolution_validate_runtime_mutation()"
    )
    op.execute(
        "CREATE TRIGGER trg_official_resolution_license_validate_insert "
        "BEFORE INSERT ON official_resolution_license_attestations "
        "FOR EACH ROW EXECUTE FUNCTION official_resolution_validate_license_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_official_resolution_response_validate_insert "
        "BEFORE INSERT ON official_resolution_source_responses "
        "FOR EACH ROW EXECUTE FUNCTION official_resolution_validate_response_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_official_resolution_exact_validate_insert "
        "BEFORE INSERT ON official_resolution_exact_actuals "
        "FOR EACH ROW EXECUTE FUNCTION official_resolution_validate_exact_actual_insert()"
    )
    op.execute(
        "CREATE TRIGGER trg_settlement_actuals_require_official_exact "
        "BEFORE INSERT OR UPDATE OR DELETE ON settlement_actuals FOR EACH ROW "
        "EXECUTE FUNCTION official_resolution_validate_settlement_actual_insert()"
    )
    for table_name in (
        "official_resolution_license_attestations",
        "official_resolution_source_responses",
        "official_resolution_exact_actuals",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION official_resolution_reject_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            r"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM official_resolution_license_attestations)
                   OR EXISTS (SELECT 1 FROM official_resolution_source_responses)
                   OR EXISTS (SELECT 1 FROM official_resolution_exact_actuals)
                   OR EXISTS (
                       SELECT 1 FROM settlement_actuals
                       WHERE official_exact_actual_id IS NOT NULL
                   ) THEN
                    RAISE EXCEPTION USING
                        MESSAGE = 'refusing 0039 downgrade: official resolution evidence exists',
                        HINT = 'export and explicitly reset official-resolution evidence first';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute(
        "DROP TRIGGER trg_settlement_actuals_require_official_exact ON settlement_actuals"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_exact_actuals_append_only "
        "ON official_resolution_exact_actuals"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_source_responses_append_only "
        "ON official_resolution_source_responses"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_license_attestations_append_only "
        "ON official_resolution_license_attestations"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_exact_validate_insert "
        "ON official_resolution_exact_actuals"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_response_validate_insert "
        "ON official_resolution_source_responses"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_license_validate_insert "
        "ON official_resolution_license_attestations"
    )
    op.execute(
        "DROP TRIGGER trg_official_resolution_runtime_immutable "
        "ON official_resolution_evidence_runtime"
    )
    op.execute("DROP FUNCTION official_resolution_validate_settlement_actual_insert()")
    op.execute("DROP FUNCTION official_resolution_validate_exact_actual_insert()")
    op.execute("DROP FUNCTION official_resolution_validate_response_insert()")
    op.execute("DROP FUNCTION official_resolution_validate_license_insert()")
    op.execute("DROP FUNCTION official_resolution_validate_runtime_mutation()")
    op.execute("DROP FUNCTION official_resolution_reject_mutation()")
    op.execute("DROP FUNCTION official_resolution_utc_iso(timestamptz)")
    op.execute("DROP FUNCTION official_resolution_hash(jsonb)")
    op.execute("DROP FUNCTION official_resolution_canonical_json(jsonb)")
    op.drop_constraint(
        "uq_settlement_actual_official_exact",
        "settlement_actuals",
        type_="unique",
    )
    op.drop_constraint(
        "fk_settlement_actuals_official_exact_actual",
        "settlement_actuals",
        type_="foreignkey",
    )
    op.drop_column("settlement_actuals", "official_exact_actual_id")
    op.drop_index(
        "ix_official_resolution_exact_station_date",
        table_name="official_resolution_exact_actuals",
    )
    op.drop_table("official_resolution_exact_actuals")
    op.drop_index(
        "ix_official_resolution_response_license",
        table_name="official_resolution_source_responses",
    )
    op.drop_index(
        "ix_official_resolution_response_contract_seen",
        table_name="official_resolution_source_responses",
    )
    op.drop_table("official_resolution_source_responses")
    op.drop_index(
        "ix_official_resolution_license_contract_time",
        table_name="official_resolution_license_attestations",
    )
    op.drop_table("official_resolution_license_attestations")
    op.drop_table("official_resolution_evidence_runtime")
