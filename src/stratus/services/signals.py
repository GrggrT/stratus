from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from stratus.db.models import SignalConfidence, TradeSide


@dataclass(frozen=True, slots=True)
class SignalInputs:
    model_probability: float
    yes_best_bid: float
    yes_best_ask: float
    yes_bid_depth_usd: float
    yes_ask_depth_usd: float
    no_best_bid: float
    no_best_ask: float
    no_bid_depth_usd: float
    no_ask_depth_usd: float
    horizon_h: float
    market_created_at: datetime
    price_ts: datetime
    probability_ts: datetime
    station_verified: bool
    ensemble_yes_agreement: float
    bankroll_usd: float
    fee_rate: float = 0.0
    # Calibration sample count backing this market's probability (station/
    # kind/horizon bucket). Zero means the model runs on defaults and has not
    # earned the right to disagree with the market.
    calibration_samples: int = 0


@dataclass(frozen=True, slots=True)
class SignalPolicy:
    """Trading thresholds. Every value mirrors a Settings field so nothing is
    hardcoded; defaults match the validation-stage (anti-luck) configuration."""

    min_edge: float = 0.05
    # Adverse-selection ceiling. The largest model-vs-market disagreements were
    # systematically WRONG and unprofitable in paper: model-minus-market Brier
    # widens and realised outcomes go against the trade exactly where the edge
    # is biggest (docs/FORECAST_REVIEW_2026-07-21.md, experiment E3). This is the
    # active counterpart of the shadow edge-cap-Xc family. Reject signals whose
    # edge exceeds this ceiling. 1.0 disables it (no binary edge can exceed 1).
    max_edge: float = 0.20
    max_spread: float = 0.04
    # Edge must clear execution costs with margin: half the spread plus this
    # buffer (fees are already inside the effective-price edge).
    edge_cost_buffer: float = 0.02
    kelly_multiplier: float = 0.25
    # Cap on potential PAYOUT per position, as a fraction of bankroll. Caps
    # by cost alone let a $38 longshot carry a 20%-of-bankroll payout.
    max_win_fraction: float = 0.02
    tail_price_low: float = 0.10
    tail_price_high: float = 0.90
    min_calibration_samples: int = 30


DEFAULT_POLICY = SignalPolicy()


@dataclass(frozen=True, slots=True)
class SignalDecision:
    side: TradeSide
    entry_price: float
    edge: float
    confidence: SignalConfidence
    kelly_fraction: float
    size_usd: float
    ensemble_agreement: float
    reasons: tuple[str, ...]
    effective_price: float


CONFIDENCE_CAPS = {
    SignalConfidence.C: 0.02,
    SignalConfidence.B: 0.035,
    SignalConfidence.A: 0.05,
}


def ensemble_agreement(components: dict[str, Any], side: TradeSide) -> float:
    weighted_probability = 0.0
    members = 0
    for name in ("ecmwf_ens", "gefs"):
        component = components.get(name)
        if not isinstance(component, dict):
            continue
        probability = component.get("probability")
        sample_n = component.get("sample_n")
        if isinstance(probability, (int, float)) and isinstance(sample_n, int) and sample_n > 0:
            weighted_probability += float(probability) * sample_n
            members += sample_n
    if members == 0:
        return 0.0
    yes_agreement = weighted_probability / members
    return yes_agreement if side is TradeSide.BUY_YES else 1 - yes_agreement


def full_kelly_fraction(true_probability: float, entry_price: float) -> float:
    if not 0 < entry_price < 1:
        return 0.0
    return max(0.0, (true_probability - entry_price) / (1 - entry_price))


