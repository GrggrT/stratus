from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from stratus.backtesting.engine import OrderStyle
from stratus.backtesting.research import (
    MovingAverageCandidateSpec,
    ResearchExecutionConfig,
    ResearchExperimentSpec,
    ResearchSelectionPolicy,
)
from stratus.backtesting.windows import (
    walk_forward_plan_from_payload,
    walk_forward_plan_payload,
)
from stratus.jobs.research_binance import research_spec_from_payload


def _execution(**changes: object) -> ResearchExecutionConfig:
    values: dict[str, object] = {
        "initial_quote_per_symbol": Decimal("1000.00"),
        "order_notional": Decimal("100.0"),
        "maker_fee_bps": Decimal("1.000"),
        "taker_fee_bps": Decimal("10.00"),
        "entry_slippage_bps": Decimal("5.0"),
        "exit_slippage_bps": Decimal("5.0"),
        "latency_ms": 250,
        "order_ttl_seconds": 20,
        "maker_participation_rate": Decimal("0.0500"),
        "liquidate_at_end": True,
    }
    values.update(changes)
    return ResearchExecutionConfig(**values)  # type: ignore[arg-type]


def _spec(*, offset: timezone = UTC) -> ResearchExperimentSpec:
    return ResearchExperimentSpec(
        name="binance-ma-v1",
        symbols=("ETHUSDT", "BTCUSDT"),
        interval="5m",
        dataset_start=datetime(2024, 1, 1, tzinfo=offset),
        dataset_end=datetime(2025, 1, 1, tzinfo=offset),
        minimum_train=timedelta(days=120),
        validation=timedelta(days=30),
        test=timedelta(days=30),
        step=timedelta(days=30),
        final_holdout=timedelta(days=60),
        execution=_execution(),
        selection=ResearchSelectionPolicy(),
        candidates=(
            MovingAverageCandidateSpec(12, 48),
            MovingAverageCandidateSpec(18, 72, OrderStyle.MAKER, Decimal("1.00")),
        ),
    )


def test_research_contract_canonicalizes_decimals_symbols_and_timezones() -> None:
    utc_spec = _spec()
    plus_two = timezone(timedelta(hours=2))
    shifted_spec = replace(
        utc_spec,
        dataset_start=utc_spec.dataset_start.astimezone(plus_two),
        dataset_end=utc_spec.dataset_end.astimezone(plus_two),
    )

    assert utc_spec.symbols == ("BTCUSDT", "ETHUSDT")
    assert utc_spec.execution.to_payload()["initial_quote_per_symbol"] == "1000"
    assert utc_spec.execution.to_payload()["maker_participation_rate"] == "0.05"
    assert utc_spec.experiment_key == shifted_spec.experiment_key
    assert utc_spec.plan.plan_hash == shifted_spec.plan.plan_hash


def test_research_payload_round_trips_and_rejects_ambiguous_types() -> None:
    execution = _execution()
    payload = execution.to_payload()

    assert ResearchExecutionConfig.from_payload(payload) == execution
    payload["liquidate_at_end"] = "false"
    with pytest.raises(ValueError, match="must be a boolean"):
        ResearchExecutionConfig.from_payload(payload)

    selection = ResearchSelectionPolicy()
    assert ResearchSelectionPolicy.from_payload(selection.to_payload()) == selection
    candidate = MovingAverageCandidateSpec(12, 48, maker_offset_bps=Decimal("1.00"))
    assert MovingAverageCandidateSpec.from_payload(candidate.to_payload()) == candidate


def test_walk_forward_contract_round_trip_recomputes_hash() -> None:
    plan = _spec().plan
    restored = walk_forward_plan_from_payload(walk_forward_plan_payload(plan))

    assert restored == plan

    payload = walk_forward_plan_payload(plan)
    payload["folds"][0]["test"][1] = payload["folds"][0]["test"][0]
    with pytest.raises(ValueError, match="start must be before end"):
        walk_forward_plan_from_payload(payload)


def test_research_contract_rejects_duplicate_candidates_and_nonfinite_values() -> None:
    with pytest.raises(ValueError, match="finite"):
        _execution(entry_slippage_bps=Decimal("NaN"))

    candidate = MovingAverageCandidateSpec(12, 48)
    spec = _spec()
    with pytest.raises(ValueError, match="duplicates"):
        ResearchExperimentSpec(
            name="duplicate-candidates",
            symbols=("BTCUSDT",),
            interval=spec.interval,
            dataset_start=spec.dataset_start,
            dataset_end=spec.dataset_end,
            minimum_train=spec.minimum_train,
            validation=spec.validation,
            test=spec.test,
            step=spec.step,
            final_holdout=spec.final_holdout,
            execution=spec.execution,
            selection=spec.selection,
            candidates=(candidate, candidate),
        )


def test_documented_24_month_contract_has_stable_registered_hashes() -> None:
    path = Path("docs/binance_research_24m.example.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    spec = research_spec_from_payload(payload)

    assert len(spec.plan.folds) == 8
    assert len(spec.candidates) == 9
    assert spec.experiment_key == (
        "e229d60ad8c201a57fc0485dccce20d2a4cb959997e261ed5c73ad9888458c34"
    )
    assert spec.plan.plan_hash == (
        "cec7fd7944f486f59dee0562317a09d210d96efb0e48ceb91944cf42680e059a"
    )
    assert spec.candidate_set_hash == (
        "ee64413159219bf70098ea19ead77f12a1c64719ef2d650936d3eaa2aee23438"
    )
