from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.db.models import (
    ClobMarketSnapshot,
    Market,
    MarketStatus,
    WeatherKind,
)
from stratus.jobs.recompute_probs import (
    _daily_actual_training_statement,
    _fit_stack_candidates,
    _materialize_event_probability_coherence,
    _merge_training_actuals,
    _renormalize_event_probabilities,
    _seasonal_values,
    _stack_gate_passed,
)
from stratus.repositories.model_probs import ModelProbabilityValue
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.event_probability_coherence import EventCoherenceReason

TS = datetime(2026, 7, 12, tzinfo=UTC)


def test_training_actual_query_excludes_reconciled_conflicts() -> None:
    statement = _daily_actual_training_statement(
        station_ids={7, 8},
        earliest_date=date(2025, 1, 1),
        truth_cutoff=TS,
    )

    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "NOT (EXISTS (SELECT resolution_reconciliations.id" in sql
    assert "resolution_reconciliations.station_id = daily_actuals.station_id" in sql
    assert "resolution_reconciliations.market_date = daily_actuals.date" in sql
    assert "resolution_reconciliations.kind = daily_actuals.kind" in sql
    assert "'clear_mismatch'" in sql
    assert "'exact_truth_gap'" in sql
    assert "'exact_source_mismatch'" in sql
    assert "'boundary_sensitive'" not in sql
    assert "NOT (EXISTS (SELECT settlement_actuals.id" in sql
    assert "settlement_actuals.station_id = daily_actuals.station_id" in sql


def test_exact_truth_replaces_fallback_once_in_seasonal_history() -> None:
    actual_date = date(2025, 7, 11)
    lookup, seasonal = _merge_training_actuals(
        fallback_rows=[(7, actual_date, WeatherKind.HIGH, Decimal("25"))],
        exact_rows=[(7, actual_date, WeatherKind.HIGH, Decimal("26"))],
    )

    assert lookup[(7, actual_date, WeatherKind.HIGH)] == 26.0
    assert seasonal[(7, WeatherKind.HIGH)] == [(actual_date, 26.0)]
    assert _seasonal_values(
        seasonal[(7, WeatherKind.HIGH)], date(2026, 7, 11)
    ) == [26.0]


def _value(market_id: int, shrunk: str, raw: float) -> ModelProbabilityValue:
    return ModelProbabilityValue(
        market_id=market_id,
        ts=TS,
        prob=Decimal(shrunk),
        sigma=Decimal("1.5"),
        horizon_h=Decimal("12"),
        components={"shrinkage": {"p_model_raw": raw, "applied": True}},
    )


def test_event_probabilities_renormalize_to_raw_total() -> None:
    # Two buckets of one event, each shrunk to 0.53 (sum 1.06 vig); raw model
    # summed to 1.0. Expect rescale to ~0.5 each and preserved metadata.
    values = [_value(1, "0.53", 0.5), _value(2, "0.53", 0.5)]
    result = _renormalize_event_probabilities(values, {1: "ev", 2: "ev"})

    total = sum(float(value.prob) for value in result)
    assert total == pytest.approx(1.0, abs=1e-6)
    scale = result[0].components["shrinkage"]["event_scale"]
    assert scale == pytest.approx(1.0 / 1.06, abs=1e-6)
    assert "p_final" in result[0].components["shrinkage"]
    # Original raw metadata is preserved.
    assert result[0].components["shrinkage"]["p_model_raw"] == 0.5


def test_separate_events_are_normalized_independently() -> None:
    values = [
        _value(1, "0.53", 0.5),
        _value(2, "0.53", 0.5),
        _value(3, "0.8", 0.75),  # lone bucket of another event: untouched
    ]
    result = _renormalize_event_probabilities(
        values, {1: "ev-a", 2: "ev-a", 3: "ev-b"}
    )
    assert sum(float(result[i].prob) for i in (0, 1)) == pytest.approx(1.0, abs=1e-6)
    # Single-bucket event keeps its value and gets no scale.
    assert result[2].prob == Decimal("0.8")
    assert "event_scale" not in result[2].components["shrinkage"]


