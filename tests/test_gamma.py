from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

import httpx
import pytest

from stratus.collectors.gamma import (
    ExpectedGammaMarket,
    GammaClient,
    GammaError,
    parse_complete_event_resolution,
    parse_resolved_markets,
)


def _raw_market(
    market_id: str,
    *,
    yes_wins: bool,
    labels: tuple[str, str] = ("Yes", "No"),
    closed_time: str = "2026-07-05T23:43:34Z",
    prices: tuple[object, object] | None = None,
) -> dict[str, object]:
    yes_price, no_price = ("1", "0") if yes_wins else ("0", "1")
    price_by_label = {"yes": yes_price, "no": no_price}
    token_by_label = {"yes": f"yes-token-{market_id}", "no": f"no-token-{market_id}"}
    return {
        "id": market_id,
        "closed": True,
        "umaResolutionStatus": "resolved",
        "closedTime": closed_time,
        "outcomes": json.dumps(list(labels)),
        "outcomePrices": json.dumps(
            list(prices)
            if prices is not None
            else [price_by_label[label.casefold()] for label in labels]
        ),
        "clobTokenIds": json.dumps([token_by_label[label.casefold()] for label in labels]),
        "conditionId": f"condition-{market_id}",
        "questionID": f"question-{market_id}",
        "resolvedBy": "0xresolver",
    }


def _event(*markets: dict[str, object], event_id: str = "event-1") -> dict[str, object]:
    return {
        "id": event_id,
        "closedTime": "2026-07-05T23:50:00Z",
        "updatedAt": "2026-07-05T23:51:00Z",
        "markets": list(markets),
    }


def _expected(market_id: str, internal_id: int) -> ExpectedGammaMarket:
    return ExpectedGammaMarket(
        market_id=internal_id,
        polymarket_id=market_id,
        condition_id=f"condition-{market_id}",
        yes_token_id=f"yes-token-{market_id}",
        no_token_id=f"no-token-{market_id}",
        rules_hash=(f"{internal_id:x}" * 64)[:64],
    )


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


