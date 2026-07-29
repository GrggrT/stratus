from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from enum import Enum
from typing import Any, Literal

from stratus.db.models import ClobMarketSnapshot, TradeSide

EXECUTION_COST_DEFINITION_VERSION = "execution-cost-v1"
PAPER_EXECUTION_EVIDENCE = "paper-clob-cross-v1"
LIVE_RECONCILIATION_EVIDENCE = "live-sdk-reconciliation-v1"
POSTFILL_MARK_PROTOCOL = "clob-dual-postfill-v1"
MARK_HORIZONS_SECONDS = (300, 1800)
MARK_GRACE_SECONDS = 300
LIVE_FILL_SNAPSHOT_MAX_AGE_SECONDS = 120
COST_IDENTITY_TOLERANCE = Decimal("0.000000000001")
SHARE_QUANTUM = Decimal("0.000001")
WALK_CASH_TOLERANCE = Decimal("0.000001")

EXECUTION_COST_DEFINITION_PAYLOAD = {
    "definition_version": EXECUTION_COST_DEFINITION_VERSION,
    "paper_execution_evidence": PAPER_EXECUTION_EVIDENCE,
    "live_reconciliation_evidence": LIVE_RECONCILIATION_EVIDENCE,
    "postfill_mark_protocol": POSTFILL_MARK_PROTOCOL,
    "mark_horizons_seconds": list(MARK_HORIZONS_SECONDS),
    "mark_grace_seconds": MARK_GRACE_SECONDS,
    "live_fill_snapshot_max_age_seconds": LIVE_FILL_SNAPSHOT_MAX_AGE_SECONDS,
}
EXECUTION_COST_DEFINITION = json.dumps(
    EXECUTION_COST_DEFINITION_PAYLOAD,
    ensure_ascii=True,
    sort_keys=True,
    separators=(",", ":"),
)
EXECUTION_COST_DEFINITION_HASH = hashlib.sha256(
    EXECUTION_COST_DEFINITION.encode("utf-8")
).hexdigest()


def next_mark_collection_due(
    *,
    target_at: datetime,
    now: datetime,
) -> datetime | None:
    """Return the next collection attempt, or ``None`` once grace is exhausted.

    A due horizon is polled throughout its grace window so a snapshot that
    arrives after the exact target can still be sealed promptly.  The caller
    must check the complete target-to-grace snapshot window before treating
    ``None`` as missing.
    """

    if target_at.tzinfo is None or target_at.utcoffset() is None:
        raise ValueError("target_at must be timezone-aware")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if now < target_at:
        return target_at
    grace_at = target_at + timedelta(seconds=MARK_GRACE_SECONDS)
    if now >= grace_at:
        return None
    # Keep the horizon immediately eligible for the next composite scheduler
    # invocation.  Adding the nominal scheduler interval here can make that
    # next invocation arrive a few milliseconds before ``next_due_at`` and
    # defer collection for a second interval.
    return now

EXPECTED_SLIPPAGE_NOT_MODELED = "expected_slippage_not_modeled"
EXPECTED_SLIPPAGE_MODELED = "modeled"
EXPECTED_SLIPPAGE_MISSING = "missing"
FEE_COVERAGE_COMPLETE = "complete"
FEE_COVERAGE_MISSING = "missing"
MARKOUT_COVERAGE_COMPLETE = "complete"
MARKOUT_COVERAGE_MISSING = "missing"

LiquidityRole = Literal["maker", "taker", "unknown"]
AggregateLiquidityRole = Literal["maker", "taker", "mixed", "unknown"]


class ExecutionCostIntegrityError(ValueError):
    """Raised when evidence cannot support the frozen execution-cost algebra."""