def test_markets_without_event_mapping_are_left_alone() -> None:
    values = [_value(1, "0.53", 0.5), _value(2, "0.53", 0.5)]
    result = _renormalize_event_probabilities(values, {})
    assert [value.prob for value in result] == [Decimal("0.53"), Decimal("0.53")]


def _coherence_market(
    market_id: int,
    *,
    low: str | None,
    high: str | None,
) -> Market:
    return Market(
        id=market_id,
        polymarket_id=f"materialized-{market_id}",
        event_id="materialized-event",
        station_id=17,
        market_date=date(2026, 7, 16),
        kind=WeatherKind.HIGH,
        rules_raw="complete event partition",
        resolves_at=TS + timedelta(days=2),
        resolution_source="https://example.test/final-observation",
        bucket_low_c=Decimal(low) if low is not None else None,
        bucket_high_c=Decimal(high) if high is not None else None,
        status=MarketStatus.ACTIVE,
        station_verified=True,
    )


def _coherence_value(market_id: int, probability: str) -> ModelProbabilityValue:
    return ModelProbabilityValue(
        market_id=market_id,
        ts=TS,
        prob=Decimal(probability),
        sigma=Decimal("1.5"),
        horizon_h=Decimal("12"),
        components={"pipeline": {"cohort": "accuracy-v4+materialized"}},
        market_snapshot_id=1000 + market_id,
        market_anchor_protocol=CLOB_DUAL_PROTOCOL,
    )


def _coherence_fixture() -> tuple[
    list[Market], dict[int, tuple[ClobMarketSnapshot, datetime]]
]:
    markets = [
        _coherence_market(1, low=None, high="20"),
        _coherence_market(2, low="20", high="21"),
        _coherence_market(3, low="21", high=None),
    ]
    prices = {
        market.id: (
            ClobMarketSnapshot(
                id=1000 + market.id,
                market_id=market.id,
                batch_id=700,
                eligible=True,
            ),
            TS,
        )
        for market in markets
    }
    return markets, prices


def test_final_event_coherence_is_materialized_in_model_values() -> None:
    markets, prices = _coherence_fixture()
    values = [
        _coherence_value(1, "0.160462"),
        _coherence_value(2, "0.300000"),
        _coherence_value(3, "0.539550"),
    ]

    materialization = _materialize_event_probability_coherence(
        values,
        markets,
        eligible_market_ids={1, 2, 3},
        latest_prices=prices,
    )

    result = list(materialization.values)
    assert materialization.valid_event_ids == frozenset({"materialized-event"})
    assert materialization.blocked_event_ids == frozenset()
    assert sum((value.prob for value in result), start=Decimal("0")) == 1
    assert result[0].prob == Decimal("0.160460")
    audit = result[0].components["event_coherence"]
    assert audit["passed"] is True
    assert audit["applied"] is True
    assert audit["raw_probability"] == "0.160462"
    assert audit["final_probability"] == "0.160460"
    assert audit["event_total_before"] == "1.000012"
    assert audit["event_total_final"] == "1.000000"
    assert audit["normalization_scale"] is not None
    assert isinstance(audit["distribution_sha256"], str)
    assert len(audit["distribution_sha256"]) == 64


def test_incomplete_event_keeps_raw_probability_and_records_block() -> None:
    markets, prices = _coherence_fixture()
    values = [
        _coherence_value(1, "0.160462"),
        _coherence_value(3, "0.539550"),
    ]

    materialization = _materialize_event_probability_coherence(
        values,
        markets,
        eligible_market_ids={1, 2, 3},
        latest_prices=prices,
    )

    result = list(materialization.values)
    assert materialization.valid_event_ids == frozenset()
    assert materialization.blocked_event_ids == frozenset({"materialized-event"})
    assert [value.prob for value in result] == [
        Decimal("0.160462"),
        Decimal("0.539550"),
    ]
    audit = result[0].components["event_coherence"]
    assert audit["passed"] is False
    assert audit["applied"] is False
    assert audit["reason"] == EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE.value
    assert audit["final_probability"] == audit["raw_probability"]


