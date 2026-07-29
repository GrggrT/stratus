from __future__ import annotations

from datetime import date, timedelta

import pytest

from stratus.services.probability import Bucket
from stratus.services.regime_probability import (
    DatedResidual,
    apply_regime_shift_to_bucket,
    detect_regime_shift,
)


def _observations(as_of: date, baseline: float, recent: float) -> list[DatedResidual]:
    return [
        DatedResidual(as_of - timedelta(days=day), recent if day <= 30 else baseline)
        for day in range(1, 121)
    ]


def test_detect_regime_shift_is_deterministic_and_uses_daily_blocks() -> None:
    as_of = date(2026, 7, 13)
    observations = _observations(as_of, baseline=0.1, recent=-1.4)
    observations.append(DatedResidual(as_of - timedelta(days=5), -20.0))

    first = detect_regime_shift(observations, as_of=as_of, seed=17)
    second = detect_regime_shift(observations, as_of=as_of, seed=17)

    assert first == second
    assert first is not None and first.detected is True
    assert first.boundary_kind == "rolling_change_point"
    assert first.baseline_n == 90
    assert first.recent_n == 30
    assert first.shift_c == pytest.approx(-1.5)
    assert first.adjustment_c == pytest.approx(-1.5)
    assert first.ci_high_c < 0


def test_known_recent_version_boundary_takes_priority() -> None:
    as_of = date(2026, 7, 13)
    boundary = date(2026, 5, 12)
    observations = [
        DatedResidual(as_of - timedelta(days=day), -1.0 if day <= 61 else 0.5)
        for day in range(1, 151)
    ]

    result = detect_regime_shift(
        observations,
        as_of=as_of,
        known_version_date=boundary,
        seed=19,
    )

    assert result is not None and result.detected is True
    assert result.boundary_kind == "known_model_version"
    assert result.boundary_date == boundary
    assert result.recent_n == 62


def test_regime_shift_fails_closed_when_sparse_or_not_significant() -> None:
    as_of = date(2026, 7, 13)
    assert detect_regime_shift([], as_of=as_of) is None
    stable = detect_regime_shift(_observations(as_of, 0.1, 0.1), as_of=as_of)
    assert stable is not None and stable.detected is False


def test_bucket_shift_preserves_shape_delta_and_bounds_probability() -> None:
    bucket = Bucket(low_c=20.0, high_c=None)
    increased = apply_regime_shift_to_bucket(
        probability=0.6,
        mean_c=19.0,
        sigma_c=1.0,
        adjustment_c=1.0,
        bucket=bucket,
    )
    assert increased > 0.6
    assert 0 <= increased <= 1
