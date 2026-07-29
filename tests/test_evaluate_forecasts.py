from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from stratus.db.models import GammaOutcomeEvent, Market, Station, WeatherKind
from stratus.jobs.evaluate_forecasts import (
    SelectedProbability,
    _close_snapshot_statement,
    _evaluation_values,
    _selected_probability_statement,
    _target_batch_snapshot_statement,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.forecast_evaluation import DualPricePoint, DualPriceTimeline

NOW = datetime(2026, 7, 28, 10, tzinfo=UTC)


def _postgres_sql(statement: object) -> str:
    return str(
        statement.compile(  # type: ignore[attr-defined]
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    ).lower()


def test_selection_is_bounded_ranked_anti_joined_and_narrow() -> None:
    statement = _selected_probability_statement(
        market_ids=[11, 12],
        cutoff=NOW - timedelta(days=365),
        now=NOW,
        limit=7,
    )

    assert tuple(statement.selected_columns.keys()) == (
        "model_prob_id",
        "market_id",
        "decision_at",
        "probability",
        "horizon_h",
        "market_snapshot_id",
        "horizon_bucket",
    )
    sql = _postgres_sql(statement)
    assert "model_probs.components" not in sql
    assert "row_number() over (partition by model_probs.market_id" in sql
    assert all(bucket in sql for bucket in ("0-6h", "6-24h", "24-48h", "48h+"))
    assert "forecast_evaluations.model_prob_id = anon_1.model_prob_id" in sql
    assert "forecast_evaluations.resolution_event_id is not null" in sql
    assert "forecast_evaluations.resolution_vector_hash is not null" in sql
    assert "anon_1.selection_rank = 1" in sql
    assert "limit 7" in sql


def test_snapshot_queries_are_projection_only_and_scope_to_requested_ids() -> None:
    batch_statement = _target_batch_snapshot_statement(
        target_snapshot_ids=[101, 102],
        sibling_market_ids=[11, 12, 13],
    )
    close_statement = _close_snapshot_statement(market_ids=[11, 12])

    expected_columns = (
        "id",
        "batch_id",
        "market_id",
        "observed_at",
        "record_hash",
        "binary_yes_probability",
        "yes_midpoint",
        "yes_best_bid",
        "yes_best_ask",
        "no_midpoint",
        "no_best_bid",
        "no_best_ask",
        "eligible",
    )
    assert tuple(batch_statement.selected_columns.keys()) == expected_columns
    assert tuple(close_statement.selected_columns.keys()) == expected_columns

    batch_sql = _postgres_sql(batch_statement)
    close_sql = _postgres_sql(close_statement)
    assert "source_response_body" not in batch_sql
    assert "requested_token_ids" not in batch_sql
    assert "clob_market_snapshots.id in (101, 102)" in batch_sql
    assert "clob_market_snapshots.market_id in (11, 12, 13)" in batch_sql
    assert "row_number() over (partition by clob_market_snapshots.market_id" in close_sql
    assert "clob_market_snapshots.eligible is true" in close_sql


def test_evaluation_details_do_not_copy_model_components() -> None:
    decision_at = NOW - timedelta(hours=12)
    resolves_at = NOW
    target = Market(
        id=11,
        event_id="event-1",
        market_date=date(2026, 7, 28),
        kind=WeatherKind.HIGH,
        resolves_at=resolves_at,
    )
    station = Station(id=3, icao="KLGA")
    resolution = GammaOutcomeEvent(
        id=31,
        event_id="event-1",
        evidence_protocol="gamma-outcome-v1",
        first_outcome_at=NOW + timedelta(hours=1),
        outcome_vector_hash="a" * 64,
    )
    prices = DualPriceTimeline(
        [
            DualPricePoint(
                id=101,
                batch_id=201,
                market_id=11,
                observed_at=decision_at - timedelta(minutes=1),
                record_hash="b" * 64,
                binary_yes_probability=0.4,
                yes_midpoint=0.4,
                yes_best_bid=0.39,
                yes_best_ask=0.41,
                no_midpoint=0.6,
                no_best_bid=0.59,
                no_best_ask=0.61,
                eligible=True,
            ),
            DualPricePoint(
                id=102,
                batch_id=201,
                market_id=12,
                observed_at=decision_at - timedelta(minutes=1),
                record_hash="c" * 64,
                binary_yes_probability=0.6,
                yes_midpoint=0.6,
                yes_best_bid=0.59,
                yes_best_ask=0.61,
                no_midpoint=0.4,
                no_best_bid=0.39,
                no_best_ask=0.41,
                eligible=True,
            ),
            DualPricePoint(
                id=103,
                batch_id=202,
                market_id=11,
                observed_at=resolves_at - timedelta(minutes=1),
                record_hash="d" * 64,
                binary_yes_probability=0.7,
                yes_midpoint=0.7,
                yes_best_bid=0.69,
                yes_best_ask=0.71,
                no_midpoint=0.3,
                no_best_bid=0.29,
                no_best_ask=0.31,
                eligible=True,
            ),
        ]
    )

    values = _evaluation_values(
        probabilities=[
            SelectedProbability(
                id=41,
                market_id=11,
                ts=decision_at,
                probability=Decimal("0.45"),
                horizon_h=Decimal("12"),
                market_snapshot_id=101,
                horizon_bucket="6-24h",
            )
        ],
        markets={11: (target, station)},
        outcome_events={"event-1": resolution},
        outcomes_by_market={11: True},
        event_market_ids={"event-1": [11, 12]},
        timeline=prices,
        evaluated_at=NOW,
    )

    assert len(values) == 1
    assert values[0].horizon_market_probability == Decimal("0.4")
    assert values[0].pre_resolution_close == Decimal("0.7")
    assert values[0].details["horizon_bucket"] == "6-24h"
    assert "model_components" not in values[0].details
    assert values[0].market_benchmark_protocol == CLOB_DUAL_PROTOCOL
