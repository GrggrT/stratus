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


def test_shrink_to_market_weights_by_calibration_depth() -> None:
    from stratus.services.probability import shrink_to_market

    # No calibration: the model has no say, p_used == market.
    cold = shrink_to_market(0.9, 0.5, calibration_samples=0, prior_samples=30)
    assert cold.applied is True
    assert cold.weight == 0.0
    assert cold.probability == 0.5

    # n == prior -> half weight.
    warm = shrink_to_market(0.9, 0.5, calibration_samples=30, prior_samples=30)
    assert warm.weight == 0.5
    assert warm.probability == 0.7

    # Deep calibration -> mostly the model.
    deep = shrink_to_market(0.9, 0.5, calibration_samples=270, prior_samples=30)
    assert deep.weight == 0.9
    assert abs(deep.probability - 0.86) < 1e-9

    # Disabled prior or missing market anchor leaves the model untouched.
    off = shrink_to_market(0.9, 0.5, calibration_samples=0, prior_samples=0)
    assert off.applied is False and off.probability == 0.9
    anchorless = shrink_to_market(0.9, None, calibration_samples=0, prior_samples=30)
    assert anchorless.applied is False and anchorless.probability == 0.9
    # Output stays inside the open unit interval.
    clamped = shrink_to_market(1.0, 1.0, calibration_samples=30, prior_samples=30)
    assert clamped.probability == 0.999999


def test_pit_and_ks_uniformity() -> None:
    import math

    from stratus.services.probability import ks_uniform_pvalue, pit_value

    assert pit_value(20.0, 20.0, 2.0) == 0.5
    assert pit_value(24.0, 20.0, 2.0) == pytest.approx(
        0.5 * (1 + math.erf(2 / math.sqrt(2)))
    )
    with pytest.raises(ValueError):
        pit_value(20.0, 20.0, 0.0)

    # A near-uniform grid should not be rejected...
    uniform = [(i + 0.5) / 40 for i in range(40)]
    p_uniform = ks_uniform_pvalue(uniform)
    assert p_uniform is not None and p_uniform > 0.5
    # ...while an overconfident U-shape (all mass at the edges) is.
    clustered = [0.01] * 20 + [0.99] * 20
    p_clustered = ks_uniform_pvalue(clustered)
    assert p_clustered is not None and p_clustered < 0.01
    assert ks_uniform_pvalue([0.5, 0.5]) is None
