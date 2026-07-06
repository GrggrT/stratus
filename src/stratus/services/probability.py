from __future__ import annotations

import math
from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Bucket:
    low_c: float | None
    high_c: float | None


@dataclass(frozen=True, slots=True)
class CalibrationParams:
    bias_c: float
    sigma_c: float
    intercept: float
    slope: float
    sigma_intercept: float
    sigma_slope: float
    source_bucket: str


@dataclass(frozen=True, slots=True)
class ProbabilityComponent:
    name: str
    probability: float
    mean_c: float
    sigma_c: float
    weight: float
    is_ensemble: bool
    sample_n: int
    calibration_bucket: str
    outlier: bool = False


@dataclass(frozen=True, slots=True)
class ProbabilityResult:
    probability: float
    sigma_c: float
    mean_c: float
    components: list[ProbabilityComponent]
    climate_probability: float | None
    climate_weight: float
    climate_sample_n: int


@dataclass(frozen=True, slots=True)
class ComponentScore:
    brier: float
    sample_n: int


def component_brier_scores(
    rows: list[tuple[int, bool, dict[str, object]]],
) -> dict[tuple[int, str, str], ComponentScore]:
    errors: dict[tuple[int, str, str], list[float]] = {}
    for station_id, outcome, details in rows:
        blend = details.get("blend")
        if not isinstance(blend, dict):
            continue
        horizon_bucket = blend.get("target_calibration_bucket")
        if not isinstance(horizon_bucket, str):
            continue
        for name, component in details.items():
            if name in {"blend", "climatology"} or not isinstance(component, dict):
                continue
            probability = component.get("probability")
            if not isinstance(probability, int | float) or not 0 <= probability <= 1:
                continue
            key = (station_id, horizon_bucket, name)
            errors.setdefault(key, []).append((float(probability) - float(outcome)) ** 2)
    return {
        key: ComponentScore(sum(values) / len(values), len(values))
        for key, values in errors.items()
    }


def apply_adaptive_weights(
    components: list[ProbabilityComponent],
    scores: dict[str, ComponentScore | None],
    *,
    min_samples: int = 30,
    k: float = 5.0,
) -> tuple[list[ProbabilityComponent], bool]:
    if min_samples < 1:
        raise ValueError("min_samples must be positive")
    if k <= 0:
        raise ValueError("k must be positive")
    complete_scores = {
        component.name: scores.get(component.name) for component in components
    }
    if not components or any(
        score is None or score.sample_n < min_samples
        for score in complete_scores.values()
    ):
        return components, False

    raw_weights = {
        name: math.exp(-k * score.brier)
        for name, score in complete_scores.items()
        if score is not None
    }
    total = sum(raw_weights.values())
    return (
        [
            replace(component, weight=raw_weights[component.name] / total)
            for component in components
        ],
        True,
    )


def gaussian_bucket_probability(
    mean_c: float, sigma_c: float, bucket: Bucket
) -> float:
    if sigma_c <= 0:
        raise ValueError("sigma must be positive")

    def cdf(value: float | None, *, upper: bool) -> float:
        if value is None:
            return 1.0 if upper else 0.0
        z = (value - mean_c) / (sigma_c * math.sqrt(2))
        return 0.5 * (1 + math.erf(z))

    probability = cdf(bucket.high_c, upper=True) - cdf(bucket.low_c, upper=False)
    return min(1.0, max(0.0, probability))


def deterministic_component(
    *,
    name: str,
    raw_temp_c: float,
    calibration: CalibrationParams,
    horizon_h: float,
    bucket: Bucket,
    weight: float,
) -> ProbabilityComponent:
    mean_c = calibration.intercept + calibration.slope * raw_temp_c
    sigma_c = max(
        0.5,
        calibration.sigma_intercept + calibration.sigma_slope * max(horizon_h, 0),
    )
    return ProbabilityComponent(
        name=name,
        probability=gaussian_bucket_probability(mean_c, sigma_c, bucket),
        mean_c=mean_c,
        sigma_c=sigma_c,
        weight=weight,
        is_ensemble=False,
        sample_n=1,
        calibration_bucket=calibration.source_bucket,
    )


