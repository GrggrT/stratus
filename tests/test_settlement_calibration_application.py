from datetime import UTC, datetime
from decimal import Decimal

import pytest

from stratus.db.models import SettlementProbabilityCalibrator
from stratus.jobs.recompute_probs import _renormalize_event_probabilities
from stratus.repositories.accuracy_promotions import (
    EffectivePromotion,
    EffectivePromotionKey,
)
from stratus.repositories.model_probs import ModelProbabilityValue


def _value(market_id: int, probability: str) -> ModelProbabilityValue:
    return ModelProbabilityValue(
        market_id=market_id,
        ts=datetime(2026, 7, 12, tzinfo=UTC),
        prob=Decimal(probability),
        sigma=Decimal("1"),
        horizon_h=Decimal("12"),
        components={"shrinkage": {"p_model_raw": float(probability)}},
    )


def _calibrator(*, eligible: bool) -> SettlementProbabilityCalibrator:
    return SettlementProbabilityCalibrator(
        scope_key="global:all:all",
        station_id=None,
        horizon_bucket=None,
        kind=None,
        alpha=Decimal("2"),
        beta=Decimal("0"),
        training_blocks=100,
        training_buckets=300,
        oof_baseline_brier=Decimal("0.18"),
        oof_calibrated_brier=Decimal("0.16"),
        oof_baseline_ece=Decimal("0.08"),
        oof_calibrated_ece=Decimal("0.05"),
        delta_brier_ci_low=Decimal("-0.03"),
        delta_brier_ci_high=Decimal("-0.01"),
        eligible=eligible,
        trained_through=datetime(2026, 7, 12, tzinfo=UTC),
        details={},
    )


def _promotion() -> EffectivePromotion:
    return EffectivePromotion(
        key=EffectivePromotionKey(
            expert="settlement_beta",
            legacy_expert_version="partition-beta-v1",
            expert_version="partition-beta-v1-sealed-v2",
            experiment_fingerprint="f" * 64,
            pipeline_cohort="accuracy-v4+test",
            station_id=1,
            horizon_bucket="6-24h",
            kind="high",
        ),
        activation_id="activation-1",
        definition_hash="d" * 64,
        decision_hash="e" * 64,
        activated_at=datetime(2026, 7, 12, tzinfo=UTC),
    )


def test_ineligible_calibrator_is_shadow_only() -> None:
    values = [_value(1, "0.5"), _value(2, "0.25"), _value(3, "0.25")]

    result = _renormalize_event_probabilities(
        values,
        {1: "event", 2: "event", 3: "event"},
        settlement_calibrator=_calibrator(eligible=False),
    )

    assert [float(item.prob) for item in result] == [0.5, 0.25, 0.25]
    detail = result[0].components["settlement_calibration"]
    assert detail["applied"] is False
    assert detail["shadow_probability"] == pytest.approx(2 / 3, abs=1e-6)
    assert detail["p_final"] == 0.5


def test_only_promoted_calibrator_changes_the_trading_probability() -> None:
    values = [_value(1, "0.5"), _value(2, "0.25"), _value(3, "0.25")]

    result = _renormalize_event_probabilities(
        values,
        {1: "event", 2: "event", 3: "event"},
        settlement_calibrator=_calibrator(eligible=True),
        settlement_promotions={"event": _promotion()},
    )

    assert [float(item.prob) for item in result] == pytest.approx(
        [2 / 3, 1 / 6, 1 / 6], abs=1e-6
    )
    detail = result[0].components["settlement_calibration"]
    assert detail["applied"] is True
    assert detail["activation_id"] == "activation-1"
    assert detail["p_final"] == pytest.approx(2 / 3, abs=1e-6)


def test_rolling_eligible_calibrator_without_activation_stays_shadow_only() -> None:
    values = [_value(1, "0.5"), _value(2, "0.25"), _value(3, "0.25")]

    result = _renormalize_event_probabilities(
        values,
        {1: "event", 2: "event", 3: "event"},
        settlement_calibrator=_calibrator(eligible=True),
    )

    assert [float(item.prob) for item in result] == [0.5, 0.25, 0.25]
    detail = result[0].components["settlement_calibration"]
    assert detail["applied"] is False
    assert detail["rolling_report_eligible"] is True
