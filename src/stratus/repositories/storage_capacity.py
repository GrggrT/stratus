from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import StorageCapacitySample
from stratus.services.storage_capacity import (
    CapacityCollectionDecision,
    CapacityPoint,
    assess_capacity_collection_gate,
)

CAPACITY_WRITE_ADVISORY_LOCK_ID = 0x53545241545553


@dataclass(frozen=True, slots=True)
class CapacityMeasurement:
    point: CapacityPoint
    clob_batch_count: int
    clob_snapshot_count: int
    oldest_batch_at: datetime | None
    newest_batch_at: datetime | None
    free_disk_path: str
    free_disk_error: str | None


@dataclass(frozen=True, slots=True)
class CapacityGuardMeasurement:
    point: CapacityPoint
    free_disk_path: str
    free_disk_error: str | None


@dataclass(frozen=True, slots=True)
class AuthoritativeCapacityCheck:
    decision: CapacityCollectionDecision
    measurement: CapacityGuardMeasurement | None


def _free_disk_measurement(path: str) -> tuple[int | None, str | None]:
    try:
        return int(shutil.disk_usage(path).free), None
    except OSError as exc:
        return None, f"{type(exc).__name__}: {exc}"[:500]


def _capacity_path_projection(
    storage_path_override: str | None,
) -> tuple[str, dict[str, str]]:
    """Return a path projection that preserves the least-privilege boundary.

    PostgreSQL 16 restricts ``current_setting('data_directory')`` to roles
    with ``pg_read_all_settings``.  Container deployments already provide a
    filesystem-equivalent probe path, so they must not evaluate that setting
    merely to discard it afterward.
    """

    if storage_path_override:
        return (
            "CAST(:storage_path_override AS text) AS data_directory",
            {"storage_path_override": storage_path_override.strip()},
        )
    return "current_setting('data_directory') AS data_directory", {}


async def acquire_capacity_write_lock(session: AsyncSession) -> None:
    """Serialize the final capacity check and immutable evidence write."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_id)"),
        {"lock_id": CAPACITY_WRITE_ADVISORY_LOCK_ID},
    )


async def read_capacity_guard_measurement(
    session: AsyncSession,
    *,
    captured_at: datetime,
    storage_path_override: str | None = None,
) -> CapacityGuardMeasurement:
    """Read the live write footprint without the hourly audit's table scans."""

    path_projection, path_parameters = _capacity_path_projection(storage_path_override)
    row = (
        (
            await session.execute(
                text(
                    f"""
                SELECT
                    pg_database_size(current_database())::bigint AS database_bytes,
                    pg_total_relation_size('clob_book_batches')::bigint
                        AS clob_book_batches_bytes,
                    pg_total_relation_size('clob_market_snapshots')::bigint
                        AS clob_market_snapshots_bytes,
                    {path_projection}
                """
                ),
                path_parameters,
            )
        )
        .mappings()
        .one()
    )
    free_disk_path = (storage_path_override or str(row["data_directory"])).strip()
    free_disk_bytes, free_disk_error = _free_disk_measurement(free_disk_path)
    return CapacityGuardMeasurement(
        point=CapacityPoint(
            captured_at=captured_at,
            database_bytes=int(row["database_bytes"]),
            clob_book_batches_bytes=int(row["clob_book_batches_bytes"]),
            clob_market_snapshots_bytes=int(row["clob_market_snapshots_bytes"]),
            free_disk_bytes=free_disk_bytes,
        ),
        free_disk_path=free_disk_path,
        free_disk_error=free_disk_error,
    )


async def authoritative_capacity_check(
    session: AsyncSession,
    *,
    now: datetime,
    budget_bytes: int,
    minimum_free_bytes: int,
    sample_ttl: timedelta,
    storage_path_override: str | None = None,
    pending_bytes: int = 0,
) -> AuthoritativeCapacityCheck:
    """Recheck persisted freshness and live storage under the caller's lock.

    Callers must first acquire :func:`acquire_capacity_write_lock` in the same
    transaction. Requiring both a recent audit sample and a synchronous live
    measurement prevents execution from relying on a stale RuntimeControl
    snapshot or on a capacity probe that failed after the last price cycle.
    """

    samples = await latest_capacity_samples(session, limit=1)
    latest = samples[0] if samples else None
    sample_decision = assess_capacity_collection_gate(
        sample_capacity_point(latest) if latest is not None else None,
        now=now,
        budget_bytes=budget_bytes,
        minimum_free_bytes=minimum_free_bytes,
        sample_ttl=sample_ttl,
    )
    if not sample_decision.allowed:
        return AuthoritativeCapacityCheck(sample_decision, None)

    measurement = await read_capacity_guard_measurement(
        session,
        captured_at=now,
        storage_path_override=storage_path_override,
    )
    live_decision = assess_capacity_collection_gate(
        measurement.point,
        now=now,
        budget_bytes=budget_bytes,
        minimum_free_bytes=minimum_free_bytes,
        sample_ttl=sample_ttl,
        pending_bytes=pending_bytes,
    )
    return AuthoritativeCapacityCheck(live_decision, measurement)


