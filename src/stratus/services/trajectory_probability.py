from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, median

from stratus.services.probability import Bucket


@dataclass(frozen=True, slots=True)
class HourlyCurve:
    model: str
    points: tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class TrajectoryDistribution:
    highs_c: tuple[float, ...]
    lows_c: tuple[float, ...]
    model_count: int
    hour_count: int
    sample_count: int
    rho: float


@dataclass(frozen=True, slots=True)
class NowcastAdjustment:
    curves: tuple[HourlyCurve, ...]
    recent_bias_c: float
    matched_hours: int
    latest_observed_hour: str
    high_shift_c: float
    low_shift_c: float


def _hour_distance(later: str, earlier: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 3600


def build_remaining_day_nowcast(
    curves: list[HourlyCurve],
    observed_by_local_hour: dict[str, float],
    *,
    minimum_matched_hours: int = 3,
    lookback_hours: int = 6,
    decay_half_life_hours: float = 6.0,
    maximum_bias_c: float = 4.0,
) -> NowcastAdjustment | None:
    if len(curves) < 2:
        return None
    if minimum_matched_hours < 1 or lookback_hours < minimum_matched_hours:
        raise ValueError("invalid nowcast observation requirements")
    if decay_half_life_hours <= 0 or maximum_bias_c <= 0:
        raise ValueError("nowcast decay and bias cap must be positive")
    point_maps = [dict(curve.points) for curve in curves]
    common_hours = sorted(set.intersection(*(set(points) for points in point_maps)))
    consensus = {
        hour: median(points[hour] for points in point_maps) for hour in common_hours
    }
    matched = sorted(set(consensus) & set(observed_by_local_hour))
    if len(matched) < minimum_matched_hours:
        return None
    matched = matched[-lookback_hours:]
    latest_hour = matched[-1]
    residuals = [observed_by_local_hour[hour] - consensus[hour] for hour in matched]
    center = median(residuals)
    absolute_deviations = [abs(value - center) for value in residuals]
    robust_radius = max(0.5, 2.5 * median(absolute_deviations))
    clipped = [
        min(center + robust_radius, max(center - robust_radius, value))
        for value in residuals
    ]
    weights = [
        0.5 ** (_hour_distance(latest_hour, hour) / max(decay_half_life_hours / 2, 0.1))
        for hour in matched
    ]
    recent_bias = sum(value * weight for value, weight in zip(clipped, weights, strict=True)) / sum(
        weights
    )
    recent_bias = min(maximum_bias_c, max(-maximum_bias_c, recent_bias))

    adjusted_curves: list[HourlyCurve] = []
    for curve in curves:
        adjusted_points: list[tuple[str, float]] = []
        for hour, value in curve.points:
            if hour > latest_hour:
                hours_ahead = _hour_distance(hour, latest_hour)
                decay = 0.5 ** (hours_ahead / decay_half_life_hours)
                value += recent_bias * decay
            adjusted_points.append((hour, value))
        adjusted_curves.append(HourlyCurve(model=curve.model, points=tuple(adjusted_points)))

    baseline_path = [observed_by_local_hour.get(hour, consensus[hour]) for hour in common_hours]
    adjusted_maps = [dict(curve.points) for curve in adjusted_curves]
    adjusted_consensus = {
        hour: median(points[hour] for points in adjusted_maps) for hour in common_hours
    }
    adjusted_path = [
        observed_by_local_hour.get(hour, adjusted_consensus[hour]) for hour in common_hours
    ]
    return NowcastAdjustment(
        curves=tuple(adjusted_curves),
        recent_bias_c=recent_bias,
        matched_hours=len(matched),
        latest_observed_hour=latest_hour,
        high_shift_c=max(adjusted_path) - max(baseline_path),
        low_shift_c=min(adjusted_path) - min(baseline_path),
    )


def _stable_seed(seed_material: str) -> int:
    return int.from_bytes(hashlib.sha256(seed_material.encode()).digest()[:8], "big")


def _standardize_extremes(
    values: list[float],
    *,
    target_mean: float,
    target_sigma: float,
) -> tuple[float, ...]:
    source_mean = fmean(values)
    source_variance = fmean((value - source_mean) ** 2 for value in values)
    source_sigma = math.sqrt(max(source_variance, 1e-9))
    return tuple(
        target_mean + (value - source_mean) * target_sigma / source_sigma for value in values
    )


def sample_daily_extremes(
    curves: list[HourlyCurve],
    *,
    high_mean_c: float,
    low_mean_c: float,
    high_sigma_c: float,
    low_sigma_c: float,
    observed_by_local_hour: dict[str, float] | None = None,
    samples: int = 512,
    rho: float = 0.85,
    seed_material: str,
) -> TrajectoryDistribution:
    if len(curves) < 2:
        raise ValueError("at least two hourly model curves are required")
    if samples < 100:
        raise ValueError("at least 100 trajectory samples are required")
    if not 0 <= rho < 1:
        raise ValueError("rho must be in [0, 1)")
    if high_sigma_c <= 0 or low_sigma_c <= 0:
        raise ValueError("target sigmas must be positive")

    point_maps = [dict(curve.points) for curve in curves]
    common_hours = sorted(set.intersection(*(set(points) for points in point_maps)))
    if len(common_hours) < 20:
        raise ValueError("model curves do not share a complete local day")
    consensus = [median(points[hour] for points in point_maps) for hour in common_hours]
    observed = observed_by_local_hour or {}
    rng = random.Random(_stable_seed(seed_material))
    innovation_scale = math.sqrt(1 - rho**2)
    raw_highs: list[float] = []
    raw_lows: list[float] = []
    for _ in range(samples):
        residual = rng.gauss(0, 1)
        path: list[float] = []
        for hour, base_c in zip(common_hours, consensus, strict=True):
            if hour in observed:
                path.append(observed[hour])
            else:
                residual = rho * residual + innovation_scale * rng.gauss(0, 1)
                path.append(base_c + residual)
        raw_highs.append(max(path))
        raw_lows.append(min(path))

    highs = _standardize_extremes(
        raw_highs, target_mean=high_mean_c, target_sigma=high_sigma_c
    )
    lows = _standardize_extremes(
        raw_lows, target_mean=low_mean_c, target_sigma=low_sigma_c
    )
    if observed:
        observed_high = max(observed.values())
        observed_low = min(observed.values())
        highs = tuple(max(value, observed_high) for value in highs)
        lows = tuple(min(value, observed_low) for value in lows)
    return TrajectoryDistribution(
        highs_c=highs,
        lows_c=lows,
        model_count=len(curves),
        hour_count=len(common_hours),
        sample_count=samples,
        rho=rho,
    )


def empirical_trajectory_probability(values_c: tuple[float, ...], bucket: Bucket) -> float:
    if not values_c:
        raise ValueError("trajectory distribution is empty")
    hits = sum(
        (bucket.low_c is None or value >= bucket.low_c)
        and (bucket.high_c is None or value < bucket.high_c)
        for value in values_c
    )
    return hits / len(values_c)
