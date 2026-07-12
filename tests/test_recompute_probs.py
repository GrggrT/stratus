from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stratus.jobs.recompute_probs import _renormalize_event_probabilities
from stratus.repositories.model_probs import ModelProbabilityValue

TS = datetime(2026, 7, 12, tzinfo=UTC)


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
