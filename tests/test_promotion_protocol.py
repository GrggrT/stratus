from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stratus.services.promotion_protocol import (
    PROMOTION_PROTOCOL_EPOCH,
    PROMOTION_PROTOCOL_VERSION,
    PROMOTION_TARGET_BLOCKS,
    PROMOTION_TEST_METHOD,
    PairedBrierBlock,
    PromotionDecisionStatus,
    PromotionDefinition,
    PromotionProtocolError,
    build_activation_manifest,
    create_passed_activation,
    evaluate_fixed_promotion,
    promotion_definition_hash,
)

START = datetime(2026, 7, 15, tzinfo=UTC)


def _definition(
    *,
    experiment_id: str = "trajectory-ar1-v1",
    family_id: str = "trajectory",
    target_blocks: int = PROMOTION_TARGET_BLOCKS,
    alpha: float = 0.025,
    family_member_index: int = 1,
    family_size: int = 2,
) -> PromotionDefinition:
    return PromotionDefinition(
        family_id=family_id,
        experiment_id=experiment_id,
        candidate_id=f"{experiment_id}-candidate",
        baseline_id="production-baseline",
        pipeline_cohort="stratus-accuracy-v5+0123456789abcdef",
        registered_at=START - timedelta(hours=1),
        starts_at=START,
        target_blocks=target_blocks,
        alpha=alpha,
        expert="trajectory_ar1",
        legacy_expert_version="trajectory-ar1-v1",
        expert_version="trajectory-ar1-v1-sealed-v2",
        experiment_fingerprint="a" * 64,
        station_id=1,
        horizon_bucket="0-6h",
        kind="high",
        scope_key="1:0-6h:high",
        family_member_index=family_member_index,
        family_size=family_size,
        family_alpha=0.05,
    )


def _block(
    index: int,
    *,
    delta: float = -0.1,
    lower: float = -0.2,
    upper: float = 0.2,
) -> PairedBrierBlock:
    baseline = 0.25
    return PairedBrierBlock(
        block_key=f"station-day-{index:03d}",
        resolved_cutoff=START + timedelta(days=index),
        baseline_brier=baseline,
        candidate_brier=baseline + delta,
        predictable_improvement_lower=lower,
        predictable_improvement_upper=upper,
        evidence_hash=f"evidence-{index:03d}-{delta:+.3f}",
    )


def test_definition_and_fixed_look_are_deterministic_under_input_reordering() -> None:
    definition = _definition()
    blocks = [_block(index) for index in range(PROMOTION_TARGET_BLOCKS)]

    first = evaluate_fixed_promotion(definition, blocks)
    second = evaluate_fixed_promotion(definition, list(reversed(blocks)))

    assert promotion_definition_hash(definition) == definition.definition_hash
    assert first == second
    assert first.status is PromotionDecisionStatus.PASSED
    assert first.observed_blocks == PROMOTION_TARGET_BLOCKS
    assert first.wins == PROMOTION_TARGET_BLOCKS
    assert first.nonwins == 0
    assert first.mean_improvement_lcb is not None
    assert first.mean_improvement_lcb > 0
    assert first.one_sided_p_bound is not None
    assert first.one_sided_p_bound <= definition.alpha


def test_protocol_identity_and_predictable_range_method_are_frozen() -> None:
    assert PROMOTION_PROTOCOL_VERSION == "fixed-range-brier-v3"
    assert PROMOTION_PROTOCOL_EPOCH == "fixed-range-brier-v3-20260714"
    assert PROMOTION_TEST_METHOD == "predictable-range-hoeffding-v1"


def test_definition_rejects_any_nonfixed_sample_target() -> None:
    with pytest.raises(PromotionProtocolError, match="fixed protocol look 60"):
        _definition(target_blocks=59)


def test_frequent_small_wins_cannot_hide_a_large_mean_loss() -> None:
    blocks = (
        [_block(index, delta=-0.001, lower=-1.0, upper=0.1) for index in range(59)]
        + [_block(59, delta=0.75, lower=-1.0, upper=0.1)]
    )

    decision = evaluate_fixed_promotion(_definition(), blocks)

    assert decision.wins == 59
    assert decision.mean_delta_brier is not None and decision.mean_delta_brier > 0
    assert decision.status is PromotionDecisionStatus.FAILED


def test_predictable_narrow_ranges_have_more_power_than_wide_ranges() -> None:
    definition = _definition()
    narrow = evaluate_fixed_promotion(
        definition,
        [_block(index, delta=-0.04, lower=0.03, upper=0.05) for index in range(60)],
    )
    wide = evaluate_fixed_promotion(
        definition,
        [_block(index, delta=-0.04, lower=-0.2, upper=0.2) for index in range(60)],
    )

    assert narrow.status is PromotionDecisionStatus.PASSED
    assert wide.status is PromotionDecisionStatus.FAILED
    assert narrow.confidence_radius is not None
    assert wide.confidence_radius is not None
    assert narrow.confidence_radius < wide.confidence_radius


