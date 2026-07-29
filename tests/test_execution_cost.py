from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.db.models import ClobMarketSnapshot, TradeSide
from stratus.services.execution_cost import (
    COST_IDENTITY_TOLERANCE,
    EXECUTION_COST_DEFINITION_HASH,
    EXECUTION_COST_DEFINITION_VERSION,
    EXPECTED_SLIPPAGE_MISSING,
    EXPECTED_SLIPPAGE_MODELED,
    EXPECTED_SLIPPAGE_NOT_MODELED,
    FEE_COVERAGE_COMPLETE,
    FEE_COVERAGE_MISSING,
    LIVE_FILL_SNAPSHOT_MAX_AGE_SECONDS,
    MARK_GRACE_SECONDS,
    MARK_HORIZONS_SECONDS,
    MARKOUT_COVERAGE_COMPLETE,
    MARKOUT_COVERAGE_MISSING,
    ExecutionCostIntegrityError,
    ExecutionFillLeg,
    aggregate_liquidity_role,
    calculate_adverse_selection,
    calculate_seal_metrics,
    canonical_weighted_fill_price,
    execution_record_hash,
    next_mark_collection_due,
    postgres_jsonb_text,
    purchased_side_probability,
    purchased_side_quote,
    source_payload_hash,
    walk_taker_ask_levels,
)


def _snapshot(*, no_midpoint: Decimal | None = Decimal("0.605")) -> ClobMarketSnapshot:
    return ClobMarketSnapshot(
        yes_token_id="yes-token",
        no_token_id="no-token",
        yes_best_bid=Decimal("0.38"),
        yes_best_ask=Decimal("0.41"),
        yes_midpoint=Decimal("0.395"),
        no_best_bid=Decimal("0.59"),
        no_best_ask=Decimal("0.62"),
        no_midpoint=no_midpoint,
    )


def _leg(
    sequence: int,
    role: str,
    *,
    shares: str,
    fill: str,
    midpoint: str,
    ask: str,
    fee: str | None,
) -> ExecutionFillLeg:
    assert role in {"maker", "taker", "unknown"}
    return ExecutionFillLeg(
        sequence=sequence,
        liquidity_role=role,  # type: ignore[arg-type]
        shares=Decimal(shares),
        fill_price=Decimal(fill),
        fill_midpoint=Decimal(midpoint),
        fill_ask=Decimal(ask),
        fee_usd=Decimal(fee) if fee is not None else None,
    )


def test_frozen_execution_cost_definition_identity() -> None:
    assert EXECUTION_COST_DEFINITION_VERSION == "execution-cost-v1"
    assert MARK_HORIZONS_SECONDS == (300, 1800)
    assert MARK_GRACE_SECONDS == 300
    assert LIVE_FILL_SNAPSHOT_MAX_AGE_SECONDS == 120
    assert EXECUTION_COST_DEFINITION_HASH == (
        "9a5878e7867d3fba6bebfc1aa986474dc6e29b3b26f07532bfe9a730acd5b6b8"
    )
    assert COST_IDENTITY_TOLERANCE == Decimal("0.000000000001")


def test_mark_collection_retries_through_grace_boundary() -> None:
    target = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    grace = target.replace(minute=5)

    assert next_mark_collection_due(
        target_at=target,
        now=target.replace(second=0) - timedelta(seconds=1),
    ) == target
    assert next_mark_collection_due(target_at=target, now=target) == target
    inside_grace = target + timedelta(seconds=137)
    assert next_mark_collection_due(target_at=target, now=inside_grace) == inside_grace
    assert next_mark_collection_due(
        target_at=target,
        now=grace - timedelta(seconds=1),
    ) == grace - timedelta(seconds=1)
    assert next_mark_collection_due(target_at=target, now=grace) is None
    assert next_mark_collection_due(
        target_at=target,
        now=grace + timedelta(seconds=1),
    ) is None


def test_mark_collection_rejects_naive_clocks() -> None:
    aware = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    naive = aware.replace(tzinfo=None)

    with pytest.raises(ValueError, match="target_at"):
        next_mark_collection_due(target_at=naive, now=aware)
    with pytest.raises(ValueError, match="now"):
        next_mark_collection_due(target_at=aware, now=naive)


def test_yes_and_no_projection_use_independent_snapshot_books() -> None:
    snapshot = _snapshot()

    yes = purchased_side_quote(snapshot, TradeSide.BUY_YES, Decimal("0.63"))
    no = purchased_side_quote(snapshot, TradeSide.BUY_NO, Decimal("0.63"))

    assert yes.token_id == "yes-token"
    assert yes.side_model_probability == Decimal("0.63")
    assert yes.best_bid == Decimal("0.38")
    assert yes.best_ask == Decimal("0.41")
    assert yes.midpoint == Decimal("0.395")
    assert no.token_id == "no-token"
    assert no.side_model_probability == Decimal("0.37")
    assert no.best_bid == Decimal("0.59")
    assert no.best_ask == Decimal("0.62")
    assert no.midpoint == Decimal("0.605")


