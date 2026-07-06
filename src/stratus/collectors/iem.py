from __future__ import annotations

import asyncio
import csv
import io
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import httpx

from stratus.collectors.aviation_weather import MetarObservation


class IemError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class IemStation:
    station_id: int
    icao: str
    network: str
    source_station_id: str


def year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    if start >= end:
        return []
    chunks = []
    cursor = start
    while cursor < end:
        next_year = date(cursor.year + 1, 1, 1)
        chunk_end = min(next_year, end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    return chunks


def parse_iem_csv(
    content: str,
    *,
    station_id: int,
    start: date,
    end: date,
) -> list[MetarObservation]:
    lines = [line for line in content.splitlines() if line and not line.startswith("#")]
    reader = csv.DictReader(io.StringIO("\n".join(lines)))
    expected = {"station", "valid", "tmpc", "metar"}
    if reader.fieldnames is None or not expected.issubset(reader.fieldnames):
        raise IemError(f"IEM CSV has unexpected columns: {reader.fieldnames}")

    deduplicated: dict[datetime, MetarObservation] = {}
    start_time = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_time = datetime.combine(end, datetime.min.time(), tzinfo=UTC)
    for row in reader:
        raw_temp = (row.get("tmpc") or "").strip()
        raw_metar = (row.get("metar") or "").strip()
        if raw_temp.lower() in {"", "null", "m"} or raw_metar.lower() in {"", "null", "m"}:
            continue
        try:
            obs_time = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=UTC)
            temp_c = Decimal(raw_temp)
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise IemError("IEM CSV contains an invalid observation") from exc
        if start_time <= obs_time < end_time:
            deduplicated[obs_time] = MetarObservation(
                station_id=station_id,
                obs_time=obs_time,
                temp_c=temp_c,
                raw_metar=raw_metar,
            )
    return list(sorted(deduplicated.values(), key=lambda value: value.obs_time))


class IemClient:
    def __init__(
        self,
        *,
        asos_url: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.asos_url = asos_url
        self._client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> IemClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0), headers={"User-Agent": "STRATUS/0.1"}
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()

    async def fetch(
        self, station: IemStation, *, start: date, end: date
    ) -> list[MetarObservation]:
        if self._client is None:
            raise RuntimeError("IemClient must be used as an async context manager")
        params = [
            ("network", station.network),
            ("station", station.source_station_id),
            ("data", "tmpc"),
            ("data", "metar"),
            ("year1", str(start.year)),
            ("month1", str(start.month)),
            ("day1", str(start.day)),
            ("year2", str(end.year)),
            ("month2", str(end.month)),
            ("day2", str(end.day)),
            ("tz", "Etc/UTC"),
            ("format", "onlycomma"),
            ("latlon", "no"),
            ("elev", "no"),
            ("missing", "null"),
            ("trace", "null"),
            ("direct", "no"),
            ("report_type", "3"),
            ("report_type", "4"),
        ]
        content = await self._get_text(tuple(params))
        return parse_iem_csv(content, station_id=station.station_id, start=start, end=end)

    async def _get_text(self, params: tuple[tuple[str, str], ...]) -> str:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = await self._client.get(self.asos_url, params=params)
                response.raise_for_status()
                return response.text
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(min(15 * (2**attempt), 60))
        raise IemError(f"IEM request failed after 4 attempts: {last_error}") from last_error

