from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketStatus,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    SelectionPolicyDecision,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    StationStage,
    TradeSide,
    WeatherKind,
)
from stratus.repositories.clob_snapshots import persist_clob_batch
from stratus.repositories.market_prices import PriceTarget
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs
from stratus.repositories.validation_ledger import enroll_terminal_order
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True, slots=True)
class DualSeed:
    market_id: int
    model_prob_id: int
    signal_id: int
    snapshot_id: int
    token_id: str
    decision_at: datetime
    model_value: ModelProbabilityValue


def _raw_book(
    target: PriceTarget,
    *,
    yes: bool,
    source_timestamp: datetime,
) -> dict[str, Any]:
    return {
        "market": target.condition_id,
        "asset_id": target.yes_token_id if yes else target.no_token_id,
        "timestamp": str(int(source_timestamp.timestamp() * 1000)),
        "hash": f"opaque-immutability-{'yes' if yes else 'no'}",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [{"price": "0.39" if yes else "0.59", "size": "25"}],
        "asks": [{"price": "0.41" if yes else "0.61", "size": "25"}],
    }


def _collected_batch(target: PriceTarget) -> ClobBooksBatch:
    received_at = datetime.now(UTC) - timedelta(milliseconds=100)
    source_timestamp = received_at - timedelta(milliseconds=100)
    books = [
        _raw_book(target, yes=True, source_timestamp=source_timestamp),
        _raw_book(target, yes=False, source_timestamp=source_timestamp),
    ]
    raw = json.dumps(books, separators=(",", ":")).encode()
    requested = tuple(sorted((target.yes_token_id, target.no_token_id)))
    by_token = {str(book["asset_id"]): book for book in books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=received_at - timedelta(milliseconds=200),
        response_received_at=received_at,
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(by_token[token]) for token in requested),
    )


async def _seed_dual_trade(db_sessions: SessionFactory) -> DualSeed:
    suffix = uuid4().hex
    condition_id = f"0x{suffix}"
    yes_token_id = f"yes-{suffix}"
    no_token_id = f"no-{suffix}"
    async with db_sessions.begin() as session:
        station = Station(
            icao=f"I{suffix[:3].upper()}",
            name="Dual immutability test station",
            city="Test City",
            lat=Decimal("50.000000"),
            lon=Decimal("8.000000"),
            tz="Europe/Berlin",
            active=True,
            stage=StationStage.PAPER,
        )
        session.add(station)
        await session.flush()
        market = Market(
            polymarket_id=f"immutable-{suffix}",
            event_id=f"immutable-event-{suffix}",
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            station_id=station.id,
            market_date=datetime.now(UTC).date(),
            kind=WeatherKind.HIGH,
            bucket_low_c=None,
            bucket_high_c=Decimal("20"),
            rules_raw="immutable dual trade test rules",
            station_verified=True,
            status=MarketStatus.ACTIVE,
            resolves_at=datetime.now(UTC) + timedelta(hours=2),
        )
        session.add(market)
        await session.flush()
        target = PriceTarget(
            market_id=market.id,
            event_id=market.event_id,
            condition_id=condition_id,
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
        )

    async with db_sessions.begin() as session:
        persisted = await persist_clob_batch(session, _collected_batch(target), [target])
        snapshot = persisted.snapshots[0]
        snapshot_id = snapshot.id
        decision_at = persisted.batch.first_seen_at + timedelta(seconds=1)

    model_value = ModelProbabilityValue(
        market_id=target.market_id,
        ts=decision_at,
        prob=Decimal("0.700000"),
        sigma=Decimal("1.5000"),
        horizon_h=Decimal("2.00"),
        components={"pipeline": {"version": "dual-immutability-test-v1"}},
        market_snapshot_id=snapshot_id,
        market_anchor_protocol=CLOB_DUAL_PROTOCOL,
    )
    async with db_sessions.begin() as session:
        assert await upsert_model_probs(session, [model_value]) == 1
        model_prob = await session.scalar(
            select(ModelProb).where(
                ModelProb.market_id == target.market_id,
                ModelProb.ts == decision_at,
            )
        )
        assert model_prob is not None
        signal = Signal(
            market_id=target.market_id,
            ts=decision_at + timedelta(seconds=1),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.700000"),
            market_price=Decimal("0.410000"),
            edge=Decimal("0.290000"),
            kelly_frac=Decimal("0.050000"),
            size_usd=Decimal("10.0000"),
            confidence=SignalConfidence.A,
            status=SignalStatus.PENDING,
            skip_reason=None,
            clob_snapshot_id=snapshot_id,
            token_id=yes_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )
        session.add(signal)
        await session.flush()
        return DualSeed(
            market_id=target.market_id,
            model_prob_id=model_prob.id,
            signal_id=signal.id,
            snapshot_id=snapshot_id,
            token_id=yes_token_id,
            decision_at=decision_at,
            model_value=model_value,
        )


