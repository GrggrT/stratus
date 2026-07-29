from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.collectors.aviation_weather import MetarObservation
from stratus.collectors.iem import IemFetchResult, IemRequestManifest
from stratus.services.daily_actuals import IncompleteObservationDayError
from stratus.services.observation_recovery import (
    ObservationRecoveryError,
    closed_day_recovery_window,
    filter_observations_to_closed_day,
    recover_observation_day,
    validate_observation_recovery_artifact,
)


def _observations(
    *, station_id: int, start: datetime, end: datetime
) -> tuple[MetarObservation, ...]:
    values = []
    cursor = start
    index = 0
    while cursor < end:
        values.append(
            MetarObservation(
                station_id=station_id,
                obs_time=cursor,
                temp_c=Decimal(index % 17) + Decimal("0.5"),
                raw_metar=f"TEST {cursor:%d%H%M}Z {index}",
            )
        )
        cursor += timedelta(hours=1)
        index += 1
    return tuple(values)


def _artifact(
    *,
    station_id: int,
    request_start: date,
    request_end: date,
    observations: tuple[MetarObservation, ...],
    received_at: datetime,
    response_body: bytes = b"immutable IEM response",
) -> IemFetchResult:
    manifest = IemRequestManifest(
        source_url="https://mesonet.example/asos.py",
        station_id=station_id,
        icao="EDDF",
        network="DE_ASOS",
        source_station_id="FRA",
        start=request_start,
        end=request_end,
        params=(("station", "FRA"),),
    )
    return IemFetchResult(
        request_manifest=manifest,
        request_started_at=received_at - timedelta(seconds=1),
        response_received_at=received_at,
        response_status_code=200,
        response_headers=(("content-type", "text/csv"),),
        response_body=response_body,
        observations=observations,
    )


@pytest.mark.parametrize(
    ("target_date", "expected_hours", "expected_start", "expected_end"),
    (
        (
            date(2026, 3, 29),
            23,
            datetime(2026, 3, 28, 23, tzinfo=UTC),
            datetime(2026, 3, 29, 22, tzinfo=UTC),
        ),
        (
            date(2026, 10, 25),
            25,
            datetime(2026, 10, 24, 22, tzinfo=UTC),
            datetime(2026, 10, 25, 23, tzinfo=UTC),
        ),
    ),
)
def test_closed_day_window_is_dst_aware_and_pads_the_iem_utc_request(
    target_date: date,
    expected_hours: int,
    expected_start: datetime,
    expected_end: datetime,
) -> None:
    window = closed_day_recovery_window(
        target_date=target_date,
        timezone="Europe/Berlin",
        now=datetime(2026, 11, 1, tzinfo=UTC),
    )
    assert window.utc_start == expected_start
    assert window.utc_end == expected_end
    assert window.duration == timedelta(hours=expected_hours)
    assert window.request_start == expected_start.date()
    assert window.request_end == expected_end.date() + timedelta(days=1)


def test_recovery_rejects_today_and_future_in_station_local_time() -> None:
    now = datetime(2026, 7, 4, 22, 30, tzinfo=UTC)  # 5 July in Berlin.
    with pytest.raises(ObservationRecoveryError, match="closed local days"):
        closed_day_recovery_window(
            target_date=date(2026, 7, 5),
            timezone="Europe/Berlin",
            now=now,
        )
    with pytest.raises(ObservationRecoveryError, match="closed local days"):
        closed_day_recovery_window(
            target_date=date(2026, 7, 6),
            timezone="Europe/Berlin",
            now=now,
        )


def test_filter_selects_exact_half_open_local_day_and_rejects_request_escape() -> None:
    window = closed_day_recovery_window(
        target_date=date(2026, 7, 4),
        timezone="America/New_York",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )
    padding_before = MetarObservation(
        1, window.utc_start - timedelta(hours=1), Decimal("99"), "padding before"
    )
    padding_after = MetarObservation(1, window.utc_end, Decimal("99"), "padding after")
    exact = _observations(station_id=1, start=window.utc_start, end=window.utc_end)
    selected = filter_observations_to_closed_day(
        (padding_before, *exact, padding_after),
        station_id=1,
        window=window,
    )
    assert selected == exact
    assert selected[0].obs_time == window.utc_start
    assert selected[-1].obs_time < window.utc_end

    escaped = MetarObservation(
        1,
        datetime.combine(window.request_end, datetime.min.time(), tzinfo=UTC),
        Decimal("1"),
        "outside",
    )
    with pytest.raises(ObservationRecoveryError, match="outside request bounds"):
        filter_observations_to_closed_day(
            (*exact, escaped), station_id=1, window=window
        )


