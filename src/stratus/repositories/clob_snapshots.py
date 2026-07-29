from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from email.utils import parsedate_to_datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.collectors.clob import (
    BinaryBookPair,
    ClobBooksBatch,
    ClobError,
    StrictBookSnapshot,
    parse_binary_book_pair,
    parse_strict_book_snapshot,
)
from stratus.db.models import ClobBookBatch, ClobMarketSnapshot
from stratus.repositories.market_prices import PriceTarget
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL


class ClobSnapshotIntegrityError(RuntimeError):
    """Raw CLOB evidence and its immutable semantic projection disagree."""


@dataclass(frozen=True, slots=True)
class ClobBookBatchValue:
    evidence_protocol: str
    request_started_at: datetime
    response_received_at: datetime
    source_url: str
    source_http_date: datetime | None
    source_etag: str | None
    requested_token_ids: list[str]
    requested_token_count: int
    requested_tokens_sha256: str
    source_response_body: bytes
    source_response_bytes: int
    source_response_sha256: str


@dataclass(frozen=True, slots=True)
class ClobMarketSnapshotValue:
    market_id: int
    condition_id: str
    yes_token_id: str
    no_token_id: str
    yes_source_timestamp: datetime
    no_source_timestamp: datetime
    yes_source_hash: str
    no_source_hash: str
    yes_payload_sha256: str
    no_payload_sha256: str
    yes_tick_size: Decimal
    no_tick_size: Decimal
    yes_neg_risk: bool
    no_neg_risk: bool
    yes_best_bid: Decimal | None
    yes_best_ask: Decimal | None
    yes_midpoint: Decimal | None
    yes_bid_depth_usd: Decimal | None
    yes_ask_depth_usd: Decimal | None
    no_best_bid: Decimal | None
    no_best_ask: Decimal | None
    no_midpoint: Decimal | None
    no_bid_depth_usd: Decimal | None
    no_ask_depth_usd: Decimal | None
    source_skew_ms: int
    complement_residual: Decimal | None
    binary_yes_probability: Decimal | None
    complete: bool
    eligible: bool


@dataclass(frozen=True, slots=True)
class PersistedClobBatch:
    batch: ClobBookBatch
    snapshots: tuple[ClobMarketSnapshot, ...]


def requested_tokens_sha256(token_ids: Sequence[str]) -> str:
    ordered = sorted(token_ids)
    if not ordered or len(set(ordered)) != len(ordered):
        raise ClobSnapshotIntegrityError("requested token ids must be nonempty and distinct")
    return hashlib.sha256("\n".join(ordered).encode("utf-8")).hexdigest()


def reparse_clob_batch(batch: ClobBooksBatch) -> tuple[StrictBookSnapshot, ...]:
    """Independently parse exact raw bytes and prove the requested universe."""

    if (
        not batch.response_body
        or len(batch.response_body) != batch.response_bytes
        or hashlib.sha256(batch.response_body).hexdigest() != batch.response_sha256
    ):
        raise ClobSnapshotIntegrityError("CLOB batch bytes do not match size/hash")
    requested = batch.requested_token_ids
    if not requested or len(set(requested)) != len(requested):
        raise ClobSnapshotIntegrityError("CLOB requested token universe is invalid")
    try:
        payload = json.loads(batch.response_body, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ClobSnapshotIntegrityError) as exc:
        raise ClobSnapshotIntegrityError("CLOB response body is not strict JSON") from exc
    raw_by_token = _exact_raw_books(payload, requested)
    try:
        reparsed = tuple(parse_strict_book_snapshot(raw_by_token[token]) for token in requested)
    except ClobError as exc:
        raise ClobSnapshotIntegrityError("CLOB raw book failed strict parsing") from exc
    supplied_by_token = {snapshot.token_id: snapshot for snapshot in batch.snapshots}
    if (
        len(supplied_by_token) != len(batch.snapshots)
        or set(supplied_by_token) != set(requested)
        or any(supplied_by_token[item.token_id] != item for item in reparsed)
    ):
        raise ClobSnapshotIntegrityError(
            "collector snapshots do not match independently reparsed raw books"
        )
    return reparsed


