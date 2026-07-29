from __future__ import annotations

import pytest

from stratus.services.hierarchical_probability import (
    CalibrationPeer,
    partial_pool_calibration,
)
from stratus.services.probability import CalibrationParams


def _params(intercept: float, sigma: float = 1.5) -> CalibrationParams:
    return CalibrationParams(
        bias_c=intercept,
        sigma_c=sigma,
        intercept=intercept,
        slope=1.0,
        sigma_intercept=sigma,
        sigma_slope=0.01,
        source_bucket="6-24h",
    )


def test_partial_pool_calibration_shrinks_local_toward_peer_parent() -> None:
    result = partial_pool_calibration(
        CalibrationPeer(station_id=1, params=_params(2.0, 2.0), sample_n=20),
        [
            CalibrationPeer(station_id=2, params=_params(0.0, 1.0), sample_n=100),
            CalibrationPeer(station_id=3, params=_params(1.0, 1.5), sample_n=100),
        ],
        prior_samples=60,
    )

    assert result is not None
    assert result.local_weight == pytest.approx(0.25)
    assert result.params.intercept == pytest.approx(0.875)
    assert result.params.sigma_intercept == pytest.approx(1.4375)
    assert result.peer_stations == 2
    assert result.peer_sample_n == 200
    assert result.params.source_bucket == "hierarchical:6-24h"


def test_partial_pool_calibration_excludes_local_and_requires_peer_diversity() -> None:
    local = CalibrationPeer(station_id=1, params=_params(2.0), sample_n=20)
    assert (
        partial_pool_calibration(
            local,
            [
                CalibrationPeer(station_id=1, params=_params(99.0), sample_n=1000),
                CalibrationPeer(station_id=2, params=_params(0.0), sample_n=100),
            ],
            prior_samples=60,
        )
        is None
    )


def test_partial_pool_calibration_fails_closed_for_invalid_or_sparse_inputs() -> None:
    local = CalibrationPeer(station_id=1, params=_params(2.0), sample_n=0)
    assert partial_pool_calibration(local, [], prior_samples=60) is None
    with pytest.raises(ValueError, match="prior_samples"):
        partial_pool_calibration(
            CalibrationPeer(station_id=1, params=_params(2.0), sample_n=20),
            [],
            prior_samples=-1,
        )