def test_duplicate_block_does_not_increase_independent_sample_size() -> None:
    blocks = [_block(index) for index in range(PROMOTION_TARGET_BLOCKS - 1)]
    blocks.append(_block(PROMOTION_TARGET_BLOCKS - 2))

    decision = evaluate_fixed_promotion(_definition(), blocks)

    assert decision.status is PromotionDecisionStatus.INCOMPLETE
    assert decision.observed_blocks == PROMOTION_TARGET_BLOCKS - 1


def test_incomplete_fixed_look_cannot_leak_or_make_a_decision() -> None:
    decision = evaluate_fixed_promotion(
        _definition(),
        [
            _block(index, delta=-0.2, lower=-0.21, upper=0.21)
            for index in range(PROMOTION_TARGET_BLOCKS - 1)
        ],
    )

    assert decision.status is PromotionDecisionStatus.INCOMPLETE
    assert decision.mean_delta_brier is None
    assert decision.wins is None
    assert decision.nonwins is None
    assert decision.one_sided_p_bound is None
    assert decision.mean_improvement_lcb is None
    assert decision.selected_block_keys == ()


def test_failed_fixed_look_cannot_be_reversed_by_later_favorable_blocks() -> None:
    definition = _definition()
    failed = evaluate_fixed_promotion(
        definition,
        [_block(index, delta=0.05) for index in range(PROMOTION_TARGET_BLOCKS)],
    )
    with_later_data = evaluate_fixed_promotion(
        definition,
        [_block(index, delta=0.05) for index in range(PROMOTION_TARGET_BLOCKS)]
        + [
            _block(index, delta=-0.24, lower=-0.2, upper=0.3)
            for index in range(PROMOTION_TARGET_BLOCKS, PROMOTION_TARGET_BLOCKS + 15)
        ],
    )

    assert failed.status is PromotionDecisionStatus.FAILED
    assert with_later_data == failed


def test_per_trial_alpha_above_bonferroni_allocation_is_rejected() -> None:
    with pytest.raises(PromotionProtocolError, match="allocated alpha"):
        _definition(experiment_id="trajectory-a", alpha=0.03, family_member_index=1)


def test_conflicting_duplicate_block_fails_closed() -> None:
    original = _block(0)
    conflict = PairedBrierBlock(
        block_key=original.block_key,
        resolved_cutoff=original.resolved_cutoff,
        baseline_brier=original.baseline_brier,
        candidate_brier=0.24,
        predictable_improvement_lower=-0.2,
        predictable_improvement_upper=0.2,
        evidence_hash="different-evidence",
    )

    with pytest.raises(PromotionProtocolError, match="conflicting evidence"):
        evaluate_fixed_promotion(_definition(), [original, conflict])


def test_activation_manifest_is_stable_and_changes_with_activation_set() -> None:
    first_definition = _definition(
        experiment_id="trajectory-a", alpha=0.02, family_member_index=1
    )
    second_definition = _definition(
        experiment_id="trajectory-b", alpha=0.02, family_member_index=2
    )
    blocks = [_block(index) for index in range(PROMOTION_TARGET_BLOCKS)]
    first_activation = create_passed_activation(
        first_definition,
        evaluate_fixed_promotion(first_definition, blocks),
        activation_id="activation-a",
        activated_at=START + timedelta(days=10),
        activated_by="operator@example.test",
    )
    second_activation = create_passed_activation(
        second_definition,
        evaluate_fixed_promotion(second_definition, blocks),
        activation_id="activation-b",
        activated_at=START + timedelta(days=11),
        activated_by="operator@example.test",
    )

    first_manifest = build_activation_manifest([first_activation])
    reordered = build_activation_manifest([second_activation, first_activation])
    repeated = build_activation_manifest([first_activation, second_activation])

    assert reordered == repeated
    assert first_manifest.manifest_hash != reordered.manifest_hash


def test_failed_decision_cannot_create_activation() -> None:
    definition = _definition()
    decision = evaluate_fixed_promotion(
        definition,
        [_block(index, delta=0.1) for index in range(PROMOTION_TARGET_BLOCKS)],
    )

    with pytest.raises(PromotionProtocolError, match="only a passed"):
        create_passed_activation(
            definition,
            decision,
            activation_id="invalid",
            activated_at=START + timedelta(days=10),
            activated_by="operator@example.test",
        )
