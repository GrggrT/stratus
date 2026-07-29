from __future__ import annotations

from stratus.services.censored_probability import (
    CensoredEventCase,
    blocked_oof_censored_assessment,
    censored_gaussian_probabilities,
    fit_censored_gaussian,
)
from stratus.services.probability import Bucket


def _shifted_cases(count: int = 60) -> list[CensoredEventCase]:
    cases = []
    for index in range(count):
        mean = 15.0 + (index % 12)
        buckets = (
            Bucket(None, mean + 0.25),
            Bucket(mean + 0.25, mean + 1.50),
            Bucket(mean + 1.50, None),
        )
        baseline = censored_gaussian_probabilities(
            buckets,
            mean_c=mean,
            sigma_c=0.8,
            bias_c=0.0,
            sigma_scale=1.0,
        )
        cases.append(
            CensoredEventCase(
                key=f"station-day-{index}",
                mean_c=mean,
                sigma_c=0.8,
                buckets=buckets,
                baseline_probabilities=baseline,
                outcomes=(False, True, False),
            )
        )
    return cases


def test_censored_fit_learns_bias_without_bucket_midpoints() -> None:
    fitted = fit_censored_gaussian(_shifted_cases())

    assert fitted.bias_c > 0.3
    assert 0.35 <= fitted.sigma_scale <= 3.0
    assert fitted.training_blocks == 60


def test_blocked_oof_censored_fit_improves_event_brier() -> None:
    assessment = blocked_oof_censored_assessment(_shifted_cases())

    assert assessment.blocks == 60
    assert assessment.buckets == 180
    assert assessment.calibrated_brier < assessment.baseline_brier
    assert assessment.delta_ci is not None
    assert assessment.delta_ci[1] < 0


def test_censored_partition_normalizes_open_tail_buckets() -> None:
    probabilities = censored_gaussian_probabilities(
        (Bucket(None, 0.0), Bucket(0.0, 1.0), Bucket(1.0, None)),
        mean_c=0.5,
        sigma_c=1.0,
        bias_c=0.0,
        sigma_scale=1.0,
    )

    assert sum(probabilities) == 1.0
    assert all(0 < probability < 1 for probability in probabilities)
