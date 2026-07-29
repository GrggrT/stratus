from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.config import Settings
from stratus.db.models import Market, ModelProb, WeatherKind
from stratus.repositories.accuracy import AccuracyPredictionValue
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    event_probability_distribution_sha256,
)
from stratus.services.pipeline_identity import probability_pipeline_fingerprint
from stratus.services.sealed_accuracy import (
    seal_legacy_accuracy_predictions,
    sealed_experiment_fingerprint,
    sealed_expert_version,
)

NOW = datetime(2026, 7, 14, 8, tzinfo=UTC)
RESOLVES_AT = NOW + timedelta(hours=12)


def _market(market_id: int, *, event_id: str = "event-1") -> Market:
    return Market(
        id=market_id,
        polymarket_id=f"poly-{market_id}",
        event_id=event_id,
        station_id=17,
        market_date=date(2026, 7, 14),
        kind=WeatherKind.HIGH,
        bucket_low_c=Decimal(str(20 + market_id)),
        bucket_high_c=Decimal(str(21 + market_id)),
        rules_raw="test",
        resolves_at=RESOLVES_AT,
    )


def _baseline(
    market_id: int,
    probability: str,
    *,
    ts: datetime = NOW,
    pipeline_version: str = "accuracy-v4",
) -> ModelProb:
    return ModelProb(
        id=100 + market_id,
        market_id=market_id,
        ts=ts,
        prob=Decimal(probability),
        sigma=Decimal("1.2500"),
        horizon_h=Decimal("12"),
        components={
            "pipeline": {
                "version": pipeline_version,
                "cohort": f"{pipeline_version}+test",
            }
        },
    )


def _legacy(
    market_id: int,
    probability: str,
    *,
    ts: datetime = NOW,
    expert: str = "trajectory_copula",
    version: str = "gaussian-copula-v1",
) -> AccuracyPredictionValue:
    return AccuracyPredictionValue(
        market_id=market_id,
        ts=ts,
        expert=expert,
        expert_version=version,
        probability=Decimal(probability),
        mean_c=Decimal("24.1250"),
        sigma_c=Decimal("1.3750"),
        training_sample_n=128,
        point_in_time_valid=True,
        distribution={"method": "legacy", "horizon_bucket": "6-24h"},
    )


def _attest_materialized_baselines(
    baselines: list[ModelProb],
    markets: list[Market],
) -> list[ModelProb]:
    markets_by_id = {market.id: market for market in markets}
    by_event: dict[str, list[ModelProb]] = {}
    for baseline in baselines:
        by_event.setdefault(markets_by_id[baseline.market_id].event_id, []).append(
            baseline
        )
    for event_id, event_baselines in by_event.items():
        probabilities = {row.market_id: row.prob for row in event_baselines}
        distribution_hash = event_probability_distribution_sha256(probabilities)
        for baseline in event_baselines:
            pipeline = baseline.components["pipeline"]
            cohort = pipeline.get("cohort") or pipeline["version"]
            baseline.components = {
                **baseline.components,
                "event_coherence": {
                    "protocol": EVENT_PROBABILITY_COHERENCE_PROTOCOL,
                    "event_id": event_id,
                    "materialized": True,
                    "materialization_reason": None,
                    # Deliberately trade-blocked: sealed accuracy must depend on
                    # the model vector, not on an executable CLOB witness.
                    "passed": False,
                    "reason": "event_probability_anchor_incomplete",
                    "bucket_count": len(event_baselines),
                    "probability_count": len(event_baselines),
                    "final_probability": str(baseline.prob),
                    "event_total_final": "1.000000",
                    "probability_ts": baseline.ts.isoformat(),
                    "pipeline_cohort": cohort,
                    "distribution_sha256": distribution_hash,
                },
            }
    return baselines


@pytest.fixture
def complete_inputs() -> tuple[
    list[AccuracyPredictionValue], list[ModelProb], list[Market]
]:
    markets = [_market(1), _market(2)]
    baselines = _attest_materialized_baselines(
        [_baseline(1, "0.45"), _baseline(2, "0.55")],
        markets,
    )
    return [_legacy(1, "0.2"), _legacy(2, "0.3")], baselines, markets


