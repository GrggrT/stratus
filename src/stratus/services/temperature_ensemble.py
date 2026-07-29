from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import fmean

from stratus.services.accuracy_models import block_bootstrap_mean_ci
from stratus.services.probability import Bucket, gaussian_bucket_probability


@dataclass(frozen=True, slots=True)
class TemperatureEnsembleCase:
    scope_key: str
    block_key: str
    valid_date: date
    predictions: tuple[tuple[str, float], ...]
    actual_c: float

    def as_mapping(self) -> dict[str, float]:
        return dict(self.predictions)


@dataclass(frozen=True, slots=True)
class TemperatureEnsembleFit:
    models: tuple[str, ...]
    weights: tuple[float, ...]
    intercepts: tuple[float, ...]
    slopes: tuple[float, ...]
    residual_sigma_c: float
    residuals_c: tuple[float, ...]
    training_samples: int
    training_data_hash: str

    def weight_mapping(self) -> dict[str, float]:
        return dict(zip(self.models, self.weights, strict=True))

    def predict(self, predictions: Mapping[str, float]) -> float:
        missing = [model for model in self.models if model not in predictions]
        if missing:
            raise ValueError(f"temperature ensemble is missing models: {missing}")
        return sum(
            weight * (intercept + slope * predictions[model])
            for model, weight, intercept, slope in zip(
                self.models,
                self.weights,
                self.intercepts,
                self.slopes,
                strict=True,
            )
        )


@dataclass(frozen=True, slots=True)
class TemperatureEnsembleAssessment:
    samples: int
    independent_blocks: int
    fitted_folds: int
    baseline_rmse_c: float
    candidate_rmse_c: float
    delta_mse: float
    delta_ci: tuple[float, float] | None


@dataclass(frozen=True, slots=True)
class SettlementAlignedCase:
    block_key: str
    valid_date: date
    scope_candidates: tuple[str, ...]
    predictions: tuple[tuple[str, float], ...]
    buckets: tuple[Bucket, ...]
    baseline_probabilities: tuple[float, ...]
    outcomes: tuple[bool, ...]

    def as_mapping(self) -> dict[str, float]:
        return dict(self.predictions)


@dataclass(frozen=True, slots=True)
class SettlementAlignedAssessment:
    available_events: int
    fitted_events: int
    independent_blocks: int
    market_rows: int
    baseline_brier: float
    candidate_brier: float
    delta_brier: float
    delta_ci: tuple[float, float] | None
    empirical_candidate_brier: float
    empirical_delta_brier: float
    empirical_delta_ci: tuple[float, float] | None
    shrunk_empirical_weight: float
    shrunk_empirical_brier: float
    shrunk_empirical_delta_brier: float
    shrunk_empirical_delta_ci: tuple[float, float] | None


def _normalized_prior(models: tuple[str, ...], prior: Mapping[str, float]) -> list[float]:
    values = [max(0.0, prior.get(model, 0.0)) for model in models]
    total = sum(values)
    if total <= 0:
        return [1 / len(models)] * len(models)
    return [value / total for value in values]


def _affine_correction(values: list[tuple[float, float]]) -> tuple[float, float]:
    forecast_mean = fmean(forecast for forecast, _ in values)
    actual_mean = fmean(actual for _, actual in values)
    denominator = sum((forecast - forecast_mean) ** 2 for forecast, _ in values)
    slope = (
        sum(
            (forecast - forecast_mean) * (actual - actual_mean)
            for forecast, actual in values
        )
        / denominator
        if denominator > 1e-9
        else 1.0
    )
    # A wildly extrapolating affine fit is not a weather calibration.
    slope = min(1.75, max(0.25, slope))
    return actual_mean - slope * forecast_mean, slope


def _project_simplex(values: list[float]) -> list[float]:
    """Euclidean projection onto non-negative weights summing to one."""
    ordered = sorted(values, reverse=True)
    cumulative = 0.0
    rho = 0
    for index, value in enumerate(ordered, start=1):
        cumulative += value
        if value - (cumulative - 1.0) / index > 0:
            rho = index
    if rho == 0:
        return [1 / len(values)] * len(values)
    threshold = (sum(ordered[:rho]) - 1.0) / rho
    projected = [max(0.0, value - threshold) for value in values]
    total = sum(projected)
    return [value / total for value in projected]


