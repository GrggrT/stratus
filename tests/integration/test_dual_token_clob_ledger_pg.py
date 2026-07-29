from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.repositories.gamma_outcomes import (
    GammaOutcomeEventValue,
    upsert_gamma_outcome_event,
)
from stratus.repositories.validation_ledger import enroll_terminal_order

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]

TARGET_MODEL_PROBABILITY = Decimal("0.400000")
EXPECTED_MARKET_PROBABILITY = Decimal("0.363636")
EXPECTED_EVENT_TOTAL = Decimal("1.100000")
EXPECTED_EVENT_OVERROUND = Decimal("0.100000")
EXPECTED_MODEL_BRIER = Decimal("0.16000000")
EXPECTED_MARKET_BRIER = Decimal("0.13223140")


def _book(
    token_id: str,
    condition_id: str,
    timestamp_ms: int,
    *,
    source_hash: str,
    bid: str,
    ask: str | None,
) -> dict[str, object]:
    return {
        "asset_id": token_id,
        "market": condition_id,
        "timestamp": str(timestamp_ms),
        "hash": source_hash,
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [{"price": bid, "size": "10"}],
        "asks": [] if ask is None else [{"price": ask, "size": "10"}],
    }


async def _reset_clob(session: AsyncSession) -> None:
    # The shared integration fixture predates migration 0031. Keep this
    # focused test isolated without changing its global table list.
    await session.execute(text("TRUNCATE clob_book_batches RESTART IDENTITY CASCADE"))


async def _seed_market(
    session: AsyncSession,
    *,
    suffix: str,
    now: datetime,
    event_id: str | None = None,
) -> tuple[int, str, str, str]:
    station_id = await session.scalar(
        text(
            """
            INSERT INTO stations
                (icao, name, city, lat, lon, tz, active, stage)
            VALUES
                (:icao, :name, :city, 40.0, -73.0, 'UTC', true, 'paper')
            RETURNING id
            """
        ),
        {
            "icao": f"T{suffix:0>3}"[-4:],
            "name": f"Test {suffix}",
            "city": f"Test {suffix}",
        },
    )
    condition_id = f"0xcondition-{suffix}"
    yes_token_id = f"yes-token-{suffix}"
    no_token_id = f"no-token-{suffix}"
    market_id = await session.scalar(
        text(
            """
            INSERT INTO markets
                (polymarket_id, event_id, condition_id, yes_token_id, no_token_id,
                 station_id, market_date, kind, rules_raw, station_verified,
                 status, created_at, resolves_at)
            VALUES
                (:external_id, :event_id, :condition_id, :yes_token_id, :no_token_id,
                 :station_id, :market_date, 'high', 'test rules', true,
                 'active', :created_at, :resolves_at)
            RETURNING id
            """
        ),
        {
            "external_id": f"market-{suffix}",
            "event_id": event_id or f"event-{suffix}",
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "station_id": station_id,
            "market_date": now.date(),
            "created_at": now - timedelta(days=1),
            "resolves_at": now + timedelta(days=1),
        },
    )
    assert market_id is not None
    return int(market_id), condition_id, yes_token_id, no_token_id


async def _payload_hashes(
    session: AsyncSession,
    response_body: bytes,
) -> dict[str, str]:
    rows = (
        await session.execute(
            text(
                """
                SELECT value->>'asset_id' AS token_id,
                       encode(digest(convert_to(value::text, 'UTF8'), 'sha256'), 'hex')
                           AS payload_hash
                FROM jsonb_array_elements(
                    convert_from(CAST(:body AS bytea), 'UTF8')::jsonb
                ) AS item(value)
                """
            ),
            {"body": response_body},
        )
    ).all()
    return {str(row.token_id): str(row.payload_hash) for row in rows}


