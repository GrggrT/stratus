from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from stratus.db.models import (
    EvidenceContinuityLane,
    ExecutionMode,
    ExecutionOrderIntent,
    Market,
    ModelProb,
    Order,
    SelectionPolicyDecision,
    Signal,
)
from stratus.db.session import dml_rowcount
from stratus.repositories.analytics import probability_pipeline_version
from stratus.repositories.evidence_continuity import current_evidence_continuity_lanes
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.selection_policy import (
    SHADOW_SELECTION_POLICIES,
    selection_horizon_bucket,
    selection_policy_accepts,
    selection_policy_hash,
)

_FILLED_STATUSES = ("filled", "partially_filled")


@dataclass(frozen=True, slots=True)
class SelectionPolicyLedgerCounts:
    recorded: int
    resolved: int


async def _active_selection_lane_order_ids(
    session: AsyncSession,
    order_ids: Sequence[int],
) -> tuple[int, ...]:
    """Keep only orders attested to the active selection-policy lineage.

    A reviewed ``continue`` creates a new lane row while preserving its
    identity and boundary, so matching the lineage rather than the row id
    retains valid evidence. A ``fork`` opens a new boundary/identity and
    therefore excludes every decision attached to the former lane.
    """

    canonical_order_ids = tuple(sorted(set(order_ids)))
    if not canonical_order_ids:
        return ()
    active_lanes = {
        lane.lane_name: lane
        for lane in await current_evidence_continuity_lanes(session)
        if lane.lane_name in {"selection_policy", "station_lifecycle"}
        and lane.action in {"continue", "fork"}
    }
    active_selection_lane = active_lanes.get("selection_policy")
    active_station_lane = active_lanes.get("station_lifecycle")
    if active_selection_lane is None or active_station_lane is None:
        return ()
    enrolled_selection_lane = aliased(EvidenceContinuityLane)
    enrolled_station_lane = aliased(EvidenceContinuityLane)
    rows = (
        await session.scalars(
            select(ExecutionOrderIntent.order_id)
            .join(
                enrolled_selection_lane,
                enrolled_selection_lane.id == ExecutionOrderIntent.selection_lane_id,
            )
            .join(
                enrolled_station_lane,
                enrolled_station_lane.id == ExecutionOrderIntent.station_lane_id,
            )
            .where(
                ExecutionOrderIntent.order_id.in_(canonical_order_ids),
                enrolled_selection_lane.lane_name == "selection_policy",
                enrolled_selection_lane.identity_hash
                == active_selection_lane.identity_hash,
                enrolled_selection_lane.boundary_at
                == active_selection_lane.boundary_at,
                enrolled_station_lane.lane_name == "station_lifecycle",
                enrolled_station_lane.identity_hash == active_station_lane.identity_hash,
                enrolled_station_lane.boundary_at == active_station_lane.boundary_at,
            )
            .order_by(ExecutionOrderIntent.order_id)
        )
    ).all()
    return tuple(rows)


async def capture_selection_policy_decisions(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> int:
    """Persist fixed shadow decisions for newly filled unresolved orders.

    The market must still be unresolved and its declared resolution time must
    be in the future. Existing `(order, policy)` decisions are never updated,
    making the table an immutable pre-outcome experiment ledger.
    """
    decided_at = now or datetime.now(UTC)
    pre_signal_components = (
        select(ModelProb.components)
        .where(
            ModelProb.market_id == Signal.market_id,
            ModelProb.ts <= Signal.ts,
            ModelProb.market_anchor_protocol == Signal.benchmark_protocol,
            or_(
                Signal.benchmark_protocol != CLOB_DUAL_PROTOCOL,
                ModelProb.market_snapshot_id == Signal.clob_snapshot_id,
            ),
        )
        .order_by(ModelProb.ts.desc(), ModelProb.id.desc())
        .limit(1)
        .correlate(Signal)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            select(
                Order.id,
                Order.filled_at,
                Signal.ts,
                Signal.edge,
                Signal.side,
                Market.resolves_at,
                pre_signal_components,
            )
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .where(
                Order.status.in_(_FILLED_STATUSES),
                Order.filled_at.is_not(None),
                Market.resolved_outcome.is_(None),
                Market.resolves_at > decided_at,
            )
        )
    ).all()
    values = []
    for (
        order_id,
        filled_at,
        signal_ts,
        predicted_edge,
        side,
        resolves_at,
        components,
    ) in rows:
        if filled_at is None:
            continue
        pipeline_version = probability_pipeline_version(components) or "unknown"
        edge = float(predicted_edge)
        side_value = side.value if hasattr(side, "value") else str(side)
        horizon_bucket = selection_horizon_bucket(signal_ts, resolves_at)
        for policy in SHADOW_SELECTION_POLICIES:
            values.append(
                {
                    "order_id": order_id,
                    "policy_name": policy.name,
                    "policy_hash": selection_policy_hash(policy),
                    "max_predicted_edge": (
                        Decimal(str(policy.max_predicted_edge))
                        if policy.max_predicted_edge is not None
                        else None
                    ),
                    "predicted_edge": predicted_edge,
                    "accepted": selection_policy_accepts(
                        policy,
                        predicted_edge=edge,
                        side=side_value,
                        horizon_bucket=horizon_bucket,
                    ),
                    "pipeline_version": pipeline_version,
                    "signal_ts": signal_ts,
                    "filled_at": filled_at,
                    "decided_at": decided_at,
                }
            )
    if not values:
        return 0
    statement = insert(SelectionPolicyDecision).values(values)
    statement = statement.on_conflict_do_nothing(
        constraint="uq_selection_policy_decisions_order_policy"
    )
    return dml_rowcount(await session.execute(statement))


