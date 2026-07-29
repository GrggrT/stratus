from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    Observation,
    ResolutionReconciliation,
    Station,
    StationStage,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.resolution_reconciliation import (
    ResolutionReconciliationIntegrityError,
    resolution_reconciliation_record_is_valid,
)
from stratus.repositories.station_reliability import (
    StationReliabilityIntegrityError,
    StationReliabilitySnapshotValue,
    acquire_station_reliability_gate_lock,
    insert_station_reliability_snapshot,
    latest_station_reliability_by_station,
    station_reliability_record_is_valid,
)
from stratus.services.station_reliability import (
    StationReconciliationEvidence,
    assess_station_reliability,
    reduce_station_reconciliation_units,
    station_reliability_policy_hash,
    station_reliability_source_hash,
)

logger = logging.getLogger(__name__)


def _observation_payload_hash(observation: Observation | None) -> str | None:
    if observation is None:
        return None
    payload = json.dumps(
        {
            "id": observation.id,
            "station_id": observation.station_id,
            "obs_time": observation.obs_time.isoformat(),
            "temp_c": format(observation.temp_c.normalize(), "f"),
            "raw_metar_sha256": hashlib.sha256(observation.raw_metar.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def assess_station_reliability_job(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    evaluated_at = datetime.now(UTC)
    # Query a conservative UTC envelope, then apply the exact half-open
    # [local_today-window_days, local_today) interval independently per station.
    earliest_date = evaluated_at.date() - timedelta(
        days=settings.station_reliability_window_days + 2
    )
    policy_hash = station_reliability_policy_hash(settings)

    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            await acquire_station_reliability_gate_lock(session)
            stations = list(
                (
                    await session.scalars(
                        select(Station)
                        .where(
                            Station.active.is_(True),
                            Station.stage != StationStage.DISABLED,
                        )
                        .order_by(Station.id)
                    )
                ).all()
            )
            station_ids = [station.id for station in stations]
            reconciliation_rows = (
                list(
                    (
                        await session.scalars(
                            select(ResolutionReconciliation).where(
                                ResolutionReconciliation.station_id.in_(station_ids),
                                ResolutionReconciliation.market_date >= earliest_date,
                            )
                        )
                    ).all()
                )
                if station_ids
                else []
            )
            observation_rows = (
                list(
                    (
                        await session.scalars(
                            select(Observation)
                            .where(Observation.station_id.in_(station_ids))
                            .distinct(Observation.station_id)
                            .order_by(
                                Observation.station_id,
                                Observation.obs_time.desc(),
                                Observation.id.desc(),
                            )
                        )
                    ).all()
                )
                if station_ids
                else []
            )
            previous_by_station = await latest_station_reliability_by_station(
                session,
                station_ids,
            )
            invalid_previous_ids = [
                row.id
                for row in previous_by_station.values()
                if not station_reliability_record_is_valid(row)
            ]
            if invalid_previous_ids:
                raise StationReliabilityIntegrityError(
                    "station reliability refused invalid prior snapshots: "
                    + ",".join(str(row_id) for row_id in invalid_previous_ids[:20])
                )

            invalid_reconciliation_ids = [
                row.id
                for row in reconciliation_rows
                if not resolution_reconciliation_record_is_valid(row)
            ]
            if invalid_reconciliation_ids:
                raise ResolutionReconciliationIntegrityError(
                    "station reliability refused invalid reconciliation evidence: "
                    + ",".join(str(row_id) for row_id in invalid_reconciliation_ids[:20])
                )

            evidence = (
                StationReconciliationEvidence(
                    station_id=row.station_id,
                    market_date=row.market_date,
                    kind=row.kind,
                    classification=row.classification,
                    checked_at=row.checked_at,
                    record_hash=row.record_hash,
                )
                for row in reconciliation_rows
                if row.station_id is not None
            )
            units = reduce_station_reconciliation_units(evidence)
            units_by_station = {
                station_id: tuple(item for item in units if item.station_id == station_id)
                for station_id in station_ids
            }
            observations_by_station = {
                observation.station_id: observation for observation in observation_rows
            }

            for station in stations:
                local_today = evaluated_at.astimezone(ZoneInfo(station.tz)).date()
                window_start = local_today - timedelta(
                    days=settings.station_reliability_window_days
                )
                station_units = tuple(
                    item
                    for item in units_by_station[station.id]
                    if window_start <= item.market_date < local_today
                )
                observation = observations_by_station.get(station.id)
                assessment = assess_station_reliability(
                    station_units,
                    now=evaluated_at,
                    station_timezone=station.tz,
                    latest_observation_at=(
                        observation.obs_time if observation is not None else None
                    ),
                    min_samples=settings.station_reliability_min_samples,
                    min_lower_bound=settings.station_reliability_min_lower_bound,
                    resolution_cooldown=timedelta(hours=settings.station_resolution_cooldown_hours),
                    observation_stale_after=timedelta(
                        minutes=settings.station_observation_stale_minutes
                    ),
                    previous_snapshot=previous_by_station.get(station.id),
                )
                value = StationReliabilitySnapshotValue(
                    station_id=station.id,
                    evaluated_at=evaluated_at,
                    window_days=settings.station_reliability_window_days,
                    comparable_units=assessment.comparable_units,
                    match_units=assessment.match_units,
                    conflict_units=assessment.conflict_units,
                    exact_source_mismatch_units=(assessment.exact_source_mismatch_units),
                    clear_mismatch_units=assessment.clear_mismatch_units,
                    exact_truth_gap_units=assessment.exact_truth_gap_units,
                    boundary_units=assessment.boundary_units,
                    unavailable_units=assessment.unavailable_units,
                    reliability_score=assessment.reliability_score,
                    reliability_lower_bound=assessment.reliability_lower_bound,
                    status=assessment.status,
                    latest_conflict_at=assessment.latest_conflict_at,
                    latest_conflict_date=assessment.latest_conflict_date,
                    latest_conflict_kind=assessment.latest_conflict_kind,
                    resolution_cooldown_until=assessment.resolution_cooldown_until,
                    latest_observation_at=(
                        observation.obs_time if observation is not None else None
                    ),
                    observation_local_date=assessment.observation_local_date,
                    observation_cooldown_until=(assessment.observation_cooldown_until),
                    reasons=assessment.reasons,
                    policy_hash=policy_hash,
                    source_hash=station_reliability_source_hash(
                        station_units,
                        latest_observation_id=(observation.id if observation is not None else None),
                        latest_observation_at=(
                            observation.obs_time if observation is not None else None
                        ),
                        latest_observation_payload_hash=(_observation_payload_hash(observation)),
                    ),
                )
                await insert_station_reliability_snapshot(session, value)

    logger.info(
        "station reliability assessment complete: stations=%d policy=%s",
        len(stations),
        policy_hash[:12],
    )
    return len(stations)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(assess_station_reliability_job(settings))


if __name__ == "__main__":
    main()
