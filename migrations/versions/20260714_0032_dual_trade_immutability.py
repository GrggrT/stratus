"""Seal prospective dual-token trade and decision rows after use.

Revision ID: 20260714_0032
Revises: 20260714_0031
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260714_0032"
down_revision: str | None = "20260714_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        r"""
        CREATE FUNCTION guard_dual_model_prob_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.market_anchor_protocol = 'clob-dual-v1' THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'dual CLOB model probability cannot be deleted';
                END IF;
                IF NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'dual CLOB model probability is immutable';
                END IF;
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
        """
        CREATE TRIGGER trg_model_probs_guard_dual_immutability
        BEFORE UPDATE OR DELETE ON model_probs
        FOR EACH ROW EXECUTE FUNCTION guard_dual_model_prob_immutability()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION guard_dual_signal_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.benchmark_protocol = 'clob-dual-v1' THEN
                IF TG_OP = 'DELETE' THEN
                    IF EXISTS (SELECT 1 FROM orders WHERE signal_id = OLD.id) THEN
                        RAISE EXCEPTION
                            'dual CLOB signal with an order cannot be deleted';
                    END IF;
                    IF OLD.status::text NOT IN ('skipped', 'expired') THEN
                        RAISE EXCEPTION
                            'dual CLOB signal is not retention-eligible for deletion';
                    END IF;
                    RETURN OLD;
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.ts IS DISTINCT FROM OLD.ts
                   OR NEW.side IS DISTINCT FROM OLD.side
                   OR NEW.model_prob IS DISTINCT FROM OLD.model_prob
                   OR NEW.market_price IS DISTINCT FROM OLD.market_price
                   OR NEW.edge IS DISTINCT FROM OLD.edge
                   OR NEW.kelly_frac IS DISTINCT FROM OLD.kelly_frac
                   OR NEW.size_usd IS DISTINCT FROM OLD.size_usd
                   OR NEW.confidence IS DISTINCT FROM OLD.confidence
                   OR NEW.clob_snapshot_id IS DISTINCT FROM OLD.clob_snapshot_id
                   OR NEW.token_id IS DISTINCT FROM OLD.token_id
                   OR NEW.benchmark_protocol IS DISTINCT FROM OLD.benchmark_protocol THEN
                    RAISE EXCEPTION 'dual CLOB signal decision/provenance is immutable';
                END IF;
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
        """
        CREATE TRIGGER trg_signals_guard_dual_immutability
        BEFORE UPDATE OR DELETE ON signals
        FOR EACH ROW EXECUTE FUNCTION guard_dual_signal_immutability()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION guard_dual_order_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            old_protocol text;
            new_protocol text;
        BEGIN
            SELECT benchmark_protocol INTO old_protocol
            FROM signals WHERE id = OLD.signal_id;
            IF TG_OP = 'DELETE' THEN
                IF old_protocol = 'clob-dual-v1' THEN
                    RAISE EXCEPTION 'dual CLOB order cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;

            SELECT benchmark_protocol INTO new_protocol
            FROM signals WHERE id = NEW.signal_id;
            IF old_protocol = 'clob-dual-v1' OR new_protocol = 'clob-dual-v1' THEN
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.signal_id IS DISTINCT FROM OLD.signal_id
                   OR NEW.mode IS DISTINCT FROM OLD.mode
                   OR NEW.token_id IS DISTINCT FROM OLD.token_id
                   OR NEW.side IS DISTINCT FROM OLD.side
                   OR NEW.price IS DISTINCT FROM OLD.price
                   OR NEW.size_usd IS DISTINCT FROM OLD.size_usd
                   OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                    RAISE EXCEPTION 'dual CLOB order identity/decision is immutable';
                END IF;
            END IF;
            IF old_protocol = 'clob-dual-v1'
               AND OLD.status IN ('filled', 'partially_filled')
               AND (
                    NEW.status IS DISTINCT FROM OLD.status
                    OR NEW.filled_usd IS DISTINCT FROM OLD.filled_usd
                    OR NEW.fee_usd IS DISTINCT FROM OLD.fee_usd
                    OR NEW.filled_at IS DISTINCT FROM OLD.filled_at
                    OR NEW.clob_order_id IS DISTINCT FROM OLD.clob_order_id
               ) THEN
                RAISE EXCEPTION 'terminal dual CLOB order outcome is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_orders_guard_dual_immutability
        BEFORE UPDATE OR DELETE ON orders
        FOR EACH ROW EXECUTE FUNCTION guard_dual_order_immutability()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION guard_dual_position_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.benchmark_protocol = 'clob-dual-v1' THEN
                IF TG_OP = 'DELETE' THEN
                    RAISE EXCEPTION 'dual CLOB position cannot be deleted';
                END IF;
                IF NEW.id IS DISTINCT FROM OLD.id
                   OR NEW.market_id IS DISTINCT FROM OLD.market_id
                   OR NEW.order_id IS DISTINCT FROM OLD.order_id
                   OR NEW.mode IS DISTINCT FROM OLD.mode
                   OR NEW.side IS DISTINCT FROM OLD.side
                   OR NEW.token_id IS DISTINCT FROM OLD.token_id
                   OR NEW.benchmark_protocol IS DISTINCT FROM OLD.benchmark_protocol THEN
                    RAISE EXCEPTION 'dual CLOB position identity/provenance is immutable';
                END IF;
                IF OLD.status::text = 'closed' AND NEW IS DISTINCT FROM OLD THEN
                    RAISE EXCEPTION 'closed dual CLOB position is immutable';
                END IF;
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
        """
        CREATE TRIGGER trg_positions_guard_dual_immutability
        BEFORE UPDATE OR DELETE ON positions
        FOR EACH ROW EXECUTE FUNCTION guard_dual_position_immutability()
        """
    )

    op.execute(
        r"""
        CREATE FUNCTION guard_dual_selection_policy_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            old_protocol text;
            new_protocol text;
        BEGIN
            SELECT signal.benchmark_protocol INTO old_protocol
            FROM orders AS order_row
            JOIN signals AS signal ON signal.id = order_row.signal_id
            WHERE order_row.id = OLD.order_id;
            IF TG_OP = 'DELETE' THEN
                IF old_protocol = 'clob-dual-v1' THEN
                    RAISE EXCEPTION 'dual CLOB selection-policy decision cannot be deleted';
                END IF;
                RETURN OLD;
            END IF;
            SELECT signal.benchmark_protocol INTO new_protocol
            FROM orders AS order_row
            JOIN signals AS signal ON signal.id = order_row.signal_id
            WHERE order_row.id = NEW.order_id;
            IF old_protocol = 'clob-dual-v1' OR new_protocol = 'clob-dual-v1' THEN
                RAISE EXCEPTION 'dual CLOB selection-policy decision is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_selection_policy_guard_dual_immutability
        BEFORE UPDATE OR DELETE ON selection_policy_decisions
        FOR EACH ROW EXECUTE FUNCTION guard_dual_selection_policy_immutability()
        """
    )


