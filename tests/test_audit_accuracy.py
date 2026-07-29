from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from stratus.db.models import Forecast, WeatherKind
from stratus.jobs.audit_accuracy import (
    HORIZON_ANCHORS,
    UPSERT_BATCH_ROWS,
    SelectedTrajectoryVector,
    TrajectoryMarketRow,
    TrajectoryModelProbRow,
    TrajectoryPredictionRow,
    TrajectorySnapshot,
    _cached_block_bootstrap_mean_ci,
    _canonical_exact_truth_exists,
    _consider_trajectory_snapshot,
    _daily_actual_truth_exists,
    _daily_actual_truth_statement,
    _model_prob_pipeline_version,
    _normalized_baseline_probabilities,
    _row_batches,
    _trajectory_prediction_record_is_valid,
    _trajectory_projection_from_mapping,
    _truth_date_index,
    _truth_day_count,
)
from stratus.repositories.accuracy import (
    ACCURACY_PREDICTION_BATCH_ROWS,
    AccuracyPredictionValue,
    _prediction_batches,
    accuracy_prediction_record_hash,
)
from stratus.services.accuracy_models import block_bootstrap_mean_ci


def test_continuous_accuracy_truth_excludes_reconciled_daily_actual_conflicts() -> None:
    statement = _daily_actual_truth_statement(
        as_of=datetime(2026, 7, 20, 12, tzinfo=UTC)
    )

    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "NOT (EXISTS (SELECT resolution_reconciliations.id" in sql
    assert "'clear_mismatch'" in sql
    assert "'exact_truth_gap'" in sql
    assert "'exact_source_mismatch'" in sql
    assert "'boundary_sensitive'" not in sql
    assert "NOT (EXISTS (SELECT settlement_actuals.id" in sql


def test_forecast_selector_requires_undisputed_daily_actual_fallback() -> None:
    statement = select(Forecast.id).where(
        _daily_actual_truth_exists(
            as_of=datetime(2026, 7, 20, 12, tzinfo=UTC)
        )
    )

    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "daily_actuals.station_id = forecasts.station_id" in sql
    assert "daily_actuals.date = forecasts.valid_date" in sql
    assert "daily_actuals.kind = forecasts.kind" in sql
    assert "NOT (EXISTS (SELECT resolution_reconciliations.id" in sql
    assert "'boundary_sensitive'" not in sql
    assert "NOT (EXISTS (SELECT settlement_actuals.id" in sql


def test_forecast_selector_accepts_only_agreed_canonical_exact_truth() -> None:
    statement = select(Forecast.id).where(
        _canonical_exact_truth_exists(
            as_of=datetime(2026, 7, 20, 12, tzinfo=UTC)
        )
    )

    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "canonical_settlement_actuals" in sql
    assert "GROUP BY eligible_settlement_actuals.station_id" in sql
    assert "HAVING min(eligible_settlement_actuals.value_c) = max(" in sql
    assert "canonical_settlement_actuals.station_id = forecasts.station_id" in sql


def test_accuracy_audit_batches_stay_below_asyncpg_parameter_limit() -> None:
    rows = [{"row": index} for index in range(2_201)]

    batches = _row_batches(rows)

    assert [len(batch) for batch in batches] == [500, 500, 500, 500, 201]
    assert UPSERT_BATCH_ROWS * 18 < 32_767
    assert [row for batch in batches for row in batch] == rows


def test_accuracy_audit_batches_reject_invalid_size() -> None:
    with pytest.raises(ValueError, match="batch_size"):
        _row_batches([], 0)


def test_accuracy_audit_reconstructs_and_attests_normalized_baseline() -> None:
    rows = []
    for market_id, raw, normalized in ((1, "0.501", 0.501), (2, "0.499", 0.499)):
        prediction = SimpleNamespace(
            distribution={
                "baseline_probability": float(raw),
                "normalized_baseline_probability": normalized,
                "baseline_pipeline_version": "accuracy-v4+source-a",
            }
        )
        market = SimpleNamespace(id=market_id)
        model_prob = SimpleNamespace(
            prob=Decimal(raw),
            components={
                "pipeline": {
                    "version": "accuracy-v4",
                    "cohort": "accuracy-v4+source-a",
                }
            },
        )
        rows.append((prediction, market, model_prob))

    reconstructed = _normalized_baseline_probabilities(rows)  # type: ignore[arg-type]

    assert reconstructed == pytest.approx({1: 0.501, 2: 0.499})
    rows[0][0].distribution["normalized_baseline_probability"] = 0.1
    assert _normalized_baseline_probabilities(rows) is None  # type: ignore[arg-type]


def test_accuracy_prediction_upsert_batches_large_market_sets() -> None:
    now = datetime.now(UTC)
    values = [
        AccuracyPredictionValue(
            market_id=index,
            ts=now,
            expert="test",
            expert_version="v1",
            probability=Decimal("0.5"),
            mean_c=None,
            sigma_c=None,
            training_sample_n=0,
            point_in_time_valid=True,
            distribution={},
        )
        for index in range(1_201)
    ]

    batches = _prediction_batches(values)

    assert [len(batch) for batch in batches] == [500, 500, 201]
    assert ACCURACY_PREDICTION_BATCH_ROWS * 10 < 32_767


