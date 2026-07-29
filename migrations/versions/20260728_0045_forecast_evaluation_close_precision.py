"""Align forecast-evaluation close provenance with its stored precision.

Revision ID: 20260728_0045
Revises: 20260719_0044
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260728_0045"
down_revision: str | None = "20260719_0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EXACT_COMPARISON = (
    "OR NEW.pre_resolution_close IS DISTINCT FROM close_probability THEN"
)
_ROUNDED_COMPARISON = (
    "OR NEW.pre_resolution_close IS DISTINCT FROM round(close_probability, 6) THEN"
)


def _replace_comparison(*, old: str, new: str) -> None:
    # Preserve the complete, already-audited 0031 trigger body and replace
    # only the comparison whose source and destination columns intentionally
    # have different scales (NUMERIC(10,8) -> NUMERIC(8,6)).  The guarded
    # replacement fails closed if the expected predecessor is not installed.
    op.execute(
        rf"""
        DO $migration$
        DECLARE
            function_sql text;
        BEGIN
            SELECT pg_get_functiondef(
                'public.validate_forecast_evaluation_clob_provenance()'::regprocedure
            )
            INTO function_sql;

            IF function_sql IS NULL
               OR strpos(function_sql, {old!r}) = 0
               OR strpos(function_sql, {new!r}) > 0 THEN
                RAISE EXCEPTION
                    'forecast evaluation provenance trigger predecessor is unexpected';
            END IF;

            function_sql := replace(function_sql, {old!r}, {new!r});
            EXECUTE function_sql;
        END
        $migration$
        """
    )


def upgrade() -> None:
    _replace_comparison(old=_EXACT_COMPARISON, new=_ROUNDED_COMPARISON)


def downgrade() -> None:
    _replace_comparison(old=_ROUNDED_COMPARISON, new=_EXACT_COMPARISON)
