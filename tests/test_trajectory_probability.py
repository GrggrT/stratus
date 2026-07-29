import pytest

from stratus.services.probability import Bucket
from stratus.services.trajectory_probability import (
    HourlyCurve,
    build_remaining_day_nowcast,
    empirical_trajectory_probability,
    sample_daily_extremes,
)


def _curve(model: str, offset: float) -> HourlyCurve:
    return HourlyCurve(
        model=model,
        points=tuple(
            (f"2026-07-12T{hour:02d}:00", 14 + offset + 8 * abs(12 - hour) / -12)
            for hour in range(24)
        ),
    )


def test_trajectory_sampler_is_reproducible_and_matches_target_moments() -> None:
    kwargs = {
        "high_mean_c": 25.0,
        "low_mean_c": 12.0,
        "high_sigma_c": 1.8,
        "low_sigma_c": 1.2,
        "samples": 512,
        "rho": 0.8,
        "seed_material": "KLGA:2026-07-12:12h",
    }

    first = sample_daily_extremes([_curve("ecmwf", 0), _curve("gfs", 1)], **kwargs)
    second = sample_daily_extremes([_curve("ecmwf", 0), _curve("gfs", 1)], **kwargs)

    assert first == second
    assert sum(first.highs_c) / len(first.highs_c) == pytest.approx(25.0)
    assert sum(first.lows_c) / len(first.lows_c) == pytest.approx(12.0)
    assert first.hour_count == 24


def test_observations_make_impossible_extremes_impossible() -> None:
    distribution = sample_daily_extremes(
        [_curve("ecmwf", 0), _curve("gfs", 1)],
        high_mean_c=25,
        low_mean_c=12,
        high_sigma_c=2,
        low_sigma_c=2,
        observed_by_local_hour={
            "2026-07-12T05:00": 8.0,
            "2026-07-12T14:00": 29.0,
        },
        seed_material="observed",
    )

    assert min(distribution.highs_c) >= 29.0
    assert max(distribution.lows_c) <= 8.0
    assert empirical_trajectory_probability(
        distribution.highs_c, Bucket(low_c=None, high_c=28.5)
    ) == 0


def test_bucket_partition_sums_to_one() -> None:
    distribution = sample_daily_extremes(
        [_curve("ecmwf", 0), _curve("gfs", 1)],
        high_mean_c=25,
        low_mean_c=12,
        high_sigma_c=2,
        low_sigma_c=2,
        seed_material="partition",
    )
    buckets = [
        Bucket(None, 23),
        Bucket(23, 25),
        Bucket(25, 27),
        Bucket(27, None),
    ]

    assert sum(
        empirical_trajectory_probability(distribution.highs_c, bucket)
        for bucket in buckets
    ) == pytest.approx(1.0)


def test_remaining_day_nowcast_moves_future_extreme_with_recent_bias() -> None:
    curves = [
        HourlyCurve(
            model=model,
            points=tuple(
                (f"2026-07-12T{hour:02d}:00", 10 + hour + offset)
                for hour in range(24)
            ),
        )
        for model, offset in (("ecmwf", 0.0), ("gfs", 0.4))
    ]
    observed = {
        f"2026-07-12T{hour:02d}:00": 12 + hour + (8 if hour == 1 else 0)
        for hour in range(4)
    }

    adjustment = build_remaining_day_nowcast(curves, observed)

    assert adjustment is not None
    assert adjustment.matched_hours == 4
    assert adjustment.recent_bias_c == pytest.approx(1.8, abs=0.3)
    assert adjustment.high_shift_c > 0
    assert adjustment.low_shift_c == 0


def test_remaining_day_nowcast_requires_enough_matched_hours() -> None:
    curves = [_curve("ecmwf", 0), _curve("gfs", 1)]

    assert (
        build_remaining_day_nowcast(
            curves,
            {"2026-07-12T00:00": 12, "2026-07-12T01:00": 13},
        )
        is None
    )
