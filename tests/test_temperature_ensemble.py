from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from stratus.services.probability import Bucket
from stratus.services.temperature_ensemble import (
    SettlementAlignedCase,
    TemperatureEnsembleCase,
    fit_temperature_ensemble,
    settlement_aligned_temperature_assessment,
    walk_forward_temperature_assessment,
)

PRIOR = {"ecmwf": 0.35, "gfs": 0.40, "icon": 0.15, "gem": 0.10}


def _cases(days: int = 760) -> list[TemperatureEnsembleCase]:
    start = date(2024, 1, 1)
    cases = []
    for index in range(days):
        valid_date = start + timedelta(days=index)
        seasonal = 17 + 8 * math.sin(index * 2 * math.pi / 365.25)
        weather = 2.2 * math.sin(index * 0.71) + 0.8 * math.cos(index * 0.19)
        actual = seasonal + weather
        predictions = (
            ("ecmwf", actual + 0.25 * math.sin(index * 1.11)),
            ("gfs", actual + 2.4 * math.sin(index * 0.37) + 0.8),
            ("icon", actual + 0.45 * math.cos(index * 0.83)),
            ("gem", actual + 1.8 * math.cos(index * 0.43) - 0.5),
        )
        cases.append(
            TemperatureEnsembleCase(
                scope_key="KLGA:6-24h:high",
                block_key=f"KLGA:{valid_date.isoformat()}:high",
                valid_date=valid_date,
                predictions=predictions,
                actual_c=actual,
            )
        )
    return cases


def test_temperature_fit_is_convex_and_downweights_noisy_model() -> None:
    fit = fit_temperature_ensemble(_cases(365), prior=PRIOR)
    weights = fit.weight_mapping()

    assert sum(weights.values()) == pytest.approx(1.0)
    assert all(weight >= 0 for weight in weights.values())
    assert weights["ecmwf"] > weights["gfs"]
    assert fit.training_samples == 365
    assert fit.residual_sigma_c >= 0.5
    assert len(fit.residuals_c) == 365


def test_walk_forward_weighting_improves_without_future_training() -> None:
    assessment = walk_forward_temperature_assessment(_cases(), prior=PRIOR)

    assert assessment.samples >= 170
    assert assessment.independent_blocks == assessment.samples
    assert assessment.fitted_folds == 6
    assert assessment.candidate_rmse_c < assessment.baseline_rmse_c
    assert assessment.delta_mse < 0
    assert assessment.delta_ci is not None
    assert assessment.delta_ci[1] < 0


def test_temperature_fit_requires_real_depth() -> None:
    with pytest.raises(ValueError, match="at least 30"):
        fit_temperature_ensemble(_cases(29), prior=PRIOR)


def test_settlement_aligned_backtest_uses_prior_weather_only() -> None:
    history = _cases()
    events = []
    for weather_case in history[-60:]:
        actual = weather_case.actual_c
        events.append(
            SettlementAlignedCase(
                block_key=weather_case.block_key,
                valid_date=weather_case.valid_date,
                scope_candidates=(weather_case.scope_key,),
                predictions=weather_case.predictions,
                buckets=(
                    Bucket(low_c=None, high_c=actual - 0.75),
                    Bucket(low_c=actual - 0.75, high_c=actual + 0.75),
                    Bucket(low_c=actual + 0.75, high_c=None),
                ),
                baseline_probabilities=(1 / 3, 1 / 3, 1 / 3),
                outcomes=(False, True, False),
            )
        )

    assessment = settlement_aligned_temperature_assessment(
        history,
        events,
        prior=PRIOR,
    )

    assert assessment.available_events == 60
    assert assessment.fitted_events == 60
    assert assessment.independent_blocks == 60
    assert assessment.market_rows == 180
    assert assessment.candidate_brier < assessment.baseline_brier
    assert assessment.delta_ci is not None
    assert assessment.delta_ci[1] < 0
    assert 0 <= assessment.empirical_candidate_brier <= 1
    assert assessment.empirical_delta_ci is not None
    assert assessment.shrunk_empirical_weight == 0.25
    assert assessment.shrunk_empirical_brier < assessment.baseline_brier
    assert assessment.shrunk_empirical_delta_ci is not None