def build_clob_book_batch_value(batch: ClobBooksBatch) -> ClobBookBatchValue:
    reparse_clob_batch(batch)
    ordered_tokens = sorted(batch.requested_token_ids)
    if len(ordered_tokens) < 2 or len(ordered_tokens) % 2:
        raise ClobSnapshotIntegrityError(
            "paired CLOB batch must contain a positive even token count"
        )
    headers = dict(batch.response_headers)
    return ClobBookBatchValue(
        evidence_protocol=CLOB_DUAL_PROTOCOL,
        request_started_at=batch.request_started_at,
        response_received_at=batch.response_received_at,
        source_url=batch.source_url,
        source_http_date=_parse_http_date(headers.get("date")),
        source_etag=headers.get("etag"),
        requested_token_ids=ordered_tokens,
        requested_token_count=len(ordered_tokens),
        requested_tokens_sha256=requested_tokens_sha256(ordered_tokens),
        source_response_body=batch.response_body,
        source_response_bytes=batch.response_bytes,
        source_response_sha256=batch.response_sha256,
    )


def build_clob_market_snapshot_values(
    batch: ClobBooksBatch,
    targets: Sequence[PriceTarget],
) -> tuple[ClobMarketSnapshotValue, ...]:
    reparsed = reparse_clob_batch(batch)
    snapshots_by_token = {snapshot.token_id: snapshot for snapshot in reparsed}
    raw_payload = json.loads(batch.response_body, parse_constant=_reject_json_constant)
    raw_by_token = _exact_raw_books(raw_payload, batch.requested_token_ids)
    seen_markets: set[int] = set()
    used_tokens: set[str] = set()
    values: list[ClobMarketSnapshotValue] = []
    for target in sorted(targets, key=lambda item: item.market_id):
        if target.market_id in seen_markets:
            raise ClobSnapshotIntegrityError(f"duplicate target market {target.market_id}")
        seen_markets.add(target.market_id)
        target_tokens = {target.yes_token_id, target.no_token_id}
        if len(target_tokens) != 2 or not target_tokens.issubset(snapshots_by_token):
            raise ClobSnapshotIntegrityError(
                f"market {target.market_id} tokens are absent or non-distinct"
            )
        if used_tokens.intersection(target_tokens):
            raise ClobSnapshotIntegrityError("one CLOB token is assigned to multiple markets")
        used_tokens.update(target_tokens)
        if any(
            raw_by_token[token_id].get("market") != target.condition_id
            for token_id in target_tokens
        ):
            raise ClobSnapshotIntegrityError(
                f"market {target.market_id} condition differs from raw CLOB payload"
            )
        pair = parse_binary_book_pair(
            reparsed,
            yes_token_id=target.yes_token_id,
            no_token_id=target.no_token_id,
        )
        values.append(_snapshot_value(target, pair))
    if used_tokens != set(batch.requested_token_ids):
        missing = sorted(set(batch.requested_token_ids) - used_tokens)
        extra = sorted(used_tokens - set(batch.requested_token_ids))
        raise ClobSnapshotIntegrityError(
            f"market pairs do not exactly cover batch tokens: missing={missing} extra={extra}"
        )
    return tuple(values)


