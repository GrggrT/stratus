from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True, slots=True)
class ForecastProvenanceCollectorSummary:
    collector: str
    request_model: str
    ingests: int
    stations: int
    responses: int
    raw_bytes: int
    verified_upstream_inits: int
    latest_first_seen_at: datetime | None


@dataclass(frozen=True, slots=True)
class ForecastProvenanceSummary:
    opened_at: datetime
    definition_version: str
    definition_hash: str
    ingests: int
    stations: int
    responses: int
    raw_bytes: int
    verified_upstream_inits: int
    single_run_ingests: int
    pipeline_fingerprints: int
    materialized_forecasts: int
    materialized_trajectories: int
    latest_first_seen_at: datetime | None
    average_http_latency_ms: float | None
    p95_http_latency_ms: float | None
    average_ingestion_lag_ms: float | None
    p95_ingestion_lag_ms: float | None
    average_verified_upstream_latency_minutes: float | None
    p95_verified_upstream_latency_minutes: float | None
    collectors: tuple[ForecastProvenanceCollectorSummary, ...]


async def forecast_provenance_summary(
    session: AsyncSession,
) -> ForecastProvenanceSummary:
    row = (
        await session.execute(
            text(
                r"""
                WITH response_rollup AS (
                    SELECT ingest_id,
                           count(*)::bigint AS responses,
                           coalesce(sum(response_bytes), 0)::bigint AS raw_bytes,
                           max(response_received_at) AS last_response_at
                    FROM forecast_source_responses
                    GROUP BY ingest_id
                ), ingest_lags AS (
                    SELECT i.id,
                           extract(epoch FROM (i.first_seen_at - r.last_response_at))
                               * 1000.0 AS lag_ms
                    FROM forecast_ingests i
                    JOIN response_rollup r ON r.ingest_id = i.id
                )
                SELECT runtime.opened_at,
                       runtime.definition_version,
                       runtime.definition_hash,
                       (SELECT count(*) FROM forecast_ingests)::bigint AS ingests,
                       (SELECT count(DISTINCT station_id) FROM forecast_ingests)::bigint
                           AS stations,
                       (SELECT count(*) FROM forecast_source_responses)::bigint AS responses,
                       (SELECT coalesce(sum(response_bytes), 0)
                          FROM forecast_source_responses)::bigint AS raw_bytes,
                       (SELECT count(*) FROM forecast_ingests
                         WHERE upstream_init_verified)::bigint AS verified_upstream_inits,
                       (SELECT count(*) FROM forecast_ingests
                         WHERE run_id IS NOT NULL)::bigint AS single_run_ingests,
                       (SELECT count(DISTINCT pipeline_fingerprint)
                          FROM forecast_ingests)::bigint AS pipeline_fingerprints,
                       (SELECT count(*) FROM forecasts
                         WHERE ingest_id IS NOT NULL)::bigint AS materialized_forecasts,
                       (SELECT count(*) FROM forecast_trajectories
                         WHERE ingest_id IS NOT NULL)::bigint AS materialized_trajectories,
                       (SELECT max(first_seen_at) FROM forecast_ingests)
                           AS latest_first_seen_at,
                       (SELECT avg(extract(epoch FROM
                            (response_received_at - request_started_at)) * 1000.0)
                          FROM forecast_source_responses) AS average_http_latency_ms,
                       (SELECT percentile_cont(0.95) WITHIN GROUP (
                            ORDER BY extract(epoch FROM
                                (response_received_at - request_started_at)) * 1000.0)
                          FROM forecast_source_responses) AS p95_http_latency_ms,
                       (SELECT avg(lag_ms) FROM ingest_lags)
                           AS average_ingestion_lag_ms,
                       (SELECT percentile_cont(0.95) WITHIN GROUP (ORDER BY lag_ms)
                          FROM ingest_lags) AS p95_ingestion_lag_ms,
                       (SELECT avg(extract(epoch FROM
                            (first_seen_at - upstream_init_time)) / 60.0)
                          FROM forecast_ingests
                         WHERE upstream_init_verified)
                           AS average_verified_upstream_latency_minutes,
                       (SELECT percentile_cont(0.95) WITHIN GROUP (
                            ORDER BY extract(epoch FROM
                                (first_seen_at - upstream_init_time)) / 60.0)
                          FROM forecast_ingests
                         WHERE upstream_init_verified)
                           AS p95_verified_upstream_latency_minutes
                FROM forecast_provenance_runtime runtime
                WHERE runtime.id = 1
                """
            )
        )
    ).mappings().one()
    collector_rows = (
        await session.execute(
            text(
                r"""
                WITH response_rollup AS (
                    SELECT ingest_id,
                           count(*)::bigint AS responses,
                           coalesce(sum(response_bytes), 0)::bigint AS raw_bytes
                    FROM forecast_source_responses
                    GROUP BY ingest_id
                )
                SELECT i.collector,
                       i.request_model,
                       count(*)::bigint AS ingests,
                       count(DISTINCT i.station_id)::bigint AS stations,
                       coalesce(sum(r.responses), 0)::bigint AS responses,
                       coalesce(sum(r.raw_bytes), 0)::bigint AS raw_bytes,
                       count(*) FILTER (WHERE i.upstream_init_verified)::bigint
                           AS verified_upstream_inits,
                       max(i.first_seen_at) AS latest_first_seen_at
                FROM forecast_ingests i
                LEFT JOIN response_rollup r ON r.ingest_id = i.id
                GROUP BY i.collector, i.request_model
                ORDER BY i.collector, i.request_model
                """
            )
        )
    ).mappings().all()
    return ForecastProvenanceSummary(
        opened_at=row["opened_at"],
        definition_version=row["definition_version"],
        definition_hash=row["definition_hash"],
        ingests=int(row["ingests"]),
        stations=int(row["stations"]),
        responses=int(row["responses"]),
        raw_bytes=int(row["raw_bytes"]),
        verified_upstream_inits=int(row["verified_upstream_inits"]),
        single_run_ingests=int(row["single_run_ingests"]),
        pipeline_fingerprints=int(row["pipeline_fingerprints"]),
        materialized_forecasts=int(row["materialized_forecasts"]),
        materialized_trajectories=int(row["materialized_trajectories"]),
        latest_first_seen_at=row["latest_first_seen_at"],
        average_http_latency_ms=(
            float(row["average_http_latency_ms"])
            if row["average_http_latency_ms"] is not None
            else None
        ),
        p95_http_latency_ms=(
            float(row["p95_http_latency_ms"])
            if row["p95_http_latency_ms"] is not None
            else None
        ),
        average_ingestion_lag_ms=(
            float(row["average_ingestion_lag_ms"])
            if row["average_ingestion_lag_ms"] is not None
            else None
        ),
        p95_ingestion_lag_ms=(
            float(row["p95_ingestion_lag_ms"])
            if row["p95_ingestion_lag_ms"] is not None
            else None
        ),
        average_verified_upstream_latency_minutes=(
            float(row["average_verified_upstream_latency_minutes"])
            if row["average_verified_upstream_latency_minutes"] is not None
            else None
        ),
        p95_verified_upstream_latency_minutes=(
            float(row["p95_verified_upstream_latency_minutes"])
            if row["p95_verified_upstream_latency_minutes"] is not None
            else None
        ),
        collectors=tuple(
            ForecastProvenanceCollectorSummary(
                collector=item["collector"],
                request_model=item["request_model"],
                ingests=int(item["ingests"]),
                stations=int(item["stations"]),
                responses=int(item["responses"]),
                raw_bytes=int(item["raw_bytes"]),
                verified_upstream_inits=int(item["verified_upstream_inits"]),
                latest_first_seen_at=item["latest_first_seen_at"],
            )
            for item in collector_rows
        ),
    )
