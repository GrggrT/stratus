from __future__ import annotations

import pytest

from stratus.db.models import WeatherKind
from stratus.services.accuracy_models import (
    AnalogCase,
    QuantileRegressionForest,
    analog_ensemble,
    ar1_adjustment,
    blend_experts,
    block_bootstrap_mean_ci,
    constrained_extreme_probability,
    fit_ar1,
    fit_regularized_stack,
    fit_two_gaussian_mixture,
)
from stratus.services.probability import Bucket


def test_ar1_fit_recovers_persistent_residual_direction() -> None:
    residuals = [0.0]
    for _ in range(30):
        residuals.append(0.2 + 0.7 * residuals[-1])
    fit = fit_ar1(residuals)
    assert fit is not None
    correction, sigma = ar1_adjustment(fit, residuals[-1])
    assert fit.phi == pytest.approx(0.7, abs=0.03)
    assert correction > 0.5
    assert sigma >= 0.15


def test_intraday_high_constraint_eliminates_impossible_bucket() -> None:
    probability = constrained_extreme_probability(
        future_mean_c=25,
        future_sigma_c=1,
        observed_extreme_c=27,
        kind=WeatherKind.HIGH,
        bucket=Bucket(24.5, 25.5),
    )
    assert probability == 0


def test_intraday_low_constraint_preserves_partition() -> None:
    buckets = [Bucket(None, 9.5), Bucket(9.5, 10.5), Bucket(10.5, None)]
    probabilities = [
        constrained_extreme_probability(
            future_mean_c=10,
            future_sigma_c=1,
            observed_extreme_c=11,
            kind=WeatherKind.LOW,
            bucket=bucket,
        )
        for bucket in buckets
    ]
    assert sum(probabilities) == pytest.approx(1)


def test_analog_and_qrf_return_empirical_distributions() -> None:
    cases = [AnalogCase((float(index),), actual_c=float(index * 2)) for index in range(20)]
    analog = analog_ensemble((9.2,), cases, neighbors=5)
    assert analog is not None
    assert 15 < analog.mean_c < 22

    forest = QuantileRegressionForest(trees=16, min_leaf=3, seed=7)
    forest.fit([case.features for case in cases], [case.actual_c for case in cases])
    distribution = forest.distribution((9.2,))
    assert distribution
    assert 10 < sum(distribution) / len(distribution) < 28


def test_analog_bucket_probability_weights_nearest_cases() -> None:
    cases = [
        AnalogCase((0.0,), actual_c=0.0),
        AnalogCase((1.0,), actual_c=10.0),
        AnalogCase((2.0,), actual_c=10.0),
    ]
    analog = analog_ensemble((0.05,), cases, neighbors=3)

    assert analog is not None
    assert analog.bucket_probability(Bucket(low_c=None, high_c=5.0)) > 0.8


def test_mixture_detects_two_residual_regimes() -> None:
    values = [-2 + index * 0.02 for index in range(30)] + [2 + index * 0.02 for index in range(30)]
    mixture = fit_two_gaussian_mixture(values)
    assert mixture is not None
    assert mixture.means[0] < -1
    assert mixture.means[1] > 1
    assert sum(mixture.weights) == pytest.approx(1)


def test_regularized_stack_favors_better_expert_without_negative_weights() -> None:
    outcomes = [False, False, True, True] * 20
    predictions = [
        {
            "good": 0.1 if not outcome else 0.9,
            "bad": 0.9 if not outcome else 0.1,
        }
        for outcome in outcomes
    ]
    weights = fit_regularized_stack(predictions, outcomes, l2=0.01)
    assert weights["good"] > 0.9
    assert weights["bad"] >= 0
    assert blend_experts({"good": 0.8, "bad": 0.2}, weights) > 0.7


def test_block_bootstrap_is_deterministic_and_requires_independent_blocks() -> None:
    values = {f"day-{index}": [float(index)] for index in range(10)}
    first = block_bootstrap_mean_ci(values, iterations=500, seed=12)
    second = block_bootstrap_mean_ci(values, iterations=500, seed=12)
    assert first == second
    assert first is not None
    assert first[0] < 4.5 < first[1]
    assert block_bootstrap_mean_ci({"one": [1.0]}) is None
