from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmosFit:
    bias_c: float
    sigma_c: float
    intercept: float
    slope: float
    sample_n: int


@dataclass(frozen=True, slots=True)
class HorizonSigmaFit:
    intercept: float
    slope: float


def fit_horizon_sigma(
    samples: list[tuple[float, float, int]],
) -> HorizonSigmaFit:
    if len(samples) < 2:
        raise ValueError("at least two horizon sigma points are required")
    if any(horizon < 0 or sigma <= 0 or weight <= 0 for horizon, sigma, weight in samples):
        raise ValueError("horizon sigma samples must be positive")
    total_weight = sum(weight for _, _, weight in samples)
    mean_horizon = sum(horizon * weight for horizon, _, weight in samples) / total_weight
    mean_sigma = sum(sigma * weight for _, sigma, weight in samples) / total_weight
    variance = sum(
        weight * (horizon - mean_horizon) ** 2
        for horizon, _, weight in samples
    )
    if variance <= 1e-12:
        raise ValueError("horizon variance is zero")
    covariance = sum(
        weight * (horizon - mean_horizon) * (sigma - mean_sigma)
        for horizon, sigma, weight in samples
    )
    slope = covariance / variance
    if slope <= 0:
        return HorizonSigmaFit(intercept=mean_sigma, slope=0.0)
    return HorizonSigmaFit(
        intercept=max(0.0, mean_sigma - slope * mean_horizon),
        slope=slope,
    )


def fit_emos(samples: list[tuple[float, float]], sigma_floor: float = 0.5) -> EmosFit:
    if len(samples) < 3:
        raise ValueError("at least three samples are required")
    raw_values = [raw for raw, _ in samples]
    actual_values = [actual for _, actual in samples]
    mean_raw = sum(raw_values) / len(samples)
    mean_actual = sum(actual_values) / len(samples)
    variance = sum((raw - mean_raw) ** 2 for raw in raw_values)
    if variance <= 1e-12:
        raise ValueError("forecast variance is zero")
    covariance = sum(
        (raw - mean_raw) * (actual - mean_actual)
        for raw, actual in samples
    )
    slope = covariance / variance
    intercept = mean_actual - slope * mean_raw
    residuals = [
        actual - (intercept + slope * raw) for raw, actual in samples
    ]
    residual_sigma = math.sqrt(
        sum(residual**2 for residual in residuals) / (len(samples) - 2)
    )
    return EmosFit(
        bias_c=sum(actual - raw for raw, actual in samples) / len(samples),
        sigma_c=max(residual_sigma, sigma_floor),
        intercept=intercept,
        slope=slope,
        sample_n=len(samples),
    )


def horizon_bucket(horizon_h: int) -> str:
    if horizon_h <= 6:
        return "0-6h"
    if horizon_h <= 24:
        return "6-24h"
    if horizon_h <= 48:
        return "24-48h"
    return "48h+"
