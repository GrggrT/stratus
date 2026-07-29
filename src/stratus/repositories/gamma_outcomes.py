from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.gamma import (
    CompleteGammaResolution,
    ExpectedGammaMarket,
    GammaError,
    GammaEventSnapshot,
    parse_complete_event_resolution,
)
from stratus.db.models import GammaOutcomeEvent, Market

GAMMA_OUTCOME_PROTOCOL = "gamma-outcome-v1"
GAMMA_EVENT_LOCK_SEED = 71_020_260_714


class GammaOutcomeIntegrityError(RuntimeError):
    """A witnessed event conflicts with immutable settlement evidence."""


async def lock_gamma_event(session: AsyncSession, event_id: str) -> None:
    """Serialize discovery and settlement for one mutable Gamma event universe."""

    await session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            f"hashtextextended(:event_id, {GAMMA_EVENT_LOCK_SEED}))"
        ),
        {"event_id": event_id},
    )


@dataclass(frozen=True, slots=True)
class GammaOutcomeEventValue:
    event_id: str
    evidence_protocol: str
    source_url: str
    source_http_date: datetime | None
    source_last_modified: datetime | None
    source_etag: str | None
    source_response_body: bytes
    source_response_sha256: str
    source_response_bytes: int
    source_event_updated_at: datetime | None
    first_outcome_at: datetime
    fully_resolved_at: datetime
    market_count: int
    market_universe_hash: str
    rules_manifest_hash: str
    outcome_vector_hash: str
    outcome_vector: list[dict[str, Any]]


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot hash Gamma outcome value {type(value)!r}")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _record_payload(value: GammaOutcomeEventValue) -> dict[str, Any]:
    # first_seen_at is deliberately absent: PostgreSQL supplies it from
    # clock_timestamp(), while an otherwise byte-identical retry remains
    # idempotent.
    return asdict(value)


def gamma_outcome_event_record_hash(value: GammaOutcomeEventValue) -> str:
    return _canonical_hash(_record_payload(value))