def test_no_projection_never_falls_back_to_yes_complement() -> None:
    with pytest.raises(ExecutionCostIntegrityError, match="two-sided"):
        purchased_side_quote(_snapshot(no_midpoint=None), TradeSide.BUY_NO, Decimal("0.63"))


def test_taker_walk_emits_deterministic_atomic_price_level_legs() -> None:
    def fee(shares: Decimal, _price: Decimal) -> Decimal:
        return shares * Decimal("0.01")

    walk = walk_taker_ask_levels(
        (
            (Decimal("0.60"), Decimal("100")),
            (Decimal("0.51"), Decimal("20")),
            (Decimal("0.50"), Decimal("10")),
        ),
        target_notional_usd=Decimal("10.30"),
        fee_fn=fee,
    )

    assert [(leg.sequence, leg.price, leg.shares) for leg in walk.legs] == [
        (1, Decimal("0.50"), Decimal("10.000000")),
        (2, Decimal("0.51"), Decimal("10.000000")),
    ]
    assert walk.filled_notional_usd == Decimal("10.10000000")
    assert walk.total_fee_usd == Decimal("0.20000000")
    assert walk.total_cost_usd == Decimal("10.30000000")
    assert walk.unfilled_usd == 0
    assert walk.fully_filled is True


def test_taker_walk_partial_leg_never_exceeds_budget_and_reports_depth_shortfall() -> None:
    def zero_fee(_shares: Decimal, _price: Decimal) -> Decimal:
        return Decimal(0)

    partial = walk_taker_ask_levels(
        ((Decimal("0.51"), Decimal("100")),),
        target_notional_usd=Decimal("10"),
        fee_fn=zero_fee,
    )
    assert len(partial.legs) == 1
    assert partial.legs[0].shares == Decimal("19.607843")
    assert partial.total_cost_usd <= Decimal("10")
    assert partial.unfilled_usd <= Decimal("0.000001")
    assert partial.fully_filled is True

    insufficient = walk_taker_ask_levels(
        ((Decimal("0.50"), Decimal("10")),),
        target_notional_usd=Decimal("20"),
        fee_fn=zero_fee,
    )
    assert insufficient.total_cost_usd == Decimal("5.00000000")
    assert insufficient.unfilled_usd == Decimal("15.00000000")
    assert insufficient.fully_filled is False


def test_taker_walk_rejects_invalid_fee_evidence() -> None:
    with pytest.raises(ExecutionCostIntegrityError, match="fee callback"):
        walk_taker_ask_levels(
            ((Decimal("0.50"), Decimal("10")),),
            target_notional_usd=Decimal("5"),
            fee_fn=lambda _shares, _price: Decimal("-0.01"),
        )


@pytest.mark.parametrize("probability", [Decimal("0"), Decimal("1"), Decimal("NaN")])
def test_side_probability_is_strictly_inside_unit_interval(probability: Decimal) -> None:
    with pytest.raises(ExecutionCostIntegrityError, match="strictly between"):
        purchased_side_probability(probability, TradeSide.BUY_YES)


def test_maker_algebra_satisfies_direct_net_edge_identity() -> None:
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.65"),
        intent_midpoint=Decimal("0.50"),
        legs=(
            _leg(
                1,
                "maker",
                shares="10",
                fill="0.49",
                midpoint="0.51",
                ask="0.52",
                fee="0.10",
            ),
        ),
    )

    assert metrics.aggregate_role == "maker"
    assert metrics.gross_model_edge == Decimal("0.15")
    assert metrics.latency_movement == Decimal("0.01")
    assert metrics.taker_spread == 0
    assert metrics.taker_slippage == 0
    assert metrics.maker_improvement == Decimal("0.02")
    assert metrics.fee_drag == Decimal("0.01")
    assert metrics.realized_net_edge == Decimal("0.15")
    assert metrics.direct_net_edge == Decimal("0.15")
    assert metrics.identity_residual == 0
    assert metrics.identity_holds is True


