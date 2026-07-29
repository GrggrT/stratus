from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConformalScale:
    scale: float
    quantile: float
    coverage: float
    sample_n: int
    empirical_coverage_before: float
    empirical_coverage_after: float


def finite_sample_quantile(values: list[float], coverage: float) -> float:
    if not values:
        raise ValueError("conformal scores are empty")
    if not 0 < coverage < 1:
        raise ValueError("coverage must be between zero and one")
    if any(not math.isfinite(value) or value < 0 for value in values):
        raise ValueError("conformal scores must be finite and non-negative")
    ordered = sorted(values)
    # Split-conformal finite-sample correction: ceil((n+1)*(1-alpha)).
    rank = min(len(ordered), math.ceil((len(ordered) + 1) * coverage))
    return ordered[rank - 1]


def fit_conformal_scale(
    standardized_absolute_residuals: list[float],
    *,
    coverage: float = 0.90,
    minimum_samples: int = 60,
    normal_reference_quantile: float = 1.6448536269514722,
    minimum_scale: float = 0.75,
    maximum_scale: float = 2.5,
) -> ConformalScale | None:
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    if len(standardized_absolute_residuals) < minimum_samples:
        return None
    if normal_reference_quantile <= 0:
        raise ValueError("normal reference quantile must be positive")
    if not 0 < minimum_scale <= maximum_scale:
        raise ValueError("conformal scale bounds are invalid")
    quantile = finite_sample_quantile(standardized_absolute_residuals, coverage)
    scale = min(maximum_scale, max(minimum_scale, quantile / normal_reference_quantile))
    before = sum(
        score <= normal_reference_quantile for score in standardized_absolute_residuals
    ) / len(standardized_absolute_residuals)
    after = sum(
        score <= normal_reference_quantile * scale
        for score in standardized_absolute_residuals
    ) / len(standardized_absolute_residuals)
    return ConformalScale(
        scale=scale,
        quantile=quantile,
        coverage=coverage,
        sample_n=len(standardized_absolute_residuals),
        empirical_coverage_before=before,
        empirical_coverage_after=after,
    )
