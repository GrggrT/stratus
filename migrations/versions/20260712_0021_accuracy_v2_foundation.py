"""Add settlement-native, point-in-time Accuracy v2 foundation.

Revision ID: 20260712_0021
Revises: 20260712_0020
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0021"
down_revision: str | None = "20260712_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

weather_kind = postgresql.ENUM("high", "low", name="weather_kind", create_type=False)


def upgrade() -> None:
    op.create_table(
        "resolution_contracts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("rules_hash", sa.String(length=64), nullable=False),
        sa.Column("source_family", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("station_code", sa.String(length=16), nullable=False),
        sa.Column("local_timezone", sa.String(length=64), nullable=False),
        sa.Column("statistic", sa.String(length=8), nullable=False),
        sa.Column("source_unit", sa.String(length=4), nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False),
        sa.Column("finalization_rule", sa.String(length=64), nullable=False),
        sa.Column("license_status", sa.String(length=32), nullable=False),
        sa.Column(
            "storage_permitted",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("license_url", sa.Text(), nullable=True),
        sa.Column("rules_raw", sa.Text(), nullable=False),
        sa.Column(
            "discovered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("statistic IN ('high', 'low')", name="ck_resolution_statistic"),
        sa.CheckConstraint("source_unit IN ('C', 'F')", name="ck_resolution_source_unit"),
        sa.CheckConstraint("decimal_places BETWEEN 0 AND 3", name="ck_resolution_decimal_places"),
        sa.UniqueConstraint("event_id", "rules_hash", name="uq_resolution_contract_event_rules"),
    )
    op.create_index(
        "ix_resolution_contract_active",
        "resolution_contracts",
        ["event_id", "retired_at"],
    )

    op.create_table(
        "settlement_actuals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "contract_id",
            sa.BigInteger(),
            sa.ForeignKey("resolution_contracts.id"),
            nullable=False,
        ),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", weather_kind, nullable=False),
        sa.Column("source_value", sa.Numeric(8, 3), nullable=False),
        sa.Column("source_unit", sa.String(length=4), nullable=False),
        sa.Column("value_c", sa.Numeric(8, 4), nullable=False),
        sa.Column("is_final", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "retrieved_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("source_response_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint("source_unit IN ('C', 'F')", name="ck_settlement_actual_unit"),
        sa.UniqueConstraint("contract_id", name="uq_settlement_actual_contract"),
    )
    op.create_index(
        "ix_settlement_actual_station_date",
        "settlement_actuals",
        ["station_id", "date", "kind"],
    )

    op.create_table(
        "forecast_model_registry",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("region", sa.String(length=64), nullable=False),
        sa.Column("resolution_km", sa.Numeric(8, 3), nullable=True),
        sa.Column("run_frequency_hours", sa.Numeric(6, 2), nullable=True),
        sa.Column("expected_latency_minutes", sa.Integer(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("license_status", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("shadow_only", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("introduced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint("provider", "model", "model_version", name="uq_forecast_model_version"),
    )

    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "registry_id",
            sa.BigInteger(),
            sa.ForeignKey("forecast_model_registry.id"),
            nullable=False,
        ),
        sa.Column("init_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upstream_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.CheckConstraint(
            "available_at >= init_time", name="ck_forecast_run_available_after_init"
        ),
        sa.UniqueConstraint("registry_id", "init_time", name="uq_forecast_run_identity"),
    )
    op.create_index("ix_forecast_runs_available", "forecast_runs", ["registry_id", "available_at"])

    op.add_column("forecasts", sa.Column("run_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "forecasts",
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Existing forecasts were available no later than their recorded fetch time.
    # Backfilling from fetched_at preserves point-in-time ordering across the migration.
    op.execute("UPDATE forecasts SET available_at = fetched_at")
    op.create_foreign_key("fk_forecasts_run_id", "forecasts", "forecast_runs", ["run_id"], ["id"])
    op.create_index("ix_forecasts_point_in_time", "forecasts", ["available_at", "valid_date"])

    op.create_table(
        "forecast_evaluations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column(
            "model_prob_id", sa.BigInteger(), sa.ForeignKey("model_probs.id"), nullable=False
        ),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("independent_sample_key", sa.String(length=128), nullable=False),
        sa.Column("model_probability", sa.Numeric(8, 6), nullable=False),
        sa.Column("horizon_market_probability", sa.Numeric(8, 6), nullable=True),
        sa.Column("market_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("executable_bid", sa.Numeric(8, 6), nullable=True),
        sa.Column("executable_ask", sa.Numeric(8, 6), nullable=True),
        sa.Column("pre_resolution_close", sa.Numeric(8, 6), nullable=True),
        sa.Column("close_snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("event_probability_total", sa.Numeric(10, 6), nullable=True),
        sa.Column("event_overround", sa.Numeric(10, 6), nullable=True),
        sa.Column("resolved_outcome", sa.Boolean(), nullable=False),
        sa.Column("model_brier", sa.Numeric(10, 8), nullable=False),
        sa.Column("market_brier", sa.Numeric(10, 8), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint("model_prob_id", name="uq_forecast_evaluation_model_prob"),
    )
    op.create_index(
        "ix_forecast_evaluations_sample",
        "forecast_evaluations",
        ["independent_sample_key", "decision_at"],
    )

    op.create_table(
        "accuracy_predictions",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("market_id", sa.BigInteger(), sa.ForeignKey("markets.id"), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expert", sa.String(length=64), nullable=False),
        sa.Column("expert_version", sa.String(length=64), nullable=False),
        sa.Column("probability", sa.Numeric(8, 6), nullable=False),
        sa.Column("mean_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("sigma_c", sa.Numeric(8, 4), nullable=True),
        sa.Column("training_sample_n", sa.Integer(), nullable=False),
        sa.Column("point_in_time_valid", sa.Boolean(), nullable=False),
        sa.Column(
            "distribution",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "market_id", "ts", "expert", "expert_version", name="uq_accuracy_prediction"
        ),
    )
    op.create_index(
        "ix_accuracy_predictions_market_ts", "accuracy_predictions", ["market_id", "ts"]
    )

    op.create_table(
        "accuracy_stack_weights",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("horizon_bucket", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("expert", sa.String(length=64), nullable=False),
        sa.Column("weight", sa.Numeric(10, 8), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("effective_sample_n", sa.Numeric(10, 3), nullable=False),
        sa.Column("out_of_fold_brier", sa.Numeric(10, 8), nullable=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name="ck_accuracy_weight_range"),
        sa.UniqueConstraint(
            "station_id",
            "horizon_bucket",
            "kind",
            "expert",
            "valid_from",
            name="uq_accuracy_stack_weight_version",
        ),
    )
    op.create_index(
        "ix_accuracy_stack_weights_current",
        "accuracy_stack_weights",
        ["station_id", "horizon_bucket", "kind", "valid_to"],
    )

    op.create_table(
        "model_skill_reports",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("report_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("registry_id", sa.BigInteger(), sa.ForeignKey("forecast_model_registry.id")),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=False),
        sa.Column("horizon_bucket", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("sample_n", sa.Integer(), nullable=False),
        sa.Column("coverage", sa.Numeric(8, 6), nullable=False),
        sa.Column("p95_latency_minutes", sa.Numeric(10, 3), nullable=True),
        sa.Column("brier", sa.Numeric(10, 8), nullable=True),
        sa.Column("crps", sa.Numeric(10, 8), nullable=True),
        sa.Column("delta_brier", sa.Numeric(10, 8), nullable=True),
        sa.Column("delta_crps", sa.Numeric(10, 8), nullable=True),
        sa.Column("ci_low", sa.Numeric(10, 8), nullable=True),
        sa.Column("ci_high", sa.Numeric(10, 8), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index(
        "ix_model_skill_latest",
        "model_skill_reports",
        ["station_id", "horizon_bucket", "kind", "evaluated_at"],
    )

    op.create_table(
        "model_drift_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_key", sa.String(length=160), nullable=False, unique=True),
        sa.Column("registry_id", sa.BigInteger(), sa.ForeignKey("forecast_model_registry.id")),
        sa.Column("station_id", sa.BigInteger(), sa.ForeignKey("stations.id"), nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_days", sa.SmallInteger(), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("baseline_value", sa.Numeric(12, 8), nullable=False),
        sa.Column("recent_value", sa.Numeric(12, 8), nullable=False),
        sa.Column("delta", sa.Numeric(12, 8), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    op.create_index("ix_model_drift_detected", "model_drift_events", ["detected_at"])


def downgrade() -> None:
    op.drop_index("ix_model_drift_detected", table_name="model_drift_events")
    op.drop_table("model_drift_events")
    op.drop_index("ix_model_skill_latest", table_name="model_skill_reports")
    op.drop_table("model_skill_reports")
    op.drop_index("ix_accuracy_stack_weights_current", table_name="accuracy_stack_weights")
    op.drop_table("accuracy_stack_weights")
    op.drop_index("ix_accuracy_predictions_market_ts", table_name="accuracy_predictions")
    op.drop_table("accuracy_predictions")
    op.drop_index("ix_forecast_evaluations_sample", table_name="forecast_evaluations")
    op.drop_table("forecast_evaluations")
    op.drop_index("ix_forecasts_point_in_time", table_name="forecasts")
    op.drop_constraint("fk_forecasts_run_id", "forecasts", type_="foreignkey")
    op.drop_column("forecasts", "available_at")
    op.drop_column("forecasts", "run_id")
    op.drop_index("ix_forecast_runs_available", table_name="forecast_runs")
    op.drop_table("forecast_runs")
    op.drop_table("forecast_model_registry")
    op.drop_index("ix_settlement_actual_station_date", table_name="settlement_actuals")
    op.drop_table("settlement_actuals")
    op.drop_index("ix_resolution_contract_active", table_name="resolution_contracts")
    op.drop_table("resolution_contracts")
