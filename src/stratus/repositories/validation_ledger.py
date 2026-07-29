from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    EvidenceContinuityLane,
    ExecutionFillLeg,
    ExecutionMode,
    ExecutionOrderIntent,
    GammaOutcomeEvent,
    Market,
    ModelProb,
    Order,
    Position,
    ProspectiveValidationEnrollment,
    ProspectiveValidationOutcome,
    ProspectiveValidationRuntime,
    Signal,
    Station,
    TradeSide,
)
from stratus.repositories.evidence_continuity import current_evidence_continuity_lanes
from stratus.repositories.gamma_outcomes import (
    gamma_closed_at_map,
    gamma_outcome_event_record_is_valid,
    gamma_outcome_map,
    lock_gamma_event,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.execution_cost import canonical_weighted_fill_price
from stratus.services.live_analytics import ProspectiveValidationTrade

VALIDATION_DEFINITION_VERSION = "prospective-validation-v1"
VALIDATION_DEFINITION = (
    "prospective-validation-v1|zero-backfill|clob-dual-v1|order-fill|"
    "gamma-outcome|independent-block:pipeline,station,date,kind"
)
VALIDATION_DEFINITION_HASH = hashlib.sha256(VALIDATION_DEFINITION.encode("utf-8")).hexdigest()
SOURCE_INTEGRITY_LABEL = "database-attested-order-fill+gamma+clob-v1"
_FILLED_STATUSES = frozenset({"filled", "partially_filled"})
_ENROLLMENT_PRICE_QUANTUM = Decimal("0.000001")
_ENROLLMENT_NOTIONAL_QUANTUM = Decimal("0.0001")
_ENROLLMENT_FEE_QUANTUM = Decimal("0.00001")
_ENROLLMENT_SHARE_QUANTUM = Decimal("0.000001")


class ProspectiveValidationIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _FillProjection:
    fill_price: Decimal
    filled_usd: Decimal
    fee_usd: Decimal
    size_shares: Decimal
    evidence_eligible: bool
    fee_complete: bool


async def _execution_fill_projection(
    session: AsyncSession,
    order: Order,
    position: Position | None,
    intent: ExecutionOrderIntent | None,
) -> _FillProjection | None:
    """Project post-0038 fills from their authoritative atomic legs.

    ``Order.price`` is the immutable requested/decision limit.  It is a valid
    legacy fallback, but it is not the execution VWAP once an execution-cost
    intent has atomic fill legs.  Keep every enrollment value at the exact
    precision of its durable prospective-validation column so Python and the
    PostgreSQL attestation hash the same representation.
    """

    if intent is None:
        return None
    legs = list(
        (
            await session.scalars(
                select(ExecutionFillLeg)
                .where(ExecutionFillLeg.intent_id == intent.id)
                .order_by(ExecutionFillLeg.leg_sequence)
            )
        ).all()
    )
    if not legs:
        raise ProspectiveValidationIntegrityError("execution intent has no authoritative fill legs")
    if any(leg.order_id != order.id for leg in legs):
        raise ProspectiveValidationIntegrityError(
            "execution fill projection contains a leg for another order"
        )

    total_shares = sum((Decimal(leg.shares) for leg in legs), Decimal(0)).quantize(
        _ENROLLMENT_SHARE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    filled_usd = sum((Decimal(leg.notional_usd) for leg in legs), Decimal(0)).quantize(
        _ENROLLMENT_NOTIONAL_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    fill_price = canonical_weighted_fill_price(
        tuple((Decimal(leg.shares), Decimal(leg.price)) for leg in legs)
    ).quantize(_ENROLLMENT_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    fee_complete = all(leg.fee_usd is not None for leg in legs)
    if fee_complete:
        fee_usd = sum((Decimal(leg.fee_usd or 0) for leg in legs), Decimal(0)).quantize(
            _ENROLLMENT_FEE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    else:
        fallback_fee = position.fee_usd if position is not None else order.fee_usd
        fee_usd = Decimal(fallback_fee or 0).quantize(
            _ENROLLMENT_FEE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
    return _FillProjection(
        fill_price=fill_price,
        filled_usd=filled_usd,
        fee_usd=fee_usd,
        size_shares=total_shares,
        evidence_eligible=all(leg.eligible for leg in legs),
        fee_complete=fee_complete,
    )


def _json_value(value: object) -> object:
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    """Serialize exactly as ``prospective_validation_canonical_json(jsonb)``.

    Python's JSON encoder writes small and large floats with exponent notation,
    while PostgreSQL jsonb expands those numbers to plain decimal text before
    the validation-ledger hash is calculated.  Serialize recursively so floats
    use that same jsonb number domain without turning them into strings.
    """
    normalized = _json_value(value)
    return _canonical_json_value(normalized)


def _canonical_json_value(value: object) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float is not valid PostgreSQL jsonb")
        decimal_value = Decimal(str(value))
        if decimal_value.is_zero():
            decimal_value = decimal_value.copy_abs()
        return format(decimal_value, "f")
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(str(key), ensure_ascii=False) + ":" + _canonical_json_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            )
            + "}"
        )
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_canonical_json_value(item) for item in value) + "]"
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _record_hash(value: dict[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _pipeline_cohort(components: object) -> str | None:
    if not isinstance(components, dict):
        return None
    pipeline = components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    cohort = pipeline.get("cohort")
    if isinstance(cohort, str) and cohort:
        return cohort
    version = pipeline.get("version")
    return version if isinstance(version, str) and version else None


def _source_hashes(
    order: Order,
    signal: Signal,
    model: ModelProb | None,
) -> tuple[str, str, str | None]:
    order_hash = _record_hash(
        {
            "id": order.id,
            "signal_id": order.signal_id,
            "mode": order.mode,
            "token_id": order.token_id,
            "side": order.side,
            "price": order.price,
            "size_usd": order.size_usd,
            "filled_usd": order.filled_usd,
            "fee_usd": order.fee_usd,
            "status": order.status,
            "created_at": order.created_at,
            "filled_at": order.filled_at,
        }
    )
    signal_hash = _record_hash(
        {
            "id": signal.id,
            "market_id": signal.market_id,
            "ts": signal.ts,
            "side": signal.side,
            "model_prob": signal.model_prob,
            "market_price": signal.market_price,
            "edge": signal.edge,
            "kelly_frac": signal.kelly_frac,
            "size_usd": signal.size_usd,
            "confidence": signal.confidence,
            "clob_snapshot_id": signal.clob_snapshot_id,
            "token_id": signal.token_id,
            "benchmark_protocol": signal.benchmark_protocol,
        }
    )
    model_hash = (
        _record_hash(
            {
                "id": model.id,
                "market_id": model.market_id,
                "ts": model.ts,
                "prob": model.prob,
                "sigma": model.sigma,
                "horizon_h": model.horizon_h,
                "components": model.components,
                "market_snapshot_id": model.market_snapshot_id,
                "market_anchor_protocol": model.market_anchor_protocol,
            }
        )
        if model is not None
        else None
    )
    return order_hash, signal_hash, model_hash


async def enroll_terminal_order(session: AsyncSession, order_id: int) -> int | None:
    """Enroll one newly terminal fill in its transaction; never backfills."""
    runtime = await session.get(ProspectiveValidationRuntime, 1)
    intent = await session.scalar(
        select(ExecutionOrderIntent)
        .where(ExecutionOrderIntent.order_id == order_id)
        .with_for_update()
    )
    order = await session.get(Order, order_id, with_for_update=True)
    if runtime is None or order is None:
        return None
    # A flush writes Numeric values at the database column precision, but the
    # identity-mapped instance may still retain the more precise Decimal that
    # was assigned by the caller.  The enrollment trigger hashes the stored
    # row, so refresh the locked order before validating or hashing it.  This
    # keeps Python and PostgreSQL on the same canonical representation without
    # weakening the database attestation.
    await session.refresh(order, with_for_update=True)
    if (
        order.status not in _FILLED_STATUSES
        or order.filled_at is None
        or order.filled_at < runtime.opened_at
        or order.filled_usd <= 0
    ):
        return None
    existing = await session.scalar(
        select(ProspectiveValidationEnrollment.id).where(
            ProspectiveValidationEnrollment.order_id == order_id
        )
    )
    if existing is not None:
        return existing

    row = (
        await session.execute(
            select(Signal, Market, Station, ClobMarketSnapshot)
            .join(Market, Market.id == Signal.market_id)
            .outerjoin(Station, Station.id == Market.station_id)
            .outerjoin(
                ClobMarketSnapshot,
                ClobMarketSnapshot.id == Signal.clob_snapshot_id,
            )
            .where(Signal.id == order.signal_id)
        )
    ).one_or_none()
    if row is None:
        raise ProspectiveValidationIntegrityError(
            f"order {order_id} has no signal/market provenance"
        )
    signal, market, station, snapshot = row

    # Serialize enrollment with the immutable Gamma outcome projection.  The
    # market was read before taking the event lock solely to discover the lock
    # key, so force a fresh READ COMMITTED read after acquiring it.  Whichever
    # transaction obtains the lock first therefore defines whether this fill
    # was genuinely prospective.
    await lock_gamma_event(session, market.event_id)
    await session.refresh(
        market,
        attribute_names=["resolution_event_id", "resolved_outcome"],
    )
    gamma_outcome_known = (
        await session.scalar(
            select(GammaOutcomeEvent.id)
            .where(GammaOutcomeEvent.event_id == market.event_id)
            .limit(1)
        )
        is not None
    )
    outcome_already_known = (
        gamma_outcome_known
        or market.resolution_event_id is not None
        or market.resolved_outcome is not None
    )
    models = list(
        (
            await session.scalars(
                select(ModelProb)
                .where(
                    ModelProb.market_id == signal.market_id,
                    ModelProb.ts <= signal.ts,
                    ModelProb.market_snapshot_id.is_not_distinct_from(signal.clob_snapshot_id),
                    ModelProb.market_anchor_protocol == signal.benchmark_protocol,
                )
                .order_by(ModelProb.ts.desc(), ModelProb.id.desc())
            )
        ).all()
    )
    model = models[0] if models else None
    position = (
        await session.scalar(select(Position).where(Position.order_id == order.id))
        if order.mode is ExecutionMode.LIVE
        else None
    )
    execution_projection = await _execution_fill_projection(
        session,
        order,
        position,
        intent,
    )
    if execution_projection is not None:
        fill_price = execution_projection.fill_price
        filled_usd = execution_projection.filled_usd
        fee_usd = execution_projection.fee_usd
        size_shares = execution_projection.size_shares
    else:
        fill_price = Decimal(position.avg_price if position is not None else order.price)
        filled_usd = Decimal(position.size_usd if position is not None else order.filled_usd)
        fee_usd = Decimal((position.fee_usd if position is not None else order.fee_usd) or 0)
        size_shares = Decimal(
            position.size_shares if position is not None else filled_usd / fill_price
        ).quantize(_ENROLLMENT_SHARE_QUANTUM)
    candidate_cohorts = [_pipeline_cohort(candidate.components) for candidate in models]
    distinct_cohorts = {
        candidate_cohort for candidate_cohort in candidate_cohorts if candidate_cohort is not None
    }
    cohort = (
        next(iter(distinct_cohorts))
        if len(distinct_cohorts) == 1
        and all(candidate_cohort is not None for candidate_cohort in candidate_cohorts)
        else None
    )
    eligible = True
    reason = "eligible"
    execution_checks: tuple[tuple[bool, str], ...] = ()
    if execution_projection is not None:
        execution_checks = (
            (
                execution_projection.evidence_eligible,
                "execution_evidence_ineligible",
            ),
            (execution_projection.fee_complete, "execution_fee_unknown"),
        )
    checks = (
        (not outcome_already_known, "outcome_already_known"),
        *execution_checks,
        (signal.benchmark_protocol == CLOB_DUAL_PROTOCOL, "non_dual_protocol"),
        (snapshot is not None and snapshot.eligible, "snapshot_ineligible"),
        (model is not None, "model_probability_missing"),
        (
            all(candidate_cohort is not None for candidate_cohort in candidate_cohorts),
            "pipeline_cohort_missing",
        ),
        (len(distinct_cohorts) == 1, "model_anchor_ambiguous"),
        (station is not None, "station_missing"),
        (order.token_id == signal.token_id, "token_mismatch"),
    )
    for passed, failure in checks:
        if not passed:
            eligible = False
            reason = failure
            break
    enrolled_at = await session.scalar(select(func.clock_timestamp()))
    assert enrolled_at is not None
    order_hash, signal_hash, model_hash = _source_hashes(order, signal, model)
    values: dict[str, object] = {
        "order_id": order.id,
        "signal_id": signal.id,
        "model_prob_id": model.id if model is not None else None,
        "market_id": market.id,
        "clob_snapshot_id": snapshot.id if snapshot is not None else None,
        "mode": order.mode,
        "pipeline_cohort": cohort,
        "station_icao": station.icao if station is not None else None,
        "market_date": market.market_date,
        "kind": market.kind,
        "side": order.side,
        "token_id": order.token_id,
        "fill_price": fill_price,
        "filled_usd": filled_usd,
        "fee_usd": fee_usd,
        "size_shares": size_shares,
        "filled_at": order.filled_at,
        "enrolled_at": enrolled_at,
        "benchmark_protocol": signal.benchmark_protocol,
        "definition_version": VALIDATION_DEFINITION_VERSION,
        "definition_hash": VALIDATION_DEFINITION_HASH,
        "order_record_hash": order_hash,
        "signal_record_hash": signal_hash,
        "model_record_hash": model_hash,
        "snapshot_record_hash": snapshot.record_hash if snapshot is not None else None,
        "eligible": eligible,
        "eligibility_reason": reason,
    }
    values["record_hash"] = _record_hash(values)
    result = await session.execute(
        insert(ProspectiveValidationEnrollment)
        .values(**values)
        .on_conflict_do_nothing(constraint="uq_prospective_validation_enrollments_order")
        .returning(ProspectiveValidationEnrollment.id)
    )
    return result.scalar_one_or_none()


def enrollment_record_is_valid(row: ProspectiveValidationEnrollment) -> bool:
    values = {
        key: getattr(row, key)
        for key in (
            "order_id",
            "signal_id",
            "model_prob_id",
            "market_id",
            "clob_snapshot_id",
            "mode",
            "pipeline_cohort",
            "station_icao",
            "market_date",
            "kind",
            "side",
            "token_id",
            "fill_price",
            "filled_usd",
            "fee_usd",
            "size_shares",
            "filled_at",
            "enrolled_at",
            "benchmark_protocol",
            "definition_version",
            "definition_hash",
            "order_record_hash",
            "signal_record_hash",
            "model_record_hash",
            "snapshot_record_hash",
            "eligible",
            "eligibility_reason",
        )
    }
    return row.definition_hash == VALIDATION_DEFINITION_HASH and row.record_hash == _record_hash(
        values
    )


async def seal_prospective_validation_outcomes(
    session: AsyncSession,
    *,
    resolution_event_id: int,
) -> int:
    """Seal pending enrollments only from a valid immutable Gamma event."""
    event = await session.get(GammaOutcomeEvent, resolution_event_id)
    if event is None or not gamma_outcome_event_record_is_valid(event):
        raise ProspectiveValidationIntegrityError("Gamma outcome attestation is invalid")
    outcomes = gamma_outcome_map(event)
    closed_at = gamma_closed_at_map(event)
    enrollments = (
        (
            await session.execute(
                select(ProspectiveValidationEnrollment)
                .join(Market, Market.id == ProspectiveValidationEnrollment.market_id)
                .outerjoin(
                    ProspectiveValidationOutcome,
                    ProspectiveValidationOutcome.enrollment_id
                    == ProspectiveValidationEnrollment.id,
                )
                .where(
                    ProspectiveValidationEnrollment.eligible,
                    Market.resolution_event_id == resolution_event_id,
                    ProspectiveValidationOutcome.id.is_(None),
                )
                .order_by(ProspectiveValidationEnrollment.id)
                .with_for_update(of=ProspectiveValidationEnrollment)
            )
        )
        .scalars()
        .all()
    )
    recognized_at = event.first_seen_at
    written = 0
    for enrollment in enrollments:
        if not enrollment_record_is_valid(enrollment):
            raise ProspectiveValidationIntegrityError(
                f"enrollment {enrollment.id} failed record-hash verification"
            )
        resolved_yes = outcomes.get(enrollment.market_id)
        market_closed_at = closed_at.get(enrollment.market_id)
        if resolved_yes is None or market_closed_at is None:
            raise ProspectiveValidationIntegrityError("Gamma vector misses enrolled market")
        won = (enrollment.side is TradeSide.BUY_YES) == resolved_yes
        pnl = (
            (enrollment.size_shares if won else Decimal(0))
            - enrollment.filled_usd
            - enrollment.fee_usd
        ).quantize(Decimal("0.0001"))
        side_midpoint = (
            ClobMarketSnapshot.yes_midpoint
            if enrollment.side is TradeSide.BUY_YES
            else ClobMarketSnapshot.no_midpoint
        )
        close = (
            await session.execute(
                select(ClobMarketSnapshot, side_midpoint)
                .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
                .where(
                    ClobMarketSnapshot.market_id == enrollment.market_id,
                    ClobMarketSnapshot.eligible,
                    side_midpoint.is_not(None),
                    ClobBookBatch.first_seen_at >= enrollment.filled_at,
                    ClobBookBatch.first_seen_at <= market_closed_at,
                )
                .order_by(ClobBookBatch.first_seen_at.desc(), ClobMarketSnapshot.id.desc())
                .limit(1)
            )
        ).one_or_none()
        close_snapshot = close[0] if close is not None else None
        close_price = Decimal(close[1]) if close is not None else None
        clv = (
            (close_price - enrollment.fill_price).quantize(Decimal("0.000001"))
            if close_price is not None
            else None
        )
        skill = (
            (enrollment.size_shares * clv - enrollment.fee_usd).quantize(Decimal("0.0001"))
            if clv is not None
            else None
        )
        luck = (pnl - skill).quantize(Decimal("0.0001")) if skill is not None else None
        values: dict[str, object] = {
            "enrollment_id": enrollment.id,
            "resolution_event_id": event.id,
            "gamma_record_hash": event.record_hash,
            "resolution_vector_hash": event.outcome_vector_hash,
            "resolved_yes": resolved_yes,
            "won": won,
            "market_closed_at": market_closed_at,
            "recognized_at": recognized_at,
            "pnl_usd": pnl,
            "close_snapshot_id": close_snapshot.id if close_snapshot is not None else None,
            "close_snapshot_hash": (
                close_snapshot.record_hash if close_snapshot is not None else None
            ),
            "close_price": close_price,
            "clv": clv,
            "skill_pnl_usd": skill,
            "luck_pnl_usd": luck,
        }
        values["record_hash"] = _record_hash(values)
        result = await session.execute(
            insert(ProspectiveValidationOutcome)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_prospective_validation_outcomes_enrollment")
            .returning(ProspectiveValidationOutcome.id)
        )
        written += int(result.scalar_one_or_none() is not None)
    return written


def outcome_record_is_valid(row: ProspectiveValidationOutcome) -> bool:
    values = {
        key: getattr(row, key)
        for key in (
            "enrollment_id",
            "resolution_event_id",
            "gamma_record_hash",
            "resolution_vector_hash",
            "resolved_yes",
            "won",
            "market_closed_at",
            "recognized_at",
            "pnl_usd",
            "close_snapshot_id",
            "close_snapshot_hash",
            "close_price",
            "clv",
            "skill_pnl_usd",
            "luck_pnl_usd",
        )
    }
    return row.record_hash == _record_hash(values)


@dataclass(frozen=True, slots=True)
class ProspectiveValidationLaneLineage:
    """The active lane identity against which a snapshot was selected."""

    lane_id: int
    identity_hash: str
    boundary_at: datetime


@dataclass(frozen=True, slots=True)
class ProspectiveValidationDataset:
    opened_at: datetime
    effective_opened_at: datetime
    definition_version: str
    definition_hash: str
    predictive_lineage: ProspectiveValidationLaneLineage | None
    execution_lineage: ProspectiveValidationLaneLineage | None
    enrolled_orders: int
    eligible_enrollments: int
    pending_outcomes: int
    pending_blocks: int
    excluded_enrollments: int
    trades: list[ProspectiveValidationTrade]

    @property
    def formal_order_ids(self) -> tuple[int, ...]:
        """Current-lane, eligible, resolved, block-complete order identities."""

        return tuple(sorted({trade.order_id for trade in self.trades}))


def _side_probability(yes_probability: Decimal, side: TradeSide) -> float:
    value = yes_probability if side is TradeSide.BUY_YES else Decimal(1) - yes_probability
    return float(value)


def _raw_side_model_probability(
    components: object,
    side: TradeSide,
) -> float | None:
    if not isinstance(components, dict):
        return None
    shrinkage = components.get("shrinkage")
    if not isinstance(shrinkage, dict):
        return None
    raw = shrinkage.get("p_model_raw")
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        return None
    value = float(raw)
    if not math.isfinite(value) or not 0 <= value <= 1:
        return None
    return value if side is TradeSide.BUY_YES else 1 - value


def _validation_horizon_bucket(signal_ts: datetime, resolves_at: datetime) -> str:
    hours = (resolves_at - signal_ts).total_seconds() / 3600
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "24-48h"
    return "48h+"


async def load_prospective_validation_dataset(
    session: AsyncSession,
    *,
    mode: ExecutionMode,
    pipeline_cohort: str,
) -> ProspectiveValidationDataset:
    runtime = await session.get(ProspectiveValidationRuntime, 1)
    if runtime is None:
        raise ProspectiveValidationIntegrityError("prospective runtime is missing")
    current_lanes = {
        lane.lane_name: lane for lane in await current_evidence_continuity_lanes(session)
    }
    predictive_boundary = current_lanes.get("predictive_validation")
    execution_boundary = current_lanes.get("execution_evidence")
    if predictive_boundary is None or execution_boundary is None:
        # An installation without an activated continuity epoch has no formal
        # evidence universe.  Return an explicit empty, unscoped snapshot so
        # every downstream gate remains blocked without turning absence into
        # legacy evidence or an optimistic fallback.
        return ProspectiveValidationDataset(
            opened_at=runtime.opened_at,
            effective_opened_at=runtime.opened_at,
            definition_version=runtime.definition_version,
            definition_hash=runtime.definition_hash,
            predictive_lineage=None,
            execution_lineage=None,
            enrolled_orders=0,
            eligible_enrollments=0,
            pending_outcomes=0,
            pending_blocks=0,
            excluded_enrollments=0,
            trades=[],
        )
    enrolled_predictive_lane = aliased(EvidenceContinuityLane)
    enrolled_execution_lane = aliased(EvidenceContinuityLane)
    result = await session.execute(
        select(
            ProspectiveValidationEnrollment,
            ProspectiveValidationOutcome,
            Order,
            ExecutionOrderIntent,
            Signal,
            Market,
            ModelProb,
            ClobMarketSnapshot,
            enrolled_predictive_lane,
            enrolled_execution_lane,
        )
        .join(Order, Order.id == ProspectiveValidationEnrollment.order_id)
        .join(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
        .join(Signal, Signal.id == ProspectiveValidationEnrollment.signal_id)
        .join(Market, Market.id == ProspectiveValidationEnrollment.market_id)
        .outerjoin(ModelProb, ModelProb.id == ProspectiveValidationEnrollment.model_prob_id)
        .outerjoin(
            ClobMarketSnapshot,
            ClobMarketSnapshot.id == ProspectiveValidationEnrollment.clob_snapshot_id,
        )
        .join(
            enrolled_predictive_lane,
            enrolled_predictive_lane.id == ExecutionOrderIntent.predictive_lane_id,
        )
        .join(
            enrolled_execution_lane,
            enrolled_execution_lane.id == ExecutionOrderIntent.execution_lane_id,
        )
        .outerjoin(
            ProspectiveValidationOutcome,
            ProspectiveValidationOutcome.enrollment_id == ProspectiveValidationEnrollment.id,
        )
        .where(
            ProspectiveValidationEnrollment.mode == mode,
            ProspectiveValidationEnrollment.pipeline_cohort == pipeline_cohort,
            ExecutionOrderIntent.pipeline_cohort == pipeline_cohort,
            enrolled_predictive_lane.lane_name == "predictive_validation",
            enrolled_predictive_lane.boundary_at == predictive_boundary.boundary_at,
            enrolled_predictive_lane.identity_hash == predictive_boundary.identity_hash,
            enrolled_execution_lane.lane_name == "execution_evidence",
            enrolled_execution_lane.boundary_at == execution_boundary.boundary_at,
            enrolled_execution_lane.identity_hash == execution_boundary.identity_hash,
        )
        .order_by(ProspectiveValidationEnrollment.id)
    )
    selected = list(result.tuples().all())
    enrolled = len(selected)
    eligible = sum(1 for row in selected if row[0].eligible)
    excluded = sum(1 for row in selected if not row[0].eligible)
    trades_by_block: dict[tuple[str, str, date, str], list[ProspectiveValidationTrade]] = {}
    pending_block_keys: set[tuple[str, str, date, str]] = set()
    for (
        enrollment,
        outcome,
        order,
        intent,
        signal,
        market,
        model,
        snapshot,
        enrolled_predictive,
        enrolled_execution,
    ) in selected:
        if not enrollment_record_is_valid(enrollment):
            raise ProspectiveValidationIntegrityError("enrollment record hash mismatch")
        if not enrollment.eligible:
            continue
        if enrollment.eligibility_reason != "eligible":
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment has a non-eligible reason"
            )
        if model is None or snapshot is None:
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment lacks model or market snapshot evidence"
            )
        if (
            enrollment.pipeline_cohort is None
            or enrollment.station_icao is None
            or snapshot.binary_yes_probability is None
        ):
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment lacks formal scoring provenance"
            )
        if (
            intent.signal_id != enrollment.signal_id
            or intent.model_prob_id != enrollment.model_prob_id
            or intent.market_id != enrollment.market_id
            or intent.decision_clob_snapshot_id != enrollment.clob_snapshot_id
            or intent.mode is not enrollment.mode
            or intent.side is not enrollment.side
            or intent.pipeline_cohort != enrollment.pipeline_cohort
            or signal.market_id != enrollment.market_id
            or market.id != enrollment.market_id
            or model.market_id != enrollment.market_id
            or snapshot.market_id != enrollment.market_id
            or intent.yes_model_probability != signal.model_prob
            or model.prob != signal.model_prob
            or not snapshot.eligible
        ):
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment source identity is inconsistent"
            )
        order_hash, signal_hash, model_hash = _source_hashes(order, signal, model)
        if (
            order_hash != enrollment.order_record_hash
            or signal_hash != enrollment.signal_record_hash
            or model_hash != enrollment.model_record_hash
            or snapshot.record_hash != enrollment.snapshot_record_hash
            or intent.signal_record_hash != enrollment.signal_record_hash
            or intent.model_record_hash != enrollment.model_record_hash
            or intent.snapshot_record_hash != enrollment.snapshot_record_hash
        ):
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment source hash mismatch"
            )
        required_numbers = (
            enrollment.fill_price,
            enrollment.filled_usd,
            enrollment.fee_usd,
            enrollment.size_shares,
            intent.requested_limit_price,
            intent.yes_model_probability,
            intent.side_model_probability,
            snapshot.binary_yes_probability,
            signal.edge,
        )
        if any(not value.is_finite() for value in required_numbers) or (
            enrollment.fill_price <= 0
            or enrollment.fill_price >= 1
            or enrollment.filled_usd <= 0
            or enrollment.fee_usd < 0
            or enrollment.size_shares <= 0
            or intent.requested_limit_price <= 0
            or intent.requested_limit_price >= 1
            or intent.yes_model_probability < 0
            or intent.yes_model_probability > 1
            or intent.side_model_probability < 0
            or intent.side_model_probability > 1
            or snapshot.binary_yes_probability < 0
            or snapshot.binary_yes_probability > 1
        ):
            raise ProspectiveValidationIntegrityError(
                "eligible enrollment has invalid numeric scoring evidence"
            )
        expected_side_model = _side_probability(
            intent.yes_model_probability,
            enrollment.side,
        )
        if abs(expected_side_model - float(intent.side_model_probability)) > 1e-8:
            raise ProspectiveValidationIntegrityError(
                "intent side-model probability is inconsistent"
            )
        block_key = (
            enrollment.pipeline_cohort,
            enrollment.station_icao,
            enrollment.market_date,
            enrollment.kind.value,
        )
        if outcome is None:
            pending_block_keys.add(block_key)
            continue
        if not outcome_record_is_valid(outcome):
            raise ProspectiveValidationIntegrityError("outcome record hash mismatch")
        if (
            not outcome.pnl_usd.is_finite()
            or (outcome.close_price is not None and not outcome.close_price.is_finite())
            or (outcome.clv is not None and not outcome.clv.is_finite())
            or (
                outcome.skill_pnl_usd is not None
                and not outcome.skill_pnl_usd.is_finite()
            )
            or (
                outcome.luck_pnl_usd is not None
                and not outcome.luck_pnl_usd.is_finite()
            )
            or outcome.won
            != ((enrollment.side is TradeSide.BUY_YES) == outcome.resolved_yes)
        ):
            raise ProspectiveValidationIntegrityError(
                "outcome has invalid formal scoring evidence"
            )
        side_market_probability = _side_probability(
            snapshot.binary_yes_probability,
            enrollment.side,
        )
        cost = enrollment.filled_usd + enrollment.fee_usd
        trades_by_block.setdefault(block_key, []).append(
            ProspectiveValidationTrade(
                enrollment_id=enrollment.id,
                order_id=enrollment.order_id,
                pipeline_cohort=enrollment.pipeline_cohort,
                station_icao=enrollment.station_icao,
                market_date=enrollment.market_date,
                kind=enrollment.kind.value,
                recognized_at=outcome.recognized_at,
                pnl_usd=float(outcome.pnl_usd),
                cost_usd=float(cost),
                skill_pnl_usd=(
                    float(outcome.skill_pnl_usd) if outcome.skill_pnl_usd is not None else None
                ),
                luck_pnl_usd=(
                    float(outcome.luck_pnl_usd) if outcome.luck_pnl_usd is not None else None
                ),
                signal_id=enrollment.signal_id,
                market_id=enrollment.market_id,
                polymarket_id=market.polymarket_id,
                side=enrollment.side.value,
                signal_ts=signal.ts,
                resolves_at=market.resolves_at,
                confidence=signal.confidence.value,
                horizon_bucket=_validation_horizon_bucket(signal.ts, market.resolves_at),
                requested_limit_price=float(intent.requested_limit_price),
                fill_vwap=float(enrollment.fill_price),
                filled_notional_usd=float(enrollment.filled_usd),
                fee_usd=float(enrollment.fee_usd),
                fee_complete=True,
                size_shares=float(enrollment.size_shares),
                yes_model_prob=float(intent.yes_model_probability),
                model_prob=float(intent.side_model_probability),
                yes_market_prob=float(snapshot.binary_yes_probability),
                market_prob=side_market_probability,
                raw_model_prob=_raw_side_model_probability(model.components, enrollment.side),
                predicted_edge=float(signal.edge),
                resolved_yes=outcome.resolved_yes,
                won=outcome.won,
                market_closed_at=outcome.market_closed_at,
                close_price=(
                    float(outcome.close_price) if outcome.close_price is not None else None
                ),
                clv=float(outcome.clv) if outcome.clv is not None else None,
                predictive_lane_id=enrolled_predictive.id,
                predictive_lane_identity_hash=enrolled_predictive.identity_hash,
                predictive_lane_boundary_at=enrolled_predictive.boundary_at,
                execution_lane_id=enrolled_execution.id,
                execution_lane_identity_hash=enrolled_execution.identity_hash,
                execution_lane_boundary_at=enrolled_execution.boundary_at,
            )
        )
    trades = [
        trade
        for block_key, members in trades_by_block.items()
        if block_key not in pending_block_keys
        for trade in members
    ]
    return ProspectiveValidationDataset(
        opened_at=runtime.opened_at,
        effective_opened_at=max(
            runtime.opened_at,
            predictive_boundary.boundary_at,
            execution_boundary.boundary_at,
        ),
        definition_version=runtime.definition_version,
        definition_hash=runtime.definition_hash,
        predictive_lineage=ProspectiveValidationLaneLineage(
            lane_id=predictive_boundary.id,
            identity_hash=predictive_boundary.identity_hash,
            boundary_at=predictive_boundary.boundary_at,
        ),
        execution_lineage=ProspectiveValidationLaneLineage(
            lane_id=execution_boundary.id,
            identity_hash=execution_boundary.identity_hash,
            boundary_at=execution_boundary.boundary_at,
        ),
        enrolled_orders=enrolled,
        eligible_enrollments=eligible,
        pending_outcomes=sum(
            1 for row in selected if row[0].eligible and row[1] is None
        ),
        pending_blocks=len(pending_block_keys),
        excluded_enrollments=excluded,
        trades=trades,
    )
