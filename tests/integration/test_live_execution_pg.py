"""Live executor placement machinery against a real Postgres, with a fake
trader (no wallet, no network): the crash-safety and idempotency guarantees
that the mock-free unit tests cannot express."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import stratus.jobs.execute_signals as execute_signals_module
from stratus.collectors.clob import ClobBooksBatch, parse_strict_book_snapshot
from stratus.collectors.clob_trader import (
    ClobPlacementOutcomeUnknown,
    ClobVenueRejected,
    PlacedOrder,
)
from stratus.collectors.geoblock import GeoblockStatus
from stratus.config import Settings
from stratus.db.models import (
    BankrollLog,
    ExecutionCostObligation,
    ExecutionCostSeal,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
    ExecutionReconciliationObservation,
    Market,
    MarketStatus,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    ProspectiveValidationEnrollment,
    RuntimeControl,
    Signal,
    SignalConfidence,
    SignalStatus,
    Station,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    TradeSide,
    WeatherKind,
)
from stratus.jobs.audit_storage_capacity import audit_storage_capacity
from stratus.jobs.execute_live_signals import (
    cancel_live_orders,
    execute_live_signals,
    poll_live_fills,
    preflight_live,
)
from stratus.jobs.execute_signals import execute_paper_signals
from stratus.jobs.generate_signals import generate_signals
from stratus.jobs.quarantine_execution_state import (
    ExecutionStateQuarantineError,
    quarantine_execution_state,
)
from stratus.jobs.recompute_probs import _materialize_event_probability_coherence
from stratus.repositories.accuracy_promotions import build_effective_promotion_manifest
from stratus.repositories.clob_snapshots import persist_clob_batch
from stratus.repositories.evidence_continuity import probability_pipeline_release_cohort
from stratus.repositories.execution import (
    LiveOrderClaim,
    PaperExecutionValue,
    claim_live_order,
    persist_paper_executions,
)
from stratus.repositories.market_prices import PriceTarget
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs
from stratus.repositories.signals import expire_unclaimed_pending_signals_for_events
from stratus.repositories.station_reliability import (
    StationReliabilitySnapshotValue,
    insert_station_reliability_snapshot,
)
from stratus.services.clob_evidence import (
    CLOB_DUAL_PROTOCOL,
    LEGACY_MARKET_PROTOCOL,
)
from stratus.services.pipeline_identity import probability_pipeline_fingerprint
from stratus.services.station_reliability import station_reliability_policy_hash

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("STRATUS_TEST_DATABASE_URL"),
        reason="STRATUS_TEST_DATABASE_URL is not set",
    ),
    pytest.mark.usefixtures("activated_probability_release_epoch"),
]

SessionFactory = async_sessionmaker[AsyncSession]
ReleaseActivator = Callable[[Settings], Awaitable[object]]
NOW = datetime.now(UTC)
NOT_BLOCKED = GeoblockStatus(blocked=False, country="AT", detail="ok")
_TEST_SETTINGS = Settings(_env_file=None)
TEST_IMAGE_DIGEST = "b" * 64
TEST_PIPELINE_COHORT = probability_pipeline_release_cohort(
    pipeline_version=_TEST_SETTINGS.probability_pipeline_version,
    material_fingerprint=probability_pipeline_fingerprint(_TEST_SETTINGS),
    activation_manifest_hash=build_effective_promotion_manifest(()).manifest_hash,
)


def _settings(url: str) -> Settings:
    return Settings(
        _env_file=None,
        database_url=url,
        release_image_digest=TEST_IMAGE_DIGEST,
        live_legal_approved=True,
        live_tax_approved=True,
        polygon_private_key="0xtestkey",
        live_max_order_usd=25.0,
        clob_ledger_capacity_path=str(Path.cwd()),
        clob_ledger_capacity_min_free_bytes=500_000_000,
    )


@pytest.fixture(autouse=True)
async def _fresh_capacity_sample(db_sessions: SessionFactory) -> None:
    assessment = await audit_storage_capacity(
        Settings(
            clob_ledger_capacity_path=str(Path.cwd()),
            clob_ledger_capacity_min_free_bytes=500_000_000,
        ),
        db_sessions,
    )
    assert assessment.severity != "blocking"


class FakeTrader:
    def __init__(
        self,
        *,
        result: PlacedOrder | None = None,
        place_error: Exception | None = None,
        order_payloads: dict[str, dict[str, object]] | None = None,
        trade_payloads: dict[str, list[dict[str, object]]] | None = None,
        balance: Decimal = Decimal("1000"),
    ) -> None:
        self.result = result or PlacedOrder(order_id="0xdead", status="live")
        self.place_error = place_error
        self.order_payloads = order_payloads or {}
        self.trade_payloads = trade_payloads or {}
        self.balance = balance
        self.placed: list[dict[str, object]] = []
        self.canceled: list[str] = []

    async def tick_size(self, token_id: str) -> Decimal:
        return Decimal("0.01")

    async def neg_risk(self, token_id: str) -> bool:
        return False

    async def place_gtc_buy(self, **kwargs: object) -> PlacedOrder:
        if self.place_error is not None:
            raise self.place_error
        self.placed.append(kwargs)
        return self.result

    async def cancel(self, order_ids: list[str]) -> None:
        self.canceled.extend(order_ids)

    async def order(self, order_id: str) -> dict[str, object]:
        return self.order_payloads[order_id]

    async def trades(self, asset_id: str) -> list[dict[str, object]]:
        return self.trade_payloads.get(asset_id, [])

    async def collateral_balance(self) -> Decimal:
        return self.balance


async def _set_runtime(
    sessions: SessionFactory, *, mode: ExecutionMode, kill: bool = False
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(RuntimeControl)
            .where(RuntimeControl.id == 1)
            .values(mode=mode, kill_switch=kill, halted_reason=None)
        )


def _raw_book(
    target: PriceTarget,
    *,
    yes: bool,
    source_timestamp: datetime,
    yes_ask: str = "0.52",
    yes_ask_size: str = "100",
    no_ask_size: str = "100",
) -> dict[str, Any]:
    return {
        "market": target.condition_id,
        "asset_id": target.yes_token_id if yes else target.no_token_id,
        "timestamp": str(int(source_timestamp.timestamp() * 1000)),
        "hash": f"opaque-live-{'yes' if yes else 'no'}",
        "tick_size": "0.01",
        "neg_risk": False,
        "bids": [{"price": "0.50" if yes else "0.48", "size": "100"}],
        "asks": [
            {
                "price": yes_ask if yes else "0.50",
                "size": yes_ask_size if yes else no_ask_size,
            }
        ],
    }


def _dual_batch(
    target: PriceTarget,
    *,
    yes_ask: str = "0.52",
    yes_ask_size: str = "100",
    causal_now: bool = False,
) -> ClobBooksBatch:
    if causal_now:
        request_started_at = datetime.now(UTC)
        response_received_at = datetime.now(UTC)
        source_timestamp = request_started_at
    else:
        response_received_at = datetime.now(UTC) - timedelta(milliseconds=100)
        request_started_at = response_received_at - timedelta(milliseconds=200)
        source_timestamp = response_received_at - timedelta(milliseconds=100)
    books = [
        _raw_book(
            target,
            yes=True,
            source_timestamp=source_timestamp,
            yes_ask=yes_ask,
            yes_ask_size=yes_ask_size,
        ),
        _raw_book(target, yes=False, source_timestamp=source_timestamp),
    ]
    response_body = json.dumps(books, separators=(",", ":")).encode()
    requested = tuple(sorted((target.yes_token_id, target.no_token_id)))
    books_by_token = {str(book["asset_id"]): book for book in books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        response_body=response_body,
        response_sha256=hashlib.sha256(response_body).hexdigest(),
        response_bytes=len(response_body),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(books_by_token[token]) for token in requested),
    )


def _event_batch(targets: list[PriceTarget]) -> ClobBooksBatch:
    response_received_at = datetime.now(UTC) - timedelta(milliseconds=100)
    request_started_at = response_received_at - timedelta(milliseconds=200)
    source_timestamp = response_received_at - timedelta(milliseconds=100)
    books = [
        _raw_book(
            target,
            yes=yes,
            source_timestamp=source_timestamp,
            yes_ask_size="1000",
            no_ask_size="1000",
        )
        for target in targets
        for yes in (True, False)
    ]
    response_body = json.dumps(books, separators=(",", ":")).encode()
    requested = tuple(sorted(str(book["asset_id"]) for book in books))
    books_by_token = {str(book["asset_id"]): book for book in books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        response_body=response_body,
        response_sha256=hashlib.sha256(response_body).hexdigest(),
        response_bytes=len(response_body),
        response_headers=(),
        snapshots=tuple(parse_strict_book_snapshot(books_by_token[token]) for token in requested),
    )


def _candidate_only_eligible_event_batch(
    targets: list[PriceTarget],
    *,
    candidate_market_id: int,
) -> ClobBooksBatch:
    """Keep one exact dual book eligible while retaining raw sibling books."""

    response_received_at = datetime.now(UTC) - timedelta(milliseconds=100)
    request_started_at = response_received_at - timedelta(milliseconds=200)
    source_timestamp = response_received_at - timedelta(milliseconds=100)
    books = [
        _raw_book(
            target,
            yes=yes,
            source_timestamp=source_timestamp,
            yes_ask=("0.52" if target.market_id == candidate_market_id else "0.80"),
            yes_ask_size="1000",
            no_ask_size="1000",
        )
        for target in targets
        for yes in (True, False)
    ]
    response_body = json.dumps(books, separators=(",", ":")).encode()
    requested = tuple(sorted(str(book["asset_id"]) for book in books))
    books_by_token = {str(book["asset_id"]): book for book in books}
    return ClobBooksBatch(
        source_url="https://clob.polymarket.com/books",
        requested_token_ids=requested,
        request_started_at=request_started_at,
        response_received_at=response_received_at,
        response_body=response_body,
        response_sha256=hashlib.sha256(response_body).hexdigest(),
        response_bytes=len(response_body),
        response_headers=(),
        snapshots=tuple(
            parse_strict_book_snapshot(books_by_token[token]) for token in requested
        ),
    )


async def _seed_market(sessions: SessionFactory, *, polymarket_id: str) -> PriceTarget:
    async with sessions.begin() as session:
        station = await session.scalar(
            select(Station).where(Station.icao == "KLGA")
        )
        assert station is not None
        market = Market(
            polymarket_id=polymarket_id,
            event_id="ev-1",
            condition_id="0xcondition-live-1",
            yes_token_id="yes-token",
            no_token_id="no-token",
            station_id=station.id,
            market_date=NOW.date(),
            kind=WeatherKind.HIGH,
            rules_raw="rules",
            resolves_at=NOW + timedelta(hours=6),
            status=MarketStatus.ACTIVE,
            station_verified=True,
        )
        session.add(market)
        await session.flush()
        return PriceTarget(
            market_id=market.id,
            event_id=market.event_id,
            condition_id=market.condition_id,
            yes_token_id=market.yes_token_id,
            no_token_id=market.no_token_id,
        )


async def _pending_signal(
    sessions: SessionFactory,
    *,
    model_probability: Decimal = Decimal("0.600000"),
    model_components: dict[str, Any] | None = None,
    include_reliability_provenance: bool = True,
    size_usd: Decimal = Decimal("20.0000"),
) -> int:
    target = await _seed_market(sessions, polymarket_id="pm-live-1")
    async with sessions.begin() as session:
        persisted = await persist_clob_batch(
            session,
            _dual_batch(target),
            [target],
        )
        snapshot_id = persisted.snapshots[0].id
        signal_ts = persisted.batch.first_seen_at + timedelta(microseconds=1)

    async with sessions.begin() as session:
        market = await session.get(Market, target.market_id)
        assert market is not None and market.station_id is not None
        station = await session.get(Station, market.station_id)
        assert station is not None
        reliability_snapshot: StationReliabilitySnapshot | None = None
        if include_reliability_provenance:
            reliability_snapshot = await _append_station_reliability_snapshot(
                session,
                station=station,
                evaluated_at=signal_ts,
            )
        session.add(
            ModelProb(
                market_id=target.market_id,
                ts=signal_ts,
                prob=model_probability,
                sigma=Decimal("1.0000"),
                horizon_h=Decimal("6.00"),
                components=model_components
                or {"pipeline": {"cohort": TEST_PIPELINE_COHORT}},
                market_snapshot_id=snapshot_id,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
        )
        signal = Signal(
            market_id=target.market_id,
            ts=signal_ts,
            side=TradeSide.BUY_YES,
            model_prob=model_probability,
            market_price=Decimal("0.520000"),
            edge=Decimal("0.080000"),
            kelly_frac=Decimal("0.010000") if size_usd > 0 else Decimal("0.000000"),
            size_usd=size_usd,
            confidence=SignalConfidence.B,
            status=SignalStatus.PENDING,
            clob_snapshot_id=snapshot_id,
            token_id=target.yes_token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
            station_reliability_snapshot_id=(
                reliability_snapshot.id if reliability_snapshot is not None else None
            ),
        )
        session.add(signal)
        await session.flush()
        return signal.id


async def _append_station_reliability_snapshot(
    session: AsyncSession,
    *,
    station: Station,
    evaluated_at: datetime,
    conflict: bool = False,
) -> StationReliabilitySnapshot:
    local_date = evaluated_at.astimezone(ZoneInfo(station.tz)).date()
    conflict_at = evaluated_at if conflict else None
    return await insert_station_reliability_snapshot(
        session,
        StationReliabilitySnapshotValue(
            station_id=station.id,
            evaluated_at=evaluated_at,
            window_days=180,
            comparable_units=20,
            match_units=19 if conflict else 20,
            conflict_units=1 if conflict else 0,
            exact_source_mismatch_units=1 if conflict else 0,
            clear_mismatch_units=0,
            exact_truth_gap_units=0,
            boundary_units=0,
            unavailable_units=0,
            reliability_score=Decimal("0.950000") if conflict else Decimal("1.000000"),
            reliability_lower_bound=(
                Decimal("0.763869") if conflict else Decimal("0.838875")
            ),
            status=(
                StationReliabilityStatus.BLOCKED
                if conflict
                else StationReliabilityStatus.RELIABLE
            ),
            latest_conflict_at=conflict_at,
            latest_conflict_date=local_date - timedelta(days=1) if conflict else None,
            latest_conflict_kind=WeatherKind.HIGH if conflict else None,
            resolution_cooldown_until=(
                evaluated_at + timedelta(hours=48) if conflict else None
            ),
            latest_observation_at=evaluated_at,
            observation_local_date=local_date,
            observation_cooldown_until=None,
            reasons=("station_resolution_conflict",) if conflict else (),
            policy_hash=station_reliability_policy_hash(_TEST_SETTINGS),
            source_hash=("f" if conflict else "e") * 64,
        ),
    )


async def _refresh_current_reliability_for_signal(
    sessions: SessionFactory,
    *,
    signal_id: int,
    evaluated_at: datetime,
) -> None:
    """Keep current evidence valid while tests advance the executor clock."""

    async with sessions.begin() as session:
        signal = await session.get(Signal, signal_id)
        assert signal is not None
        market = await session.get(Market, signal.market_id)
        assert market is not None and market.station_id is not None
        station = await session.get(Station, market.station_id)
        assert station is not None
        await _append_station_reliability_snapshot(
            session,
            station=station,
            evaluated_at=evaluated_at,
        )


async def _append_conflicting_snapshot_for_signal(
    sessions: SessionFactory,
    signal_id: int,
) -> None:
    async with sessions.begin() as session:
        signal = await session.get(Signal, signal_id)
        assert signal is not None and signal.station_reliability_snapshot_id is not None
        market = await session.get(Market, signal.market_id)
        assert market is not None and market.station_id is not None
        station = await session.get(Station, market.station_id)
        attested = await session.get(
            StationReliabilitySnapshot,
            signal.station_reliability_snapshot_id,
        )
        assert station is not None and attested is not None
        evaluated_at = max(
            datetime.now(UTC),
            attested.evaluated_at + timedelta(microseconds=1),
        )
        await _append_station_reliability_snapshot(
            session,
            station=station,
            evaluated_at=evaluated_at,
            conflict=True,
        )


async def test_materialized_model_probability_matches_signal_and_intent(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    activated_probability_release_epoch: ReleaseActivator,
) -> None:
    settings = _settings(migrated_database_url).model_copy(
        update={
            "min_traded_calibration_samples": 10,
            "signal_min_edge": 0.01,
            "signal_max_edge": 1.0,
            "edge_cost_buffer": 0.0,
            "max_spread": 0.10,
        }
    )
    await activated_probability_release_epoch(settings)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    now = datetime.now(UTC)
    event_id = "coherent-event"
    bounds = ((None, "20"), ("20", "21"), ("21", None))
    raw_probabilities = ("0.160462", "0.300000", "0.539550")

    async with db_sessions.begin() as session:
        station = await session.scalar(
            select(Station).where(Station.icao == "KCOH")
        )
        assert station is not None
        markets = [
            Market(
                polymarket_id=f"coherent-{index}",
                event_id=event_id,
                condition_id=f"0xcoherent-{index}",
                yes_token_id=f"coherent-yes-{index}",
                no_token_id=f"coherent-no-{index}",
                station_id=station.id,
                market_date=now.date(),
                kind=WeatherKind.HIGH,
                rules_raw="complete coherent bucket partition",
                resolves_at=now + timedelta(hours=5),
                resolution_source="https://example.test/final-observation",
                bucket_low_c=Decimal(low) if low is not None else None,
                bucket_high_c=Decimal(high) if high is not None else None,
                status=MarketStatus.ACTIVE,
                station_verified=True,
                created_at=now - timedelta(days=1),
            )
            for index, (low, high) in enumerate(bounds, start=1)
        ]
        session.add_all(markets)
        await session.flush()
        targets = [
            PriceTarget(
                market_id=market.id,
                event_id=market.event_id,
                condition_id=market.condition_id or "",
                yes_token_id=market.yes_token_id or "",
                no_token_id=market.no_token_id or "",
            )
            for market in markets
        ]
        persisted = await persist_clob_batch(session, _event_batch(targets), targets)
        snapshots = {snapshot.market_id: snapshot for snapshot in persisted.snapshots}
        calculation_time = persisted.batch.first_seen_at + timedelta(microseconds=1)
        await _append_station_reliability_snapshot(
            session,
            station=station,
            evaluated_at=calculation_time,
        )
        raw_values = [
            ModelProbabilityValue(
                market_id=market.id,
                ts=calculation_time,
                prob=Decimal(raw_probability),
                sigma=Decimal("1.0000"),
                horizon_h=Decimal("5.00"),
                components={
                    "pipeline": {"cohort": TEST_PIPELINE_COHORT},
                    "shrinkage": {"n": 300},
                    "ecmwf_ens": {
                        "probability": float(raw_probability),
                        "sample_n": 50,
                    },
                    "gefs": {
                        "probability": float(raw_probability),
                        "sample_n": 50,
                    },
                },
                market_snapshot_id=snapshots[market.id].id,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
            for market, raw_probability in zip(
                markets, raw_probabilities, strict=True
            )
        ]
        materialization = _materialize_event_probability_coherence(
            raw_values,
            markets,
            eligible_market_ids={market.id for market in markets},
            latest_prices={
                market.id: (snapshots[market.id], persisted.batch.first_seen_at)
                for market in markets
            },
        )
        await upsert_model_probs(session, list(materialization.values))
        selected_market_id = markets[0].id

    normalized = Decimal("0.160460")
    assert materialization.values[0].prob == normalized
    accepted, _skipped = await generate_signals(settings, db_sessions)
    assert accepted >= 1

    async with db_sessions() as session:
        signal = await session.scalar(
            select(Signal).where(Signal.market_id == selected_market_id)
        )
        assert signal is not None
        model = await session.scalar(
            select(ModelProb).where(
                ModelProb.market_id == signal.market_id,
                ModelProb.ts == calculation_time,
            )
        )
    assert model is not None
    assert signal.status is SignalStatus.PENDING
    assert model.prob == signal.model_prob == normalized
    assert model.components["event_coherence"]["final_probability"] == str(normalized)

    # The first pass commits the order and immutable 0038 intent. There is no
    # post-intent book yet, so no paper fill is expected.
    assert (await execute_paper_signals(settings, db_sessions))[0] == 0

    async with db_sessions() as session:
        order = await session.scalar(select(Order).where(Order.signal_id == signal.id))
        assert order is not None
        intent = await session.scalar(
            select(ExecutionOrderIntent).where(ExecutionOrderIntent.order_id == order.id)
        )

    assert intent is not None


async def test_candidate_anchor_v2_uses_current_materialized_siblings_not_old_anchors(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    activated_probability_release_epoch: ReleaseActivator,
) -> None:
    settings = _settings(migrated_database_url).model_copy(
        update={
            "min_traded_calibration_samples": 10,
            "signal_min_edge": 0.01,
            "signal_max_edge": 1.0,
            "edge_cost_buffer": 0.0,
            "max_spread": 0.10,
        }
    )
    await activated_probability_release_epoch(settings)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    now = datetime.now(UTC)
    bounds = ((None, "20"), ("20", "21"), ("21", None))
    probabilities = ("0.800000", "0.100000", "0.100000")

    async with db_sessions.begin() as session:
        station = await session.scalar(select(Station).where(Station.icao == "KCOH"))
        assert station is not None
        markets = [
            Market(
                polymarket_id=f"candidate-anchor-v2-{index}",
                event_id="candidate-anchor-v2-event",
                condition_id=f"0xcandidate-anchor-v2-{index}",
                yes_token_id=f"candidate-anchor-v2-yes-{index}",
                no_token_id=f"candidate-anchor-v2-no-{index}",
                station_id=station.id,
                market_date=now.date(),
                kind=WeatherKind.HIGH,
                rules_raw="candidate anchor v2 complete bucket partition",
                resolves_at=now + timedelta(hours=5),
                resolution_source="https://example.test/final-observation",
                bucket_low_c=Decimal(low) if low is not None else None,
                bucket_high_c=Decimal(high) if high is not None else None,
                status=MarketStatus.ACTIVE,
                station_verified=True,
                created_at=now - timedelta(days=1),
            )
            for index, (low, high) in enumerate(bounds, start=1)
        ]
        session.add_all(markets)
        await session.flush()
        targets = [
            PriceTarget(
                market_id=market.id,
                event_id=market.event_id,
                condition_id=market.condition_id or "",
                yes_token_id=market.yes_token_id or "",
                no_token_id=market.no_token_id or "",
            )
            for market in markets
        ]

        # The older cohort has all three executable anchors. It is retained to
        # prove that selection must not borrow those sibling rows after a newer
        # complete model vector has been published.
        old_batch = await persist_clob_batch(session, _event_batch(targets), targets)
        old_snapshots = {
            snapshot.market_id: snapshot for snapshot in old_batch.snapshots
        }
        old_time = old_batch.batch.first_seen_at + timedelta(microseconds=1)
        old_values = [
            ModelProbabilityValue(
                market_id=market.id,
                ts=old_time,
                prob=Decimal(probability),
                sigma=Decimal("1.0000"),
                horizon_h=Decimal("5.00"),
                components={
                    "pipeline": {"cohort": TEST_PIPELINE_COHORT},
                    "shrinkage": {"n": 300},
                    "ecmwf_ens": {
                        "probability": float(probability),
                        "sample_n": 50,
                    },
                    "gefs": {
                        "probability": float(probability),
                        "sample_n": 50,
                    },
                },
                market_snapshot_id=old_snapshots[market.id].id,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
            for market, probability in zip(markets, probabilities, strict=True)
        ]
        old_materialization = _materialize_event_probability_coherence(
            old_values,
            markets,
            eligible_market_ids={market.id for market in markets},
            latest_prices={
                market.id: (old_snapshots[market.id], old_batch.batch.first_seen_at)
                for market in markets
            },
        )
        assert old_materialization.valid_event_ids == frozenset(
            {"candidate-anchor-v2-event"}
        )
        await upsert_model_probs(session, list(old_materialization.values))

        current_batch = await persist_clob_batch(
            session,
            _candidate_only_eligible_event_batch(
                targets,
                candidate_market_id=markets[0].id,
            ),
            targets,
        )
        current_snapshots = {
            snapshot.market_id: snapshot for snapshot in current_batch.snapshots
        }
        assert current_snapshots[markets[0].id].eligible
        assert all(
            not current_snapshots[market.id].eligible for market in markets[1:]
        )
        current_time = current_batch.batch.first_seen_at + timedelta(microseconds=1)
        await _append_station_reliability_snapshot(
            session,
            station=station,
            evaluated_at=current_time,
        )
        current_values = [
            ModelProbabilityValue(
                market_id=market.id,
                ts=current_time,
                prob=Decimal(probability),
                sigma=Decimal("1.0000"),
                horizon_h=Decimal("5.00"),
                components={
                    "pipeline": {"cohort": TEST_PIPELINE_COHORT},
                    "shrinkage": {"n": 300},
                    "ecmwf_ens": {
                        "probability": float(probability),
                        "sample_n": 50,
                    },
                    "gefs": {
                        "probability": float(probability),
                        "sample_n": 50,
                    },
                },
                market_snapshot_id=(
                    current_snapshots[market.id].id
                    if market.id == markets[0].id
                    else None
                ),
                market_anchor_protocol=(
                    CLOB_DUAL_PROTOCOL
                    if market.id == markets[0].id
                    else LEGACY_MARKET_PROTOCOL
                ),
            )
            for market, probability in zip(markets, probabilities, strict=True)
        ]
        current_materialization = _materialize_event_probability_coherence(
            current_values,
            markets,
            eligible_market_ids={market.id for market in markets},
            latest_prices={
                markets[0].id: (
                    current_snapshots[markets[0].id],
                    current_batch.batch.first_seen_at,
                )
            },
        )
        assert all(
            value.components["event_coherence"]["materialized"] is True
            for value in current_materialization.values
        )
        assert current_materialization.blocked_event_ids == frozenset(
            {"candidate-anchor-v2-event"}
        )
        await upsert_model_probs(session, list(current_materialization.values))
        selected_market_id = markets[0].id
        sibling_market_ids = [market.id for market in markets[1:]]

    accepted, _skipped = await generate_signals(settings, db_sessions)

    assert accepted == 1
    async with db_sessions() as session:
        signal = await session.scalar(
            select(Signal).where(Signal.market_id == selected_market_id)
        )
        sibling_signals = await session.scalar(
            select(func.count())
            .select_from(Signal)
            .where(Signal.market_id.in_(sibling_market_ids))
        )
        selected_model = await session.scalar(
            select(ModelProb).where(
                ModelProb.market_id == selected_market_id,
                ModelProb.ts == current_time,
            )
        )

    assert signal is not None and signal.status is SignalStatus.PENDING
    assert signal.ts >= current_time
    assert signal.model_prob == Decimal("0.800000")
    assert signal.clob_snapshot_id == current_snapshots[selected_market_id].id
    assert selected_model is not None
    assert selected_model.id > 0
    assert sibling_signals == 0


async def test_blocked_event_revokes_only_unclaimed_pending_signal(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    claimed_signal_id = await _pending_signal(db_sessions)
    assert (await execute_paper_signals(_settings(migrated_database_url), db_sessions))[0] == 0

    async with db_sessions.begin() as session:
        claimed = await session.get(Signal, claimed_signal_id)
        assert claimed is not None
        market = await session.get(Market, claimed.market_id)
        assert market is not None
        unclaimed = Signal(
            market_id=claimed.market_id,
            ts=claimed.ts + timedelta(seconds=1),
            side=claimed.side,
            model_prob=claimed.model_prob,
            market_price=claimed.market_price,
            edge=claimed.edge,
            kelly_frac=claimed.kelly_frac,
            size_usd=claimed.size_usd,
            confidence=claimed.confidence,
            status=SignalStatus.PENDING,
            clob_snapshot_id=claimed.clob_snapshot_id,
            token_id=claimed.token_id,
            benchmark_protocol=claimed.benchmark_protocol,
            station_reliability_snapshot_id=claimed.station_reliability_snapshot_id,
        )
        session.add(unclaimed)
        await session.flush()
        unclaimed_signal_id = unclaimed.id
        event_id = market.event_id

    async with db_sessions.begin() as session:
        revoked = await expire_unclaimed_pending_signals_for_events(
            session,
            {event_id},
            reason="event_probability_cohort_blocked",
        )
    assert revoked == 1

    async with db_sessions() as session:
        claimed = await session.get(Signal, claimed_signal_id)
        unclaimed = await session.get(Signal, unclaimed_signal_id)
        order = await session.scalar(
            select(Order).where(Order.signal_id == claimed_signal_id)
        )
    assert claimed is not None and claimed.status is SignalStatus.PENDING
    assert order is not None
    assert unclaimed is not None and unclaimed.status is SignalStatus.EXPIRED
    assert unclaimed.skip_reason == "event_probability_cohort_blocked"


async def test_live_claim_rechecks_pending_signal_under_lock(
    db_sessions: SessionFactory,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        signal = await session.get(Signal, signal_id, with_for_update=True)
        assert signal is not None
        signal.status = SignalStatus.EXPIRED
        signal.skip_reason = "event_probability_cohort_blocked"

    async with db_sessions.begin() as session:
        order_id = await claim_live_order(
            session,
            LiveOrderClaim(
                signal_id=signal_id,
                market_id=1,
                side=TradeSide.BUY_YES,
                price=Decimal("0.52"),
                size_usd=Decimal("20"),
                token_id="yes-token",
            ),
        )
    assert order_id is None
    async with db_sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Order)) == 0


async def _target_for_signal(
    sessions: SessionFactory,
    signal_id: int,
) -> PriceTarget:
    async with sessions() as session:
        row = (
            await session.execute(
                select(Market)
                .join(Signal, Signal.market_id == Market.id)
                .where(Signal.id == signal_id)
            )
        ).scalar_one()
    assert row.condition_id is not None
    assert row.yes_token_id is not None
    assert row.no_token_id is not None
    return PriceTarget(
        market_id=row.id,
        event_id=row.event_id,
        condition_id=row.condition_id,
        yes_token_id=row.yes_token_id,
        no_token_id=row.no_token_id,
    )


async def _legacy_pending_signal(sessions: SessionFactory) -> int:
    target = await _seed_market(sessions, polymarket_id="pm-live-legacy")
    async with sessions.begin() as session:
        signal = Signal(
            market_id=target.market_id,
            ts=datetime.now(UTC),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.600000"),
            market_price=Decimal("0.520000"),
            edge=Decimal("0.080000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("20.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.PENDING,
        )
        session.add(signal)
        await session.flush()
        return signal.id


async def test_paper_pending_signal_without_reliability_provenance_expires_before_order(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    signal_id = await _pending_signal(
        db_sessions,
        include_reliability_provenance=False,
    )

    assert await execute_paper_signals(
        _settings(migrated_database_url),
        db_sessions,
    ) == (0, 1, 0)

    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = await session.scalar(select(Order).where(Order.signal_id == signal_id))
    assert signal is not None and signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "station_reliability_provenance_missing"
    assert order is None


async def test_paper_nonpositive_pending_signal_expires_without_order_or_global_kill(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    signal_id = await _pending_signal(db_sessions, size_usd=Decimal("0.0000"))

    assert await execute_paper_signals(
        _settings(migrated_database_url),
        db_sessions,
    ) == (0, 1, 0)

    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        control = await session.get(RuntimeControl, 1)
        order_count = await session.scalar(select(func.count()).select_from(Order))
        intent_count = await session.scalar(
            select(func.count()).select_from(ExecutionOrderIntent)
        )
    assert signal is not None
    assert signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "paper_order_size_nonpositive"
    assert control is not None and control.kill_switch is False
    assert order_count == 0
    assert intent_count == 0


async def test_new_station_conflict_revokes_paper_signal_before_order(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    signal_id = await _pending_signal(db_sessions)
    await _append_conflicting_snapshot_for_signal(db_sessions, signal_id)

    assert await execute_paper_signals(
        _settings(migrated_database_url),
        db_sessions,
    ) == (0, 1, 0)

    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = await session.scalar(select(Order).where(Order.signal_id == signal_id))
    assert signal is not None and signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "station_resolution_conflict"
    assert order is None


async def test_new_station_conflict_revokes_live_signal_before_venue_call(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _pending_signal(db_sessions)
    await _append_conflicting_snapshot_for_signal(db_sessions, signal_id)
    trader = FakeTrader()

    assert await execute_live_signals(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
        geoblock=NOT_BLOCKED,
    ) == (0, 0, 0)

    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = await session.scalar(select(Order).where(Order.signal_id == signal_id))
    assert signal is not None and signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "station_resolution_conflict"
    assert order is None
    assert trader.placed == []


async def test_signal_station_reliability_reference_is_immutable(
    db_sessions: SessionFactory,
) -> None:
    signal_id = await _pending_signal(db_sessions)

    with pytest.raises(DBAPIError, match="station reliability reference is immutable"):
        async with db_sessions.begin() as session:
            await session.execute(
                update(Signal)
                .where(Signal.id == signal_id)
                .values(station_reliability_snapshot_id=None)
            )


async def test_signal_rejects_snapshot_from_another_station(
    db_sessions: SessionFactory,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    with pytest.raises(
        DBAPIError,
        match="station reliability reference does not match market station",
    ):
        async with db_sessions.begin() as session:
            original = await session.get(Signal, signal_id)
            other_station = await session.scalar(select(Station).where(Station.icao == "KCOH"))
            assert original is not None and other_station is not None
            mismatch_snapshot = await _append_station_reliability_snapshot(
                session,
                station=other_station,
                evaluated_at=datetime.now(UTC),
            )
            mismatched = Signal(
                market_id=original.market_id,
                ts=original.ts + timedelta(microseconds=1),
                side=original.side,
                model_prob=original.model_prob,
                market_price=original.market_price,
                edge=original.edge,
                kelly_frac=original.kelly_frac,
                size_usd=original.size_usd,
                confidence=original.confidence,
                status=SignalStatus.PENDING,
                clob_snapshot_id=original.clob_snapshot_id,
                token_id=original.token_id,
                benchmark_protocol=original.benchmark_protocol,
                station_reliability_snapshot_id=mismatch_snapshot.id,
            )
            session.add(mismatched)
            await session.flush()


async def test_places_a_live_order_and_marks_the_signal_executed(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _pending_signal(db_sessions)
    trader = FakeTrader(result=PlacedOrder(order_id="0xabc", status="live"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (1, 0, 0)
    assert len(trader.placed) == 1
    # Price snapped down to the tick below the +0.01 cap (0.53).
    assert trader.placed[0]["price"] == Decimal("0.53")

    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        signal = await session.get(Signal, signal_id)
    assert order.mode is ExecutionMode.LIVE
    assert order.clob_order_id == "0xabc"
    assert order.status == "open"
    assert signal is not None and signal.status is SignalStatus.EXECUTED


async def test_live_order_intent_fails_closed_when_capacity_probe_is_unavailable(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    trader = FakeTrader()
    settings = _settings(migrated_database_url).model_copy(
        update={"clob_ledger_capacity_path": str(tmp_path / "missing-probe")}
    )

    result = await execute_live_signals(
        settings,
        db_sessions,
        trader=trader,
        geoblock=NOT_BLOCKED,
    )

    assert result == (0, 0, 0)
    assert trader.placed == []
    async with db_sessions.begin() as session:
        signal = await session.get(Signal, signal_id)
        control = await session.get(RuntimeControl, 1)
        assert signal is not None and signal.status is SignalStatus.EXPIRED
        assert control is not None and control.kill_switch is True
        assert await session.scalar(select(Order).where(Order.signal_id == signal_id)) is None
        control.kill_switch = False


async def test_paper_fill_fails_closed_when_capacity_probe_is_unavailable(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    tmp_path: Path,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    settings = _settings(migrated_database_url).model_copy(
        update={"clob_ledger_capacity_path": str(tmp_path / "missing-probe")}
    )

    result = await execute_paper_signals(settings, db_sessions)

    assert result[0] == 0
    async with db_sessions.begin() as session:
        signal = await session.get(Signal, signal_id)
        control = await session.get(RuntimeControl, 1)
        assert signal is not None and signal.status is SignalStatus.EXPIRED
        assert control is not None and control.kill_switch is True
        assert await session.scalar(select(Order).where(Order.signal_id == signal_id)) is None
        control.kill_switch = False


async def test_paper_uses_earliest_qualifying_post_intent_book(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    target = await _target_for_signal(db_sessions, signal_id)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    settings = _settings(migrated_database_url)

    # First pass durably commits the resting order and immutable intent.  The
    # decision snapshot predates the intent and therefore cannot fill it.
    assert (await execute_paper_signals(settings, db_sessions))[0] == 0

    async with db_sessions.begin() as session:
        non_crossing = await persist_clob_batch(
            session,
            _dual_batch(target, yes_ask="0.52", causal_now=True),
            [target],
        )
        non_crossing_snapshot_id = non_crossing.snapshots[0].id
    async with db_sessions.begin() as session:
        crossing = await persist_clob_batch(
            session,
            _dual_batch(target, yes_ask="0.51", causal_now=True),
            [target],
        )
        crossing_snapshot_id = crossing.snapshots[0].id

    executed, expired, _unfilled = await execute_paper_signals(settings, db_sessions)
    assert (executed, expired) == (1, 0)

    async with db_sessions() as session:
        order = (
            await session.execute(select(Order).where(Order.signal_id == signal_id))
        ).scalar_one()
        signal = await session.get(Signal, signal_id)
        leg = (
            await session.execute(
                select(ExecutionFillLeg).where(ExecutionFillLeg.order_id == order.id)
            )
        ).scalar_one()
        assert order.status == "filled"
        assert signal is not None and signal.status is SignalStatus.EXECUTED
        assert leg.fill_clob_snapshot_id == crossing_snapshot_id
        assert leg.fill_clob_snapshot_id != non_crossing_snapshot_id


async def test_paper_without_qualifying_book_stays_pending_until_ttl(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    activated_probability_release_epoch: ReleaseActivator,
) -> None:
    settings = _settings(migrated_database_url).model_copy(
        update={"paper_order_ttl_minutes": 1}
    )
    await activated_probability_release_epoch(settings)
    signal_id = await _pending_signal(db_sessions)
    target = await _target_for_signal(db_sessions, signal_id)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)

    assert (await execute_paper_signals(settings, db_sessions))[0] == 0
    async with db_sessions.begin() as session:
        await persist_clob_batch(
            session,
            _dual_batch(target, yes_ask="0.52", causal_now=True),
            [target],
        )

    assert (await execute_paper_signals(settings, db_sessions))[0] == 0
    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = (
            await session.execute(select(Order).where(Order.signal_id == signal_id))
        ).scalar_one()
        intent = (
            await session.execute(
                select(ExecutionOrderIntent).where(ExecutionOrderIntent.order_id == order.id)
            )
        ).scalar_one()
        assert signal is not None and signal.status is SignalStatus.PENDING
        assert order.status == "paper_resting"
        assert intent.id > 0
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionFillLeg)
                .where(ExecutionFillLeg.intent_id == intent.id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(ExecutionCostSeal)
                .where(ExecutionCostSeal.intent_id == intent.id)
            )
            == 0
        )
        future = signal.ts + timedelta(minutes=2)

    await _refresh_current_reliability_for_signal(
        db_sessions,
        signal_id=signal_id,
        evaluated_at=future,
    )

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> FutureDateTime:
            if tz is None:
                return cls.fromtimestamp(future.timestamp())
            return cls.fromtimestamp(future.timestamp(), tz)  # type: ignore[arg-type]

    monkeypatch.setattr(execute_signals_module, "datetime", FutureDateTime)
    executed, expired, _unfilled = await execute_paper_signals(settings, db_sessions)
    assert (executed, expired) == (0, 1)

    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = (
            await session.execute(select(Order).where(Order.signal_id == signal_id))
        ).scalar_one()
        seal = (
            await session.execute(
                select(ExecutionCostSeal).where(ExecutionCostSeal.order_id == order.id)
            )
        ).scalar_one()
        assert signal is not None and signal.status is SignalStatus.EXPIRED
        assert order.status == "unfilled_maker"
        assert seal.eligible is False
        assert seal.eligibility_reason == "no_fill"


async def test_kill_switch_quarantine_seals_stranded_paper_intent(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    signal_id = await _pending_signal(db_sessions)
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    settings = _settings(migrated_database_url)

    assert (await execute_paper_signals(settings, db_sessions))[0] == 0
    async with db_sessions.begin() as session:
        signal = await session.get(Signal, signal_id, with_for_update=True)
        control = await session.get(RuntimeControl, 1, with_for_update=True)
        assert signal is not None and control is not None
        signal.status = SignalStatus.EXPIRED
        signal.skip_reason = "kill_switch"
        control.kill_switch = True

    result = await quarantine_execution_state(settings, db_sessions)
    assert result.paper_orders_terminalized == 1
    assert result.paper_intents_sealed == 1
    assert result.legacy_paper_orders_terminalized == 0

    async with db_sessions() as session:
        order = (
            await session.execute(select(Order).where(Order.signal_id == signal_id))
        ).scalar_one()
        intent = (
            await session.execute(
                select(ExecutionOrderIntent).where(ExecutionOrderIntent.order_id == order.id)
            )
        ).scalar_one()
        seal = (
            await session.execute(
                select(ExecutionCostSeal).where(ExecutionCostSeal.intent_id == intent.id)
            )
        ).scalar_one()
        obligation = (
            await session.execute(
                select(ExecutionCostObligation).where(
                    ExecutionCostObligation.intent_id == intent.id
                )
            )
        ).scalar_one()
        assert order.status == "unfilled_maker"
        assert seal.eligible is False
        assert seal.reason_detail == {
            "terminal_status": "unfilled_maker",
            "failure_reason": "kill_switch_execution_quarantine",
        }
        assert obligation.state == "sealed"

    repeated = await quarantine_execution_state(settings, db_sessions)
    assert repeated.paper_orders_terminalized == 0
    assert repeated.paper_intents_sealed == 0


async def test_execution_state_quarantine_requires_kill_switch(
    db_sessions: SessionFactory,
    migrated_database_url: str,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)

    with pytest.raises(ExecutionStateQuarantineError, match="requires the kill switch"):
        await quarantine_execution_state(_settings(migrated_database_url), db_sessions)


async def test_legacy_pending_signal_is_expired_without_live_placement(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _legacy_pending_signal(db_sessions)
    trader = FakeTrader()

    result = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert result == (0, 0, 0)
    assert trader.placed == []
    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
        order = (await session.execute(select(Order))).scalar_one_or_none()
    assert signal is not None
    assert signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "legacy_price_protocol"
    assert order is None


async def test_readiness_gate_blocks_when_geoblocked(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader()

    result = await execute_live_signals(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
        geoblock=GeoblockStatus(blocked=True, country="US", detail="ok"),
    )

    assert result == (0, 0, 0)
    assert trader.placed == []
    async with db_sessions() as session:
        assert (await session.execute(select(Order))).first() is None


async def test_unknown_outcome_halts_trading(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(place_error=TimeoutError("network timeout"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 1)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
    # The order MAY be live: recorded as unconfirmed, kill switch engaged.
    assert order.status == "submit_unconfirmed"
    assert control is not None and control.kill_switch is True


async def test_missing_order_id_response_halts_without_no_fill_seal(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(
        place_error=ClobPlacementOutcomeUnknown("CLOB response is missing orderID")
    )

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 1)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
        seal_count = await session.scalar(
            select(func.count()).select_from(ExecutionCostSeal)
        )
    assert order.status == "submit_unconfirmed"
    assert control is not None and control.kill_switch is True
    assert seal_count == 0


async def test_explicit_rejection_does_not_halt(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(place_error=ClobVenueRejected("not enough balance"))

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 1, 0)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
        seal = (await session.execute(select(ExecutionCostSeal))).scalar_one()
    assert order.status == "submit_failed"
    assert control is not None and control.kill_switch is False
    assert seal.eligible is False
    assert seal.eligibility_reason == "no_fill"


async def test_orphaned_submitting_order_is_escalated_not_resent(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    signal_id = await _pending_signal(db_sessions)
    # Simulate a crash between claim-commit and submit.
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.LIVE,
                clob_order_id=None,
                token_id="yes-token",
                side=TradeSide.BUY_YES,
                price=Decimal("0.530000"),
                size_usd=Decimal("20.0000"),
                filled_usd=Decimal("0"),
                status="submitting",
            )
        )
    trader = FakeTrader()

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 1)
    assert trader.placed == []  # never re-sent
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        control = await session.get(RuntimeControl, 1)
    assert order.status == "submit_unconfirmed"
    assert control is not None and control.kill_switch is True


async def _place_one(db_sessions: SessionFactory, url: str) -> tuple[int, str]:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    trader = FakeTrader(result=PlacedOrder(order_id="0xfill", status="live"))
    await execute_live_signals(_settings(url), db_sessions, trader=trader, geoblock=NOT_BLOCKED)
    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
    return order.id, order.clob_order_id or ""


async def _place_cancel_requested(
    db_sessions: SessionFactory,
    url: str,
) -> tuple[int, str]:
    order_id, clob_order_id = await _place_one(db_sessions, url)
    async with db_sessions.begin() as session:
        await session.execute(
            update(Order).where(Order.id == order_id).values(status="cancel_requested")
        )
    return order_id, clob_order_id


async def test_poll_builds_position_from_partial_then_full_fill(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_id = await _place_one(db_sessions, migrated_database_url)

    # First poll: partial fill of 20 shares, order still live.
    partial = FakeTrader(
        order_payloads={clob_id: {"size_matched": "20", "price": "0.53", "status": "LIVE"}},
        balance=Decimal("980"),
    )
    updated, logged = await poll_live_fills(
        _settings(migrated_database_url), db_sessions, trader=partial
    )
    assert (updated, logged) == (1, 1)
    async with db_sessions() as session:
        position = (await session.execute(select(Position))).scalar_one()
    assert position.order_id == order_id
    assert position.size_shares == Decimal("20.000000")
    assert position.status is PositionStatus.OPEN

    # Second poll: fully matched. Position recomputed (not doubled), order filled.
    full = FakeTrader(
        order_payloads={clob_id: {"size_matched": "47.16", "price": "0.53", "status": "MATCHED"}},
        balance=Decimal("975"),
    )
    await poll_live_fills(_settings(migrated_database_url), db_sessions, trader=full)
    async with db_sessions() as session:
        positions = (await session.execute(select(Position))).scalars().all()
        order = await session.get(Order, order_id)
        balances = (
            (
                await session.execute(
                    select(BankrollLog).where(BankrollLog.mode == ExecutionMode.LIVE)
                )
            )
            .scalars()
            .all()
        )
    assert len(positions) == 1  # recomputed, not appended
    assert positions[0].size_shares == Decimal("47.160000")
    assert order is not None and order.status == "filled"
    # Two distinct balances snapshotted (980 then 975).
    assert {b.balance_usd for b in balances} == {Decimal("980.0000"), Decimal("975.0000")}


async def test_immediate_matched_placement_is_reconciled_before_terminal_state(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)
    placement = FakeTrader(result=PlacedOrder(order_id="0ximmediate", status="matched"))

    assert await execute_live_signals(
        _settings(migrated_database_url),
        db_sessions,
        trader=placement,
        geoblock=NOT_BLOCKED,
    ) == (1, 0, 0)
    async with db_sessions() as session:
        placed_order = (await session.execute(select(Order))).scalar_one()
        assert placed_order.status == "open"
        assert placed_order.filled_usd == Decimal("0.0000")

    reconciliation = FakeTrader(
        order_payloads={
            "0ximmediate": {
                "size_matched": "47.16",
                "price": "0.53",
                "status": "MATCHED",
            }
        }
    )
    await poll_live_fills(
        _settings(migrated_database_url),
        db_sessions,
        trader=reconciliation,
    )

    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
        position = (await session.execute(select(Position))).scalar_one()
        enrollment = (await session.execute(select(ProspectiveValidationEnrollment))).scalar_one()
    assert order.status == "filled"
    assert order.filled_usd == position.size_usd == Decimal("24.9948")
    assert order.filled_at is not None
    assert enrollment.order_id == order.id


async def test_terminal_partial_cancel_preserves_fill_and_enrolls_order(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_id = await _place_one(db_sessions, migrated_database_url)
    partial_cancel = FakeTrader(
        order_payloads={
            clob_id: {
                "size_matched": "20",
                "price": "0.53",
                "status": "CANCELED",
            }
        }
    )

    updated, _ = await poll_live_fills(
        _settings(migrated_database_url),
        db_sessions,
        trader=partial_cancel,
    )

    assert updated == 1
    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        position = (await session.execute(select(Position))).scalar_one()
        enrollment = (await session.execute(select(ProspectiveValidationEnrollment))).scalar_one()
    assert order is not None
    assert order.status == "partially_filled"
    assert order.filled_usd == position.size_usd == Decimal("10.6000")
    assert order.filled_at is not None
    assert enrollment.order_id == order_id


async def test_terminal_zero_fill_cancel_drops_out_of_poll_set(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_id = await _place_one(db_sessions, migrated_database_url)
    canceled = FakeTrader(
        order_payloads={
            clob_id: {
                "size_matched": "0",
                "price": "0.53",
                "status": "CANCELED",
            }
        }
    )

    updated, _ = await poll_live_fills(
        _settings(migrated_database_url),
        db_sessions,
        trader=canceled,
    )

    assert updated == 1
    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        positions = (await session.execute(select(Position))).scalars().all()
        enrollments = (
            (await session.execute(select(ProspectiveValidationEnrollment))).scalars().all()
        )
    assert order is not None and order.status == "canceled"
    assert positions == []
    assert enrollments == []


async def test_execute_signals_dispatches_by_runtime_mode(
    db_sessions: SessionFactory, migrated_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stratus.jobs import execute_signals as dispatch_module

    calls: list[str] = []

    async def fake_live(settings: object, sf: object = None, **_: object) -> tuple[int, int, int]:
        calls.append("live")
        return (1, 0, 0)

    async def fake_paper(settings: object, sf: object = None) -> tuple[int, int, int]:
        calls.append("paper")
        return (0, 0, 0)

    monkeypatch.setattr("stratus.jobs.execute_live_signals.execute_live_signals", fake_live)
    monkeypatch.setattr(dispatch_module, "execute_paper_signals", fake_paper)

    await _set_runtime(db_sessions, mode=ExecutionMode.PAPER)
    await dispatch_module.execute_signals(_settings(migrated_database_url), db_sessions)
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await dispatch_module.execute_signals(_settings(migrated_database_url), db_sessions)

    assert calls == ["paper", "live"]


async def test_audit_health_flags_unconfirmed_live_orders(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    from stratus.jobs.audit_health import audit_health

    signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.LIVE,
                clob_order_id=None,
                token_id="yes-token",
                side=TradeSide.BUY_YES,
                price=Decimal("0.530000"),
                size_usd=Decimal("20.0000"),
                filled_usd=Decimal("0"),
                status="submit_unconfirmed",
            )
        )

    status, _issue_count = await audit_health(_settings(migrated_database_url), db_sessions)
    assert status == "blocking"
    async with db_sessions() as session:
        latest = await session.get(RuntimeControl, 1)
    # Critical audit engages the kill switch.
    assert latest is not None and latest.kill_switch is True


async def test_preflight_reports_ready_without_placing_an_order(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _pending_signal(db_sessions)  # gives an active verified market to sample
    trader = FakeTrader(balance=Decimal("500"))

    report = await preflight_live(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert report.ready is True
    assert report.balance_usd == Decimal("500")
    assert report.sample_tick == Decimal("0.01")
    assert trader.placed == []  # never places an order
    assert report.checks["geoblock"].startswith("allowed")


async def test_preflight_not_ready_when_geoblocked(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    trader = FakeTrader()
    report = await preflight_live(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
        geoblock=GeoblockStatus(blocked=True, country="US", detail="ok"),
    )
    assert report.ready is False
    assert report.geoblock_blocked is True


async def test_preflight_not_ready_without_a_wallet(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key=None,
    )  # no private key
    report = await preflight_live(settings, db_sessions, geoblock=NOT_BLOCKED)
    assert report.configured is False
    assert report.ready is False


async def test_poll_uses_trade_vwap_when_it_agrees_with_the_order(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_id = await _place_one(db_sessions, migrated_database_url)
    # Order reports 40 matched at limit 0.53; trades show a better VWAP + fee.
    trader = FakeTrader(
        order_payloads={clob_id: {"size_matched": "40", "price": "0.53", "status": "LIVE"}},
        trade_payloads={
            "yes-token": [
                {
                    "status": "MATCHED",
                    "taker_order_id": clob_id,
                    "size": "40",
                    "price": "0.51",
                    "fee_rate_bps": "20",
                }
            ]
        },
    )
    await poll_live_fills(_settings(migrated_database_url), db_sessions, trader=trader)

    async with db_sessions() as session:
        position = (await session.execute(select(Position))).scalar_one()
    # VWAP 0.51 (not the 0.53 limit); fee = 20bps(0.002) * 0.51 * 40 = 0.0408.
    assert position.avg_price == Decimal("0.510000")
    assert position.size_usd == Decimal("20.4000")
    assert position.fee_usd == Decimal("0.04080")


async def test_poll_falls_back_to_limit_price_on_trade_drift(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_id = await _place_one(db_sessions, migrated_database_url)
    # Order says 40 matched; trades only cover 10 -> inconsistent, use limit.
    trader = FakeTrader(
        order_payloads={clob_id: {"size_matched": "40", "price": "0.53", "status": "LIVE"}},
        trade_payloads={
            "yes-token": [
                {
                    "status": "MATCHED",
                    "taker_order_id": clob_id,
                    "size": "10",
                    "price": "0.40",
                    "fee_rate_bps": "20",
                }
            ]
        },
    )
    await poll_live_fills(_settings(migrated_database_url), db_sessions, trader=trader)

    async with db_sessions() as session:
        position = (await session.execute(select(Position))).scalar_one()
    assert position.avg_price == Decimal("0.530000")  # limit price fallback
    assert position.size_shares == Decimal("40.000000")


async def test_stale_live_signals_are_expired_not_left_pending(
    db_sessions: SessionFactory,
    migrated_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    # A valid dual-token signal older than the 10-minute live TTL.
    signal_id = await _pending_signal(db_sessions)
    async with db_sessions() as session:
        seeded_signal = await session.get(Signal, signal_id)
    assert seeded_signal is not None
    future_now = seeded_signal.ts + timedelta(minutes=30)

    # Keep current evidence valid if the simulated clock crosses KLGA
    # midnight; the attested decision-time snapshot remains unchanged.
    await _refresh_current_reliability_for_signal(
        db_sessions,
        signal_id=signal_id,
        evaluated_at=future_now,
    )

    class FutureDateTime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return future_now if tz is not None else future_now.replace(tzinfo=None)

    monkeypatch.setattr("stratus.jobs.execute_live_signals.datetime", FutureDateTime)
    trader = FakeTrader()

    placed, failed, unconfirmed = await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    assert (placed, failed, unconfirmed) == (0, 0, 0)
    assert trader.placed == []
    async with db_sessions() as session:
        signal = await session.get(Signal, signal_id)
    assert signal is not None
    assert signal.status is SignalStatus.EXPIRED
    assert signal.skip_reason == "live_execution_timeout"


async def test_order_placed_into_active_kill_switch_is_queued_for_cancel(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    await _set_runtime(db_sessions, mode=ExecutionMode.LIVE)
    await _pending_signal(db_sessions)

    # A trader whose placement engages the kill switch mid-cycle, simulating a
    # /stop that lands after the readiness check but during placement.
    class KillMidPlacement(FakeTrader):
        async def place_gtc_buy(self, **kwargs: object) -> PlacedOrder:
            async with db_sessions.begin() as session:
                await session.execute(
                    RuntimeControl.__table__.update()
                    .where(RuntimeControl.id == 1)
                    .values(kill_switch=True)
                )
            return await super().place_gtc_buy(**kwargs)

    trader = KillMidPlacement(result=PlacedOrder(order_id="0xrace", status="live"))
    await execute_live_signals(
        _settings(migrated_database_url), db_sessions, trader=trader, geoblock=NOT_BLOCKED
    )

    async with db_sessions() as session:
        order = (await session.execute(select(Order))).scalar_one()
    # The fresh order missed the kill switch's sweep; the re-check catches it.
    assert order.clob_order_id == "0xrace"
    assert order.status == "cancel_requested"


async def test_in_flight_live_exposure_tracks_orders_without_a_position(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    from stratus.repositories.execution import in_flight_live_exposure

    signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.LIVE,
                clob_order_id="0xopen",
                token_id="yes-token",
                side=TradeSide.BUY_YES,
                price=Decimal("0.53"),
                size_usd=Decimal("20"),
                filled_usd=Decimal("0"),
                status="open",
            )
        )
        await session.flush()
        order_id = (await session.execute(select(Order.id))).scalar_one()

    # Placed, no position yet -> counted as in-flight committed capital.
    async with db_sessions() as session:
        rows = await in_flight_live_exposure(session)
    assert len(rows) == 1
    assert rows[0].size_usd == Decimal("20.0000")

    # Once the fill poll creates the position, it is no longer in-flight.
    async with db_sessions.begin() as session:
        market_id = rows[0].market_id
        session.add(
            Position(
                order_id=order_id,
                market_id=market_id,
                mode=ExecutionMode.LIVE,
                side=TradeSide.BUY_YES,
                avg_price=Decimal("0.53"),
                size_shares=Decimal("37"),
                size_usd=Decimal("20"),
                status=PositionStatus.OPEN,
                benchmark_protocol=CLOB_DUAL_PROTOCOL,
                token_id="yes-token",
            )
        )
    async with db_sessions() as session:
        assert await in_flight_live_exposure(session) == []


async def test_in_flight_excludes_paper_orders(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    from stratus.repositories.execution import in_flight_live_exposure

    signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        session.add(
            Order(
                signal_id=signal_id,
                mode=ExecutionMode.PAPER,
                clob_order_id="paper-1",
                token_id="yes-token",
                side=TradeSide.BUY_YES,
                price=Decimal("0.53"),
                size_usd=Decimal("20"),
                filled_usd=Decimal("20"),
                status="filled",
            )
        )
    async with db_sessions() as session:
        assert await in_flight_live_exposure(session) == []


async def test_paper_executions_aggregate_open_position_by_market_and_side(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    first_signal_id = await _pending_signal(db_sessions)
    async with db_sessions.begin() as session:
        first_signal = await session.get(Signal, first_signal_id)
        assert first_signal is not None
        second_signal = Signal(
            market_id=first_signal.market_id,
            ts=first_signal.ts + timedelta(seconds=1),
            side=TradeSide.BUY_YES,
            model_prob=Decimal("0.610000"),
            market_price=Decimal("0.520000"),
            edge=Decimal("0.070000"),
            kelly_frac=Decimal("0.010000"),
            size_usd=Decimal("30.0000"),
            confidence=SignalConfidence.B,
            status=SignalStatus.PENDING,
            clob_snapshot_id=first_signal.clob_snapshot_id,
            token_id=first_signal.token_id,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
            station_reliability_snapshot_id=first_signal.station_reliability_snapshot_id,
        )
        session.add(second_signal)
        await session.flush()
        second_signal_id = second_signal.id
        market_id = first_signal.market_id

    async with db_sessions.begin() as session:
        filled_at = await session.scalar(select(func.clock_timestamp()))
        assert filled_at is not None
        written = await persist_paper_executions(
            session,
            [
                PaperExecutionValue(
                    signal_id=first_signal_id,
                    market_id=market_id,
                    side=TradeSide.BUY_YES,
                    requested_usd=Decimal("20.0000"),
                    filled_usd=Decimal("20.0000"),
                    avg_price=Decimal("0.500000"),
                    size_shares=Decimal("40.000000"),
                    fee_usd=Decimal("0.00000"),
                    total_cost_usd=Decimal("20.0000"),
                    filled_at=filled_at,
                ),
                PaperExecutionValue(
                    signal_id=second_signal_id,
                    market_id=market_id,
                    side=TradeSide.BUY_YES,
                    requested_usd=Decimal("30.0000"),
                    filled_usd=Decimal("30.0000"),
                    avg_price=Decimal("0.600000"),
                    size_shares=Decimal("50.000000"),
                    fee_usd=Decimal("0.00000"),
                    total_cost_usd=Decimal("30.0000"),
                    filled_at=filled_at,
                ),
            ],
        )

    async with db_sessions() as session:
        positions = (await session.execute(select(Position))).scalars().all()
        signals = (await session.execute(select(Signal))).scalars().all()

    assert written == 2
    assert len(positions) == 1
    assert positions[0].size_usd == Decimal("50.0000")
    assert positions[0].size_shares == Decimal("90.000000")
    assert positions[0].avg_price == Decimal("0.555556")
    assert {signal.status for signal in signals} == {SignalStatus.EXECUTED}


async def test_cancel_requested_live_orders_are_canceled(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_order_id = await _place_cancel_requested(
        db_sessions,
        migrated_database_url,
    )
    trader = FakeTrader(
        order_payloads={
            clob_order_id: {
                "size_matched": "0",
                "price": "0.53",
                "status": "CANCELED",
            }
        }
    )

    canceled = await cancel_live_orders(
        _settings(migrated_database_url), db_sessions, trader=trader
    )

    assert canceled == 1
    assert trader.canceled == [clob_order_id]
    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        seal = (await session.execute(select(ExecutionCostSeal))).scalar_one()
        obligation = (await session.execute(select(ExecutionCostObligation))).scalar_one()
        observations = list(
            (
                await session.scalars(
                    select(ExecutionReconciliationObservation).order_by(
                        ExecutionReconciliationObservation.id
                    )
                )
            ).all()
        )
        leg_count = await session.scalar(select(func.count()).select_from(ExecutionFillLeg))
    assert order is not None
    assert order.status == "canceled"
    assert seal.eligibility_reason == "no_fill"
    assert seal.markout_300s_coverage == "not_applicable"
    assert obligation.state == "sealed"
    assert leg_count == 0
    assert {row.observation_kind for row in observations} == {
        "placement",
        "cancel",
        "order",
    }


async def test_cancel_reconciles_late_partial_fill_before_terminalizing(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_order_id = await _place_cancel_requested(
        db_sessions,
        migrated_database_url,
    )
    trader = FakeTrader(
        order_payloads={
            clob_order_id: {
                "size_matched": "20",
                "price": "0.53",
                "status": "CANCELED",
            }
        }
    )

    canceled = await cancel_live_orders(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
    )

    assert canceled == 1
    assert trader.canceled == [clob_order_id]
    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        position = (await session.execute(select(Position))).scalar_one()
        enrollment = (await session.execute(select(ProspectiveValidationEnrollment))).scalar_one()
        seal = (await session.execute(select(ExecutionCostSeal))).scalar_one()
        obligation = (await session.execute(select(ExecutionCostObligation))).scalar_one()
        leg = (await session.execute(select(ExecutionFillLeg))).scalar_one()
        observations = list(
            (await session.scalars(select(ExecutionReconciliationObservation))).all()
        )
    assert order is not None
    assert order.status == "partially_filled"
    assert order.filled_usd == position.size_usd == Decimal("10.6000")
    assert enrollment.order_id == order.id
    assert seal.eligible is False
    assert seal.eligibility_reason == "live_schema_unverified"
    assert seal.markout_300s_coverage == "not_applicable"
    assert obligation.state == "sealed"
    assert leg.eligible is False
    assert leg.liquidity_role == "unknown"
    assert leg.fill_effective_at is None
    assert leg.fill_clob_snapshot_id is None
    assert {row.observation_kind for row in observations} == {
        "placement",
        "cancel",
        "order",
        "trades",
    }


async def test_cancel_keeps_order_pending_when_final_state_is_unavailable(
    db_sessions: SessionFactory, migrated_database_url: str
) -> None:
    order_id, clob_order_id = await _place_cancel_requested(
        db_sessions,
        migrated_database_url,
    )
    trader = FakeTrader(order_payloads={})

    canceled = await cancel_live_orders(
        _settings(migrated_database_url),
        db_sessions,
        trader=trader,
    )

    assert canceled == 0
    async with db_sessions() as session:
        order = await session.get(Order, order_id)
        observations = list(
            (await session.scalars(select(ExecutionReconciliationObservation))).all()
        )
        assert await session.scalar(select(func.count()).select_from(ExecutionCostSeal)) == 0
    assert order is not None
    assert order.status == "cancel_requested"
    assert trader.canceled == [clob_order_id]
    assert {row.observation_kind for row in observations} == {"placement", "cancel"}
