from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from stratus.services.accuracy_models import block_bootstrap_mean_ci
from stratus.services.live_analytics import TradeOutcome, brier_comparison


@dataclass(frozen=True, slots=True)
class SelectionPolicySpec:
    name: str
    max_predicted_edge: float | None
    excluded_sides: tuple[str, ...] = ()
    excluded_horizons: tuple[str, ...] = ()

    @property
    def is_reference(self) -> bool:
        return self.name == "baseline"

    @property
    def description(self) -> str:
        if self.is_reference:
            return "All filled opportunities (reference)"
        criteria = []
        if self.max_predicted_edge is not None:
            criteria.append(f"predicted edge <= {self.max_predicted_edge:.0%}")
        if self.excluded_sides:
            criteria.append("exclude side " + ", ".join(self.excluded_sides))
        if self.excluded_horizons:
            criteria.append("exclude horizon " + ", ".join(self.excluded_horizons))
        return "; ".join(criteria)


@dataclass(frozen=True, slots=True)
class SelectionPolicyScore:
    name: str
    description: str
    is_reference: bool
    max_predicted_edge: float | None
    opportunities: int
    accepted: int
    retention: float
    independent_blocks: int
    brier_model: float | None
    brier_market: float | None
    model_minus_market: float | None
    total_pnl_usd: float
    avg_realized_return: float | None
    information_value: float
    delta_information_value: float
    delta_ci_low: float | None
    delta_ci_high: float | None
    eligible: bool


SHADOW_SELECTION_POLICIES = (
    SelectionPolicySpec("baseline", None),
    SelectionPolicySpec("edge-cap-10c-v1", 0.10),
    SelectionPolicySpec("edge-cap-15c-v1", 0.15),
    SelectionPolicySpec("edge-cap-20c-v1", 0.20),
    # These two slices failed both legacy Brier and CLV diagnostics. They are
    # deliberately pre-registered as shadow policies, not production rules,
    # so future outcomes can test the hypotheses without hindsight.
    SelectionPolicySpec("exclude-buy-yes-v1", None, excluded_sides=("buy_yes",)),
    SelectionPolicySpec(
        "exclude-horizon-6-24h-v1",
        None,
        excluded_horizons=("6-24h",),
    ),
)


