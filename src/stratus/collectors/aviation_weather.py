from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx


class AviationWeatherError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetarObservation:
    station_id: int
    obs_time: datetime
    temp_c: Decimal
    raw_metar: str


def parse_metar_observations(
    payload: list[Any], station_ids_by_icao: dict[str, int]
) -> list[MetarObservation]:
    observations: list[MetarObservation] = []
    for item in payload:
        if not isinstance(item, dict):
            raise AviationWeatherError("METAR response contains a non-object entry")
        icao = str(item.get("icaoId") or "").upper()
        station_id = station_ids_by_icao.get(icao)
        if station_id is None:
            raise AviationWeatherError(f"METAR response contains an unexpected station: {icao}")
        raw_obs_time = item.get("obsTime")
        raw_temp = item.get("temp")
        raw_metar = item.get("rawOb")
        if raw_obs_time is None or raw_temp is None or not isinstance(raw_metar, str):
            raise AviationWeatherError(f"METAR response is incomplete for station {icao}")
        try:
            obs_time = datetime.fromtimestamp(float(raw_obs_time), tz=UTC)
            temp_c = Decimal(str(raw_temp))
        except (ValueError, TypeError, ArithmeticError, OSError) as exc:
            raise AviationWeatherError(
                f"METAR response has invalid values for station {icao}"
            ) from exc
        observations.append(
            MetarObservation(
                station_id=station_id,
                obs_time=obs_time,
                temp_c=temp_c,
                raw_metar=raw_metar,
            )
        )
    return observations


class AviationWeatherClient:
    def __init__(
        self,
        *,
        metar_url: str,
        user_agent: str,
        lookback_hours: int,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.metar_url = metar_url
        self.user_agent = user_agent
        self.lookback_hours = lookback_hours
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> AviationWeatherClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(30.0), headers={"User-Agent": self.user_agent}
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch_metars(self, station_ids_by_icao: dict[str, int]) -> list[MetarObservation]:
        if self._client is None:
            raise RuntimeError("AviationWeatherClient must be used as an async context manager")
        if not station_ids_by_icao:
            return []
        params = {
            "ids": ",".join(sorted(station_ids_by_icao)),
            "format": "json",
            "hours": self.lookback_hours,
        }
        payload = await self._get_json(params)
        return parse_metar_observations(payload, station_ids_by_icao)

    async def _get_json(self, params: dict[str, object]) -> list[Any]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.get(self.metar_url, params=params)
                if response.status_code == 204:
                    return []
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, list):
                    raise AviationWeatherError("AviationWeather returned a non-array JSON response")
                return payload
            except (httpx.HTTPError, ValueError, AviationWeatherError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2**attempt)
        raise AviationWeatherError(
            f"AviationWeather request failed after 3 attempts: {last_error}"
        ) from last_error
