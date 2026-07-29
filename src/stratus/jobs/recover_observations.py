from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.iem import IemClient, IemFetchResult, IemStation
from stratus.config import Settings, get_settings
from stratus.db.models import (
    DailyActual,
    ObservationRecoveryArtifact,
    ObservationRecoveryRuntime,
    Station,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.observation_recovery import (
    IEM_ASOS_RECOVERY_PROTOCOL,
    IEM_RETENTION_POLICY_STATEMENT,
    IEM_RETENTION_POLICY_URL,
    IEM_RETENTION_POLICY_VERSION,
    ObservationRecoveryEvidence,
    ObservationRecoveryPointValue,
    record_observation_recovery,
)
from stratus.services.daily_actuals import IncompleteObservationDayError
from stratus.services.observation_recovery import (
    closed_day_recovery_window,
    recover_observation_day,
    validate_observation_recovery_artifact,
)

logger = logging.getLogger(__name__)

IEM_RETENTION_POLICY_REVIEWED_AT = datetime(2026, 7, 17, tzinfo=UTC)


class ObservationRecoveryJobError(RuntimeError):
    pass


class ObservationRecoveryReleaseBlockedError(ObservationRecoveryJobError):
    pass


class ObservationRecoverySelectionError(ObservationRecoveryJobError):
    pass


class ObservationRecoveryPolicyError(ObservationRecoveryJobError):
    pass


class ObservationRecoveryAllFailedError(ObservationRecoveryJobError):
    def __init__(self, summary: ObservationRecoveryRunResult) -> None:
        self.summary = summary
        super().__init__("all due observation recoveries failed: " + "; ".join(summary.failures))


@dataclass(frozen=True, slots=True)
class ObservationRecoveryRunResult:
    scanned: int
    attempted: int
    materialized: int
    quarantined: int
    idempotent: int
    skipped_complete: int
    skipped_cooldown: int
    skipped_cap: int
    conflicts: int
    failures: tuple[str, ...]

    @property
    def daily_actual_rows(self) -> int:
        return self.materialized * 2


@dataclass(frozen=True, slots=True)
class _StationRecovery:
    station_id: int
    icao: str
    timezone: str
    network: str | None
    source_station_id: str | None
    target_date: date


class _EvidenceClient(Protocol):
    async def fetch_with_evidence(
        self, station: IemStation, *, start: date, end: date
    ) -> IemFetchResult: ...


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ObservationRecoveryJobError("recovery clock must be timezone-aware")
    return value.astimezone(UTC)


def _requested_dates(
    *,
    now: datetime,
    timezone: str,
    lookback_days: int,
    requested_date: date | None,
    automatic: bool,
    recovery_local_hour: int,
) -> tuple[date, ...]:
    local_now = now.astimezone(ZoneInfo(timezone))
    local_today = local_now.date()
    if requested_date is not None:
        age = (local_today - requested_date).days
        if not 1 <= age <= lookback_days:
            raise ObservationRecoverySelectionError(
                f"{requested_date} is not a closed day inside the {lookback_days}-day lookback"
            )
        return (requested_date,)
    if automatic and local_now.hour < recovery_local_hour:
        return ()
    return tuple(local_today - timedelta(days=offset) for offset in range(lookback_days, 0, -1))


async def _select_candidates(
    sessions: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    now: datetime,
    requested_date: date | None,
    station_icao: str | None,
) -> tuple[list[_StationRecovery], int, int, int, int, list[str]]:
    async with sessions() as session:
        statement = select(Station).where(Station.active.is_(True)).order_by(Station.icao)
        if station_icao is not None:
            statement = statement.where(func.upper(Station.icao) == station_icao.upper())
        stations = list((await session.scalars(statement)).all())
        if station_icao is not None and not stations:
            raise ObservationRecoverySelectionError(
                f"active station {station_icao.upper()} was not found"
            )

        candidate_rows = [
            _StationRecovery(
                station_id=station.id,
                icao=station.icao,
                timezone=station.tz,
                network=station.iem_network,
                source_station_id=station.iem_station_id,
                target_date=target_date,
            )
            for station in stations
            for target_date in _requested_dates(
                now=now,
                timezone=station.tz,
                lookback_days=settings.daily_actual_recovery_lookback_days,
                requested_date=requested_date,
                automatic=requested_date is None and station_icao is None,
                recovery_local_hour=settings.daily_actual_recovery_local_hour,
            )
        ]
        if not candidate_rows:
            return [], 0, 0, 0, 0, []

        station_ids = {value.station_id for value in candidate_rows}
        dates = {value.target_date for value in candidate_rows}
        actual_rows = (
            await session.execute(
                select(DailyActual.station_id, DailyActual.date, DailyActual.kind).where(
                    DailyActual.station_id.in_(station_ids),
                    DailyActual.date.in_(dates),
                )
            )
        ).all()
        actuals_by_day: dict[tuple[int, date], set[WeatherKind]] = {}
        for station_id, actual_date, kind in actual_rows:
            actuals_by_day.setdefault((station_id, actual_date), set()).add(kind)

        last_artifacts = (
            await session.execute(
                select(
                    ObservationRecoveryArtifact.station_id,
                    ObservationRecoveryArtifact.local_date,
                    func.max(ObservationRecoveryArtifact.first_seen_at),
                )
                .where(
                    ObservationRecoveryArtifact.station_id.in_(station_ids),
                    ObservationRecoveryArtifact.local_date.in_(dates),
                )
                .group_by(
                    ObservationRecoveryArtifact.station_id,
                    ObservationRecoveryArtifact.local_date,
                )
            )
        ).all()
    latest_by_day = {
        (station_id, local_date): first_seen_at
        for station_id, local_date, first_seen_at in last_artifacts
    }

    complete_kinds = {WeatherKind.HIGH, WeatherKind.LOW}
    selected: list[_StationRecovery] = []
    failures: list[str] = []
    skipped_complete = 0
    skipped_cooldown = 0
    conflicts = 0
    manual = requested_date is not None or station_icao is not None
    retry_cutoff = now - timedelta(hours=settings.daily_actual_recovery_retry_hours)
    for candidate in candidate_rows:
        key = (candidate.station_id, candidate.target_date)
        kinds = actuals_by_day.get(key, set())
        if kinds == complete_kinds:
            skipped_complete += 1
            continue
        if kinds:
            conflicts += 1
            failures.append(
                f"{candidate.icao}/{candidate.target_date}: partial daily-actual pair conflict"
            )
            continue
        if not candidate.network or not candidate.source_station_id:
            failures.append(
                f"{candidate.icao}/{candidate.target_date}: missing IEM source metadata"
            )
            continue
        latest = latest_by_day.get(key)
        if latest is not None and latest.astimezone(UTC) > retry_cutoff and not manual:
            skipped_cooldown += 1
            continue
        selected.append(candidate)
    return (
        selected,
        len(candidate_rows),
        skipped_complete,
        skipped_cooldown,
        conflicts,
        failures,
    )


def _header(result: IemFetchResult, name: str) -> str | None:
    wanted = name.lower()
    return next((value for key, value in result.response_headers if key == wanted), None)


def _http_date(result: IemFetchResult) -> datetime | None:
    value = _header(result, "date")
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _recovery_points(
    result: IemFetchResult,
    *,
    selected_times: set[datetime],
) -> list[ObservationRecoveryPointValue]:
    if not result.exact_rows:
        raise ObservationRecoveryJobError("IEM result has no exact parsed CSV rows")
    selected = [
        row
        for row in result.exact_rows
        if row.observation.obs_time.astimezone(UTC) in selected_times
    ]
    if len(selected) != len(selected_times):
        raise ObservationRecoveryJobError(
            "selected recovery observations are not fully bound to exact CSV rows"
        )
    return [
        ObservationRecoveryPointValue(
            obs_time=row.observation.obs_time,
            temp_c=row.observation.temp_c,
            raw_report=row.observation.raw_metar,
            source_row_bytes=row.source_row,
        )
        for row in selected
    ]


async def _run_candidates(
    client: _EvidenceClient,
    sessions: async_sessionmaker[AsyncSession],
    *,
    settings: Settings,
    candidates: Sequence[_StationRecovery],
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]],
    initial: ObservationRecoveryRunResult,
) -> ObservationRecoveryRunResult:
    attempted = 0
    materialized = 0
    quarantined = 0
    idempotent = 0
    failures = list(initial.failures)
    for index, candidate in enumerate(candidates):
        attempted += 1
        try:
            window = closed_day_recovery_window(
                target_date=candidate.target_date,
                timezone=candidate.timezone,
                now=_require_aware_utc(clock()),
            )
            result = await client.fetch_with_evidence(
                IemStation(
                    station_id=candidate.station_id,
                    icao=candidate.icao,
                    network=candidate.network or "",
                    source_station_id=candidate.source_station_id or "",
                ),
                start=window.request_start,
                end=window.request_end,
            )
            if result.response_bytes > settings.daily_actual_recovery_max_response_bytes:
                raise ObservationRecoveryJobError(
                    "IEM response exceeds configured recovery byte cap: "
                    f"{result.response_bytes} > "
                    f"{settings.daily_actual_recovery_max_response_bytes}"
                )
            validation_now = _require_aware_utc(clock())
            validated = validate_observation_recovery_artifact(
                result,
                station_id=candidate.station_id,
                target_date=candidate.target_date,
                timezone=candidate.timezone,
                now=validation_now,
            )
            complete = True
            try:
                recover_observation_day(
                    (result,),
                    station_id=candidate.station_id,
                    target_date=candidate.target_date,
                    timezone=candidate.timezone,
                    now=validation_now,
                    min_samples=settings.daily_actual_min_samples,
                    boundary_tolerance=timedelta(
                        minutes=settings.daily_actual_boundary_tolerance_minutes
                    ),
                    max_gap=timedelta(minutes=settings.daily_actual_max_gap_minutes),
                )
            except IncompleteObservationDayError:
                complete = False

            points = _recovery_points(
                result,
                selected_times={value.obs_time for value in validated.observations},
            )
            evidence = ObservationRecoveryEvidence(
                station_id=candidate.station_id,
                local_date=candidate.target_date,
                timezone=candidate.timezone,
                utc_start=validated.window.utc_start,
                utc_end=validated.window.utc_end,
                network=result.request_manifest.network,
                source_station_id=result.request_manifest.source_station_id,
                source_name="Iowa Environmental Mesonet ASOS archive",
                source_protocol=IEM_ASOS_RECOVERY_PROTOCOL,
                source_url=result.request_manifest.source_url,
                request_manifest=result.request_manifest.to_payload(),
                request_started_at=result.request_started_at,
                response_received_at=result.response_received_at,
                response_body=result.response_body,
                retention_policy_url=IEM_RETENTION_POLICY_URL,
                retention_policy_reviewed_at=IEM_RETENTION_POLICY_REVIEWED_AT,
                retention_policy_version=IEM_RETENTION_POLICY_VERSION,
                retention_policy_statement=IEM_RETENTION_POLICY_STATEMENT,
                storage_permitted=True,
                minimum_samples=settings.daily_actual_min_samples,
                boundary_tolerance_seconds=(
                    settings.daily_actual_boundary_tolerance_minutes * 60
                ),
                max_permitted_gap_seconds=settings.daily_actual_max_gap_minutes * 60,
                http_status=result.response_status_code,
                content_type=_header(result, "content-type") or "text/csv",
                http_date=_http_date(result),
                etag=_header(result, "etag"),
            )
            async with sessions.begin() as session:
                persisted = await record_observation_recovery(
                    session,
                    evidence=evidence,
                    points=points,
                )
            if persisted.materialized:
                if not complete:
                    raise ObservationRecoveryJobError(
                        "repository materialized a day rejected by the pure completeness gate"
                    )
                materialized += 1
                idempotent += int(persisted.idempotent)
            else:
                quarantined += 1
                failures.append(
                    f"{candidate.icao}/{candidate.target_date}: {persisted.validation_code}"
                )
        except Exception as exc:  # noqa: BLE001 - station isolation is the job contract.
            failures.append(f"{candidate.icao}/{candidate.target_date}: {exc}")
            logger.exception(
                "observation recovery failed: station=%s date=%s",
                candidate.icao,
                candidate.target_date,
            )
        if index + 1 < len(candidates):
            await sleep(settings.iem_request_delay_seconds)
    return ObservationRecoveryRunResult(
        scanned=initial.scanned,
        attempted=attempted,
        materialized=materialized,
        quarantined=quarantined,
        idempotent=idempotent,
        skipped_complete=initial.skipped_complete,
        skipped_cooldown=initial.skipped_cooldown,
        skipped_cap=initial.skipped_cap,
        conflicts=initial.conflicts,
        failures=tuple(failures),
    )


