from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any

import httpx

from stratus.db.models import WeatherKind

MODEL_API_IDS = {
    "ecmwf": "ecmwf_ifs025",
    "gfs": "gfs_seamless",
    "icon": "icon_seamless",
    "gem": "gem_seamless",
    "hrrr": "ncep_hrrr_conus",
    "nbm": "ncep_nbm_conus",
    "uk-2km": "ukmo_uk_deterministic_2km",
    "arome-france": "meteofrance_arome_france",
    "icon-eu": "icon_eu",
    "harmonie-arome": "knmi_harmonie_arome_netherlands",
    "jma-msm": "jma_msm",
    "kma-ldps": "kma_ldps",
    "grapes-global": "cma_grapes_global",
    "access-g": "bom_access_global",
    "aifs-single": "ecmwf_aifs025_single",
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
    run_id: int | None = None
    available_at: datetime | None = None
    ingest_id: int | None = None


@dataclass(frozen=True, slots=True)
class ForecastTrajectoryValue:
    station_id: int
    model: str
    run_time: datetime
    valid_date: date
    points: tuple[tuple[str, Decimal], ...]
    fetched_at: datetime
    run_id: int | None = None
    available_at: datetime | None = None
    ingest_id: int | None = None


@dataclass(frozen=True, slots=True)
class SourceResponseEvidence:
    request_started_at: datetime
    response_received_at: datetime
    source_url: str
    http_date: datetime | None
    etag: str | None
    response_body: bytes
    request_method: str = "GET"

    def __post_init__(self) -> None:
        for field_name, value in (
            ("request_started_at", self.request_started_at),
            ("response_received_at", self.response_received_at),
        ):
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise ValueError(f"{field_name} must be timezone-aware UTC")
        if self.response_received_at < self.request_started_at:
            raise ValueError("response_received_at cannot precede request_started_at")
        if self.http_date is not None and (
            self.http_date.tzinfo is None or self.http_date.utcoffset() != timedelta(0)
        ):
            raise ValueError("http_date must be timezone-aware UTC")
        if not self.source_url:
            raise ValueError("source_url cannot be empty")
        if not isinstance(self.response_body, bytes):
            raise TypeError("response_body must be bytes")
        if self.request_method != "GET":
            raise ValueError("request_method must be GET")

    @property
    def response_sha256(self) -> str:
        return sha256(self.response_body).hexdigest()

    @property
    def response_bytes(self) -> int:
        return len(self.response_body)


@dataclass(frozen=True, slots=True)
class ForecastBundle:
    forecasts: tuple[ForecastValue, ...]
    trajectories: tuple[ForecastTrajectoryValue, ...]
    evidence: tuple[SourceResponseEvidence, ...] = ()


def _parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _response_evidence(
    response: httpx.Response,
    *,
    request_started_at: datetime,
    response_received_at: datetime,
) -> SourceResponseEvidence:
    return SourceResponseEvidence(
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        source_url=str(response.request.url),
        http_date=_parse_http_date(response.headers.get("Date")),
        etag=response.headers.get("ETag"),
        response_body=response.content,
    )


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


def parse_daily_trajectories(
    payload: dict[str, Any],
    *,
    station_id: int,
    model: str,
    run_time: datetime,
    fetched_at: datetime,
) -> list[ForecastTrajectoryValue]:
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise OpenMeteoError("response is missing the hourly object")
    times = hourly.get("time")
    temperatures = hourly.get("temperature_2m")
    if not isinstance(times, list) or not isinstance(temperatures, list):
        raise OpenMeteoError("response is missing hourly time or temperature_2m arrays")
    if not times or len(times) != len(temperatures):
        raise OpenMeteoError("hourly time and temperature_2m arrays are empty or misaligned")

    grouped: dict[date, list[tuple[str, Decimal] | None]] = defaultdict(list)
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
        grouped[local_date].append((timestamp, temperature))

    values: list[ForecastTrajectoryValue] = []
    for valid_date, raw_points in sorted(grouped.items()):
        if any(point is None for point in raw_points) or not 20 <= len(raw_points) <= 25:
            continue
        points = tuple(point for point in raw_points if point is not None)
        values.append(
            ForecastTrajectoryValue(
                station_id=station_id,
                model=model,
                run_time=run_time,
                valid_date=valid_date,
                points=points,
                fetched_at=fetched_at,
            )
        )
    if not values:
        raise OpenMeteoError("hourly response contains no complete forecast trajectory")
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
        exact_run: bool = False,
    ) -> list[ForecastValue]:
        payload, _ = await self._fetch_hourly_payload(
            station, model, run_time=run_time, exact_run=exact_run
        )
        return parse_daily_extremes(
            payload,
            station_id=station.id,
            model=model,
            run_time=run_time,
            fetched_at=fetched_at,
        )

    async def fetch_bundle(
        self,
        station: StationTarget,
        model: str,
        *,
        run_time: datetime,
        fetched_at: datetime,
        exact_run: bool = False,
    ) -> ForecastBundle:
        payload, evidence = await self._fetch_hourly_payload(
            station, model, run_time=run_time, exact_run=exact_run
        )

        return ForecastBundle(
            forecasts=tuple(
                parse_daily_extremes(
                    payload,
                    station_id=station.id,
                    model=model,
                    run_time=run_time,
                    fetched_at=fetched_at,
                )
            ),
            trajectories=tuple(
                parse_daily_trajectories(
                    payload,
                    station_id=station.id,
                    model=model,
                    run_time=run_time,
                    fetched_at=fetched_at,
                )
            ),
            evidence=(evidence,),
        )

    async def _fetch_hourly_payload(
        self,
        station: StationTarget,
        model: str,
        *,
        run_time: datetime,
        exact_run: bool,
    ) -> tuple[dict[str, Any], SourceResponseEvidence]:
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
        if exact_run:
            params["run"] = run_time.strftime("%Y-%m-%dT%H:%M")
        async with self._semaphore:
            return await self._get_json(params)

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
                    raise OpenMeteoError("Open-Meteo returned a non-object JSON response")
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
            f"Open-Meteo request failed after 3 attempts: {last_error}"
        ) from last_error