async def _seed_pair(
    session: AsyncSession,
    *,
    suffix: str,
    complete: bool,
) -> dict[str, object]:
    now = datetime.now(UTC).replace(microsecond=0)
    source_at = now - timedelta(seconds=5)
    timestamp_ms = int(source_at.timestamp()) * 1000
    market_id, condition_id, yes_token_id, no_token_id = await _seed_market(
        session,
        suffix=suffix,
        now=now,
    )
    yes_book = _book(
        yes_token_id,
        condition_id,
        timestamp_ms,
        source_hash=f"yes-source-{suffix}",
        bid="0.40",
        ask="0.42" if complete else None,
    )
    no_book = _book(
        no_token_id,
        condition_id,
        timestamp_ms,
        source_hash=f"no-source-{suffix}",
        bid="0.58",
        ask="0.60",
    )
    response_body = json.dumps(
        [yes_book, no_book], separators=(",", ":"), sort_keys=False
    ).encode()
    tokens = sorted([yes_token_id, no_token_id])
    token_hash = hashlib.sha256("\n".join(tokens).encode()).hexdigest()
    response_hash = hashlib.sha256(response_body).hexdigest()
    batch_id = await session.scalar(
        text(
            """
            INSERT INTO clob_book_batches
                (request_started_at, response_received_at, source_url,
                 requested_token_ids, requested_token_count, requested_tokens_sha256,
                 source_response_body, source_response_bytes, source_response_sha256,
                 record_hash)
            VALUES
                (:request_started_at, :response_received_at, :source_url,
                 CAST(:tokens AS jsonb), 2, :token_hash,
                 :body, :body_bytes, :body_hash, :placeholder_hash)
            RETURNING id
            """
        ),
        {
            "request_started_at": now - timedelta(seconds=4),
            "response_received_at": now - timedelta(seconds=3),
            "source_url": "https://clob.polymarket.test/books",
            "tokens": json.dumps(tokens),
            "token_hash": token_hash,
            "body": response_body,
            "body_bytes": len(response_body),
            "body_hash": response_hash,
            "placeholder_hash": "0" * 64,
        },
    )
    assert batch_id is not None
    payload_hashes = await _payload_hashes(session, response_body)

    yes_midpoint = Decimal("0.41000000") if complete else None
    residual = Decimal("0.00000000") if complete else None
    binary_yes = Decimal("0.41000000") if complete else None
    snapshot_id = await session.scalar(
        text(
            """
            INSERT INTO clob_market_snapshots
                (batch_id, market_id, condition_id, yes_token_id, no_token_id,
                 yes_source_timestamp, no_source_timestamp,
                 yes_source_hash, no_source_hash,
                 yes_payload_sha256, no_payload_sha256,
                 yes_tick_size, no_tick_size, yes_neg_risk, no_neg_risk,
                 yes_best_bid, yes_best_ask, yes_midpoint,
                 yes_bid_depth_usd, yes_ask_depth_usd,
                 no_best_bid, no_best_ask, no_midpoint,
                 no_bid_depth_usd, no_ask_depth_usd,
                 source_skew_ms, complement_residual, binary_yes_probability,
                 complete, eligible, record_hash)
            VALUES
                (:batch_id, :market_id, :condition_id, :yes_token_id, :no_token_id,
                 :source_at, :source_at, :yes_source_hash, :no_source_hash,
                 :yes_payload_hash, :no_payload_hash,
                 0.01, 0.01, false, false,
                 0.40, :yes_ask, :yes_midpoint, 4.000000, :yes_ask_depth,
                 0.58, 0.60, 0.59000000, 5.800000, 6.000000,
                 0, :residual, :binary_yes, :complete, :eligible, :placeholder_hash)
            RETURNING id
            """
        ),
        {
            "batch_id": batch_id,
            "market_id": market_id,
            "condition_id": condition_id,
            "yes_token_id": yes_token_id,
            "no_token_id": no_token_id,
            "source_at": source_at,
            "yes_source_hash": yes_book["hash"],
            "no_source_hash": no_book["hash"],
            "yes_payload_hash": payload_hashes[yes_token_id],
            "no_payload_hash": payload_hashes[no_token_id],
            "yes_ask": Decimal("0.42") if complete else None,
            "yes_midpoint": yes_midpoint,
            "yes_ask_depth": Decimal("4.200000") if complete else None,
            "residual": residual,
            "binary_yes": binary_yes,
            "complete": complete,
            "eligible": complete,
            "placeholder_hash": "0" * 64,
        },
    )
    assert snapshot_id is not None
    batch_first_seen, batch_hash = (
        await session.execute(
            text(
                "SELECT first_seen_at, record_hash FROM clob_book_batches WHERE id=:id"
            ),
            {"id": batch_id},
        )
    ).one()
    snapshot_hash = await session.scalar(
        text("SELECT record_hash FROM clob_market_snapshots WHERE id=:id"),
        {"id": snapshot_id},
    )
    return {
        "market_id": market_id,
        "condition_id": condition_id,
        "yes_token_id": yes_token_id,
        "no_token_id": no_token_id,
        "batch_id": int(batch_id),
        "batch_first_seen": batch_first_seen,
        "batch_hash": str(batch_hash),
        "snapshot_id": int(snapshot_id),
        "snapshot_hash": str(snapshot_hash),
        "response_body": response_body,
        "response_hash": response_hash,
        "tokens": tokens,
        "token_hash": token_hash,
    }


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def _seed_event_batch(
    session: AsyncSession,
    *,
    suffix: str,
    markets: list[dict[str, Any]],
) -> dict[str, Any]:
    now = datetime.now(UTC).replace(microsecond=0)
    source_at = now - timedelta(seconds=5)
    timestamp_ms = int(source_at.timestamp()) * 1000
    books: list[dict[str, object]] = []
    quotes: dict[int, dict[str, Decimal]] = {}
    for market in markets:
        market_id = int(market["market_id"])
        probability = Decimal(str(market["market_probability"]))
        yes_bid = probability - Decimal("0.01")
        yes_ask = probability + Decimal("0.01")
        no_midpoint = Decimal("1") - probability
        no_bid = no_midpoint - Decimal("0.01")
        no_ask = no_midpoint + Decimal("0.01")
        books.extend(
            [
                _book(
                    str(market["yes_token_id"]),
                    str(market["condition_id"]),
                    timestamp_ms,
                    source_hash=f"yes-source-{suffix}-{market_id}",
                    bid=format(yes_bid, "f"),
                    ask=format(yes_ask, "f"),
                ),
                _book(
                    str(market["no_token_id"]),
                    str(market["condition_id"]),
                    timestamp_ms,
                    source_hash=f"no-source-{suffix}-{market_id}",
                    bid=format(no_bid, "f"),
                    ask=format(no_ask, "f"),
                ),
            ]
        )
        quotes[market_id] = {
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "yes_midpoint": probability,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "no_midpoint": no_midpoint,
        }

    response_body = json.dumps(books, separators=(",", ":")).encode()
    tokens = sorted(
        token
        for market in markets
        for token in (str(market["yes_token_id"]), str(market["no_token_id"]))
    )
    token_hash = hashlib.sha256("\n".join(tokens).encode()).hexdigest()
    response_hash = hashlib.sha256(response_body).hexdigest()
    batch_id = await session.scalar(
        text(
            """
            INSERT INTO clob_book_batches
                (request_started_at, response_received_at, source_url,
                 requested_token_ids, requested_token_count, requested_tokens_sha256,
                 source_response_body, source_response_bytes, source_response_sha256,
                 record_hash)
            VALUES
                (:request_started_at, :response_received_at, :source_url,
                 CAST(:tokens AS jsonb), :token_count, :token_hash,
                 :body, :body_bytes, :body_hash, :placeholder_hash)
            RETURNING id
            """
        ),
        {
            "request_started_at": now - timedelta(seconds=4),
            "response_received_at": now - timedelta(seconds=3),
            "source_url": "https://clob.polymarket.test/books",
            "tokens": json.dumps(tokens),
            "token_count": len(tokens),
            "token_hash": token_hash,
            "body": response_body,
            "body_bytes": len(response_body),
            "body_hash": response_hash,
            "placeholder_hash": "0" * 64,
        },
    )
    assert batch_id is not None
    payload_hashes = await _payload_hashes(session, response_body)
    snapshot_ids: dict[int, int] = {}
    for market in markets:
        market_id = int(market["market_id"])
        quote = quotes[market_id]
        snapshot_id = await session.scalar(
            text(
                """
                INSERT INTO clob_market_snapshots
                    (batch_id, market_id, condition_id, yes_token_id, no_token_id,
                     yes_source_timestamp, no_source_timestamp,
                     yes_source_hash, no_source_hash,
                     yes_payload_sha256, no_payload_sha256,
                     yes_tick_size, no_tick_size, yes_neg_risk, no_neg_risk,
                     yes_best_bid, yes_best_ask, yes_midpoint,
                     yes_bid_depth_usd, yes_ask_depth_usd,
                     no_best_bid, no_best_ask, no_midpoint,
                     no_bid_depth_usd, no_ask_depth_usd,
                     source_skew_ms, complement_residual, binary_yes_probability,
                     complete, eligible, record_hash)
                VALUES
                    (:batch_id, :market_id, :condition_id, :yes_token_id, :no_token_id,
                     :source_at, :source_at, :yes_source_hash, :no_source_hash,
                     :yes_payload_hash, :no_payload_hash,
                     0.01, 0.01, false, false,
                     :yes_bid, :yes_ask, :yes_midpoint,
                     :yes_bid_depth, :yes_ask_depth,
                     :no_bid, :no_ask, :no_midpoint,
                     :no_bid_depth, :no_ask_depth,
                     0, 0.00000000, :binary_yes_probability,
                     true, true, :placeholder_hash)
                RETURNING id
                """
            ),
            {
                "batch_id": batch_id,
                "market_id": market_id,
                "condition_id": market["condition_id"],
                "yes_token_id": market["yes_token_id"],
                "no_token_id": market["no_token_id"],
                "source_at": source_at,
                "yes_source_hash": f"yes-source-{suffix}-{market_id}",
                "no_source_hash": f"no-source-{suffix}-{market_id}",
                "yes_payload_hash": payload_hashes[str(market["yes_token_id"])],
                "no_payload_hash": payload_hashes[str(market["no_token_id"])],
                "yes_bid": quote["yes_bid"],
                "yes_ask": quote["yes_ask"],
                "yes_midpoint": quote["yes_midpoint"],
                "yes_bid_depth": quote["yes_bid"] * 10,
                "yes_ask_depth": quote["yes_ask"] * 10,
                "no_bid": quote["no_bid"],
                "no_ask": quote["no_ask"],
                "no_midpoint": quote["no_midpoint"],
                "no_bid_depth": quote["no_bid"] * 10,
                "no_ask_depth": quote["no_ask"] * 10,
                "binary_yes_probability": quote["yes_midpoint"],
                "placeholder_hash": "0" * 64,
            },
        )
        assert snapshot_id is not None
        snapshot_ids[market_id] = int(snapshot_id)

    batch_first_seen = await session.scalar(
        text("SELECT first_seen_at FROM clob_book_batches WHERE id=:id"),
        {"id": batch_id},
    )
    assert batch_first_seen is not None
    snapshot_hashes = {
        market_id: str(
            await session.scalar(
                text("SELECT record_hash FROM clob_market_snapshots WHERE id=:id"),
                {"id": snapshot_id},
            )
        )
        for market_id, snapshot_id in snapshot_ids.items()
    }
    return {
        "batch_id": int(batch_id),
        "batch_first_seen": batch_first_seen,
        "snapshot_ids": snapshot_ids,
        "snapshot_hashes": snapshot_hashes,
    }


