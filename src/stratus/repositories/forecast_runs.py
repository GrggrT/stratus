from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import ForecastModelRegistry, ForecastRun
from stratus.services.model_catalog import ModelDescriptor


async def upsert_model_registry(session: AsyncSession, descriptor: ModelDescriptor) -> int:
    insert_statement = insert(ForecastModelRegistry).values(
        provider=descriptor.provider,
        model=descriptor.model,
        model_version=descriptor.model_version,
        region=descriptor.region,
        resolution_km=descriptor.resolution_km,
        run_frequency_hours=descriptor.run_frequency_hours,
        expected_latency_minutes=descriptor.expected_latency_minutes,
        source_url=descriptor.source_url,
        license_status=descriptor.license_status,
        enabled=descriptor.enabled,
        shadow_only=descriptor.shadow_only,
        introduced_at=descriptor.introduced_at,
        metadata_={},
    )
    statement = insert_statement.on_conflict_do_update(
        constraint="uq_forecast_model_version",
        set_={
            "resolution_km": descriptor.resolution_km,
            "run_frequency_hours": descriptor.run_frequency_hours,
            "expected_latency_minutes": descriptor.expected_latency_minutes,
            "source_url": descriptor.source_url,
            "license_status": descriptor.license_status,
            "enabled": descriptor.enabled,
            "shadow_only": descriptor.shadow_only,
            "retired_at": None,
        },
    ).returning(ForecastModelRegistry.id)
    return (await session.execute(statement)).scalar_one()


async def upsert_forecast_run(
    session: AsyncSession,
    *,
    descriptor: ModelDescriptor,
    init_time: datetime,
    available_at: datetime,
    retrieved_at: datetime,
    source_url: str,
    status: str = "complete",
    checksum: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> int:
    if available_at < init_time:
        raise ValueError("forecast run cannot be available before initialization")
    registry_id = await upsert_model_registry(session, descriptor)
    insert_statement = insert(ForecastRun).values(
        registry_id=registry_id,
        init_time=init_time,
        first_seen_at=available_at,
        available_at=available_at,
        retrieved_at=retrieved_at,
        source_url=source_url,
        checksum=checksum,
        status=status,
        metadata_=metadata or {},
    )
    # A provider run is point-in-time evidence.  A retry may discover the same
    # run again, but it must never move first_seen/available_at forward or
    # rewrite the original source metadata.  The per-request ingest ledger
    # records every later retrieval separately.
    statement = insert_statement.on_conflict_do_nothing(
        constraint="uq_forecast_run_identity"
    ).returning(ForecastRun.id)
    inserted_id = (await session.execute(statement)).scalar_one_or_none()
    if inserted_id is not None:
        return inserted_id
    existing_id = await session.scalar(
        select(ForecastRun.id).where(
            ForecastRun.registry_id == registry_id,
            ForecastRun.init_time == init_time,
        )
    )
    if existing_id is None:
        raise RuntimeError("forecast run conflict did not resolve to an existing row")
    return existing_id
