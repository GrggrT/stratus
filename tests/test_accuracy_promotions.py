from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from stratus.db.models import (
    AccuracyPrediction,
    AccuracyPromotionActivation,
    AccuracyPromotionLook,
    AccuracyPromotionTrial,
    AccuracyPromotionTrialStatus,
    Market,
    ModelProb,
    WeatherKind,
)
from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    accuracy_prediction_record_hash,
)
from stratus.repositories.accuracy_promotions import (
    CORE_PROMOTION_EXPERTS,
    PROMOTION_FAMILY_ALPHA,
    PROMOTION_REGISTRATION_EPOCH,
    AccuracyPromotionIntegrityError,
    EffectivePromotion,
    EffectivePromotionKey,
    PromotionScope,
    _activate_terminal_family,
    _definition_for_scope,
    _definition_from_trial,
    build_effective_promotion_manifest,
    effective_promotion_for_current_source,
    promotion_family_is_terminal,
    promotion_registration_is_ready,
    promotion_scopes_from_unresolved_rows,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.promotion_protocol import (
    PROMOTION_PROTOCOL_VERSION,
    PROMOTION_TEST_METHOD,
)
from stratus.services.sealed_accuracy import (
    SEALED_EVIDENCE_PROTOCOL,
    sealed_experiment_fingerprint,
    sealed_expert_version,
)

REGISTERED_AT = datetime(2026, 7, 14, 12, tzinfo=UTC)
PREDICTION_AT = REGISTERED_AT - timedelta(minutes=5)
RESOLVES_AT = REGISTERED_AT + timedelta(days=2)
PIPELINE_FINGERPRINT = "0123456789abcdef"
PIPELINE_COHORT = "stratus-accuracy-v4-test+0123456789abcdef"
EXPERT = "regularized_stack"
LEGACY_VERSION = "regularized-brier-v1"
SEALED_VERSION = sealed_expert_version(LEGACY_VERSION)
EXPERIMENT_FINGERPRINT = sealed_experiment_fingerprint(
    pipeline_fingerprint=PIPELINE_FINGERPRINT,
    expert=EXPERT,
    legacy_version=LEGACY_VERSION,
    sealed_version=SEALED_VERSION,
    pipeline_version=PIPELINE_COHORT,
)


def _candidate_row(
    index: int,
    *,
    candidate_probability: str,
    baseline_probability: str,
    expert: str = EXPERT,
    pipeline_fingerprint: str = PIPELINE_FINGERPRINT,
    include_horizon: bool = True,
) -> tuple[AccuracyPrediction, Market, ModelProb]:
    market = Market(
        id=index,
        polymarket_id=f"promotion-market-{index}",
        event_id="promotion-event",
        station_id=17,
        market_date=date(2026, 7, 16),
        kind=WeatherKind.HIGH,
        rules_raw=f"promotion rules {index}",
        resolves_at=RESOLVES_AT,
        resolved_outcome=None,
        resolution_event_id=None,
    )
    baseline = ModelProb(
        id=100 + index,
        market_id=index,
        ts=PREDICTION_AT,
        prob=Decimal(baseline_probability),
        sigma=Decimal("1.2"),
        horizon_h=Decimal("12.1"),
        components={"pipeline": {"cohort": PIPELINE_COHORT}},
        market_snapshot_id=900 + index,
        market_anchor_protocol=CLOB_DUAL_PROTOCOL,
    )
    fingerprint = sealed_experiment_fingerprint(
        pipeline_fingerprint=pipeline_fingerprint,
        expert=expert,
        legacy_version=LEGACY_VERSION,
        sealed_version=SEALED_VERSION,
        pipeline_version=PIPELINE_COHORT,
    )
    distribution: dict[str, object] = {
        "evidence_protocol": SEALED_EVIDENCE_PROTOCOL,
        "legacy_expert_version": LEGACY_VERSION,
        "baseline_pipeline_version": PIPELINE_COHORT,
        "baseline_probability": float(Decimal(baseline_probability)),
        "normalized_baseline_probability": float(Decimal(baseline_probability)),
    }
    if include_horizon:
        distribution["horizon_bucket"] = "6-24h"
    value = AccuracyPredictionValue(
        market_id=index,
        ts=PREDICTION_AT,
        expert=expert,
        expert_version=SEALED_VERSION,
        probability=Decimal(candidate_probability),
        mean_c=Decimal("20.0"),
        sigma_c=Decimal("1.2"),
        training_sample_n=120,
        point_in_time_valid=True,
        distribution=distribution,
        generated_at=REGISTERED_AT - timedelta(minutes=1),
        training_cutoff=PREDICTION_AT,
        baseline_model_prob_id=baseline.id,
        input_hash="a" * 64,
        experiment_fingerprint=fingerprint,
        evidence_eligible=True,
    )
    prediction = AccuracyPrediction(
        id=200 + index,
        market_id=value.market_id,
        ts=value.ts,
        expert=value.expert,
        expert_version=value.expert_version,
        probability=value.probability,
        mean_c=value.mean_c,
        sigma_c=value.sigma_c,
        training_sample_n=value.training_sample_n,
        point_in_time_valid=value.point_in_time_valid,
        generated_at=value.generated_at,
        training_cutoff=value.training_cutoff,
        baseline_model_prob_id=value.baseline_model_prob_id,
        input_hash=value.input_hash,
        experiment_fingerprint=value.experiment_fingerprint,
        evidence_eligible=value.evidence_eligible,
        distribution=value.distribution,
        record_hash=accuracy_prediction_record_hash(value),
    )
    return prediction, market, baseline


def _complete_rows() -> list[tuple[AccuracyPrediction, Market, ModelProb]]:
    return [
        _candidate_row(
            1,
            candidate_probability="0.200000",
            baseline_probability="0.250000",
        ),
        _candidate_row(
            2,
            candidate_probability="0.300000",
            baseline_probability="0.250000",
        ),
        _candidate_row(
            3,
            candidate_probability="0.500000",
            baseline_probability="0.500000",
        ),
    ]


def test_registration_discovers_only_complete_current_code_scope() -> None:
    rows = _complete_rows()

    scopes = promotion_scopes_from_unresolved_rows(
        rows,
        event_market_ids={"promotion-event": {1, 2, 3}},
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        registered_at=REGISTERED_AT,
    )

    assert scopes == (
        PromotionScope(
            expert=EXPERT,
            legacy_expert_version=LEGACY_VERSION,
            expert_version=SEALED_VERSION,
            experiment_fingerprint=EXPERIMENT_FINGERPRINT,
            baseline_pipeline_cohort=PIPELINE_COHORT,
            station_id=17,
            horizon_bucket="6-24h",
            kind="high",
        ),
    )
    assert scopes[0].scope_key == "17:6-24h:high"


def test_registration_fails_closed_for_incomplete_stale_or_unpromotable_rows() -> None:
    rows = _complete_rows()
    expected = {"promotion-event": {1, 2, 3}}

    assert not promotion_scopes_from_unresolved_rows(
        rows[:2],
        event_market_ids=expected,
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        registered_at=REGISTERED_AT,
    )
    assert not promotion_scopes_from_unresolved_rows(
        rows,
        event_market_ids=expected,
        pipeline_fingerprint="stale-code-fingerprint",
        registered_at=REGISTERED_AT,
    )
    diagnostic_rows = [
        _candidate_row(
            index,
            candidate_probability=candidate,
            baseline_probability=baseline,
            expert="censored_gaussian",
        )
        for index, candidate, baseline in (
            (1, "0.200000", "0.250000"),
            (2, "0.300000", "0.250000"),
            (3, "0.500000", "0.500000"),
        )
    ]
    assert not promotion_scopes_from_unresolved_rows(
        diagnostic_rows,
        event_market_ids=expected,
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        registered_at=REGISTERED_AT,
    )


def test_registration_excludes_rows_without_exact_horizon_metadata() -> None:
    rows = _complete_rows()
    rows[0] = _candidate_row(
        1,
        candidate_probability="0.200000",
        baseline_probability="0.250000",
        include_horizon=False,
    )

    assert not promotion_scopes_from_unresolved_rows(
        rows,
        event_market_ids={"promotion-event": {1, 2, 3}},
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        registered_at=REGISTERED_AT,
    )


def test_registration_readiness_requires_core_roster_but_not_conditional_regime() -> None:
    base = PromotionScope(
        expert=EXPERT,
        legacy_expert_version=LEGACY_VERSION,
        expert_version=SEALED_VERSION,
        experiment_fingerprint=EXPERIMENT_FINGERPRINT,
        baseline_pipeline_cohort=PIPELINE_COHORT,
        station_id=17,
        horizon_bucket="6-24h",
        kind="high",
    )
    core = [replace(base, expert=expert) for expert in sorted(CORE_PROMOTION_EXPERTS)]

    assert promotion_registration_is_ready(core)
    assert promotion_registration_is_ready([*core, replace(base, expert="regime_emos")])
    assert not promotion_registration_is_ready(core[:-1])


@pytest.mark.parametrize("family_size", [1, 3, 303, 4999])
def test_definition_alpha_roundtrip_never_exceeds_global_family_budget(
    family_size: int,
) -> None:
    scope = PromotionScope(
        expert=EXPERT,
        legacy_expert_version=LEGACY_VERSION,
        expert_version=SEALED_VERSION,
        experiment_fingerprint=EXPERIMENT_FINGERPRINT,
        baseline_pipeline_cohort=PIPELINE_COHORT,
        station_id=17,
        horizon_bucket="6-24h",
        kind="high",
    )
    definition = _definition_for_scope(
        scope,
        registered_at=REGISTERED_AT,
        family_member_index=1,
        family_size=family_size,
    )
    trial = AccuracyPromotionTrial(
        protocol_version=PROMOTION_PROTOCOL_VERSION,
        registration_epoch=PROMOTION_REGISTRATION_EPOCH,
        family_id=definition.family_id,
        experiment_id=definition.experiment_id,
        candidate_id=definition.candidate_id,
        baseline_id=definition.baseline_id,
        pipeline_cohort=definition.pipeline_cohort,
        registered_at=definition.registered_at,
        starts_at=definition.starts_at,
        target_blocks=definition.target_blocks,
        allocated_alpha=Decimal(str(definition.alpha)),
        minimum_brier_improvement=Decimal(str(definition.minimum_brier_improvement)),
        test_method=PROMOTION_TEST_METHOD,
        definition_hash=definition.definition_hash,
        expert=scope.expert,
        legacy_expert_version=scope.legacy_expert_version,
        expert_version=scope.expert_version,
        experiment_fingerprint=scope.experiment_fingerprint,
        station_id=scope.station_id,
        horizon_bucket=scope.horizon_bucket,
        kind=WeatherKind.HIGH,
        scope_key=scope.scope_key,
        family_member_index=1,
        family_size=family_size,
        family_alpha=PROMOTION_FAMILY_ALPHA,
    )

    assert _definition_from_trial(trial).definition_hash == definition.definition_hash
    assert Decimal(str(definition.alpha)) * family_size <= PROMOTION_FAMILY_ALPHA


def test_family_wide_activation_waits_until_every_trial_is_terminal() -> None:
    assert not promotion_family_is_terminal([])
    assert not promotion_family_is_terminal(
        [
            AccuracyPromotionTrial(status=AccuracyPromotionTrialStatus.PASSED),
            AccuracyPromotionTrial(status=AccuracyPromotionTrialStatus.COLLECTING),
        ]
    )
    assert promotion_family_is_terminal(
        [
            AccuracyPromotionTrial(status=AccuracyPromotionTrialStatus.PASSED),
            AccuracyPromotionTrial(status=AccuracyPromotionTrialStatus.FAILED),
        ]
    )


class _ScalarRows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _ScalarRows:
        return self

    def all(self) -> list[Any]:
        return self._rows

    def __iter__(self) -> Iterator[Any]:
        return iter(self._rows)


class _ActivationSession:
    def __init__(self, results: list[list[Any]]) -> None:
        self._results = [_ScalarRows(rows) for rows in results]
        self.execute_calls = 0

    async def execute(self, _statement: object) -> _ScalarRows:
        self.execute_calls += 1
        return self._results.pop(0)

    async def scalar(self, _statement: object) -> datetime:
        return REGISTERED_AT + timedelta(minutes=1)


def _terminal_trial(index: int) -> AccuracyPromotionTrial:
    return AccuracyPromotionTrial(
        id=index,
        status=AccuracyPromotionTrialStatus.PASSED,
        definition_hash=f"{index:x}" * 64,
        family_id=PROMOTION_REGISTRATION_EPOCH,
        experiment_id=f"experiment-{index}",
        candidate_id=f"candidate-{index}",
        baseline_id="baseline",
        pipeline_cohort=PIPELINE_COHORT,
    )


@pytest.mark.asyncio
async def test_family_activation_retry_is_idempotent_and_counts_actual_inserts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trials = [_terminal_trial(1), _terminal_trial(2)]
    looks = [
        # The database trigger owns every derived field; activation needs only
        # each immutable trial/look identity.
        #
        # One returned insert ID below intentionally models a conflict on the
        # other trial, proving the job metric counts actual rows, not attempts.
        AccuracyPromotionLook(
            id=100 + trial.id,
            trial_id=trial.id,
            decision_hash=f"{trial.id + 2:x}" * 64,
        )
        for trial in trials
    ]

    async def load_family(_session: object) -> list[AccuracyPromotionTrial]:
        return trials

    monkeypatch.setattr(
        "stratus.repositories.accuracy_promotions._load_family",
        load_family,
    )
    first = _ActivationSession([[], looks, [], [501]])
    assert await _activate_terminal_family(first) == 1  # type: ignore[arg-type]

    retry = _ActivationSession([[], looks, [1, 2]])
    assert await _activate_terminal_family(retry) == 0  # type: ignore[arg-type]
    assert retry.execute_calls == 3


def _activation(
    *,
    fingerprint: str = EXPERIMENT_FINGERPRINT,
    pipeline_cohort: str = PIPELINE_COHORT,
) -> AccuracyPromotionActivation:
    return AccuracyPromotionActivation(
        activation_id="activation-test",
        definition_hash="b" * 64,
        decision_hash="c" * 64,
        family_id=PROMOTION_REGISTRATION_EPOCH,
        experiment_id="promotion-test",
        candidate_id="candidate-test",
        baseline_id="baseline-test",
        pipeline_cohort=pipeline_cohort,
        expert=EXPERT,
        legacy_expert_version=LEGACY_VERSION,
        expert_version=SEALED_VERSION,
        experiment_fingerprint=fingerprint,
        station_id=17,
        horizon_bucket="6-24h",
        kind=WeatherKind.HIGH,
        activated_at=REGISTERED_AT,
        activated_by="unit-test",
    )


def test_effective_manifest_excludes_stale_code_and_is_deterministic() -> None:
    exact = effective_promotion_for_current_source(
        _activation(),
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        current_pipeline_base_cohort=PIPELINE_COHORT,
    )
    assert exact is not None
    assert (
        effective_promotion_for_current_source(
            _activation(),
            pipeline_fingerprint="changed-source-code",
            current_pipeline_base_cohort=PIPELINE_COHORT,
        )
        is None
    )
    assert (
        effective_promotion_for_current_source(
            _activation(),
            pipeline_fingerprint=PIPELINE_FINGERPRINT,
            current_pipeline_base_cohort=f"next-version+{PIPELINE_FINGERPRINT}",
        )
        is None
    )
    suffixed_cohort = f"{PIPELINE_COHORT}+promotions-{'a' * 16}"
    suffixed_fingerprint = sealed_experiment_fingerprint(
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        expert=EXPERT,
        legacy_version=LEGACY_VERSION,
        sealed_version=SEALED_VERSION,
        pipeline_version=suffixed_cohort,
    )
    assert (
        effective_promotion_for_current_source(
            _activation(
                fingerprint=suffixed_fingerprint,
                pipeline_cohort=suffixed_cohort,
            ),
            pipeline_fingerprint=PIPELINE_FINGERPRINT,
            current_pipeline_base_cohort=PIPELINE_COHORT,
        )
        is not None
    )
    second = EffectivePromotion(
        key=EffectivePromotionKey(
            expert="qrf",
            legacy_expert_version="qrf-v1",
            expert_version="qrf-v1-sealed-v2",
            experiment_fingerprint="d" * 64,
            pipeline_cohort=PIPELINE_COHORT,
            station_id=18,
            horizon_bucket="24-48h",
            kind="low",
        ),
        activation_id="activation-second",
        definition_hash="e" * 64,
        decision_hash="f" * 64,
        activated_at=REGISTERED_AT + timedelta(seconds=1),
    )

    forward = build_effective_promotion_manifest([exact, second])
    reverse = build_effective_promotion_manifest([second, exact])

    assert forward == reverse
    assert forward.index[exact.key] == exact


def test_effective_manifest_rejects_two_activations_for_one_scope() -> None:
    exact = effective_promotion_for_current_source(
        _activation(),
        pipeline_fingerprint=PIPELINE_FINGERPRINT,
        current_pipeline_base_cohort=PIPELINE_COHORT,
    )
    assert exact is not None
    conflicting = EffectivePromotion(
        key=exact.key,
        activation_id="activation-conflict",
        definition_hash="d" * 64,
        decision_hash="e" * 64,
        activated_at=REGISTERED_AT,
    )

    with pytest.raises(
        AccuracyPromotionIntegrityError,
        match="multiple active promotions",
    ):
        build_effective_promotion_manifest([exact, conflicting])