async def recover_observations(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    target_date: date | None = None,
    station_icao: str | None = None,
    client: _EvidenceClient | None = None,
    clock: Callable[[], datetime] = _utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> ObservationRecoveryRunResult:
    settings = settings or get_settings()
    if not settings.daily_actual_recovery_enabled:
        return ObservationRecoveryRunResult(0, 0, 0, 0, 0, 0, 0, 0, 0, ())
    policy_hash = hashlib.sha256(IEM_RETENTION_POLICY_STATEMENT).hexdigest()
    if policy_hash != settings.iem_recovery_terms_policy_hash:
        raise ObservationRecoveryPolicyError(
            "reviewed IEM retention-policy statement does not match configured hash"
        )
    now = _require_aware_utc(clock())

    async with session_factory_scope(settings, session_factory) as sessions:
        # This is intentionally before station selection and before opening an
        # HTTP client: an unreviewed predictive fork cannot collect 0043 evidence.
        async with sessions.begin() as session:
            recovery_runtime = await session.get(ObservationRecoveryRuntime, 1)
            if recovery_runtime is None:
                raise ObservationRecoveryPolicyError(
                    "observation recovery runtime is unavailable"
                )
            configured_source_hash = hashlib.sha256(
                settings.iem_asos_url.encode("utf-8")
            ).hexdigest()
            if configured_source_hash != recovery_runtime.source_url_sha256:
                raise ObservationRecoveryPolicyError(
                    "configured IEM endpoint does not match the reviewed recovery runtime"
                )
            gate = await probability_release_gate(session, settings)
        if not gate.allowed:
            raise ObservationRecoveryReleaseBlockedError(
                f"probability release gate blocked recovery: {gate.reason}"
            )

        (
            selected,
            scanned,
            skipped_complete,
            skipped_cooldown,
            conflicts,
            failures,
        ) = await _select_candidates(
            sessions,
            settings=settings,
            now=now,
            requested_date=target_date,
            station_icao=station_icao,
        )
        capped = selected[: settings.daily_actual_recovery_max_requests_per_run]
        skipped_cap = len(selected) - len(capped)
        initial = ObservationRecoveryRunResult(
            scanned=scanned,
            attempted=0,
            materialized=0,
            quarantined=0,
            idempotent=0,
            skipped_complete=skipped_complete,
            skipped_cooldown=skipped_cooldown,
            skipped_cap=skipped_cap,
            conflicts=conflicts,
            failures=tuple(failures),
        )
        if not capped:
            if failures:
                raise ObservationRecoveryAllFailedError(initial)
            return initial

        if client is not None:
            summary = await _run_candidates(
                client,
                sessions,
                settings=settings,
                candidates=capped,
                clock=clock,
                sleep=sleep,
                initial=initial,
            )
        else:
            async with IemClient(
                asos_url=settings.iem_asos_url,
                max_response_bytes=settings.daily_actual_recovery_max_response_bytes,
                request_budget=settings.daily_actual_recovery_max_requests_per_run,
            ) as managed_client:
                summary = await _run_candidates(
                    managed_client,
                    sessions,
                    settings=settings,
                    candidates=capped,
                    clock=clock,
                    sleep=sleep,
                    initial=initial,
                )
        if summary.materialized == 0 and summary.failures:
            raise ObservationRecoveryAllFailedError(summary)
        logger.info(
            "observation recovery complete: attempted=%d materialized=%d "
            "quarantined=%d failures=%d",
            summary.attempted,
            summary.materialized,
            summary.quarantined,
            len(summary.failures),
        )
        return summary


def _date_argument(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover bounded closed-day IEM observations")
    parser.add_argument("--date", type=_date_argument, dest="target_date")
    parser.add_argument("--station", dest="station_icao")
    args = parser.parse_args()
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(
        recover_observations(
            settings,
            target_date=args.target_date,
            station_icao=args.station_icao,
        )
    )


if __name__ == "__main__":
    main()