def test_seals_complete_event_vector_deterministically(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    settings = Settings()

    sealed = seal_legacy_accuracy_predictions(legacy, baselines, markets, settings)
    retry = seal_legacy_accuracy_predictions(legacy, baselines, markets, settings)

    assert sealed == retry
    assert [row.market_id for row in sealed] == [1, 2]
    assert [row.probability for row in sealed] == [
        Decimal("0.400000"),
        Decimal("0.600000"),
    ]
    assert sum((row.probability for row in sealed), start=Decimal("0")) == Decimal("1")
    assert {row.expert_version for row in sealed} == {
        "gaussian-copula-v1-sealed-v2"
    }
    assert all(row.evidence_eligible and row.point_in_time_valid for row in sealed)
    assert all(row.training_cutoff == NOW and row.generated_at is None for row in sealed)
    assert [row.baseline_model_prob_id for row in sealed] == [101, 102]
    assert len({row.input_hash for row in sealed}) == 1
    assert len(sealed[0].input_hash or "") == 64
    assert len({row.experiment_fingerprint for row in sealed}) == 1
    assert len(sealed[0].experiment_fingerprint or "") == 64
    assert [row.distribution["baseline_probability"] for row in sealed] == [0.45, 0.55]
    assert all(
        row.distribution["baseline_pipeline_version"] == "accuracy-v4+test"
        and row.distribution["evidence_protocol"] == "sealed-v2"
        and row.distribution["legacy_expert_version"] == "gaussian-copula-v1"
        and row.distribution["event_cohort"]["market_ids"] == [1, 2]
        and row.distribution["event_cohort"]["market_count"] == 2
        and row.distribution["horizon_bucket"] == "6-24h"
        for row in sealed
    )


def test_public_sealed_identity_helpers_match_persisted_identity(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    settings = Settings()
    sealed = seal_legacy_accuracy_predictions(legacy, baselines, markets, settings)
    version = sealed_expert_version("gaussian-copula-v1")

    assert version == "gaussian-copula-v1-sealed-v2"
    assert sealed[0].experiment_fingerprint == sealed_experiment_fingerprint(
        pipeline_fingerprint=probability_pipeline_fingerprint(settings),
        expert="trajectory_copula",
        legacy_version="gaussian-copula-v1",
        sealed_version=version,
        pipeline_version="accuracy-v4+test",
    )


def test_effective_cohort_changes_the_sealed_identity(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    original = seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings())
    changed_baselines = []
    for baseline in baselines:
        changed = ModelProb(
            id=baseline.id,
            market_id=baseline.market_id,
            ts=baseline.ts,
            prob=baseline.prob,
            sigma=baseline.sigma,
            horizon_h=baseline.horizon_h,
            components={
                "pipeline": {
                    "version": "accuracy-v4",
                    "cohort": "accuracy-v4+different-source-fingerprint",
                }
            },
        )
        changed_baselines.append(changed)
    _attest_materialized_baselines(changed_baselines, markets)

    changed = seal_legacy_accuracy_predictions(
        legacy,
        changed_baselines,
        markets,
        Settings(),
    )

    assert original[0].distribution["baseline_pipeline_version"] == "accuracy-v4+test"
    assert (
        changed[0].distribution["baseline_pipeline_version"]
        == "accuracy-v4+different-source-fingerprint"
    )
    assert changed[0].input_hash != original[0].input_hash
    assert changed[0].experiment_fingerprint != original[0].experiment_fingerprint


def test_empty_effective_cohort_falls_back_to_semantic_version(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    for baseline in baselines:
        baseline.components = {
            "pipeline": {"version": "accuracy-v4", "cohort": ""}
        }
    _attest_materialized_baselines(baselines, markets)

    sealed = seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings())

    assert len(sealed) == 2
    assert all(
        row.distribution["baseline_pipeline_version"] == "accuracy-v4"
        for row in sealed
    )


def test_input_hash_and_fingerprint_change_with_material_input(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    original = seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings())
    changed_input = seal_legacy_accuracy_predictions(
        [replace(legacy[0], probability=Decimal("0.21")), legacy[1]],
        baselines,
        markets,
        Settings(),
    )
    changed_settings = seal_legacy_accuracy_predictions(
        legacy,
        baselines,
        markets,
        Settings(accuracy_stack_l2=0.07),
    )

    assert original[0].input_hash != changed_input[0].input_hash
    assert original[0].experiment_fingerprint == changed_input[0].experiment_fingerprint
    assert original[0].experiment_fingerprint != changed_settings[0].experiment_fingerprint


def test_post_materialization_baseline_drift_fails_closed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    baselines[0] = _baseline(1, "0.451515")

    assert seal_legacy_accuracy_predictions(
        legacy, baselines, markets, Settings()
    ) == []


def test_explicit_training_cutoff_is_sealed_and_cannot_precede_cohort(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    cutoff = NOW + timedelta(seconds=3)

    sealed = seal_legacy_accuracy_predictions(
        legacy,
        baselines,
        markets,
        Settings(),
        training_cutoff=cutoff,
    )
    default_cutoff = seal_legacy_accuracy_predictions(
        legacy,
        baselines,
        markets,
        Settings(),
    )

    assert all(row.training_cutoff == cutoff for row in sealed)
    assert sealed[0].input_hash != default_cutoff[0].input_hash
    assert (
        seal_legacy_accuracy_predictions(
            legacy,
            baselines,
            markets,
            Settings(),
            training_cutoff=NOW - timedelta(microseconds=1),
        )
        == []
    )


def test_algorithmic_legacy_baseline_is_hashed_but_exact_baseline_is_attested(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    original = seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings())
    legacy[0] = replace(
        legacy[0],
        distribution={**legacy[0].distribution, "baseline_probability": 0.2},
    )

    sealed = seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings())

    assert len(sealed) == 2
    assert sealed[0].distribution["baseline_probability"] == 0.45
    assert sealed[0].input_hash != original[0].input_hash


@pytest.mark.parametrize("missing", ["bucket", "baseline"])
def test_missing_bucket_or_exact_baseline_fails_closed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
    missing: str,
) -> None:
    legacy, baselines, markets = complete_inputs
    if missing == "bucket":
        legacy = legacy[:1]
    else:
        baselines = baselines[:1]

    assert seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings()) == []


