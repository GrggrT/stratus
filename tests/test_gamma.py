import httpx

from stratus.collectors.gamma import GammaClient, parse_resolved_markets


async def test_gamma_client_paginates_weather_tag() -> None:
    offsets = []

    async def handler(request: httpx.Request) -> httpx.Response:
        offsets.append(request.url.params["offset"])
        assert request.url.params["tag_id"] == "84"
        assert request.url.params["active"] == "true"
        if request.url.params["offset"] == "0":
            return httpx.Response(200, json=[{"id": "1"}, {"id": "2"}])
        return httpx.Response(200, json=[{"id": "3"}])

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with GammaClient(
            events_url="https://example.test/events",
            weather_tag_id=84,
            page_size=2,
            http_client=http_client,
        ) as client:
            events = await client.fetch_active_weather_events()

    assert [event["id"] for event in events] == ["1", "2", "3"]
    assert offsets == ["0", "2"]


def test_parse_resolved_markets_accepts_only_final_binary_outcomes() -> None:
    assert parse_resolved_markets(
        {
            "closedTime": "2026-07-05T23:43:34Z",
            "markets": [
                {
                    "id": "yes",
                    "closed": True,
                    "umaResolutionStatus": "resolved",
                    "outcomePrices": '["1", "0"]',
                },
                {
                    "id": "no",
                    "closed": True,
                    "umaResolutionStatus": "resolved",
                    "outcomePrices": '["0", "1"]',
                },
                {
                    "id": "pending",
                    "closed": True,
                    "umaResolutionStatus": "proposed",
                    "outcomePrices": '["1", "0"]',
                },
            ]
        }
    ) == {"yes": True, "no": False}
