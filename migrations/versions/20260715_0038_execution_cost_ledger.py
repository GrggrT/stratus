"""Add the zero-backfill execution-cost and post-fill markout ledger.

Revision ID: 20260715_0038
Revises: 20260715_0037
Create Date: 2026-07-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_0038"
down_revision: str | None = "20260715_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFINITION_HASH = "9a5878e7867d3fba6bebfc1aa986474dc6e29b3b26f07532bfe9a730acd5b6b8"
EXECUTION_MODE = postgresql.ENUM("paper", "live", name="execution_mode", create_type=False)
TRADE_SIDE = postgresql.ENUM("buy_yes", "buy_no", name="trade_side", create_type=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "execution_cost_runtime",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("paper_execution_evidence", sa.String(length=32), nullable=False),
        sa.Column("live_reconciliation_evidence", sa.String(length=40), nullable=False),
        sa.Column("postfill_mark_protocol", sa.String(length=32), nullable=False),
        sa.Column(
            "mark_horizons_seconds",
            postgresql.ARRAY(sa.Integer()),
            nullable=False,
        ),
        sa.Column("mark_grace_seconds", sa.Integer(), nullable=False),
        sa.Column("live_fill_snapshot_max_age_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_execution_cost_runtime_singleton"),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' "
            "AND paper_execution_evidence = 'paper-clob-cross-v1' "
            "AND live_reconciliation_evidence = 'live-sdk-reconciliation-v1' "
            "AND postfill_mark_protocol = 'clob-dual-postfill-v1' "
            "AND mark_horizons_seconds = ARRAY[300, 1800]::integer[] "
            "AND mark_grace_seconds = 300 "
            "AND live_fill_snapshot_max_age_seconds = 120",
            name="ck_execution_cost_runtime_protocol",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_cost_runtime_hash",
        ),
    )
    # The database clock is the cohort boundary.  No historical order, fill,
    # position, CLV row, or 0035 validation row is copied into this ledger.
    op.execute(
        "INSERT INTO execution_cost_runtime ("
        "id, opened_at, definition_version, definition_hash, "
        "paper_execution_evidence, live_reconciliation_evidence, "
        "postfill_mark_protocol, mark_horizons_seconds, mark_grace_seconds, "
        "live_fill_snapshot_max_age_seconds) VALUES ("
        f"1, clock_timestamp(), 'execution-cost-v1', '{DEFINITION_HASH}', "
        "'paper-clob-cross-v1', 'live-sdk-reconciliation-v1', "
        "'clob-dual-postfill-v1', ARRAY[300, 1800], 300, 120)"
    )

    op.create_table(
        "execution_order_intents",
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
            nullable=False,
        ),
        sa.Column(
            "market_id",
            sa.BigInteger(),
            sa.ForeignKey("markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "decision_clob_snapshot_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("mode", EXECUTION_MODE, nullable=False),
        sa.Column("side", TRADE_SIDE, nullable=False),
        sa.Column("token_id", sa.String(length=80), nullable=False),
        sa.Column("pipeline_cohort", sa.String(length=128), nullable=False),
        sa.Column("signal_record_hash", sa.String(length=64), nullable=False),
        sa.Column("model_record_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_record_hash", sa.String(length=64), nullable=False),
        sa.Column("yes_model_probability", sa.Numeric(10, 8), nullable=False),
        sa.Column("side_model_probability", sa.Numeric(10, 8), nullable=False),
        sa.Column("side_best_bid", sa.Numeric(10, 8), nullable=False),
        sa.Column("side_best_ask", sa.Numeric(10, 8), nullable=False),
        sa.Column("side_midpoint", sa.Numeric(10, 8), nullable=False),
        sa.Column("order_style", sa.String(length=16), nullable=False),
        sa.Column("requested_limit_price", sa.Numeric(10, 8), nullable=False),
        sa.Column("requested_shares", sa.Numeric(18, 6), nullable=False),
        sa.Column("requested_notional_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("expected_fee_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("expected_fee_unavailable_reason", sa.String(length=64), nullable=True),
        sa.Column("expected_slippage_per_share", sa.Numeric(12, 10), nullable=True),
        sa.Column(
            "expected_slippage_unavailable_reason", sa.String(length=64), nullable=True
        ),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "intent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_execution_order_intents_order"),
        sa.CheckConstraint(
            "yes_model_probability > 0 AND yes_model_probability < 1 "
            "AND side_model_probability > 0 AND side_model_probability < 1",
            name="ck_execution_order_intents_probability_range",
        ),
        sa.CheckConstraint(
            "side_best_bid > 0 AND side_best_bid < 1 "
            "AND side_best_ask > 0 AND side_best_ask < 1 "
            "AND side_midpoint > 0 AND side_midpoint < 1 "
            "AND side_best_bid <= side_midpoint AND side_midpoint <= side_best_ask",
            name="ck_execution_order_intents_book",
        ),
        sa.CheckConstraint(
            "order_style IN ('maker', 'taker')",
            name="ck_execution_order_intents_style",
        ),
        sa.CheckConstraint(
            "requested_limit_price > 0 AND requested_limit_price < 1 "
            "AND requested_shares > 0 AND requested_notional_usd > 0",
            name="ck_execution_order_intents_request",
        ),
        sa.CheckConstraint(
            "(expected_fee_usd IS NOT NULL AND expected_fee_usd >= 0 "
            " AND expected_fee_unavailable_reason IS NULL) OR "
            "(expected_fee_usd IS NULL AND expected_fee_unavailable_reason IS NOT NULL "
            " AND length(btrim(expected_fee_unavailable_reason)) > 0)",
            name="ck_execution_order_intents_expected_fee",
        ),
        sa.CheckConstraint(
            "(expected_slippage_per_share IS NOT NULL "
            " AND expected_slippage_per_share >= 0 "
            " AND expected_slippage_unavailable_reason IS NULL) OR "
            "(expected_slippage_per_share IS NULL "
            " AND expected_slippage_unavailable_reason IS NOT NULL "
            " AND length(btrim(expected_slippage_unavailable_reason)) > 0)",
            name="ck_execution_order_intents_expected_slippage",
        ),
        sa.CheckConstraint(
            "decision_at <= intent_at",
            name="ck_execution_order_intents_clock_order",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' AND "
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "signal_record_hash ~ '^[0-9a-f]{64}$' AND "
            "model_record_hash ~ '^[0-9a-f]{64}$' AND "
            "snapshot_record_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_order_intents_hashes",
        ),
    )
    op.create_index(
        "ix_execution_order_intents_market_time",
        "execution_order_intents",
        ["market_id", "intent_at"],
    )
    op.create_index(
        "ix_execution_order_intents_mode_time",
        "execution_order_intents",
        ["mode", "intent_at"],
    )

    op.create_table(
        "execution_reconciliation_observations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("observation_kind", sa.String(length=16), nullable=False),
        sa.Column("sdk_method", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("request_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("response_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("normalized_payload", postgresql.JSONB(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "order_id",
            "observation_kind",
            "sdk_method",
            "payload_sha256",
            name="uq_execution_reconciliation_observations_content",
        ),
        sa.CheckConstraint(
            "observation_kind IN ('placement', 'order', 'trades', 'cancel')",
            name="ck_execution_reconciliation_observations_kind",
        ),
        sa.CheckConstraint(
            "request_started_at <= response_received_at "
            "AND response_received_at <= first_seen_at",
            name="ck_execution_reconciliation_observations_timing",
        ),
        sa.CheckConstraint(
            "length(btrim(sdk_method)) > 0 AND length(btrim(parser_version)) > 0",
            name="ck_execution_reconciliation_observations_parser",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' AND "
            "payload_sha256 ~ '^[0-9a-f]{64}$' AND "
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_reconciliation_observations_hashes",
        ),
    )
    op.create_index(
        "ix_execution_reconciliation_observations_order_seen",
        "execution_reconciliation_observations",
        ["order_id", "first_seen_at"],
    )

    op.create_table(
        "execution_fill_legs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "intent_id",
            sa.BigInteger(),
            sa.ForeignKey("execution_order_intents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
            "market_id",
            sa.BigInteger(),
            sa.ForeignKey("markets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reconciliation_observation_id",
            sa.BigInteger(),
            sa.ForeignKey(
                "execution_reconciliation_observations.id", ondelete="RESTRICT"
            ),
            nullable=True,
        ),
        sa.Column(
            "fill_clob_snapshot_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("mode", EXECUTION_MODE, nullable=False),
        sa.Column("side", TRADE_SIDE, nullable=False),
        sa.Column("token_id", sa.String(length=80), nullable=False),
        sa.Column("source_kind", sa.String(length=24), nullable=False),
        sa.Column("fill_clob_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("external_trade_id", sa.String(length=128), nullable=True),
        sa.Column("external_leg_key", sa.String(length=160), nullable=False),
        sa.Column("leg_sequence", sa.Integer(), nullable=False),
        sa.Column("liquidity_role", sa.String(length=16), nullable=False),
        sa.Column("shares", sa.Numeric(18, 6), nullable=False),
        sa.Column("price", sa.Numeric(10, 8), nullable=False),
        sa.Column("notional_usd", sa.Numeric(18, 6), nullable=False),
        sa.Column("fee_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("fee_semantics", sa.String(length=32), nullable=False),
        sa.Column("venue_filled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("fill_effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timestamp_semantics", sa.String(length=32), nullable=False),
        sa.Column("fill_best_bid", sa.Numeric(10, 8), nullable=True),
        sa.Column("fill_best_ask", sa.Numeric(10, 8), nullable=True),
        sa.Column("fill_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_reason", sa.String(length=64), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "order_id",
            "external_leg_key",
            name="uq_execution_fill_legs_order_external_key",
        ),
        sa.UniqueConstraint(
            "intent_id",
            "leg_sequence",
            name="uq_execution_fill_legs_intent_sequence",
        ),
        sa.CheckConstraint("leg_sequence > 0", name="ck_execution_fill_legs_sequence"),
        sa.CheckConstraint(
            "source_kind IN ('paper_simulation', 'live_sdk')",
            name="ck_execution_fill_legs_source",
        ),
        sa.CheckConstraint(
            "liquidity_role IN ('maker', 'taker', 'unknown')",
            name="ck_execution_fill_legs_role",
        ),
        sa.CheckConstraint(
            "shares > 0 AND price > 0 AND price < 1 AND notional_usd > 0",
            name="ck_execution_fill_legs_fill",
        ),
        sa.CheckConstraint(
            "fee_semantics IN ('paper_fee_model_v1', 'venue_reported', 'unknown') "
            "AND ((fee_usd IS NULL AND fee_semantics = 'unknown') OR "
            "(fee_usd IS NOT NULL AND fee_usd >= 0 AND fee_semantics <> 'unknown'))",
            name="ck_execution_fill_legs_fee",
        ),
        sa.CheckConstraint(
            "timestamp_semantics IN "
            "('paper_clob_first_seen', 'verified_venue_fill', 'unverified')",
            name="ck_execution_fill_legs_timestamp",
        ),
        sa.CheckConstraint(
            "(fill_clob_snapshot_id IS NULL AND fill_clob_snapshot_hash IS NULL "
            " AND fill_best_bid IS NULL AND fill_best_ask IS NULL "
            " AND fill_midpoint IS NULL) OR "
            "(fill_clob_snapshot_id IS NOT NULL "
            " AND fill_clob_snapshot_hash ~ '^[0-9a-f]{64}$')",
            name="ck_execution_fill_legs_snapshot_shape",
        ),
        sa.CheckConstraint(
            "(fill_best_bid IS NULL OR (fill_best_bid > 0 AND fill_best_bid < 1)) "
            "AND (fill_best_ask IS NULL OR (fill_best_ask > 0 AND fill_best_ask < 1)) "
            "AND (fill_midpoint IS NULL OR (fill_midpoint > 0 AND fill_midpoint < 1)) "
            "AND (fill_best_bid IS NULL OR fill_best_ask IS NULL "
            " OR fill_best_bid <= fill_best_ask) "
            "AND (fill_best_bid IS NULL OR fill_midpoint IS NULL "
            " OR fill_best_bid <= fill_midpoint) "
            "AND (fill_midpoint IS NULL OR fill_best_ask IS NULL "
            " OR fill_midpoint <= fill_best_ask)",
            name="ck_execution_fill_legs_book",
        ),
        sa.CheckConstraint(
            "(eligible AND eligibility_reason = 'eligible' "
            " AND liquidity_role <> 'unknown' AND fee_usd IS NOT NULL "
            " AND fill_clob_snapshot_id IS NOT NULL "
            " AND fill_best_bid IS NOT NULL AND fill_best_ask IS NOT NULL "
            " AND fill_midpoint IS NOT NULL AND fill_effective_at IS NOT NULL) OR "
            "(NOT eligible AND eligibility_reason <> 'eligible' "
            " AND length(btrim(eligibility_reason)) > 0)",
            name="ck_execution_fill_legs_eligibility",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' AND "
            "definition_hash ~ '^[0-9a-f]{64}$' AND "
            "source_hash ~ '^[0-9a-f]{64}$' AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_fill_legs_hashes",
        ),
    )
    op.create_index(
        "ix_execution_fill_legs_market_effective",
        "execution_fill_legs",
        ["market_id", "fill_effective_at"],
    )
    op.create_index(
        "ix_execution_fill_legs_intent",
        "execution_fill_legs",
        ["intent_id", "leg_sequence"],
    )

    op.create_table(
        "execution_cost_obligations",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "intent_id",
            sa.BigInteger(),
            sa.ForeignKey("execution_order_intents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("terminal_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.String(length=64), nullable=True),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("order_id", name="uq_execution_cost_obligations_order"),
        sa.UniqueConstraint("intent_id", name="uq_execution_cost_obligations_intent"),
        sa.CheckConstraint(
            "terminal_status IN ('filled', 'partially_filled', 'canceled', "
            "'unfilled_maker', 'submit_failed', 'rejected', 'expired')",
            name="ck_execution_cost_obligations_terminal_status",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'waiting_for_marks', 'sealed')",
            name="ck_execution_cost_obligations_state",
        ),
        sa.CheckConstraint(
            "(state = 'sealed' AND next_due_at IS NULL AND finalized_at IS NOT NULL) OR "
            "(state <> 'sealed' AND next_due_at IS NOT NULL AND finalized_at IS NULL)",
            name="ck_execution_cost_obligations_state_shape",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' "
            "AND definition_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_cost_obligations_definition",
        ),
    )
    op.create_index(
        "ix_execution_cost_obligations_due",
        "execution_cost_obligations",
        ["state", "next_due_at"],
    )

    op.create_table(
        "execution_postfill_marks",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "fill_leg_id",
            sa.BigInteger(),
            sa.ForeignKey("execution_fill_legs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("horizon_seconds", sa.Integer(), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "clob_snapshot_id",
            sa.BigInteger(),
            sa.ForeignKey("clob_market_snapshots.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("clob_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("missing_reason", sa.String(length=64), nullable=True),
        sa.Column("side_best_bid", sa.Numeric(10, 8), nullable=True),
        sa.Column("side_best_ask", sa.Numeric(10, 8), nullable=True),
        sa.Column("side_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offset_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "fill_leg_id",
            "horizon_seconds",
            name="uq_execution_postfill_marks_leg_horizon",
        ),
        sa.CheckConstraint(
            "horizon_seconds IN (300, 1800)",
            name="ck_execution_postfill_marks_horizon",
        ),
        sa.CheckConstraint(
            "status IN ('observed', 'missing')",
            name="ck_execution_postfill_marks_status",
        ),
        sa.CheckConstraint(
            "(status = 'observed' AND clob_snapshot_id IS NOT NULL "
            " AND clob_snapshot_hash ~ '^[0-9a-f]{64}$' "
            " AND missing_reason IS NULL AND side_best_bid IS NOT NULL "
            " AND side_best_ask IS NOT NULL AND side_midpoint IS NOT NULL "
            " AND observed_at IS NOT NULL AND offset_seconds IS NOT NULL "
            " AND offset_seconds >= 0) OR "
            "(status = 'missing' AND clob_snapshot_id IS NULL "
            " AND clob_snapshot_hash IS NULL AND missing_reason IS NOT NULL "
            " AND length(btrim(missing_reason)) > 0 AND side_best_bid IS NULL "
            " AND side_best_ask IS NULL AND side_midpoint IS NULL "
            " AND observed_at IS NULL AND offset_seconds IS NULL)",
            name="ck_execution_postfill_marks_shape",
        ),
        sa.CheckConstraint(
            "side_best_bid IS NULL OR (side_best_bid > 0 AND side_best_bid < 1 "
            " AND side_best_ask > 0 AND side_best_ask < 1 "
            " AND side_midpoint > 0 AND side_midpoint < 1 "
            " AND side_best_bid <= side_midpoint AND side_midpoint <= side_best_ask)",
            name="ck_execution_postfill_marks_book",
        ),
        sa.CheckConstraint(
            "sealed_at >= target_at",
            name="ck_execution_postfill_marks_sealed_after_target",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' AND "
            "definition_hash ~ '^[0-9a-f]{64}$' AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_postfill_marks_hashes",
        ),
    )
    op.create_index(
        "ix_execution_postfill_marks_target",
        "execution_postfill_marks",
        ["target_at", "status"],
    )

    op.create_table(
        "execution_cost_seals",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "obligation_id",
            sa.BigInteger(),
            sa.ForeignKey("execution_cost_obligations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "intent_id",
            sa.BigInteger(),
            sa.ForeignKey("execution_order_intents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("eligible", sa.Boolean(), nullable=False),
        sa.Column("eligibility_reason", sa.String(length=64), nullable=False),
        sa.Column("reason_detail", postgresql.JSONB(), nullable=True),
        sa.Column("aggregate_role", sa.String(length=16), nullable=False),
        sa.Column("leg_count", sa.Integer(), nullable=False),
        sa.Column("total_shares", sa.Numeric(18, 6), nullable=True),
        sa.Column("filled_notional_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("vwap_fill_price", sa.Numeric(10, 8), nullable=True),
        sa.Column("total_fee_usd", sa.Numeric(18, 8), nullable=True),
        sa.Column("fee_per_share", sa.Numeric(12, 10), nullable=True),
        sa.Column("intent_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("side_model_probability", sa.Numeric(10, 8), nullable=True),
        sa.Column("weighted_fill_midpoint", sa.Numeric(10, 8), nullable=True),
        sa.Column("gross_model_edge", sa.Numeric(12, 10), nullable=True),
        sa.Column("latency_movement", sa.Numeric(12, 10), nullable=True),
        sa.Column("taker_spread", sa.Numeric(12, 10), nullable=True),
        sa.Column("taker_slippage", sa.Numeric(12, 10), nullable=True),
        sa.Column("maker_improvement", sa.Numeric(12, 10), nullable=True),
        sa.Column("fee_drag", sa.Numeric(12, 10), nullable=True),
        sa.Column("realized_net_edge", sa.Numeric(12, 10), nullable=True),
        sa.Column("expected_slippage_per_share", sa.Numeric(12, 10), nullable=True),
        sa.Column("expected_slippage_coverage", sa.String(length=24), nullable=False),
        sa.Column("adverse_selection_300s", sa.Numeric(12, 10), nullable=True),
        sa.Column("markout_300s_coverage", sa.String(length=24), nullable=False),
        sa.Column("adverse_selection_1800s", sa.Numeric(12, 10), nullable=True),
        sa.Column("markout_1800s_coverage", sa.String(length=24), nullable=False),
        sa.Column("definition_version", sa.String(length=32), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("record_hash", sa.String(length=64), nullable=False),
        sa.UniqueConstraint("obligation_id", name="uq_execution_cost_seals_obligation"),
        sa.UniqueConstraint("intent_id", name="uq_execution_cost_seals_intent"),
        sa.UniqueConstraint("order_id", name="uq_execution_cost_seals_order"),
        sa.CheckConstraint("leg_count >= 0", name="ck_execution_cost_seals_leg_count"),
        sa.CheckConstraint(
            "aggregate_role IN ('maker', 'taker', 'mixed', 'unknown')",
            name="ck_execution_cost_seals_role",
        ),
        sa.CheckConstraint(
            "expected_slippage_coverage IN ('modeled', 'not_modeled', 'unavailable')",
            name="ck_execution_cost_seals_expected_slippage_coverage",
        ),
        sa.CheckConstraint(
            "markout_300s_coverage IN ('complete', 'missing', 'not_applicable') "
            "AND markout_1800s_coverage IN ('complete', 'missing', 'not_applicable')",
            name="ck_execution_cost_seals_markout_coverage",
        ),
        sa.CheckConstraint(
            "(eligible AND eligibility_reason = 'eligible' AND leg_count > 0 "
            " AND total_shares > 0 AND filled_notional_usd > 0 "
            " AND vwap_fill_price > 0 AND vwap_fill_price < 1 "
            " AND total_fee_usd >= 0 AND fee_per_share >= 0 "
            " AND intent_midpoint > 0 AND intent_midpoint < 1 "
            " AND side_model_probability > 0 AND side_model_probability < 1 "
            " AND weighted_fill_midpoint > 0 AND weighted_fill_midpoint < 1 "
            " AND gross_model_edge IS NOT NULL AND latency_movement IS NOT NULL "
            " AND taker_spread IS NOT NULL AND taker_slippage IS NOT NULL "
            " AND maker_improvement IS NOT NULL AND fee_drag IS NOT NULL "
            " AND realized_net_edge IS NOT NULL) OR "
            "(NOT eligible AND eligibility_reason <> 'eligible' "
            " AND length(btrim(eligibility_reason)) > 0)",
            name="ck_execution_cost_seals_eligibility",
        ),
        sa.CheckConstraint(
            "NOT eligible OR ("
            "abs(realized_net_edge - (gross_model_edge - latency_movement "
            "- taker_spread - taker_slippage + maker_improvement - fee_drag)) "
            "<= 0.000000005 AND "
            "abs(realized_net_edge - (side_model_probability - vwap_fill_price "
            "- fee_per_share)) <= 0.000000005)",
            name="ck_execution_cost_seals_algebra",
        ),
        sa.CheckConstraint(
            "(expected_slippage_coverage = 'modeled' "
            " AND expected_slippage_per_share IS NOT NULL) OR "
            "(expected_slippage_coverage <> 'modeled' "
            " AND expected_slippage_per_share IS NULL)",
            name="ck_execution_cost_seals_expected_slippage_shape",
        ),
        sa.CheckConstraint(
            "(markout_300s_coverage = 'complete' "
            " AND adverse_selection_300s IS NOT NULL) OR "
            "(markout_300s_coverage <> 'complete' "
            " AND adverse_selection_300s IS NULL)",
            name="ck_execution_cost_seals_markout_300s_shape",
        ),
        sa.CheckConstraint(
            "(markout_1800s_coverage = 'complete' "
            " AND adverse_selection_1800s IS NOT NULL) OR "
            "(markout_1800s_coverage <> 'complete' "
            " AND adverse_selection_1800s IS NULL)",
            name="ck_execution_cost_seals_markout_1800s_shape",
        ),
        sa.CheckConstraint(
            "definition_version = 'execution-cost-v1' AND "
            "definition_hash ~ '^[0-9a-f]{64}$' AND record_hash ~ '^[0-9a-f]{64}$'",
            name="ck_execution_cost_seals_hashes",
        ),
    )
    op.create_index(
        "ix_execution_cost_seals_mode_scope",
        "execution_cost_seals",
        ["eligible", "sealed_at"],
    )

    # PostgreSQL jsonb::text is the frozen canonical representation.  Database
    # triggers authoritatively stamp witness clocks and derive record hashes.
    op.execute(
        r"""
        CREATE FUNCTION execution_cost_hash(value jsonb)
        RETURNS text LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT encode(
                digest(convert_to(value::text, 'UTF8'), 'sha256'),
                'hex'
            )
        $$
        """
    )
    op.execute(
        r"""
        CREATE FUNCTION execution_cost_reject_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
        END;
        $$
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_runtime_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'execution cost runtime is immutable';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_cost_runtime_immutable "
        "BEFORE UPDATE OR DELETE ON execution_cost_runtime FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_runtime_mutation()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_intent_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            order_row orders%ROWTYPE;
            signal_row signals%ROWTYPE;
            model_row model_probs%ROWTYPE;
            snapshot_row clob_market_snapshots%ROWTYPE;
            expected_cohort text;
            expected_token text;
            expected_bid numeric;
            expected_ask numeric;
            expected_mid numeric;
            expected_side_probability numeric;
            expected_signal_hash text;
            expected_model_hash text;
        BEGIN
            NEW.intent_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT order_row FROM orders WHERE id = NEW.order_id;
            SELECT * INTO STRICT signal_row FROM signals WHERE id = NEW.signal_id;
            SELECT * INTO STRICT model_row FROM model_probs WHERE id = NEW.model_prob_id;
            SELECT * INTO STRICT snapshot_row
            FROM clob_market_snapshots WHERE id = NEW.decision_clob_snapshot_id;

            expected_cohort := prospective_validation_pipeline_cohort(model_row.components);
            IF expected_cohort IS NULL OR length(btrim(expected_cohort)) = 0 THEN
                RAISE EXCEPTION 'execution intent pipeline cohort is unavailable';
            END IF;
            IF NEW.side::text = 'buy_yes' THEN
                expected_token := snapshot_row.yes_token_id;
                expected_bid := snapshot_row.yes_best_bid;
                expected_ask := snapshot_row.yes_best_ask;
                expected_mid := snapshot_row.yes_midpoint;
                expected_side_probability := signal_row.model_prob;
            ELSE
                expected_token := snapshot_row.no_token_id;
                expected_bid := snapshot_row.no_best_bid;
                expected_ask := snapshot_row.no_best_ask;
                expected_mid := snapshot_row.no_midpoint;
                expected_side_probability := 1 - signal_row.model_prob;
            END IF;

            IF order_row.signal_id IS DISTINCT FROM signal_row.id
               OR signal_row.market_id IS DISTINCT FROM NEW.market_id
               OR model_row.market_id IS DISTINCT FROM NEW.market_id
               OR snapshot_row.market_id IS DISTINCT FROM NEW.market_id
               OR signal_row.clob_snapshot_id IS DISTINCT FROM snapshot_row.id
               OR model_row.market_snapshot_id IS DISTINCT FROM snapshot_row.id
               OR order_row.mode::text IS DISTINCT FROM NEW.mode::text
               OR order_row.side::text IS DISTINCT FROM NEW.side::text
               OR signal_row.side::text IS DISTINCT FROM NEW.side::text
               OR order_row.token_id IS DISTINCT FROM expected_token
               OR signal_row.token_id IS DISTINCT FROM expected_token
               OR NEW.token_id IS DISTINCT FROM expected_token THEN
                RAISE EXCEPTION 'execution intent source identity mismatch';
            END IF;
            IF signal_row.benchmark_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR model_row.market_anchor_protocol IS DISTINCT FROM 'clob-dual-v1'
               OR NOT snapshot_row.complete OR NOT snapshot_row.eligible THEN
                RAISE EXCEPTION 'execution intent requires eligible dual-token decision evidence';
            END IF;
            IF model_row.ts > signal_row.ts
               OR model_row.prob IS DISTINCT FROM signal_row.model_prob
               OR NEW.yes_model_probability IS DISTINCT FROM signal_row.model_prob
               OR NEW.side_model_probability IS DISTINCT FROM expected_side_probability
               OR NEW.side_best_bid IS DISTINCT FROM expected_bid
               OR NEW.side_best_ask IS DISTINCT FROM expected_ask
               OR NEW.side_midpoint IS DISTINCT FROM expected_mid
               OR NEW.pipeline_cohort IS DISTINCT FROM expected_cohort
               OR NEW.decision_at IS DISTINCT FROM signal_row.ts THEN
                RAISE EXCEPTION 'execution intent decision projection mismatch';
            END IF;
            IF NEW.requested_limit_price IS DISTINCT FROM order_row.price
               OR NEW.requested_notional_usd IS DISTINCT FROM order_row.size_usd
               OR abs(NEW.requested_shares - order_row.size_usd / order_row.price)
                    > 0.0000005 THEN
                RAISE EXCEPTION 'execution intent requested size mismatch';
            END IF;
            IF NEW.intent_at < runtime_row.opened_at
               OR NEW.intent_at < order_row.created_at
               OR NEW.decision_at > NEW.intent_at
               OR NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution intent runtime boundary mismatch';
            END IF;
            IF order_row.status IN (
                'filled', 'partially_filled', 'canceled', 'unfilled_maker',
                'submit_failed', 'rejected', 'expired'
            ) THEN
                RAISE EXCEPTION 'execution intent must precede terminal order state';
            END IF;

            expected_signal_hash := prospective_validation_hash(jsonb_build_object(
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
            ));
            expected_model_hash := prospective_validation_hash(jsonb_build_object(
                'id', model_row.id,
                'market_id', model_row.market_id,
                'ts', prospective_validation_iso_utc(model_row.ts),
                'prob', trim_scale(model_row.prob)::text,
                'sigma', trim_scale(model_row.sigma)::text,
                'horizon_h', trim_scale(model_row.horizon_h)::text,
                'components', model_row.components,
                'market_snapshot_id', model_row.market_snapshot_id,
                'market_anchor_protocol', model_row.market_anchor_protocol
            ));
            IF NEW.signal_record_hash IS DISTINCT FROM expected_signal_hash
               OR NEW.model_record_hash IS DISTINCT FROM expected_model_hash
               OR NEW.snapshot_record_hash IS DISTINCT FROM snapshot_row.record_hash THEN
                RAISE EXCEPTION 'execution intent source hash mismatch';
            END IF;

            NEW.record_hash := execution_cost_hash(jsonb_build_object(
                'record_kind', 'intent',
                'decision_at', NEW.decision_at,
                'decision_clob_snapshot_id', NEW.decision_clob_snapshot_id,
                'definition_hash', NEW.definition_hash,
                'definition_version', NEW.definition_version,
                'expected_fee_unavailable_reason', NEW.expected_fee_unavailable_reason,
                'expected_fee_usd', NEW.expected_fee_usd,
                'expected_slippage_per_share', NEW.expected_slippage_per_share,
                'expected_slippage_unavailable_reason',
                    NEW.expected_slippage_unavailable_reason,
                'intent_at', NEW.intent_at,
                'market_id', NEW.market_id,
                'mode', NEW.mode::text,
                'model_prob_id', NEW.model_prob_id,
                'model_record_hash', NEW.model_record_hash,
                'order_id', NEW.order_id,
                'order_style', NEW.order_style,
                'pipeline_cohort', NEW.pipeline_cohort,
                'requested_limit_price', NEW.requested_limit_price,
                'requested_notional_usd', NEW.requested_notional_usd,
                'requested_shares', NEW.requested_shares,
                'side', NEW.side::text,
                'side_best_ask', NEW.side_best_ask,
                'side_best_bid', NEW.side_best_bid,
                'side_midpoint', NEW.side_midpoint,
                'side_model_probability', NEW.side_model_probability,
                'signal_id', NEW.signal_id,
                'signal_record_hash', NEW.signal_record_hash,
                'snapshot_record_hash', NEW.snapshot_record_hash,
                'token_id', NEW.token_id,
                'yes_model_probability', NEW.yes_model_probability
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_order_intents_validate_insert "
        "BEFORE INSERT ON execution_order_intents FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_intent_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_observation_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            intent_row execution_order_intents%ROWTYPE;
            expected_payload_hash text;
        BEGIN
            NEW.first_seen_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT intent_row
            FROM execution_order_intents WHERE order_id = NEW.order_id;
            expected_payload_hash := execution_cost_hash(NEW.normalized_payload);
            IF intent_row.mode::text IS DISTINCT FROM 'live'
               OR NEW.request_started_at < intent_row.intent_at
               OR NEW.response_received_at < NEW.request_started_at
               OR NEW.response_received_at > NEW.first_seen_at THEN
                RAISE EXCEPTION 'execution reconciliation observation timing/identity mismatch';
            END IF;
            IF NEW.payload_sha256 IS DISTINCT FROM expected_payload_hash THEN
                RAISE EXCEPTION 'execution reconciliation payload hash mismatch';
            END IF;
            IF NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution reconciliation definition mismatch';
            END IF;
            NEW.record_hash := execution_cost_hash(jsonb_build_object(
                'record_kind', 'observation',
                'definition_hash', NEW.definition_hash,
                'definition_version', NEW.definition_version,
                'first_seen_at', NEW.first_seen_at,
                'payload_sha256', NEW.payload_sha256,
                'observation_kind', NEW.observation_kind,
                'order_id', NEW.order_id,
                'parser_version', NEW.parser_version,
                'request_started_at', NEW.request_started_at,
                'response_received_at', NEW.response_received_at,
                'sdk_method', NEW.sdk_method
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_reconciliation_observations_validate_insert "
        "BEFORE INSERT ON execution_reconciliation_observations FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_observation_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_fill_leg_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            intent_row execution_order_intents%ROWTYPE;
            observation_row execution_reconciliation_observations%ROWTYPE;
            snapshot_row clob_market_snapshots%ROWTYPE;
            batch_row clob_book_batches%ROWTYPE;
            expected_snapshot_id bigint;
            expected_token text;
            expected_bid numeric;
            expected_ask numeric;
            expected_mid numeric;
            expected_source_hash text;
        BEGIN
            NEW.first_seen_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT intent_row
            FROM execution_order_intents WHERE id = NEW.intent_id;
            IF NEW.reconciliation_observation_id IS NOT NULL THEN
                SELECT * INTO STRICT observation_row
                FROM execution_reconciliation_observations
                WHERE id = NEW.reconciliation_observation_id;
            END IF;
            IF NEW.fill_clob_snapshot_id IS NOT NULL THEN
                SELECT * INTO STRICT snapshot_row
                FROM clob_market_snapshots WHERE id = NEW.fill_clob_snapshot_id;
                SELECT * INTO STRICT batch_row
                FROM clob_book_batches WHERE id = snapshot_row.batch_id;
                IF NEW.side::text = 'buy_yes' THEN
                    expected_token := snapshot_row.yes_token_id;
                    expected_bid := snapshot_row.yes_best_bid;
                    expected_ask := snapshot_row.yes_best_ask;
                    expected_mid := snapshot_row.yes_midpoint;
                ELSE
                    expected_token := snapshot_row.no_token_id;
                    expected_bid := snapshot_row.no_best_bid;
                    expected_ask := snapshot_row.no_best_ask;
                    expected_mid := snapshot_row.no_midpoint;
                END IF;
                IF snapshot_row.market_id IS DISTINCT FROM NEW.market_id
                   OR NEW.token_id IS DISTINCT FROM expected_token
                   OR NEW.fill_clob_snapshot_hash IS DISTINCT FROM snapshot_row.record_hash
                   OR NEW.fill_best_bid IS DISTINCT FROM expected_bid
                   OR NEW.fill_best_ask IS DISTINCT FROM expected_ask
                   OR NEW.fill_midpoint IS DISTINCT FROM expected_mid THEN
                    RAISE EXCEPTION 'execution fill snapshot projection mismatch';
                END IF;
            END IF;

            IF NEW.order_id IS DISTINCT FROM intent_row.order_id
               OR NEW.signal_id IS DISTINCT FROM intent_row.signal_id
               OR NEW.market_id IS DISTINCT FROM intent_row.market_id
               OR NEW.mode::text IS DISTINCT FROM intent_row.mode::text
               OR NEW.side::text IS DISTINCT FROM intent_row.side::text
               OR NEW.token_id IS DISTINCT FROM intent_row.token_id
               OR NEW.first_seen_at < intent_row.intent_at THEN
                RAISE EXCEPTION 'execution fill leg intent identity mismatch';
            END IF;
            IF length(btrim(NEW.external_leg_key)) = 0
               OR abs(NEW.notional_usd - NEW.shares * NEW.price) > 0.000001 THEN
                RAISE EXCEPTION 'execution fill leg arithmetic/identity mismatch';
            END IF;
            IF NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution fill leg definition mismatch';
            END IF;

            IF NEW.source_kind = 'paper_simulation' THEN
                IF NEW.mode::text IS DISTINCT FROM 'paper'
                   OR NEW.reconciliation_observation_id IS NOT NULL
                   OR NEW.fill_clob_snapshot_id IS NULL
                   OR NEW.venue_filled_at IS NOT NULL
                   OR NEW.timestamp_semantics IS DISTINCT FROM 'paper_clob_first_seen'
                   OR NEW.fill_effective_at IS DISTINCT FROM batch_row.first_seen_at
                   OR batch_row.request_started_at < intent_row.intent_at
                   OR batch_row.first_seen_at < intent_row.intent_at THEN
                    RAISE EXCEPTION 'paper execution fill evidence mismatch';
                END IF;
                expected_source_hash := batch_row.record_hash;
            ELSE
                IF NEW.mode::text IS DISTINCT FROM 'live'
                   OR NEW.reconciliation_observation_id IS NULL
                   OR observation_row.order_id IS DISTINCT FROM NEW.order_id THEN
                    RAISE EXCEPTION 'live execution fill observation mismatch';
                END IF;
                expected_source_hash := observation_row.record_hash;
                IF NEW.timestamp_semantics = 'verified_venue_fill' THEN
                    IF NEW.venue_filled_at IS NULL
                       OR NEW.fill_effective_at IS DISTINCT FROM NEW.venue_filled_at
                       OR NEW.venue_filled_at < intent_row.intent_at
                       OR NEW.venue_filled_at > NEW.first_seen_at THEN
                        RAISE EXCEPTION 'verified live fill timestamp mismatch';
                    END IF;
                ELSIF NEW.venue_filled_at IS NOT NULL OR NEW.fill_effective_at IS NOT NULL THEN
                    RAISE EXCEPTION 'unverified live fill cannot claim an effective time';
                END IF;
                IF NEW.eligible THEN
                    IF observation_row.observation_kind IS DISTINCT FROM 'trades'
                       OR NEW.external_trade_id IS NULL
                       OR length(btrim(NEW.external_trade_id)) = 0
                       OR NEW.timestamp_semantics IS DISTINCT FROM 'verified_venue_fill' THEN
                        RAISE EXCEPTION 'eligible live fill lacks verified trade identity/time';
                    END IF;
                    SELECT candidate.id INTO expected_snapshot_id
                    FROM clob_market_snapshots AS candidate
                    JOIN clob_book_batches AS source ON source.id = candidate.batch_id
                    WHERE candidate.market_id = NEW.market_id
                      AND candidate.complete AND candidate.eligible
                      AND source.first_seen_at BETWEEN
                          NEW.venue_filled_at
                              - make_interval(secs =>
                                    runtime_row.live_fill_snapshot_max_age_seconds)
                          AND NEW.venue_filled_at
                      AND CASE WHEN NEW.side::text = 'buy_yes'
                          THEN candidate.yes_token_id = NEW.token_id
                               AND candidate.yes_best_bid IS NOT NULL
                               AND candidate.yes_best_ask IS NOT NULL
                               AND candidate.yes_midpoint IS NOT NULL
                          ELSE candidate.no_token_id = NEW.token_id
                               AND candidate.no_best_bid IS NOT NULL
                               AND candidate.no_best_ask IS NOT NULL
                               AND candidate.no_midpoint IS NOT NULL
                          END
                    ORDER BY source.first_seen_at DESC, candidate.id DESC
                    LIMIT 1;
                    IF expected_snapshot_id IS DISTINCT FROM NEW.fill_clob_snapshot_id THEN
                        RAISE EXCEPTION 'eligible live fill did not cite latest pre-fill snapshot';
                    END IF;
                END IF;
            END IF;
            IF NEW.source_hash IS DISTINCT FROM expected_source_hash THEN
                RAISE EXCEPTION 'execution fill leg source hash mismatch';
            END IF;
            IF NEW.eligible
               AND (NOT snapshot_row.complete OR NOT snapshot_row.eligible) THEN
                RAISE EXCEPTION 'eligible execution fill cites ineligible CLOB evidence';
            END IF;

            NEW.record_hash := execution_cost_hash(jsonb_build_object(
                'record_kind', 'fill_leg',
                'definition_hash', NEW.definition_hash,
                'definition_version', NEW.definition_version,
                'eligibility_reason', NEW.eligibility_reason,
                'eligible', NEW.eligible,
                'external_leg_key', NEW.external_leg_key,
                'external_trade_id', NEW.external_trade_id,
                'fee_semantics', NEW.fee_semantics,
                'fee_usd', NEW.fee_usd,
                'fill_best_ask', NEW.fill_best_ask,
                'fill_best_bid', NEW.fill_best_bid,
                'fill_clob_snapshot_hash', NEW.fill_clob_snapshot_hash,
                'fill_clob_snapshot_id', NEW.fill_clob_snapshot_id,
                'fill_effective_at', NEW.fill_effective_at,
                'fill_midpoint', NEW.fill_midpoint,
                'first_seen_at', NEW.first_seen_at,
                'intent_id', NEW.intent_id,
                'leg_sequence', NEW.leg_sequence,
                'liquidity_role', NEW.liquidity_role,
                'market_id', NEW.market_id,
                'mode', NEW.mode::text,
                'notional_usd', NEW.notional_usd,
                'order_id', NEW.order_id,
                'price', NEW.price,
                'reconciliation_observation_id', NEW.reconciliation_observation_id,
                'shares', NEW.shares,
                'side', NEW.side::text,
                'signal_id', NEW.signal_id,
                'source_hash', NEW.source_hash,
                'source_kind', NEW.source_kind,
                'timestamp_semantics', NEW.timestamp_semantics,
                'token_id', NEW.token_id,
                'venue_filled_at', NEW.venue_filled_at
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_fill_legs_validate_insert "
        "BEFORE INSERT ON execution_fill_legs FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_fill_leg_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_obligation_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            intent_row execution_order_intents%ROWTYPE;
            order_row orders%ROWTYPE;
        BEGIN
            NEW.created_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT intent_row
            FROM execution_order_intents WHERE id = NEW.intent_id;
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
    )
    op.execute(
        "CREATE TRIGGER trg_execution_cost_obligations_validate_insert "
        "BEFORE INSERT ON execution_cost_obligations FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_obligation_insert()"
    )
    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_obligation_terminal_deferred()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE current_status text;
        BEGIN
            SELECT status INTO STRICT current_status FROM orders WHERE id = NEW.order_id;
            IF current_status IS DISTINCT FROM NEW.terminal_status THEN
                RAISE EXCEPTION 'execution cost obligation terminal status mismatch';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER "
        "trg_execution_cost_obligations_validate_terminal_deferred "
        "AFTER INSERT ON execution_cost_obligations DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION "
        "execution_cost_validate_obligation_terminal_deferred()"
    )
    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_obligation_update()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id IS DISTINCT FROM OLD.id
               OR NEW.order_id IS DISTINCT FROM OLD.order_id
               OR NEW.intent_id IS DISTINCT FROM OLD.intent_id
               OR NEW.terminal_status IS DISTINCT FROM OLD.terminal_status
               OR NEW.created_at IS DISTINCT FROM OLD.created_at
               OR NEW.definition_version IS DISTINCT FROM OLD.definition_version
               OR NEW.definition_hash IS DISTINCT FROM OLD.definition_hash THEN
                RAISE EXCEPTION 'execution cost obligation identity is immutable';
            END IF;
            IF OLD.failure_reason IS NOT NULL
               AND NEW.failure_reason IS DISTINCT FROM OLD.failure_reason THEN
                RAISE EXCEPTION 'execution cost obligation failure reason is stable';
            END IF;
            IF OLD.state = 'sealed' OR NOT (
                (OLD.state = 'pending'
                    AND NEW.state IN ('pending', 'waiting_for_marks', 'sealed'))
                OR (OLD.state = 'waiting_for_marks'
                    AND NEW.state IN ('waiting_for_marks', 'sealed'))
            ) THEN
                RAISE EXCEPTION 'invalid execution cost obligation state transition';
            END IF;
            IF NEW.state = 'sealed' THEN
                IF NOT EXISTS (
                    SELECT 1 FROM execution_cost_seals
                    WHERE obligation_id = OLD.id
                ) THEN
                    RAISE EXCEPTION 'sealed execution cost obligation requires an immutable seal';
                END IF;
                NEW.finalized_at := clock_timestamp();
                NEW.next_due_at := NULL;
            ELSIF NEW.next_due_at IS NULL OR NEW.finalized_at IS NOT NULL THEN
                RAISE EXCEPTION 'unsealed execution cost obligation requires next due time';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_cost_obligations_validate_update "
        "BEFORE UPDATE ON execution_cost_obligations FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_obligation_update()"
    )
    op.execute(
        "CREATE TRIGGER trg_execution_cost_obligations_no_delete "
        "BEFORE DELETE ON execution_cost_obligations FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_reject_mutation()"
    )
    op.execute(
        r"""
        CREATE FUNCTION execution_cost_require_terminal_obligation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.status IN (
                'filled', 'partially_filled', 'canceled', 'unfilled_maker',
                'submit_failed', 'rejected', 'expired'
            ) AND EXISTS (
                SELECT 1 FROM execution_order_intents WHERE order_id = NEW.id
            ) AND NOT EXISTS (
                SELECT 1 FROM execution_cost_obligations WHERE order_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'terminal 0038 order lacks execution cost obligation';
            END IF;
            RETURN NULL;
        END;
        $$
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER trg_orders_require_execution_cost_obligation "
        "AFTER INSERT OR UPDATE ON orders DEFERRABLE INITIALLY DEFERRED "
        "FOR EACH ROW EXECUTE FUNCTION execution_cost_require_terminal_obligation()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_mark_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            leg_row execution_fill_legs%ROWTYPE;
            snapshot_row clob_market_snapshots%ROWTYPE;
            batch_row clob_book_batches%ROWTYPE;
            expected_snapshot_id bigint;
            expected_token text;
            expected_bid numeric;
            expected_ask numeric;
            expected_mid numeric;
        BEGIN
            NEW.sealed_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT leg_row
            FROM execution_fill_legs WHERE id = NEW.fill_leg_id;
            IF leg_row.fill_effective_at IS NULL
               OR NEW.target_at IS DISTINCT FROM
                    leg_row.fill_effective_at + make_interval(secs => NEW.horizon_seconds)
               OR NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution post-fill mark target/definition mismatch';
            END IF;

            SELECT candidate.id INTO expected_snapshot_id
            FROM clob_market_snapshots AS candidate
            JOIN clob_book_batches AS source ON source.id = candidate.batch_id
            WHERE candidate.market_id = leg_row.market_id
              AND candidate.complete AND candidate.eligible
              AND source.first_seen_at BETWEEN NEW.target_at
                  AND NEW.target_at + make_interval(secs => runtime_row.mark_grace_seconds)
              AND CASE WHEN leg_row.side::text = 'buy_yes'
                  THEN candidate.yes_token_id = leg_row.token_id
                       AND candidate.yes_best_bid IS NOT NULL
                       AND candidate.yes_best_ask IS NOT NULL
                       AND candidate.yes_midpoint IS NOT NULL
                  ELSE candidate.no_token_id = leg_row.token_id
                       AND candidate.no_best_bid IS NOT NULL
                       AND candidate.no_best_ask IS NOT NULL
                       AND candidate.no_midpoint IS NOT NULL
                  END
            ORDER BY source.first_seen_at, candidate.id
            LIMIT 1;

            IF NEW.status = 'observed' THEN
                IF expected_snapshot_id IS DISTINCT FROM NEW.clob_snapshot_id THEN
                    RAISE EXCEPTION 'post-fill mark did not cite first eligible snapshot';
                END IF;
                SELECT * INTO STRICT snapshot_row
                FROM clob_market_snapshots WHERE id = NEW.clob_snapshot_id;
                SELECT * INTO STRICT batch_row
                FROM clob_book_batches WHERE id = snapshot_row.batch_id;
                IF leg_row.side::text = 'buy_yes' THEN
                    expected_token := snapshot_row.yes_token_id;
                    expected_bid := snapshot_row.yes_best_bid;
                    expected_ask := snapshot_row.yes_best_ask;
                    expected_mid := snapshot_row.yes_midpoint;
                ELSE
                    expected_token := snapshot_row.no_token_id;
                    expected_bid := snapshot_row.no_best_bid;
                    expected_ask := snapshot_row.no_best_ask;
                    expected_mid := snapshot_row.no_midpoint;
                END IF;
                IF expected_token IS DISTINCT FROM leg_row.token_id
                   OR NEW.clob_snapshot_hash IS DISTINCT FROM snapshot_row.record_hash
                   OR NEW.side_best_bid IS DISTINCT FROM expected_bid
                   OR NEW.side_best_ask IS DISTINCT FROM expected_ask
                   OR NEW.side_midpoint IS DISTINCT FROM expected_mid
                   OR NEW.observed_at IS DISTINCT FROM batch_row.first_seen_at
                   OR NEW.offset_seconds IS DISTINCT FROM
                        floor(extract(epoch FROM batch_row.first_seen_at - NEW.target_at))::integer
                   OR NEW.sealed_at < batch_row.first_seen_at THEN
                    RAISE EXCEPTION 'post-fill observed mark projection mismatch';
                END IF;
            ELSE
                IF NEW.sealed_at <
                        NEW.target_at + make_interval(secs => runtime_row.mark_grace_seconds)
                   OR expected_snapshot_id IS NOT NULL THEN
                    RAISE EXCEPTION 'post-fill mark cannot be sealed missing before grace/evidence';
                END IF;
            END IF;

            NEW.record_hash := execution_cost_hash(jsonb_build_object(
                'record_kind', 'mark',
                'clob_snapshot_hash', NEW.clob_snapshot_hash,
                'clob_snapshot_id', NEW.clob_snapshot_id,
                'definition_hash', NEW.definition_hash,
                'definition_version', NEW.definition_version,
                'fill_leg_id', NEW.fill_leg_id,
                'horizon_seconds', NEW.horizon_seconds,
                'missing_reason', NEW.missing_reason,
                'observed_at', NEW.observed_at,
                'offset_seconds', NEW.offset_seconds,
                'sealed_at', NEW.sealed_at,
                'side_best_ask', NEW.side_best_ask,
                'side_best_bid', NEW.side_best_bid,
                'side_midpoint', NEW.side_midpoint,
                'status', NEW.status,
                'target_at', NEW.target_at
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_postfill_marks_validate_insert "
        "BEFORE INSERT ON execution_postfill_marks FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_mark_insert()"
    )

    op.execute(
        r"""
        CREATE FUNCTION execution_cost_validate_seal_insert()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            runtime_row execution_cost_runtime%ROWTYPE;
            obligation_row execution_cost_obligations%ROWTYPE;
            intent_row execution_order_intents%ROWTYPE;
            order_row orders%ROWTYPE;
            position_row positions%ROWTYPE;
            position_found boolean := false;
            actual_leg_count integer;
            mark_eligible_leg_count integer;
            all_eligible boolean;
            missing_fee_count integer;
            missing_mid_count integer;
            actual_total_shares numeric;
            actual_notional numeric;
            actual_total_fee numeric;
            actual_vwap numeric;
            actual_fee_per_share numeric;
            actual_weighted_mid numeric;
            actual_latency numeric;
            actual_taker_spread numeric;
            actual_taker_slippage numeric;
            actual_maker_improvement numeric;
            expected_eligible boolean;
            expected_reason text;
            expected_role text;
            expected_expected_coverage text;
            mark_300_count integer;
            mark_300_observed integer;
            mark_1800_count integer;
            mark_1800_observed integer;
            expected_mark_300_coverage text;
            expected_mark_1800_coverage text;
            actual_adverse_300 numeric;
            actual_adverse_1800 numeric;
        BEGIN
            NEW.sealed_at := clock_timestamp();
            SELECT * INTO STRICT runtime_row FROM execution_cost_runtime WHERE id = 1;
            SELECT * INTO STRICT obligation_row
            FROM execution_cost_obligations WHERE id = NEW.obligation_id;
            SELECT * INTO STRICT intent_row
            FROM execution_order_intents WHERE id = NEW.intent_id;
            SELECT * INTO STRICT order_row FROM orders WHERE id = NEW.order_id;
            IF intent_row.mode::text = 'live' THEN
                SELECT * INTO position_row FROM positions WHERE order_id = NEW.order_id;
                position_found := FOUND;
            END IF;
            IF obligation_row.intent_id IS DISTINCT FROM NEW.intent_id
               OR obligation_row.order_id IS DISTINCT FROM NEW.order_id
               OR intent_row.order_id IS DISTINCT FROM NEW.order_id
               OR NOT (obligation_row.state = ANY(
                    ARRAY['pending', 'waiting_for_marks'])) THEN
                RAISE EXCEPTION 'execution cost seal identity/state mismatch';
            END IF;
            IF NEW.definition_version IS DISTINCT FROM runtime_row.definition_version
               OR NEW.definition_hash IS DISTINCT FROM runtime_row.definition_hash THEN
                RAISE EXCEPTION 'execution cost seal definition mismatch';
            END IF;

            SELECT
                count(*)::integer,
                coalesce(bool_and(leg.eligible), false),
                count(*) FILTER (WHERE leg.fee_usd IS NULL)::integer,
                count(*) FILTER (WHERE leg.fill_midpoint IS NULL)::integer,
                sum(leg.shares),
                sum(leg.notional_usd),
                CASE WHEN count(*) FILTER (WHERE leg.fee_usd IS NULL) = 0
                    THEN sum(leg.fee_usd) ELSE NULL END,
                sum(leg.shares * leg.price) / nullif(sum(leg.shares), 0),
                CASE WHEN count(*) FILTER (WHERE leg.fee_usd IS NULL) = 0
                    THEN sum(leg.fee_usd) / nullif(sum(leg.shares), 0) ELSE NULL END,
                CASE WHEN count(*) FILTER (WHERE leg.fill_midpoint IS NULL) = 0
                    THEN sum(leg.shares * leg.fill_midpoint) / nullif(sum(leg.shares), 0)
                    ELSE NULL END,
                CASE WHEN count(*) FILTER (WHERE leg.fill_midpoint IS NULL) = 0
                    THEN sum(leg.shares * (leg.fill_midpoint - intent_row.side_midpoint))
                         / nullif(sum(leg.shares), 0) ELSE NULL END,
                CASE WHEN count(*) FILTER (
                        WHERE leg.liquidity_role = 'taker'
                          AND (leg.fill_best_ask IS NULL OR leg.fill_midpoint IS NULL)) = 0
                    THEN sum(CASE WHEN leg.liquidity_role = 'taker'
                        THEN leg.shares * (leg.fill_best_ask - leg.fill_midpoint)
                        ELSE 0 END) / nullif(sum(leg.shares), 0) ELSE NULL END,
                CASE WHEN count(*) FILTER (
                        WHERE leg.liquidity_role = 'taker'
                          AND leg.fill_best_ask IS NULL) = 0
                    THEN sum(CASE WHEN leg.liquidity_role = 'taker'
                        THEN leg.shares * (leg.price - leg.fill_best_ask)
                        ELSE 0 END) / nullif(sum(leg.shares), 0) ELSE NULL END,
                CASE WHEN count(*) FILTER (
                        WHERE leg.liquidity_role = 'maker'
                          AND leg.fill_midpoint IS NULL) = 0
                    THEN sum(CASE WHEN leg.liquidity_role = 'maker'
                        THEN leg.shares * (leg.fill_midpoint - leg.price)
                        ELSE 0 END) / nullif(sum(leg.shares), 0) ELSE NULL END
            INTO actual_leg_count, all_eligible, missing_fee_count, missing_mid_count,
                 actual_total_shares, actual_notional, actual_total_fee, actual_vwap,
                 actual_fee_per_share, actual_weighted_mid, actual_latency,
                 actual_taker_spread, actual_taker_slippage, actual_maker_improvement
            FROM execution_fill_legs AS leg
            WHERE leg.intent_id = NEW.intent_id;

            SELECT count(*)::integer INTO mark_eligible_leg_count
            FROM execution_fill_legs
            WHERE intent_id = NEW.intent_id AND fill_effective_at IS NOT NULL;

            IF actual_leg_count = 0 THEN
                expected_role := 'unknown';
                expected_eligible := false;
                expected_reason := 'no_fill';
            ELSE
                SELECT CASE
                    WHEN bool_or(liquidity_role = 'unknown') THEN 'unknown'
                    WHEN count(DISTINCT liquidity_role) = 1 THEN min(liquidity_role)
                    ELSE 'mixed'
                END
                INTO expected_role
                FROM execution_fill_legs WHERE intent_id = NEW.intent_id;
                expected_eligible := all_eligible AND missing_fee_count = 0;
                IF expected_eligible THEN
                    expected_reason := 'eligible';
                ELSE
                    SELECT eligibility_reason INTO expected_reason
                    FROM execution_fill_legs
                    WHERE intent_id = NEW.intent_id AND NOT eligible
                    ORDER BY leg_sequence LIMIT 1;
                    expected_reason := coalesce(expected_reason, 'fee_unknown');
                END IF;
            END IF;

            IF NEW.leg_count IS DISTINCT FROM actual_leg_count
               OR NEW.aggregate_role IS DISTINCT FROM expected_role
               OR NEW.eligible IS DISTINCT FROM expected_eligible
               OR NEW.eligibility_reason IS DISTINCT FROM expected_reason
               OR NEW.intent_midpoint IS DISTINCT FROM intent_row.side_midpoint
               OR NEW.side_model_probability IS DISTINCT FROM
                    intent_row.side_model_probability THEN
                RAISE EXCEPTION 'execution cost seal aggregate identity mismatch';
            END IF;
            IF NEW.total_shares IS DISTINCT FROM actual_total_shares
               OR NEW.filled_notional_usd IS DISTINCT FROM actual_notional
               OR (actual_vwap IS NULL AND NEW.vwap_fill_price IS NOT NULL)
               OR (actual_vwap IS NOT NULL
                   AND abs(NEW.vwap_fill_price - actual_vwap) > 0.000000005)
               OR NEW.total_fee_usd IS DISTINCT FROM actual_total_fee
               OR (actual_fee_per_share IS NULL AND NEW.fee_per_share IS NOT NULL)
               OR (actual_fee_per_share IS NOT NULL
                   AND abs(NEW.fee_per_share - actual_fee_per_share) > 0.000000005)
               OR (actual_weighted_mid IS NULL AND NEW.weighted_fill_midpoint IS NOT NULL)
               OR (actual_weighted_mid IS NOT NULL
                   AND abs(NEW.weighted_fill_midpoint - actual_weighted_mid) > 0.000000005)
               THEN
                RAISE EXCEPTION 'execution cost seal fill reconciliation mismatch';
            END IF;
            IF actual_leg_count = 0 THEN
                IF order_row.filled_usd <> 0 THEN
                    RAISE EXCEPTION 'no-fill execution seal disagrees with terminal order';
                END IF;
            ELSE
                IF abs(actual_notional - order_row.filled_usd) > 0.0001
                   OR (actual_total_fee IS NULL AND order_row.fee_usd IS NOT NULL)
                   OR (actual_total_fee IS NOT NULL AND
                       (order_row.fee_usd IS NULL
                        OR abs(actual_total_fee - order_row.fee_usd) > 0.00001)) THEN
                    RAISE EXCEPTION 'execution cost seal disagrees with terminal order totals';
                END IF;
                IF position_found AND (
                    abs(actual_total_shares - position_row.size_shares) > 0.0000005
                    OR abs(actual_notional - position_row.size_usd) > 0.0001
                    OR abs(actual_vwap - position_row.avg_price) > 0.0000005
                    OR (actual_total_fee IS NULL AND position_row.fee_usd IS NOT NULL)
                    OR (actual_total_fee IS NOT NULL AND
                        (position_row.fee_usd IS NULL
                         OR abs(actual_total_fee - position_row.fee_usd) > 0.00001))
                ) THEN
                    RAISE EXCEPTION 'execution cost seal disagrees with live position totals';
                END IF;
            END IF;

            IF expected_eligible THEN
                IF abs(NEW.gross_model_edge
                        - (intent_row.side_model_probability - intent_row.side_midpoint))
                        > 0.000000005
                   OR abs(NEW.latency_movement - actual_latency) > 0.000000005
                   OR abs(NEW.taker_spread - actual_taker_spread) > 0.000000005
                   OR abs(NEW.taker_slippage - actual_taker_slippage) > 0.000000005
                   OR abs(NEW.maker_improvement - actual_maker_improvement) > 0.000000005
                   OR abs(NEW.fee_drag - actual_fee_per_share) > 0.000000005
                   OR abs(NEW.realized_net_edge
                        - (intent_row.side_model_probability - actual_vwap
                           - actual_fee_per_share)) > 0.000000005 THEN
                    RAISE EXCEPTION 'execution cost seal algebra mismatch';
                END IF;
            ELSIF NEW.gross_model_edge IS NOT NULL
               OR NEW.latency_movement IS NOT NULL
               OR NEW.taker_spread IS NOT NULL
               OR NEW.taker_slippage IS NOT NULL
               OR NEW.maker_improvement IS NOT NULL
               OR NEW.fee_drag IS NOT NULL
               OR NEW.realized_net_edge IS NOT NULL THEN
                RAISE EXCEPTION 'ineligible execution seal cannot claim realized cost metrics';
            END IF;

            IF intent_row.expected_slippage_per_share IS NOT NULL THEN
                expected_expected_coverage := 'modeled';
            ELSIF intent_row.expected_slippage_unavailable_reason
                    = 'expected_slippage_not_modeled' THEN
                expected_expected_coverage := 'not_modeled';
            ELSE
                expected_expected_coverage := 'unavailable';
            END IF;
            IF NEW.expected_slippage_coverage IS DISTINCT FROM expected_expected_coverage
               OR NEW.expected_slippage_per_share IS DISTINCT FROM
                    intent_row.expected_slippage_per_share THEN
                RAISE EXCEPTION 'execution cost seal expected slippage mismatch';
            END IF;

            SELECT
                count(mark.id)::integer,
                count(mark.id) FILTER (WHERE mark.status = 'observed')::integer,
                CASE WHEN count(mark.id) = actual_leg_count
                           AND count(mark.id) FILTER (WHERE mark.status = 'observed')
                               = actual_leg_count
                           AND missing_mid_count = 0
                     THEN sum(leg.shares * (leg.fill_midpoint - mark.side_midpoint))
                          / nullif(sum(leg.shares), 0) ELSE NULL END
            INTO mark_300_count, mark_300_observed, actual_adverse_300
            FROM execution_fill_legs AS leg
            LEFT JOIN execution_postfill_marks AS mark
              ON mark.fill_leg_id = leg.id AND mark.horizon_seconds = 300
            WHERE leg.intent_id = NEW.intent_id AND leg.fill_effective_at IS NOT NULL;
            SELECT
                count(mark.id)::integer,
                count(mark.id) FILTER (WHERE mark.status = 'observed')::integer,
                CASE WHEN count(mark.id) = actual_leg_count
                           AND count(mark.id) FILTER (WHERE mark.status = 'observed')
                               = actual_leg_count
                           AND missing_mid_count = 0
                     THEN sum(leg.shares * (leg.fill_midpoint - mark.side_midpoint))
                          / nullif(sum(leg.shares), 0) ELSE NULL END
            INTO mark_1800_count, mark_1800_observed, actual_adverse_1800
            FROM execution_fill_legs AS leg
            LEFT JOIN execution_postfill_marks AS mark
              ON mark.fill_leg_id = leg.id AND mark.horizon_seconds = 1800
            WHERE leg.intent_id = NEW.intent_id AND leg.fill_effective_at IS NOT NULL;

            IF actual_leg_count = 0 THEN
                expected_mark_300_coverage := 'not_applicable';
                expected_mark_1800_coverage := 'not_applicable';
            ELSIF mark_eligible_leg_count = 0 THEN
                expected_mark_300_coverage := 'not_applicable';
                expected_mark_1800_coverage := 'not_applicable';
            ELSE
                IF mark_300_count <> mark_eligible_leg_count
                   OR mark_1800_count <> mark_eligible_leg_count THEN
                    RAISE EXCEPTION 'execution cost seal requires every frozen post-fill mark';
                END IF;
                expected_mark_300_coverage := CASE
                    WHEN mark_eligible_leg_count = actual_leg_count
                         AND mark_300_observed = actual_leg_count AND missing_mid_count = 0
                    THEN 'complete' ELSE 'missing' END;
                expected_mark_1800_coverage := CASE
                    WHEN mark_eligible_leg_count = actual_leg_count
                         AND mark_1800_observed = actual_leg_count AND missing_mid_count = 0
                    THEN 'complete' ELSE 'missing' END;
            END IF;
            IF NEW.markout_300s_coverage IS DISTINCT FROM expected_mark_300_coverage
               OR NEW.markout_1800s_coverage IS DISTINCT FROM expected_mark_1800_coverage
               OR (actual_adverse_300 IS NULL AND NEW.adverse_selection_300s IS NOT NULL)
               OR (actual_adverse_300 IS NOT NULL
                   AND abs(NEW.adverse_selection_300s - actual_adverse_300) > 0.000000005)
               OR (actual_adverse_1800 IS NULL AND NEW.adverse_selection_1800s IS NOT NULL)
               OR (actual_adverse_1800 IS NOT NULL
                   AND abs(NEW.adverse_selection_1800s - actual_adverse_1800) > 0.000000005)
               THEN
                RAISE EXCEPTION 'execution cost seal post-fill markout mismatch';
            END IF;

            NEW.record_hash := execution_cost_hash(jsonb_build_object(
                'record_kind', 'seal',
                'adverse_selection_1800s', NEW.adverse_selection_1800s,
                'adverse_selection_300s', NEW.adverse_selection_300s,
                'aggregate_role', NEW.aggregate_role,
                'definition_hash', NEW.definition_hash,
                'definition_version', NEW.definition_version,
                'eligibility_reason', NEW.eligibility_reason,
                'eligible', NEW.eligible,
                'expected_slippage_coverage', NEW.expected_slippage_coverage,
                'expected_slippage_per_share', NEW.expected_slippage_per_share,
                'fee_drag', NEW.fee_drag,
                'fee_per_share', NEW.fee_per_share,
                'filled_notional_usd', NEW.filled_notional_usd,
                'gross_model_edge', NEW.gross_model_edge,
                'intent_id', NEW.intent_id,
                'intent_midpoint', NEW.intent_midpoint,
                'latency_movement', NEW.latency_movement,
                'leg_count', NEW.leg_count,
                'maker_improvement', NEW.maker_improvement,
                'markout_1800s_coverage', NEW.markout_1800s_coverage,
                'markout_300s_coverage', NEW.markout_300s_coverage,
                'obligation_id', NEW.obligation_id,
                'order_id', NEW.order_id,
                'realized_net_edge', NEW.realized_net_edge,
                'reason_detail', NEW.reason_detail,
                'sealed_at', NEW.sealed_at,
                'side_model_probability', NEW.side_model_probability,
                'taker_slippage', NEW.taker_slippage,
                'taker_spread', NEW.taker_spread,
                'total_fee_usd', NEW.total_fee_usd,
                'total_shares', NEW.total_shares,
                'vwap_fill_price', NEW.vwap_fill_price,
                'weighted_fill_midpoint', NEW.weighted_fill_midpoint
            ));
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_execution_cost_seals_validate_insert "
        "BEFORE INSERT ON execution_cost_seals FOR EACH ROW "
        "EXECUTE FUNCTION execution_cost_validate_seal_insert()"
    )

    for table_name in (
        "execution_order_intents",
        "execution_reconciliation_observations",
        "execution_fill_legs",
        "execution_postfill_marks",
        "execution_cost_seals",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table_name}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table_name} FOR EACH ROW "
            "EXECUTE FUNCTION execution_cost_reject_mutation()"
        )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            r"""
            DO $$
            BEGIN
                IF EXISTS (SELECT 1 FROM execution_order_intents)
                   OR EXISTS (SELECT 1 FROM execution_reconciliation_observations)
                   OR EXISTS (SELECT 1 FROM execution_fill_legs)
                   OR EXISTS (SELECT 1 FROM execution_cost_obligations)
                   OR EXISTS (SELECT 1 FROM execution_postfill_marks)
                   OR EXISTS (SELECT 1 FROM execution_cost_seals) THEN
                    RAISE EXCEPTION USING
                        MESSAGE = 'refusing 0038 downgrade: execution cost evidence exists',
                        HINT = 'export and explicitly reset execution-cost evidence first';
                END IF;
            END
            $$;
            """
        )
    )
    op.execute("DROP TRIGGER trg_execution_cost_seals_append_only ON execution_cost_seals")
    op.execute("DROP TRIGGER trg_execution_postfill_marks_append_only ON execution_postfill_marks")
    op.execute("DROP TRIGGER trg_execution_fill_legs_append_only ON execution_fill_legs")
    op.execute(
        "DROP TRIGGER trg_execution_reconciliation_observations_append_only "
        "ON execution_reconciliation_observations"
    )
    op.execute("DROP TRIGGER trg_execution_order_intents_append_only ON execution_order_intents")
    op.execute("DROP TRIGGER trg_execution_cost_seals_validate_insert ON execution_cost_seals")
    op.execute("DROP TRIGGER trg_orders_require_execution_cost_obligation ON orders")
    op.execute(
        "DROP TRIGGER trg_execution_postfill_marks_validate_insert ON execution_postfill_marks"
    )
    op.execute(
        "DROP TRIGGER trg_execution_cost_obligations_no_delete "
        "ON execution_cost_obligations"
    )
    op.execute(
        "DROP TRIGGER trg_execution_cost_obligations_validate_update "
        "ON execution_cost_obligations"
    )
    op.execute(
        "DROP TRIGGER trg_execution_cost_obligations_validate_terminal_deferred "
        "ON execution_cost_obligations"
    )
    op.execute(
        "DROP TRIGGER trg_execution_cost_obligations_validate_insert "
        "ON execution_cost_obligations"
    )
    op.execute("DROP TRIGGER trg_execution_fill_legs_validate_insert ON execution_fill_legs")
    op.execute(
        "DROP TRIGGER trg_execution_reconciliation_observations_validate_insert "
        "ON execution_reconciliation_observations"
    )
    op.execute(
        "DROP TRIGGER trg_execution_order_intents_validate_insert "
        "ON execution_order_intents"
    )
    op.execute("DROP TRIGGER trg_execution_cost_runtime_immutable ON execution_cost_runtime")
    op.execute("DROP FUNCTION execution_cost_validate_seal_insert()")
    op.execute("DROP FUNCTION execution_cost_validate_mark_insert()")
    op.execute("DROP FUNCTION execution_cost_require_terminal_obligation()")
    op.execute("DROP FUNCTION execution_cost_validate_obligation_update()")
    op.execute("DROP FUNCTION execution_cost_validate_obligation_terminal_deferred()")
    op.execute("DROP FUNCTION execution_cost_validate_obligation_insert()")
    op.execute("DROP FUNCTION execution_cost_validate_fill_leg_insert()")
    op.execute("DROP FUNCTION execution_cost_validate_observation_insert()")
    op.execute("DROP FUNCTION execution_cost_validate_intent_insert()")
    op.execute("DROP FUNCTION execution_cost_validate_runtime_mutation()")
    op.execute("DROP FUNCTION execution_cost_reject_mutation()")
    op.execute("DROP FUNCTION execution_cost_hash(jsonb)")
    op.drop_index("ix_execution_cost_seals_mode_scope", table_name="execution_cost_seals")
    op.drop_table("execution_cost_seals")
    op.drop_index("ix_execution_postfill_marks_target", table_name="execution_postfill_marks")
    op.drop_table("execution_postfill_marks")
    op.drop_index("ix_execution_cost_obligations_due", table_name="execution_cost_obligations")
    op.drop_table("execution_cost_obligations")
    op.drop_index("ix_execution_fill_legs_intent", table_name="execution_fill_legs")
    op.drop_index("ix_execution_fill_legs_market_effective", table_name="execution_fill_legs")
    op.drop_table("execution_fill_legs")
    op.drop_index(
        "ix_execution_reconciliation_observations_order_seen",
        table_name="execution_reconciliation_observations",
    )
    op.drop_table("execution_reconciliation_observations")
    op.drop_index("ix_execution_order_intents_mode_time", table_name="execution_order_intents")
    op.drop_index("ix_execution_order_intents_market_time", table_name="execution_order_intents")
    op.drop_table("execution_order_intents")
    op.drop_table("execution_cost_runtime")