@dataclass(frozen=True, slots=True)
class PurchasedSideQuote:
    token_id: str
    yes_model_probability: Decimal
    side_model_probability: Decimal
    best_bid: Decimal
    best_ask: Decimal
    midpoint: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionFillLeg:
    sequence: int
    liquidity_role: LiquidityRole
    shares: Decimal
    fill_price: Decimal
    fill_midpoint: Decimal
    fill_ask: Decimal
    fee_usd: Decimal | None

    @property
    def role(self) -> LiquidityRole:
        return self.liquidity_role


@dataclass(frozen=True, slots=True)
class ExecutionSealMetrics:
    aggregate_role: AggregateLiquidityRole
    leg_count: int
    total_shares: Decimal
    filled_notional_usd: Decimal
    vwap_fill_price: Decimal
    total_fee_usd: Decimal | None
    fee_per_share: Decimal | None
    fee_coverage: str
    intent_midpoint: Decimal
    side_model_probability: Decimal
    weighted_fill_midpoint: Decimal
    gross_model_edge: Decimal
    latency_movement: Decimal
    taker_spread: Decimal
    taker_slippage: Decimal
    maker_improvement: Decimal
    fee_drag: Decimal | None
    realized_net_edge: Decimal | None
    direct_net_edge: Decimal | None
    identity_residual: Decimal | None
    expected_slippage_per_share: Decimal | None
    expected_slippage_coverage: str
    expected_slippage_unavailable_reason: str | None

    @property
    def filled_notional(self) -> Decimal:
        return self.filled_notional_usd

    @property
    def identity_holds(self) -> bool | None:
        if self.identity_residual is None:
            return None
        return abs(self.identity_residual) <= COST_IDENTITY_TOLERANCE


@dataclass(frozen=True, slots=True)
class MarkoutMetric:
    adverse_selection: Decimal | None
    coverage: str
    observed_leg_count: int
    total_leg_count: int


@dataclass(frozen=True, slots=True)
class TakerWalkLeg:
    sequence: int
    shares: Decimal
    price: Decimal
    notional_usd: Decimal
    fee_usd: Decimal
    total_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class TakerWalk:
    legs: tuple[TakerWalkLeg, ...]
    requested_notional_usd: Decimal
    filled_notional_usd: Decimal
    total_fee_usd: Decimal
    total_cost_usd: Decimal
    unfilled_usd: Decimal
    fully_filled: bool


def _require_probability(value: Decimal, label: str) -> Decimal:
    if not value.is_finite() or not Decimal(0) < value < Decimal(1):
        raise ExecutionCostIntegrityError(f"{label} must be finite and strictly between 0 and 1")
    return value


def purchased_side_probability(
    yes_model_probability: Decimal,
    side: TradeSide,
) -> Decimal:
    yes_probability = _require_probability(
        Decimal(yes_model_probability), "YES model probability"
    )
    if side is TradeSide.BUY_YES:
        return yes_probability
    if side is TradeSide.BUY_NO:
        return Decimal(1) - yes_probability
    raise ExecutionCostIntegrityError(f"unsupported trade side: {side!r}")


def purchased_side_quote(
    snapshot: ClobMarketSnapshot,
    side: TradeSide,
    yes_model_probability: Decimal,
) -> PurchasedSideQuote:
    """Project intent evidence into the independently observed purchased token.

    BUY_NO deliberately reads the NO token fields.  Missing NO evidence is an
    integrity failure; the YES complement is never used as a quote fallback.
    """

    if side is TradeSide.BUY_YES:
        token_id = snapshot.yes_token_id
        bid = snapshot.yes_best_bid
        ask = snapshot.yes_best_ask
        midpoint = snapshot.yes_midpoint
    elif side is TradeSide.BUY_NO:
        token_id = snapshot.no_token_id
        bid = snapshot.no_best_bid
        ask = snapshot.no_best_ask
        midpoint = snapshot.no_midpoint
    else:
        raise ExecutionCostIntegrityError(f"unsupported trade side: {side!r}")

    if not token_id or token_id != token_id.strip():
        raise ExecutionCostIntegrityError("purchased token id is missing or malformed")
    if bid is None or ask is None or midpoint is None:
        raise ExecutionCostIntegrityError(
            "purchased-token decision snapshot must contain a two-sided book"
        )
    bid = _require_probability(Decimal(bid), "purchased-side best bid")
    ask = _require_probability(Decimal(ask), "purchased-side best ask")
    midpoint = _require_probability(Decimal(midpoint), "purchased-side midpoint")
    if bid >= ask:
        raise ExecutionCostIntegrityError("purchased-token decision book is crossed")
    if not bid <= midpoint <= ask:
        raise ExecutionCostIntegrityError(
            "purchased-side midpoint must lie between best bid and best ask"
        )
    yes_probability = _require_probability(
        Decimal(yes_model_probability), "YES model probability"
    )
    return PurchasedSideQuote(
        token_id=token_id,
        yes_model_probability=yes_probability,
        side_model_probability=purchased_side_probability(yes_probability, side),
        best_bid=bid,
        best_ask=ask,
        midpoint=midpoint,
    )