async def _seed_gamma_event(
    session: AsyncSession,
    *,
    event_id: str,
    markets: list[dict[str, Any]],
    outcome_at: datetime,
) -> tuple[int, str]:
    closed_at = outcome_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    vector: list[dict[str, Any]] = []
    for index, market in enumerate(markets):
        outcome_yes = index == 1
        vector.append(
            {
                "market_id": int(market["market_id"]),
                "polymarket_id": str(market["polymarket_id"]),
                "condition_id": str(market["condition_id"]),
                "question_id": f"question-{market['polymarket_id']}",
                "yes_token_id": str(market["yes_token_id"]),
                "no_token_id": str(market["no_token_id"]),
                "outcome_yes": outcome_yes,
                "yes_price": "1" if outcome_yes else "0",
                "no_price": "0" if outcome_yes else "1",
                "closed_at": closed_at,
                "uma_resolution_status": "resolved",
                "resolved_by": "0xresolution-oracle",
                "rules_hash": hashlib.sha256(b"test rules").hexdigest(),
            }
        )
    vector.sort(key=lambda item: int(item["market_id"]))
    universe = [
        {
            "market_id": item["market_id"],
            "polymarket_id": item["polymarket_id"],
            "condition_id": item["condition_id"],
            "yes_token_id": item["yes_token_id"],
            "no_token_id": item["no_token_id"],
        }
        for item in vector
    ]
    rules_manifest = [
        {"market_id": item["market_id"], "rules_hash": item["rules_hash"]}
        for item in vector
    ]
    raw_markets = [
        {
            "id": item["polymarket_id"],
            "closed": True,
            "umaResolutionStatus": "resolved",
            "closedTime": item["closed_at"],
            "outcomes": '["Yes", "No"]',
            "outcomePrices": (
                '["1", "0"]' if item["outcome_yes"] else '["0", "1"]'
            ),
            "clobTokenIds": json.dumps(
                [item["yes_token_id"], item["no_token_id"]]
            ),
            "conditionId": item["condition_id"],
            "questionID": item["question_id"],
            "resolvedBy": item["resolved_by"],
        }
        for item in vector
    ]
    source_body = json.dumps(
        {
            "id": event_id,
            "closedTime": closed_at,
            "updatedAt": closed_at,
            "markets": raw_markets,
        },
        separators=(",", ":"),
    ).encode()
    value = GammaOutcomeEventValue(
        event_id=event_id,
        evidence_protocol="gamma-outcome-v1",
        source_url=f"https://gamma-api.polymarket.test/events/{event_id}",
        source_http_date=outcome_at,
        source_last_modified=outcome_at,
        source_etag='"formal-benchmark-test"',
        source_response_body=source_body,
        source_response_sha256=hashlib.sha256(source_body).hexdigest(),
        source_response_bytes=len(source_body),
        source_event_updated_at=outcome_at,
        first_outcome_at=outcome_at,
        fully_resolved_at=outcome_at,
        market_count=len(vector),
        market_universe_hash=_canonical_hash(universe),
        rules_manifest_hash=_canonical_hash(rules_manifest),
        outcome_vector_hash=_canonical_hash(vector),
        outcome_vector=vector,
    )
    event = await upsert_gamma_outcome_event(session, value)
    for item in vector:
        await session.execute(
            text(
                """
                UPDATE markets
                SET status='resolved',
                    resolved_outcome=:resolved_outcome,
                    resolution_event_id=:resolution_event_id
                WHERE id=:market_id
                """
            ),
            {
                "resolved_outcome": item["outcome_yes"],
                "resolution_event_id": event.id,
                "market_id": item["market_id"],
            },
        )
    return event.id, event.outcome_vector_hash