def test_recovery_uses_one_archive_only_and_never_mixes_live_points() -> None:
    window = closed_day_recovery_window(
        target_date=date(2026, 7, 4),
        timezone="Europe/Berlin",
        now=datetime(2026, 7, 6, tzinfo=UTC),
    )
    observations = _observations(
        station_id=1, start=window.utc_start, end=window.utc_end
    )
    artifact = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=observations,
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    with pytest.raises(ObservationRecoveryError, match="exactly one"):
        recover_observation_day(
            (),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=datetime(2026, 7, 6, tzinfo=UTC),
        )
    with pytest.raises(ObservationRecoveryError, match="exactly one"):
        recover_observation_day(
            (artifact, artifact),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=datetime(2026, 7, 6, tzinfo=UTC),
        )
    with pytest.raises(ObservationRecoveryError, match="cannot be mixed"):
        recover_observation_day(
            (artifact,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=datetime(2026, 7, 6, tzinfo=UTC),
            live_observations=observations[:1],
        )


def test_complete_recovery_is_deterministic_and_passes_the_existing_gate() -> None:
    now = datetime(2026, 7, 6, tzinfo=UTC)
    window = closed_day_recovery_window(
        target_date=date(2026, 7, 4), timezone="Europe/Berlin", now=now
    )
    observations = _observations(
        station_id=7, start=window.utc_start, end=window.utc_end
    )
    first_artifact = _artifact(
        station_id=7,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=observations,
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    second_artifact = _artifact(
        station_id=7,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=tuple(reversed(observations)),
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    first = recover_observation_day(
        (first_artifact,),
        station_id=7,
        target_date=window.target_date,
        timezone=window.timezone,
        now=now,
    )
    second = recover_observation_day(
        (second_artifact,),
        station_id=7,
        target_date=window.target_date,
        timezone=window.timezone,
        now=now,
    )
    assert first.observations == observations
    assert first.extremes.sample_n == 24
    assert first.extremes.high_c == Decimal("16.5")
    assert first.extremes.low_c == Decimal("0.5")
    assert first.point_manifest_sha256 == second.point_manifest_sha256
    assert first.actual_manifest_sha256 == second.actual_manifest_sha256
    assert len(first.point_manifest_sha256) == 64
    assert len(first.actual_manifest_sha256) == 64


def test_recovery_fails_closed_on_incomplete_or_premature_artifact() -> None:
    now = datetime(2026, 7, 6, tzinfo=UTC)
    window = closed_day_recovery_window(
        target_date=date(2026, 7, 4), timezone="Europe/Berlin", now=now
    )
    incomplete = _observations(
        station_id=1,
        start=window.utc_start + timedelta(hours=8),
        end=window.utc_end,
    )
    artifact = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=incomplete,
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    validated = validate_observation_recovery_artifact(
        artifact,
        station_id=1,
        target_date=window.target_date,
        timezone=window.timezone,
        now=now,
    )
    assert validated.observations == incomplete
    with pytest.raises(IncompleteObservationDayError):
        recover_observation_day(
            (artifact,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=now,
        )

    complete = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=_observations(
            station_id=1, start=window.utc_start, end=window.utc_end
        ),
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    with pytest.raises(IncompleteObservationDayError, match="at least 25"):
        recover_observation_day(
            (complete,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=now,
            min_samples=25,
        )

    premature = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=_observations(
            station_id=1, start=window.utc_start, end=window.utc_end
        ),
        received_at=window.utc_end - timedelta(seconds=1),
    )
    with pytest.raises(ObservationRecoveryError, match="before the local day closed"):
        recover_observation_day(
            (premature,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=now,
        )


def test_recovery_rejects_manifest_mismatch_and_duplicate_timestamp() -> None:
    now = datetime(2026, 7, 6, tzinfo=UTC)
    window = closed_day_recovery_window(
        target_date=date(2026, 7, 4), timezone="Europe/Berlin", now=now
    )
    observations = _observations(
        station_id=1, start=window.utc_start, end=window.utc_end
    )
    wrong_bounds = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end + timedelta(days=1),
        observations=observations,
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    with pytest.raises(ObservationRecoveryError, match="does not match recovery bounds"):
        recover_observation_day(
            (wrong_bounds,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=now,
        )

    duplicate = _artifact(
        station_id=1,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=(*observations, observations[0]),
        received_at=datetime(2026, 7, 5, 23, tzinfo=UTC),
    )
    with pytest.raises(ObservationRecoveryError, match="ambiguous duplicate"):
        recover_observation_day(
            (duplicate,),
            station_id=1,
            target_date=window.target_date,
            timezone=window.timezone,
            now=now,
        )