def walk_taker_ask_levels(
    asks: Sequence[tuple[Decimal, Decimal]],
    *,
    target_notional_usd: Decimal,
    fee_fn: Callable[[Decimal, Decimal], Decimal],
) -> TakerWalk:
    """Walk purchased-token asks into deterministic, six-decimal atomic legs.

    ``target_notional_usd`` retains the legacy executor's name, but is the
    maximum cash budget: every leg's notional plus its exact fee must fit.  A
    fee callback keeps the walk independent of a venue-specific fee formula.
    The last leg is selected at six-decimal share precision by monotone binary
    search, so rounded fee schedules cannot overspend the requested budget.
    """

    target = Decimal(target_notional_usd)
    if not target.is_finite() or target <= 0:
        raise ExecutionCostIntegrityError(
            "taker target notional must be finite and positive"
        )

    normalized: list[tuple[Decimal, int, Decimal]] = []
    for source_index, (raw_price, raw_shares) in enumerate(asks):
        price = _require_probability(Decimal(raw_price), "taker ask price")
        shares = Decimal(raw_shares)
        if not shares.is_finite() or shares <= 0:
            raise ExecutionCostIntegrityError(
                "taker ask shares must be finite and positive"
            )
        canonical_shares = shares.quantize(SHARE_QUANTUM, rounding=ROUND_FLOOR)
        if canonical_shares > 0:
            normalized.append((price, source_index, canonical_shares))
    normalized.sort(key=lambda item: (item[0], item[1]))

    def exact_fee(shares: Decimal, price: Decimal) -> Decimal:
        fee = Decimal(fee_fn(shares, price))
        if not fee.is_finite() or fee < 0:
            raise ExecutionCostIntegrityError(
                "taker fee callback must return a finite non-negative Decimal"
            )
        return fee

    def leg_values(
        shares: Decimal,
        price: Decimal,
    ) -> tuple[Decimal, Decimal, Decimal]:
        notional = shares * price
        fee = exact_fee(shares, price)
        return notional, fee, notional + fee

    legs: list[TakerWalkLeg] = []
    spent = Decimal(0)
    for price, _, available_shares in normalized:
        remaining = target - spent
        if remaining <= WALK_CASH_TOLERANCE:
            break
        full_notional, full_fee, full_cost = leg_values(available_shares, price)
        if full_cost <= remaining:
            selected_shares = available_shares
            notional, fee, cost = full_notional, full_fee, full_cost
        else:
            maximum_units = int(available_shares / SHARE_QUANTUM)
            low = 0
            high = maximum_units
            while low < high:
                candidate_units = (low + high + 1) // 2
                candidate_shares = SHARE_QUANTUM * candidate_units
                _, _, candidate_cost = leg_values(candidate_shares, price)
                if candidate_cost <= remaining:
                    low = candidate_units
                else:
                    high = candidate_units - 1
            if low == 0:
                continue
            selected_shares = SHARE_QUANTUM * low
            notional, fee, cost = leg_values(selected_shares, price)
        legs.append(
            TakerWalkLeg(
                sequence=len(legs) + 1,
                shares=selected_shares,
                price=price,
                notional_usd=notional,
                fee_usd=fee,
                total_cost_usd=cost,
            )
        )
        spent += cost

    filled_notional = sum(
        (leg.notional_usd for leg in legs), start=Decimal(0)
    )
    total_fee = sum((leg.fee_usd for leg in legs), start=Decimal(0))
    total_cost = filled_notional + total_fee
    unfilled = target - total_cost
    if unfilled < 0:
        raise ExecutionCostIntegrityError("taker walk exceeded its cash budget")
    return TakerWalk(
        legs=tuple(legs),
        requested_notional_usd=target,
        filled_notional_usd=filled_notional,
        total_fee_usd=total_fee,
        total_cost_usd=total_cost,
        unfilled_usd=unfilled,
        fully_filled=unfilled <= WALK_CASH_TOLERANCE,
    )


