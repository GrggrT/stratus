from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from stratus.collectors.aviation_weather import MetarObservation
from stratus.collectors.iem import IemFetchResult
from stratus.services.daily_actuals import (
    DailyExtremes,
    ObservationPoint,
    compute_daily_extremes,
    utc_bounds_for_local_date,
)


class ObservationRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ClosedDayRecoveryWindow:
    target_date: date
    timezone: str
    utc_start: datetime
    utc_end: datetime
    request_start: date
    request_end: date

    @property
    def duration(self) -> timedelta:
        return self.utc_end - self.utc_start


@dataclass(frozen=True, slots=True)
class RecoveredObservationDay:
    station_id: int
    target_date: date
    timezone: str
    utc_start: datetime
    utc_end: datetime
    request_start: date
    request_end: date
    observations: tuple[MetarObservation, ...]
    extremes: DailyExtremes
    request_manifest_sha256: str
    response_sha256: str
    response_bytes: int
    point_manifest_sha256: str
    actual_manifest_sha256: str

    @property
    def points(self) -> tuple[ObservationPoint, ...]:
        return tuple(
            ObservationPoint(obs_time=value.obs_time, temp_c=value.temp_c)
            for value in self.observations
        )


@dataclass(frozen=True, slots=True)
class ValidatedRecoveryArtifact:
    window: ClosedDayRecoveryWindow
    observations: tuple[MetarObservation, ...]


def closed_day_recovery_window(
    *,
    target_date: date,
    timezone: str,
    now: datetime,
) -> ClosedDayRecoveryWindow:
    """Return exact local-day bounds and the padded UTC-date request range.

    The IEM endpoint accepts calendar dates in the requested timezone.  Recovery
    always asks for UTC calendar dates, so non-UTC local days need a padded range;
    callers must subsequently select the exact half-open ``[utc_start, utc_end)``.
    """

    now_utc = _aware_utc(now, field_name="now")
    local_timezone = ZoneInfo(timezone)
    if target_date >= now_utc.astimezone(local_timezone).date():
        raise ObservationRecoveryError("recovery is restricted to closed local days")

    utc_start, utc_end = utc_bounds_for_local_date(target_date, timezone)
    if now_utc < utc_end:
        raise ObservationRecoveryError("the requested local day has not closed")

    request_start = utc_start.date()
    request_end = utc_end.date()
    if utc_end.timetz().replace(tzinfo=None) != time.min:
        request_end += timedelta(days=1)
    return ClosedDayRecoveryWindow(
        target_date=target_date,
        timezone=timezone,
        utc_start=utc_start,
        utc_end=utc_end,
        request_start=request_start,
        request_end=request_end,
    )


def filter_observations_to_closed_day(
    observations: Sequence[MetarObservation],
    *,
    station_id: int,
    window: ClosedDayRecoveryWindow,
) -> tuple[MetarObservation, ...]:
    """Select one station's exact local day, rejecting malformed source rows."""

    selected: list[MetarObservation] = []
    seen: set[datetime] = set()
    request_utc_start = datetime.combine(window.request_start, time.min, tzinfo=UTC)
    request_utc_end = datetime.combine(window.request_end, time.min, tzinfo=UTC)
    for observation in observations:
        if observation.station_id != station_id:
            raise ObservationRecoveryError("archive artifact contains another station")
        obs_time = _aware_utc(observation.obs_time, field_name="observation obs_time")
        if not request_utc_start <= obs_time < request_utc_end:
            raise ObservationRecoveryError("archive observation is outside request bounds")
        if window.utc_start <= obs_time < window.utc_end:
            if obs_time in seen:
                raise ObservationRecoveryError(
                    f"archive artifact has an ambiguous duplicate at {obs_time.isoformat()}"
                )
            seen.add(obs_time)
            selected.append(
                MetarObservation(
                    station_id=observation.station_id,
                    obs_time=obs_time,
                    temp_c=observation.temp_c,
                    raw_metar=observation.raw_metar,
                )
            )
    return tuple(sorted(selected, key=lambda value: value.obs_time))


def point_manifest_payload(
    *,
    station_id: int,
    window: ClosedDayRecoveryWindow,
    observations: Sequence[MetarObservation],
    request_manifest_sha256: str,
    response_sha256: str,
) -> dict[str, object]:
    ordered = sorted(observations, key=lambda value: value.obs_time)
    return {
        "schema_version": "stratus.recovered-observation-points.v1",
        "source_kind": "iem_asos_archive",
        "station_id": station_id,
        "target_date": window.target_date.isoformat(),
        "timezone": window.timezone,
        "utc_start": _datetime_text(window.utc_start),
        "utc_end": _datetime_text(window.utc_end),
        "request_manifest_sha256": request_manifest_sha256,
        "response_sha256": response_sha256,
        "points": [
            {
                "obs_time": _datetime_text(value.obs_time),
                "temp_c": _decimal_text(value.temp_c),
                "raw_metar_sha256": hashlib.sha256(value.raw_metar.encode("utf-8")).hexdigest(),
            }
            for value in ordered
        ],
    }