def test_taker_atomic_legs_are_share_weighted_and_preserve_identity() -> None:
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.65"),
        intent_midpoint=Decimal("0.50"),
        legs=(
            _leg(
                2,
                "taker",
                shares="20",
                fill="0.54",
                midpoint="0.52",
                ask="0.53",
                fee="0.20",
            ),
            _leg(
                1,
                "taker",
                shares="10",
                fill="0.52",
                midpoint="0.51",
                ask="0.52",
                fee="0.10",
            ),
        ),
    )

    assert metrics.aggregate_role == "taker"
    assert metrics.leg_count == 2
    assert metrics.total_shares == Decimal("30")
    assert metrics.filled_notional == Decimal("16.00")
    assert metrics.vwap_fill_price == Decimal("16") / Decimal("30")
    assert metrics.weighted_fill_midpoint == Decimal("15.5") / Decimal("30")
    assert metrics.latency_movement == Decimal("0.5") / Decimal("30")
    assert metrics.taker_spread == Decimal("0.30") / Decimal("30")
    assert metrics.taker_slippage == Decimal("0.20") / Decimal("30")
    assert metrics.maker_improvement == 0
    assert metrics.fee_drag == Decimal("0.30") / Decimal("30")
    assert metrics.realized_net_edge == metrics.direct_net_edge
    assert metrics.identity_holds is True
    assert metrics.identity_residual is not None
    assert abs(metrics.identity_residual) <= COST_IDENTITY_TOLERANCE


def test_canonical_vwap_does_not_divide_quantized_leg_notional() -> None:
    """Regression for the production 0.73 seal rounding failure.

    The six-decimal stored notional rounds up by 2e-7.  Dividing it by
    shares produces 0.7300000254, outside PostgreSQL's exact 5e-9 seal
    tolerance, while the canonical stored shares/price pair is exactly 0.73.
    """

    shares = Decimal("7.874060")
    price = Decimal("0.73000000")
    stored_notional = (shares * price).quantize(Decimal("0.000001"))

    assert stored_notional == Decimal("5.748064")
    assert stored_notional / shares > Decimal("0.7300000253")
    assert canonical_weighted_fill_price(((shares, price),)) == price


def test_mixed_role_algebra_and_expected_slippage_coverage() -> None:
    legs = (
        _leg(
            1,
            "maker",
            shares="10",
            fill="0.49",
            midpoint="0.51",
            ask="0.52",
            fee="0.05",
        ),
        _leg(
            2,
            "taker",
            shares="10",
            fill="0.54",
            midpoint="0.52",
            ask="0.53",
            fee="0.05",
        ),
    )
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.65"),
        intent_midpoint=Decimal("0.50"),
        legs=legs,
        expected_slippage_per_share=Decimal("0.003"),
    )

    assert aggregate_liquidity_role(legs) == "mixed"
    assert metrics.aggregate_role == "mixed"
    assert metrics.vwap_fill_price == Decimal("0.515")
    assert metrics.latency_movement == Decimal("0.015")
    assert metrics.taker_spread == Decimal("0.005")
    assert metrics.taker_slippage == Decimal("0.005")
    assert metrics.maker_improvement == Decimal("0.010")
    assert metrics.fee_drag == Decimal("0.005")
    assert metrics.realized_net_edge == Decimal("0.130")
    assert metrics.identity_residual == 0
    assert metrics.expected_slippage_per_share == Decimal("0.003")
    assert metrics.expected_slippage_coverage == EXPECTED_SLIPPAGE_MODELED
    assert metrics.expected_slippage_unavailable_reason is None


def test_favorable_negative_terms_are_not_clipped() -> None:
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.60"),
        intent_midpoint=Decimal("0.50"),
        legs=(
            _leg(
                1,
                "maker",
                shares="5",
                fill="0.50",
                midpoint="0.49",
                ask="0.51",
                fee="0",
            ),
            _leg(
                2,
                "taker",
                shares="5",
                fill="0.50",
                midpoint="0.49",
                ask="0.51",
                fee="0",
            ),
        ),
    )

    assert metrics.latency_movement == Decimal("-0.01")
    assert metrics.taker_slippage == Decimal("-0.005")
    assert metrics.maker_improvement == Decimal("-0.005")
    assert metrics.realized_net_edge == Decimal("0.10")
    assert metrics.identity_holds is True


def test_unknown_fee_stays_null_instead_of_becoming_zero() -> None:
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.65"),
        intent_midpoint=Decimal("0.50"),
        legs=(
            _leg(
                1,
                "taker",
                shares="10",
                fill="0.52",
                midpoint="0.51",
                ask="0.52",
                fee=None,
            ),
        ),
    )

    assert metrics.total_fee_usd is None
    assert metrics.fee_per_share is None
    assert metrics.fee_drag is None
    assert metrics.realized_net_edge is None
    assert metrics.direct_net_edge is None
    assert metrics.identity_residual is None
    assert metrics.identity_holds is None
    assert metrics.fee_coverage == FEE_COVERAGE_MISSING
    assert metrics.expected_slippage_per_share is None
    assert metrics.expected_slippage_coverage == EXPECTED_SLIPPAGE_MISSING
    assert (
        metrics.expected_slippage_unavailable_reason
        == EXPECTED_SLIPPAGE_NOT_MODELED
    )


