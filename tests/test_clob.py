import json
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from stratus.collectors.clob import ClobClient, parse_price_snapshot


def test_parse_price_snapshot_does_not_trust_level_order() -> None:
    snapshot = parse_price_snapshot(
        {
            "asset_id": "yes-token",
            "timestamp": "1783273867326",
            "bids": [
                {"price": "0.40", "size": "10"},
                {"price": "0.42", "size": "20"},
                {"price": "0.41", "size": "30"},
            ],
            "asks": [
                {"price": "0.50", "size": "10"},
                {"price": "0.44", "size": "20"},
                {"price": "0.43", "size": "30"},
            ],
        },
        collected_at=datetime(2026, 7, 5, 17, 52, tzinfo=UTC),
    )
    assert snapshot is not None
    assert snapshot.best_bid == Decimal("0.42")
    assert snapshot.best_ask == Decimal("0.43")
    assert snapshot.midpoint == Decimal("0.425000")
    # YES bids are the executable asks for NO, so depth is measured in NO dollars.
    assert snapshot.bid_depth_usd == Decimal("29.3000")
    assert snapshot.ask_depth_usd == Decimal("21.7000")
    assert snapshot.ts == datetime(2026, 7, 5, 17, 52, tzinfo=UTC)


def test_one_sided_book_has_no_tradeable_snapshot() -> None:
    assert (
        parse_price_snapshot(
            {
                "asset_id": "yes-token",
                "timestamp": "1783273867326",
                "bids": [],
                "asks": [{"price": "0.5", "size": "10"}],
            },
            collected_at=datetime(2026, 7, 5, 17, 52, tzinfo=UTC),
        )
        is None
    )


async def test_clob_client_batches_token_ids() -> None:
    batches = []

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        batches.append([item["token_id"] for item in body])
        return httpx.Response(200, json=[])

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            http_client=http_client,
        ) as client:
            await client.fetch_books(["a", "b", "c"])
    assert batches == [["a", "b"], ["c"]]
