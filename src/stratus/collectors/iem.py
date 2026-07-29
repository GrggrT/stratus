from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import ClassVar

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


_SAFE_RESPONSE_HEADERS = frozenset(
    {
        "cache-control",
        "content-length",
        "content-type",
        "date",
        "etag",
        "expires",
        "last-modified",
    }
)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class IemRequestManifest:
    """Canonical description of one IEM ASOS request.

    ``params`` deliberately remains an ordered tuple because IEM accepts repeated
    ``data`` and ``report_type`` keys.  Serialising it as a list of name/value
    objects makes that ordering and multiplicity unambiguous in durable evidence.
    """

    schema_version: ClassVar[str] = "stratus.iem-request.v1"

    source_url: str
    station_id: int
    icao: str
    network: str
    source_station_id: str
    start: date
    end: date
    params: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if self.station_id <= 0:
            raise ValueError("station_id must be positive")
        if self.start >= self.end:
            raise ValueError("IEM request start must precede end")
        if not self.source_url:
            raise ValueError("source_url is required")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_url": self.source_url,
            "station_id": self.station_id,
            "icao": self.icao.upper(),
            "network": self.network,
            "source_station_id": self.source_station_id.upper(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "params": [{"name": name, "value": value} for name, value in self.params],
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.to_payload())).hexdigest()

    @property
    def request_url(self) -> str:
        url = httpx.URL(self.source_url)
        for name, value in self.params:
            url = url.copy_add_param(name, value)
        return str(url)


@dataclass(frozen=True, slots=True)
class IemParsedRow:
    """A normalized observation bound to its exact selected CSV record bytes."""

    observation: MetarObservation
    source_row: bytes

    def __post_init__(self) -> None:
        if not self.source_row:
            raise ValueError("source_row must preserve non-empty CSV record bytes")


@dataclass(frozen=True, slots=True)
class IemFetchResult:
    """One immutable IEM response plus the rows parsed from those exact bytes."""

    request_manifest: IemRequestManifest
    request_started_at: datetime
    response_received_at: datetime
    response_status_code: int
    response_headers: tuple[tuple[str, str], ...]
    response_body: bytes
    observations: tuple[MetarObservation, ...]
    exact_rows: tuple[IemParsedRow, ...] = ()

    def __post_init__(self) -> None:
        for field_name, value in (
            ("request_started_at", self.request_started_at),
            ("response_received_at", self.response_received_at),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware")
        if self.response_received_at < self.request_started_at:
            raise ValueError("response_received_at cannot precede request_started_at")
        if not 200 <= self.response_status_code < 300:
            raise ValueError("successful IEM evidence requires a 2xx response")
        if tuple(sorted(self.response_headers)) != self.response_headers:
            raise ValueError("response_headers must be deterministically ordered")
        if any(name.lower() not in _SAFE_RESPONSE_HEADERS for name, _ in self.response_headers):
            raise ValueError("response_headers contains a non-allowlisted header")
        parsed_observations = tuple(row.observation for row in self.exact_rows)
        if self.exact_rows and parsed_observations != self.observations:
            raise ValueError("exact_rows and observations must describe the same ordered rows")

    @property
    def request_manifest_sha256(self) -> str:
        return self.request_manifest.sha256

    @property
    def response_sha256(self) -> str:
        return hashlib.sha256(self.response_body).hexdigest()

    @property
    def response_bytes(self) -> int:
        return len(self.response_body)

    @property
    def parsed_rows(self) -> tuple[IemParsedRow, ...]:
        """Normalized rows bound to the exact CSV record selected by the parser."""

        return self.exact_rows


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
    source_station_id: str | None = None,
) -> list[MetarObservation]:
    rows = parse_iem_csv_rows(
        content.encode("utf-8"),
        station_id=station_id,
        start=start,
        end=end,
        source_station_id=source_station_id,
    )
    return [row.observation for row in rows]


def parse_iem_csv_rows(
    content: bytes,
    *,
    station_id: int,
    start: date,
    end: date,
    source_station_id: str | None = None,
) -> list[IemParsedRow]:
    """Parse IEM CSV while retaining each selected physical record exactly.

    IEM METAR records are single-line CSV records.  A quoted multi-line record is
    rejected rather than reconstructed because its byte membership would be
    ambiguous for the append-only point ledger.
    """

    physical_lines = content.splitlines(keepends=True)
    decoded: list[tuple[bytes, str]] = []
    for raw_line in physical_lines:
        try:
            text_line = raw_line.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IemError("IEM CSV response is not valid UTF-8") from exc
        if text_line.strip() and not text_line.startswith("#"):
            decoded.append((raw_line, text_line))
    if not decoded:
        raise IemError("IEM CSV has no header")
    fieldnames = _parse_single_csv_record(decoded[0][1])
    expected = {"station", "valid", "tmpc", "metar"}
    if not expected.issubset(fieldnames):
        raise IemError(f"IEM CSV has unexpected columns: {fieldnames}")

    candidates: dict[datetime, list[IemParsedRow]] = {}
    start_time = datetime.combine(start, datetime.min.time(), tzinfo=UTC)
    end_time = datetime.combine(end, datetime.min.time(), tzinfo=UTC)
    for source_row, text_line in decoded[1:]:
        values = _parse_single_csv_record(text_line)
        if len(values) != len(fieldnames):
            raise IemError("IEM CSV contains a row with unexpected columns")
        row = dict(zip(fieldnames, values, strict=True))
        source_station = (row.get("station") or "").strip().upper()
        if source_station_id is not None and source_station != source_station_id.upper():
            raise IemError(
                "IEM CSV contains an unexpected station: "
                f"{source_station or '<missing>'} (expected {source_station_id.upper()})"
            )
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
            candidates.setdefault(obs_time, []).append(
                IemParsedRow(
                    observation=MetarObservation(
                        station_id=station_id,
                        obs_time=obs_time,
                        temp_c=temp_c,
                        raw_metar=raw_metar,
                    ),
                    source_row=source_row,
                )
            )

    resolved = [
        _resolve_iem_correction(obs_time, values) for obs_time, values in candidates.items()
    ]
    return sorted(resolved, key=lambda value: value.observation.obs_time)


