from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import fmean

from stratus.db.models import WeatherKind
from stratus.services.probability import Bucket, gaussian_bucket_probability


@dataclass(frozen=True, slots=True)
class AR1Fit:
    intercept: float
    phi: float
    innovation_sigma: float
    sample_n: int


def fit_ar1(residuals: list[float]) -> AR1Fit | None:
    if len(residuals) < 10:
        return None
    previous = residuals[:-1]
    current = residuals[1:]
    x_mean = fmean(previous)
    y_mean = fmean(current)
    denominator = sum((value - x_mean) ** 2 for value in previous)
    phi = (
        sum((x - x_mean) * (y - y_mean) for x, y in zip(previous, current, strict=True))
        / denominator
        if denominator > 1e-12
        else 0.0
    )
    # Station residuals must not create an explosive correction.
    phi = min(0.95, max(-0.95, phi))
    intercept = y_mean - phi * x_mean
    innovations = [y - (intercept + phi * x) for x, y in zip(previous, current, strict=True)]
    sigma = math.sqrt(sum(value**2 for value in innovations) / max(len(innovations) - 1, 1))
    return AR1Fit(intercept, phi, max(0.15, sigma), len(residuals))


def ar1_adjustment(fit: AR1Fit | None, latest_residual: float | None) -> tuple[float, float]:
    if fit is None or latest_residual is None:
        return 0.0, 0.0
    return fit.intercept + fit.phi * latest_residual, fit.innovation_sigma


def constrained_extreme_probability(
    *,
    future_mean_c: float,
    future_sigma_c: float,
    observed_extreme_c: float | None,
    kind: WeatherKind,
    bucket: Bucket,
) -> float:
    """Probability of the final extreme after conditioning on observations.

    The future component represents the extreme over the *remaining* local day.
    The final high is max(observed high, future high); the final low is the
    corresponding minimum. This enforces impossible buckets at probability 0.
    """

    if observed_extreme_c is None:
        return gaussian_bucket_probability(future_mean_c, future_sigma_c, bucket)
    if future_sigma_c <= 0:
        raise ValueError("future sigma must be positive")

    def cdf(value: float | None) -> float:
        if value is None:
            return 1.0
        z = (value - future_mean_c) / (future_sigma_c * math.sqrt(2))
        return 0.5 * (1 + math.erf(z))

    low = bucket.low_c
    high = bucket.high_c
    if kind == WeatherKind.HIGH:
        if high is not None and observed_extreme_c >= high:
            return 0.0
        upper_probability = cdf(high)
        if low is None or observed_extreme_c >= low:
            return upper_probability
        return max(0.0, upper_probability - cdf(low))

    if low is not None and observed_extreme_c < low:
        return 0.0
    if high is None or observed_extreme_c < high:
        lower_cdf = cdf(low) if low is not None else 0.0
        return 1.0 - lower_cdf
    lower_cdf = cdf(low) if low is not None else 0.0
    return max(0.0, cdf(high) - lower_cdf)


@dataclass(frozen=True, slots=True)
class AnalogCase:
    features: tuple[float, ...]
    actual_c: float
    age_days: int = 0


@dataclass(frozen=True, slots=True)
class AnalogForecast:
    values_c: tuple[float, ...]
    distances: tuple[float, ...]

    @property
    def mean_c(self) -> float:
        return fmean(self.values_c)

    @property
    def sigma_c(self) -> float:
        if len(self.values_c) < 2:
            return 0.25
        mean = self.mean_c
        return max(
            0.25,
            math.sqrt(
                sum((value - mean) ** 2 for value in self.values_c) / (len(self.values_c) - 1)
            ),
        )

    def bucket_probability(self, bucket: Bucket) -> float:
        weights = [1.0 / max(distance, 0.05) for distance in self.distances]
        total = sum(weights)
        hits = sum(
            weight
            for value, weight in zip(self.values_c, weights, strict=True)
            if (bucket.low_c is None or value >= bucket.low_c)
            and (bucket.high_c is None or value < bucket.high_c)
        )
        return hits / total


def analog_ensemble(
    current_features: tuple[float, ...],
    cases: list[AnalogCase],
    *,
    neighbors: int = 30,
    feature_scales: tuple[float, ...] | None = None,
    age_half_life_days: float = 365.0,
) -> AnalogForecast | None:
    if neighbors < 2 or not cases:
        return None
    dimension = len(current_features)
    if dimension == 0 or any(len(case.features) != dimension for case in cases):
        raise ValueError("analog features must have one consistent non-zero dimension")
    scales = feature_scales or tuple(1.0 for _ in current_features)
    if len(scales) != dimension or any(scale <= 0 for scale in scales):
        raise ValueError("feature scales must be positive and match feature dimension")
    ranked: list[tuple[float, AnalogCase]] = []
    for case in cases:
        distance = math.sqrt(
            sum(
                ((left - right) / scale) ** 2
                for left, right, scale in zip(current_features, case.features, scales, strict=True)
            )
        )
        age_penalty = math.log(2) * max(case.age_days, 0) / age_half_life_days
        ranked.append((distance + age_penalty, case))
    selected = sorted(ranked, key=lambda item: item[0])[: min(neighbors, len(ranked))]
    if len(selected) < 2:
        return None
    return AnalogForecast(
        values_c=tuple(case.actual_c for _, case in selected),
        distances=tuple(distance for distance, _ in selected),
    )