def downgrade() -> None:
    # Once prospective evidence exists, removing the guards would silently
    # turn an immutable ledger back into mutable application tables.
    op.execute(
        r"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM model_probs
                WHERE market_anchor_protocol = 'clob-dual-v1'
            ) OR EXISTS (
                SELECT 1 FROM signals
                WHERE benchmark_protocol = 'clob-dual-v1'
            ) OR EXISTS (
                SELECT 1 FROM positions
                WHERE benchmark_protocol = 'clob-dual-v1'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade dual trade immutability after prospective evidence';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        "DROP TRIGGER trg_selection_policy_guard_dual_immutability "
        "ON selection_policy_decisions"
    )
    op.execute("DROP FUNCTION guard_dual_selection_policy_immutability()")
    op.execute("DROP TRIGGER trg_positions_guard_dual_immutability ON positions")
    op.execute("DROP FUNCTION guard_dual_position_immutability()")
    op.execute("DROP TRIGGER trg_orders_guard_dual_immutability ON orders")
    op.execute("DROP FUNCTION guard_dual_order_immutability()")
    op.execute("DROP TRIGGER trg_signals_guard_dual_immutability ON signals")
    op.execute("DROP FUNCTION guard_dual_signal_immutability()")
    op.execute("DROP TRIGGER trg_model_probs_guard_dual_immutability ON model_probs")
    op.execute("DROP FUNCTION guard_dual_model_prob_immutability()")