def ensemble_component(
    *,
    name: str,
    member_values_c: list[float],
    calibration: CalibrationParams,
    bucket: Bucket,
    weight: float,
) -> ProbabilityComponent:
    if len(member_values_c) < 2:
        raise ValueError("at least two ensemble members are required")
    corrected = [value + calibration.bias_c for value in member_values_c]
    mean_c = sum(corrected) / len(corrected)
    sigma_c = math.sqrt(
        sum((value - mean_c) ** 2 for value in corrected) / (len(corrected) - 1)
    )
    hits = sum(
        (bucket.low_c is None or value >= bucket.low_c)
        and (bucket.high_c is None or value < bucket.high_c)
        for value in corrected
    )
    return ProbabilityComponent(
        name=name,
        probability=hits / len(corrected),
        mean_c=mean_c,
        sigma_c=max(sigma_c, 0.25),
        weight=weight,
        is_ensemble=True,
        sample_n=len(corrected),
        calibration_bucket=calibration.source_bucket,
    )


def empirical_bucket_probability(values_c: list[float], bucket: Bucket) -> float:
    if not values_c:
        raise ValueError("climatology sample is empty")
    hits = sum(
        (bucket.low_c is None or value >= bucket.low_c)
        and (bucket.high_c is None or value < bucket.high_c)
        for value in values_c
    )
    return hits / len(values_c)


def blend_probabilities(
    components: list[ProbabilityComponent],
    *,
    horizon_h: float,
    climate_values_c: list[float] | None = None,
    bucket: Bucket,
) -> ProbabilityResult:
    if not components:
        raise ValueError("at least one probability component is required")
    adjusted = list(components)
    ensemble_components = [component for component in components if component.is_ensemble]
    if ensemble_components:
        ensemble_weight = sum(component.weight for component in ensemble_components)
        ensemble_mean = sum(
            component.weight * component.mean_c for component in ensemble_components
        ) / ensemble_weight
        ensemble_variance = sum(
            component.weight
            * (component.sigma_c**2 + (component.mean_c - ensemble_mean) ** 2)
            for component in ensemble_components
        ) / ensemble_weight
        ensemble_sigma = math.sqrt(max(ensemble_variance, 0.0))
        adjusted = [
            replace(component, weight=component.weight * 0.5, outlier=True)
            if not component.is_ensemble
            and ensemble_sigma > 0
            and abs(component.mean_c - ensemble_mean) > 1.5 * ensemble_sigma
            else component
            for component in components
        ]

    total_weight = sum(component.weight for component in adjusted)
    normalized = [
        replace(component, weight=component.weight / total_weight)
        for component in adjusted
    ]
    model_probability = sum(
        component.weight * component.probability for component in normalized
    )
    model_mean = sum(component.weight * component.mean_c for component in normalized)
    model_variance = sum(
        component.weight * (component.sigma_c**2 + component.mean_c**2)
        for component in normalized
    ) - model_mean**2

    climate_probability = None
    climate_weight = 0.0
    climate_sample_n = 0
    final_probability = model_probability
    final_mean = model_mean
    final_variance = max(model_variance, 0.0)
    if climate_values_c:
        climate_probability = empirical_bucket_probability(climate_values_c, bucket)
        climate_weight = min(0.3, max(horizon_h, 0.0) / 240.0)
        climate_sample_n = len(climate_values_c)
        climate_mean = sum(climate_values_c) / climate_sample_n
        climate_variance = sum(
            (value - climate_mean) ** 2 for value in climate_values_c
        ) / max(climate_sample_n - 1, 1)
        final_probability = (
            (1 - climate_weight) * model_probability
            + climate_weight * climate_probability
        )
        final_mean = (1 - climate_weight) * model_mean + climate_weight * climate_mean
        final_variance = (
            (1 - climate_weight) * (model_variance + model_mean**2)
            + climate_weight * (climate_variance + climate_mean**2)
            - final_mean**2
        )
    return ProbabilityResult(
        probability=min(1.0, max(0.0, final_probability)),
        sigma_c=math.sqrt(max(final_variance, 0.0625)),
        mean_c=final_mean,
        components=normalized,
        climate_probability=climate_probability,
        climate_weight=climate_weight,
        climate_sample_n=climate_sample_n,
    )
