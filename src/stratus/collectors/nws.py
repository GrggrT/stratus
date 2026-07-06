from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from stratus.collectors.open_meteo import ForecastValue, StationTarget
from stratus.db.models import WeatherKind


class NwsForecastError(RuntimeError):
    pass


def _temperature_c(value: object, unit: object) -> Decimal:
    try:
        temperature = Decimal(str(value))
    except (ValueError, ArithmeticError) as exc:
        raise NwsForecastError("NWS period contains an invalid temperature") from exc
    if unit == "F":
        return (temperature - Decimal(32)) * Decimal(5) / Decimal(9)
    if unit == "C":
        return temperature
    raise NwsForecastError(f"unsupported NWS temperature unit: {unit}")


def parse_nws_hourly_forecast(
    payload: dict[str, Any],
    *,
    station: StationTarget,
    fetched_at: datetime,
) -> list[ForecastValue]:
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise NwsForecastError("NWS response is missing properties")
    update_time = properties.get("updateTime")
    periods = properties.get("periods")
    if not isinstance(update_time, str) or not isinstance(periods, list):
        raise NwsForecastError("NWS response is missing updateTime or periods")
    try:
        run_time = datetime.fromisoformat(update_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NwsForecastError("NWS updateTime is invalid") from exc
    if run_time.tzinfo is None:
        raise NwsForecastError("NWS updateTime must include a timezone")

    timezone = ZoneInfo(station.tz)
    grouped: dict[object, list[tuple[datetime, datetime, Decimal]]] = defaultdict(list)
    for period in periods:
        if not isinstance(period, dict):
            raise NwsForecastError("NWS periods contain a non-object value")
        try:
            start = datetime.fromisoformat(str(period["startTime"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(period["endTime"]).replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise NwsForecastError("NWS period contains invalid timestamps") from exc
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise NwsForecastError("NWS period timestamps are not timezone-aware hourly ranges")
        local_start = start.astimezone(timezone)
        grouped[local_start.date()].append(
            (
                local_start,
                end.astimezone(timezone),
                _temperature_c(
                    period.get("temperature"), period.get("temperatureUnit")
                ),
            )
        )

    values = []
    for valid_date, entries in sorted(grouped.items()):
        entries.sort(key=lambda entry: entry[0])
        day_start = datetime.combine(valid_date, datetime.min.time(), tzinfo=timezone)
        day_end = day_start + timedelta(days=1)
        contiguous = all(
            current[1] == following[0]
            for current, following in zip(entries, entries[1:], strict=False)
        )
        if entries[0][0] != day_start or entries[-1][1] != day_end or not contiguous:
            continue
        temperatures = [entry[2] for entry in entries]
        values.extend(
            (
                ForecastValue(
                    station_id=station.id,
                    model="nws",
                    run_time=run_time,
                    valid_date=valid_date,
                    kind=WeatherKind.HIGH,
                    temp_c=max(temperatures),
                    fetched_at=fetched_at,
                ),
                ForecastValue(
                    station_id=station.id,
                    model="nws",
                    run_time=run_time,
                    valid_date=valid_date,
                    kind=WeatherKind.LOW,
                    temp_c=min(temperatures),
                    fetched_at=fetched_at,
                ),
            )
        )
    if not values:
        raise NwsForecastError("NWS response contains no fully covered local day")
    return values


class NwsForecastClient:
    def __init__(
        self,
        *,
        base_url: str,
        user_agent: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> NwsForecastClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30),
                headers={"User-Agent": self.user_agent, "Accept": "application/geo+json"},
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch(self, station: StationTarget, *, fetched_at: datetime) -> list[ForecastValue]:
        point = await self._get_json(f"{self.base_url}/points/{station.lat},{station.lon}")
        properties = point.get("properties")
        hourly_url = properties.get("forecastHourly") if isinstance(properties, dict) else None
        if not isinstance(hourly_url, str) or not hourly_url.startswith("https://"):
            raise NwsForecastError("NWS point response has no valid forecastHourly link")
        forecast = await self._get_json(hourly_url)
        return parse_nws_hourly_forecast(
            forecast, station=station, fetched_at=fetched_at
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("NwsForecastClient must be used as an async context manager")
        last_error = None
        for attempt in range(3):
            try:
                response = await self._client.get(url)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise NwsForecastError("NWS returned non-object JSON")
                return payload
            except (httpx.HTTPError, ValueError, NwsForecastError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise NwsForecastError(f"NWS request failed after 3 attempts: {last_error}") from last_error