def evaluate_signal(
    inputs: SignalInputs, now: datetime, policy: SignalPolicy = DEFAULT_POLICY
) -> SignalDecision:
    effective_yes = inputs.yes_best_ask + inputs.fee_rate * inputs.yes_best_ask * (
        1 - inputs.yes_best_ask
    )
    effective_no = inputs.no_best_ask + inputs.fee_rate * inputs.no_best_ask * (
        1 - inputs.no_best_ask
    )
    edge_yes = inputs.model_probability - effective_yes
    edge_no = 1 - inputs.model_probability - effective_no
    if edge_yes >= edge_no:
        side = TradeSide.BUY_YES
        entry_price = inputs.yes_best_ask
        true_probability = inputs.model_probability
        effective_price = effective_yes
        edge = edge_yes
        depth = inputs.yes_ask_depth_usd
        spread = inputs.yes_best_ask - inputs.yes_best_bid
    else:
        side = TradeSide.BUY_NO
        entry_price = inputs.no_best_ask
        true_probability = 1 - inputs.model_probability
        effective_price = effective_no
        edge = edge_no
        depth = inputs.no_ask_depth_usd
        spread = inputs.no_best_ask - inputs.no_best_bid

    selected_agreement = (
        inputs.ensemble_yes_agreement
        if side is TradeSide.BUY_YES
        else 1 - inputs.ensemble_yes_agreement
    )
    if edge >= 0.12 and selected_agreement > 0.8:
        confidence = SignalConfidence.A
    elif edge >= 0.08:
        confidence = SignalConfidence.B
    else:
        confidence = SignalConfidence.C

    scaled_kelly = policy.kelly_multiplier * full_kelly_fraction(
        true_probability, effective_price
    )
    kelly_fraction = min(scaled_kelly, CONFIDENCE_CAPS[confidence])
    size_usd = inputs.bankroll_usd * kelly_fraction
    # Cap by potential payout, not just cost: shares = size/entry, payout on a
    # win is shares x (1 - entry). Shrinks longshots instead of banning them.
    if 0 < entry_price < 1 and inputs.bankroll_usd > 0:
        win_per_usd = (1 - entry_price) / entry_price
        max_win_usd = policy.max_win_fraction * inputs.bankroll_usd
        if win_per_usd > 0 and size_usd * win_per_usd > max_win_usd:
            size_usd = max_win_usd / win_per_usd
            kelly_fraction = size_usd / inputs.bankroll_usd

    half_spread = spread / 2
    calibrated = inputs.calibration_samples >= policy.min_calibration_samples
    # Tail entries are the most calibration-sensitive; they need a deeper
    # sample than the base gate before real (or even paper) money rides them.
    tail_calibrated = inputs.calibration_samples >= 3 * policy.min_calibration_samples
    reasons = []
    if edge < policy.min_edge:
        reasons.append("edge_below_min")
    # An edge larger than the ceiling is a red flag, not an opportunity: it means
    # the model disagrees with the market by more than has ever paid off, and
    # those trades were adversely selected (E3). Suppress rather than upsize.
    if edge > policy.max_edge:
        reasons.append("edge_above_max")
    if edge < half_spread + policy.edge_cost_buffer:
        reasons.append("edge_below_costs")
    if spread > policy.max_spread:
        reasons.append("spread_too_wide")
    if policy.min_calibration_samples > 0 and not calibrated:
        reasons.append("station_uncalibrated")
    if (
        policy.min_calibration_samples > 0
        and not tail_calibrated
        and not policy.tail_price_low <= entry_price <= policy.tail_price_high
    ):
        reasons.append("tail_uncalibrated")
    if inputs.horizon_h > 48:
        reasons.append("horizon_above_48h")
    if inputs.horizon_h <= 0:
        reasons.append("market_past_resolution")
    if not 0.05 <= entry_price <= 0.92:
        reasons.append("entry_price_out_of_range")
    if now - inputs.market_created_at < timedelta(hours=2):
        reasons.append("market_younger_than_2h")
    if not inputs.station_verified:
        reasons.append("station_unverified")
    max_price_age = timedelta(minutes=2 if inputs.horizon_h <= 6 else 12)
    if now - inputs.price_ts > max_price_age:
        reasons.append("price_stale")
    if now - inputs.probability_ts > timedelta(hours=7):
        reasons.append("probability_stale")
    if size_usd <= 0:
        reasons.append("kelly_size_zero")
    if depth < 3 * size_usd:
        reasons.append("insufficient_depth_3x")
    return SignalDecision(
        side=side,
        entry_price=entry_price,
        edge=edge,
        confidence=confidence,
        kelly_fraction=kelly_fraction,
        size_usd=size_usd,
        ensemble_agreement=selected_agreement,
        reasons=tuple(reasons),
        effective_price=effective_price,
    )