def _validated_legs(legs: Sequence[ExecutionFillLeg]) -> tuple[ExecutionFillLeg, ...]:
    if not legs:
        raise ExecutionCostIntegrityError("execution cost requires at least one fill leg")
    normalized = tuple(sorted(legs, key=lambda leg: leg.sequence))
    sequences = [leg.sequence for leg in normalized]
    if any(sequence <= 0 for sequence in sequences) or len(set(sequences)) != len(sequences):
        raise ExecutionCostIntegrityError("fill leg sequences must be unique positive integers")
    for leg in normalized:
        if leg.liquidity_role not in {"maker", "taker", "unknown"}:
            raise ExecutionCostIntegrityError(
                f"unsupported liquidity role: {leg.liquidity_role!r}"
            )
        if not leg.shares.is_finite() or leg.shares <= 0:
            raise ExecutionCostIntegrityError("fill shares must be finite and positive")
        _require_probability(leg.fill_price, "fill price")
        _require_probability(leg.fill_midpoint, "fill midpoint")
        _require_probability(leg.fill_ask, "fill ask")
        if leg.fee_usd is not None and (
            not leg.fee_usd.is_finite() or leg.fee_usd < 0
        ):
            raise ExecutionCostIntegrityError("fill fee must be finite and non-negative")
    return normalized


def aggregate_liquidity_role(
    legs: Sequence[ExecutionFillLeg],
) -> AggregateLiquidityRole:
    normalized = _validated_legs(legs)
    roles = {leg.liquidity_role for leg in normalized}
    if "unknown" in roles:
        return "unknown"
    if roles == {"maker", "taker"}:
        return "mixed"
    if roles == {"maker"}:
        return "maker"
    if roles == {"taker"}:
        return "taker"
    raise ExecutionCostIntegrityError("fill legs have no supported liquidity role")


def canonical_weighted_fill_price(
    legs: Sequence[tuple[Decimal, Decimal]],
) -> Decimal:
    """Return the exact share-weighted price of canonical stored fill legs.

    ``execution_fill_legs.notional_usd`` intentionally stores each product at
    six-decimal USD precision.  Dividing those independently rounded notionals
    by total shares can move the result by more than the seal protocol's
    5e-9 price tolerance.  PostgreSQL validates VWAP from the canonical leg
    columns themselves, so every Python projection must use the same
    ``sum(shares * price) / sum(shares)`` identity.
    """

    if not legs:
        raise ExecutionCostIntegrityError("fill VWAP requires at least one leg")
    total_shares = Decimal(0)
    exact_notional = Decimal(0)
    for raw_shares, raw_price in legs:
        shares = Decimal(raw_shares)
        price = Decimal(raw_price)
        if not shares.is_finite() or shares <= 0:
            raise ExecutionCostIntegrityError(
                "fill VWAP shares must be finite and positive"
            )
        _require_probability(price, "fill VWAP price")
        total_shares += shares
        exact_notional += shares * price
    return exact_notional / total_shares


