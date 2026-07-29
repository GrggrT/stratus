from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest

from stratus.collectors.open_meteo import ForecastTrajectoryValue, ForecastValue
from stratus.db.models import WeatherKind
from stratus.services.forecast_provenance import (
    MaterializedForecastLike,
    build_forecast_manifest,
    build_model_input_provenance,
    canonical_forecast_temp_c,
    canonical_trajectory_temp_c,
    manifest_run_times,
    manifest_valid_dates,
    model_input_provenance_is_complete,
)

RUN_TIME = datetime(2026, 7, 15, 6, tzinfo=UTC)


def _forecast(*, station_id: int = 7, model: str = "gfs") -> ForecastValue:
    return ForecastValue(
        station_id=station_id,
        model=model,
        run_time=RUN_TIME,
        valid_date=date(2026, 7, 16),
        kind=WeatherKind.HIGH,
        temp_c=Decimal("23.40"),
        fetched_at=RUN_TIME,
    )


def test_manifest_is_deterministic_and_excludes_runtime_database_ids() -> None:
    first = _forecast(model="gfs")
    second = replace(
        first,
        model="ecmwf",
        kind=WeatherKind.LOW,
        temp_c=Decimal("11.20"),
        run_id=99,
        ingest_id=101,
        available_at=RUN_TIME,
    )
    trajectory = ForecastTrajectoryValue(
        station_id=7,
        model="gfs",
        run_time=RUN_TIME,
        valid_date=date(2026, 7, 16),
        points=(("2026-07-16T00:00", Decimal("12.10")),),
        fetched_at=RUN_TIME,
        run_id=99,
        ingest_id=101,
        available_at=RUN_TIME,
    )

    manifest = build_forecast_manifest([first, second], [trajectory])
    reversed_manifest = build_forecast_manifest([second, first], [trajectory])

    assert manifest == reversed_manifest
    assert manifest["forecast_count"] == 2
    assert manifest["forecasts"][0]["model"] == "ecmwf"
    assert manifest["forecasts"][1]["temp_c"] == "23.40"
    assert "run_id" not in manifest["forecasts"][0]
    assert "ingest_id" not in manifest["forecasts"][0]
    assert manifest_run_times(manifest) == {RUN_TIME}
    assert manifest_valid_dates(manifest) == {date(2026, 7, 16)}


def test_manifest_rejects_mixed_stations_and_empty_payloads() -> None:
    with pytest.raises(ValueError, match="cannot be empty"):
        build_forecast_manifest([], [])
    with pytest.raises(ValueError, match="exactly one station"):
        build_forecast_manifest([_forecast(), _forecast(station_id=8)], [])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("21.11111111111111111111111111"), Decimal("21.11")),
        (Decimal("21.125"), Decimal("21.13")),
        (Decimal("-21.125"), Decimal("-21.13")),
        (Decimal("-0.004"), Decimal("0.00")),
    ],
)
def test_forecast_temperature_canonicalization_matches_postgres_numeric_scale(
    raw: Decimal,
    expected: Decimal,
) -> None:
    assert canonical_forecast_temp_c(raw) == expected
    manifest = build_forecast_manifest([replace(_forecast(), temp_c=raw)], [])
    assert manifest["forecasts"][0]["temp_c"] == format(expected, "f")


@pytest.mark.parametrize("raw", [Decimal("NaN"), Decimal("Infinity"), Decimal("10000")])
def test_forecast_temperature_canonicalization_rejects_unpersistable_values(
    raw: Decimal,
) -> None:
    with pytest.raises(ValueError, match=r"finite|NUMERIC\(6,2\)"):
        canonical_forecast_temp_c(raw)


def test_trajectory_temperature_canonicalization_matches_json_float() -> None:
    raw = Decimal("12.123456789123456789")
    expected = Decimal("12.123456789123457")
    assert canonical_trajectory_temp_c(raw) == expected
    trajectory = ForecastTrajectoryValue(
        station_id=7,
        model="gfs",
        run_time=RUN_TIME,
        valid_date=date(2026, 7, 16),
        points=(("2026-07-16T00:00", raw),),
        fetched_at=RUN_TIME,
    )
    manifest = build_forecast_manifest([], [trajectory])
    assert manifest["trajectories"][0]["points"][0]["temp_c"] == format(expected, "f")


@pytest.mark.parametrize("raw", [Decimal("NaN"), Decimal("Infinity"), Decimal("1e10000")])
def test_trajectory_temperature_canonicalization_rejects_non_json_values(
    raw: Decimal,
) -> None:
    with pytest.raises(ValueError, match=r"finite|JSON-finite"):
        canonical_trajectory_temp_c(raw)


def test_model_input_provenance_is_complete_only_with_run_and_ingest_hashes() -> None:
    prospective = cast(
        MaterializedForecastLike,
        SimpleNamespace(
            id=10,
            model="gfs",
            run_id=3,
            ingest_id=5,
            run_time=RUN_TIME,
            available_at=RUN_TIME,
        ),
    )
    complete = build_model_input_provenance(
        [prospective], [], {5: ("a" * 64, "b" * 64)}
    )
    assert complete["complete"] is True
    assert complete["ingest_ids"] == [5]
    assert complete["inputs"][0]["normalized_manifest_sha256"] == "a" * 64
    assert model_input_provenance_is_complete(
        {"forecast_provenance": complete}
    ) is True

    legacy = cast(
        MaterializedForecastLike,
        SimpleNamespace(
            id=11,
            model="icon",
            run_id=None,
            ingest_id=None,
            run_time=RUN_TIME,
            available_at=RUN_TIME,
        ),
    )
    incomplete = build_model_input_provenance([prospective, legacy], [], {})
    assert incomplete["complete"] is False
    assert incomplete["missing_evidence_count"] == 2
    assert model_input_provenance_is_complete(
        {"forecast_provenance": incomplete}
    ) is False
