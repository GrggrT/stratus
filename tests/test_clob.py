import gzip
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from stratus.collectors.clob import (
    ClobClient,
    ClobError,
    parse_binary_book_pair,
    parse_price_snapshot,
    parse_strict_book_snapshot,
)


def _book(
    token_id: str,
    *,
    timestamp: str = "1783273867326",
    source_hash: str | None = None,
    bids: list[dict[str, str]] | None = None,
    asks: list[dict[str, str]] | None = None,
    tick_size: str = "0.01",
) -> dict[str, Any]:
    return {
        "asset_id": token_id,
        "timestamp": timestamp,
        "hash": source_hash or f"opaque-{token_id}",
        "tick_size": tick_size,
        "neg_risk": False,
        "bids": bids if bids is not None else [{"price": "0.39", "size": "10"}],
        "asks": asks if asks is not None else [{"price": "0.41", "size": "12"}],
    }


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


def test_strict_snapshot_uses_source_timestamp_and_retains_full_book() -> None:
    book = _book(
        "yes-token",
        bids=[
            {"price": "0.40", "size": "10"},
            {"price": "0.39", "size": "20"},
        ],
        asks=[
            {"price": "0.42", "size": "30"},
            {"price": "0.41", "size": "40"},
        ],
    )

    snapshot = parse_strict_book_snapshot(book)

    assert snapshot.source_timestamp == "1783273867326"
    assert snapshot.source_ts == datetime(2026, 7, 5, 17, 51, 7, 326000, tzinfo=UTC)
    assert snapshot.source_hash == "opaque-yes-token"
    assert snapshot.tick_size == Decimal("0.01")
    assert snapshot.neg_risk is False
    assert snapshot.depth_range == Decimal("0.01")
    assert snapshot.complete is True
    assert snapshot.best_bid == Decimal("0.40")
    assert snapshot.best_ask == Decimal("0.41")
    assert snapshot.midpoint == Decimal("0.40500000")
    assert snapshot.spread == Decimal("0.01")
    assert snapshot.bid_depth_tokens == Decimal("30.000000")
    assert snapshot.ask_depth_tokens == Decimal("70.000000")
    assert snapshot.bid_depth_usd == Decimal("11.800000")
    assert snapshot.ask_depth_usd == Decimal("29.000000")
    assert snapshot.total_bid_depth_usd == Decimal("11.800000")
    assert snapshot.total_ask_depth_usd == Decimal("29.000000")
    canonical = (
        b'{"asks": [{"size": "30", "price": "0.42"}, '
        b'{"size": "40", "price": "0.41"}], '
        b'"bids": [{"size": "10", "price": "0.40"}, '
        b'{"size": "20", "price": "0.39"}], '
        b'"hash": "opaque-yes-token", "asset_id": "yes-token", '
        b'"neg_risk": false, "tick_size": "0.01", '
        b'"timestamp": "1783273867326"}'
    )
    assert snapshot.payload_sha256 == hashlib.sha256(canonical).hexdigest()


async def test_strict_batch_preserves_exact_response_evidence_and_request_order() -> None:
    raw_body = json.dumps(
        [_book("no-token"), _book("yes-token")],
        separators=(",", ":"),
    ).encode()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == [
            {"token_id": "yes-token"},
            {"token_id": "no-token"},
        ]
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            content=raw_body,
            headers={
                "Content-Type": "application/json",
                "ETag": '"books-v1"',
                "X-Request-Id": "request-123",
                "X-Ignored": "not-evidence",
            },
        )

    before = datetime.now(UTC)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            http_client=http_client,
        ) as client:
            batch = await client.fetch_book_batch(["yes-token", "no-token"])
    after = datetime.now(UTC)

    assert batch.source_url == "https://example.test/books"
    assert batch.requested_token_ids == ("yes-token", "no-token")
    assert before <= batch.request_started_at <= batch.response_received_at <= after
    assert batch.response_body == raw_body
    assert batch.response_bytes == len(raw_body)
    assert batch.response_sha256 == hashlib.sha256(raw_body).hexdigest()
    headers = dict(batch.response_headers)
    assert "content-encoding" not in headers
    assert headers["content-type"] == "application/json"
    assert headers["etag"] == '"books-v1"'
    assert headers["x-request-id"] == "request-123"
    assert "x-ignored" not in headers
    assert tuple(snapshot.token_id for snapshot in batch.snapshots) == (
        "yes-token",
        "no-token",
    )


async def test_strict_batch_stops_streaming_above_response_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    yielded_per_request: list[int] = []

    class OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yielded = 0
            yielded_per_request.append(yielded)
            for chunk in (b"x" * 60, b"y" * 60, b"z" * 60):
                yielded += 1
                yielded_per_request[-1] = yielded
                yield chunk

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=OversizedStream())

    async def no_sleep(_: int) -> None:
        return None

    monkeypatch.setattr("stratus.collectors.clob.asyncio.sleep", no_sleep)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            max_response_bytes=100,
            http_client=http_client,
        ) as client:
            with pytest.raises(ClobError, match="byte limit"):
                await client.fetch_book_batch(["yes-token", "no-token"])

    assert yielded_per_request == [2, 2, 2]