def fit_temperature_ensemble(
    cases: list[TemperatureEnsembleCase],
    *,
    prior: Mapping[str, float],
    l2: float = 0.15,
    iterations: int = 500,
) -> TemperatureEnsembleFit:
    if len(cases) < 30:
        raise ValueError("temperature ensemble fit needs at least 30 cases")
    model_sets = [set(case.as_mapping()) for case in cases]
    models = tuple(sorted(set.intersection(*model_sets)))
    if len(models) < 2:
        raise ValueError("temperature ensemble needs at least two common models")
    prior_weights = _normalized_prior(models, prior)
    corrections = []
    for model in models:
        corrections.append(
            _affine_correction(
                [
                    (case.as_mapping()[model], case.actual_c)
                    for case in cases
                ]
            )
        )
    corrected_rows = [
        [
            intercept + slope * case.as_mapping()[model]
            for model, (intercept, slope) in zip(models, corrections, strict=True)
        ]
        for case in cases
    ]
    size = len(models)
    gram = [
        [
            fmean(row[left] * row[right] for row in corrected_rows)
            for right in range(size)
        ]
        for left in range(size)
    ]
    cross = [
        fmean(row[index] * case.actual_c for row, case in zip(corrected_rows, cases, strict=True))
        for index in range(size)
    ]
    # The quadratic objective has a fixed Hessian. Precomputing sufficient
    # statistics makes hundreds of fits cheap enough for a daily walk-forward
    # audit without blocking the scheduler.
    lipschitz_bound = 2 * (
        max(sum(abs(value) for value in row) for row in gram) + l2
    )
    step = 0.9 / max(lipschitz_bound, 1e-9)
    weights = list(prior_weights)
    for _ in range(iterations):
        gradient = []
        for left in range(size):
            fitted_cross = sum(gram[left][right] * weights[right] for right in range(size))
            gradient.append(
                2 * (fitted_cross - cross[left])
                + 2 * l2 * (weights[left] - prior_weights[left])
            )
        weights = _project_simplex(
            [weight - step * value for weight, value in zip(weights, gradient, strict=True)]
        )
    residuals = [
        sum(weight * value for weight, value in zip(weights, row, strict=True))
        - case.actual_c
        for row, case in zip(corrected_rows, cases, strict=True)
    ]
    training_payload = [
        {
            "scope_key": case.scope_key,
            "block_key": case.block_key,
            "valid_date": case.valid_date.isoformat(),
            "predictions": case.predictions,
            "actual_c": case.actual_c,
        }
        for case in sorted(
            cases,
            key=lambda item: (item.valid_date, item.scope_key, item.block_key),
        )
    ]
    training_data_hash = hashlib.sha256(
        json.dumps(
            training_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return TemperatureEnsembleFit(
        models=models,
        weights=tuple(round(value, 8) for value in weights),
        intercepts=tuple(round(value[0], 8) for value in corrections),
        slopes=tuple(round(value[1], 8) for value in corrections),
        residual_sigma_c=round(
            max(0.5, math.sqrt(fmean(residual**2 for residual in residuals))),
            6,
        ),
        residuals_c=tuple(round(value, 6) for value in residuals),
        training_samples=len(cases),
        training_data_hash=training_data_hash,
    )


def residual_kernel_bandwidth(residuals_c: tuple[float, ...]) -> float:
    if len(residuals_c) < 2:
        return 0.25
    ordered = sorted(residuals_c)
    mean = fmean(ordered)
    standard_deviation = math.sqrt(
        sum((value - mean) ** 2 for value in ordered) / (len(ordered) - 1)
    )
    q1 = ordered[round(0.25 * (len(ordered) - 1))]
    q3 = ordered[round(0.75 * (len(ordered) - 1))]
    robust_scale = (q3 - q1) / 1.34
    scale = min(standard_deviation, robust_scale) if robust_scale > 0 else standard_deviation
    return float(max(0.15, 0.9 * scale * len(ordered) ** (-0.2)))


def residual_kernel_bucket_probability(
    fit: TemperatureEnsembleFit,
    predictions: Mapping[str, float],
    bucket: Bucket,
) -> float:
    if not fit.residuals_c:
        return gaussian_bucket_probability(
            fit.predict(predictions),
            fit.residual_sigma_c,
            bucket,
        )
    predicted_mean = fit.predict(predictions)
    bandwidth = residual_kernel_bandwidth(fit.residuals_c)
    return fmean(
        gaussian_bucket_probability(
            predicted_mean - residual,
            bandwidth,
            bucket,
        )
        for residual in fit.residuals_c
    )


def prior_temperature_prediction(
    fit: TemperatureEnsembleFit,
    predictions: Mapping[str, float],
    prior: Mapping[str, float],
) -> float:
    weights = _normalized_prior(fit.models, prior)
    return sum(
        weight * (intercept + slope * predictions[model])
        for model, weight, intercept, slope in zip(
            fit.models,
            weights,
            fit.intercepts,
            fit.slopes,
            strict=True,
        )
    )


def walk_forward_temperature_assessment(
    cases: list[TemperatureEnsembleCase],
    *,
    prior: Mapping[str, float],
    training_days: int = 365,
    validation_days: int = 180,
    fold_days: int = 30,
    minimum_training_samples: int = 180,
    l2: float = 0.15,
) -> TemperatureEnsembleAssessment:
    if training_days <= 0 or validation_days <= 0 or fold_days <= 0:
        raise ValueError("walk-forward windows must be positive")
    by_scope: dict[str, list[TemperatureEnsembleCase]] = defaultdict(list)
    for case in cases:
        by_scope[case.scope_key].append(case)
    baseline_errors = []
    candidate_errors = []
    deltas_by_block: dict[str, list[float]] = defaultdict(list)
    fitted_folds = 0
    for scope_cases in by_scope.values():
        ordered = sorted(scope_cases, key=lambda item: item.valid_date)
        if not ordered:
            continue
        validation_start = ordered[-1].valid_date - timedelta(days=validation_days - 1)
        offset = 0
        while offset < validation_days:
            fold_start = validation_start + timedelta(days=offset)
            fold_end = fold_start + timedelta(days=fold_days)
            training_start = fold_start - timedelta(days=training_days)
            training = [
                case
                for case in ordered
                if training_start <= case.valid_date < fold_start
            ]
            validation = [
                case
                for case in ordered
                if fold_start <= case.valid_date < fold_end
            ]
            offset += fold_days
            if len(training) < minimum_training_samples or not validation:
                continue
            fit = fit_temperature_ensemble(training, prior=prior, l2=l2)
            fitted_folds += 1
            for case in validation:
                predictions = case.as_mapping()
                baseline = prior_temperature_prediction(fit, predictions, prior)
                candidate = fit.predict(predictions)
                baseline_error = (baseline - case.actual_c) ** 2
                candidate_error = (candidate - case.actual_c) ** 2
                baseline_errors.append(baseline_error)
                candidate_errors.append(candidate_error)
                deltas_by_block[case.block_key].append(candidate_error - baseline_error)
    if not candidate_errors:
        raise ValueError("walk-forward assessment produced no validation cases")
    baseline_mse = fmean(baseline_errors)
    candidate_mse = fmean(candidate_errors)
    return TemperatureEnsembleAssessment(
        samples=len(candidate_errors),
        independent_blocks=len(deltas_by_block),
        fitted_folds=fitted_folds,
        baseline_rmse_c=math.sqrt(baseline_mse),
        candidate_rmse_c=math.sqrt(candidate_mse),
        delta_mse=candidate_mse - baseline_mse,
        delta_ci=block_bootstrap_mean_ci(deltas_by_block),
    )


def settlement_aligned_temperature_assessment(
    history: list[TemperatureEnsembleCase],
    events: list[SettlementAlignedCase],
    *,
    prior: Mapping[str, float],
    training_days: int = 365,
    minimum_training_samples: int = 180,
    l2: float = 0.15,
    empirical_blend_weight: float = 0.25,
) -> SettlementAlignedAssessment:
    """Score historical point-in-time forecasts against resolved market buckets.

    Fits use only weather dates strictly before each market date. Multiple
    horizon snapshots for the same station/date/kind remain one bootstrap
    block because they share the same realised weather outcome.
    """
    if (
        training_days <= 0
        or minimum_training_samples < 30
        or not 0 <= empirical_blend_weight <= 1
    ):
        raise ValueError("settlement-aligned training requirements are invalid")
    history_by_scope: dict[str, list[TemperatureEnsembleCase]] = defaultdict(list)
    for case in history:
        history_by_scope[case.scope_key].append(case)
    for cases in history_by_scope.values():
        cases.sort(key=lambda item: item.valid_date)

    fit_cache: dict[tuple[str, date], TemperatureEnsembleFit | None] = {}
    baseline_errors: list[float] = []
    candidate_errors: list[float] = []
    empirical_errors: list[float] = []
    shrunk_empirical_errors: list[float] = []
    deltas_by_block: dict[str, list[float]] = defaultdict(list)
    empirical_deltas_by_block: dict[str, list[float]] = defaultdict(list)
    shrunk_empirical_deltas_by_block: dict[str, list[float]] = defaultdict(list)
    fitted_events = 0
    for event in events:
        fit = None
        training_start = event.valid_date - timedelta(days=training_days)
        for scope_key in event.scope_candidates:
            cache_key = (scope_key, event.valid_date)
            if cache_key not in fit_cache:
                training = [
                    case
                    for case in history_by_scope.get(scope_key, [])
                    if training_start <= case.valid_date < event.valid_date
                ]
                fit_cache[cache_key] = (
                    fit_temperature_ensemble(training, prior=prior, l2=l2)
                    if len(training) >= minimum_training_samples
                    else None
                )
            fit = fit_cache[cache_key]
            if fit is not None:
                break
        if fit is None:
            continue
        raw_predictions = event.as_mapping()
        if any(model not in raw_predictions for model in fit.models):
            continue
        candidate_mean = fit.predict(raw_predictions)
        candidate_raw = [
            gaussian_bucket_probability(
                candidate_mean,
                fit.residual_sigma_c,
                bucket,
            )
            for bucket in event.buckets
        ]
        empirical_raw = [
            residual_kernel_bucket_probability(fit, raw_predictions, bucket)
            for bucket in event.buckets
        ]
        candidate_total = sum(candidate_raw)
        empirical_total = sum(empirical_raw)
        baseline_total = sum(event.baseline_probabilities)
        if candidate_total <= 0 or empirical_total <= 0 or baseline_total <= 0:
            continue
        candidate_probabilities = [value / candidate_total for value in candidate_raw]
        empirical_probabilities = [value / empirical_total for value in empirical_raw]
        baseline_probabilities = [
            value / baseline_total for value in event.baseline_probabilities
        ]
        fitted_events += 1
        for baseline, candidate, empirical, outcome in zip(
            baseline_probabilities,
            candidate_probabilities,
            empirical_probabilities,
            event.outcomes,
            strict=True,
        ):
            target = float(outcome)
            baseline_error = (baseline - target) ** 2
            candidate_error = (candidate - target) ** 2
            empirical_error = (empirical - target) ** 2
            shrunk_empirical = (
                (1 - empirical_blend_weight) * baseline
                + empirical_blend_weight * empirical
            )
            shrunk_empirical_error = (shrunk_empirical - target) ** 2
            baseline_errors.append(baseline_error)
            candidate_errors.append(candidate_error)
            empirical_errors.append(empirical_error)
            shrunk_empirical_errors.append(shrunk_empirical_error)
            deltas_by_block[event.block_key].append(candidate_error - baseline_error)
            empirical_deltas_by_block[event.block_key].append(
                empirical_error - baseline_error
            )
            shrunk_empirical_deltas_by_block[event.block_key].append(
                shrunk_empirical_error - baseline_error
            )
    if not candidate_errors:
        raise ValueError("settlement-aligned assessment produced no fitted events")
    baseline_brier = fmean(baseline_errors)
    candidate_brier = fmean(candidate_errors)
    empirical_candidate_brier = fmean(empirical_errors)
    shrunk_empirical_brier = fmean(shrunk_empirical_errors)
    return SettlementAlignedAssessment(
        available_events=len(events),
        fitted_events=fitted_events,
        independent_blocks=len(deltas_by_block),
        market_rows=len(candidate_errors),
        baseline_brier=baseline_brier,
        candidate_brier=candidate_brier,
        delta_brier=candidate_brier - baseline_brier,
        delta_ci=block_bootstrap_mean_ci(deltas_by_block),
        empirical_candidate_brier=empirical_candidate_brier,
        empirical_delta_brier=empirical_candidate_brier - baseline_brier,
        empirical_delta_ci=block_bootstrap_mean_ci(empirical_deltas_by_block),
        shrunk_empirical_weight=empirical_blend_weight,
        shrunk_empirical_brier=shrunk_empirical_brier,
        shrunk_empirical_delta_brier=shrunk_empirical_brier - baseline_brier,
        shrunk_empirical_delta_ci=block_bootstrap_mean_ci(
            shrunk_empirical_deltas_by_block
        ),
    )
