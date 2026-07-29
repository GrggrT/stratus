from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import (
    ClobBooksBatch,
    ClobClient,
    ClobError,
    parse_strict_book_snapshot,
)
from stratus.config import Settings, get_settings
from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    Market,
    MarketPrice,
    MarketStatus,
    RuntimeControl,
    Station,
    StationStage,
    WeatherKind,
)
from stratus.jobs.audit_storage_capacity import audit_storage_capacity
from stratus.jobs.fetch_prices import fetch_prices
from stratus.repositories.clob_snapshots import (
    ClobSnapshotIntegrityError,
    build_clob_book_batch_value,
    build_clob_market_snapshot_values,
    clob_batch_record_is_valid,
    first_eligible_clob_snapshot_at_or_after,
    latest_eligible_clob_snapshots,
    latest_eligible_same_batch,
    load_attested_token_book,
    load_clob_batch_snapshots,
    persist_clob_batch,
)
from stratus.repositories.market_prices import (
    PriceTarget,
    mark_prices_fetched,
    markets_due_for_price_fetch,
    project_eligible_clob_prices,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


def _capacity_settings(tmp_path: Path) -> Settings:
    return get_settings().model_copy(
        update={
            "clob_ledger_capacity_path": str(tmp_path),
            "clob_ledger_capacity_min_free_bytes": 500_000_000,
        }
    )


async def _seed_markets(
    db_sessions: SessionFactory,
    *,
    count: int = 2,
) -> list[PriceTarget]:
    suffix = uuid4().hex
    event_id = f"dual-clob-{suffix}"
    async with db_sessions.begin() as session:
        station = Station(
            icao=f"C{suffix[:3].upper()}",
            name="Dual CLOB test station",
            city="Test City",
            lat=Decimal("50.000000"),
            lon=Decimal("8.000000"),
            tz="Europe/Berlin",
            stage=StationStage.PAPER,
            active=True,
        )
        session.add(station)
        await session.flush()
        targets: list[PriceTarget] = []
        for index in range(count):
            condition_id = f"0x{suffix}{index:02d}"
            yes_token_id = f"yes-{suffix}-{index}"
            no_token_id = f"no-{suffix}-{index}"
            market = Market(
                polymarket_id=f"poly-{suffix}-{index}",
                event_id=event_id,
                condition_id=condition_id,
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                station_id=station.id,
                market_date=datetime.now(UTC).date(),
                kind=WeatherKind.HIGH,
                bucket_low_c=None,
                bucket_high_c=Decimal("20"),
                rules_raw="immutable dual-token test rules",
                station_verified=True,
                status=MarketStatus.ACTIVE,
                resolves_at=datetime.now(UTC) + timedelta(hours=2),
            )
            session.add(market)
            await session.flush()
            targets.append(
                PriceTarget(
                    market_id=market.id,
                    event_id=event_id,
                    condition_id=condition_id,
                    yes_token_id=yes_token_id,
                    no_token_id=no_token_id,
                )
            )
        return targets


def _raw_book(
    target: PriceTarget,
    *,
    yes: bool,
    source_timestamp: datetime,
    one_sided: bool = False,
) -> dict[str, Any]:
    return {
        "market": target.condition_id,
        "asset_id": target.yes_token_id if yes else target.no_token_id,
        "timestamp": str(int(source_timestamp.timestamp() * 1000)),
        "hash": f"opaque-source-{target.market_id}-{'yes' if yes else 'no'}",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": []
        if one_sided
        else [
            {
                "price": "0.39" if yes else "0.59",
                "size": "12.345678",
            }
        ],
        "asks": [
            {
                "price": "0.41" if yes else "0.61",
                "size": "23.456789",
            }
        ],
    }


def _collected_batch(
    targets: list[PriceTarget],
    *,
    one_sided_market_id: int | None = None,
) -> ClobBooksBatch:
    response_received_at = datetime.now(UTC) - timedelta(milliseconds=100)
    source_timestamp = response_received_at - timedelta(milliseconds=100)
    raw_books = [
        _raw_book(
            target,
            yes=yes,
            source_timestamp=source_timestamp,
            one_sided=yes and target.market_id == one_sided_market_id,
        )
        for target in targets
        for yes in (True, False)
    ]
    raw = json.dumps(list(reversed(raw_books)), separators=(",", ":")).encode()
    requested = tuple(sorted(str(book["asset_id"]) for book in raw_books))
    by_token = {str(book["asset_id"]): book for book in raw_books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=response_received_at - timedelta(milliseconds=200),
        response_received_at=response_received_at,
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(
            ("date", format_datetime(response_received_at, usegmt=True)),
            ("etag", '"dual-clob-test"'),
        ),
        snapshots=tuple(parse_strict_book_snapshot(by_token[token]) for token in requested),
    )


async def test_atomic_roundtrip_server_hashes_projection_and_loaders(
    db_sessions: SessionFactory,
) -> None:
    event_targets = await _seed_markets(db_sessions)
    targets = event_targets + await _seed_markets(db_sessions, count=1)
    collected = _collected_batch(targets)
    async with db_sessions.begin() as session:
        persisted = await persist_clob_batch(session, collected, targets)
        projected = await project_eligible_clob_prices(
            session,
            persisted.batch,
            persisted.snapshots,
        )
        await mark_prices_fetched(
            session,
            [target.market_id for target in targets],
            persisted.batch.first_seen_at,
        )
        batch_id = persisted.batch.id
        first_seen_at = persisted.batch.first_seen_at
        assert projected == len(targets)
        assert first_seen_at >= collected.response_received_at
        assert persisted.batch.record_hash != "0" * 64
        assert all(snapshot.record_hash != "0" * 64 for snapshot in persisted.snapshots)
        assert clob_batch_record_is_valid(persisted.batch, persisted.snapshots)

    async with db_sessions() as session:
        batch = await session.get(ClobBookBatch, batch_id)
        assert batch is not None
        snapshots = await load_clob_batch_snapshots(session, batch_id)
        assert len(snapshots) == len(targets)
        assert clob_batch_record_is_valid(batch, snapshots)
        assert all(snapshot.eligible for snapshot in snapshots)
        latest = await latest_eligible_clob_snapshots(
            session, [target.market_id for target in targets]
        )
        same_batch = await latest_eligible_same_batch(
            session, [target.market_id for target in event_targets]
        )
        assert {snapshot.id for snapshot in latest} == {snapshot.id for snapshot in snapshots}
        assert {snapshot.market_id for snapshot in same_batch} == {
            target.market_id for target in event_targets
        }

        selected = next(
            snapshot
            for snapshot in snapshots
            if snapshot.market_id == targets[0].market_id
        )
        attested_batch, attested_snapshot, raw_book = await load_attested_token_book(
            session,
            snapshot_id=selected.id,
            token_id=targets[0].yes_token_id,
        )
        assert attested_batch.id == batch_id
        assert attested_snapshot.id == selected.id
        assert raw_book["asset_id"] == targets[0].yes_token_id
        assert raw_book["asks"] == [{"price": "0.41", "size": "23.456789"}]
        first_after = await first_eligible_clob_snapshot_at_or_after(
            session,
            market_id=targets[0].market_id,
            not_before=batch.request_started_at - timedelta(microseconds=1),
        )
        assert first_after is not None and first_after.id == selected.id
        assert (
            await first_eligible_clob_snapshot_at_or_after(
                session,
                market_id=targets[0].market_id,
                not_before=first_seen_at + timedelta(microseconds=1),
            )
            is None
        )
        with pytest.raises(ClobSnapshotIntegrityError, match="is not part"):
            await load_attested_token_book(
                session,
                snapshot_id=selected.id,
                token_id="unrelated-token",
            )

        market_prices = list(
            (await session.scalars(select(MarketPrice).order_by(MarketPrice.market_id))).all()
        )
        assert len(market_prices) == len(targets)
        assert all(price.ts == first_seen_at for price in market_prices)
        assert all(price.midpoint == Decimal("0.400000") for price in market_prices)
        assert all(price.best_bid == Decimal("0.390000") for price in market_prices)
        assert all(price.bid_depth_usd == Decimal("4.8148") for price in market_prices)
        for target in targets:
            assert (
                await session.scalar(
                    select(Market.last_price_fetch_at).where(Market.id == target.market_id)
                )
                == first_seen_at
            )

        payload_hash_rows = (
            await session.execute(
                text(
                    "SELECT value->>'asset_id', "
                    "encode(digest(convert_to(value::text, 'UTF8'), 'sha256'), 'hex') "
                    "FROM jsonb_array_elements("
                    "convert_from(:body, 'UTF8')::jsonb) AS item(value)"
                ),
                {"body": batch.source_response_body},
            )
        ).all()
        payload_hashes: dict[str, str] = {str(row[0]): str(row[1]) for row in payload_hash_rows}
        for snapshot in snapshots:
            assert snapshot.yes_payload_sha256 == payload_hashes[snapshot.yes_token_id]
            assert snapshot.no_payload_sha256 == payload_hashes[snapshot.no_token_id]


async def test_due_selection_expands_one_due_market_to_whole_event(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions)
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        second = await session.get(Market, targets[1].market_id)
        assert second is not None
        second.last_price_fetch_at = now

    async with db_sessions() as session:
        due = await markets_due_for_price_fetch(session, now)

    assert [target.market_id for target in due] == [
        targets[0].market_id,
        targets[1].market_id,
    ]
    assert {token for target in due for token in (target.yes_token_id, target.no_token_id)} == {
        targets[0].yes_token_id,
        targets[0].no_token_id,
        targets[1].yes_token_id,
        targets[1].no_token_id,
    }


async def test_due_selection_excludes_active_expired_event(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions)
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        await session.execute(
            update(Market)
            .where(Market.id.in_([target.market_id for target in targets]))
            .values(resolves_at=now - timedelta(microseconds=1))
        )

    async with db_sessions() as session:
        due = await markets_due_for_price_fetch(session, now)

    assert due == []


async def test_due_selection_excludes_expired_sibling_but_keeps_complete_future_cohort(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions, count=3)
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        expired = await session.get(Market, targets[0].market_id)
        due_future = await session.get(Market, targets[1].market_id)
        fresh_future = await session.get(Market, targets[2].market_id)
        assert expired is not None
        assert due_future is not None
        assert fresh_future is not None
        expired.resolves_at = now - timedelta(microseconds=1)
        due_future.resolves_at = now + timedelta(hours=2)
        fresh_future.resolves_at = now + timedelta(hours=2)
        fresh_future.last_price_fetch_at = now

    async with db_sessions() as session:
        due = await markets_due_for_price_fetch(session, now)

    assert [target.market_id for target in due] == [
        targets[1].market_id,
        targets[2].market_id,
    ]


async def test_due_selection_excludes_market_resolving_exactly_now(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions, count=1)
    now = datetime.now(UTC)
    async with db_sessions.begin() as session:
        market = await session.get(Market, targets[0].market_id)
        assert market is not None
        market.resolves_at = now

    async with db_sessions() as session:
        due = await markets_due_for_price_fetch(session, now)

    assert due == []


async def test_one_sided_pair_is_committed_but_not_operationally_projected(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions, count=1)
    collected = _collected_batch(
        targets,
        one_sided_market_id=targets[0].market_id,
    )
    async with db_sessions.begin() as session:
        persisted = await persist_clob_batch(session, collected, targets)
        assert (
            await project_eligible_clob_prices(
                session,
                persisted.batch,
                persisted.snapshots,
            )
            == 0
        )
        snapshot = persisted.snapshots[0]
        assert snapshot.complete is False
        assert snapshot.eligible is False
        assert snapshot.yes_best_bid is None
        assert snapshot.yes_bid_depth_usd is None
        batch_id = persisted.batch.id

    async with db_sessions() as session:
        (stored,) = await load_clob_batch_snapshots(session, batch_id)
        assert stored.eligible is False
        assert await latest_eligible_clob_snapshots(session, [targets[0].market_id]) == []
        assert (
            await session.scalar(
                select(MarketPrice).where(MarketPrice.market_id == targets[0].market_id)
            )
            is None
        )


async def test_rejected_strict_batch_does_not_mark_market_fetched(
    db_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    targets = await _seed_markets(db_sessions, count=1)
    settings = _capacity_settings(tmp_path)
    assessment = await audit_storage_capacity(settings, db_sessions)
    assert assessment.severity != "blocking"

    async def reject_batch(_: ClobClient, token_ids: list[str]) -> ClobBooksBatch:
        assert set(token_ids) == {targets[0].yes_token_id, targets[0].no_token_id}
        raise ClobError("CLOB token universe mismatch: missing=['no'] extra=[]")

    monkeypatch.setattr(ClobClient, "fetch_book_batch", reject_batch)
    written, skipped = await fetch_prices(settings, db_sessions)

    assert (written, skipped) == (0, 1)
    async with db_sessions() as session:
        market = await session.get(Market, targets[0].market_id)
        assert market is not None
        assert market.last_price_fetch_at is None
        assert await session.scalar(select(func.count()).select_from(ClobBookBatch)) == 0


async def test_missing_capacity_sample_blocks_before_http_and_engages_kill_switch(
    db_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    targets = await _seed_markets(db_sessions, count=1)
    calls = 0

    async def unexpected_fetch(_: ClobClient, token_ids: list[str]) -> ClobBooksBatch:
        del token_ids
        nonlocal calls
        calls += 1
        raise AssertionError("capacity preflight must run before CLOB HTTP")

    monkeypatch.setattr(ClobClient, "fetch_book_batch", unexpected_fetch)
    written, skipped = await fetch_prices(_capacity_settings(tmp_path), db_sessions)

    assert (written, skipped) == (0, 1)
    assert calls == 0
    async with db_sessions.begin() as session:
        market = await session.get(Market, targets[0].market_id)
        control = await session.get(RuntimeControl, 1)
        assert market is not None and market.last_price_fetch_at is None
        assert control is not None and control.kill_switch is True
        assert await session.scalar(select(func.count()).select_from(ClobBookBatch)) == 0
        control.kill_switch = False


async def test_stale_capacity_sample_blocks_even_when_no_market_is_due(
    db_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _capacity_settings(tmp_path)
    assessment = await audit_storage_capacity(settings, db_sessions)
    assert assessment.severity != "blocking"

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.now(UTC) + timedelta(hours=2)
            return value if tz is not None else value.replace(tzinfo=None)

    async def unexpected_fetch(_: ClobClient, token_ids: list[str]) -> ClobBooksBatch:
        del token_ids
        raise AssertionError("stale capacity must block before CLOB HTTP")

    monkeypatch.setattr("stratus.jobs.fetch_prices.datetime", FutureDateTime)
    monkeypatch.setattr(ClobClient, "fetch_book_batch", unexpected_fetch)

    assert await fetch_prices(settings, db_sessions) == (0, 0)
    async with db_sessions.begin() as session:
        control = await session.get(RuntimeControl, 1)
        assert control is not None and control.kill_switch is True
        control.kill_switch = False


async def test_atomic_post_fetch_capacity_guard_rolls_back_without_cursor_advance(
    db_sessions: SessionFactory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    targets = await _seed_markets(db_sessions, count=1)
    targets += await _seed_markets(db_sessions, count=1)
    base_settings = _capacity_settings(tmp_path)
    assessment = await audit_storage_capacity(base_settings, db_sessions)
    assert assessment.severity != "blocking"
    fetch_calls = 0

    async def collected_batch(_: ClobClient, token_ids: list[str]) -> ClobBooksBatch:
        nonlocal fetch_calls
        fetch_calls += 1
        selected = next(
            target
            for target in targets
            if set(token_ids) == {target.yes_token_id, target.no_token_id}
        )
        return _collected_batch([selected])

    monkeypatch.setattr(ClobClient, "fetch_book_batch", collected_batch)
    settings = base_settings.model_copy(
        update={
            # The persisted hourly sample passes preflight, while the live
            # measurement plus the reserved atomic write cannot fit.
            "clob_ledger_capacity_budget_bytes": (
                assessment.ledger_bytes + base_settings.clob_ledger_capacity_write_reserve_bytes
            ),
            "clob_batch_size": 2,
        }
    )

    written, skipped = await fetch_prices(settings, db_sessions)

    assert (written, skipped) == (0, 2)
    assert fetch_calls == 1
    async with db_sessions.begin() as session:
        market = await session.get(Market, targets[0].market_id)
        control = await session.get(RuntimeControl, 1)
        assert market is not None and market.last_price_fetch_at is None
        assert control is not None and control.kill_switch is True
        assert await session.scalar(select(func.count()).select_from(ClobBookBatch)) == 0
        assert await session.scalar(select(func.count()).select_from(ClobMarketSnapshot)) == 0
        assert await session.scalar(select(func.count()).select_from(MarketPrice)) == 0
        control.kill_switch = False


async def test_deferred_constraint_rejects_partial_batch_projection(
    db_sessions: SessionFactory,
) -> None:
    targets = await _seed_markets(db_sessions, count=2)
    collected = _collected_batch(targets)
    batch_value = build_clob_book_batch_value(collected)
    snapshot_values = build_clob_market_snapshot_values(collected, targets)

    with pytest.raises(DBAPIError, match="complete committed snapshot projection"):
        async with db_sessions.begin() as session:
            batch = ClobBookBatch(**asdict(batch_value), record_hash="0" * 64)
            session.add(batch)
            await session.flush()
            session.add(
                ClobMarketSnapshot(
                    batch_id=batch.id,
                    **asdict(snapshot_values[0]),
                    record_hash="0" * 64,
                )
            )

    async with db_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(ClobBookBatch)) == 0
