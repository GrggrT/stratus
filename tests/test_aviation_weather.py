from datetime import UTC, datetime
from decimal import Decimal

import httpx

from stratus.collectors.aviation_weather import (
    AviationWeatherClient,
    parse_metar_observations,
)


def test_parse_metar_observation_uses_obs_time_and_decoded_temperature() -> None:
    values = parse_metar_observations(
        [
            {
                "icaoId": "KLGA",
                "obsTime": 1783270260,
                "reportTime": "2026-07-05T17:00:00.000Z",
                "temp": 24.4,
                "rawOb": "METAR KLGA 051651Z 04012KT 10SM 24/21 A2998",
            }
        ],
        {"KLGA": 7},
    )
    assert values[0].station_id == 7
    assert values[0].obs_time == datetime(2026, 7, 5, 16, 51, tzinfo=UTC)
    assert values[0].temp_c == Decimal("24.4")


async def test_client_combines_station_ids_and_sets_lookback() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["ids"] == "EGLC,KLGA"
        assert request.url.params["hours"] == "6"
        assert request.headers["User-Agent"] == "STRATUS/test"
        return httpx.Response(200, json=[])

    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), headers={"User-Agent": "STRATUS/test"}
    )
    async with http_client:
        async with AviationWeatherClient(
            metar_url="https://example.test/metar",
            user_agent="STRATUS/test",
            lookback_hours=6,
            http_client=http_client,
        ) as client:
            values = await client.fetch_metars({"KLGA": 1, "EGLC": 2})
    assert values == []