def build_gamma_outcome_event_value(
    snapshot: GammaEventSnapshot,
    resolution: CompleteGammaResolution,
    markets: Sequence[Market],
) -> GammaOutcomeEventValue:
    if snapshot.event_id != resolution.event_id:
        raise GammaOutcomeIntegrityError("Gamma snapshot and resolution event ids differ")
    if (
        not snapshot.response_body
        or len(snapshot.response_body) != snapshot.response_bytes
        or hashlib.sha256(snapshot.response_body).hexdigest()
        != snapshot.response_sha256
    ):
        raise GammaOutcomeIntegrityError(
            "Gamma snapshot bytes do not match the witnessed size/hash"
        )
    try:
        witnessed_payload = json.loads(snapshot.response_body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GammaOutcomeIntegrityError("Gamma snapshot body is not valid JSON") from exc
    if not isinstance(witnessed_payload, dict) or witnessed_payload != snapshot.payload:
        raise GammaOutcomeIntegrityError(
            "Gamma snapshot body does not match the parsed payload"
        )
    market_by_external = {market.polymarket_id: market for market in markets}
    if len(market_by_external) != len(markets):
        raise GammaOutcomeIntegrityError("local event contains duplicate Polymarket ids")
    if set(market_by_external) != {
        outcome.polymarket_id for outcome in resolution.outcomes
    }:
        raise GammaOutcomeIntegrityError("Gamma resolution does not cover local event universe")
    expected_markets = [
        ExpectedGammaMarket(
            market_id=market.id,
            polymarket_id=market.polymarket_id,
            condition_id=market.condition_id,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
            rules_hash=hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest(),
        )
        for market in markets
    ]
    try:
        witnessed_resolution = parse_complete_event_resolution(
            witnessed_payload,
            expected_event_id=snapshot.event_id,
            expected_markets=expected_markets,
        )
    except GammaError as exc:
        raise GammaOutcomeIntegrityError(
            "Gamma snapshot body is not a complete strict resolution"
        ) from exc
    if witnessed_resolution != resolution:
        raise GammaOutcomeIntegrityError(
            "Gamma parsed resolution does not match the witnessed response body"
        )

    vector: list[dict[str, Any]] = []
    universe: list[dict[str, Any]] = []
    rules_manifest: list[dict[str, Any]] = []
    for outcome in resolution.outcomes:
        market = market_by_external[outcome.polymarket_id]
        rules_hash = hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest()
        universe.append(
            {
                "market_id": market.id,
                "polymarket_id": market.polymarket_id,
                # Gamma supplies the first immutable token/condition witness
                # when legacy discovery rows lack these optional identities.
                # The DB trigger still requires equality whenever the local
                # value is present.
                "condition_id": outcome.condition_id,
                "yes_token_id": outcome.yes_token_id,
                "no_token_id": outcome.no_token_id,
            }
        )
        rules_manifest.append(
            {
                "market_id": market.id,
                "rules_hash": rules_hash,
            }
        )
        vector.append(
            {
                "market_id": market.id,
                "polymarket_id": market.polymarket_id,
                "condition_id": outcome.condition_id,
                "question_id": outcome.question_id,
                "yes_token_id": outcome.yes_token_id,
                "no_token_id": outcome.no_token_id,
                "outcome_yes": outcome.outcome_yes,
                "yes_price": outcome.yes_price,
                "no_price": outcome.no_price,
                "closed_at": _utc_iso(outcome.closed_at),
                "uma_resolution_status": outcome.uma_resolution_status,
                "resolved_by": outcome.resolved_by,
                "rules_hash": rules_hash,
            }
        )

    universe.sort(key=lambda item: int(item["market_id"]))
    rules_manifest.sort(key=lambda item: int(item["market_id"]))
    vector.sort(key=lambda item: int(item["market_id"]))
    return GammaOutcomeEventValue(
        event_id=resolution.event_id,
        evidence_protocol=GAMMA_OUTCOME_PROTOCOL,
        source_url=snapshot.source_url,
        source_http_date=snapshot.http_date,
        source_last_modified=snapshot.last_modified,
        source_etag=snapshot.etag,
        source_response_body=snapshot.response_body,
        source_response_sha256=snapshot.response_sha256,
        source_response_bytes=snapshot.response_bytes,
        source_event_updated_at=resolution.event_updated_at,
        first_outcome_at=resolution.first_outcome_at,
        fully_resolved_at=resolution.fully_resolved_at,
        market_count=len(vector),
        market_universe_hash=_canonical_hash(universe),
        rules_manifest_hash=_canonical_hash(rules_manifest),
        outcome_vector_hash=_canonical_hash(vector),
        outcome_vector=vector,
    )


def _value_from_model(event: GammaOutcomeEvent) -> GammaOutcomeEventValue:
    return GammaOutcomeEventValue(
        event_id=event.event_id,
        evidence_protocol=event.evidence_protocol,
        source_url=event.source_url,
        source_http_date=event.source_http_date,
        source_last_modified=event.source_last_modified,
        source_etag=event.source_etag,
        source_response_body=event.source_response_body,
        source_response_sha256=event.source_response_sha256,
        source_response_bytes=event.source_response_bytes,
        source_event_updated_at=event.source_event_updated_at,
        first_outcome_at=event.first_outcome_at,
        fully_resolved_at=event.fully_resolved_at,
        market_count=event.market_count,
        market_universe_hash=event.market_universe_hash,
        rules_manifest_hash=event.rules_manifest_hash,
        outcome_vector_hash=event.outcome_vector_hash,
        outcome_vector=event.outcome_vector,
    )


def _resolution_matches_vector(
    resolution: CompleteGammaResolution,
    event: GammaOutcomeEvent,
) -> bool:
    if (
        resolution.event_id != event.event_id
        or resolution.first_outcome_at != event.first_outcome_at
        or resolution.fully_resolved_at != event.fully_resolved_at
        or resolution.event_updated_at != event.source_event_updated_at
        or len(resolution.outcomes) != event.market_count
    ):
        return False
    vector_by_external = {
        item.get("polymarket_id"): item
        for item in event.outcome_vector
        if isinstance(item, dict)
    }
    if len(vector_by_external) != event.market_count:
        return False
    for outcome in resolution.outcomes:
        item = vector_by_external.get(outcome.polymarket_id)
        if not isinstance(item, dict) or (
            item.get("condition_id") != outcome.condition_id
            or item.get("question_id") != outcome.question_id
            or item.get("yes_token_id") != outcome.yes_token_id
            or item.get("no_token_id") != outcome.no_token_id
            or item.get("outcome_yes") is not outcome.outcome_yes
            or item.get("yes_price") != outcome.yes_price
            or item.get("no_price") != outcome.no_price
            or item.get("closed_at") != _utc_iso(outcome.closed_at)
            or item.get("uma_resolution_status") != outcome.uma_resolution_status
            or item.get("resolved_by") != outcome.resolved_by
        ):
            return False
    return True


def gamma_outcome_event_record_is_valid(event: GammaOutcomeEvent) -> bool:
    try:
        if (
            not isinstance(event.source_response_body, bytes)
            or len(event.source_response_body) != event.source_response_bytes
            or hashlib.sha256(event.source_response_body).hexdigest()
            != event.source_response_sha256
        ):
            return False
        vector = event.outcome_vector
        if not isinstance(vector, list) or len(vector) != event.market_count:
            return False
        market_ids: list[int] = []
        for item in vector:
            if not isinstance(item, dict):
                return False
            market_id = item.get("market_id")
            if not isinstance(market_id, int) or isinstance(market_id, bool):
                return False
            market_ids.append(market_id)
        if market_ids != sorted(market_ids) or len(set(market_ids)) != len(market_ids):
            return False
        payload = json.loads(event.source_response_body)
        if not isinstance(payload, dict):
            return False
        expected_markets = [
            ExpectedGammaMarket(
                market_id=int(item["market_id"]),
                polymarket_id=str(item["polymarket_id"]),
                condition_id=item["condition_id"],
                yes_token_id=item["yes_token_id"],
                no_token_id=item["no_token_id"],
                rules_hash=str(item["rules_hash"]),
            )
            for item in vector
        ]
        resolution = parse_complete_event_resolution(
            payload,
            expected_event_id=event.event_id,
            expected_markets=expected_markets,
        )
        if not _resolution_matches_vector(resolution, event):
            return False
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
            {
                "market_id": item["market_id"],
                "rules_hash": item["rules_hash"],
            }
            for item in vector
        ]
        return bool(
            _canonical_hash(vector) == event.outcome_vector_hash
            and _canonical_hash(universe) == event.market_universe_hash
            and _canonical_hash(rules_manifest) == event.rules_manifest_hash
            and gamma_outcome_event_record_hash(_value_from_model(event))
            == event.record_hash
        )
    except (
        AttributeError,
        GammaError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return False


def gamma_outcome_map(event: GammaOutcomeEvent) -> dict[int, bool]:
    if not gamma_outcome_event_record_is_valid(event):
        raise GammaOutcomeIntegrityError(
            f"Gamma outcome event {event.event_id} has an invalid record hash"
        )
    result: dict[int, bool] = {}
    for item in event.outcome_vector:
        market_id = item.get("market_id")
        outcome_yes = item.get("outcome_yes")
        if (
            not isinstance(market_id, int)
            or isinstance(market_id, bool)
            or not isinstance(outcome_yes, bool)
            or market_id in result
        ):
            raise GammaOutcomeIntegrityError(
                f"Gamma outcome event {event.event_id} has a malformed vector"
            )
        result[market_id] = outcome_yes
    if len(result) != event.market_count or sum(result.values()) != 1:
        raise GammaOutcomeIntegrityError(
            f"Gamma outcome event {event.event_id} is not mutually exclusive"
        )
    return result


def gamma_closed_at_map(event: GammaOutcomeEvent) -> dict[int, datetime]:
    # Validate the complete record and vector first.
    gamma_outcome_map(event)
    result: dict[int, datetime] = {}
    for item in event.outcome_vector:
        market_id = int(item["market_id"])
        raw_closed_at = item.get("closed_at")
        if not isinstance(raw_closed_at, str):
            raise GammaOutcomeIntegrityError(
                f"Gamma outcome event {event.event_id} lacks closed_at"
            )
        try:
            closed_at = datetime.fromisoformat(raw_closed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GammaOutcomeIntegrityError(
                f"Gamma outcome event {event.event_id} has invalid closed_at"
            ) from exc
        if closed_at.tzinfo is None:
            closed_at = closed_at.replace(tzinfo=UTC)
        result[market_id] = closed_at.astimezone(UTC)
    return result


async def upsert_gamma_outcome_event(
    session: AsyncSession, value: GammaOutcomeEventValue
) -> GammaOutcomeEvent:
    row = asdict(value)
    row["record_hash"] = gamma_outcome_event_record_hash(value)
    await session.execute(
        insert(GammaOutcomeEvent)
        .values(**row)
        .on_conflict_do_nothing(constraint="uq_gamma_outcome_events_event_id")
    )
    stored = (
        await session.execute(
            select(GammaOutcomeEvent)
            .where(GammaOutcomeEvent.event_id == value.event_id)
            .with_for_update()
        )
    ).scalar_one()
    if not gamma_outcome_event_record_is_valid(stored):
        raise GammaOutcomeIntegrityError(
            f"stored Gamma outcome event {value.event_id} failed record attestation"
        )
    if (
        stored.evidence_protocol != value.evidence_protocol
        or stored.market_universe_hash != value.market_universe_hash
        or stored.rules_manifest_hash != value.rules_manifest_hash
        or stored.outcome_vector_hash != value.outcome_vector_hash
        or stored.first_outcome_at != value.first_outcome_at
        or stored.fully_resolved_at != value.fully_resolved_at
    ):
        raise GammaOutcomeIntegrityError(
            f"conflicting immutable Gamma outcome for event {value.event_id}"
        )
    # Gamma may update unrelated event metadata after resolution. A later raw
    # response hash therefore does not replace or invalidate the first witness
    # when the canonical resolution projection is identical.
    return stored