def test_failed_probability_materialization_cannot_be_sealed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    proof = baselines[0].components["event_coherence"]
    proof["materialized"] = False
    proof["materialization_reason"] = "event_probability_vector_incomplete"

    assert seal_legacy_accuracy_predictions(
        legacy, baselines, markets, Settings()
    ) == []


def test_mixed_candidate_timestamps_fail_closed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    later = NOW + timedelta(minutes=1)
    legacy[1] = replace(legacy[1], ts=later)
    baselines[1] = _baseline(2, "0.55", ts=later)

    assert seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings()) == []


def test_mixed_baseline_pipelines_fail_closed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
) -> None:
    legacy, baselines, markets = complete_inputs
    baselines[1] = _baseline(2, "0.55", pipeline_version="accuracy-v5")

    assert seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings()) == []


@pytest.mark.parametrize(
    "mutation",
    [
        "baseline_not_partition",
        "post_resolution",
        "baseline_provenance_mismatch",
        "not_point_in_time",
        "already_sealed",
    ],
)
def test_invalid_cohort_fails_closed(
    complete_inputs: tuple[
        list[AccuracyPredictionValue], list[ModelProb], list[Market]
    ],
    mutation: str,
) -> None:
    legacy, baselines, markets = complete_inputs
    if mutation == "baseline_not_partition":
        baselines[1] = _baseline(2, "0.50")
    elif mutation == "post_resolution":
        markets[1].resolves_at = NOW
    elif mutation == "baseline_provenance_mismatch":
        legacy[1] = replace(
            legacy[1],
            distribution={
                **legacy[1].distribution,
                "baseline_probability": 0.40,
                "baseline_pipeline_version": "accuracy-v4",
            },
        )
    elif mutation == "not_point_in_time":
        legacy[1] = replace(legacy[1], point_in_time_valid=False)
    else:
        legacy[1] = replace(
            legacy[1],
            distribution={**legacy[1].distribution, "evidence_protocol": "sealed-v2"},
        )

    assert seal_legacy_accuracy_predictions(legacy, baselines, markets, Settings()) == []


def test_invalid_event_does_not_discard_independent_valid_event() -> None:
    valid_legacy = [_legacy(1, "0.4"), _legacy(2, "0.6")]
    invalid_legacy = [
        _legacy(3, "0.4", expert="conformal"),
    ]
    baselines = [
        _baseline(1, "0.45"),
        _baseline(2, "0.55"),
        _baseline(3, "0.5"),
        _baseline(4, "0.5"),
    ]
    markets = [
        _market(1),
        _market(2),
        _market(3, event_id="event-2"),
        _market(4, event_id="event-2"),
    ]
    _attest_materialized_baselines(baselines, markets)

    sealed = seal_legacy_accuracy_predictions(
        valid_legacy + invalid_legacy,
        baselines,
        markets,
        Settings(),
    )

    assert [row.market_id for row in sealed] == [1, 2]
