from sqlalchemy.dialects import postgresql

from stratus.jobs.backfill_fees import (
    _legacy_fee_backfill_orders_statement,
    _legacy_fee_backfill_positions_statement,
)


def test_fee_backfill_excludes_orders_with_modern_execution_intents() -> None:
    sql = str(
        _legacy_fee_backfill_orders_statement().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "LEFT OUTER JOIN execution_order_intents" in sql
    assert "execution_order_intents.id IS NULL" in sql
    assert "orders.fee_usd IS NULL" in sql


def test_position_fee_backfill_requires_positive_pre_ledger_proof() -> None:
    sql = str(
        _legacy_fee_backfill_positions_statement().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "orders.filled_at < (SELECT execution_cost_runtime.opened_at" in sql
    assert "orders.filled_at >= (SELECT execution_cost_runtime.opened_at" in sql
    assert sql.count("orders.filled_at IS NOT NULL") == 2
    assert "orders.status IN ('filled', 'partially_filled')" in sql
    assert "orders.filled_usd > 0" in sql
    assert "execution_order_intents" in sql
    assert "execution_order_intents.token_id IS NOT DISTINCT FROM positions.token_id" in sql
    assert sql.count("NOT (EXISTS") == 2
