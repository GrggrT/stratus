from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx

from stratus.db.models import WeatherKind

MODEL_API_IDS = {
    "ecmwf": "ecmwf_ifs025",
    "gfs": "gfs_seamless",
    "icon": "icon_seamless",
    "gem": "gem_seamless",
}


class OpenMeteoError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class StationTarget:
    id: int
    icao: str
    lat: float
    lon: float
    tz: str


@dataclass(frozen=True, slots=True)
class ForecastValue:
    station_id: int
    model: str
    run_time: datetime
    valid_date: date
    kind: WeatherKind
    temp_c: Decimal
    fetched_at: datetime


def parse_daily_extremes(
    payload: dict[str, Any],
    *,
    station_id: int,
    model: str,
    run_time: datetime,
    fetched_at: datetime,
) -> list[ForecastValue]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise OpenMeteoError("response is missing the hourly object")

    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temperatures, list):
        raise OpenMeteoError("response is missing hourly time or temperature_2m arrays")
    if not times or len(times) != len(temperatures):
        raise OpenMeteoError("hourly time and temperature_2m arrays are empty or misaligned")

    grouped: dict[date, list[Decimal | None]] = defaultdict(list)
    for timestamp, raw_temp in zip(times, temperatures, strict=True):
        if not isinstance(timestamp, str):
            raise OpenMeteoError("hourly response contains an invalid timestamp")
        try:
            local_date = date.fromisoformat(timestamp[:10])
        except (ValueError, ArithmeticError) as exc:
            raise OpenMeteoError("hourly response contains an invalid timestamp") from exc

        if raw_temp is None:
            grouped[local_date].append(None)
            continue
        try:
            temperature = Decimal(str(raw_temp))
        except (ValueError, ArithmeticError) as exc:
            raise OpenMeteoError("hourly response contains an invalid temperature") from exc
        grouped[local_date].append(temperature)

    values: list[ForecastValue] = []
    for valid_date, daily_temperatures in sorted(grouped.items()):
        # Models have different forecast horizons. Open-Meteo pads the unavailable
        # tail with nulls; a partially covered day cannot produce a valid daily extreme.
        if any(temperature is None for temperature in daily_temperatures):
            continue
        complete_temperatures = [
            temperature for temperature in daily_temperatures if temperature is not None
        ]
        values.extend(
            (
                ForecastValue(
                    station_id=station_id,
                    model=model,
                    run_time=run_time,
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=max(complete_temperatures),
                    fetched_at=fetched_at,
                ),
                ForecastValue(
                    station_id=station_id,
                    model=model,
                    run_time=run_time,
                    valid_date=valid_date,
                    kind=WeatherKind.LOW,
                    temp_c=min(complete_temperatures),
                    fetched_at=fetched_at,
                ),
            )
        )
    if not values:
        raise OpenMeteoError("hourly response contains no fully covered forecast day")
    return values


class OpenMeteoClient:
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

    async def __aenter__(self) -> OpenMeteoClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
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
        if model not in MODEL_API_IDS:
            raise ValueError(f"unsupported Open-Meteo model: {model}")
        if self._client is None:
            raise RuntimeError("OpenMeteoClient must be used as an async context manager")

        params: dict[str, str | int | float] = {
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": "temperature_2m",
            "models": MODEL_API_IDS[model],
            "timezone": station.tz,
            "forecast_days": self.forecast_days,
            "temperature_unit": "celsius",
        }
        async with self._semaphore:
            payload = await self._get_json(params)
        return parse_daily_extremes(
            payload,
            station_id=station.id,
            model=model,
            run_time=run_time,
            fetched_at=fetched_at,
        )

    async def _get_json(self, params: dict[str, str | int | float]) -> dict[str, Any]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(self.base_url, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise OpenMeteoError("Open-Meteo returned a non-object JSON response")
                return payload
            except (httpx.HTTPError, ValueError, OpenMeteoError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise OpenMeteoError(
            f"Open-Meteo request failed after 3 attempts: {last_error}"
        ) from last_error