@dataclass(slots=True)
class _TreeNode:
    values: tuple[float, ...]
    feature: int | None = None
    threshold: float | None = None
    left: _TreeNode | None = None
    right: _TreeNode | None = None


class QuantileRegressionForest:
    """Small dependency-free QRF challenger for station-level research.

    It stores response samples in leaves and therefore returns an empirical
    conditional distribution instead of only a point estimate.
    """

    def __init__(
        self,
        *,
        trees: int = 64,
        max_depth: int = 7,
        min_leaf: int = 8,
        feature_fraction: float = 0.7,
        seed: int = 1729,
    ) -> None:
        if trees < 1 or max_depth < 1 or min_leaf < 2:
            raise ValueError("invalid forest size")
        if not 0 < feature_fraction <= 1:
            raise ValueError("feature_fraction must be in (0, 1]")
        self.trees = trees
        self.max_depth = max_depth
        self.min_leaf = min_leaf
        self.feature_fraction = feature_fraction
        self.seed = seed
        self._roots: list[_TreeNode] = []
        self._feature_count = 0

    def fit(self, features: list[tuple[float, ...]], targets: list[float]) -> None:
        if len(features) != len(targets) or len(targets) < self.min_leaf * 2:
            raise ValueError("QRF needs aligned data and at least two leaves")
        self._feature_count = len(features[0])
        if self._feature_count == 0 or any(len(row) != self._feature_count for row in features):
            raise ValueError("QRF feature rows must have one consistent dimension")
        rng = random.Random(self.seed)
        self._roots = []
        for _ in range(self.trees):
            indices = [rng.randrange(len(targets)) for _ in targets]
            self._roots.append(self._build(features, targets, indices, 0, rng))

    def _build(
        self,
        features: list[tuple[float, ...]],
        targets: list[float],
        indices: list[int],
        depth: int,
        rng: random.Random,
    ) -> _TreeNode:
        values = tuple(targets[index] for index in indices)
        node = _TreeNode(values=values)
        if depth >= self.max_depth or len(indices) < self.min_leaf * 2:
            return node
        feature_n = max(1, round(self._feature_count * self.feature_fraction))
        candidates = rng.sample(range(self._feature_count), min(feature_n, self._feature_count))
        parent_mean = fmean(values)
        parent_loss = sum((value - parent_mean) ** 2 for value in values)
        best: tuple[float, int, float, list[int], list[int]] | None = None
        for feature in candidates:
            ordered = sorted(features[index][feature] for index in indices)
            thresholds = {
                ordered[position]
                for position in (len(ordered) // 3, len(ordered) // 2, 2 * len(ordered) // 3)
            }
            for threshold in thresholds:
                left = [index for index in indices if features[index][feature] <= threshold]
                right = [index for index in indices if features[index][feature] > threshold]
                if len(left) < self.min_leaf or len(right) < self.min_leaf:
                    continue
                left_mean = fmean(targets[index] for index in left)
                right_mean = fmean(targets[index] for index in right)
                loss = sum((targets[index] - left_mean) ** 2 for index in left) + sum(
                    (targets[index] - right_mean) ** 2 for index in right
                )
                gain = parent_loss - loss
                if best is None or gain > best[0]:
                    best = (gain, feature, threshold, left, right)
        if best is None or best[0] <= 1e-9:
            return node
        _, node.feature, node.threshold, left_indices, right_indices = best
        node.left = self._build(features, targets, left_indices, depth + 1, rng)
        node.right = self._build(features, targets, right_indices, depth + 1, rng)
        return node

    def distribution(self, features: tuple[float, ...]) -> tuple[float, ...]:
        if not self._roots or len(features) != self._feature_count:
            raise ValueError("QRF is not fitted or feature dimension is wrong")
        values: list[float] = []
        for root in self._roots:
            node = root
            while node.feature is not None:
                assert (
                    node.threshold is not None and node.left is not None and node.right is not None
                )
                node = node.left if features[node.feature] <= node.threshold else node.right
            values.extend(node.values)
        return tuple(values)

    def bucket_probability(self, features: tuple[float, ...], bucket: Bucket) -> float:
        values = self.distribution(features)
        hits = sum(
            (bucket.low_c is None or value >= bucket.low_c)
            and (bucket.high_c is None or value < bucket.high_c)
            for value in values
        )
        return hits / len(values)


@dataclass(frozen=True, slots=True)
class GaussianMixture:
    weights: tuple[float, float]
    means: tuple[float, float]
    sigmas: tuple[float, float]
    sample_n: int

    def bucket_probability(self, bucket: Bucket) -> float:
        return sum(
            weight * gaussian_bucket_probability(mean, sigma, bucket)
            for weight, mean, sigma in zip(self.weights, self.means, self.sigmas, strict=True)
        )


def fit_two_gaussian_mixture(
    values: list[float], *, iterations: int = 50
) -> GaussianMixture | None:
    if len(values) < 20:
        return None
    ordered = sorted(values)
    means = [ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3]]
    overall_mean = fmean(values)
    overall_sigma = max(
        0.25,
        math.sqrt(sum((value - overall_mean) ** 2 for value in values) / (len(values) - 1)),
    )
    sigmas = [overall_sigma, overall_sigma]
    weights = [0.5, 0.5]
    for _ in range(iterations):
        responsibilities: list[tuple[float, float]] = []
        for value in values:
            densities = [
                weight
                * math.exp(-0.5 * ((value - mean) / sigma) ** 2)
                / (sigma * math.sqrt(2 * math.pi))
                for weight, mean, sigma in zip(weights, means, sigmas, strict=True)
            ]
            total = sum(densities)
            responsibilities.append(
                (densities[0] / total, densities[1] / total) if total > 0 else (0.5, 0.5)
            )
        totals = [sum(row[index] for row in responsibilities) for index in range(2)]
        if min(totals) < 2:
            return None
        weights = [total / len(values) for total in totals]
        means = [
            sum(row[index] * value for row, value in zip(responsibilities, values, strict=True))
            / totals[index]
            for index in range(2)
        ]
        sigmas = [
            max(
                0.15,
                math.sqrt(
                    sum(
                        row[index] * (value - means[index]) ** 2
                        for row, value in zip(responsibilities, values, strict=True)
                    )
                    / totals[index]
                ),
            )
            for index in range(2)
        ]
    order = sorted(range(2), key=lambda index: means[index])
    return GaussianMixture(
        weights=(weights[order[0]], weights[order[1]]),
        means=(means[order[0]], means[order[1]]),
        sigmas=(sigmas[order[0]], sigmas[order[1]]),
        sample_n=len(values),
    )


