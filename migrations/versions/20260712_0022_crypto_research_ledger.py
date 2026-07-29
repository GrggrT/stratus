"""Add immutable Binance strategy-research ledger.

Revision ID: 20260712_0022
Revises: 20260712_0021
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260712_0022"
down_revision: str | None = "20260712_0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "crypto_research_experiments",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("experiment_key", sa.String(length=64), nullable=False, unique=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="registered"),
        sa.Column("symbols", postgresql.JSONB(), nullable=False),
        sa.Column("interval", sa.String(length=8), nullable=False),
        sa.Column("dataset_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("dataset_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holdout_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("holdout_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.Column("candidate_set_hash", sa.String(length=64), nullable=False),
        sa.Column("execution_config", postgresql.JSONB(), nullable=False),
        sa.Column("execution_config_hash", sa.String(length=64), nullable=False),
        sa.Column("selection_policy", postgresql.JSONB(), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("protocol_version", sa.String(length=64), nullable=False),
        sa.Column("selected_candidate_key", sa.String(length=64), nullable=True),
        sa.Column("holdout_unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column(
            "decision_reasons",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('registered', 'oos_running', 'oos_complete', "
            "'holdout_unlocked', 'holdout_running', 'complete', 'failed')",
            name="ck_crypto_research_experiment_status",
        ),
        sa.CheckConstraint(
            "dataset_start < dataset_end AND holdout_start < holdout_end",
            name="ck_crypto_research_experiment_windows",
        ),
        sa.CheckConstraint(
            "decision IS NULL OR decision IN ('GO', 'NO_GO')",
            name="ck_crypto_research_experiment_decision",
        ),
    )
    op.create_index(
        "ix_crypto_research_experiments_status",
        "crypto_research_experiments",
        ["status", "created_at"],
    )

    op.create_table(
        "crypto_research_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_research_experiments.id"),
            nullable=False,
        ),
        sa.Column("candidate_key", sa.String(length=64), nullable=False),
        sa.Column("strategy_name", sa.String(length=64), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=False),
        sa.Column("parameters", postgresql.JSONB(), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "candidate_key",
            name="uq_crypto_research_candidates_identity",
        ),
    )
    op.create_index(
        "ix_crypto_research_candidates_experiment",
        "crypto_research_candidates",
        ["experiment_id", "candidate_key"],
    )

    op.create_table(
        "crypto_research_results",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "experiment_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_research_experiments.id"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_research_candidates.id"),
            nullable=False,
        ),
        sa.Column(
            "symbol_id",
            sa.BigInteger(),
            sa.ForeignKey("crypto_symbols.id"),
            nullable=False,
        ),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("fold_number", sa.SmallInteger(), nullable=False),
        sa.Column("stress_case", sa.String(length=24), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.Column("data_hash", sa.String(length=64), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.Column("cycle_net_pnls", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "phase IN ('oos', 'holdout')",
            name="ck_crypto_research_result_phase",
        ),
        sa.CheckConstraint(
            "stress_case IN ('baseline', 'double_slippage')",
            name="ck_crypto_research_result_stress",
        ),
        sa.CheckConstraint(
            "fold_number >= 0 AND window_start < window_end",
            name="ck_crypto_research_result_window",
        ),
        sa.UniqueConstraint(
            "experiment_id",
            "candidate_id",
            "symbol_id",
            "phase",
            "fold_number",
            "stress_case",
            name="uq_crypto_research_results_identity",
        ),
    )
    op.create_index(
        "ix_crypto_research_results_lookup",
        "crypto_research_results",
        ["experiment_id", "phase", "fold_number"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_crypto_research_ledger_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name in ("crypto_research_candidates", "crypto_research_results"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_crypto_research_ledger_mutation()
            """
        )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_crypto_research_results_append_only ON crypto_research_results")
    op.execute(
        "DROP TRIGGER trg_crypto_research_candidates_append_only ON crypto_research_candidates"
    )
    op.drop_index(
        "ix_crypto_research_results_lookup",
        table_name="crypto_research_results",
    )
    op.drop_table("crypto_research_results")
    op.drop_index(
        "ix_crypto_research_candidates_experiment",
        table_name="crypto_research_candidates",
    )
    op.drop_table("crypto_research_candidates")
    op.drop_index(
        "ix_crypto_research_experiments_status",
        table_name="crypto_research_experiments",
    )
    op.drop_table("crypto_research_experiments")
    op.execute("DROP FUNCTION reject_crypto_research_ledger_mutation()")