async def test_gamma_snapshot_hashes_exact_response_bytes_before_parsing() -> None:
    # Deliberately preserve whitespace and key order that json.dumps(payload) would change.
    raw = b'{\n  "markets" : [], "id" : "event-1"\n}'

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.test/events/event-1"
        return httpx.Response(
            200,
            content=raw,
            headers={
                "Date": "Sun, 05 Jul 2026 23:52:00 GMT",
                "Last-Modified": "Sun, 05 Jul 2026 23:51:00 GMT",
                "ETag": '"revision-7"',
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    async with http_client:
        async with GammaClient(
            events_url="https://example.test/events",
            weather_tag_id=84,
            http_client=http_client,
        ) as client:
            snapshot = await client.fetch_event_snapshot("event-1")

    assert snapshot.payload == {"markets": [], "id": "event-1"}
    assert snapshot.response_sha256 == hashlib.sha256(raw).hexdigest()
    assert snapshot.response_bytes == len(raw)
    assert snapshot.response_body == raw
    assert snapshot.source_url == "https://example.test/events/event-1"
    assert snapshot.http_date == datetime(2026, 7, 5, 23, 52, tzinfo=UTC)
    assert snapshot.last_modified == datetime(2026, 7, 5, 23, 51, tzinfo=UTC)
    assert snapshot.etag == '"revision-7"'


def test_parse_resolved_markets_accepts_only_final_binary_outcomes() -> None:
    pending = _raw_market("pending", yes_wins=True)
    pending["umaResolutionStatus"] = "proposed"
    assert parse_resolved_markets(
        _event(
            _raw_market("yes", yes_wins=True),
            _raw_market("no", yes_wins=False),
            pending,
        )
    ) == {"yes": True, "no": False}


def test_complete_parser_maps_reversed_labels_tokens_and_prices_by_name() -> None:
    event = _event(
        _raw_market("winner", yes_wins=True, labels=("No", "Yes")),
        _raw_market("loser", yes_wins=False, labels=("No", "Yes")),
    )
    result = parse_complete_event_resolution(
        event,
        expected_event_id="event-1",
        expected_markets=[_expected("loser", 2), _expected("winner", 1)],
    )

    assert [row.polymarket_id for row in result.outcomes] == ["winner", "loser"]
    assert [row.outcome_yes for row in result.outcomes] == [True, False]
    winner = result.outcomes[0]
    assert winner.yes_price == "1"
    assert winner.no_price == "0"
    assert winner.yes_token_id == "yes-token-winner"
    assert winner.no_token_id == "no-token-winner"
    assert result.first_outcome_at == datetime(2026, 7, 5, 23, 43, 34, tzinfo=UTC)
    assert result.fully_resolved_at == result.first_outcome_at
    assert result.event_closed_at == datetime(2026, 7, 5, 23, 50, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw_markets", "expected", "message"),
    [
        (
            [_raw_market("winner", yes_wins=True)],
            [_expected("winner", 1), _expected("loser", 2)],
            "market universe mismatch",
        ),
        (
            [
                _raw_market("winner", yes_wins=True),
                _raw_market("winner", yes_wins=True),
            ],
            [_expected("winner", 1), _expected("loser", 2)],
            "duplicate market ids",
        ),
    ],
)
def test_complete_parser_rejects_missing_or_duplicate_gamma_markets(
    raw_markets: list[dict[str, object]],
    expected: list[ExpectedGammaMarket],
    message: str,
) -> None:
    with pytest.raises(GammaError, match=message):
        parse_complete_event_resolution(
            _event(*raw_markets),
            expected_event_id="event-1",
            expected_markets=expected,
        )


@pytest.mark.parametrize("yes_wins", [False, True])
def test_complete_parser_rejects_zero_or_two_winners(yes_wins: bool) -> None:
    with pytest.raises(GammaError, match="exactly one YES winner"):
        parse_complete_event_resolution(
            _event(
                _raw_market("one", yes_wins=yes_wins),
                _raw_market("two", yes_wins=yes_wins),
            ),
            expected_event_id="event-1",
            expected_markets=[_expected("one", 1), _expected("two", 2)],
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("closedTime", "not-a-time", "invalid closedTime"),
        ("outcomePrices", '["0.99", "0.01"]', "non-binary terminal payout"),
        ("outcomePrices", '["garbage", "0"]', "invalid outcome prices"),
        ("outcomes", '["Yes", "Maybe"]', "invalid outcome labels"),
        ("outcomes", '["Yes", "Yes"]', "invalid outcome labels"),
    ],
)
def test_complete_parser_rejects_malformed_time_price_or_labels(
    field: str,
    value: object,
    message: str,
) -> None:
    winner = _raw_market("winner", yes_wins=True)
    winner[field] = value
    with pytest.raises(GammaError, match=message):
        parse_complete_event_resolution(
            _event(winner, _raw_market("loser", yes_wins=False)),
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )


def test_complete_parser_rejects_condition_or_token_identity_change() -> None:
    winner = _raw_market("winner", yes_wins=True)
    winner["conditionId"] = "changed-condition"
    with pytest.raises(GammaError, match="condition id mismatch"):
        parse_complete_event_resolution(
            _event(winner, _raw_market("loser", yes_wins=False)),
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )

    winner = _raw_market("winner", yes_wins=True)
    winner["clobTokenIds"] = json.dumps(["changed-token", "no-token-winner"])
    with pytest.raises(GammaError, match="token identity mismatch"):
        parse_complete_event_resolution(
            _event(winner, _raw_market("loser", yes_wins=False)),
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )


@pytest.mark.parametrize(
    "token_ids",
    [
        [None, "no-token-winner"],
        [17, "no-token-winner"],
        ["", "no-token-winner"],
        ["   ", "no-token-winner"],
        ["yes-token-winner", None],
        ["yes-token-winner", 17],
        ["yes-token-winner", ""],
        ["yes-token-winner", "   "],
    ],
)
def test_complete_parser_rejects_non_string_or_empty_token_ids(
    token_ids: list[object],
) -> None:
    winner = _raw_market("winner", yes_wins=True)
    winner["clobTokenIds"] = json.dumps(token_ids)

    with pytest.raises(GammaError, match="invalid token ids"):
        parse_complete_event_resolution(
            _event(winner, _raw_market("loser", yes_wins=False)),
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )


def test_strict_parser_requires_per_market_closed_time_without_event_fallback() -> None:
    winner = _raw_market("winner", yes_wins=True)
    winner.pop("closedTime")
    event = _event(winner, _raw_market("loser", yes_wins=False))

    # The legacy discovery-facing API retains its event-level fallback.
    assert parse_resolved_markets(event)["winner"] is True

    # Formal settlement evidence must carry an explicit timestamp per bucket.
    with pytest.raises(GammaError, match="market winner is missing closedTime"):
        parse_complete_event_resolution(
            event,
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )


@pytest.mark.parametrize(
    "closed_time",
    ["2026-07-05T23:43:34", "2026-07-05"],
)
def test_complete_parser_rejects_naive_or_date_only_market_timestamps(
    closed_time: str,
) -> None:
    with pytest.raises(GammaError, match="closedTime must include a timezone"):
        parse_complete_event_resolution(
            _event(
                _raw_market("winner", yes_wins=True, closed_time=closed_time),
                _raw_market("loser", yes_wins=False),
            ),
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )


@pytest.mark.parametrize("field", ["closedTime", "updatedAt"])
@pytest.mark.parametrize("timestamp", ["2026-07-05T23:50:00", "2026-07-05"])
def test_complete_parser_rejects_naive_or_date_only_event_evidence_timestamps(
    field: str,
    timestamp: str,
) -> None:
    event = _event(
        _raw_market("winner", yes_wins=True),
        _raw_market("loser", yes_wins=False),
    )
    event[field] = timestamp

    with pytest.raises(GammaError, match=rf"{field} must include a timezone"):
        parse_complete_event_resolution(
            event,
            expected_event_id="event-1",
            expected_markets=[_expected("winner", 1), _expected("loser", 2)],
        )
