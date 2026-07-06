from datetime import UTC, datetime, timedelta

import pytest

from stratus.db.models import SignalConfidence, TradeSide
from stratus.services.signals import (
    SignalInputs,
    ensemble_agreement,
    evaluate_signal,
    full_kelly_fraction,
)

NOW = datetime(2026, 7, 6, 12, tzinfo=UTC)


def signal_inputs(**overrides: object) -> SignalInputs:
    values: dict[str, object] = {
        "model_probability": 0.75,
        "best_bid": 0.58,
        "best_ask": 0.60,
        "bid_depth_usd": 500.0,
        "ask_depth_usd": 500.0,
        "horizon_h": 24.0,
        "market_created_at": NOW - timedelta(days=1),
        "price_ts": NOW - timedelta(minutes=1),
        "probability_ts": NOW - timedelta(minutes=5),
        "station_verified": True,
        "ensemble_yes_agreement": 0.85,
        "bankroll_usd": 1_000.0,
    }
    values.update(overrides)
    return SignalInputs(**values)  # type: ignore[arg-type]


def test_yes_signal_is_a_confidence_and_capped_at_five_percent() -> None:
    decision = evaluate_signal(signal_inputs(), NOW)

    assert decision.side is TradeSide.BUY_YES
    assert decision.entry_price == pytest.approx(0.60)
    assert decision.edge == pytest.approx(0.15)
    assert decision.confidence is SignalConfidence.A
    assert decision.kelly_fraction == pytest.approx(0.05)
    assert decision.size_usd == pytest.approx(50.0)
    assert decision.reasons == ()


def test_no_signal_uses_complement_probability_and_bid() -> None:
    decision = evaluate_signal(
        signal_inputs(
            model_probability=0.30,
            ensemble_yes_agreement=0.15,
        ),
        NOW,
    )

    assert decision.side is TradeSide.BUY_NO
    assert decision.entry_price == pytest.approx(0.42)
    assert decision.edge == pytest.approx(0.28)
    assert decision.ensemble_agreement == pytest.approx(0.85)
    assert decision.confidence is SignalConfidence.A
    assert decision.size_usd == pytest.approx(50.0)
    assert decision.reasons == ()


def test_all_entry_filters_are_reported() -> None:
    decision = evaluate_signal(
        signal_inputs(
            model_probability=0.04,
            best_bid=0.01,
            best_ask=0.02,
            ask_depth_usd=0,
            horizon_h=49,
            market_created_at=NOW - timedelta(hours=1),
            price_ts=NOW - timedelta(minutes=13),
            probability_ts=NOW - timedelta(hours=8),
            station_verified=False,
            ensemble_yes_agreement=0.5,
        ),
        NOW,
    )

    assert set(decision.reasons) == {
        "edge_below_0.05",
        "horizon_above_48h",
        "entry_price_out_of_range",
        "market_younger_than_2h",
        "station_unverified",
        "price_stale",
        "probability_stale",
        "insufficient_depth_3x",
    }


def test_past_resolution_and_wide_spread_are_rejected() -> None:
    decision = evaluate_signal(
        signal_inputs(
            model_probability=0.70,
            best_bid=0.50,
            best_ask=0.60,
            horizon_h=0,
            price_ts=NOW - timedelta(minutes=3),
        ),
        NOW,
    )

    assert "spread_above_0.04" in decision.reasons
    assert "market_past_resolution" in decision.reasons
    assert "price_stale" in decision.reasons


def test_ensemble_agreement_is_member_weighted_and_side_aware() -> None:
    components = {
        "ecmwf_ens": {"probability": 0.9, "sample_n": 51},
        "gefs": {"probability": 0.5, "sample_n": 31},
    }
    expected_yes = (0.9 * 51 + 0.5 * 31) / 82

    assert ensemble_agreement(components, TradeSide.BUY_YES) == pytest.approx(
        expected_yes
    )
    assert ensemble_agreement(components, TradeSide.BUY_NO) == pytest.approx(
        1 - expected_yes
    )
    assert ensemble_agreement({}, TradeSide.BUY_YES) == 0.0


def test_full_kelly_fraction() -> None:
    assert full_kelly_fraction(0.75, 0.60) == pytest.approx(0.375)
    assert full_kelly_fraction(0.40, 0.60) == 0.0
    assert full_kelly_fraction(0.75, 1.0) == 0.0