async def test_dual_model_prob_allows_idempotent_upsert_but_rejects_rewrite_delete(
    db_sessions: SessionFactory,
) -> None:
    seed = await _seed_dual_trade(db_sessions)

    async with db_sessions.begin() as session:
        assert await upsert_model_probs(session, [seed.model_value]) == 1

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="model probability is immutable"):
            await session.execute(
                update(ModelProb)
                .where(ModelProb.id == seed.model_prob_id)
                .values(prob=Decimal("0.710000"))
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="model probability cannot be deleted"):
            await session.execute(delete(ModelProb).where(ModelProb.id == seed.model_prob_id))
        await session.rollback()


async def test_dual_signal_allows_status_retention_but_freezes_decision(
    db_sessions: SessionFactory,
) -> None:
    seed = await _seed_dual_trade(db_sessions)

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="not retention-eligible"):
            await session.execute(delete(Signal).where(Signal.id == seed.signal_id))
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="decision/provenance is immutable"):
            await session.execute(
                update(Signal)
                .where(Signal.id == seed.signal_id)
                .values(edge=Decimal("0.300000"))
            )
        await session.rollback()

    async with db_sessions.begin() as session:
        await session.execute(
            update(Signal)
            .where(Signal.id == seed.signal_id)
            .values(status=SignalStatus.SKIPPED, skip_reason="retention-test")
        )
    async with db_sessions.begin() as session:
        await session.execute(delete(Signal).where(Signal.id == seed.signal_id))
    async with db_sessions() as session:
        assert await session.get(Signal, seed.signal_id) is None