async def test_strict_batch_rejects_compression_before_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    compressed_bomb = gzip.compress(b"x" * 1_000_000)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            content=compressed_bomb,
            headers={"Content-Encoding": "gzip"},
        )

    async def no_sleep(_: int) -> None:
        return None

    monkeypatch.setattr("stratus.collectors.clob.asyncio.sleep", no_sleep)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            max_response_bytes=2_000_000,
            http_client=http_client,
        ) as client:
            with pytest.raises(ClobError, match="compressed responses"):
                await client.fetch_book_batch(["yes-token", "no-token"])

    assert calls == 3


@pytest.mark.parametrize(
    ("requested", "returned", "match"),
    [
        (["yes-token", "no-token"], [_book("yes-token")], "missing"),
        (["yes-token"], [_book("yes-token"), _book("no-token")], "extra"),
        (
            ["yes-token", "no-token"],
            [_book("yes-token"), _book("yes-token")],
            "duplicate token ids",
        ),
    ],
)
async def test_strict_batch_rejects_non_exact_response_universe(
    monkeypatch: pytest.MonkeyPatch,
    requested: list[str],
    returned: list[dict[str, Any]],
    match: str,
) -> None:
    async def no_sleep(_: int) -> None:
        return None

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=returned)

    monkeypatch.setattr("stratus.collectors.clob.asyncio.sleep", no_sleep)
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            http_client=http_client,
        ) as client:
            with pytest.raises(ClobError, match=match):
                await client.fetch_book_batch(requested)


@pytest.mark.parametrize("requested", [[], ["yes-token", "yes-token"]])
async def test_strict_batch_rejects_empty_or_duplicate_request_universe(
    requested: list[str],
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid request must fail before HTTP")

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with ClobClient(
            books_url="https://example.test/books",
            batch_size=2,
            http_client=http_client,
        ) as client:
            with pytest.raises(ClobError):
                await client.fetch_book_batch(requested)


def test_binary_pair_uses_independent_no_book_without_synthesis() -> None:
    yes = parse_strict_book_snapshot(
        _book(
            "yes-token",
            bids=[{"price": "0.39", "size": "10"}],
            asks=[{"price": "0.41", "size": "11"}],
        )
    )
    no = parse_strict_book_snapshot(
        _book(
            "no-token",
            timestamp="1783273868326",
            bids=[{"price": "0.59", "size": "17"}],
            asks=[{"price": "0.61", "size": "19"}],
        )
    )

    pair = parse_binary_book_pair(
        [yes, no],
        yes_token_id="yes-token",
        no_token_id="no-token",
    )

    assert pair.yes is yes
    assert pair.no is no
    assert pair.no.best_bid == Decimal("0.59")
    assert pair.no.ask_depth_tokens == Decimal("19.000000")
    assert pair.source_skew_seconds == Decimal("1")
    assert pair.complement_residual == Decimal("0.000000")
    assert pair.binary_yes_probability == Decimal("0.4")
    assert pair.eligible is True
    assert pair.ineligibility_reasons == ()


def test_binary_pair_keeps_one_sided_book_but_marks_it_ineligible() -> None:
    yes = parse_strict_book_snapshot(
        _book("yes-token", bids=[], asks=[{"price": "0.41", "size": "11"}])
    )
    no = parse_strict_book_snapshot(_book("no-token"))

    pair = parse_binary_book_pair(
        [yes, no],
        yes_token_id="yes-token",
        no_token_id="no-token",
    )

    assert yes.complete is False
    assert yes.best_bid is None
    assert yes.best_ask == Decimal("0.41")
    assert yes.midpoint is None
    assert pair.binary_yes_probability is None
    assert pair.complement_residual is None
    assert pair.eligible is False
    assert pair.ineligibility_reasons == ("yes_book_incomplete",)


@pytest.mark.parametrize(
    ("no_timestamp", "no_bid", "no_ask", "expected_reason"),
    [
        ("1783273869327", "0.59", "0.61", "source_skew_exceeds_limit"),
        ("1783273867326", "0.54", "0.56", "complement_residual_exceeds_tick"),
    ],
)
def test_binary_pair_rejects_excessive_skew_or_complement_residual(
    no_timestamp: str,
    no_bid: str,
    no_ask: str,
    expected_reason: str,
) -> None:
    yes = parse_strict_book_snapshot(_book("yes-token"))
    no = parse_strict_book_snapshot(
        _book(
            "no-token",
            timestamp=no_timestamp,
            bids=[{"price": no_bid, "size": "10"}],
            asks=[{"price": no_ask, "size": "10"}],
        )
    )

    pair = parse_binary_book_pair(
        [yes, no],
        yes_token_id="yes-token",
        no_token_id="no-token",
        max_source_skew=timedelta(seconds=2),
    )

    assert pair.eligible is False
    assert expected_reason in pair.ineligibility_reasons