def effective_sample_size(case_weights: list[float]) -> float:
    if not case_weights:
        return 0.0
    total = sum(case_weights)
    squared = sum(weight**2 for weight in case_weights)
    return total**2 / squared if squared > 0 else 0.0


def fit_regularized_stack(
    predictions: list[dict[str, float]],
    outcomes: list[bool],
    *,
    prior: dict[str, float] | None = None,
    l2: float = 0.05,
    learning_rate: float = 0.2,
    iterations: int = 500,
) -> dict[str, float]:
    if len(predictions) != len(outcomes) or not predictions:
        raise ValueError("stack predictions and outcomes must be non-empty and aligned")
    experts = sorted(set.intersection(*(set(row) for row in predictions)))
    if not experts:
        raise ValueError("stack rows have no common experts")
    target_prior = prior or {expert: 1 / len(experts) for expert in experts}
    prior_total = sum(max(0.0, target_prior.get(expert, 0.0)) for expert in experts)
    if prior_total <= 0:
        target_prior = {expert: 1 / len(experts) for expert in experts}
    else:
        target_prior = {
            expert: max(0.0, target_prior.get(expert, 0.0)) / prior_total for expert in experts
        }
    weights = dict(target_prior)
    for _ in range(iterations):
        gradients = {expert: 0.0 for expert in experts}
        for row, outcome in zip(predictions, outcomes, strict=True):
            blended = sum(weights[expert] * row[expert] for expert in experts)
            error = blended - float(outcome)
            for expert in experts:
                gradients[expert] += 2 * error * row[expert] / len(predictions)
        for expert in experts:
            gradients[expert] += 2 * l2 * (weights[expert] - target_prior[expert])
            weights[expert] = max(0.0, weights[expert] - learning_rate * gradients[expert])
        total = sum(weights.values())
        weights = (
            {expert: weight / total for expert, weight in weights.items()}
            if total > 0
            else dict(target_prior)
        )
    return weights


def blend_experts(probabilities: dict[str, float], weights: dict[str, float]) -> float:
    shared = set(probabilities) & set(weights)
    total = sum(weights[expert] for expert in shared)
    if total <= 0:
        raise ValueError("no positive expert weights are available")
    value = sum(probabilities[expert] * weights[expert] for expert in shared) / total
    return min(1.0, max(0.0, value))


def block_bootstrap_mean_ci(
    values_by_block: dict[str, list[float]],
    *,
    iterations: int = 2000,
    seed: int = 20260712,
) -> tuple[float, float] | None:
    if len(values_by_block) < 5 or iterations < 100:
        return None
    block_means = [fmean(values) for values in values_by_block.values() if values]
    if len(block_means) < 5:
        return None
    rng = random.Random(seed)
    samples = sorted(fmean(rng.choice(block_means) for _ in block_means) for _ in range(iterations))
    low_index = max(0, round(0.025 * (iterations - 1)))
    high_index = min(iterations - 1, round(0.975 * (iterations - 1)))
    return samples[low_index], samples[high_index]
