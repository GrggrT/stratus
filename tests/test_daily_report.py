from datetime import UTC, datetime, timedelta

import pytest

from stratus.services.telegram_reports import largest_model_errors


def test_largest_model_errors_deduplicates_snapshots_and_orders_absolute_error() -> None:
    older = datetime(2026, 7, 5, 12, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    rows = [
        ("KLGA", "max", 30.0, older, {"gfs": {"mean_c": 20.0}}),
        (
            "KLGA",
            "max",
            30.0,
            newer,
            {
                "gfs": {"mean_c": 28.0},
                "ecmwf_ens": {"mean_c": 35.0},
                "blend": {"mean_c": 31.0},
                "climatology": {"mean_c": 100.0},
            },
        ),
        ("EGLC", "min", 20.0, newer, {"icon": {"mean_c": 19.5}}),
    ]

    errors = largest_model_errors(rows)

    assert [(error.icao, error.target, error.model) for error in errors] == [
        ("KLGA", "max", "ecmwf_ens"),
        ("KLGA", "max", "gfs"),
        ("KLGA", "max", "blend"),
        ("EGLC", "min", "icon"),
    ]
    assert errors[0].error_c == pytest.approx(5.0)
    assert errors[1].error_c == pytest.approx(-2.0)


def test_largest_model_errors_validates_limit() -> None:
    with pytest.raises(ValueError, match="limit"):
        largest_model_errors([], limit=0)
