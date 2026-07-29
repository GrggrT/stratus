from __future__ import annotations

import pytest

from stratus.services.settlement_calibration import (
    SettlementEventCase,
    blocked_oof_assessment,
    calibrate_partition,
    expected_calibration_error,
    fit_partition_calibrator,
)


def _cases(count: int = 50) -> list[SettlementEventCase]:
    cases: list[SettlementEventCase] = []
    for index in range(count):
        winner = index % 3
        outcomes = tuple(position == winner for position in range(3))
        # Deliberately underconfident but coherent forecasts. A fitted
        # calibrator should sharpen them out of sample.
        probabilities = tuple(0.50 if outcome else 0.25 for outcome in outcomes)
        cases.append(
            SettlementEventCase(
                key=f"station-day-{index}",
                probabilities=probabilities,
                outcomes=outcomes,
            )
        )
    return cases


def test_identity_partition_calibration_preserves_coherent_probabilities() -> None:
    probabilities = (0.1, 0.3, 0.6)
    calibrated = calibrate_partition(probabilities, alpha=1, beta=0)
    assert calibrated == pytest.approx(probabilities)
    assert sum(calibrated) == pytest.approx(1)


def test_fit_sharpens_repeatably_underconfident_events() -> None:
    fitted = fit_partition_calibrator(_cases())
    assert fitted.alpha > 1
    assert fitted.beta < 0
    calibrated = fitted.calibrate((0.5, 0.25, 0.25))
    assert calibrated[0] > 0.5


def test_blocked_oof_proves_improvement_without_bucket_pseudoreplication() -> None:
    assessment = blocked_oof_assessment(_cases())
    assert assessment.blocks == 50
    assert assessment.buckets == 150
    assert assessment.calibrated_brier < assessment.baseline_brier
    assert assessment.calibrated_ece < assessment.baseline_ece
    assert assessment.delta_ci is not None
    assert assessment.delta_ci[1] < 0


def test_ece_rejects_unaligned_inputs() -> None:
    with pytest.raises(ValueError):
        expected_calibration_error([0.1], [])


def test_event_requires_exactly_one_winning_bucket() -> None:
    with pytest.raises(ValueError):
        SettlementEventCase("bad", (0.5, 0.5), (False, False))
