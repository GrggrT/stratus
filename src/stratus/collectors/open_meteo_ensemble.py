from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from stratus.collectors.open_meteo import (
    ForecastBundle,
    ForecastValue,
    OpenMeteoError,
    SourceResponseEvidence,
    StationTarget,
    _response_evidence,
    parse_daily_extremes,
)


@dataclass(frozen=True, slots=True)
class EnsembleModel:
    api_id: str
    member_prefix: str
    member_count: int


ENSEMBLE_MODELS = {
    "gefs": EnsembleModel(
        api_id="gfs_seamless", member_prefix="gefs", member_count=31
    ),
    "ecmwf_ens": EnsembleModel(
        api_id="ecmwf_ifs025", member_prefix="ecmwf_ens", member_count=51
    ),
}
_MEMBER_PATTERN = re.compile(r"^temperature_2m_member(\d+)$")


def parse_ensemble_daily_extremes(
    payload: dict[str, Any],
    *,
    station_id: int,
    model: str,
    run_time: datetime,
    fetched_at: datetime,
) -> list[ForecastValue]:
    model_config = ENSEMBLE_MODELS.get(model)
    if model_config is None:
        raise ValueError(f"unsupported ensemble model: {model}")
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise OpenMeteoError("ensemble response is missing hourly time data")

    member_fields: list[tuple[int, str]] = []
    if "temperature_2m" in hourly:
        member_fields.append((0, "temperature_2m"))
    for field in hourly:
        match = _MEMBER_PATTERN.fullmatch(field)
        if match:
            member_fields.append((int(match.group(1)), field))
    member_fields.sort(key=lambda item: item[0])
    if len(member_fields) != model_config.member_count:
        raise OpenMeteoError(
            f"{model} returned {len(member_fields)} members; "
            f"expected {model_config.member_count}"
        )
    if len({number for number, _ in member_fields}) != len(member_fields):
        raise OpenMeteoError(f"{model} response contains duplicate member numbers")

    values: list[ForecastValue] = []
    for normalized_index, (_, field) in enumerate(member_fields, start=1):
        member_model = f"{model_config.member_prefix}_m{normalized_index:02d}"
        values.extend(
            parse_daily_extremes(
                {"hourly": {"time": hourly["time"], "temperature_2m": hourly[field]}},
                station_id=station_id,
                model=member_model,
                run_time=run_time,
                fetched_at=fetched_at,
            )
        )
    return values


class OpenMeteoEnsembleClient:
    def __init__(
        self,
        *,
        base_url: str,
        forecast_days: int,
        concurrency: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self.forecast_days = forecast_days
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> OpenMeteoEnsembleClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch(
        self,
        station: StationTarget,
        model: str,
        *,
        run_time: datetime,
        fetched_at: datetime,
    ) -> list[ForecastValue]:
        bundle = await self.fetch_bundle(
            station, model, run_time=run_time, fetched_at=fetched_at
        )
        return list(bundle.forecasts)

    async def fetch_bundle(
        self,
        station: StationTarget,
        model: str,
        *,
        run_time: datetime,
        fetched_at: datetime,
    ) -> ForecastBundle:
        model_config = ENSEMBLE_MODELS.get(model)
        if model_config is None:
            raise ValueError(f"unsupported ensemble model: {model}")
        if self._client is None:
            raise RuntimeError("OpenMeteoEnsembleClient must be used as an async context manager")
        params: dict[str, str | int | float] = {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": "temperature_2m",
            "models": model_config.api_id,
            "timezone": station.tz,
            "forecast_days": self.forecast_days,
            "temperature_unit": "celsius",
        }
        async with self._semaphore:
            payload, evidence = await self._get_json(params)
        return ForecastBundle(
            forecasts=tuple(
                parse_ensemble_daily_extremes(
                    payload,
                    station_id=station.id,
                    model=model,
                    run_time=run_time,
                    fetched_at=fetched_at,
                )
            ),
            trajectories=(),
            evidence=(evidence,),
        )

    async def _get_json(
        self, params: dict[str, str | int | float]
    ) -> tuple[dict[str, Any], SourceResponseEvidence]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                request_started_at = datetime.now(UTC)
                response = await self._client.get(self.base_url, params=params)
                response_received_at = datetime.now(UTC)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise OpenMeteoError("ensemble API returned a non-object JSON response")
                return payload, _response_evidence(
                    response,
                    request_started_at=request_started_at,
                    response_received_at=response_received_at,
                )
            except (httpx.HTTPError, ValueError, OpenMeteoError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise OpenMeteoError(
            f"ensemble API request failed after 3 attempts: {last_error}"
        ) from last_error

