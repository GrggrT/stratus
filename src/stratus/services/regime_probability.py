from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from stratus.services.probability import Bucket, gaussian_bucket_probability


@dataclass(frozen=True, slots=True)
class DatedResidual:
    day: date
    residual_c: float


@dataclass(frozen=True, slots=True)
class RegimeShift:
    baseline_bias_c: float
    recent_bias_c: float
    shift_c: float
    adjustment_c: float
    ci_low_c: float
    ci_high_c: float
    baseline_n: int
    recent_n: int
    boundary_kind: str
    boundary_date: date
    detected: bool


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    index = round(probability * (len(ordered) - 1))
    return ordered[index]


def detect_regime_shift(
    observations: list[DatedResidual],
    *,
    as_of: date,
    known_version_date: date | None = None,
    recent_days: int = 30,
    baseline_days: int = 90,
    minimum_recent: int = 20,
    minimum_baseline: int = 40,
    minimum_shift_c: float = 0.25,
    maximum_adjustment_c: float = 2.0,
    bootstrap_samples: int = 512,
    seed: int = 0,
) -> RegimeShift | None:
    """Detect a persistent residual-bias regime change without time leakage.

    Multiple forecasts for one valid day are collapsed to a daily median before
    resampling. A known, recent model-version boundary takes priority; otherwise
    the last ``recent_days`` are compared with the preceding baseline window.
    """

    if recent_days <= 0 or baseline_days <= 0:
        raise ValueError("window lengths must be positive")
    if minimum_recent < 2 or minimum_baseline < 2:
        raise ValueError("minimum sample sizes must be at least two")
    if minimum_shift_c < 0 or maximum_adjustment_c <= 0:
        raise ValueError("shift thresholds must be non-negative and bounded")
    if bootstrap_samples < 100:
        raise ValueError("bootstrap_samples must be at least 100")

    by_day: dict[date, list[float]] = {}
    for observation in observations:
        if observation.day >= as_of:
            continue
        by_day.setdefault(observation.day, []).append(observation.residual_c)
    daily = {day: median(values) for day, values in by_day.items()}

    use_known_boundary = bool(
        known_version_date is not None
        and as_of - timedelta(days=baseline_days) <= known_version_date < as_of
    )
    if use_known_boundary:
        assert known_version_date is not None
        boundary = known_version_date
        boundary_kind = "known_model_version"
        baseline_start = boundary - timedelta(days=baseline_days)
        baseline = [
            value for day, value in daily.items() if baseline_start <= day < boundary
        ]
        recent = [value for day, value in daily.items() if boundary <= day < as_of]
    else:
        boundary = as_of - timedelta(days=recent_days)
        boundary_kind = "rolling_change_point"
        baseline_start = boundary - timedelta(days=baseline_days)
        baseline = [
            value for day, value in daily.items() if baseline_start <= day < boundary
        ]
        recent = [value for day, value in daily.items() if boundary <= day < as_of]

    if len(recent) < minimum_recent or len(baseline) < minimum_baseline:
        return None

    baseline_bias = float(median(baseline))
    recent_bias = float(median(recent))
    shift = recent_bias - baseline_bias
    full_bias = float(median([*baseline, *recent]))
    adjustment = max(
        -maximum_adjustment_c,
        min(maximum_adjustment_c, recent_bias - full_bias),
    )

    rng = random.Random(seed)
    bootstrap_shifts = []
    for _ in range(bootstrap_samples):
        baseline_sample = [baseline[rng.randrange(len(baseline))] for _ in baseline]
        recent_sample = [recent[rng.randrange(len(recent))] for _ in recent]
        bootstrap_shifts.append(float(median(recent_sample) - median(baseline_sample)))
    ci_low = _percentile(bootstrap_shifts, 0.025)
    ci_high = _percentile(bootstrap_shifts, 0.975)
    excludes_zero = ci_low > 0 or ci_high < 0

    return RegimeShift(
        baseline_bias_c=baseline_bias,
        recent_bias_c=recent_bias,
        shift_c=shift,
        adjustment_c=adjustment,
        ci_low_c=ci_low,
        ci_high_c=ci_high,
        baseline_n=len(baseline),
        recent_n=len(recent),
        boundary_kind=boundary_kind,
        boundary_date=boundary,
        detected=abs(shift) >= minimum_shift_c and excludes_zero,
    )


def apply_regime_shift_to_bucket(
    *,
    probability: float,
    mean_c: float,
    sigma_c: float,
    adjustment_c: float,
    bucket: Bucket,
) -> float:
    """Shift a component while preserving its non-Gaussian baseline shape."""

    gaussian_before = gaussian_bucket_probability(mean_c, sigma_c, bucket)
    gaussian_after = gaussian_bucket_probability(mean_c + adjustment_c, sigma_c, bucket)
    return min(1.0, max(0.0, probability + gaussian_after - gaussian_before))
