from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from stratus.collectors.aviation_weather import MetarObservation
from stratus.collectors.iem import IemFetchResult, IemParsedRow, IemRequestManifest
from stratus.jobs.recover_observations import (
    ObservationRecoveryJobError,
    ObservationRecoverySelectionError,
    _recovery_points,
    _requested_dates,
)


def test_automatic_candidate_dates_open_at_or_after_the_station_local_hour() -> None:
    before = datetime(2026, 7, 17, 7, 59, tzinfo=UTC)  # 03:59 New York.
    open_hour = datetime(2026, 7, 17, 8, 5, tzinfo=UTC)  # 04:05 New York.
    after = datetime(2026, 7, 17, 15, 5, tzinfo=UTC)  # 11:05 New York.
    expected = (date(2026, 7, 14), date(2026, 7, 15), date(2026, 7, 16))
    assert _requested_dates(
        now=before,
        timezone="America/New_York",
        lookback_days=3,
        requested_date=None,
        automatic=True,
        recovery_local_hour=4,
    ) == ()
    assert _requested_dates(
        now=open_hour,
        timezone="America/New_York",
        lookback_days=3,
        requested_date=None,
        automatic=True,
        recovery_local_hour=4,
    ) == expected
    assert _requested_dates(
        now=after,
        timezone="America/New_York",
        lookback_days=3,
        requested_date=None,
        automatic=True,
        recovery_local_hour=4,
    ) == expected


def test_manual_date_bypasses_hour_but_not_closed_day_or_lookback() -> None:
    now = datetime(2026, 7, 17, 7, 59, tzinfo=UTC)
    assert _requested_dates(
        now=now,
        timezone="America/New_York",
        lookback_days=3,
        requested_date=date(2026, 7, 16),
        automatic=False,
        recovery_local_hour=4,
    ) == (date(2026, 7, 16),)
    with pytest.raises(ObservationRecoverySelectionError, match="closed day"):
        _requested_dates(
            now=now,
            timezone="America/New_York",
            lookback_days=3,
            requested_date=date(2026, 7, 17),
            automatic=False,
            recovery_local_hour=4,
        )
    with pytest.raises(ObservationRecoverySelectionError, match="lookback"):
        _requested_dates(
            now=now,
            timezone="America/New_York",
            lookback_days=3,
            requested_date=date(2026, 7, 12),
            automatic=False,
            recovery_local_hour=4,
        )


def test_repository_points_are_bound_to_exact_selected_csv_rows() -> None:
    obs_time = datetime(2026, 7, 16, 1, tzinfo=UTC)
    observation = MetarObservation(1, obs_time, Decimal("18.5"), "KLGA report")
    source_row = b"LGA,2026-07-16 01:00,18.5,KLGA report\r\n"
    manifest = IemRequestManifest(
        source_url="https://example.invalid/asos.py",
        station_id=1,
        icao="KLGA",
        network="NY_ASOS",
        source_station_id="LGA",
        start=date(2026, 7, 16),
        end=date(2026, 7, 17),
        params=(("station", "LGA"),),
    )
    result = IemFetchResult(
        request_manifest=manifest,
        request_started_at=datetime(2026, 7, 17, 1, tzinfo=UTC),
        response_received_at=datetime(2026, 7, 17, 1, 0, 1, tzinfo=UTC),
        response_status_code=200,
        response_headers=(),
        response_body=b"header\r\n" + source_row,
        observations=(observation,),
        exact_rows=(IemParsedRow(observation, source_row),),
    )
    points = _recovery_points(result, selected_times={obs_time})
    assert len(points) == 1
    assert points[0].source_row_bytes == source_row

    with pytest.raises(ObservationRecoveryJobError, match="fully bound"):
        _recovery_points(
            result,
            selected_times={obs_time, datetime(2026, 7, 16, 2, tzinfo=UTC)},
        )
