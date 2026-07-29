from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.db.models import ClobBookBatch, ClobMarketSnapshot
from stratus.repositories.clob_snapshots import (
    ClobSnapshotIntegrityError,
    build_clob_book_batch_value,
    build_clob_market_snapshot_values,
    clob_batch_record_is_valid,
    clob_book_batch_record_hash,
    clob_market_snapshot_record_hash,
    reparse_clob_batch,
    requested_tokens_sha256,
)
from stratus.repositories.market_prices import PriceTarget

NOW = datetime(2026, 7, 14, 8, 30, 1, tzinfo=UTC)


def _target(index: int = 1) -> PriceTarget:
    return PriceTarget(
        market_id=index,
        event_id="event-1",
        condition_id=f"condition-{index}",
        yes_token_id=f"yes-{index}",
        no_token_id=f"no-{index}",
    )


def _book(
    token_id: str,
    condition_id: str,
    *,
    bid: str,
    ask: str,
    one_sided: bool = False,
) -> dict[str, Any]:
    return {
        "market": condition_id,
        "asset_id": token_id,
        "timestamp": str(int(NOW.timestamp() * 1000)),
        "hash": f"opaque:{token_id}:not-a-sha",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [] if one_sided else [{"price": bid, "size": "12.345678"}],
        "asks": [{"price": ask, "size": "23.456789"}],
    }


def _batch(
    targets: list[PriceTarget],
    *,
    one_sided_market: int | None = None,
) -> ClobBooksBatch:
    books: list[dict[str, Any]] = []
    for target in targets:
        books.extend(
            [
                _book(
                    target.yes_token_id,
                    target.condition_id,
                    bid="0.39",
                    ask="0.41",
                    one_sided=target.market_id == one_sided_market,
                ),
                _book(
                    target.no_token_id,
                    target.condition_id,
                    bid="0.59",
                    ask="0.61",
                ),
            ]
        )
    requested = tuple(sorted(str(book["asset_id"]) for book in books))
    by_token = {str(book["asset_id"]): book for book in books}
    raw = json.dumps(list(reversed(books)), separators=(",", ":")).encode()
    return ClobBooksBatch(
        source_url="https://clob.example.test/books",
        requested_token_ids=requested,
        request_started_at=NOW + timedelta(milliseconds=100),
        response_received_at=NOW + timedelta(milliseconds=200),
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(
            ("date", "Tue, 14 Jul 2026 08:30:01 GMT"),
            ("etag", '"clob-revision"'),
        ),
        snapshots=tuple(parse_strict_book_snapshot(by_token[token]) for token in requested),
    )


def _stored_models(
    collected: ClobBooksBatch,
    targets: list[PriceTarget],
) -> tuple[ClobBookBatch, tuple[ClobMarketSnapshot, ...]]:
    batch_value = build_clob_book_batch_value(collected)
    stored_batch = ClobBookBatch(
        id=91,
        **asdict(batch_value),
        first_seen_at=NOW + timedelta(milliseconds=300),
        record_hash="",
    )
    stored_batch.record_hash = clob_book_batch_record_hash(stored_batch)
    snapshots = tuple(
        ClobMarketSnapshot(
            id=100 + index,
            batch_id=stored_batch.id,
            **asdict(value),
            record_hash="",
        )
        for index, value in enumerate(build_clob_market_snapshot_values(collected, targets))
    )
    for snapshot in snapshots:
        snapshot.record_hash = clob_market_snapshot_record_hash(stored_batch, snapshot)
    return stored_batch, snapshots


def test_requested_token_hash_is_sorted_and_order_independent() -> None:
    expected = hashlib.sha256(b"no-1\nyes-1").hexdigest()
    assert requested_tokens_sha256(["yes-1", "no-1"]) == expected
    assert requested_tokens_sha256(["no-1", "yes-1"]) == expected
    with pytest.raises(ClobSnapshotIntegrityError, match="distinct"):
        requested_tokens_sha256(["yes-1", "yes-1"])


def test_builder_reparses_raw_and_builds_independent_yes_no_semantics() -> None:
    target = _target()
    collected = _batch([target])

    reparsed = reparse_clob_batch(collected)
    batch_value = build_clob_book_batch_value(collected)
    (value,) = build_clob_market_snapshot_values(collected, [target])

    assert tuple(snapshot.token_id for snapshot in reparsed) == collected.requested_token_ids
    assert batch_value.requested_token_ids == ["no-1", "yes-1"]
    assert batch_value.source_http_date == datetime(2026, 7, 14, 8, 30, 1, tzinfo=UTC)
    assert value.yes_best_bid == Decimal("0.39")
    assert value.no_best_bid == Decimal("0.59")
    assert value.yes_bid_depth_usd == Decimal("4.814814")
    assert value.no_bid_depth_usd == Decimal("7.283950")
    assert value.binary_yes_probability == Decimal("0.40000000")
    assert value.complement_residual == Decimal("0E-8")
    assert value.complete is True
    assert value.eligible is True
    assert value.yes_source_hash == "opaque:yes-1:not-a-sha"
    assert value.yes_payload_sha256 == next(
        snapshot.payload_sha256 for snapshot in reparsed if snapshot.token_id == "yes-1"
    )


def test_one_sided_pair_is_persistable_but_ineligible() -> None:
    target = _target()
    (value,) = build_clob_market_snapshot_values(
        _batch([target], one_sided_market=target.market_id),
        [target],
    )

    assert value.yes_best_bid is None
    assert value.yes_bid_depth_usd is None
    assert value.yes_best_ask == Decimal("0.41")
    assert value.yes_ask_depth_usd == Decimal("9.617283")
    assert value.no_best_bid == Decimal("0.59")
    assert value.complete is False
    assert value.eligible is False
    assert value.binary_yes_probability is None
    assert value.complement_residual is None


@pytest.mark.parametrize("tamper", ["bytes", "missing", "duplicate", "extra"])
def test_reparser_rejects_tampered_or_nonexact_raw_universe(tamper: str) -> None:
    target = _target()
    collected = _batch([target])
    payload = json.loads(collected.response_body)
    if tamper == "bytes":
        broken = replace(collected, response_body=collected.response_body + b" ")
    else:
        if tamper == "missing":
            payload.pop()
        elif tamper == "duplicate":
            payload[1] = payload[0]
        else:
            payload.append(_book("extra", target.condition_id, bid="0.2", ask="0.3"))
        raw = json.dumps(payload, separators=(",", ":")).encode()
        broken = replace(
            collected,
            response_body=raw,
            response_bytes=len(raw),
            response_sha256=hashlib.sha256(raw).hexdigest(),
        )
    with pytest.raises(ClobSnapshotIntegrityError):
        reparse_clob_batch(broken)


def test_independent_record_verifier_covers_raw_semantics_and_hashes() -> None:
    targets = [_target(1), _target(2)]
    collected = _batch(targets)
    stored_batch, snapshots = _stored_models(collected, targets)

    assert clob_batch_record_is_valid(stored_batch, snapshots)
    original_bid = snapshots[0].yes_best_bid
    snapshots[0].yes_best_bid = Decimal("0.38")
    assert clob_batch_record_is_valid(stored_batch, snapshots) is False
    snapshots[0].yes_best_bid = original_bid
    assert clob_batch_record_is_valid(stored_batch, snapshots)
    stored_batch.source_response_body += b" "
    assert clob_batch_record_is_valid(stored_batch, snapshots) is False
