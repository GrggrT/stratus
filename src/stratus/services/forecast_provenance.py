from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol

from stratus.collectors.open_meteo import ForecastTrajectoryValue, ForecastValue

FORECAST_INGEST_PROTOCOL = "prospective-http-v1"
FORECAST_AVAILABILITY_SEMANTICS = (
    "database-first-seen-after-successful-http-parse-v1"
)
MODEL_INPUT_PROVENANCE_PROTOCOL = "forecast-input-union-v1"
FORECAST_TEMP_QUANTUM = Decimal("0.01")
FORECAST_TEMP_MIN = Decimal("-9999.99")
FORECAST_TEMP_MAX = Decimal("9999.99")


class MaterializedForecastLike(Protocol):
    id: int
    model: str
    run_id: int | None
    ingest_id: int | None
    run_time: datetime
    available_at: datetime


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("forecast provenance timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_text(value: Decimal) -> str:
    if not value.is_finite():
        raise ValueError("forecast provenance values must be finite")
    return format(value, "f")


def canonical_forecast_temp_c(value: Decimal) -> Decimal:
    """Match the persisted ``forecasts.temp_c NUMERIC(6,2)`` representation.

    PostgreSQL ``numeric`` rounds ties away from zero when coercing a value to
    a declared scale.  ``ROUND_HALF_UP`` has the same behavior for positive
    and negative Decimals.  Canonicalizing before the manifest is sealed keeps
    its immutable value identical to the value seen by the materialization
    trigger instead of relying on an implicit database-side conversion.
    """

    if not value.is_finite():
        raise ValueError("forecast provenance values must be finite")
    try:
        canonical = value.quantize(FORECAST_TEMP_QUANTUM, rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError("forecast temperature cannot be represented as NUMERIC(6,2)") from exc
    if canonical < FORECAST_TEMP_MIN or canonical > FORECAST_TEMP_MAX:
        raise ValueError("forecast temperature cannot be represented as NUMERIC(6,2)")
    if canonical == 0:
        return Decimal("0.00")
    return canonical


def canonical_trajectory_temp_c(value: Decimal) -> Decimal:
    """Match the JSON number emitted by trajectory materialization.

    ``forecast_trajectories.points`` is JSONB and the repository deliberately
    converts each Decimal to ``float`` before SQLAlchemy's standard JSON
    serialization.  Seal that same finite JSON number instead of an
    arbitrarily more precise Decimal which the stored row cannot reproduce.
    """

    if not value.is_finite():
        raise ValueError("forecast provenance values must be finite")
    try:
        serialized = json.dumps(float(value), allow_nan=False)
        canonical = Decimal(serialized)
    except (OverflowError, ValueError) as exc:
        raise ValueError("forecast trajectory temperature is not JSON-finite") from exc
    if canonical == 0:
        return Decimal("0.0")
    return canonical


def build_forecast_manifest(
    forecasts: Sequence[ForecastValue],
    trajectories: Sequence[ForecastTrajectoryValue],
) -> dict[str, Any]:
    """Return the deterministic parsed-output contract for one HTTP ingest.

    The raw response remains the primary source artifact.  This manifest seals
    the exact normalized values derived from it, so a later materialized-row
    retry cannot erase what the probability pipeline actually received.
    Database identifiers and availability timestamps are deliberately absent:
    they are assigned only after this manifest is sealed.
    """

    if not forecasts and not trajectories:
        raise ValueError("forecast ingest manifest cannot be empty")
    station_ids = {
        *(value.station_id for value in forecasts),
        *(value.station_id for value in trajectories),
    }
    if len(station_ids) != 1:
        raise ValueError("one forecast ingest must contain exactly one station")

    forecast_rows = sorted(
        (
            {
                "station_id": value.station_id,
                "model": value.model,
                "run_time": _utc_text(value.run_time),
                "valid_date": value.valid_date.isoformat(),
                "kind": value.kind.value,
                "temp_c": _decimal_text(canonical_forecast_temp_c(value.temp_c)),
            }
            for value in forecasts
        ),
        key=lambda row: (
            row["station_id"],
            row["model"],
            row["run_time"],
            row["valid_date"],
            row["kind"],
        ),
    )
    trajectory_rows = sorted(
        (
            {
                "station_id": value.station_id,
                "model": value.model,
                "run_time": _utc_text(value.run_time),
                "valid_date": value.valid_date.isoformat(),
                "points": [
                    {
                        "local_time": local_time,
                        "temp_c": _decimal_text(canonical_trajectory_temp_c(temp_c)),
                    }
                    for local_time, temp_c in value.points
                ],
            }
            for value in trajectories
        ),
        key=lambda row: (
            row["station_id"],
            row["model"],
            row["run_time"],
            row["valid_date"],
        ),
    )
    return {
        "schema": "forecast-normalized-manifest-v1",
        "station_id": next(iter(station_ids)),
        "forecast_count": len(forecast_rows),
        "trajectory_count": len(trajectory_rows),
        "forecasts": forecast_rows,
        "trajectories": trajectory_rows,
    }


def manifest_run_times(manifest: dict[str, Any]) -> set[datetime]:
    """Extract run timestamps from a validated v1 manifest."""

    if manifest.get("schema") != "forecast-normalized-manifest-v1":
        raise ValueError("unsupported forecast manifest schema")
    result: set[datetime] = set()
    for collection in ("forecasts", "trajectories"):
        rows = manifest.get(collection)
        if not isinstance(rows, list):
            raise ValueError(f"forecast manifest {collection} must be a list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("run_time"), str):
                raise ValueError("forecast manifest row has no run_time")
            parsed = datetime.fromisoformat(row["run_time"].replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise ValueError("forecast manifest run_time must be timezone-aware")
            result.add(parsed.astimezone(UTC))
    return result


def manifest_valid_dates(manifest: dict[str, Any]) -> set[date]:
    """Extract target dates from a validated v1 manifest."""

    if manifest.get("schema") != "forecast-normalized-manifest-v1":
        raise ValueError("unsupported forecast manifest schema")
    result: set[date] = set()
    for collection in ("forecasts", "trajectories"):
        rows = manifest.get(collection)
        if not isinstance(rows, list):
            raise ValueError(f"forecast manifest {collection} must be a list")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("valid_date"), str):
                raise ValueError("forecast manifest row has no valid_date")
            result.add(date.fromisoformat(row["valid_date"]))
    return result


def build_model_input_provenance(
    forecasts: Sequence[MaterializedForecastLike],
    trajectories: Sequence[MaterializedForecastLike],
    ingest_identities: Mapping[int, tuple[str, str]],
) -> dict[str, Any]:
    """Seal the exact materialized input union visible to one probability.

    The normalized-manifest and ingest record hashes make every prospective
    row independently traceable to immutable source bytes.  Legacy rows are
    retained but explicitly lower ``complete`` instead of being backfilled.
    """

    entries: list[dict[str, Any]] = []
    for row_type, rows in (("forecast", forecasts), ("trajectory", trajectories)):
        for row in rows:
            identity = (
                ingest_identities.get(row.ingest_id)
                if row.ingest_id is not None
                else None
            )
            entries.append(
                {
                    "row_type": row_type,
                    "row_id": row.id,
                    "model": row.model,
                    "run_id": row.run_id,
                    "ingest_id": row.ingest_id,
                    "run_time": _utc_text(row.run_time),
                    "available_at": _utc_text(row.available_at),
                    "normalized_manifest_sha256": identity[0] if identity else None,
                    "ingest_record_hash": identity[1] if identity else None,
                }
            )
    entries.sort(
        key=lambda row: (
            row["row_type"],
            row["model"],
            row["run_time"],
            row["row_id"],
        )
    )
    missing = [
        row
        for row in entries
        if row["run_id"] is None
        or row["ingest_id"] is None
        or row["normalized_manifest_sha256"] is None
        or row["ingest_record_hash"] is None
    ]
    available_times = [row["available_at"] for row in entries]
    return {
        "protocol": MODEL_INPUT_PROVENANCE_PROTOCOL,
        "complete": bool(entries) and not missing,
        "input_count": len(entries),
        "forecast_count": sum(row["row_type"] == "forecast" for row in entries),
        "trajectory_count": sum(row["row_type"] == "trajectory" for row in entries),
        "missing_evidence_count": len(missing),
        "ingest_ids": sorted(
            {row["ingest_id"] for row in entries if row["ingest_id"] is not None}
        ),
        "max_available_at": max(available_times) if available_times else None,
        "inputs": entries,
    }


def model_input_provenance_is_complete(components: object) -> bool:
    """Validate the compact contract consumed by health and the live gate."""

    if not isinstance(components, dict):
        return False
    detail = components.get("forecast_provenance")
    if (
        not isinstance(detail, dict)
        or detail.get("protocol") != MODEL_INPUT_PROVENANCE_PROTOCOL
        or detail.get("complete") is not True
        or detail.get("missing_evidence_count") != 0
    ):
        return False
    inputs = detail.get("inputs")
    if (
        not isinstance(inputs, list)
        or not inputs
        or detail.get("input_count") != len(inputs)
    ):
        return False
    ingest_ids = detail.get("ingest_ids")
    if not isinstance(ingest_ids, list) or not ingest_ids:
        return False
    expected_ingests = set(ingest_ids)
    if any(not isinstance(ingest_id, int) or ingest_id <= 0 for ingest_id in ingest_ids):
        return False
    for item in inputs:
        if not isinstance(item, dict):
            return False
        ingest_id = item.get("ingest_id")
        if (
            not isinstance(item.get("row_id"), int)
            or not isinstance(item.get("run_id"), int)
            or not isinstance(ingest_id, int)
            or ingest_id not in expected_ingests
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("normalized_manifest_sha256")))
            is None
            or re.fullmatch(r"[0-9a-f]{64}", str(item.get("ingest_record_hash")))
            is None
        ):
            return False
    return True