def test_accuracy_audit_reuses_bit_exact_bootstrap_for_identical_values() -> None:
    blocks = {
        "day-1": [-0.2, -0.1],
        "day-2": [-0.3],
        "day-3": [0.1],
        "day-4": [-0.4],
        "day-5": [-0.2],
    }
    equivalent = {
        "renamed-1": [-0.2, -0.1],
        "renamed-2": [-0.3],
        "renamed-3": [0.1],
        "renamed-4": [-0.4],
        "renamed-5": [-0.2],
    }
    cache: dict[tuple[tuple[float, ...], ...], tuple[float, float] | None] = {}

    expected = block_bootstrap_mean_ci(blocks)
    first = _cached_block_bootstrap_mean_ci(blocks, cache)
    second = _cached_block_bootstrap_mean_ci(equivalent, cache)

    assert first == expected
    assert second == expected
    assert len(cache) == 1


def test_accuracy_audit_truth_day_index_matches_reference_scan() -> None:
    truth = {
        (7, date(2026, 7, 1), WeatherKind.HIGH): 20.0,
        (7, date(2026, 7, 3), WeatherKind.HIGH): 21.0,
        (7, date(2026, 7, 2), WeatherKind.LOW): 10.0,
        (8, date(2026, 7, 4), WeatherKind.HIGH): 22.0,
    }
    index = _truth_date_index(truth)

    assert index[(7, WeatherKind.HIGH)] == (
        date(2026, 7, 1),
        date(2026, 7, 3),
    )
    assert (
        _truth_day_count(
            index,
            station_id=7,
            kind=WeatherKind.HIGH,
            starting_on=date(2026, 7, 2),
        )
        == 1
    )
    assert (
        _truth_day_count(
            index,
            station_id=7,
            kind=WeatherKind.LOW,
            starting_on=date(2026, 7, 4),
        )
        == 0
    )


def test_streamed_trajectory_projection_preserves_record_hash_validation() -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    distribution = {
        "evidence_protocol": "sealed-v2",
        "horizon_bucket": "6-24h",
        "baseline_probability": 0.5,
        "normalized_baseline_probability": 0.5,
        "baseline_pipeline_version": "accuracy-v4+fixture",
    }
    value = AccuracyPredictionValue(
        market_id=41,
        ts=now,
        expert="trajectory_ar1",
        expert_version="trajectory-ar1-v1",
        probability=Decimal("0.600000"),
        mean_c=Decimal("20.0000"),
        sigma_c=Decimal("1.5000"),
        training_sample_n=90,
        point_in_time_valid=True,
        distribution=distribution,
        generated_at=now + timedelta(seconds=1),
        training_cutoff=now - timedelta(days=1),
        baseline_model_prob_id=73,
        input_hash="a" * 64,
        experiment_fingerprint="b" * 64,
        evidence_eligible=True,
    )
    prediction = TrajectoryPredictionRow(
        market_id=value.market_id,
        ts=value.ts,
        expert=value.expert,
        expert_version=value.expert_version,
        probability=value.probability,
        mean_c=value.mean_c,
        sigma_c=value.sigma_c,
        training_sample_n=value.training_sample_n,
        point_in_time_valid=value.point_in_time_valid,
        generated_at=value.generated_at or now,
        training_cutoff=value.training_cutoff,
        baseline_model_prob_id=value.baseline_model_prob_id,
        input_hash=value.input_hash,
        experiment_fingerprint=value.experiment_fingerprint,
        record_hash=accuracy_prediction_record_hash(value),
        evidence_eligible=value.evidence_eligible,
        distribution=value.distribution,
    )

    assert _trajectory_prediction_record_is_valid(prediction)
    assert not _trajectory_prediction_record_is_valid(
        replace(prediction, probability=Decimal("0.610000"))
    )


def test_streamed_trajectory_projection_preserves_pipeline_json_types() -> None:
    now = datetime(2026, 7, 19, 8, tzinfo=UTC)
    distribution = {
        "horizon_bucket": "6-24h",
        "baseline_probability": 0.5,
        "normalized_baseline_probability": 0.5,
        "baseline_pipeline_version": "accuracy-v4",
    }
    row = {
        "prediction_market_id": 41,
        "prediction_ts": now,
        "prediction_expert": "trajectory_ar1",
        "prediction_expert_version": "trajectory-ar1-v1",
        "prediction_probability": Decimal("0.500000"),
        "prediction_mean_c": None,
        "prediction_sigma_c": None,
        "prediction_training_sample_n": 90,
        "prediction_point_in_time_valid": True,
        "prediction_generated_at": now,
        "prediction_training_cutoff": now - timedelta(days=1),
        "prediction_baseline_model_prob_id": 73,
        "prediction_input_hash": "a" * 64,
        "prediction_experiment_fingerprint": "b" * 64,
        "prediction_record_hash": "c" * 64,
        "prediction_evidence_eligible": True,
        "prediction_distribution": distribution,
        "market_id": 41,
        "market_event_id": "event-vector",
        "market_station_id": 7,
        "market_kind": WeatherKind.HIGH,
        "market_date": date(2026, 7, 20),
        "market_resolves_at": now + timedelta(days=1),
        "model_prob_id": 73,
        "model_prob_market_id": 41,
        "model_prob_ts": now,
        "model_prob_prob": Decimal("0.500000"),
        "model_prob_horizon_h": Decimal("12"),
        "model_prob_pipeline": {"cohort": 123, "version": "accuracy-v4"},
    }

    _prediction, _market, model_prob = _trajectory_projection_from_mapping(  # type: ignore[arg-type]
        row
    )

    assert model_prob.components["pipeline"] == {
        "cohort": 123,
        "version": "accuracy-v4",
    }
    assert _model_prob_pipeline_version(model_prob) == "accuracy-v4"