def actual_manifest_payload(
    *,
    station_id: int,
    window: ClosedDayRecoveryWindow,
    extremes: DailyExtremes,
    point_manifest_sha256: str,
) -> dict[str, object]:
    return {
        "schema_version": "stratus.recovered-daily-actual.v1",
        "source_kind": "iem_asos_archive",
        "station_id": station_id,
        "target_date": window.target_date.isoformat(),
        "timezone": window.timezone,
        "point_manifest_sha256": point_manifest_sha256,
        "sample_n": extremes.sample_n,
        "actuals": [
            {"kind": "high", "temp_c": _decimal_text(extremes.high_c)},
            {"kind": "low", "temp_c": _decimal_text(extremes.low_c)},
        ],
    }


def canonical_manifest_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recover_observation_day(
    artifacts: Sequence[IemFetchResult],
    *,
    station_id: int,
    target_date: date,
    timezone: str,
    now: datetime,
    live_observations: Sequence[MetarObservation] = (),
    min_samples: int = 20,
    boundary_tolerance: timedelta = timedelta(minutes=90),
    max_gap: timedelta = timedelta(hours=2),
) -> RecoveredObservationDay:
    """Validate and materialise one closed day from exactly one archive artifact.

    Live observations are intentionally not mergeable here.  Recovery either
    succeeds from a single immutable archive response or fails without producing
    a synthetic mixed-source day.
    """

    if live_observations:
        raise ObservationRecoveryError("live and archive observations cannot be mixed")
    if len(artifacts) != 1:
        raise ObservationRecoveryError("recovery requires exactly one archive artifact")

    artifact = artifacts[0]
    validated = validate_observation_recovery_artifact(
        artifact,
        station_id=station_id,
        target_date=target_date,
        timezone=timezone,
        now=now,
    )
    window = validated.window
    observations = validated.observations
    points = [
        ObservationPoint(obs_time=value.obs_time, temp_c=value.temp_c)
        for value in observations
    ]
    # This is deliberately the same completeness gate as the live finaliser.
    extremes = compute_daily_extremes(
        points,
        target_date=target_date,
        timezone=timezone,
        min_samples=min_samples,
        boundary_tolerance=boundary_tolerance,
        max_gap=max_gap,
    )
    point_payload = point_manifest_payload(
        station_id=station_id,
        window=window,
        observations=observations,
        request_manifest_sha256=artifact.request_manifest_sha256,
        response_sha256=artifact.response_sha256,
    )
    point_hash = canonical_manifest_sha256(point_payload)
    actual_payload = actual_manifest_payload(
        station_id=station_id,
        window=window,
        extremes=extremes,
        point_manifest_sha256=point_hash,
    )
    return RecoveredObservationDay(
        station_id=station_id,
        target_date=target_date,
        timezone=timezone,
        utc_start=window.utc_start,
        utc_end=window.utc_end,
        request_start=window.request_start,
        request_end=window.request_end,
        observations=observations,
        extremes=extremes,
        request_manifest_sha256=artifact.request_manifest_sha256,
        response_sha256=artifact.response_sha256,
        response_bytes=artifact.response_bytes,
        point_manifest_sha256=point_hash,
        actual_manifest_sha256=canonical_manifest_sha256(actual_payload),
    )


def validate_observation_recovery_artifact(
    artifact: IemFetchResult,
    *,
    station_id: int,
    target_date: date,
    timezone: str,
    now: datetime,
) -> ValidatedRecoveryArtifact:
    """Validate artifact identity/time bounds before the completeness decision.

    Jobs use this phase to retain an incomplete but otherwise well-formed
    artifact.  Only the subsequent completeness gate may materialise actuals.
    """

    now_utc = _aware_utc(now, field_name="now")
    window = closed_day_recovery_window(
        target_date=target_date,
        timezone=timezone,
        now=now_utc,
    )
    manifest = artifact.request_manifest
    if manifest.station_id != station_id:
        raise ObservationRecoveryError("archive request manifest has another station")
    if manifest.start != window.request_start or manifest.end != window.request_end:
        raise ObservationRecoveryError("archive request manifest does not match recovery bounds")
    if artifact.response_received_at.astimezone(UTC) < window.utc_end:
        raise ObservationRecoveryError("archive artifact was received before the local day closed")
    if artifact.response_received_at.astimezone(UTC) > now_utc:
        raise ObservationRecoveryError("archive artifact response timestamp is in the future")
    observations = filter_observations_to_closed_day(
        artifact.observations,
        station_id=station_id,
        window=window,
    )
    return ValidatedRecoveryArtifact(window=window, observations=observations)


def recover_observation_day_from_artifact(
    artifact: IemFetchResult,
    *,
    station_id: int,
    target_date: date,
    timezone: str,
    now: datetime,
    min_samples: int = 20,
    boundary_tolerance: timedelta = timedelta(minutes=90),
    max_gap: timedelta = timedelta(hours=2),
) -> RecoveredObservationDay:
    return recover_observation_day(
        (artifact,),
        station_id=station_id,
        target_date=target_date,
        timezone=timezone,
        now=now,
        min_samples=min_samples,
        boundary_tolerance=boundary_tolerance,
        max_gap=max_gap,
    )


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationRecoveryError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _datetime_text(value: datetime) -> str:
    return _aware_utc(value, field_name="manifest datetime").isoformat().replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ObservationRecoveryError("manifest temperatures must be finite")
    if value == 0:
        return "0"
    return format(value.normalize(), "f")