async def test_dual_order_position_and_policy_keep_normal_lifecycle_then_seal(
    db_sessions: SessionFactory,
) -> None:
    seed = await _seed_dual_trade(db_sessions)
    async with db_sessions.begin() as session:
        order = Order(
            signal_id=seed.signal_id,
            mode=ExecutionMode.LIVE,
            clob_order_id="exchange-order-1",
            token_id=seed.token_id,
            side=TradeSide.BUY_YES,
            price=Decimal("0.410000"),
            size_usd=Decimal("10.0000"),
            filled_usd=Decimal("0"),
            fee_usd=None,
            status="open",
            filled_at=None,
        )
        session.add(order)
        await session.flush()
        position = Position(
            market_id=seed.market_id,
            order_id=order.id,
            mode=ExecutionMode.LIVE,
            side=TradeSide.BUY_YES,
            avg_price=Decimal("0.410000"),
            size_shares=Decimal("10.000000"),
            size_usd=Decimal("4.1000"),
            fee_usd=None,
            status=PositionStatus.OPEN,
            pnl_usd=None,
            clv=None,
            closed_at=None,
            market_closed_at=None,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
            token_id=seed.token_id,
            clv_snapshot_id=None,
            clv_snapshot_hash=None,
        )
        session.add(position)
        partial_order = Order(
            signal_id=seed.signal_id,
            mode=ExecutionMode.PAPER,
            clob_order_id=f"paper-{seed.signal_id}",
            token_id=seed.token_id,
            side=TradeSide.BUY_YES,
            price=Decimal("0.410000"),
            size_usd=Decimal("10.0000"),
            filled_usd=Decimal("2.0000"),
            fee_usd=Decimal("0.00500"),
            status="partially_filled",
            filled_at=datetime.now(UTC),
        )
        session.add(partial_order)
        await session.flush()
        assert await enroll_terminal_order(session, partial_order.id) is not None
        order_id = order.id
        position_id = position.id
        partial_order_id = partial_order.id

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="order identity/decision is immutable"):
            await session.execute(
                update(Order)
                .where(Order.id == order_id)
                .values(price=Decimal("0.420000"))
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="terminal dual CLOB order outcome is immutable"):
            await session.execute(
                update(Order)
                .where(Order.id == partial_order_id)
                .values(status="filled")
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="position identity/provenance is immutable"):
            await session.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(order_id=None)
            )
        await session.rollback()

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="position cannot be deleted"):
            await session.execute(delete(Position).where(Position.id == position_id))
        await session.rollback()

    # Cumulative live polling may update an open position.
    async with db_sessions.begin() as session:
        await session.execute(
            update(Position)
            .where(Position.id == position_id)
            .values(
                avg_price=Decimal("0.420000"),
                size_shares=Decimal("12.000000"),
                size_usd=Decimal("5.0400"),
                fee_usd=Decimal("0.01000"),
            )
        )
    filled_at = datetime.now(UTC)
    # Resting/open -> terminal fill remains a legal atomic transition.
    async with db_sessions.begin() as session:
        await session.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(
                status="filled",
                filled_usd=Decimal("5.0400"),
                fee_usd=Decimal("0.01000"),
                filled_at=filled_at,
            )
        )
        await session.execute(
            update(Signal)
            .where(Signal.id == seed.signal_id)
            .values(status=SignalStatus.EXECUTED)
        )
        assert await enroll_terminal_order(session, order_id) is not None
        session.add(
            SelectionPolicyDecision(
                order_id=order_id,
                policy_name="dual-immutability-test",
                policy_hash="0123456789abcdef",
                max_predicted_edge=Decimal("0.300000"),
                predicted_edge=Decimal("0.290000"),
                accepted=True,
                pipeline_version="dual-immutability-test-v1",
                signal_ts=seed.decision_at + timedelta(seconds=1),
                filled_at=filled_at,
                decided_at=filled_at,
            )
        )

    # Settlement may close an open position once.
    async with db_sessions.begin() as session:
        await session.execute(
            update(Position)
            .where(Position.id == position_id)
            .values(
                status=PositionStatus.CLOSED,
                pnl_usd=Decimal("6.9500"),
                closed_at=filled_at + timedelta(hours=1),
                market_closed_at=filled_at + timedelta(minutes=50),
            )
        )

    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="terminal dual CLOB order outcome is immutable"):
            await session.execute(
                update(Order)
                .where(Order.id == order_id)
                .values(filled_usd=Decimal("5.1400"))
            )
        await session.rollback()
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="dual CLOB order cannot be deleted"):
            await session.execute(delete(Order).where(Order.id == order_id))
        await session.rollback()
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="closed dual CLOB position is immutable"):
            await session.execute(
                update(Position)
                .where(Position.id == position_id)
                .values(pnl_usd=Decimal("7.9500"))
            )
        await session.rollback()
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="signal with an order cannot be deleted"):
            await session.execute(delete(Signal).where(Signal.id == seed.signal_id))
        await session.rollback()

    async with db_sessions() as session:
        raw_policy_id = await session.scalar(
            select(SelectionPolicyDecision.id).where(
                SelectionPolicyDecision.order_id == order_id
            )
        )
        assert raw_policy_id is not None
        policy_id = raw_policy_id
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="selection-policy decision is immutable"):
            await session.execute(
                update(SelectionPolicyDecision)
                .where(SelectionPolicyDecision.id == policy_id)
                .values(accepted=False)
            )
        await session.rollback()
    async with db_sessions() as session:
        with pytest.raises(DBAPIError, match="selection-policy decision cannot be deleted"):
            await session.execute(
                delete(SelectionPolicyDecision).where(
                    SelectionPolicyDecision.id == policy_id
                )
            )
        await session.rollback()
