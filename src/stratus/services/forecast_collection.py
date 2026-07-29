from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.open_meteo import (
    ForecastBundle,
    ForecastTrajectoryValue,
    ForecastValue,
)
from stratus.repositories.forecast_provenance import (
    SourceResponseEvidenceLike,
    record_forecast_ingest,
)
from stratus.repositories.forecast_runs import upsert_forecast_run
from stratus.repositories.forecast_trajectories import upsert_forecast_trajectories
from stratus.repositories.forecasts import upsert_forecasts
from stratus.services.forecast_provenance import (
    FORECAST_AVAILABILITY_SEMANTICS,
    FORECAST_INGEST_PROTOCOL,
    build_forecast_manifest,
    canonical_forecast_temp_c,
    canonical_trajectory_temp_c,
)
from stratus.services.model_catalog import canonical_model, descriptor_for


@dataclass(frozen=True, slots=True)
class PersistedForecastBundle:
    ingest_id: int
    first_seen_at: datetime
    forecast_rows: int
    trajectory_rows: int
    normalized_manifest_sha256: str


async def persist_forecast_bundle(
    session: AsyncSession,
    *,
    bundle: ForecastBundle,
    station_id: int,
    request_model: str,
    collector: str,
    run_source_url: str,
    pipeline_fingerprint: str,
    upstream_init_verified: bool,
) -> PersistedForecastBundle:
    """Seal one successful provider request before materializing its values."""

    if not bundle.evidence:
        raise ValueError("prospective forecast bundle has no source-response evidence")
    canonical_forecasts = tuple(
        replace(value, temp_c=canonical_forecast_temp_c(value.temp_c))
        for value in bundle.forecasts
    )
    canonical_trajectories = tuple(
        replace(
            value,
            points=tuple(
                (local_time, canonical_trajectory_temp_c(temp_c))
                for local_time, temp_c in value.points
            ),
        )
        for value in bundle.trajectories
    )
    manifest = build_forecast_manifest(canonical_forecasts, canonical_trajectories)
    if manifest["station_id"] != station_id:
        raise ValueError("forecast bundle station does not match the requested station")

    rows_by_model: dict[str, list[ForecastValue | ForecastTrajectoryValue]] = {}
    for forecast in canonical_forecasts:
        rows_by_model.setdefault(canonical_model(forecast.model), []).append(forecast)
    for trajectory in canonical_trajectories:
        rows_by_model.setdefault(canonical_model(trajectory.model), []).append(trajectory)
    if not rows_by_model:
        raise ValueError("prospective forecast bundle contains no normalized values")

    response_received_at = max(
        evidence.response_received_at for evidence in bundle.evidence
    )
    run_ids: dict[str, int] = {}
    run_times: set[datetime] = set()
    for model, rows in sorted(rows_by_model.items()):
        model_run_times = {row.run_time for row in rows}
        if len(model_run_times) != 1:
            raise ValueError(f"forecast ingest contains multiple run times for {model}")
        run_time = next(iter(model_run_times))
        run_times.add(run_time)
        run_ids[model] = await upsert_forecast_run(
            session,
            descriptor=descriptor_for(
                model,
                init_time=run_time,
                source_url=run_source_url,
            ),
            init_time=run_time,
            available_at=response_received_at,
            retrieved_at=response_received_at,
            source_url=run_source_url,
            status="snapshot",
            metadata={
                "init_time_semantics": (
                    "upstream_update_time"
                    if upstream_init_verified
                    else "collector_snapshot"
                ),
                "upstream_init_verified": upstream_init_verified,
                "availability_authority": "forecast_ingests.first_seen_at",
            },
        )

    if upstream_init_verified and len(run_times) != 1:
        raise ValueError("verified upstream initialization must identify exactly one run")
    ingest_run_id = next(iter(run_ids.values())) if len(run_ids) == 1 else None
    ingest = await record_forecast_ingest(
        session,
        run_id=ingest_run_id,
        station_id=station_id,
        request_model=request_model,
        collector=collector,
        pipeline_fingerprint=pipeline_fingerprint,
        availability_semantics=FORECAST_AVAILABILITY_SEMANTICS,
        normalized_manifest=manifest,
        responses=cast(tuple[SourceResponseEvidenceLike, ...], bundle.evidence),
        upstream_init_time=(next(iter(run_times)) if upstream_init_verified else None),
        upstream_init_verified=upstream_init_verified,
        collection_protocol=FORECAST_INGEST_PROTOCOL,
    )

    bound_forecasts = [
        replace(
            value,
            run_id=run_ids[canonical_model(value.model)],
            ingest_id=ingest.ingest_id,
            available_at=ingest.first_seen_at,
        )
        for value in canonical_forecasts
    ]
    bound_trajectories = [
        replace(
            value,
            run_id=run_ids[canonical_model(value.model)],
            ingest_id=ingest.ingest_id,
            available_at=ingest.first_seen_at,
        )
        for value in canonical_trajectories
    ]
    forecast_rows = await upsert_forecasts(session, bound_forecasts)
    trajectory_rows = await upsert_forecast_trajectories(session, bound_trajectories)
    return PersistedForecastBundle(
        ingest_id=ingest.ingest_id,
        first_seen_at=ingest.first_seen_at,
        forecast_rows=forecast_rows,
        trajectory_rows=trajectory_rows,
        normalized_manifest_sha256=ingest.normalized_manifest_sha256,
    )
