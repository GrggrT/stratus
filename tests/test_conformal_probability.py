import pytest

from stratus.services.conformal_probability import (
    finite_sample_quantile,
    fit_conformal_scale,
)


def test_finite_sample_quantile_uses_corrected_rank() -> None:
    values = [float(value) for value in range(1, 11)]

    assert finite_sample_quantile(values, 0.80) == 9
    assert finite_sample_quantile(values, 0.90) == 10


def test_conformal_scale_widens_underdispersed_sigma() -> None:
    scores = [0.2 + index * 0.03 for index in range(100)]

    result = fit_conformal_scale(scores, coverage=0.90, minimum_samples=60)

    assert result is not None
    assert result.scale > 1
    assert result.empirical_coverage_after >= result.empirical_coverage_before
    assert result.empirical_coverage_after >= 0.90


def test_conformal_scale_is_bounded_and_sparse_data_stays_unfitted() -> None:
    assert fit_conformal_scale([1.0] * 59, minimum_samples=60) is None
    result = fit_conformal_scale([10.0] * 60, minimum_samples=60)

    assert result is not None
    assert result.scale == 2.5


@pytest.mark.parametrize("coverage", [0.0, 1.0])
def test_quantile_rejects_invalid_coverage(coverage: float) -> None:
    with pytest.raises(ValueError):
        finite_sample_quantile([1.0], coverage)