def calculate_seal_metrics(
    *,
    side_model_probability: Decimal,
    intent_midpoint: Decimal,
    legs: Sequence[ExecutionFillLeg],
    expected_slippage_per_share: Decimal | None = None,
    expected_slippage_unavailable_reason: str | None = EXPECTED_SLIPPAGE_NOT_MODELED,
) -> ExecutionSealMetrics:
    """Apply the frozen purchased-token algebra without floating-point coercion.

    An unknown liquidity role cannot support the decomposition and is rejected.
    An unknown fee remains null: pre-fee terms are retained, while fee drag and
    both net-edge representations remain unavailable rather than becoming zero.
    """

    probability = _require_probability(
        Decimal(side_model_probability), "purchased-side model probability"
    )
    midpoint0 = _require_probability(Decimal(intent_midpoint), "intent midpoint")
    normalized = _validated_legs(legs)
    role = aggregate_liquidity_role(normalized)
    if role == "unknown":
        raise ExecutionCostIntegrityError(
            "unknown liquidity role cannot support execution-cost decomposition"
        )

    if expected_slippage_per_share is None:
        reason = (expected_slippage_unavailable_reason or "").strip()
        if not reason:
            raise ExecutionCostIntegrityError(
                "missing expected slippage requires an unavailable reason"
            )
        expected_coverage = EXPECTED_SLIPPAGE_MISSING
        expected_reason: str | None = reason
    else:
        if (
            not expected_slippage_per_share.is_finite()
            or expected_slippage_per_share < 0
        ):
            raise ExecutionCostIntegrityError(
                "modeled expected slippage must be finite and non-negative"
            )
        if expected_slippage_unavailable_reason not in {
            None,
            "",
            EXPECTED_SLIPPAGE_NOT_MODELED,
        }:
            raise ExecutionCostIntegrityError(
                "modeled expected slippage cannot have an unavailable reason"
            )
        expected_coverage = EXPECTED_SLIPPAGE_MODELED
        expected_reason = None

    total_shares = sum((leg.shares for leg in normalized), start=Decimal(0))
    filled_notional = sum(
        (leg.shares * leg.fill_price for leg in normalized), start=Decimal(0)
    )
    vwap_fill_price = canonical_weighted_fill_price(
        tuple((leg.shares, leg.fill_price) for leg in normalized)
    )
    weighted_fill_midpoint = (
        sum(
            (leg.shares * leg.fill_midpoint for leg in normalized),
            start=Decimal(0),
        )
        / total_shares
    )
    gross_model_edge = probability - midpoint0
    latency_movement = (
        sum(
            (leg.shares * (leg.fill_midpoint - midpoint0) for leg in normalized),
            start=Decimal(0),
        )
        / total_shares
    )
    taker_spread = (
        sum(
            (
                leg.shares * (leg.fill_ask - leg.fill_midpoint)
                for leg in normalized
                if leg.liquidity_role == "taker"
            ),
            start=Decimal(0),
        )
        / total_shares
    )
    taker_slippage = (
        sum(
            (
                leg.shares * (leg.fill_price - leg.fill_ask)
                for leg in normalized
                if leg.liquidity_role == "taker"
            ),
            start=Decimal(0),
        )
        / total_shares
    )
    maker_improvement = (
        sum(
            (
                leg.shares * (leg.fill_midpoint - leg.fill_price)
                for leg in normalized
                if leg.liquidity_role == "maker"
            ),
            start=Decimal(0),
        )
        / total_shares
    )

    fees = [leg.fee_usd for leg in normalized]
    if any(fee is None for fee in fees):
        total_fee_usd = None
        fee_per_share = None
        fee_drag = None
        realized_net_edge = None
        direct_net_edge = None
        identity_residual = None
        fee_coverage = FEE_COVERAGE_MISSING
    else:
        total_fee_usd = sum(
            (fee for fee in fees if fee is not None), start=Decimal(0)
        )
        fee_per_share = total_fee_usd / total_shares
        fee_drag = fee_per_share
        decomposed = (
            gross_model_edge
            - latency_movement
            - taker_spread
            - taker_slippage
            + maker_improvement
            - fee_drag
        )
        direct_net_edge = probability - vwap_fill_price - fee_drag
        identity_residual = decomposed - direct_net_edge
        if abs(identity_residual) > COST_IDENTITY_TOLERANCE:
            raise ExecutionCostIntegrityError(
                "execution-cost decomposition violates the frozen net-edge identity"
            )
        realized_net_edge = direct_net_edge
        fee_coverage = FEE_COVERAGE_COMPLETE

    return ExecutionSealMetrics(
        aggregate_role=role,
        leg_count=len(normalized),
        total_shares=total_shares,
        filled_notional_usd=filled_notional,
        vwap_fill_price=vwap_fill_price,
        total_fee_usd=total_fee_usd,
        fee_per_share=fee_per_share,
        fee_coverage=fee_coverage,
        intent_midpoint=midpoint0,
        side_model_probability=probability,
        weighted_fill_midpoint=weighted_fill_midpoint,
        gross_model_edge=gross_model_edge,
        latency_movement=latency_movement,
        taker_spread=taker_spread,
        taker_slippage=taker_slippage,
        maker_improvement=maker_improvement,
        fee_drag=fee_drag,
        realized_net_edge=realized_net_edge,
        direct_net_edge=direct_net_edge,
        identity_residual=identity_residual,
        expected_slippage_per_share=expected_slippage_per_share,
        expected_slippage_coverage=expected_coverage,
        expected_slippage_unavailable_reason=expected_reason,
    )


