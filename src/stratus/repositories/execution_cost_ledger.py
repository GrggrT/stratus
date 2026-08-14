from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from math import floor
from typing import cast

from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Exists

from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    EvidenceContinuityLane,
    ExecutionCostObligation,
    ExecutionCostRuntime,
    ExecutionCostSeal,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
    ExecutionPostfillMark,
    ExecutionReconciliationObservation,
    Market,
    ModelProb,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    TradeSide,
)
from stratus.repositories.analytics import probability_pipeline_version
from stratus.repositories.evidence_continuity import (
    current_probability_pipeline_release_epoch,
)
from stratus.repositories.gamma_outcomes import lock_gamma_event
from stratus.repositories.validation_ledger import _source_hashes, enroll_terminal_order
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.execution_cost import (
    EXECUTION_COST_DEFINITION_HASH,
    EXECUTION_COST_DEFINITION_VERSION,
    EXPECTED_SLIPPAGE_NOT_MODELED,
    MARK_GRACE_SECONDS,
    MARK_HORIZONS_SECONDS,
    ExecutionCostIntegrityError,
    calculate_adverse_selection,
    calculate_seal_metrics,
    canonical_weighted_fill_price,
    next_mark_collection_due,
    purchased_side_quote,
    source_payload_hash,
)
from stratus.services.execution_cost import (
    ExecutionFillLeg as AlgebraFillLeg,
)

_ZERO_HASH = "0" * 64
_EXPECTED_FEE_NOT_MODELED = "expected_fee_not_modeled"
_FILLED_STATUSES = frozenset({"filled", "partially_filled"})
_NO_FILL_STATUSES = frozenset(
    {"canceled", "unfilled_maker", "submit_failed", "rejected", "expired"}
)
_SHARE_QUANTUM = Decimal("0.000001")
_NOTIONAL_QUANTUM = Decimal("0.000001")
_PRICE_QUANTUM = Decimal("0.00000001")
_FEE_QUANTUM = Decimal("0.00000001")
_LIVE_TERMINAL_STATUSES = frozenset(
    {"filled", "partially_filled", "canceled", "cancelled", "rejected", "submit_failed"}
)
_OBSERVATION_KINDS = frozenset({"placement", "order", "trades", "cancel"})


@dataclass(frozen=True, slots=True)
class PaperFillLegSpec:
    sequence: int
    shares: Decimal
    price: Decimal
    fee_usd: Decimal
    liquidity_role: str
    external_leg_key: str | None = None


@dataclass(frozen=True, slots=True)
class PaperFillResult:
    order_id: int
    intent_id: int
    obligation_id: int
    leg_ids: tuple[int, ...]
    terminal_status: str
    filled_notional_usd: Decimal
    total_fee_usd: Decimal
    total_cost_usd: Decimal
    total_shares: Decimal
    vwap_fill_price: Decimal
    filled_at: datetime


@dataclass(frozen=True, slots=True)
class MarkoutSealRun:
    obligations_claimed: int = 0
    marks_observed: int = 0
    marks_missing: int = 0
    obligations_deferred: int = 0
    obligation_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class ObligationFinalizationRun:
    obligations_claimed: int = 0
    obligations_sealed: int = 0
    obligations_deferred: int = 0
    obligation_ids: tuple[int, ...] = ()


async def append_live_reconciliation_observation(
    session: AsyncSession,
    order_id: int,
    *,
    observation_kind: str,
    sdk_method: str,
    parser_version: str,
    request_started_at: datetime,
    response_received_at: datetime,
    normalized_payload: Mapping[str, object],
) -> ExecutionReconciliationObservation:
    """Append or content-dedupe one normalized, source-clocked live SDK response."""

    if observation_kind not in _OBSERVATION_KINDS:
        raise ExecutionCostIntegrityError("unsupported live observation kind")
    method = sdk_method.strip()
    parser = parser_version.strip()
    if not method or not parser:
        raise ExecutionCostIntegrityError("SDK method and parser version are required")
    if (
        request_started_at.tzinfo is None
        or request_started_at.utcoffset() is None
        or response_received_at.tzinfo is None
        or response_received_at.utcoffset() is None
        or response_received_at < request_started_at
    ):
        raise ExecutionCostIntegrityError(
            "live observation clocks must be ordered and timezone-aware"
        )
    intent = await session.scalar(
        select(ExecutionOrderIntent).where(ExecutionOrderIntent.order_id == order_id)
    )
    if intent is None or intent.mode is not ExecutionMode.LIVE:
        raise ExecutionCostIntegrityError("live observation requires a live 0038 intent")
    if request_started_at < intent.intent_at:
        raise ExecutionCostIntegrityError("live SDK request predates its immutable intent")
    payload = dict(normalized_payload)
    payload_hash = source_payload_hash(payload)
    values = {
        "order_id": order_id,
        "observation_kind": observation_kind,
        "sdk_method": method,
        "parser_version": parser,
        "request_started_at": request_started_at,
        "response_received_at": response_received_at,
        "normalized_payload": payload,
        "payload_sha256": payload_hash,
        "definition_version": EXECUTION_COST_DEFINITION_VERSION,
        "definition_hash": EXECUTION_COST_DEFINITION_HASH,
        "record_hash": _ZERO_HASH,
    }
    observation_id = (
        await session.execute(
            insert(ExecutionReconciliationObservation)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_execution_reconciliation_observations_content")
            .returning(ExecutionReconciliationObservation.id)
        )
    ).scalar_one_or_none()
    if observation_id is None:
        observation = await session.scalar(
            select(ExecutionReconciliationObservation).where(
                ExecutionReconciliationObservation.order_id == order_id,
                ExecutionReconciliationObservation.observation_kind == observation_kind,
                ExecutionReconciliationObservation.sdk_method == method,
                ExecutionReconciliationObservation.payload_sha256 == payload_hash,
            )
        )
        if observation is None:
            raise ExecutionCostIntegrityError(
                "live observation conflict did not expose the immutable row"
            )
        if observation.parser_version != parser or observation.normalized_payload != payload:
            raise ExecutionCostIntegrityError(
                "live observation retry differs from immutable stored evidence"
            )
        return observation
    observation = await session.get(
        ExecutionReconciliationObservation,
        observation_id,
    )
    if observation is None:
        raise ExecutionCostIntegrityError("stored live observation is unavailable")
    await session.refresh(observation)
    return observation