def test_trade_blocked_event_still_materializes_accuracy_vector() -> None:
    markets, _prices = _coherence_fixture()
    values = [
        _coherence_value(1, "0.160462"),
        _coherence_value(2, "0.300000"),
        _coherence_value(3, "0.539550"),
    ]

    materialization = _materialize_event_probability_coherence(
        values,
        markets,
        eligible_market_ids=set(),
        latest_prices={},
    )

    result = list(materialization.values)
    assert materialization.valid_event_ids == frozenset()
    assert materialization.blocked_event_ids == frozenset({"materialized-event"})
    assert sum((value.prob for value in result), start=Decimal("0")) == 1
    audit = result[0].components["event_coherence"]
    assert audit["materialized"] is True
    assert audit["materialization_reason"] is None
    assert audit["passed"] is False
    assert audit["reason"] == EventCoherenceReason.BUCKET_INELIGIBLE.value
    assert isinstance(audit["distribution_sha256"], str)


@pytest.mark.parametrize(
    ("has_weights", "promotion_active", "expected"),
    [
        (False, True, False),
        (True, False, False),
        (True, True, True),
    ],
)
def test_stack_requires_immutable_promotion(
    has_weights: bool,
    promotion_active: bool,
    expected: bool,
) -> None:
    assert (
        _stack_gate_passed(
            has_fitted_weights=has_weights,
            promotion_active=promotion_active,
        )
        is expected
    )


def _stack_cases(count: int, *, offset: float = 0.0) -> list[tuple[dict[str, float], bool]]:
    return [
        (
            {
                "model": min(0.95, max(0.05, (0.75 if outcome else 0.25) + offset)),
                "market": 0.7 if outcome else 0.3,
            },
            outcome,
        )
        for outcome in (index % 2 == 0 for index in range(count))
    ]


def test_stack_uses_leave_one_station_out_peer_pool() -> None:
    target = (1, "0-6h", WeatherKind.HIGH)
    cases = {
        target: _stack_cases(2, offset=0.1),
        (2, "0-6h", WeatherKind.HIGH): _stack_cases(2),
        (3, "0-6h", WeatherKind.HIGH): _stack_cases(2),
        (4, "0-6h", WeatherKind.HIGH): _stack_cases(2),
    }
    fits = _fit_stack_candidates(
        cases,
        {target},
        minimum_samples=6,
        minimum_peer_stations=3,
        l2=0.05,
        learning_rate=0.2,
    )

    fit = fits[target]
    assert fit.source == "peer_pool"
    assert fit.local_sample_n == 2
    assert fit.peer_sample_n == 6
    assert fit.peer_stations == 3
    assert set(fit.weights) == {"market", "model"}


def test_stack_prefers_sufficient_local_history() -> None:
    target = (1, "0-6h", WeatherKind.HIGH)
    fits = _fit_stack_candidates(
        {target: _stack_cases(6)},
        {target},
        minimum_samples=6,
        minimum_peer_stations=3,
        l2=0.05,
        learning_rate=0.2,
    )

    fit = fits[target]
    assert fit.source == "local"
    assert fit.training_sample_n == 6
    assert fit.peer_sample_n == 0
    assert fit.peer_stations == 0


def test_stack_peer_pool_requires_three_other_stations() -> None:
    target = (1, "0-6h", WeatherKind.HIGH)
    cases = {
        target: _stack_cases(2),
        (2, "0-6h", WeatherKind.HIGH): _stack_cases(3),
        (3, "0-6h", WeatherKind.HIGH): _stack_cases(3),
    }
    fits = _fit_stack_candidates(
        cases,
        {target},
        minimum_samples=6,
        minimum_peer_stations=3,
        l2=0.05,
        learning_rate=0.2,
    )

    assert target not in fits