def test_streamed_trajectory_selection_keeps_only_complete_nearest_vector() -> None:
    ts = datetime(2026, 7, 19, 8, tzinfo=UTC)
    fingerprint = "c" * 64
    bucket = "6-24h"

    def snapshot(
        *,
        at: datetime,
        horizon_h: Decimal,
        first_candidate: Decimal = Decimal("0.600000"),
    ) -> TrajectorySnapshot:
        rows: TrajectorySnapshot = []
        for market_id, candidate, baseline in (
            (1, first_candidate, Decimal("0.550000")),
            (2, Decimal("1") - first_candidate, Decimal("0.450000")),
        ):
            distribution = {
                "horizon_bucket": bucket,
                "baseline_probability": float(baseline),
                "normalized_baseline_probability": float(baseline),
                "baseline_pipeline_version": "accuracy-v4+fixture",
            }
            prediction = TrajectoryPredictionRow(
                market_id=market_id,
                ts=at,
                expert="trajectory_ar1",
                expert_version="trajectory-ar1-v1",
                probability=candidate,
                mean_c=None,
                sigma_c=None,
                training_sample_n=90,
                point_in_time_valid=True,
                generated_at=at,
                training_cutoff=at - timedelta(days=1),
                baseline_model_prob_id=100 + market_id,
                input_hash="d" * 64,
                experiment_fingerprint=fingerprint,
                record_hash="e" * 64,
                evidence_eligible=True,
                distribution=distribution,
            )
            market = TrajectoryMarketRow(
                id=market_id,
                event_id="event-vector",
                station_id=7,
                kind=WeatherKind.HIGH,
                market_date=date(2026, 7, 20),
                resolves_at=at + timedelta(days=1),
            )
            model_prob = TrajectoryModelProbRow(
                id=100 + market_id,
                market_id=market_id,
                ts=at,
                prob=baseline,
                horizon_h=horizon_h,
                components={
                    "pipeline": {
                        "version": "accuracy-v4",
                        "cohort": "accuracy-v4+fixture",
                    }
                },
            )
            rows.append((prediction, market, model_prob))
        return rows

    selected: dict[
        tuple[str, str, str, str, str], SelectedTrajectoryVector
    ] = {}
    distances: dict[tuple[str, str, str, str, str], float] = {}

    def consider(rows: TrajectorySnapshot, at: datetime) -> None:
        _consider_trajectory_snapshot(
            snapshot_key=(
                "trajectory_ar1",
                "trajectory-ar1-v1",
                fingerprint,
                "event-vector",
                bucket,
                at,
            ),
            snapshot_rows=rows,
            resolved_market_ids_by_event={"event-vector": {1, 2}},
            outcomes_by_market={1: False, 2: True},
            selected_vectors=selected,
            selected_distances=distances,
        )

    incomplete = snapshot(at=ts, horizon_h=Decimal("10"))[:1]
    consider(incomplete, ts)
    assert selected == {}

    farther = snapshot(at=ts + timedelta(minutes=1), horizon_h=Decimal("10"))
    nearest = snapshot(
        at=ts + timedelta(minutes=2),
        horizon_h=Decimal(str(HORIZON_ANCHORS[bucket])),
    )
    equal_later = snapshot(
        at=ts + timedelta(minutes=3),
        horizon_h=Decimal(str(HORIZON_ANCHORS[bucket])),
        first_candidate=Decimal("0.700000"),
    )
    for at, rows in (
        (ts + timedelta(minutes=1), farther),
        (ts + timedelta(minutes=2), nearest),
        (ts + timedelta(minutes=3), equal_later),
    ):
        consider(rows, at)

    key = (
        "trajectory_ar1",
        "trajectory-ar1-v1",
        fingerprint,
        "event-vector",
        bucket,
    )
    assert selected[key].event_id == "event-vector"
    assert tuple(score.baseline_probability for score in selected[key].scores) == (
        pytest.approx(0.55),
        pytest.approx(0.45),
    )
    assert selected[key].scores[0].candidate_probability == Decimal("0.600000")
    assert selected[key].scores[1].candidate_probability == Decimal("0.400000")
    assert distances[key] == 0.0
