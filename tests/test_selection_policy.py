from datetime import date, timedelta

from stratus.services.live_analytics import TradeOutcome
from stratus.services.selection_policy import (
    SHADOW_SELECTION_POLICIES,
    evaluate_recorded_selection_policies,
    evaluate_selection_policies,
    selection_policy_accepts,
    selection_policy_hash,
)


def _trade(
    index: int,
    *,
    edge: float,
    harmful: bool,
    side: str = "buy_no",
    horizon: str = "0-6h",
) -> TradeOutcome:
    won = not harmful
    model = 0.9 if harmful else 0.8
    market = 0.6
    return TradeOutcome(
        model_prob=model,
        market_prob=market,
        won=won,
        pnl_usd=-10.0 if harmful else 5.0,
        cost_usd=10.0,
        predicted_edge=edge,
        station_icao=f"K{index % 5:03d}",
        kind="high" if index % 2 else "low",
        side=side,
        horizon_bucket=horizon,
        market_date=date(2026, 1, 1) + timedelta(days=index),
        pipeline_version="v4",
        order_id=index + 1,
    )


def test_edge_cap_policy_stays_shadow_until_prospective_minimums() -> None:
    scores = evaluate_selection_policies(
        [_trade(index, edge=0.25, harmful=True) for index in range(20)]
        + [_trade(index + 20, edge=0.08, harmful=False) for index in range(20)]
    )

    cap = next(score for score in scores if score.name == "edge-cap-10c-v1")
    assert cap.accepted == 20
    assert cap.delta_information_value > 0
    assert cap.eligible is False


def test_edge_cap_can_qualify_only_with_blocked_prospective_evidence() -> None:
    trades = [
        _trade(index, edge=0.25, harmful=True) for index in range(70)
    ] + [
        _trade(index + 70, edge=0.08, harmful=False) for index in range(70)
    ]
    scores = evaluate_selection_policies(trades)

    baseline = next(score for score in scores if score.name == "baseline")
    cap = next(score for score in scores if score.name == "edge-cap-10c-v1")
    assert baseline.accepted == 140
    assert cap.accepted == 70
    assert cap.retention == 0.5
    assert cap.brier_model is not None and cap.brier_market is not None
    assert cap.brier_model < cap.brier_market
    assert cap.delta_ci_low is not None and cap.delta_ci_low > 0
    assert cap.eligible is True


def test_recorded_policy_score_ignores_missing_and_uses_frozen_decisions() -> None:
    trades = [
        _trade(0, edge=0.25, harmful=True),
        _trade(1, edge=0.08, harmful=False),
        _trade(2, edge=0.25, harmful=True),
    ]
    decisions = {
        1: {"baseline": True, "edge-cap-10c-v1": False},
        2: {"baseline": True, "edge-cap-10c-v1": True},
        # Order 3 deliberately has no durable decision and must disappear.
    }

    scores = evaluate_recorded_selection_policies(
        trades,
        decisions,
        minimum_opportunities=2,
        minimum_blocks=2,
        minimum_accepted=1,
        minimum_retention=0.35,
    )

    baseline = next(score for score in scores if score.name == "baseline")
    cap = next(score for score in scores if score.name == "edge-cap-10c-v1")
    assert baseline.opportunities == 2
    assert cap.opportunities == 2
    assert cap.accepted == 1
    assert cap.delta_information_value > 0


def test_policy_definition_hash_is_stable_and_threshold_sensitive() -> None:
    hashes = [selection_policy_hash(policy) for policy in SHADOW_SELECTION_POLICIES]
    assert hashes == [selection_policy_hash(policy) for policy in SHADOW_SELECTION_POLICIES]
    assert len(set(hashes)) == len(hashes)
    # Immutable edge-cap decisions were already captured in production. New
    # policy dimensions must not invalidate those existing fingerprints.
    actual = dict(
        zip(
            (item.name for item in SHADOW_SELECTION_POLICIES),
            hashes,
            strict=True,
        )
    )
    expected_existing = {
        "baseline": "4061a470c4409f4b",
        "edge-cap-10c-v1": "87601273bad835cb",
        "edge-cap-15c-v1": "d21a915e00d9b5b4",
        "edge-cap-20c-v1": "4736cd520039b971",
    }
    assert {name: actual[name] for name in expected_existing} == expected_existing


def test_slice_policies_are_pre_registered_and_use_only_pre_outcome_fields() -> None:
    policies = {item.name: item for item in SHADOW_SELECTION_POLICIES}
    no_yes = policies["exclude-buy-yes-v1"]
    no_mid_horizon = policies["exclude-horizon-6-24h-v1"]

    assert selection_policy_accepts(
        no_yes,
        predicted_edge=0.08,
        side="buy_yes",
        horizon_bucket="0-6h",
    ) is False
    assert selection_policy_accepts(
        no_yes,
        predicted_edge=0.08,
        side="buy_no",
        horizon_bucket="6-24h",
    ) is True
    assert selection_policy_accepts(
        no_mid_horizon,
        predicted_edge=0.08,
        side="buy_no",
        horizon_bucket="6-24h",
    ) is False

    scores = evaluate_selection_policies(
        [
            _trade(0, edge=0.08, harmful=True, side="buy_yes"),
            _trade(1, edge=0.08, harmful=True, horizon="6-24h"),
            _trade(2, edge=0.08, harmful=False),
        ]
    )
    assert next(score for score in scores if score.name == no_yes.name).accepted == 2
    assert next(
        score for score in scores if score.name == no_mid_horizon.name
    ).accepted == 2