async def read_capacity_measurement(
    session: AsyncSession,
    *,
    captured_at: datetime,
    storage_path_override: str | None = None,
) -> CapacityMeasurement:
    path_projection, path_parameters = _capacity_path_projection(storage_path_override)
    row = (
        (
            await session.execute(
                text(
                    f"""
                SELECT
                    pg_database_size(current_database())::bigint AS database_bytes,
                    pg_total_relation_size('clob_book_batches')::bigint
                        AS clob_book_batches_bytes,
                    pg_total_relation_size('clob_market_snapshots')::bigint
                        AS clob_market_snapshots_bytes,
                    (SELECT count(*) FROM clob_book_batches)::bigint
                        AS clob_batch_count,
                    (SELECT count(*) FROM clob_market_snapshots)::bigint
                        AS clob_snapshot_count,
                    (SELECT min(first_seen_at) FROM clob_book_batches)
                        AS oldest_batch_at,
                    (SELECT max(first_seen_at) FROM clob_book_batches)
                        AS newest_batch_at,
                    {path_projection}
                """
                ),
                path_parameters,
            )
        )
        .mappings()
        .one()
    )
    free_disk_path = (storage_path_override or str(row["data_directory"])).strip()
    free_disk_bytes, free_disk_error = _free_disk_measurement(free_disk_path)

    return CapacityMeasurement(
        point=CapacityPoint(
            captured_at=captured_at,
            database_bytes=int(row["database_bytes"]),
            clob_book_batches_bytes=int(row["clob_book_batches_bytes"]),
            clob_market_snapshots_bytes=int(row["clob_market_snapshots_bytes"]),
            free_disk_bytes=free_disk_bytes,
        ),
        clob_batch_count=int(row["clob_batch_count"]),
        clob_snapshot_count=int(row["clob_snapshot_count"]),
        oldest_batch_at=cast(datetime | None, row["oldest_batch_at"]),
        newest_batch_at=cast(datetime | None, row["newest_batch_at"]),
        free_disk_path=free_disk_path,
        free_disk_error=free_disk_error,
    )


async def capacity_baseline(
    session: AsyncSession,
    *,
    at_or_before: datetime,
) -> StorageCapacitySample | None:
    return cast(
        StorageCapacitySample | None,
        await session.scalar(
            select(StorageCapacitySample)
            .where(StorageCapacitySample.captured_at <= at_or_before)
            .order_by(StorageCapacitySample.captured_at.desc())
            .limit(1)
        ),
    )


async def latest_capacity_samples(
    session: AsyncSession,
    *,
    limit: int = 48,
) -> list[StorageCapacitySample]:
    rows = await session.scalars(
        select(StorageCapacitySample)
        .order_by(StorageCapacitySample.captured_at.desc())
        .limit(limit)
    )
    return list(rows)


async def store_capacity_sample(
    session: AsyncSession,
    measurement: CapacityMeasurement,
    *,
    policy_version: str,
    details: dict[str, Any],
) -> StorageCapacitySample:
    point = measurement.point
    sample = StorageCapacitySample(
        captured_at=point.captured_at,
        database_bytes=point.database_bytes,
        clob_book_batches_bytes=point.clob_book_batches_bytes,
        clob_market_snapshots_bytes=point.clob_market_snapshots_bytes,
        clob_batch_count=measurement.clob_batch_count,
        clob_snapshot_count=measurement.clob_snapshot_count,
        oldest_batch_at=measurement.oldest_batch_at,
        newest_batch_at=measurement.newest_batch_at,
        free_disk_bytes=point.free_disk_bytes,
        retention_policy_version=policy_version,
        details=details,
    )
    session.add(sample)
    await session.flush()
    await session.refresh(sample)
    return sample


def sample_capacity_point(sample: StorageCapacitySample) -> CapacityPoint:
    return CapacityPoint(
        captured_at=sample.captured_at,
        database_bytes=sample.database_bytes,
        clob_book_batches_bytes=sample.clob_book_batches_bytes,
        clob_market_snapshots_bytes=sample.clob_market_snapshots_bytes,
        free_disk_bytes=sample.free_disk_bytes,
    )