async def valid_selection_policy_decisions(
    session: AsyncSession,
    order_ids: Sequence[int],
    *,
    pipeline_version: str | None = None,
) -> dict[int, dict[str, bool]]:
    """Load current-lane, definition-matching decisions recorded pre-resolution."""
    scoped_order_ids = await _active_selection_lane_order_ids(session, order_ids)
    if not scoped_order_ids:
        return {}
    current_hashes = {
        policy.name: selection_policy_hash(policy)
        for policy in SHADOW_SELECTION_POLICIES
    }
    conditions = [
        SelectionPolicyDecision.order_id.in_(scoped_order_ids),
        SelectionPolicyDecision.decided_at <= Market.resolves_at,
        SelectionPolicyDecision.filled_at <= SelectionPolicyDecision.decided_at,
        SelectionPolicyDecision.signal_ts <= SelectionPolicyDecision.decided_at,
    ]
    if pipeline_version is not None:
        conditions.append(
            SelectionPolicyDecision.pipeline_version == pipeline_version
        )
    rows = (
        await session.execute(
            select(
                SelectionPolicyDecision.order_id,
                SelectionPolicyDecision.policy_name,
                SelectionPolicyDecision.policy_hash,
                SelectionPolicyDecision.accepted,
            )
            .join(Order, SelectionPolicyDecision.order_id == Order.id)
            .join(Signal, Order.signal_id == Signal.id)
            .join(Market, Signal.market_id == Market.id)
            .where(*conditions)
        )
    ).all()
    decisions: dict[int, dict[str, bool]] = {}
    for order_id, policy_name, policy_hash, accepted in rows:
        if current_hashes.get(policy_name) != policy_hash:
            continue
        decisions.setdefault(order_id, {})[policy_name] = bool(accepted)
    return decisions


async def selection_policy_ledger_counts(
    session: AsyncSession,
    order_ids: Sequence[int],
    *,
    mode: ExecutionMode,
    pipeline_version: str | None,
    benchmark_protocol: str | None = None,
) -> SelectionPolicyLedgerCounts:
    """Count baseline decisions inside one formal current-lane order sample."""
    if pipeline_version is None:
        return SelectionPolicyLedgerCounts(recorded=0, resolved=0)
    scoped_order_ids = await _active_selection_lane_order_ids(session, order_ids)
    if not scoped_order_ids:
        return SelectionPolicyLedgerCounts(recorded=0, resolved=0)
    baseline = SHADOW_SELECTION_POLICIES[0]
    statement = (
        select(Market.resolved_outcome)
        .select_from(SelectionPolicyDecision)
        .join(Order, SelectionPolicyDecision.order_id == Order.id)
        .join(Signal, Order.signal_id == Signal.id)
        .join(Market, Signal.market_id == Market.id)
        .where(
            SelectionPolicyDecision.order_id.in_(scoped_order_ids),
            SelectionPolicyDecision.policy_name == baseline.name,
            SelectionPolicyDecision.policy_hash == selection_policy_hash(baseline),
            SelectionPolicyDecision.pipeline_version == pipeline_version,
            SelectionPolicyDecision.decided_at <= Market.resolves_at,
            SelectionPolicyDecision.filled_at <= SelectionPolicyDecision.decided_at,
            SelectionPolicyDecision.signal_ts <= SelectionPolicyDecision.decided_at,
            Order.mode == mode,
        )
    )
    if benchmark_protocol is not None:
        statement = statement.where(Signal.benchmark_protocol == benchmark_protocol)
    rows = (await session.execute(statement)).scalars().all()
    return SelectionPolicyLedgerCounts(
        recorded=len(rows),
        resolved=sum(outcome is not None for outcome in rows),
    )
