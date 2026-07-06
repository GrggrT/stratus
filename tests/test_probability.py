import math

import pytest

from stratus.services.probability import (
    Bucket,
    CalibrationParams,
    ComponentScore,
    ProbabilityComponent,
    apply_adaptive_weights,
    blend_probabilities,
    component_brier_scores,
    deterministic_component,
    empirical_bucket_probability,
    ensemble_component,
    gaussian_bucket_probability,
)

IDENTITY = CalibrationParams(
    bias_c=0,
    sigma_c=1,
    intercept=0,
    slope=1,
    sigma_intercept=1,
    sigma_slope=0,
    source_bucket="6-24h",
)


def test_gaussian_buckets_partition_distribution() -> None:
    buckets = [Bucket(None, -0.5), Bucket(-0.5, 0.5), Bucket(0.5, None)]
    total = sum(gaussian_bucket_probability(0, 1, bucket) for bucket in buckets)
    assert total == pytest.approx(1.0)


def test_ensemble_fraction_uses_bias_corrected_members() -> None:
    calibration = CalibrationParams(
        bias_c=1,
        sigma_c=1,
        intercept=0,
        slope=1,
        sigma_intercept=1,
        sigma_slope=0,
        source_bucket="6-24h",
    )
    component = ensemble_component(
        name="gefs",
        member_values_c=[9, 10, 11],
        calibration=calibration,
        bucket=Bucket(10.5, 11.5),
        weight=0.3,
    )
    assert component.probability == pytest.approx(1 / 3)


def test_deterministic_emos_correction() -> None:
    calibration = CalibrationParams(
        bias_c=0,
        sigma_c=1,
        intercept=1,
        slope=2,
        sigma_intercept=1,
        sigma_slope=0,
        source_bucket="6-24h",
    )
    component = deterministic_component(
        name="gfs",
        raw_temp_c=10,
        calibration=calibration,
        horizon_h=24,
        bucket=Bucket(20.5, 21.5),
        weight=0.1,
    )
    assert component.mean_c == 21
    assert component.probability > 0.38


def test_outlier_weight_is_halved_and_climate_is_applied() -> None:
    components = [
        ProbabilityComponent("ens", 0.5, 20, 1, 0.8, True, 31, "6-24h"),
        ProbabilityComponent("det", 0.9, 25, 1, 0.2, False, 1, "6-24h"),
    ]
    result = blend_probabilities(
        components,
        horizon_h=24,
        climate_values_c=[19, 20, 21],
        bucket=Bucket(19.5, 20.5),
    )
    det = next(component for component in result.components if component.name == "det")
    assert det.outlier is True
    assert result.climate_weight == 0.1
    assert 0 <= result.probability <= 1
    assert math.isfinite(result.sigma_c)


def test_empirical_bucket_probabilities_preserve_partition() -> None:
    values = [0, 1, 2]
    probabilities = [
        empirical_bucket_probability(values, Bucket(None, 0.5)),
        empirical_bucket_probability(values, Bucket(0.5, 1.5)),
        empirical_bucket_probability(values, Bucket(1.5, None)),
    ]
    assert sum(probabilities) == pytest.approx(1.0)


def test_component_brier_scores_are_grouped_by_station_horizon_and_model() -> None:
    rows = [
        (
            1,
            True,
            {
                "gfs": {"probability": 0.8},
                "ecmwf_ens": {"probability": 0.6},
                "blend": {"target_calibration_bucket": "6-24h"},
            },
        ),
        (
            1,
            False,
            {
                "gfs": {"probability": 0.2},
                "blend": {"target_calibration_bucket": "6-24h"},
            },
        ),
    ]

    scores = component_brier_scores(rows)

    assert scores[(1, "6-24h", "gfs")].brier == pytest.approx(0.04)
    assert scores[(1, "6-24h", "gfs")].sample_n == 2
    assert scores[(1, "6-24h", "ecmwf_ens")].brier == pytest.approx(0.16)


def test_adaptive_weights_reward_lower_brier() -> None:
    components = [
        ProbabilityComponent("good", 0.5, 20, 1, 0.5, True, 31, "6-24h"),
        ProbabilityComponent("bad", 0.5, 20, 1, 0.5, True, 31, "6-24h"),
    ]
    scores = {
        "good": ComponentScore(0.10, 30),
        "bad": ComponentScore(0.30, 30),
    }

    weighted, adaptive = apply_adaptive_weights(components, scores)

    assert adaptive is True
    assert weighted[0].weight > weighted[1].weight
    assert sum(component.weight for component in weighted) == pytest.approx(1.0)


def test_adaptive_weights_wait_for_complete_sample() -> None:
    components = [
        ProbabilityComponent("a", 0.5, 20, 1, 0.7, True, 31, "6-24h"),
        ProbabilityComponent("b", 0.5, 20, 1, 0.3, True, 31, "6-24h"),
    ]
    scores = {
        "a": ComponentScore(0.10, 30),
        "b": ComponentScore(0.20, 29),
    }

    weighted, adaptive = apply_adaptive_weights(components, scores)

    assert adaptive is False
    assert [component.weight for component in weighted] == [0.7, 0.3]
