import pytest

from stratus.services.emos import fit_emos, fit_horizon_sigma, horizon_bucket


def test_fit_emos_recovers_linear_correction_and_sigma_floor() -> None:
    fit = fit_emos([(1, 3), (2, 5), (3, 7), (4, 9)])
    assert fit.intercept == pytest.approx(1.0)
    assert fit.slope == pytest.approx(2.0)
    assert fit.bias_c == pytest.approx(3.5)
    assert fit.sigma_c == 0.5


def test_horizon_buckets() -> None:
    assert horizon_bucket(6) == "0-6h"
    assert horizon_bucket(24) == "6-24h"
    assert horizon_bucket(48) == "24-48h"
    assert horizon_bucket(72) == "48h+"


def test_fit_horizon_sigma_recovers_increasing_uncertainty() -> None:
    fit = fit_horizon_sigma([(24, 1.0, 100), (48, 1.5, 100), (72, 2.0, 100)])

    assert fit.intercept == pytest.approx(0.5)
    assert fit.slope == pytest.approx(1 / 48)


def test_fit_horizon_sigma_does_not_allow_decreasing_uncertainty() -> None:
    fit = fit_horizon_sigma([(24, 2.0, 100), (48, 1.5, 100), (72, 1.0, 100)])

    assert fit.intercept == pytest.approx(1.5)
    assert fit.slope == 0.0


def test_fit_horizon_sigma_validates_samples() -> None:
    with pytest.raises(ValueError, match="two"):
        fit_horizon_sigma([(24, 1.0, 100)])