def calculate_adverse_selection(
    legs: Sequence[ExecutionFillLeg],
    post_midpoints_by_sequence: Mapping[int, Decimal | None],
) -> MarkoutMetric:
    """Calculate one all-or-nothing order-level markout in purchased-token terms.

    Positive values mean the purchased token moved down after the fill and the
    execution therefore experienced adverse selection.  A missing mark for any
    leg makes the metric null; partial coverage is never silently averaged.
    """

    normalized = _validated_legs(legs)
    expected_sequences = {leg.sequence for leg in normalized}
    extra_sequences = set(post_midpoints_by_sequence) - expected_sequences
    if extra_sequences:
        raise ExecutionCostIntegrityError(
            f"post-fill marks contain unknown leg sequences: {sorted(extra_sequences)}"
        )
    observed: dict[int, Decimal] = {}
    for leg in normalized:
        value = post_midpoints_by_sequence.get(leg.sequence)
        if value is None:
            continue
        observed[leg.sequence] = _require_probability(
            Decimal(value), "post-fill midpoint"
        )
    if len(observed) != len(normalized):
        return MarkoutMetric(
            adverse_selection=None,
            coverage=MARKOUT_COVERAGE_MISSING,
            observed_leg_count=len(observed),
            total_leg_count=len(normalized),
        )
    total_shares = sum((leg.shares for leg in normalized), start=Decimal(0))
    adverse_selection = (
        sum(
            (
                leg.shares * (leg.fill_midpoint - observed[leg.sequence])
                for leg in normalized
            ),
            start=Decimal(0),
        )
        / total_shares
    )
    return MarkoutMetric(
        adverse_selection=adverse_selection,
        coverage=MARKOUT_COVERAGE_COMPLETE,
        observed_leg_count=len(normalized),
        total_leg_count=len(normalized),
    )


def _postgres_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ExecutionCostIntegrityError("record timestamps must be timezone-aware")
    rendered = value.astimezone(UTC).isoformat()
    if "." not in rendered:
        return rendered
    prefix, suffix = rendered.split(".", 1)
    fraction, offset = suffix.split("+", 1)
    fraction = fraction.rstrip("0")
    return f"{prefix}{'.' + fraction if fraction else ''}+{offset}"


