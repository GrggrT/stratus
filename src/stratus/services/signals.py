from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from stratus.db.models import SignalConfidence, TradeSide


@dataclass(frozen=True, slots=True)
class SignalInputs:
    model_probability: float
    best_bid: float
    best_ask: float
    bid_depth_usd: float
    ask_depth_usd: float
    horizon_h: float
    market_created_at: datetime
    price_ts: datetime
    probability_ts: datetime
    station_verified: bool
    ensemble_yes_agreement: float
    bankroll_usd: float
    fee_rate: float = 0.0


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


def evaluate_signal(inputs: SignalInputs, now: datetime) -> SignalDecision:
    effective_yes = inputs.best_ask + inputs.fee_rate * inputs.best_ask * (
        1 - inputs.best_ask
    )
    no_price = 1 - inputs.best_bid
    effective_no = no_price + inputs.fee_rate * no_price * (1 - no_price)
    edge_yes = inputs.model_probability - effective_yes
    edge_no = 1 - inputs.model_probability - effective_no
    if edge_yes >= edge_no:
        side = TradeSide.BUY_YES
        entry_price = inputs.best_ask
        true_probability = inputs.model_probability
        effective_price = effective_yes
        edge = edge_yes
        depth = inputs.ask_depth_usd
    else:
        side = TradeSide.BUY_NO
        entry_price = no_price
        true_probability = 1 - inputs.model_probability
        effective_price = effective_no
        edge = edge_no
        depth = inputs.bid_depth_usd

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

    quarter_kelly = 0.25 * full_kelly_fraction(true_probability, effective_price)
    kelly_fraction = min(quarter_kelly, CONFIDENCE_CAPS[confidence])
    size_usd = inputs.bankroll_usd * kelly_fraction
    spread = inputs.best_ask - inputs.best_bid
    reasons = []
    if edge < 0.05:
        reasons.append("edge_below_0.05")
    if spread > 0.04:
        reasons.append("spread_above_0.04")
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