def _snapshot_value(
    target: PriceTarget,
    pair: BinaryBookPair,
) -> ClobMarketSnapshotValue:
    return ClobMarketSnapshotValue(
        market_id=target.market_id,
        condition_id=target.condition_id,
        yes_token_id=target.yes_token_id,
        no_token_id=target.no_token_id,
        yes_source_timestamp=pair.yes.source_ts,
        no_source_timestamp=pair.no.source_ts,
        yes_source_hash=pair.yes.source_hash,
        no_source_hash=pair.no.source_hash,
        yes_payload_sha256=pair.yes.payload_sha256,
        no_payload_sha256=pair.no.payload_sha256,
        yes_tick_size=pair.yes.tick_size,
        no_tick_size=pair.no.tick_size,
        yes_neg_risk=pair.yes.neg_risk,
        no_neg_risk=pair.no.neg_risk,
        yes_best_bid=pair.yes.best_bid,
        yes_best_ask=pair.yes.best_ask,
        yes_midpoint=pair.yes.midpoint,
        yes_bid_depth_usd=(
            pair.yes.bid_depth_usd if pair.yes.best_bid is not None else None
        ),
        yes_ask_depth_usd=(
            pair.yes.ask_depth_usd if pair.yes.best_ask is not None else None
        ),
        no_best_bid=pair.no.best_bid,
        no_best_ask=pair.no.best_ask,
        no_midpoint=pair.no.midpoint,
        no_bid_depth_usd=(pair.no.bid_depth_usd if pair.no.best_bid is not None else None),
        no_ask_depth_usd=(pair.no.ask_depth_usd if pair.no.best_ask is not None else None),
        source_skew_ms=int(
            (pair.source_skew_seconds * Decimal(1000)).quantize(
                Decimal(1), rounding=ROUND_HALF_UP
            )
        ),
        complement_residual=_scale_8(pair.complement_residual),
        binary_yes_probability=_scale_8(pair.binary_yes_probability),
        complete=pair.yes.complete and pair.no.complete,
        eligible=pair.eligible,
    )


async def persist_clob_batch(
    session: AsyncSession,
    batch: ClobBooksBatch,
    targets: Sequence[PriceTarget],
) -> PersistedClobBatch:
    """Append a raw batch and every pair; caller must commit them together."""

    batch_value = build_clob_book_batch_value(batch)
    snapshot_values = build_clob_market_snapshot_values(batch, targets)
    stored_batch = ClobBookBatch(**asdict(batch_value), record_hash="0" * 64)
    session.add(stored_batch)
    await session.flush()
    await session.refresh(stored_batch)

    stored_snapshots = tuple(
        ClobMarketSnapshot(
            batch_id=stored_batch.id,
            **asdict(value),
            record_hash="0" * 64,
        )
        for value in snapshot_values
    )
    session.add_all(stored_snapshots)
    await session.flush()
    for snapshot in stored_snapshots:
        await session.refresh(snapshot)
    if not clob_batch_record_is_valid(stored_batch, stored_snapshots):
        raise ClobSnapshotIntegrityError("stored CLOB batch failed independent attestation")
    return PersistedClobBatch(batch=stored_batch, snapshots=stored_snapshots)


