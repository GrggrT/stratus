from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import fmean

from stratus.services.accuracy_models import block_bootstrap_mean_ci
from stratus.services.probability import Bucket, gaussian_bucket_probability

_EPSILON = 1e-9


@dataclass(frozen=True, slots=True)
class CensoredEventCase:
    """One resolved temperature event whose exact source value is unknown.

    The winning bucket is an interval-censored observation. Treating its
    midpoint as the source temperature would manufacture precision, so the
    likelihood integrates the Gaussian over the whole published interval.
    """

    key: str
    mean_c: float
    sigma_c: float
    buckets: tuple[Bucket, ...]
    baseline_probabilities: tuple[float, ...]
    outcomes: tuple[bool, ...]

    def __post_init__(self) -> None:
        if self.sigma_c <= 0:
            raise ValueError("censored event sigma must be positive")
        if len(self.buckets) < 2:
            raise ValueError("censored event needs at least two buckets")
        if not (
            len(self.buckets)
            == len(self.baseline_probabilities)
            == len(self.outcomes)
        ):
            raise ValueError("censored event inputs must be aligned")
        if sum(self.outcomes) != 1:
            raise ValueError("censored event must have exactly one winner")
        if any(not 0 <= value <= 1 for value in self.baseline_probabilities):
            raise ValueError("baseline probabilities must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class CensoredGaussianFit:
    bias_c: float
    sigma_scale: float
    training_blocks: int

    def probabilities(
        self,
        buckets: tuple[Bucket, ...],
        mean_c: float,
        sigma_c: float,
    ) -> tuple[float, ...]:
        return censored_gaussian_probabilities(
            buckets,
            mean_c=mean_c,
            sigma_c=sigma_c,
            bias_c=self.bias_c,
            sigma_scale=self.sigma_scale,
        )


@dataclass(frozen=True, slots=True)
class CensoredGaussianAssessment:
    fit: CensoredGaussianFit
    blocks: int
    buckets: int
    baseline_brier: float
    calibrated_brier: float
    delta_ci: tuple[float, float] | None


def _normalize(probabilities: tuple[float, ...]) -> tuple[float, ...]:
    clipped = tuple(max(_EPSILON, value) for value in probabilities)
    total = sum(clipped)
    if total <= 0:
        raise ValueError("probability partition total must be positive")
    return tuple(value / total for value in clipped)


def censored_gaussian_probabilities(
    buckets: tuple[Bucket, ...],
    *,
    mean_c: float,
    sigma_c: float,
    bias_c: float,
    sigma_scale: float,
) -> tuple[float, ...]:
    if sigma_c <= 0 or sigma_scale <= 0:
        raise ValueError("sigma and sigma_scale must be positive")
    adjusted_mean = mean_c + bias_c
    adjusted_sigma = sigma_c * sigma_scale
    return _normalize(
        tuple(
            gaussian_bucket_probability(adjusted_mean, adjusted_sigma, bucket)
            for bucket in buckets
        )
    )


def _loss(
    cases: list[CensoredEventCase],
    *,
    bias_c: float,
    log_sigma_scale: float,
    l2: float,
) -> float:
    sigma_scale = math.exp(log_sigma_scale)
    negative_log_likelihood = 0.0
    for case in cases:
        probabilities = censored_gaussian_probabilities(
            case.buckets,
            mean_c=case.mean_c,
            sigma_c=case.sigma_c,
            bias_c=bias_c,
            sigma_scale=sigma_scale,
        )
        winner = case.outcomes.index(True)
        negative_log_likelihood -= math.log(max(_EPSILON, probabilities[winner]))
    regularization = l2 * (bias_c**2 + log_sigma_scale**2)
    return negative_log_likelihood / len(cases) + regularization


def fit_censored_gaussian(
    cases: list[CensoredEventCase],
    *,
    l2: float = 0.03,
) -> CensoredGaussianFit:
    """Fit global source bias and dispersion without inventing exact values."""
    if len(cases) < 10:
        raise ValueError("censored Gaussian fit needs at least 10 independent events")
    best_bias = 0.0
    best_log_scale = 0.0
    best_loss = _loss(
        cases,
        bias_c=best_bias,
        log_sigma_scale=best_log_scale,
        l2=l2,
    )
    # Deterministic coarse-to-fine search. Two parameters and small event
    # counts make this both faster and easier to reproduce than an opaque
    # optimiser dependency.
    bias_radius = 3.0
    scale_radius = math.log(2.5)
    for divisions in (12, 10, 8):
        for bias_index in range(-divisions, divisions + 1):
            candidate_bias = best_bias + bias_radius * bias_index / divisions
            if not -6.0 <= candidate_bias <= 6.0:
                continue
            for scale_index in range(-divisions, divisions + 1):
                candidate_log_scale = (
                    best_log_scale + scale_radius * scale_index / divisions
                )
                if not math.log(0.35) <= candidate_log_scale <= math.log(3.0):
                    continue
                candidate_loss = _loss(
                    cases,
                    bias_c=candidate_bias,
                    log_sigma_scale=candidate_log_scale,
                    l2=l2,
                )
                if candidate_loss < best_loss:
                    best_bias = candidate_bias
                    best_log_scale = candidate_log_scale
                    best_loss = candidate_loss
        bias_radius /= 4
        scale_radius /= 4
    return CensoredGaussianFit(
        bias_c=round(best_bias, 6),
        sigma_scale=round(math.exp(best_log_scale), 6),
        training_blocks=len(cases),
    )


def _event_brier(probabilities: tuple[float, ...], outcomes: tuple[bool, ...]) -> float:
    return fmean(
        (probability - float(outcome)) ** 2
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )


def _fold(key: str, folds: int) -> int:
    digest = hashlib.sha256(key.encode()).digest()
    return int.from_bytes(digest[:8], "big") % folds


def blocked_oof_censored_assessment(
    cases: list[CensoredEventCase],
    *,
    folds: int = 5,
    l2: float = 0.03,
) -> CensoredGaussianAssessment:
    if len(cases) < max(20, folds * 4):
        raise ValueError("censored OOF assessment needs at least 20 independent events")
    baseline_by_block: dict[str, float] = {}
    calibrated_by_block: dict[str, float] = {}
    for fold in range(folds):
        validation = [case for case in cases if _fold(case.key, folds) == fold]
        training = [case for case in cases if _fold(case.key, folds) != fold]
        if not validation or len(training) < 10:
            continue
        fit = fit_censored_gaussian(training, l2=l2)
        for case in validation:
            baseline = _normalize(case.baseline_probabilities)
            calibrated = fit.probabilities(case.buckets, case.mean_c, case.sigma_c)
            baseline_by_block[case.key] = _event_brier(baseline, case.outcomes)
            calibrated_by_block[case.key] = _event_brier(calibrated, case.outcomes)
    if len(calibrated_by_block) != len(cases):
        raise ValueError("censored OOF folds did not cover every event")
    deltas = {
        key: [calibrated_by_block[key] - baseline]
        for key, baseline in baseline_by_block.items()
    }
    return CensoredGaussianAssessment(
        fit=fit_censored_gaussian(cases, l2=l2),
        blocks=len(cases),
        buckets=sum(len(case.buckets) for case in cases),
        baseline_brier=fmean(baseline_by_block.values()),
        calibrated_brier=fmean(calibrated_by_block.values()),
        delta_ci=block_bootstrap_mean_ci(deltas),
    )
