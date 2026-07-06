from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from stratus.collectors.open_meteo import MODEL_API_IDS, OpenMeteoError, StationTarget
from stratus.db.models import WeatherKind


@dataclass(frozen=True, slots=True)
class HistoricalForecastValue:
    station_id: int
    model: str
    valid_date: date
    kind: WeatherKind
    horizon_h: int
    temp_c: Decimal
    source: str
    fetched_at: datetime


def parse_previous_runs(
    payload: dict[str, Any],
    *,
    station_id: int,
    model: str,
    horizon_days: tuple[int, ...],
    fetched_at: datetime,
) -> list[HistoricalForecastValue]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict) or not isinstance(hourly.get("time"), list):
        raise OpenMeteoError("previous-runs response is missing hourly time data")
    times = hourly["time"]
    values: list[HistoricalForecastValue] = []
    for horizon_day in horizon_days:
        field = f"temperature_2m_previous_day{horizon_day}"
        temperatures = hourly.get(field)
        if not isinstance(temperatures, list) or len(temperatures) != len(times):
            raise OpenMeteoError(f"previous-runs response has invalid {field} data")
        grouped: dict[date, list[Decimal | None]] = defaultdict(list)
        for timestamp, raw_temp in zip(times, temperatures, strict=True):
            if not isinstance(timestamp, str):
                raise OpenMeteoError("previous-runs response contains an invalid timestamp")
            try:
                valid_date = date.fromisoformat(timestamp[:10])
                temperature = None if raw_temp is None else Decimal(str(raw_temp))
            except (ValueError, ArithmeticError) as exc:
                raise OpenMeteoError("previous-runs response contains an invalid value") from exc
            grouped[valid_date].append(temperature)
        for valid_date, daily_temperatures in grouped.items():
            if not daily_temperatures or any(value is None for value in daily_temperatures):
                continue
            complete = [value for value in daily_temperatures if value is not None]
            common = {
                "station_id": station_id,
                "model": model,
                "valid_date": valid_date,
                "horizon_h": horizon_day * 24,
                "source": "open_meteo_previous_runs",
                "fetched_at": fetched_at,
            }
            values.extend(
                (
                    HistoricalForecastValue(
                        **common, kind=WeatherKind.HIGH, temp_c=max(complete)
                    ),
                    HistoricalForecastValue(
                        **common, kind=WeatherKind.LOW, temp_c=min(complete)
                    ),
                )
            )
    return values


class OpenMeteoPreviousRunsClient:
    def __init__(
        self,
        *,
        base_url: str,
        concurrency: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url
        self._semaphore = asyncio.Semaphore(concurrency)
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> OpenMeteoPreviousRunsClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch(
        self,
        station: StationTarget,
        model: str,
        *,
        start_date: date,
        end_date: date,
        horizon_days: tuple[int, ...],
        fetched_at: datetime,
    ) -> list[HistoricalForecastValue]:
        if model not in MODEL_API_IDS:
            raise ValueError(f"unsupported previous-runs model: {model}")
        if self._client is None:
            raise RuntimeError(
                "OpenMeteoPreviousRunsClient must be used as an async context manager"
            )
        fields = ",".join(
            f"temperature_2m_previous_day{day}" for day in horizon_days
        )
        params = {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": fields,
            "models": MODEL_API_IDS[model],
            "timezone": station.tz,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "temperature_unit": "celsius",
        }
        async with self._semaphore:
            payload = await self._get_json(params)
        return parse_previous_runs(
            payload,
            station_id=station.id,
            model=model,
            horizon_days=horizon_days,
            fetched_at=fetched_at,
        )

    async def _get_json(self, params: dict[str, object]) -> dict[str, Any]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = await self._client.get(self.base_url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise OpenMeteoError("previous-runs API returned invalid JSON")
                return payload
            except (httpx.HTTPError, ValueError, OpenMeteoError) as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(min(5 * (2**attempt), 30))
        raise OpenMeteoError(
            f"previous-runs request failed after 4 attempts: {last_error}"
        ) from last_error