def selection_policy_hash(policy: SelectionPolicySpec) -> str:
    """Stable definition fingerprint stored before the market resolves.

    A code change that alters a threshold without renaming the policy then
    fails closed: old rows no longer match the current definition hash.
    """
    definition: dict[str, object] = {
        "schema": 1,
        "name": policy.name,
        "max_predicted_edge": policy.max_predicted_edge,
    }
    # Keep the original edge-cap hashes byte-for-byte stable so decisions
    # already frozen in the ledger remain valid. New dimensions are included
    # only for policies that actually use them.
    if policy.excluded_sides:
        definition["excluded_sides"] = policy.excluded_sides
    if policy.excluded_horizons:
        definition["excluded_horizons"] = policy.excluded_horizons
    payload = json.dumps(
        definition,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def selection_policy_by_name() -> dict[str, SelectionPolicySpec]:
    return {policy.name: policy for policy in SHADOW_SELECTION_POLICIES}


def selection_horizon_bucket(signal_ts: datetime, resolves_at: datetime) -> str:
    hours = (resolves_at - signal_ts).total_seconds() / 3600
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "24-48h"
    return "48h+"


def selection_policy_accepts(
    policy: SelectionPolicySpec,
    *,
    predicted_edge: float,
    side: str | None,
    horizon_bucket: str | None,
) -> bool:
    """Evaluate only attributes available before the outcome is known."""
    if (
        policy.max_predicted_edge is not None
        and predicted_edge > policy.max_predicted_edge
    ):
        return False
    if policy.excluded_sides:
        if side is None or side in policy.excluded_sides:
            return False
    if policy.excluded_horizons:
        if horizon_bucket is None or horizon_bucket in policy.excluded_horizons:
            return False
    return True


def _information_value(trade: TradeOutcome) -> float:
    outcome = float(trade.won)
    model_error = (trade.model_prob - outcome) ** 2
    market_error = (trade.market_prob - outcome) ** 2
    return market_error - model_error


def _block_key(trade: TradeOutcome, index: int) -> str:
    if trade.market_date is None:
        return f"unknown-{index}"
    return "|".join(
        (
            trade.station_icao or "unknown",
            trade.market_date.isoformat(),
            trade.kind or "unknown",
        )
    )


def evaluate_selection_policies(
    trades: list[TradeOutcome],
    *,
    minimum_opportunities: int = 100,
    minimum_blocks: int = 60,
    minimum_accepted: int = 30,
    minimum_retention: float = 0.35,
) -> tuple[SelectionPolicyScore, ...]:
    """Prospectively score fixed protective subsets of executed trades.

    Rejected opportunities contribute zero information value and zero PnL.
    Candidate-vs-baseline deltas are bootstrapped by station/day/kind so many
    correlated buckets from one weather event cannot manufacture confidence.
    No candidate is activated here; this is a shadow research gate only.
    """
    opportunities = len(trades)
    baseline_information = [_information_value(trade) for trade in trades]
    scores = []
    for policy in SHADOW_SELECTION_POLICIES:
        accepted = [
            trade
            for trade in trades
            if selection_policy_accepts(
                policy,
                predicted_edge=trade.predicted_edge,
                side=trade.side,
                horizon_bucket=trade.horizon_bucket,
            )
        ]
        accepted_ids = {id(trade) for trade in accepted}
        candidate_information = [
            value if id(trade) in accepted_ids else 0.0
            for trade, value in zip(trades, baseline_information, strict=True)
        ]
        deltas = [
            candidate - baseline
            for candidate, baseline in zip(
                candidate_information, baseline_information, strict=True
            )
        ]
        deltas_by_block: dict[str, list[float]] = defaultdict(list)
        for index, (trade, delta) in enumerate(zip(trades, deltas, strict=True)):
            deltas_by_block[_block_key(trade, index)].append(delta)
        ci = (
            block_bootstrap_mean_ci(deltas_by_block)
            if not policy.is_reference
            else None
        )
        briers = brier_comparison(accepted)
        accepted_cost = sum(trade.cost_usd for trade in accepted)
        accepted_pnl = sum(trade.pnl_usd for trade in accepted)
        retention = len(accepted) / opportunities if opportunities else 0.0
        model_minus_market = briers[0] - briers[1] if briers is not None else None
        eligible = bool(
            not policy.is_reference
            and opportunities >= minimum_opportunities
            and len(deltas_by_block) >= minimum_blocks
            and len(accepted) >= minimum_accepted
            and retention >= minimum_retention
            and model_minus_market is not None
            and model_minus_market < 0
            and ci is not None
            and ci[0] > 0
        )
        scores.append(
            SelectionPolicyScore(
                name=policy.name,
                description=policy.description,
                is_reference=policy.is_reference,
                max_predicted_edge=policy.max_predicted_edge,
                opportunities=opportunities,
                accepted=len(accepted),
                retention=round(retention, 6),
                independent_blocks=len(deltas_by_block),
                brier_model=briers[0] if briers is not None else None,
                brier_market=briers[1] if briers is not None else None,
                model_minus_market=(
                    round(model_minus_market, 6)
                    if model_minus_market is not None
                    else None
                ),
                total_pnl_usd=round(accepted_pnl, 4),
                avg_realized_return=(
                    round(accepted_pnl / accepted_cost, 6)
                    if accepted_cost > 0
                    else None
                ),
                information_value=round(sum(candidate_information), 6),
                delta_information_value=round(sum(deltas), 6),
                delta_ci_low=round(ci[0], 6) if ci is not None else None,
                delta_ci_high=round(ci[1], 6) if ci is not None else None,
                eligible=eligible,
            )
        )
    return tuple(scores)


def evaluate_recorded_selection_policies(
    trades: list[TradeOutcome],
    decisions_by_order: Mapping[int, Mapping[str, bool]],
    *,
    minimum_opportunities: int = 100,
    minimum_blocks: int = 60,
    minimum_accepted: int = 30,
    minimum_retention: float = 0.35,
) -> tuple[SelectionPolicyScore, ...]:
    """Score only decisions that were durably recorded before resolution.

    Each candidate is compared on orders that contain both its own decision
    and the baseline decision. Missing, stale-hash, or post-resolution rows
    are omitted by the repository and therefore can never help eligibility.
    """
    scores = []
    for policy in SHADOW_SELECTION_POLICIES:
        recorded: list[tuple[TradeOutcome, bool, bool]] = []
        for trade in trades:
            if trade.order_id is None:
                continue
            decisions = decisions_by_order.get(trade.order_id)
            if decisions is None or "baseline" not in decisions:
                continue
            if policy.name not in decisions:
                continue
            recorded.append(
                (
                    trade,
                    decisions["baseline"],
                    decisions[policy.name],
                )
            )

        accepted = [trade for trade, _, selected in recorded if selected]
        deltas_by_block: dict[str, list[float]] = defaultdict(list)
        baseline_information = []
        candidate_information = []
        for index, (trade, baseline_selected, selected) in enumerate(recorded):
            information = _information_value(trade)
            baseline_value = information if baseline_selected else 0.0
            candidate_value = information if selected else 0.0
            baseline_information.append(baseline_value)
            candidate_information.append(candidate_value)
            deltas_by_block[_block_key(trade, index)].append(
                candidate_value - baseline_value
            )

        deltas = [
            candidate - baseline
            for candidate, baseline in zip(
                candidate_information,
                baseline_information,
                strict=True,
            )
        ]
        ci = (
            block_bootstrap_mean_ci(deltas_by_block)
            if not policy.is_reference
            else None
        )
        briers = brier_comparison(accepted)
        accepted_cost = sum(trade.cost_usd for trade in accepted)
        accepted_pnl = sum(trade.pnl_usd for trade in accepted)
        opportunities = len(recorded)
        retention = len(accepted) / opportunities if opportunities else 0.0
        model_minus_market = briers[0] - briers[1] if briers is not None else None
        eligible = bool(
            not policy.is_reference
            and opportunities >= minimum_opportunities
            and len(deltas_by_block) >= minimum_blocks
            and len(accepted) >= minimum_accepted
            and retention >= minimum_retention
            and model_minus_market is not None
            and model_minus_market < 0
            and ci is not None
            and ci[0] > 0
        )
        scores.append(
            SelectionPolicyScore(
                name=policy.name,
                description=policy.description,
                is_reference=policy.is_reference,
                max_predicted_edge=policy.max_predicted_edge,
                opportunities=opportunities,
                accepted=len(accepted),
                retention=round(retention, 6),
                independent_blocks=len(deltas_by_block),
                brier_model=briers[0] if briers is not None else None,
                brier_market=briers[1] if briers is not None else None,
                model_minus_market=(
                    round(model_minus_market, 6)
                    if model_minus_market is not None
                    else None
                ),
                total_pnl_usd=round(accepted_pnl, 4),
                avg_realized_return=(
                    round(accepted_pnl / accepted_cost, 6)
                    if accepted_cost > 0
                    else None
                ),
                information_value=round(sum(candidate_information), 6),
                delta_information_value=round(sum(deltas), 6),
                delta_ci_low=round(ci[0], 6) if ci is not None else None,
                delta_ci_high=round(ci[1], 6) if ci is not None else None,
                eligible=eligible,
            )
        )
    return tuple(scores)