def _decimal(value: Decimal, *, quantum: Decimal, label: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite() or result <= 0:
        raise ExecutionCostIntegrityError(f"{label} must be finite and positive")
    return result.quantize(quantum, rounding=ROUND_HALF_UP)


async def _clock_timestamp(session: AsyncSession) -> datetime:
    value = await session.scalar(select(func.clock_timestamp()))
    if value is None:
        raise ExecutionCostIntegrityError("database clock is unavailable")
    return cast(datetime, value)


async def execution_cost_clock_timestamp(session: AsyncSession) -> datetime:
    """Expose one database-witness clock for a composite maintenance cycle."""

    return await _clock_timestamp(session)


def _missing_required_mark_exists() -> Exists:
    """Correlated predicate for obligations whose effective legs need a mark."""

    missing_horizon = or_(
        *(
            ~select(ExecutionPostfillMark.id)
            .where(
                ExecutionPostfillMark.fill_leg_id == ExecutionFillLeg.id,
                ExecutionPostfillMark.horizon_seconds == horizon,
            )
            .correlate(ExecutionFillLeg)
            .exists()
            for horizon in MARK_HORIZONS_SECONDS
        )
    )
    return (
        select(ExecutionFillLeg.id)
        .where(
            ExecutionFillLeg.intent_id == ExecutionCostObligation.intent_id,
            ExecutionFillLeg.fill_effective_at.is_not(None),
            missing_horizon,
        )
        .correlate(ExecutionCostObligation)
        .exists()
    )


async def create_intent_for_order(
    session: AsyncSession,
    order_id: int,
    *,
    release_image_digest: str,
    order_style: str,
    expected_fee_usd: Decimal | None = None,
    expected_fee_unavailable_reason: str | None = None,
    expected_slippage_per_share: Decimal | None = None,
    expected_slippage_unavailable_reason: str | None = EXPECTED_SLIPPAGE_NOT_MODELED,
) -> ExecutionOrderIntent:
    """Append one zero-backfill execution intent from attested decision evidence.

    The database trigger independently reprojects and hashes every cited source.
    A repeated call returns the existing immutable row, but never silently accepts
    a different execution style or expected-cost declaration.
    """

    if order_style not in {"maker", "taker"}:
        raise ExecutionCostIntegrityError("order_style must be maker or taker")
    requested_expected_fee = None if expected_fee_usd is None else Decimal(expected_fee_usd)
    requested_fee_reason = (
        (expected_fee_unavailable_reason or _EXPECTED_FEE_NOT_MODELED).strip()
        if expected_fee_usd is None
        else None
    )
    requested_expected_slippage = (
        None if expected_slippage_per_share is None else Decimal(expected_slippage_per_share)
    )
    requested_slippage_reason = (
        (expected_slippage_unavailable_reason or "").strip()
        if expected_slippage_per_share is None
        else None
    )
    existing = await session.scalar(
        select(ExecutionOrderIntent)
        .where(ExecutionOrderIntent.order_id == order_id)
        .with_for_update()
    )
    if existing is not None:
        if (
            existing.order_style != order_style
            or existing.expected_fee_usd != requested_expected_fee
            or existing.expected_fee_unavailable_reason != requested_fee_reason
            or existing.expected_slippage_per_share != requested_expected_slippage
            or existing.expected_slippage_unavailable_reason != requested_slippage_reason
        ):
            raise ExecutionCostIntegrityError(
                f"order {order_id} already has a different immutable execution intent"
            )
        return existing

    runtime = await session.get(ExecutionCostRuntime, 1)
    order = await session.get(Order, order_id, with_for_update=True)
    if runtime is None:
        raise ExecutionCostIntegrityError("execution-cost runtime is unavailable")
    if order is None:
        raise ExecutionCostIntegrityError(f"order {order_id} does not exist")
    requested_limit_price = Decimal(order.price)
    requested_notional_usd = Decimal(order.size_usd)
    if (
        not requested_limit_price.is_finite()
        or requested_limit_price <= 0
        or requested_limit_price >= 1
    ):
        raise ExecutionCostIntegrityError(
            "order requested limit price must be finite and between zero and one"
        )
    if not requested_notional_usd.is_finite() or requested_notional_usd <= 0:
        raise ExecutionCostIntegrityError(
            "order requested notional must be finite and positive"
        )
    requested_shares = (requested_notional_usd / requested_limit_price).quantize(
        _SHARE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if not requested_shares.is_finite() or requested_shares <= 0:
        raise ExecutionCostIntegrityError(
            "order requested shares must be positive after quantization"
        )
    signal = await session.get(Signal, order.signal_id)
    if signal is None or signal.clob_snapshot_id is None:
        raise ExecutionCostIntegrityError("order signal lacks decision CLOB provenance")
    snapshot = await session.get(ClobMarketSnapshot, signal.clob_snapshot_id)
    if snapshot is None:
        raise ExecutionCostIntegrityError("decision CLOB snapshot does not exist")
    model = await session.scalar(
        select(ModelProb)
        .where(
            ModelProb.market_id == signal.market_id,
            ModelProb.ts <= signal.ts,
            ModelProb.prob == signal.model_prob,
            ModelProb.market_snapshot_id == signal.clob_snapshot_id,
            ModelProb.market_anchor_protocol == signal.benchmark_protocol,
        )
        .order_by(ModelProb.ts.desc(), ModelProb.id.desc())
        .limit(1)
    )
    if model is None:
        raise ExecutionCostIntegrityError("order signal lacks matching model provenance")
    cohort = probability_pipeline_version(model.components)
    if not cohort:
        raise ExecutionCostIntegrityError("model probability lacks a pipeline cohort")
    release_epoch = await current_probability_pipeline_release_epoch(session)
    if release_epoch is None:
        raise ExecutionCostIntegrityError(
            "probability pipeline release epoch is not activated"
        )
    if cohort != release_epoch.cohort:
        raise ExecutionCostIntegrityError(
            "model probability cohort differs from the active release epoch"
        )
    if release_image_digest != release_epoch.image_digest:
        raise ExecutionCostIntegrityError(
            "runtime image digest differs from the active release epoch"
        )
    required_decision_lanes = {
        "predictive_validation",
        "selection_policy",
        "station_lifecycle",
    }
    decision_lane_rows = (
        await session.execute(
            select(
                EvidenceContinuityLane.lane_name,
                EvidenceContinuityLane.boundary_at,
            ).where(
            EvidenceContinuityLane.epoch_id == release_epoch.id,
            EvidenceContinuityLane.lane_name.in_(required_decision_lanes),
            EvidenceContinuityLane.action.in_(("continue", "fork")),
        )
        )
    ).all()
    decision_boundaries = {
        lane_name: boundary_at for lane_name, boundary_at in decision_lane_rows
    }
    if set(decision_boundaries) != required_decision_lanes:
        raise ExecutionCostIntegrityError(
            "decision evidence boundaries are unavailable"
        )
    evidence_opened_at = max(decision_boundaries.values())
    if signal.ts < evidence_opened_at or model.ts < evidence_opened_at:
        raise ExecutionCostIntegrityError(
            "order decision evidence predates an active evidence-lane boundary"
        )
    # The 0040 database trigger independently verifies the exact runtime
    # image witness.  SET LOCAL semantics keep the value transaction-scoped
    # and prevent connection-pool leakage.
    await session.execute(
        text("SELECT set_config('stratus.release_image_digest', :digest, true)"),
        {"digest": release_image_digest},
    )
    if signal.benchmark_protocol != CLOB_DUAL_PROTOCOL:
        raise ExecutionCostIntegrityError("execution intent requires dual-token provenance")
    quote = purchased_side_quote(snapshot, order.side, signal.model_prob)
    if order.token_id != quote.token_id or signal.token_id != quote.token_id:
        raise ExecutionCostIntegrityError("order token differs from purchased-side evidence")
    if order.created_at < runtime.opened_at:
        raise ExecutionCostIntegrityError("order predates the execution-cost cohort boundary")

    _, signal_hash, model_hash = _source_hashes(order, signal, model)
    if model_hash is None:
        raise ExecutionCostIntegrityError("model source hash is unavailable")
    if expected_fee_usd is None:
        expected_fee_reason = requested_fee_reason or ""
        if not expected_fee_reason:
            raise ExecutionCostIntegrityError("missing expected fee requires a reason")
        expected_fee = None
    else:
        expected_fee = Decimal(expected_fee_usd)
        if not expected_fee.is_finite() or expected_fee < 0:
            raise ExecutionCostIntegrityError("expected fee must be finite and non-negative")
        if expected_fee_unavailable_reason:
            raise ExecutionCostIntegrityError(
                "modeled expected fee cannot have an unavailable reason"
            )
        expected_fee_reason = None
    if expected_slippage_per_share is None:
        expected_slippage_reason = requested_slippage_reason or ""
        if not expected_slippage_reason:
            raise ExecutionCostIntegrityError("missing expected slippage requires a reason")
        expected_slippage = None
    else:
        expected_slippage = Decimal(expected_slippage_per_share)
        if not expected_slippage.is_finite() or expected_slippage < 0:
            raise ExecutionCostIntegrityError("expected slippage must be finite and non-negative")
        if expected_slippage_unavailable_reason not in {
            None,
            "",
            EXPECTED_SLIPPAGE_NOT_MODELED,
        }:
            raise ExecutionCostIntegrityError(
                "modeled expected slippage cannot have an unavailable reason"
            )
        expected_slippage_reason = None

    intent = ExecutionOrderIntent(
        order_id=order.id,
        signal_id=signal.id,
        model_prob_id=model.id,
        market_id=signal.market_id,
        decision_clob_snapshot_id=snapshot.id,
        mode=order.mode,
        side=order.side,
        token_id=quote.token_id,
        pipeline_cohort=cohort,
        signal_record_hash=signal_hash,
        model_record_hash=model_hash,
        snapshot_record_hash=snapshot.record_hash,
        yes_model_probability=quote.yes_model_probability,
        side_model_probability=quote.side_model_probability,
        side_best_bid=quote.best_bid,
        side_best_ask=quote.best_ask,
        side_midpoint=quote.midpoint,
        order_style=order_style,
        requested_limit_price=requested_limit_price,
        requested_shares=requested_shares,
        requested_notional_usd=requested_notional_usd,
        expected_fee_usd=expected_fee,
        expected_fee_unavailable_reason=expected_fee_reason,
        expected_slippage_per_share=expected_slippage,
        expected_slippage_unavailable_reason=expected_slippage_reason,
        decision_at=signal.ts,
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        record_hash=_ZERO_HASH,
    )
    session.add(intent)
    await session.flush()
    await session.refresh(intent)
    return intent


async def _aggregate_paper_position(
    session: AsyncSession,
    *,
    intent: ExecutionOrderIntent,
    avg_price: Decimal,
    total_shares: Decimal,
    notional_usd: Decimal,
    fee_usd: Decimal,
) -> None:
    event_id = await session.scalar(select(Market.event_id).where(Market.id == intent.market_id))
    if event_id is None:
        raise ExecutionCostIntegrityError("paper fill market lacks an event identity")
    await lock_gamma_event(session, event_id)
    position = await session.scalar(
        select(Position)
        .where(
            Position.market_id == intent.market_id,
            Position.mode == ExecutionMode.PAPER,
            Position.side == intent.side,
            Position.status == PositionStatus.OPEN,
            Position.benchmark_protocol == CLOB_DUAL_PROTOCOL,
            Position.token_id == intent.token_id,
        )
        .order_by(Position.id)
        .with_for_update()
    )
    total_cost = notional_usd + fee_usd
    if position is None:
        session.add(
            Position(
                market_id=intent.market_id,
                mode=ExecutionMode.PAPER,
                side=intent.side,
                avg_price=avg_price,
                size_shares=total_shares,
                size_usd=total_cost,
                fee_usd=fee_usd,
                status=PositionStatus.OPEN,
                benchmark_protocol=CLOB_DUAL_PROTOCOL,
                token_id=intent.token_id,
            )
        )
        return
    combined_shares = position.size_shares + total_shares
    position.avg_price = (
        (position.avg_price * position.size_shares + avg_price * total_shares) / combined_shares
    ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    position.size_shares = combined_shares.quantize(_SHARE_QUANTUM)
    position.size_usd = (position.size_usd + total_cost).quantize(Decimal("0.0001"))
    position.fee_usd = ((position.fee_usd or Decimal(0)) + fee_usd).quantize(Decimal("0.00001"))


async def record_paper_fill(
    session: AsyncSession,
    intent_id: int,
    fill_clob_snapshot_id: int,
    legs: Sequence[PaperFillLegSpec],
) -> PaperFillResult:
    """Atomically project one attested paper fill into legacy state and 0038."""

    intent = await session.get(ExecutionOrderIntent, intent_id, with_for_update=True)
    if intent is None:
        raise ExecutionCostIntegrityError(f"execution intent {intent_id} does not exist")
    existing_obligation = await session.scalar(
        select(ExecutionCostObligation).where(ExecutionCostObligation.intent_id == intent_id)
    )
    if existing_obligation is not None:
        existing_legs = list(
            (
                await session.scalars(
                    select(ExecutionFillLeg)
                    .where(ExecutionFillLeg.intent_id == intent_id)
                    .order_by(ExecutionFillLeg.leg_sequence)
                )
            ).all()
        )
        if not existing_legs:
            raise ExecutionCostIntegrityError("intent already terminated without a fill")
        ordered_specs = sorted(legs, key=lambda item: item.sequence)
        if len(ordered_specs) != len(existing_legs) or any(
            stored.fill_clob_snapshot_id != fill_clob_snapshot_id
            or stored.leg_sequence != supplied.sequence
            or stored.shares
            != Decimal(supplied.shares).quantize(_SHARE_QUANTUM, rounding=ROUND_HALF_UP)
            or stored.price
            != Decimal(supplied.price).quantize(_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
            or stored.fee_usd
            != Decimal(supplied.fee_usd).quantize(_FEE_QUANTUM, rounding=ROUND_HALF_UP)
            or stored.liquidity_role != supplied.liquidity_role
            for stored, supplied in zip(existing_legs, ordered_specs, strict=True)
        ):
            raise ExecutionCostIntegrityError(
                "paper fill retry differs from immutable stored evidence"
            )
        order = await session.get(Order, intent.order_id)
        if order is None or order.filled_at is None or order.fee_usd is None:
            raise ExecutionCostIntegrityError("persisted paper fill has incomplete legacy state")
        total_shares = sum((leg.shares for leg in existing_legs), Decimal(0))
        notional = sum((leg.notional_usd for leg in existing_legs), Decimal(0))
        vwap = canonical_weighted_fill_price(
            tuple((leg.shares, leg.price) for leg in existing_legs)
        )
        return PaperFillResult(
            order_id=order.id,
            intent_id=intent.id,
            obligation_id=existing_obligation.id,
            leg_ids=tuple(leg.id for leg in existing_legs),
            terminal_status=order.status,
            filled_notional_usd=notional,
            total_fee_usd=order.fee_usd,
            total_cost_usd=notional + order.fee_usd,
            total_shares=total_shares,
            vwap_fill_price=vwap,
            filled_at=order.filled_at,
        )
    if intent.mode is not ExecutionMode.PAPER:
        raise ExecutionCostIntegrityError("paper fill requires a paper execution intent")
    if not legs:
        raise ExecutionCostIntegrityError("paper fill requires at least one atomic leg")

    order = await session.get(Order, intent.order_id, with_for_update=True)
    signal = await session.get(Signal, intent.signal_id, with_for_update=True)
    snapshot = await session.get(ClobMarketSnapshot, fill_clob_snapshot_id)
    if order is None or signal is None or snapshot is None:
        raise ExecutionCostIntegrityError("paper fill source identity is incomplete")
    batch = await session.get(ClobBookBatch, snapshot.batch_id)
    if batch is None:
        raise ExecutionCostIntegrityError("paper fill CLOB batch does not exist")
    quote = purchased_side_quote(snapshot, intent.side, intent.yes_model_probability)
    if (
        quote.token_id != intent.token_id
        or batch.request_started_at < intent.intent_at
        or batch.first_seen_at < intent.intent_at
    ):
        raise ExecutionCostIntegrityError("paper fill does not follow its immutable intent")

    normalized: list[PaperFillLegSpec] = []
    seen_sequences: set[int] = set()
    for raw in legs:
        if raw.sequence <= 0 or raw.sequence in seen_sequences:
            raise ExecutionCostIntegrityError(
                "paper fill leg sequences must be unique and positive"
            )
        seen_sequences.add(raw.sequence)
        if raw.liquidity_role not in {"maker", "taker"}:
            raise ExecutionCostIntegrityError("paper fill liquidity role must be maker or taker")
        shares = _decimal(raw.shares, quantum=_SHARE_QUANTUM, label="fill shares")
        price = _decimal(raw.price, quantum=_PRICE_QUANTUM, label="fill price")
        if price >= 1:
            raise ExecutionCostIntegrityError("fill price must be below one")
        fee = Decimal(raw.fee_usd).quantize(_FEE_QUANTUM, rounding=ROUND_HALF_UP)
        if not fee.is_finite() or fee < 0:
            raise ExecutionCostIntegrityError("fill fee must be finite and non-negative")
        normalized.append(
            PaperFillLegSpec(
                sequence=raw.sequence,
                shares=shares,
                price=price,
                fee_usd=fee,
                liquidity_role=raw.liquidity_role,
                external_leg_key=raw.external_leg_key,
            )
        )
    normalized.sort(key=lambda item: item.sequence)
    if [item.sequence for item in normalized] != list(range(1, len(normalized) + 1)):
        raise ExecutionCostIntegrityError("paper fill leg sequences must be contiguous")

    stored_legs: list[ExecutionFillLeg] = []
    for spec in normalized:
        notional = (spec.shares * spec.price).quantize(_NOTIONAL_QUANTUM, rounding=ROUND_HALF_UP)
        row = ExecutionFillLeg(
            intent_id=intent.id,
            order_id=order.id,
            signal_id=signal.id,
            market_id=intent.market_id,
            reconciliation_observation_id=None,
            fill_clob_snapshot_id=snapshot.id,
            mode=ExecutionMode.PAPER,
            side=intent.side,
            token_id=intent.token_id,
            source_kind="paper_simulation",
            fill_clob_snapshot_hash=snapshot.record_hash,
            external_trade_id=None,
            external_leg_key=(
                spec.external_leg_key or f"paper:{intent.id}:{snapshot.id}:{spec.sequence}"
            ),
            leg_sequence=spec.sequence,
            liquidity_role=spec.liquidity_role,
            shares=spec.shares,
            price=spec.price,
            notional_usd=notional,
            fee_usd=spec.fee_usd,
            fee_semantics="paper_fee_model_v1",
            venue_filled_at=None,
            fill_effective_at=batch.first_seen_at,
            timestamp_semantics="paper_clob_first_seen",
            fill_best_bid=quote.best_bid,
            fill_best_ask=quote.best_ask,
            fill_midpoint=quote.midpoint,
            eligible=True,
            eligibility_reason="eligible",
            definition_version=EXECUTION_COST_DEFINITION_VERSION,
            definition_hash=EXECUTION_COST_DEFINITION_HASH,
            source_hash=batch.record_hash,
            record_hash=_ZERO_HASH,
        )
        session.add(row)
        stored_legs.append(row)
    await session.flush()
    for row in stored_legs:
        await session.refresh(row)

    total_shares = sum((leg.shares for leg in stored_legs), Decimal(0))
    filled_notional = sum((leg.notional_usd for leg in stored_legs), Decimal(0))
    total_fee = sum((leg.fee_usd or Decimal(0) for leg in stored_legs), Decimal(0))
    total_cost = filled_notional + total_fee
    vwap = canonical_weighted_fill_price(
        tuple((leg.shares, leg.price) for leg in stored_legs)
    )
    terminal_status = (
        "filled" if total_cost >= order.size_usd - Decimal("0.0001") else "partially_filled"
    )
    order.status = terminal_status
    # Order.price is the immutable requested/decision limit bound into the
    # dual-CLOB order and execution intent.  Actual execution price belongs in
    # the atomic fill legs, execution seal, and aggregated Position.avg_price.
    order.filled_usd = filled_notional
    order.fee_usd = total_fee
    order.filled_at = batch.first_seen_at
    signal.status = SignalStatus.EXECUTED
    await _aggregate_paper_position(
        session,
        intent=intent,
        avg_price=vwap,
        total_shares=total_shares,
        notional_usd=filled_notional,
        fee_usd=total_fee,
    )
    # The obligation trigger independently reads the terminal legacy order.
    # Flush that projection first; the deferred same-transaction constraint
    # still guarantees that the obligation itself exists before commit.
    await session.flush()
    obligation = ExecutionCostObligation(
        order_id=order.id,
        intent_id=intent.id,
        terminal_status=terminal_status,
        next_due_at=batch.first_seen_at + timedelta(seconds=MARK_HORIZONS_SECONDS[0]),
        state="pending",
        finalized_at=None,
        failure_reason=None,
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
    )
    session.add(obligation)
    await session.flush()
    await session.refresh(obligation)
    await enroll_terminal_order(session, order.id)
    return PaperFillResult(
        order_id=order.id,
        intent_id=intent.id,
        obligation_id=obligation.id,
        leg_ids=tuple(leg.id for leg in stored_legs),
        terminal_status=terminal_status,
        filled_notional_usd=filled_notional,
        total_fee_usd=total_fee,
        total_cost_usd=total_cost,
        total_shares=total_shares,
        vwap_fill_price=vwap,
        filled_at=batch.first_seen_at,
    )


async def record_terminal_no_fill(
    session: AsyncSession,
    intent_id: int,
    *,
    terminal_status: str,
    failure_reason: str | None = None,
) -> ExecutionCostSeal:
    """Atomically terminalize a zero-fill order and append its ineligible seal."""

    if terminal_status not in _NO_FILL_STATUSES:
        raise ExecutionCostIntegrityError("unsupported no-fill terminal status")
    intent = await session.get(ExecutionOrderIntent, intent_id, with_for_update=True)
    if intent is None:
        raise ExecutionCostIntegrityError(f"execution intent {intent_id} does not exist")
    existing = await session.scalar(
        select(ExecutionCostSeal).where(ExecutionCostSeal.intent_id == intent_id)
    )
    if existing is not None:
        detail = existing.reason_detail or {}
        if (
            detail.get("terminal_status") != terminal_status
            or detail.get("failure_reason") != failure_reason
        ):
            raise ExecutionCostIntegrityError(
                "no-fill retry differs from immutable stored evidence"
            )
        return existing
    order = await session.get(Order, intent.order_id, with_for_update=True)
    signal = await session.get(Signal, intent.signal_id, with_for_update=True)
    if order is None or signal is None:
        raise ExecutionCostIntegrityError("no-fill intent lacks legacy order state")
    if await session.scalar(
        select(func.count())
        .select_from(ExecutionFillLeg)
        .where(ExecutionFillLeg.intent_id == intent_id)
    ):
        raise ExecutionCostIntegrityError("no-fill terminalization cannot discard fill legs")
    order.status = terminal_status
    order.filled_usd = Decimal(0)
    order.fee_usd = None
    order.filled_at = None
    if signal.status is SignalStatus.PENDING:
        signal.status = SignalStatus.EXPIRED
        signal.skip_reason = failure_reason or terminal_status
    # See record_paper_fill: the insert trigger reads orders directly and must
    # observe the terminal status before the obligation row is appended.
    await session.flush()
    now = await _clock_timestamp(session)
    obligation = ExecutionCostObligation(
        order_id=order.id,
        intent_id=intent.id,
        terminal_status=terminal_status,
        next_due_at=now,
        state="pending",
        finalized_at=None,
        failure_reason=failure_reason,
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
    )
    session.add(obligation)
    await session.flush()
    seal = ExecutionCostSeal(
        obligation_id=obligation.id,
        intent_id=intent.id,
        order_id=order.id,
        eligible=False,
        eligibility_reason="no_fill",
        reason_detail={"terminal_status": terminal_status, "failure_reason": failure_reason},
        aggregate_role="unknown",
        leg_count=0,
        total_shares=None,
        filled_notional_usd=None,
        vwap_fill_price=None,
        total_fee_usd=None,
        fee_per_share=None,
        intent_midpoint=intent.side_midpoint,
        side_model_probability=intent.side_model_probability,
        weighted_fill_midpoint=None,
        gross_model_edge=None,
        latency_movement=None,
        taker_spread=None,
        taker_slippage=None,
        maker_improvement=None,
        fee_drag=None,
        realized_net_edge=None,
        expected_slippage_per_share=intent.expected_slippage_per_share,
        expected_slippage_coverage=(
            "modeled"
            if intent.expected_slippage_per_share is not None
            else (
                "not_modeled"
                if intent.expected_slippage_unavailable_reason == EXPECTED_SLIPPAGE_NOT_MODELED
                else "unavailable"
            )
        ),
        adverse_selection_300s=None,
        markout_300s_coverage="not_applicable",
        adverse_selection_1800s=None,
        markout_1800s_coverage="not_applicable",
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        record_hash=_ZERO_HASH,
    )
    session.add(seal)
    await session.flush()
    await session.refresh(seal)
    obligation.state = "sealed"
    obligation.next_due_at = None
    await session.flush()
    return seal


async def record_terminal_unverified_live_execution(
    session: AsyncSession,
    intent_id: int,
    observation_id: int,
    *,
    terminal_status: str,
    matched_shares: Decimal,
    avg_price: Decimal | None = None,
    cost_usd: Decimal | None = None,
    fee_usd: Decimal | None = None,
    failure_reason: str | None = "live_schema_unverified",
) -> ExecutionCostSeal:
    """Terminalize a live cumulative fill without overstating its evidence.

    Current SDK payloads can support cumulative shares, VWAP and sometimes a
    fee, but do not yet prove atomic venue trade identity, liquidity role, or
    an authoritative venue fill timestamp.  The resulting aggregate leg is
    therefore explicit ``live_schema_unverified`` evidence with no CLOB mark
    or effective time.  Its obligation is immediately finalized with
    not-applicable markouts rather than manufacturing post-fill clocks.
    """

    raw_status = terminal_status.strip().lower()
    if raw_status not in _LIVE_TERMINAL_STATUSES:
        raise ExecutionCostIntegrityError("unsupported terminal live status")
    intent = await session.get(ExecutionOrderIntent, intent_id, with_for_update=True)
    observation = await session.get(ExecutionReconciliationObservation, observation_id)
    if intent is None or intent.mode is not ExecutionMode.LIVE:
        raise ExecutionCostIntegrityError("terminal live execution requires a live intent")
    if observation is None or observation.order_id != intent.order_id:
        raise ExecutionCostIntegrityError(
            "terminal live execution requires an observation for the same order"
        )
    existing = await session.scalar(
        select(ExecutionCostSeal).where(ExecutionCostSeal.intent_id == intent_id)
    )
    shares = Decimal(matched_shares)
    if not shares.is_finite() or shares < 0:
        raise ExecutionCostIntegrityError("matched live shares must be finite and non-negative")
    shares = shares.quantize(_SHARE_QUANTUM, rounding=ROUND_HALF_UP)
    if shares == 0:
        if raw_status in {"filled", "partially_filled"}:
            raise ExecutionCostIntegrityError("zero-share live execution cannot be filled")
        if avg_price is not None or (cost_usd is not None and Decimal(cost_usd) != Decimal(0)):
            raise ExecutionCostIntegrityError("zero-share live execution cannot claim fill values")
        normalized_status = "canceled" if raw_status == "cancelled" else raw_status
        if existing is not None:
            detail = existing.reason_detail or {}
            if detail.get("terminal_status") != normalized_status:
                raise ExecutionCostIntegrityError(
                    "terminal live retry differs from immutable no-fill evidence"
                )
            return existing
        return await record_terminal_no_fill(
            session,
            intent_id,
            terminal_status=normalized_status,
            failure_reason=failure_reason,
        )

    if raw_status not in {"filled", "partially_filled", "canceled", "cancelled"}:
        raise ExecutionCostIntegrityError(
            "positive live fill must terminate filled, partially_filled, or canceled"
        )
    normalized_status = (
        "partially_filled" if raw_status in {"canceled", "cancelled"} else raw_status
    )
    if avg_price is None or cost_usd is None:
        raise ExecutionCostIntegrityError("positive live fill requires price and cost")
    price = _decimal(avg_price, quantum=_PRICE_QUANTUM, label="live fill price")
    if price >= 1:
        raise ExecutionCostIntegrityError("live fill price must be below one")
    source_cost = Decimal(cost_usd)
    if not source_cost.is_finite() or source_cost <= 0:
        raise ExecutionCostIntegrityError("live fill cost must be finite and positive")
    canonical_notional = (shares * price).quantize(
        _NOTIONAL_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    if abs(source_cost - canonical_notional) > Decimal("0.0001"):
        raise ExecutionCostIntegrityError(
            "live cumulative cost disagrees with matched shares and VWAP"
        )
    provided_fee: Decimal | None
    if fee_usd is None:
        provided_fee = None
    else:
        provided_fee = Decimal(fee_usd).quantize(_FEE_QUANTUM, rounding=ROUND_HALF_UP)
        if not provided_fee.is_finite() or provided_fee < 0:
            raise ExecutionCostIntegrityError("live fill fee must be finite and non-negative")

    stored_leg = await session.scalar(
        select(ExecutionFillLeg).where(ExecutionFillLeg.intent_id == intent.id)
    )
    if existing is not None or stored_leg is not None:
        order = await session.get(Order, intent.order_id)
        if (
            existing is None
            or stored_leg is None
            or order is None
            or stored_leg.reconciliation_observation_id != observation.id
            or stored_leg.shares != shares
            or stored_leg.price != price
            or (provided_fee is not None and stored_leg.fee_usd != provided_fee)
            or order.status != normalized_status
        ):
            raise ExecutionCostIntegrityError(
                "terminal live retry differs from immutable stored evidence"
            )
        return existing

    order = await session.get(Order, intent.order_id, with_for_update=True)
    signal = await session.get(Signal, intent.signal_id, with_for_update=True)
    if order is None or signal is None:
        raise ExecutionCostIntegrityError("terminal live intent lacks legacy order state")
    event_id = await session.scalar(select(Market.event_id).where(Market.id == intent.market_id))
    if event_id is None:
        raise ExecutionCostIntegrityError("terminal live market lacks event identity")
    await lock_gamma_event(session, event_id)
    position = await session.scalar(
        select(Position).where(Position.order_id == order.id).with_for_update()
    )
    if position is not None and position.status is PositionStatus.CLOSED:
        raise ExecutionCostIntegrityError("terminal live position is already closed")
    effective_fee = provided_fee
    if effective_fee is None:
        if position is not None and position.fee_usd is not None:
            effective_fee = Decimal(position.fee_usd).quantize(
                _FEE_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
        elif order.fee_usd is not None:
            effective_fee = Decimal(order.fee_usd).quantize(
                _FEE_QUANTUM,
                rounding=ROUND_HALF_UP,
            )
    canonical_cost = source_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    canonical_position_price = price.quantize(
        Decimal("0.000001"),
        rounding=ROUND_HALF_UP,
    )
    if position is None:
        position = Position(
            market_id=intent.market_id,
            order_id=order.id,
            mode=ExecutionMode.LIVE,
            side=intent.side,
            avg_price=canonical_position_price,
            size_shares=shares,
            size_usd=canonical_cost,
            fee_usd=effective_fee,
            status=PositionStatus.OPEN,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
            token_id=intent.token_id,
        )
        session.add(position)
    else:
        position.avg_price = canonical_position_price
        position.size_shares = shares
        position.size_usd = canonical_cost
        if effective_fee is not None:
            position.fee_usd = effective_fee

    leg = ExecutionFillLeg(
        intent_id=intent.id,
        order_id=order.id,
        signal_id=signal.id,
        market_id=intent.market_id,
        reconciliation_observation_id=observation.id,
        fill_clob_snapshot_id=None,
        mode=ExecutionMode.LIVE,
        side=intent.side,
        token_id=intent.token_id,
        source_kind="live_sdk",
        fill_clob_snapshot_hash=None,
        external_trade_id=None,
        external_leg_key=f"live-unverified-cumulative:{intent.id}",
        leg_sequence=1,
        liquidity_role="unknown",
        shares=shares,
        price=price,
        notional_usd=canonical_notional,
        fee_usd=effective_fee,
        fee_semantics=("venue_reported" if effective_fee is not None else "unknown"),
        venue_filled_at=None,
        fill_effective_at=None,
        timestamp_semantics="unverified",
        fill_best_bid=None,
        fill_best_ask=None,
        fill_midpoint=None,
        eligible=False,
        eligibility_reason="live_schema_unverified",
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        source_hash=observation.record_hash,
        record_hash=_ZERO_HASH,
    )
    session.add(leg)
    order.status = normalized_status
    # Preserve the immutable requested/decision limit on the legacy order.
    # The observed cumulative VWAP is retained by the fill leg, seal, and
    # Position.avg_price instead.
    order.filled_usd = canonical_notional
    order.fee_usd = effective_fee
    # This is explicitly the database-witness clock of the SDK observation,
    # not a claim about venue execution time.
    order.filled_at = observation.first_seen_at
    if signal.status is SignalStatus.PENDING:
        signal.status = SignalStatus.EXECUTED
    await session.flush()
    obligation = ExecutionCostObligation(
        order_id=order.id,
        intent_id=intent.id,
        terminal_status=normalized_status,
        next_due_at=await _clock_timestamp(session),
        state="pending",
        finalized_at=None,
        failure_reason=failure_reason,
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
    )
    session.add(obligation)
    await session.flush()
    await finalize_execution_cost_obligations(session)
    seal = await session.scalar(
        select(ExecutionCostSeal).where(ExecutionCostSeal.intent_id == intent.id)
    )
    if seal is None:
        raise ExecutionCostIntegrityError("terminal live obligation was not finalized")
    await enroll_terminal_order(session, order.id)
    return seal


async def seal_due_execution_markouts(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 250,
) -> MarkoutSealRun:
    """Seal due markouts from the first eligible snapshot in each frozen window.

    Obligations are claimed with ``SKIP LOCKED`` so overlapping scheduler runs
    cannot author competing mark rows.  A missing mark is only appended after
    the database-witness window has fully elapsed.
    """

    if limit <= 0:
        raise ValueError("limit must be positive")
    observed_now = now or await _clock_timestamp(session)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    obligations = list(
        (
            await session.scalars(
                select(ExecutionCostObligation)
                .where(
                    ExecutionCostObligation.state.in_(("pending", "waiting_for_marks")),
                    ExecutionCostObligation.next_due_at <= observed_now,
                    _missing_required_mark_exists(),
                )
                .order_by(
                    ExecutionCostObligation.next_due_at,
                    ExecutionCostObligation.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    marks_observed = 0
    marks_missing = 0
    deferred = 0
    for obligation in obligations:
        legs = list(
            (
                await session.scalars(
                    select(ExecutionFillLeg)
                    .where(
                        ExecutionFillLeg.intent_id == obligation.intent_id,
                        ExecutionFillLeg.fill_effective_at.is_not(None),
                    )
                    .order_by(ExecutionFillLeg.leg_sequence)
                )
            ).all()
        )
        existing = {
            (mark.fill_leg_id, mark.horizon_seconds)
            for mark in (
                await session.scalars(
                    select(ExecutionPostfillMark)
                    .join(
                        ExecutionFillLeg,
                        ExecutionFillLeg.id == ExecutionPostfillMark.fill_leg_id,
                    )
                    .where(ExecutionFillLeg.intent_id == obligation.intent_id)
                )
            ).all()
        }
        next_due: list[datetime] = []
        wrote_for_obligation = False
        for leg in legs:
            if leg.fill_effective_at is None:
                continue
            for horizon in MARK_HORIZONS_SECONDS:
                key = (leg.id, horizon)
                if key in existing:
                    continue
                target_at = leg.fill_effective_at + timedelta(seconds=horizon)
                if target_at > observed_now:
                    next_due.append(target_at)
                    continue
                row = (
                    await session.execute(
                        select(ClobMarketSnapshot, ClobBookBatch)
                        .join(
                            ClobBookBatch,
                            ClobBookBatch.id == ClobMarketSnapshot.batch_id,
                        )
                        .where(
                            ClobMarketSnapshot.market_id == leg.market_id,
                            ClobMarketSnapshot.complete.is_(True),
                            ClobMarketSnapshot.eligible.is_(True),
                            ClobBookBatch.first_seen_at >= target_at,
                            ClobBookBatch.first_seen_at
                            <= target_at + timedelta(seconds=MARK_GRACE_SECONDS),
                            *(
                                (
                                    ClobMarketSnapshot.yes_token_id == leg.token_id,
                                    ClobMarketSnapshot.yes_best_bid.is_not(None),
                                    ClobMarketSnapshot.yes_best_ask.is_not(None),
                                    ClobMarketSnapshot.yes_midpoint.is_not(None),
                                )
                                if leg.side is TradeSide.BUY_YES
                                else (
                                    ClobMarketSnapshot.no_token_id == leg.token_id,
                                    ClobMarketSnapshot.no_best_bid.is_not(None),
                                    ClobMarketSnapshot.no_best_ask.is_not(None),
                                    ClobMarketSnapshot.no_midpoint.is_not(None),
                                )
                            ),
                        )
                        .order_by(
                            ClobBookBatch.first_seen_at,
                            ClobMarketSnapshot.id,
                        )
                        .limit(1)
                    )
                ).one_or_none()
                if row is not None:
                    snapshot, batch = row
                    quote = purchased_side_quote(
                        snapshot,
                        leg.side,
                        Decimal("0.5"),
                    )
                    mark = ExecutionPostfillMark(
                        fill_leg_id=leg.id,
                        horizon_seconds=horizon,
                        target_at=target_at,
                        status="observed",
                        clob_snapshot_id=snapshot.id,
                        clob_snapshot_hash=snapshot.record_hash,
                        missing_reason=None,
                        side_best_bid=quote.best_bid,
                        side_best_ask=quote.best_ask,
                        side_midpoint=quote.midpoint,
                        observed_at=batch.first_seen_at,
                        offset_seconds=floor((batch.first_seen_at - target_at).total_seconds()),
                        definition_version=EXECUTION_COST_DEFINITION_VERSION,
                        definition_hash=EXECUTION_COST_DEFINITION_HASH,
                        record_hash=_ZERO_HASH,
                    )
                    session.add(mark)
                    marks_observed += 1
                    wrote_for_obligation = True
                    continue
                next_collection_at = next_mark_collection_due(
                    target_at=target_at,
                    now=observed_now,
                )
                if next_collection_at is None:
                    session.add(
                        ExecutionPostfillMark(
                            fill_leg_id=leg.id,
                            horizon_seconds=horizon,
                            target_at=target_at,
                            status="missing",
                            clob_snapshot_id=None,
                            clob_snapshot_hash=None,
                            missing_reason="no_eligible_snapshot_in_window",
                            side_best_bid=None,
                            side_best_ask=None,
                            side_midpoint=None,
                            observed_at=None,
                            offset_seconds=None,
                            definition_version=EXECUTION_COST_DEFINITION_VERSION,
                            definition_hash=EXECUTION_COST_DEFINITION_HASH,
                            record_hash=_ZERO_HASH,
                        )
                    )
                    marks_missing += 1
                    wrote_for_obligation = True
                else:
                    next_due.append(next_collection_at)
        await session.flush()
        if next_due:
            obligation.state = "waiting_for_marks"
            obligation.next_due_at = min(next_due)
            deferred += 1
        elif legs:
            obligation.state = "waiting_for_marks"
            obligation.next_due_at = observed_now
        elif not wrote_for_obligation:
            # Live observations with an unverified effective time have no
            # defensible markout target and can be sealed ineligible now.
            obligation.state = "waiting_for_marks"
            obligation.next_due_at = observed_now
    await session.flush()
    return MarkoutSealRun(
        obligations_claimed=len(obligations),
        marks_observed=marks_observed,
        marks_missing=marks_missing,
        obligations_deferred=deferred,
        obligation_ids=tuple(obligation.id for obligation in obligations),
    )


async def finalize_execution_cost_obligations(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 250,
) -> ObligationFinalizationRun:
    """Append immutable order seals once every applicable mark is terminal."""

    if limit <= 0:
        raise ValueError("limit must be positive")
    observed_now = now or await _clock_timestamp(session)
    if observed_now.tzinfo is None or observed_now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    obligations = list(
        (
            await session.scalars(
                select(ExecutionCostObligation)
                .where(
                    ExecutionCostObligation.state.in_(("pending", "waiting_for_marks")),
                    ExecutionCostObligation.next_due_at <= observed_now,
                    ~_missing_required_mark_exists(),
                )
                .order_by(
                    ExecutionCostObligation.next_due_at,
                    ExecutionCostObligation.id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    sealed = 0
    deferred = 0
    for obligation in obligations:
        existing = await session.scalar(
            select(ExecutionCostSeal).where(ExecutionCostSeal.obligation_id == obligation.id)
        )
        if existing is not None:
            obligation.state = "sealed"
            obligation.next_due_at = None
            sealed += 1
            continue
        remaining = await _remaining_mark_due_times(
            session,
            obligation.intent_id,
            now=observed_now,
        )
        if remaining:
            obligation.state = "waiting_for_marks"
            obligation.next_due_at = min(remaining)
            deferred += 1
            continue
        await _append_cost_seal(session, obligation)
        obligation.state = "sealed"
        obligation.next_due_at = None
        sealed += 1
    await session.flush()
    return ObligationFinalizationRun(
        obligations_claimed=len(obligations),
        obligations_sealed=sealed,
        obligations_deferred=deferred,
        obligation_ids=tuple(obligation.id for obligation in obligations),
    )


async def _remaining_mark_due_times(
    session: AsyncSession,
    intent_id: int,
    *,
    now: datetime,
) -> list[datetime]:
    legs = list(
        (
            await session.scalars(
                select(ExecutionFillLeg).where(
                    ExecutionFillLeg.intent_id == intent_id,
                    ExecutionFillLeg.fill_effective_at.is_not(None),
                )
            )
        ).all()
    )
    existing = {
        (mark.fill_leg_id, mark.horizon_seconds)
        for mark in (
            await session.scalars(
                select(ExecutionPostfillMark)
                .join(
                    ExecutionFillLeg,
                    ExecutionFillLeg.id == ExecutionPostfillMark.fill_leg_id,
                )
                .where(ExecutionFillLeg.intent_id == intent_id)
            )
        ).all()
    }
    result: list[datetime] = []
    for leg in legs:
        if leg.fill_effective_at is None:
            continue
        for horizon in MARK_HORIZONS_SECONDS:
            if (leg.id, horizon) not in existing:
                target_at = leg.fill_effective_at + timedelta(seconds=horizon)
                result.append(
                    next_mark_collection_due(
                        target_at=target_at,
                        now=now,
                    )
                    or now
                )
    return result


def _aggregate_role(legs: Sequence[ExecutionFillLeg]) -> str:
    roles = {leg.liquidity_role for leg in legs}
    if not roles or "unknown" in roles:
        return "unknown"
    if len(roles) == 1:
        return next(iter(roles))
    return "mixed"


async def _append_cost_seal(
    session: AsyncSession,
    obligation: ExecutionCostObligation,
) -> ExecutionCostSeal:
    intent = await session.get(ExecutionOrderIntent, obligation.intent_id)
    if intent is None:
        raise ExecutionCostIntegrityError("execution obligation lacks its intent")
    legs = list(
        (
            await session.scalars(
                select(ExecutionFillLeg)
                .where(ExecutionFillLeg.intent_id == intent.id)
                .order_by(ExecutionFillLeg.leg_sequence)
            )
        ).all()
    )
    marks = list(
        (
            await session.scalars(
                select(ExecutionPostfillMark)
                .join(
                    ExecutionFillLeg,
                    ExecutionFillLeg.id == ExecutionPostfillMark.fill_leg_id,
                )
                .where(ExecutionFillLeg.intent_id == intent.id)
            )
        ).all()
    )
    mark_by_leg_horizon = {(mark.fill_leg_id, mark.horizon_seconds): mark for mark in marks}
    aggregate_role = _aggregate_role(legs)
    eligible = bool(legs) and all(leg.eligible and leg.fee_usd is not None for leg in legs)
    if not legs:
        eligibility_reason = "no_fill"
    elif eligible:
        eligibility_reason = "eligible"
    else:
        eligibility_reason = next(
            (
                leg.eligibility_reason
                for leg in sorted(legs, key=lambda item: item.leg_sequence)
                if not leg.eligible
            ),
            "fee_unknown",
        )
    total_shares = sum((leg.shares for leg in legs), Decimal(0)) if legs else None
    filled_notional = sum((leg.notional_usd for leg in legs), Decimal(0)) if legs else None
    vwap = (
        canonical_weighted_fill_price(tuple((leg.shares, leg.price) for leg in legs))
        if legs
        else None
    )
    all_fees_known = bool(legs) and all(leg.fee_usd is not None for leg in legs)
    total_fee = (
        sum((leg.fee_usd or Decimal(0) for leg in legs), Decimal(0)) if all_fees_known else None
    )
    fee_per_share = (
        total_fee / total_shares if total_fee is not None and total_shares is not None else None
    )
    all_mids_known = bool(legs) and all(leg.fill_midpoint is not None for leg in legs)
    weighted_mid = (
        sum(
            (leg.shares * (leg.fill_midpoint or Decimal(0)) for leg in legs),
            Decimal(0),
        )
        / total_shares
        if all_mids_known and total_shares is not None
        else None
    )
    algebra = None
    if eligible:
        algebra = calculate_seal_metrics(
            side_model_probability=intent.side_model_probability,
            intent_midpoint=intent.side_midpoint,
            legs=tuple(
                AlgebraFillLeg(
                    sequence=leg.leg_sequence,
                    liquidity_role=("maker" if leg.liquidity_role == "maker" else "taker"),
                    shares=leg.shares,
                    fill_price=leg.price,
                    fill_midpoint=leg.fill_midpoint or Decimal(0),
                    fill_ask=leg.fill_best_ask or Decimal(0),
                    fee_usd=leg.fee_usd,
                )
                for leg in legs
            ),
            expected_slippage_per_share=intent.expected_slippage_per_share,
            expected_slippage_unavailable_reason=(intent.expected_slippage_unavailable_reason),
        )
    effective_legs = [leg for leg in legs if leg.fill_effective_at is not None]
    mark_values: dict[int, tuple[str, Decimal | None]] = {}
    adverse_values: dict[int, Decimal | None] = {}
    for horizon in MARK_HORIZONS_SECONDS:
        if not legs or not effective_legs:
            mark_values[horizon] = ("not_applicable", None)
            continue
        horizon_marks = [mark_by_leg_horizon.get((leg.id, horizon)) for leg in effective_legs]
        complete = (
            len(effective_legs) == len(legs)
            and all(mark is not None and mark.status == "observed" for mark in horizon_marks)
            and all_mids_known
        )
        if not complete:
            mark_values[horizon] = ("missing", None)
            continue
        post_midpoints = {
            leg.leg_sequence: (mark_by_leg_horizon[(leg.id, horizon)].side_midpoint) for leg in legs
        }
        adverse = calculate_adverse_selection(
            tuple(
                AlgebraFillLeg(
                    sequence=leg.leg_sequence,
                    liquidity_role=(
                        "maker"
                        if leg.liquidity_role == "maker"
                        else ("taker" if leg.liquidity_role == "taker" else "unknown")
                    ),
                    shares=leg.shares,
                    fill_price=leg.price,
                    fill_midpoint=leg.fill_midpoint or Decimal(0),
                    fill_ask=leg.fill_best_ask or leg.price,
                    fee_usd=leg.fee_usd,
                )
                for leg in legs
            ),
            post_midpoints,
        )
        mark_values[horizon] = (adverse.coverage, adverse.adverse_selection)
        adverse_values[horizon] = adverse.adverse_selection
    expected_coverage = (
        "modeled"
        if intent.expected_slippage_per_share is not None
        else (
            "not_modeled"
            if intent.expected_slippage_unavailable_reason == EXPECTED_SLIPPAGE_NOT_MODELED
            else "unavailable"
        )
    )
    seal = ExecutionCostSeal(
        obligation_id=obligation.id,
        intent_id=intent.id,
        order_id=intent.order_id,
        eligible=eligible,
        eligibility_reason=eligibility_reason,
        reason_detail=(
            None
            if eligible
            else {
                "terminal_status": obligation.terminal_status,
                "failure_reason": obligation.failure_reason,
            }
        ),
        aggregate_role=aggregate_role,
        leg_count=len(legs),
        total_shares=total_shares,
        filled_notional_usd=filled_notional,
        vwap_fill_price=vwap,
        total_fee_usd=total_fee,
        fee_per_share=fee_per_share,
        intent_midpoint=intent.side_midpoint,
        side_model_probability=intent.side_model_probability,
        weighted_fill_midpoint=weighted_mid,
        gross_model_edge=algebra.gross_model_edge if algebra else None,
        latency_movement=algebra.latency_movement if algebra else None,
        taker_spread=algebra.taker_spread if algebra else None,
        taker_slippage=algebra.taker_slippage if algebra else None,
        maker_improvement=algebra.maker_improvement if algebra else None,
        fee_drag=algebra.fee_drag if algebra else None,
        realized_net_edge=algebra.realized_net_edge if algebra else None,
        expected_slippage_per_share=intent.expected_slippage_per_share,
        expected_slippage_coverage=expected_coverage,
        adverse_selection_300s=adverse_values.get(300),
        markout_300s_coverage=mark_values.get(300, ("not_applicable", None))[0],
        adverse_selection_1800s=adverse_values.get(1800),
        markout_1800s_coverage=mark_values.get(1800, ("not_applicable", None))[0],
        definition_version=EXECUTION_COST_DEFINITION_VERSION,
        definition_hash=EXECUTION_COST_DEFINITION_HASH,
        record_hash=_ZERO_HASH,
    )
    session.add(seal)
    await session.flush()
    await session.refresh(seal)
    return seal


__all__ = [
    "MarkoutSealRun",
    "ObligationFinalizationRun",
    "PaperFillLegSpec",
    "PaperFillResult",
    "append_live_reconciliation_observation",
    "create_intent_for_order",
    "execution_cost_clock_timestamp",
    "finalize_execution_cost_obligations",
    "record_paper_fill",
    "record_terminal_no_fill",
    "record_terminal_unverified_live_execution",
    "seal_due_execution_markouts",
]
