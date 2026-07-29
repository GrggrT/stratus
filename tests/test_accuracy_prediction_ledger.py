from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    _prediction_row,
    accuracy_prediction_record_hash,
)

NOW = datetime(2026, 7, 14, 6, tzinfo=UTC)
INPUT_HASH = "a" * 64


def _sealed_value(**changes: object) -> AccuracyPredictionValue:
    value = AccuracyPredictionValue(
        market_id=17,
        ts=NOW,
        expert="historical_weighted_shrunk",
        expert_version="convex-affine-empirical-shrink25-sealed-v2",
        probability=Decimal("0.421337"),
        mean_c=Decimal("24.1250"),
        sigma_c=Decimal("1.3750"),
        training_sample_n=384,
        point_in_time_valid=True,
        distribution={
            "evidence_protocol": "sealed-v2",
            "baseline_pipeline_version": "test-pipeline-v1",
            "baseline_probability": 0.4,
            "normalized_baseline_probability": 0.4,
            "models": ["ecmwf", "gfs", "icon", "gem"],
        },
        generated_at=NOW + timedelta(seconds=2),
        training_cutoff=NOW,
        baseline_model_prob_id=91,
        input_hash=INPUT_HASH,
        experiment_fingerprint="b" * 64,
        evidence_eligible=True,
    )
    return replace(value, **changes)


def test_accuracy_prediction_record_hash_is_canonical_and_retry_stable() -> None:
    value = _sealed_value()
    retry = replace(
        value,
        generated_at=NOW + timedelta(minutes=1),
        distribution={
            "models": ["ecmwf", "gfs", "icon", "gem"],
            "evidence_protocol": "sealed-v2",
            "baseline_pipeline_version": "test-pipeline-v1",
            "baseline_probability": 0.4,
            "normalized_baseline_probability": 0.4,
        },
    )

    assert accuracy_prediction_record_hash(value) == accuracy_prediction_record_hash(retry)


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("probability", Decimal("0.421338")),
        ("training_cutoff", NOW - timedelta(seconds=1)),
        ("baseline_model_prob_id", 92),
        ("input_hash", "b" * 64),
        ("experiment_fingerprint", "c" * 64),
        ("evidence_eligible", False),
        ("distribution", {"evidence_protocol": "tampered"}),
    ],
)
def test_accuracy_prediction_record_hash_covers_evidence_content(
    field: str, changed: object
) -> None:
    value = _sealed_value()

    assert accuracy_prediction_record_hash(value) != accuracy_prediction_record_hash(
        replace(value, **{field: changed})
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"point_in_time_valid": False},
        {"training_cutoff": None},
        {"baseline_model_prob_id": None},
        {"input_hash": None},
        {"input_hash": "too-short"},
        {"experiment_fingerprint": "", "expert_version": ""},
        {
            "distribution": {
                "evidence_protocol": "sealed-v2",
                "baseline_pipeline_version": "",
                "baseline_probability": 0.4,
                "normalized_baseline_probability": 0.4,
            }
        },
        {
            "distribution": {
                "evidence_protocol": "sealed-v2",
                "baseline_pipeline_version": "test-pipeline-v1",
                "baseline_probability": 0.4,
            }
        },
    ],
)
def test_sealed_evidence_rejects_missing_provenance(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="missing required provenance"):
        _prediction_row(_sealed_value(**changes))


@pytest.mark.parametrize(
    "changes",
    [
        {"ts": NOW + timedelta(minutes=1)},
        {"training_cutoff": NOW + timedelta(minutes=1)},
    ],
)
def test_sealed_evidence_rejects_future_dated_inputs(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="future-dated inputs"):
        _prediction_row(_sealed_value(**changes))


def test_prediction_row_persists_normalized_provenance_and_record_hash() -> None:
    value = replace(
        _sealed_value(),
        generated_at=None,
        experiment_fingerprint=None,
        evidence_eligible=False,
    )

    row = _prediction_row(value)

    assert row["generated_at"] == value.ts
    assert row["experiment_fingerprint"] == value.expert_version
    assert row["evidence_eligible"] is False
    assert row["record_hash"] == accuracy_prediction_record_hash(
        replace(
            value,
            generated_at=value.ts,
            experiment_fingerprint=value.expert_version,
        )
    )