async def _seed_benchmark_scenario(
    session: AsyncSession,
    *,
    suffix: str,
    same_batch: bool,
    sibling_in_wrong_batch: bool = False,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    event_id = f"formal-event-{suffix}"
    target_values = await _seed_market(
        session,
        suffix=f"{suffix}a",
        now=now,
        event_id=event_id,
    )
    sibling_values = await _seed_market(
        session,
        suffix=f"{suffix}b",
        now=now,
        event_id=event_id,
    )
    markets = [
        {
            "market_id": target_values[0],
            "polymarket_id": f"market-{suffix}a",
            "condition_id": target_values[1],
            "yes_token_id": target_values[2],
            "no_token_id": target_values[3],
            "market_probability": Decimal("0.40000000"),
        },
        {
            "market_id": sibling_values[0],
            "polymarket_id": f"market-{suffix}b",
            "condition_id": sibling_values[1],
            "yes_token_id": sibling_values[2],
            "no_token_id": sibling_values[3],
            "market_probability": Decimal("0.70000000"),
        },
    ]
    if same_batch:
        target_batch = await _seed_event_batch(
            session,
            suffix=f"{suffix}-same",
            markets=markets,
        )
    else:
        target_batch = await _seed_event_batch(
            session,
            suffix=f"{suffix}-target",
            markets=[markets[0]],
        )
        if sibling_in_wrong_batch:
            await _seed_event_batch(
                session,
                suffix=f"{suffix}-sibling",
                markets=[markets[1]],
            )

    batch_first_seen = target_batch["batch_first_seen"]
    assert isinstance(batch_first_seen, datetime)
    db_now = await session.scalar(text("SELECT clock_timestamp()"))
    assert isinstance(db_now, datetime)
    assert batch_first_seen < db_now
    outcome_at = batch_first_seen + (db_now - batch_first_seen) / 2
    decision_at = batch_first_seen + (outcome_at - batch_first_seen) / 2
    resolution_event_id, resolution_vector_hash = await _seed_gamma_event(
        session,
        event_id=event_id,
        markets=markets,
        outcome_at=outcome_at,
    )
    snapshot_ids = target_batch["snapshot_ids"]
    snapshot_hashes = target_batch["snapshot_hashes"]
    target_market_id = int(markets[0]["market_id"])
    target_snapshot_id = int(snapshot_ids[target_market_id])
    model_prob_id = await session.scalar(
        text(
            """
            INSERT INTO model_probs
                (market_id, ts, prob, sigma, horizon_h, components,
                 market_snapshot_id, market_anchor_protocol)
            VALUES
                (:market_id, :ts, :probability, 1.2, 12, '{}'::jsonb,
                 :snapshot_id, 'clob-dual-v1')
            RETURNING id
            """
        ),
        {
            "market_id": target_market_id,
            "ts": decision_at,
            "probability": TARGET_MODEL_PROBABILITY,
            "snapshot_id": target_snapshot_id,
        },
    )
    assert model_prob_id is not None
    return {
        "market_id": target_market_id,
        "model_prob_id": int(model_prob_id),
        "decision_at": decision_at,
        "batch_first_seen": batch_first_seen,
        "snapshot_id": target_snapshot_id,
        "snapshot_hash": str(snapshot_hashes[target_market_id]),
        "resolution_event_id": resolution_event_id,
        "resolution_vector_hash": resolution_vector_hash,
    }


async def _insert_formal_evaluation(
    session: AsyncSession,
    scenario: dict[str, Any],
    *,
    model_prob_id: int | None = None,
    decision_at: datetime | None = None,
    horizon_market_probability: Decimal | None = EXPECTED_MARKET_PROBABILITY,
    event_probability_total: Decimal | None = EXPECTED_EVENT_TOTAL,
    event_overround: Decimal | None = EXPECTED_EVENT_OVERROUND,
    model_brier: Decimal = EXPECTED_MODEL_BRIER,
    market_brier: Decimal | None = EXPECTED_MARKET_BRIER,
) -> int:
    effective_model_prob_id = model_prob_id or int(scenario["model_prob_id"])
    effective_decision_at = decision_at or scenario["decision_at"]
    evaluation_id = await session.scalar(
        text(
            """
            INSERT INTO forecast_evaluations
                (market_id, model_prob_id, decision_at, independent_sample_key,
                 model_probability, horizon_market_probability,
                 market_snapshot_at, executable_bid, executable_ask,
                 event_probability_total, event_overround,
                 resolved_outcome, resolution_event_id, resolution_vector_hash,
                 model_brier, market_brier, evaluated_at,
                 market_benchmark_protocol, market_snapshot_id,
                 market_snapshot_hash)
            VALUES
                (:market_id, :model_prob_id, :decision_at, :sample_key,
                 :model_probability, :horizon_market_probability,
                 :snapshot_at, 0.39, 0.41,
                 :event_probability_total, :event_overround,
                 false, :resolution_event_id, :resolution_vector_hash,
                 :model_brier, :market_brier, :evaluated_at,
                 'clob-dual-v1', :snapshot_id, :snapshot_hash)
            RETURNING id
            """
        ),
        {
            "market_id": scenario["market_id"],
            "model_prob_id": effective_model_prob_id,
            "decision_at": effective_decision_at,
            "sample_key": f"formal:{effective_model_prob_id}",
            "model_probability": TARGET_MODEL_PROBABILITY,
            "horizon_market_probability": horizon_market_probability,
            "snapshot_at": scenario["batch_first_seen"],
            "event_probability_total": event_probability_total,
            "event_overround": event_overround,
            "resolution_event_id": scenario["resolution_event_id"],
            "resolution_vector_hash": scenario["resolution_vector_hash"],
            "model_brier": model_brier,
            "market_brier": market_brier,
            "evaluated_at": datetime.now(UTC),
            "snapshot_id": scenario["snapshot_id"],
            "snapshot_hash": scenario["snapshot_hash"],
        },
    )
    assert evaluation_id is not None
    return int(evaluation_id)


async def test_dual_token_ledger_roundtrip_and_provenance_guards(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await _reset_clob(session)
        evidence = await _seed_pair(session, suffix="001", complete=True)

    assert evidence["batch_hash"] != "0" * 64
    assert evidence["snapshot_hash"] != "0" * 64

    async with db_sessions.begin() as session:
        decision_at = await session.scalar(text("SELECT clock_timestamp()"))
        assert decision_at is not None
        model_prob_id = await session.scalar(
            text(
                """
                INSERT INTO model_probs
                    (market_id, ts, prob, sigma, horizon_h, components,
                     market_snapshot_id, market_anchor_protocol)
                VALUES
                    (:market_id, :ts, 0.41, 1.2, 12, '{}'::jsonb,
                     :snapshot_id, 'clob-dual-v1')
                RETURNING id
                """
            ),
            {
                "market_id": evidence["market_id"],
                "ts": decision_at,
                "snapshot_id": evidence["snapshot_id"],
            },
        )
        signal_id = await session.scalar(
            text(
                """
                INSERT INTO signals
                    (market_id, ts, side, model_prob, market_price, edge,
                     kelly_frac, size_usd, confidence, status,
                     clob_snapshot_id, token_id, benchmark_protocol)
                VALUES
                    (:market_id, :ts, 'buy_no', 0.41, 0.60, 0.05,
                     0.01, 10, 'C', 'pending',
                     :snapshot_id, :token_id, 'clob-dual-v1')
                RETURNING id
                """
            ),
            {
                "market_id": evidence["market_id"],
                "ts": decision_at + timedelta(microseconds=1),
                "snapshot_id": evidence["snapshot_id"],
                "token_id": evidence["no_token_id"],
            },
        )
        filled_at = await session.scalar(text("SELECT clock_timestamp()"))
        assert filled_at is not None
        order_id = await session.scalar(
            text(
                """
                INSERT INTO orders
                    (signal_id, mode, token_id, side, price, size_usd,
                     filled_usd, status, filled_at)
                VALUES
                    (:signal_id, 'paper', :token_id, 'buy_no', 0.60, 10,
                     10, 'filled', :filled_at)
                RETURNING id
                """
            ),
            {
                "signal_id": signal_id,
                "token_id": evidence["no_token_id"],
                "filled_at": filled_at,
            },
        )
        assert order_id is not None
        assert await enroll_terminal_order(session, int(order_id)) is not None
        position_id = await session.scalar(
            text(
                """
                INSERT INTO positions
                    (market_id, order_id, mode, side, avg_price, size_shares,
                     size_usd, status, benchmark_protocol, token_id)
                VALUES
                    (:market_id, :order_id, 'paper', 'buy_no', 0.60, 16.666667,
                     10, 'open', 'clob-dual-v1', :token_id)
                RETURNING id
                """
            ),
            {
                "market_id": evidence["market_id"],
                "order_id": order_id,
                "token_id": evidence["no_token_id"],
            },
        )
        evaluation_id = await session.scalar(
            text(
                """
                INSERT INTO forecast_evaluations
                    (market_id, model_prob_id, decision_at, independent_sample_key,
                     model_probability, horizon_market_probability,
                     market_snapshot_at, executable_bid, executable_ask,
                     resolved_outcome, model_brier, market_brier, evaluated_at,
                     market_benchmark_protocol, market_snapshot_id,
                     market_snapshot_hash)
                VALUES
                    (:market_id, :model_prob_id, :decision_at, 'test:block',
                     0.41, NULL, :snapshot_at, 0.40, 0.42,
                     false, 0.1681, NULL, :evaluated_at,
                     'clob-dual-v1', :snapshot_id, :snapshot_hash)
                RETURNING id
                """
            ),
            {
                "market_id": evidence["market_id"],
                "model_prob_id": model_prob_id,
                "decision_at": decision_at,
                "snapshot_at": evidence["batch_first_seen"],
                "evaluated_at": decision_at + timedelta(seconds=2),
                "snapshot_id": evidence["snapshot_id"],
                "snapshot_hash": evidence["snapshot_hash"],
            },
        )
        assert evaluation_id is not None
        await session.execute(
            text(
                """
                UPDATE positions
                SET clv=-0.010000,
                    clv_snapshot_id=:snapshot_id,
                    clv_snapshot_hash=:snapshot_hash,
                    market_closed_at=:market_closed_at,
                    closed_at=:market_closed_at,
                    status='closed'
                WHERE id=:position_id
                """
            ),
            {
                "snapshot_id": evidence["snapshot_id"],
                "snapshot_hash": evidence["snapshot_hash"],
                "market_closed_at": evidence["batch_first_seen"]
                + timedelta(minutes=1),  # type: ignore[operator]
                "position_id": position_id,
            },
        )

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(
                text("UPDATE clob_market_snapshots SET eligible=false WHERE id=:id"),
                {"id": evidence["snapshot_id"]},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_sessions.begin() as session:
            await session.execute(
                text("UPDATE clob_book_batches SET source_etag='tampered' WHERE id=:id"),
                {"id": evidence["batch_id"]},
            )
    with pytest.raises(DBAPIError, match="sealed by CLOB evidence"):
        async with db_sessions.begin() as session:
            await session.execute(
                text("UPDATE markets SET yes_token_id='tampered' WHERE id=:id"),
                {"id": evidence["market_id"]},
            )
    with pytest.raises(DBAPIError, match="side-specific CLOB quote"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO signals
                        (market_id, ts, side, model_prob, market_price, edge,
                         kelly_frac, size_usd, confidence, status,
                         clob_snapshot_id, token_id, benchmark_protocol)
                    VALUES
                        (:market_id, :ts, 'buy_no', 0.41, 0.42, 0.05,
                         0.01, 10, 'C', 'pending',
                         :snapshot_id, :wrong_token, 'clob-dual-v1')
                    """
                ),
                {
                    "market_id": evidence["market_id"],
                    "ts": decision_at + timedelta(seconds=5),
                    "snapshot_id": evidence["snapshot_id"],
                    "wrong_token": evidence["yes_token_id"],
                },
            )


async def test_formal_event_benchmark_recomputes_exact_same_batch_vector(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await _reset_clob(session)
        scenario = await _seed_benchmark_scenario(
            session,
            suffix="101",
            same_batch=True,
        )

    forged_attempts = [
        (
            Decimal("0.360000"),
            EXPECTED_EVENT_TOTAL,
            EXPECTED_EVENT_OVERROUND,
            EXPECTED_MODEL_BRIER,
            EXPECTED_MARKET_BRIER,
            "market benchmark arithmetic mismatch",
        ),
        (
            EXPECTED_MARKET_PROBABILITY,
            Decimal("1.200000"),
            EXPECTED_EVENT_OVERROUND,
            EXPECTED_MODEL_BRIER,
            EXPECTED_MARKET_BRIER,
            "market benchmark arithmetic mismatch",
        ),
        (
            EXPECTED_MARKET_PROBABILITY,
            EXPECTED_EVENT_TOTAL,
            Decimal("0.200000"),
            EXPECTED_MODEL_BRIER,
            EXPECTED_MARKET_BRIER,
            "market benchmark arithmetic mismatch",
        ),
        (
            EXPECTED_MARKET_PROBABILITY,
            EXPECTED_EVENT_TOTAL,
            EXPECTED_EVENT_OVERROUND,
            EXPECTED_MODEL_BRIER,
            Decimal("0.13000000"),
            "market benchmark arithmetic mismatch",
        ),
        (
            EXPECTED_MARKET_PROBABILITY,
            EXPECTED_EVENT_TOTAL,
            EXPECTED_EVENT_OVERROUND,
            Decimal("0.17000000"),
            EXPECTED_MARKET_BRIER,
            "model Brier arithmetic mismatch",
        ),
    ]
    for (
        market_probability,
        event_total,
        overround,
        model_brier,
        market_brier,
        error,
    ) in forged_attempts:
        with pytest.raises(DBAPIError, match=error):
            async with db_sessions.begin() as session:
                await _insert_formal_evaluation(
                    session,
                    scenario,
                    horizon_market_probability=market_probability,
                    event_probability_total=event_total,
                    event_overround=overround,
                    model_brier=model_brier,
                    market_brier=market_brier,
                )

    with pytest.raises(DBAPIError, match="requires all market benchmark fields"):
        async with db_sessions.begin() as session:
            await _insert_formal_evaluation(
                session,
                scenario,
                market_brier=None,
            )

    stale_decision = scenario["batch_first_seen"] + timedelta(minutes=31)
    async with db_sessions.begin() as session:
        stale_model_prob_id = await session.scalar(
            text(
                """
                INSERT INTO model_probs
                    (market_id, ts, prob, sigma, horizon_h, components,
                     market_snapshot_id, market_anchor_protocol)
                VALUES
                    (:market_id, :ts, :probability, 1.2, 12, '{}'::jsonb,
                     :snapshot_id, 'clob-dual-v1')
                RETURNING id
                """
            ),
            {
                "market_id": scenario["market_id"],
                "ts": stale_decision,
                "probability": TARGET_MODEL_PROBABILITY,
                "snapshot_id": scenario["snapshot_id"],
            },
        )
    assert stale_model_prob_id is not None
    with pytest.raises(DBAPIError, match="complete fresh Gamma same-batch vector"):
        async with db_sessions.begin() as session:
            await _insert_formal_evaluation(
                session,
                scenario,
                model_prob_id=int(stale_model_prob_id),
                decision_at=stale_decision,
            )

    async with db_sessions.begin() as session:
        evaluation_id = await _insert_formal_evaluation(session, scenario)
        stored = (
            await session.execute(
                text(
                    """
                    SELECT horizon_market_probability,
                           event_probability_total,
                           event_overround,
                           model_brier,
                           market_brier
                    FROM forecast_evaluations
                    WHERE id=:id
                    """
                ),
                {"id": evaluation_id},
            )
        ).one()
    assert stored.horizon_market_probability == EXPECTED_MARKET_PROBABILITY
    assert stored.event_probability_total == EXPECTED_EVENT_TOTAL
    assert stored.event_overround == EXPECTED_EVENT_OVERROUND
    assert stored.model_brier == EXPECTED_MODEL_BRIER
    assert stored.market_brier == EXPECTED_MARKET_BRIER


@pytest.mark.parametrize(
    "sibling_in_wrong_batch",
    [False, True],
    ids=["partial-vector", "cross-batch-vector"],
)
async def test_incomplete_exact_batch_allows_only_model_brier(
    db_sessions: SessionFactory,
    sibling_in_wrong_batch: bool,
) -> None:
    suffix = "201" if sibling_in_wrong_batch else "202"
    async with db_sessions.begin() as session:
        await _reset_clob(session)
        scenario = await _seed_benchmark_scenario(
            session,
            suffix=suffix,
            same_batch=False,
            sibling_in_wrong_batch=sibling_in_wrong_batch,
        )

    with pytest.raises(
        DBAPIError,
        match="market benchmark fields require a complete fresh Gamma same-batch vector",
    ):
        async with db_sessions.begin() as session:
            await _insert_formal_evaluation(session, scenario)

    async with db_sessions.begin() as session:
        evaluation_id = await _insert_formal_evaluation(
            session,
            scenario,
            horizon_market_probability=None,
            event_probability_total=None,
            event_overround=None,
            market_brier=None,
        )
        stored = (
            await session.execute(
                text(
                    """
                    SELECT horizon_market_probability,
                           event_probability_total,
                           event_overround,
                           model_brier,
                           market_brier
                    FROM forecast_evaluations
                    WHERE id=:id
                    """
                ),
                {"id": evaluation_id},
            )
        ).one()
    assert stored.horizon_market_probability is None
    assert stored.event_probability_total is None
    assert stored.event_overround is None
    assert stored.model_brier == EXPECTED_MODEL_BRIER
    assert stored.market_brier is None


async def test_raw_tamper_incomplete_projection_and_ineligible_refs_are_rejected(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        await _reset_clob(session)

    now = datetime.now(UTC).replace(microsecond=0)
    body = b'[{"asset_id":"no"},{"asset_id":"yes"}]'
    tokens = ["no", "yes"]
    with pytest.raises(DBAPIError, match="response bytes"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO clob_book_batches
                        (request_started_at, response_received_at, source_url,
                         requested_token_ids, requested_token_count,
                         requested_tokens_sha256, source_response_body,
                         source_response_bytes, source_response_sha256, record_hash)
                    VALUES
                        (:start, :received, 'https://example.test/books',
                         CAST(:tokens AS jsonb), 2, :tokens_hash,
                         :body, :bytes, :wrong_hash, :placeholder)
                    """
                ),
                {
                    "start": now - timedelta(seconds=2),
                    "received": now - timedelta(seconds=1),
                    "tokens": json.dumps(tokens),
                    "tokens_hash": hashlib.sha256(b"no\nyes").hexdigest(),
                    "body": body,
                    "bytes": len(body),
                    "wrong_hash": "f" * 64,
                    "placeholder": "0" * 64,
                },
            )

    response_hash = hashlib.sha256(body).hexdigest()
    with pytest.raises(DBAPIError, match="complete committed snapshot projection"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO clob_book_batches
                        (request_started_at, response_received_at, source_url,
                         requested_token_ids, requested_token_count,
                         requested_tokens_sha256, source_response_body,
                         source_response_bytes, source_response_sha256, record_hash)
                    VALUES
                        (:start, :received, 'https://example.test/books',
                         CAST(:tokens AS jsonb), 2, :tokens_hash,
                         :body, :bytes, :body_hash, :placeholder)
                    """
                ),
                {
                    "start": now - timedelta(seconds=2),
                    "received": now - timedelta(seconds=1),
                    "tokens": json.dumps(tokens),
                    "tokens_hash": hashlib.sha256(b"no\nyes").hexdigest(),
                    "body": body,
                    "bytes": len(body),
                    "body_hash": response_hash,
                    "placeholder": "0" * 64,
                },
            )

    async with db_sessions.begin() as session:
        incomplete = await _seed_pair(session, suffix="002", complete=False)
    with pytest.raises(DBAPIError, match="eligible CLOB snapshot"):
        async with db_sessions.begin() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO model_probs
                        (market_id, ts, prob, sigma, horizon_h, components,
                         market_snapshot_id, market_anchor_protocol)
                    VALUES
                        (:market_id, :ts, 0.5, 1.0, 12, '{}'::jsonb,
                         :snapshot_id, 'clob-dual-v1')
                    """
                ),
                {
                    "market_id": incomplete["market_id"],
                    "ts": incomplete["batch_first_seen"] + timedelta(seconds=1),  # type: ignore[operator]
                    "snapshot_id": incomplete["snapshot_id"],
                },
            )

    # Explicit legacy rows remain valid but cannot claim the new snapshot FK.
    async with db_sessions.begin() as session:
        legacy_id = await session.scalar(
            text(
                """
                INSERT INTO model_probs
                    (market_id, ts, prob, sigma, horizon_h, components)
                VALUES (:market_id, :ts, 0.5, 1.0, 12, '{}'::jsonb)
                RETURNING id
                """
            ),
            {
                "market_id": incomplete["market_id"],
                "ts": incomplete["batch_first_seen"] + timedelta(seconds=2),  # type: ignore[operator]
            },
        )
        protocol = await session.scalar(
            text("SELECT market_anchor_protocol FROM model_probs WHERE id=:id"),
            {"id": legacy_id},
        )
        assert protocol == "legacy-synthetic-yes-v0"