def _parse_single_csv_record(content: str) -> list[str]:
    try:
        rows = list(csv.reader(io.StringIO(content), strict=True))
    except csv.Error as exc:
        raise IemError("IEM CSV contains an invalid or multi-line record") from exc
    if len(rows) != 1:
        raise IemError("IEM CSV contains an invalid or multi-line record")
    return rows[0]


def _resolve_iem_correction(
    obs_time: datetime, candidates: list[IemParsedRow]
) -> IemParsedRow:
    unique: dict[tuple[Decimal, str], IemParsedRow] = {}
    for candidate in candidates:
        key = (candidate.observation.temp_c, candidate.observation.raw_metar)
        previous = unique.get(key)
        if previous is None or candidate.source_row < previous.source_row:
            unique[key] = candidate
    if len(unique) == 1:
        return next(iter(unique.values()))

    corrected = [
        candidate
        for candidate in unique.values()
        if "COR" in candidate.observation.raw_metar.upper().split()
    ]
    if len(corrected) == 1:
        return corrected[0]
    if corrected:
        detail = "multiple distinct corrected reports"
    else:
        detail = "multiple distinct reports without a unique correction"
    raise IemError(f"ambiguous IEM observations at {obs_time.isoformat()}: {detail}")


class IemClient:
    def __init__(
        self,
        *,
        asos_url: str,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        max_response_bytes: int | None = None,
        request_budget: int | None = None,
    ) -> None:
        if max_response_bytes is not None and max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive")
        if request_budget is not None and request_budget < 1:
            raise ValueError("request_budget must be positive")
        self.asos_url = str(httpx.URL(asos_url))
        self._client = http_client
        self._owns_client = http_client is None
        self._clock = clock or (lambda: datetime.now(UTC))
        self._max_response_bytes = max_response_bytes
        self._remaining_request_budget = request_budget

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
        result = await self.fetch_with_evidence(station, start=start, end=end)
        return list(result.observations)

    async def fetch_with_evidence(
        self, station: IemStation, *, start: date, end: date
    ) -> IemFetchResult:
        if self._client is None:
            raise RuntimeError("IemClient must be used as an async context manager")
        params = (
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
        )
        manifest = IemRequestManifest(
            source_url=self.asos_url,
            station_id=station.station_id,
            icao=station.icao,
            network=station.network,
            source_station_id=station.source_station_id,
            start=start,
            end=end,
            params=params,
        )
        request_started_at, response_received_at, response = await self._get_response(params)
        parsed_rows = parse_iem_csv_rows(
            response.content,
            station_id=station.station_id,
            start=start,
            end=end,
            source_station_id=station.source_station_id,
        )
        observations = tuple(row.observation for row in parsed_rows)
        safe_headers = tuple(
            sorted(
                (name.lower(), value)
                for name, value in response.headers.multi_items()
                if name.lower() in _SAFE_RESPONSE_HEADERS
            )
        )
        return IemFetchResult(
            request_manifest=manifest,
            request_started_at=request_started_at,
            response_received_at=response_received_at,
            response_status_code=response.status_code,
            response_headers=safe_headers,
            response_body=response.content,
            observations=observations,
            exact_rows=tuple(parsed_rows),
        )

    async def _get_text(self, params: tuple[tuple[str, str], ...]) -> str:
        _, _, response = await self._get_response(params)
        try:
            return response.content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise IemError("IEM CSV response is not valid UTF-8") from exc

    async def _get_response(
        self, params: tuple[tuple[str, str], ...]
    ) -> tuple[datetime, datetime, httpx.Response]:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(4):
            if self._remaining_request_budget is not None:
                if self._remaining_request_budget <= 0:
                    raise IemError("IEM per-run HTTP request budget is exhausted")
                self._remaining_request_budget -= 1
            request_started_at = self._utc_now()
            response: httpx.Response | None = None
            try:
                request = self._client.build_request("GET", self.asos_url, params=params)
                response = await self._client.send(request, stream=True)
                response.raise_for_status()
                chunks: list[bytes] = []
                response_bytes = 0
                async for chunk in response.aiter_bytes():
                    response_bytes += len(chunk)
                    if (
                        self._max_response_bytes is not None
                        and response_bytes > self._max_response_bytes
                    ):
                        raise IemError(
                            "IEM response exceeds configured recovery byte cap: "
                            f"{response_bytes} > {self._max_response_bytes}"
                        )
                    chunks.append(chunk)
                response_received_at = self._utc_now()
                retained = httpx.Response(
                    status_code=response.status_code,
                    headers=response.headers,
                    content=b"".join(chunks),
                    request=request,
                )
                return request_started_at, response_received_at, retained
            except IemError:
                raise
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(min(15 * (2**attempt), 60))
            finally:
                if response is not None:
                    await response.aclose()
        raise IemError(f"IEM request failed after 4 attempts: {last_error}") from last_error

    def _utc_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("IEM clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