def test_known_zero_fee_is_complete_evidence() -> None:
    metrics = calculate_seal_metrics(
        side_model_probability=Decimal("0.65"),
        intent_midpoint=Decimal("0.50"),
        legs=(
            _leg(
                1,
                "maker",
                shares="10",
                fill="0.49",
                midpoint="0.51",
                ask="0.52",
                fee="0",
            ),
        ),
    )

    assert metrics.total_fee_usd == 0
    assert metrics.fee_drag == 0
    assert metrics.fee_coverage == FEE_COVERAGE_COMPLETE
    assert metrics.realized_net_edge is not None


def test_unknown_role_fails_closed_and_sequences_are_unique() -> None:
    unknown = _leg(
        1,
        "unknown",
        shares="10",
        fill="0.50",
        midpoint="0.50",
        ask="0.51",
        fee="0",
    )
    assert aggregate_liquidity_role((unknown,)) == "unknown"
    with pytest.raises(ExecutionCostIntegrityError, match="unknown liquidity"):
        calculate_seal_metrics(
            side_model_probability=Decimal("0.60"),
            intent_midpoint=Decimal("0.50"),
            legs=(unknown,),
        )
    duplicate = _leg(
        1,
        "maker",
        shares="1",
        fill="0.50",
        midpoint="0.50",
        ask="0.51",
        fee="0",
    )
    with pytest.raises(ExecutionCostIntegrityError, match="unique"):
        aggregate_liquidity_role((duplicate, duplicate))


def test_adverse_selection_sign_and_all_or_nothing_coverage() -> None:
    legs = (
        _leg(
            1,
            "maker",
            shares="10",
            fill="0.49",
            midpoint="0.51",
            ask="0.52",
            fee="0",
        ),
        _leg(
            2,
            "taker",
            shares="20",
            fill="0.54",
            midpoint="0.52",
            ask="0.53",
            fee="0",
        ),
    )

    complete = calculate_adverse_selection(
        legs,
        {1: Decimal("0.50"), 2: Decimal("0.49")},
    )
    assert complete.coverage == MARKOUT_COVERAGE_COMPLETE
    assert complete.observed_leg_count == 2
    assert complete.total_leg_count == 2
    assert complete.adverse_selection == Decimal("0.70") / Decimal("30")
    assert complete.adverse_selection is not None
    assert complete.adverse_selection > 0

    favorable = calculate_adverse_selection(
        legs,
        {1: Decimal("0.53"), 2: Decimal("0.54")},
    )
    assert favorable.adverse_selection == Decimal("-0.02")

    missing = calculate_adverse_selection(legs, {1: Decimal("0.50")})
    assert missing.coverage == MARKOUT_COVERAGE_MISSING
    assert missing.adverse_selection is None
    assert missing.observed_leg_count == 1
    assert missing.total_leg_count == 2


def test_adverse_selection_rejects_wrong_leg_identity() -> None:
    leg = _leg(
        1,
        "maker",
        shares="1",
        fill="0.49",
        midpoint="0.51",
        ask="0.52",
        fee="0",
    )
    with pytest.raises(ExecutionCostIntegrityError, match="unknown leg"):
        calculate_adverse_selection((leg,), {1: Decimal("0.50"), 2: Decimal("0.50")})


def test_postgres_canonical_hash_is_stable_and_domain_separated() -> None:
    first = {
        "longer_key": Decimal("0.5000"),
        "id": 7,
        "at": datetime(2026, 7, 15, 4, 5, 6, 120000, tzinfo=UTC),
        "side": TradeSide.BUY_NO,
        "nested": {"bb": True, "a": None},
    }
    second = dict(reversed(tuple(first.items())))

    assert postgres_jsonb_text(first) == (
        '{"at": "2026-07-15T04:05:06.12+00:00", "id": 7, '
        '"side": "buy_no", "nested": {"a": null, "bb": true}, '
        '"longer_key": 0.5000}'
    )
    assert source_payload_hash(first) == source_payload_hash(second)
    assert execution_record_hash("intent", first) == execution_record_hash(
        "intent", second
    )
    assert execution_record_hash("intent", first) != execution_record_hash(
        "fill-leg", first
    )


def test_record_hash_rejects_ambiguous_or_non_db_values() -> None:
    with pytest.raises(ExecutionCostIntegrityError, match="override"):
        execution_record_hash("intent", {"definition_hash": "bad"})
    with pytest.raises(ExecutionCostIntegrityError, match="timezone-aware"):
        source_payload_hash({"at": datetime(2026, 7, 15, 4, 5, 6)})
    with pytest.raises(TypeError, match="Decimal"):
        source_payload_hash({"price": 0.5})
