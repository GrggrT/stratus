import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import httpx
import pytest

from stratus.collectors.nws import (
    NwsForecastClient,
    NwsForecastError,
    parse_nws_hourly_forecast,
)
from stratus.collectors.open_meteo import StationTarget
from stratus.db.models import WeatherKind

STATION = StationTarget(1, "KLGA", 40.7769, -73.874, "America/New_York")


def _payload(hours: int = 48) -> dict:
    start = datetime(2026, 7, 7, tzinfo=UTC) + timedelta(hours=4)
    periods = []
    for index in range(hours):
        period_start = start + timedelta(hours=index)
        periods.append(
            {
                "startTime": period_start.isoformat(),
                "endTime": (period_start + timedelta(hours=1)).isoformat(),
                "temperature": 68 + index % 10,
                "temperatureUnit": "F",
            }
        )
    return {
        "properties": {
            "updateTime": "2026-07-06T18:35:26+00:00",
            "periods": periods,
        }
    }


def test_parse_nws_hourly_forecast_keeps_only_complete_local_days() -> None:
    values = parse_nws_hourly_forecast(
        _payload(),
        station=STATION,
        fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC),
    )

    assert len(values) == 4
    assert {value.valid_date.isoformat() for value in values} == {
        "2026-07-07",
        "2026-07-08",
    }
    high = next(
        value
        for value in values
        if value.valid_date.isoformat() == "2026-07-07" and value.kind is WeatherKind.HIGH
    )
    assert float(high.temp_c) == pytest.approx(25.0)
    assert high.model == "nws"
    assert high.run_time == datetime(2026, 7, 6, 18, 35, 26, tzinfo=UTC)


def test_parse_nws_hourly_forecast_rejects_partial_only_payload() -> None:
    with pytest.raises(NwsForecastError, match="fully covered"):
        parse_nws_hourly_forecast(
            _payload(hours=12),
            station=STATION,
            fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC),
        )


async def test_fetch_bundle_preserves_ordered_points_and_forecast_evidence() -> None:
    hourly_url = "https://api.weather.gov/gridpoints/OKX/33,37/forecast/hourly"
    points_body = json.dumps(
        {"properties": {"forecastHourly": hourly_url}}, separators=(",", ":")
    ).encode()
    forecast_body = json.dumps(_payload(), separators=(",", ":")).encode()
    requested_urls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.path.startswith("/points/"):
            return httpx.Response(
                200,
                content=points_body,
                headers={"Date": "Mon, 06 Jul 2026 18:34:00 GMT", "ETag": "points-v2"},
            )
        assert str(request.url) == hourly_url
        return httpx.Response(
            200,
            content=forecast_body,
            headers={"Date": "Mon, 06 Jul 2026 18:35:00 GMT", "ETag": "hourly-v9"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with NwsForecastClient(
            base_url="https://api.weather.gov",
            user_agent="stratus-tests@example.test",
            http_client=http_client,
        ) as client:
            bundle = await client.fetch_bundle(
                STATION, fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC)
            )
            legacy_values = await client.fetch(
                STATION, fetched_at=datetime(2026, 7, 6, 19, tzinfo=UTC)
            )

    assert isinstance(legacy_values, list)
    assert len(bundle.forecasts) == 4
    assert bundle.trajectories == ()
    assert len(bundle.evidence) == 2
    points_evidence, forecast_evidence = bundle.evidence
    assert [points_evidence.source_url, forecast_evidence.source_url] == requested_urls[:2]
    assert points_evidence.source_url.startswith("https://api.weather.gov/points/")
    assert forecast_evidence.source_url == hourly_url
    assert points_evidence.response_body == points_body
    assert forecast_evidence.response_body == forecast_body
    assert points_evidence.response_sha256 == sha256(points_body).hexdigest()
    assert forecast_evidence.response_sha256 == sha256(forecast_body).hexdigest()
    assert points_evidence.response_bytes == len(points_body)
    assert forecast_evidence.response_bytes == len(forecast_body)
    assert points_evidence.http_date == datetime(2026, 7, 6, 18, 34, tzinfo=UTC)
    assert forecast_evidence.http_date == datetime(2026, 7, 6, 18, 35, tzinfo=UTC)
    assert points_evidence.etag == "points-v2"
    assert forecast_evidence.etag == "hourly-v9"
    assert points_evidence.request_started_at <= points_evidence.response_received_at
    assert points_evidence.response_received_at <= forecast_evidence.request_started_at
