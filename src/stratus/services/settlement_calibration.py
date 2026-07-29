from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from statistics import fmean

from stratus.services.accuracy_models import block_bootstrap_mean_ci

_EPSILON = 1e-6


@dataclass(frozen=True, slots=True)
class SettlementEventCase:
    key: str
    probabilities: tuple[float, ...]
    outcomes: tuple[bool, ...]

    def __post_init__(self) -> None:
        if len(self.probabilities) < 2 or len(self.probabilities) != len(self.outcomes):
            raise ValueError("settlement event must contain aligned probability buckets")
        if sum(self.outcomes) != 1:
            raise ValueError("settlement event must contain exactly one winning bucket")
        if any(not 0 <= probability <= 1 for probability in self.probabilities):
            raise ValueError("bucket probability must be between zero and one")


@dataclass(frozen=True, slots=True)
class PartitionCalibrator:
    alpha: float
    beta: float
    training_blocks: int

    def calibrate(self, probabilities: tuple[float, ...]) -> tuple[float, ...]:
        return calibrate_partition(probabilities, alpha=self.alpha, beta=self.beta)


@dataclass(frozen=True, slots=True)
class CalibrationAssessment:
    calibrator: PartitionCalibrator
    blocks: int
    buckets: int
    baseline_brier: float
    calibrated_brier: float
    baseline_ece: float
    calibrated_ece: float
    delta_ci: tuple[float, float] | None


def _normalize(probabilities: tuple[float, ...]) -> tuple[float, ...]:
    clipped = tuple(min(1 - _EPSILON, max(_EPSILON, value)) for value in probabilities)
    total = sum(clipped)
    if total <= 0:
        raise ValueError("probability partition total must be positive")
    return tuple(value / total for value in clipped)


def calibrate_partition(
    probabilities: tuple[float, ...], *, alpha: float, beta: float
) -> tuple[float, ...]:
    normalized = _normalize(probabilities)
    scores = [
        alpha * math.log(value) + beta * math.log(1 - value)
        for value in normalized
    ]
    offset = max(scores)
    exponentials = [math.exp(score - offset) for score in scores]
    total = sum(exponentials)
    return tuple(value / total for value in exponentials)


def fit_partition_calibrator(
    cases: list[SettlementEventCase],
    *,
    l2: float = 0.05,
    learning_rate: float = 0.05,
    iterations: int = 800,
) -> PartitionCalibrator:
    if len(cases) < 10:
        raise ValueError("partition calibration needs at least 10 independent events")
    alpha = 1.0
    beta = 0.0
    for iteration in range(iterations):
        gradient_alpha = 0.0
        gradient_beta = 0.0
        for case in cases:
            normalized = _normalize(case.probabilities)
            log_p = [math.log(value) for value in normalized]
            log_one_minus_p = [math.log(1 - value) for value in normalized]
            calibrated = calibrate_partition(
                normalized,
                alpha=alpha,
                beta=beta,
            )
            winner = case.outcomes.index(True)
            gradient_alpha += sum(
                probability * feature
                for probability, feature in zip(calibrated, log_p, strict=True)
            ) - log_p[winner]
            gradient_beta += sum(
                probability * feature
                for probability, feature in zip(
                    calibrated, log_one_minus_p, strict=True
                )
            ) - log_one_minus_p[winner]
        count = len(cases)
        gradient_alpha = gradient_alpha / count + 2 * l2 * (alpha - 1)
        gradient_beta = gradient_beta / count + 2 * l2 * beta
        step = learning_rate / math.sqrt(1 + iteration / 100)
        alpha = min(5.0, max(0.05, alpha - step * gradient_alpha))
        beta = min(2.0, max(-2.0, beta - step * gradient_beta))
    return PartitionCalibrator(alpha=alpha, beta=beta, training_blocks=len(cases))


def event_brier(probabilities: tuple[float, ...], outcomes: tuple[bool, ...]) -> float:
    return fmean(
        (probability - float(outcome)) ** 2
        for probability, outcome in zip(probabilities, outcomes, strict=True)
    )


def expected_calibration_error(
    probabilities: list[float], outcomes: list[bool], *, bins: int = 10
) -> float:
    if len(probabilities) != len(outcomes) or not probabilities:
        raise ValueError("ECE inputs must be non-empty and aligned")
    total = len(probabilities)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        members = [
            (probability, outcome)
            for probability, outcome in zip(probabilities, outcomes, strict=True)
            if low <= probability < high or (index == bins - 1 and probability == 1)
        ]
        if not members:
            continue
        confidence = fmean(probability for probability, _ in members)
        frequency = fmean(float(outcome) for _, outcome in members)
        error += len(members) / total * abs(confidence - frequency)
    return error


def blocked_oof_assessment(
    cases: list[SettlementEventCase],
    *,
    folds: int = 5,
    l2: float = 0.05,
) -> CalibrationAssessment:
    if len(cases) < max(20, folds * 4):
        raise ValueError("OOF calibration needs at least 20 independent events")
    baseline_by_block: dict[str, float] = {}
    calibrated_by_block: dict[str, float] = {}
    flat_baseline: list[float] = []
    flat_calibrated: list[float] = []
    flat_outcomes: list[bool] = []
    for fold in range(folds):
        validation = [case for case in cases if _fold(case.key, folds) == fold]
        training = [case for case in cases if _fold(case.key, folds) != fold]
        if not validation or len(training) < 10:
            continue
        calibrator = fit_partition_calibrator(training, l2=l2)
        for case in validation:
            baseline = _normalize(case.probabilities)
            calibrated = calibrator.calibrate(baseline)
            baseline_by_block[case.key] = event_brier(baseline, case.outcomes)
            calibrated_by_block[case.key] = event_brier(calibrated, case.outcomes)
            flat_baseline.extend(baseline)
            flat_calibrated.extend(calibrated)
            flat_outcomes.extend(case.outcomes)
    if len(calibrated_by_block) != len(cases):
        raise ValueError("OOF folds did not cover every settlement event")
    delta_blocks = {
        key: [calibrated_by_block[key] - baseline]
        for key, baseline in baseline_by_block.items()
    }
    fitted = fit_partition_calibrator(cases, l2=l2)
    return CalibrationAssessment(
        calibrator=fitted,
        blocks=len(cases),
        buckets=sum(len(case.probabilities) for case in cases),
        baseline_brier=fmean(baseline_by_block.values()),
        calibrated_brier=fmean(calibrated_by_block.values()),
        baseline_ece=expected_calibration_error(flat_baseline, flat_outcomes),
        calibrated_ece=expected_calibration_error(flat_calibrated, flat_outcomes),
        delta_ci=block_bootstrap_mean_ci(delta_blocks),
    )


def _fold(key: str, folds: int) -> int:
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % folds
