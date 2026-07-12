from datetime import UTC, datetime, timedelta

import pytest

from stratus.db.models import SignalConfidence, TradeSide
from stratus.services.signals import (
    SignalInputs,
    SignalPolicy,
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
        # Deep calibration by default so individual tests opt in to the
        # uncalibrated gates explicitly.
        "calibration_samples": 100,
    }
    values.update(overrides)
    return SignalInputs(**values)  # type: ignore[arg-type]


def test_yes_signal_is_a_confidence_and_win_capped() -> None:
    decision = evaluate_signal(signal_inputs(), NOW)

    assert decision.side is TradeSide.BUY_YES
    assert decision.entry_price == pytest.approx(0.60)
    assert decision.edge == pytest.approx(0.15)
    assert decision.confidence is SignalConfidence.A
    # Kelly would cap at 5% ($50), but the payout cap binds first:
    # potential win = size * (1-0.6)/0.6 <= 2% * 1000 -> size = $30.
    assert decision.size_usd == pytest.approx(30.0)
    assert decision.kelly_fraction == pytest.approx(0.03)
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
    # Payout cap: potential win = size * 0.58/0.42 <= $20 -> size ~= $14.48.
    assert decision.size_usd == pytest.approx(20 * 0.42 / 0.58)
    assert decision.reasons == ()


def test_payout_cap_shrinks_longshots_instead_of_banning_them() -> None:
    # Entry at 0.15: a $30 cost would carry a $170 potential win. The cap
    # limits the payout to 2% of bankroll ($20) -> size ~= $3.53.
    decision = evaluate_signal(
        signal_inputs(
            model_probability=0.30,
            best_bid=0.14,
            best_ask=0.15,
            ensemble_yes_agreement=0.9,
        ),
        NOW,
    )

    assert decision.side is TradeSide.BUY_YES
    assert decision.size_usd == pytest.approx(20 * 0.15 / 0.85)
    assert decision.size_usd * (1 - 0.15) / 0.15 == pytest.approx(20.0)


def test_edge_must_clear_execution_costs() -> None:
    # Edge 0.025 is below half-spread (0.01) + cost buffer (0.02) and below
    # the 0.05 minimum: both cost-side reasons must fire.
    decision = evaluate_signal(
        signal_inputs(model_probability=0.625),
        NOW,
    )
    assert decision.edge == pytest.approx(0.025)
    assert "edge_below_costs" in decision.reasons
    assert "edge_below_min" in decision.reasons


def test_station_uncalibrated_blocks_and_tail_needs_deeper_sample() -> None:
    uncalibrated = evaluate_signal(
        signal_inputs(calibration_samples=10), NOW
    )
    assert "station_uncalibrated" in uncalibrated.reasons

    # Calibrated for the base gate (>=30) but not for tails (>=90).
    tail = evaluate_signal(
        signal_inputs(
            model_probability=0.99,
            best_bid=0.91,
            best_ask=0.92,
            ensemble_yes_agreement=0.95,
            calibration_samples=50,
        ),
        NOW,
    )
    assert tail.entry_price == pytest.approx(0.92)
    assert "station_uncalibrated" not in tail.reasons
    assert "tail_uncalibrated" in tail.reasons

    deep = evaluate_signal(
        signal_inputs(
            model_probability=0.99,
            best_bid=0.91,
            best_ask=0.92,
            ensemble_yes_agreement=0.95,
            calibration_samples=90,
        ),
        NOW,
    )
    assert "tail_uncalibrated" not in deep.reasons


def test_kelly_multiplier_scales_position_size() -> None:
    half_kelly = evaluate_signal(
        signal_inputs(), NOW, SignalPolicy(kelly_multiplier=0.5, max_win_fraction=0.5)
    )
    quarter_kelly = evaluate_signal(
        signal_inputs(), NOW, SignalPolicy(kelly_multiplier=0.25, max_win_fraction=0.5)
    )
    # With the payout cap out of the way, size scales with the multiplier
    # until the confidence cap binds.
    assert quarter_kelly.kelly_fraction == pytest.approx(0.05)
    assert half_kelly.kelly_fraction == pytest.approx(0.05)
    # Below every cap (full Kelly 0.025) the multiplier scales linearly.
    uncapped = evaluate_signal(
        signal_inputs(model_probability=0.61),
        NOW,
        SignalPolicy(kelly_multiplier=0.5, max_win_fraction=0.5),
    )
    reference = evaluate_signal(
        signal_inputs(model_probability=0.61),
        NOW,
        SignalPolicy(kelly_multiplier=0.25, max_win_fraction=0.5),
    )
    assert reference.kelly_fraction == pytest.approx(0.00625)
    assert uncapped.kelly_fraction == pytest.approx(2 * reference.kelly_fraction)


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
            calibration_samples=0,
        ),
        NOW,
    )

    assert set(decision.reasons) == {
        "edge_below_min",
        "edge_below_costs",
        "station_uncalibrated",
        "tail_uncalibrated",
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

    assert "spread_too_wide" in decision.reasons
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