def postgres_jsonb_text(value: object) -> str:
    """Mirror PostgreSQL ``jsonb::text`` for the evidence scalar universe."""

    if isinstance(value, Enum):
        return postgres_jsonb_text(value.value)
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("JSONB object keys must be strings")
        keys = sorted(
            value,
            key=lambda item: (len(item.encode("utf-8")), item.encode("utf-8")),
        )
        return "{" + ", ".join(
            f"{json.dumps(key, ensure_ascii=False)}: {postgres_jsonb_text(value[key])}"
            for key in keys
        ) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(postgres_jsonb_text(item) for item in value) + "]"
    if isinstance(value, datetime):
        return json.dumps(_postgres_timestamp(value))
    if isinstance(value, date):
        return json.dumps(value.isoformat())
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ExecutionCostIntegrityError("JSONB numeric must be finite")
        return format(value, "f")
    if isinstance(value, float):
        raise TypeError("execution evidence hashes require Decimal, not float")
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def source_payload_hash(value: object) -> str:
    return hashlib.sha256(postgres_jsonb_text(value).encode("utf-8")).hexdigest()


def execution_record_hash(
    record_kind: str,
    values: Mapping[str, object],
    *,
    definition_hash: str = EXECUTION_COST_DEFINITION_HASH,
) -> str:
    """Hash one 0038 record with an explicit kind and frozen definition identity."""

    kind = record_kind.strip()
    if not kind:
        raise ExecutionCostIntegrityError("execution record kind is required")
    if len(definition_hash) != 64 or any(
        character not in "0123456789abcdef" for character in definition_hash
    ):
        raise ExecutionCostIntegrityError(
            "execution definition hash must be lower-case SHA-256"
        )
    if "record_kind" in values or "definition_hash" in values:
        raise ExecutionCostIntegrityError(
            "record values cannot override hash-domain identity"
        )
    payload: dict[str, Any] = {
        "record_kind": kind,
        "definition_hash": definition_hash,
        **values,
    }
    return source_payload_hash(payload)


__all__ = [
    "COST_IDENTITY_TOLERANCE",
    "EXECUTION_COST_DEFINITION",
    "EXECUTION_COST_DEFINITION_HASH",
    "EXECUTION_COST_DEFINITION_PAYLOAD",
    "EXECUTION_COST_DEFINITION_VERSION",
    "EXPECTED_SLIPPAGE_MISSING",
    "EXPECTED_SLIPPAGE_MODELED",
    "EXPECTED_SLIPPAGE_NOT_MODELED",
    "ExecutionCostIntegrityError",
    "ExecutionFillLeg",
    "ExecutionSealMetrics",
    "FEE_COVERAGE_COMPLETE",
    "FEE_COVERAGE_MISSING",
    "LIVE_FILL_SNAPSHOT_MAX_AGE_SECONDS",
    "LIVE_RECONCILIATION_EVIDENCE",
    "MARKOUT_COVERAGE_COMPLETE",
    "MARKOUT_COVERAGE_MISSING",
    "MARK_GRACE_SECONDS",
    "MARK_HORIZONS_SECONDS",
    "MarkoutMetric",
    "PAPER_EXECUTION_EVIDENCE",
    "POSTFILL_MARK_PROTOCOL",
    "PurchasedSideQuote",
    "SHARE_QUANTUM",
    "TakerWalk",
    "TakerWalkLeg",
    "WALK_CASH_TOLERANCE",
    "aggregate_liquidity_role",
    "calculate_adverse_selection",
    "calculate_seal_metrics",
    "execution_record_hash",
    "next_mark_collection_due",
    "postgres_jsonb_text",
    "purchased_side_probability",
    "purchased_side_quote",
    "source_payload_hash",
    "walk_taker_ask_levels",
]