def clob_batch_record_is_valid(
    batch: ClobBookBatch,
    snapshots: Sequence[ClobMarketSnapshot],
) -> bool:
    try:
        if (
            batch.evidence_protocol != CLOB_DUAL_PROTOCOL
            or not batch.source_url
            or batch.request_started_at > batch.response_received_at
            or batch.response_received_at > batch.first_seen_at
            or not isinstance(batch.source_response_body, bytes)
            or len(batch.source_response_body) != batch.source_response_bytes
            or hashlib.sha256(batch.source_response_body).hexdigest()
            != batch.source_response_sha256
            or batch.requested_token_ids != sorted(batch.requested_token_ids)
            or batch.requested_token_count < 2
            or batch.requested_token_count % 2 != 0
            or len(set(batch.requested_token_ids)) != batch.requested_token_count
            or len(batch.requested_token_ids) != batch.requested_token_count
            or requested_tokens_sha256(batch.requested_token_ids)
            != batch.requested_tokens_sha256
            or clob_book_batch_record_hash(batch) != batch.record_hash
        ):
            return False
        raw_payload = json.loads(
            batch.source_response_body,
            parse_constant=_reject_json_constant,
        )
        raw_by_token = _exact_raw_books(raw_payload, tuple(batch.requested_token_ids))
        parsed_by_token = {
            token_id: parse_strict_book_snapshot(raw_by_token[token_id])
            for token_id in batch.requested_token_ids
        }
        if len({snapshot.market_id for snapshot in snapshots}) != len(snapshots):
            return False
        used_tokens: set[str] = set()
        for snapshot in snapshots:
            if snapshot.batch_id != batch.id:
                return False
            tokens = {snapshot.yes_token_id, snapshot.no_token_id}
            if len(tokens) != 2 or used_tokens.intersection(tokens):
                return False
            used_tokens.update(tokens)
            if not tokens.issubset(parsed_by_token):
                return False
            if any(raw_by_token[token].get("market") != snapshot.condition_id for token in tokens):
                return False
            pair = parse_binary_book_pair(
                parsed_by_token.values(),
                yes_token_id=snapshot.yes_token_id,
                no_token_id=snapshot.no_token_id,
            )
            target = PriceTarget(
                market_id=snapshot.market_id,
                event_id="stored",
                condition_id=snapshot.condition_id,
                yes_token_id=snapshot.yes_token_id,
                no_token_id=snapshot.no_token_id,
            )
            expected = _snapshot_value(target, pair)
            if not _stored_snapshot_matches(snapshot, expected):
                return False
            if clob_market_snapshot_record_hash(batch, snapshot) != snapshot.record_hash:
                return False
        return used_tokens == set(batch.requested_token_ids)
    except (
        AttributeError,
        ClobError,
        ClobSnapshotIntegrityError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return False


def _stored_snapshot_matches(
    stored: ClobMarketSnapshot,
    expected: ClobMarketSnapshotValue,
) -> bool:
    return all(
        getattr(stored, field) == value
        for field, value in asdict(expected).items()
    )


async def load_clob_batch_snapshots(
    session: AsyncSession,
    batch_id: int,
    *,
    eligible_only: bool = False,
) -> list[ClobMarketSnapshot]:
    statement = select(ClobMarketSnapshot).where(ClobMarketSnapshot.batch_id == batch_id)
    if eligible_only:
        statement = statement.where(ClobMarketSnapshot.eligible.is_(True))
    return list(
        (
            await session.scalars(
                statement.order_by(ClobMarketSnapshot.market_id)
            )
        ).all()
    )


async def load_attested_token_book(
    session: AsyncSession,
    *,
    snapshot_id: int,
    token_id: str,
) -> tuple[ClobBookBatch, ClobMarketSnapshot, dict[str, Any]]:
    """Return one exact raw token book after re-attesting its whole batch.

    Execution-cost evidence must never consume the mutable compatibility
    price projection or reconstruct a book from best-bid/ask columns.  This
    loader replays the immutable response bytes, validates every paired
    snapshot in the cited batch, and only then exposes the requested purchased
    token payload.
    """

    if snapshot_id <= 0 or not token_id or token_id != token_id.strip():
        raise ClobSnapshotIntegrityError("snapshot and token identities are required")
    row = (
        await session.execute(
            select(ClobBookBatch, ClobMarketSnapshot)
            .join(
                ClobMarketSnapshot,
                ClobMarketSnapshot.batch_id == ClobBookBatch.id,
            )
            .where(ClobMarketSnapshot.id == snapshot_id)
        )
    ).one_or_none()
    if row is None:
        raise ClobSnapshotIntegrityError(f"CLOB snapshot {snapshot_id} does not exist")
    batch, snapshot = row
    if token_id not in {snapshot.yes_token_id, snapshot.no_token_id}:
        raise ClobSnapshotIntegrityError(
            f"token {token_id} is not part of CLOB snapshot {snapshot_id}"
        )
    siblings = await load_clob_batch_snapshots(session, batch.id)
    if not clob_batch_record_is_valid(batch, siblings):
        raise ClobSnapshotIntegrityError(
            f"CLOB batch {batch.id} failed independent re-attestation"
        )
    try:
        payload = json.loads(
            batch.source_response_body,
            parse_constant=_reject_json_constant,
        )
        raw_by_token = _exact_raw_books(payload, tuple(batch.requested_token_ids))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClobSnapshotIntegrityError("stored CLOB response is not strict JSON") from exc
    raw = raw_by_token.get(token_id)
    if raw is None:
        raise ClobSnapshotIntegrityError(
            f"token {token_id} is absent from CLOB batch {batch.id}"
        )
    return batch, snapshot, raw


async def first_eligible_clob_snapshot_at_or_after(
    session: AsyncSession,
    *,
    market_id: int,
    not_before: datetime,
) -> ClobMarketSnapshot | None:
    """Select the first immutable execution book witnessed after an intent.

    Ascending database-witness order is deliberate: using the latest book
    would introduce hindsight whenever multiple price cycles completed before
    the executor next ran.
    """

    if market_id <= 0:
        raise ValueError("market_id must be positive")
    if not_before.tzinfo is None:
        raise ValueError("not_before must be timezone-aware")
    snapshots = await eligible_clob_snapshots_at_or_after(
        session,
        market_id=market_id,
        not_before=not_before,
    )
    return snapshots[0] if snapshots else None


async def eligible_clob_snapshots_at_or_after(
    session: AsyncSession,
    *,
    market_id: int,
    not_before: datetime,
) -> list[ClobMarketSnapshot]:
    """Return every causal candidate in database-witness order.

    Execution decides whether a candidate is *qualifying* only after replaying
    its exact raw token book.  Returning the ordered candidate stream avoids
    pinning a resting order forever to the first merely eligible, but
    non-crossing, snapshot.
    """

    if market_id <= 0:
        raise ValueError("market_id must be positive")
    if not_before.tzinfo is None:
        raise ValueError("not_before must be timezone-aware")
    return list(
        (
            await session.scalars(
                select(ClobMarketSnapshot)
                .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
                .where(
                    ClobMarketSnapshot.market_id == market_id,
                    ClobMarketSnapshot.complete.is_(True),
                    ClobMarketSnapshot.eligible.is_(True),
                    ClobBookBatch.request_started_at >= not_before,
                    ClobBookBatch.first_seen_at >= not_before,
                )
                .order_by(
                    ClobBookBatch.first_seen_at.asc(),
                    ClobMarketSnapshot.id.asc(),
                )
            )
        ).all()
    )


async def latest_eligible_clob_snapshots(
    session: AsyncSession,
    market_ids: Sequence[int] | None = None,
) -> list[ClobMarketSnapshot]:
    statement = (
        select(ClobMarketSnapshot)
        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
        .where(ClobMarketSnapshot.eligible.is_(True))
    )
    if market_ids is not None:
        if not market_ids:
            return []
        statement = statement.where(ClobMarketSnapshot.market_id.in_(set(market_ids)))
    statement = statement.distinct(ClobMarketSnapshot.market_id).order_by(
        ClobMarketSnapshot.market_id,
        ClobBookBatch.first_seen_at.desc(),
        ClobMarketSnapshot.id.desc(),
    )
    return list((await session.scalars(statement)).all())


async def latest_eligible_same_batch(
    session: AsyncSession,
    market_ids: Sequence[int],
) -> list[ClobMarketSnapshot]:
    unique_market_ids = sorted(set(market_ids))
    if not unique_market_ids:
        return []
    if len(unique_market_ids) != len(market_ids):
        raise ValueError("market_ids must be distinct")
    batch_id = await session.scalar(
        select(ClobMarketSnapshot.batch_id)
        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
        .where(
            ClobMarketSnapshot.eligible.is_(True),
            ClobMarketSnapshot.market_id.in_(unique_market_ids),
        )
        .group_by(ClobMarketSnapshot.batch_id, ClobBookBatch.first_seen_at)
        .having(func.count(ClobMarketSnapshot.market_id) == len(unique_market_ids))
        .order_by(ClobBookBatch.first_seen_at.desc(), ClobMarketSnapshot.batch_id.desc())
        .limit(1)
    )
    if batch_id is None:
        return []
    snapshots = await load_clob_batch_snapshots(session, batch_id, eligible_only=True)
    requested = set(unique_market_ids)
    return [snapshot for snapshot in snapshots if snapshot.market_id in requested]


def _exact_raw_books(
    payload: Any,
    requested: Sequence[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ClobSnapshotIntegrityError("CLOB response is not an object array")
    raw_by_token: dict[str, dict[str, Any]] = {}
    duplicate: set[str] = set()
    for item in payload:
        token_id = item.get("asset_id")
        if not isinstance(token_id, str) or not token_id or token_id != token_id.strip():
            raise ClobSnapshotIntegrityError("CLOB response contains invalid asset_id")
        if token_id in raw_by_token:
            duplicate.add(token_id)
        raw_by_token[token_id] = item
    if duplicate:
        raise ClobSnapshotIntegrityError(f"CLOB response duplicates tokens: {sorted(duplicate)}")
    if set(raw_by_token) != set(requested):
        missing = sorted(set(requested) - set(raw_by_token))
        extra = sorted(set(raw_by_token) - set(requested))
        raise ClobSnapshotIntegrityError(
            f"CLOB response universe mismatch: missing={missing} extra={extra}"
        )
    return raw_by_token


def clob_book_batch_record_hash(batch: ClobBookBatch) -> str:
    return _canonical_db_hash(
        {
            "protocol": batch.evidence_protocol,
            "request_started_at": batch.request_started_at,
            "response_received_at": batch.response_received_at,
            "first_seen_at": batch.first_seen_at,
            "source_url": batch.source_url,
            "source_http_date": batch.source_http_date,
            "source_etag": batch.source_etag,
            "requested_tokens_sha256": batch.requested_tokens_sha256,
            "requested_token_count": batch.requested_token_count,
            "source_response_sha256": batch.source_response_sha256,
            "source_response_bytes": batch.source_response_bytes,
        }
    )


def clob_market_snapshot_record_hash(
    batch: ClobBookBatch,
    snapshot: ClobMarketSnapshot,
) -> str:
    fields = (
        "market_id",
        "condition_id",
        "yes_token_id",
        "no_token_id",
        "yes_source_timestamp",
        "no_source_timestamp",
        "yes_source_hash",
        "no_source_hash",
        "yes_payload_sha256",
        "no_payload_sha256",
        "yes_tick_size",
        "no_tick_size",
        "yes_neg_risk",
        "no_neg_risk",
        "yes_best_bid",
        "yes_best_ask",
        "yes_midpoint",
        "yes_bid_depth_usd",
        "yes_ask_depth_usd",
        "no_best_bid",
        "no_best_ask",
        "no_midpoint",
        "no_bid_depth_usd",
        "no_ask_depth_usd",
        "source_skew_ms",
        "complement_residual",
        "binary_yes_probability",
        "complete",
        "eligible",
    )
    payload: dict[str, object] = {
        "batch_id": snapshot.batch_id,
        "batch_first_seen_at": batch.first_seen_at,
    }
    payload.update({field: getattr(snapshot, field) for field in fields})
    return _canonical_db_hash(payload)


def _canonical_db_hash(value: object) -> str:
    return hashlib.sha256(_postgres_jsonb_text(value).encode("utf-8")).hexdigest()


def _postgres_jsonb_text(value: object) -> str:
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSONB object keys must be strings")
        keys = sorted(
            value,
            key=lambda item: (len(item.encode("utf-8")), item.encode("utf-8")),
        )
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {_postgres_jsonb_text(value[key])}"
            for key in keys
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, datetime):
        return json.dumps(_postgres_timestamp(value))
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("JSONB numeric must be finite")
        return format(value, "f")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _postgres_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("record timestamps must be timezone-aware")
    rendered = value.astimezone(UTC).isoformat()
    if "." not in rendered:
        return rendered
    prefix, suffix = rendered.split(".", 1)
    fraction, offset = suffix.split("+", 1)
    fraction = fraction.rstrip("0")
    return f"{prefix}{'.' + fraction if fraction else ''}+{offset}"


def _parse_http_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _scale_8(value: Decimal | None) -> Decimal | None:
    return (
        None
        if value is None
        else value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP)
    )


def _reject_json_constant(value: str) -> None:
    raise ClobSnapshotIntegrityError(f"non-standard JSON constant {value}")
