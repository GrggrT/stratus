from datetime import UTC, datetime, timedelta

from stratus.db.models import (
    ExecutionMode,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    WeatherKind,
)
from stratus.jobs.audit_health import (
    ACCURACY_HASH_ATTESTATION_OVERLAP,
    ACCURACY_HASH_ATTESTATION_WINDOW,
    LEGACY_KILL_SWITCH_ISSUE_CODES,
    _clear_mismatch_station_is_contained,
    accuracy_hash_attestation_start,
    empirical_ops_health_issues,
    execution_cost_health_issues,
    exposure_breach_severity,
    health_severity_for_code,
    invalid_event_probability_sums,
    invalid_forecast_input_provenance,
    invalid_promotion_provenance,
    is_hard_exposure_breach,
    is_trade_blocking_drift,
    notification_delivery_health_issues,
    official_resolution_health_issues,
)
from stratus.repositories.accuracy_promotions import (
    EffectivePromotion,
    EffectivePromotionKey,
    EffectivePromotionManifest,
    build_effective_promotion_manifest,
)
from stratus.repositories.empirical_ops import (
    ResolutionReconciliationSummary,
    SettlementCasesSummary,
)
from stratus.repositories.execution_cost_health import (
    ExecutionCostHealthSummary,
    ExecutionCostModeCoverage,
)
from stratus.repositories.official_resolution import OfficialResolutionEvidenceSummary
from stratus.repositories.telegram import NotificationDeliverySummary
from stratus.services.health import HealthSeverity, health_status_requires_kill_switch


def test_accuracy_hash_attestation_uses_incremental_watermark_with_overlap() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    latest_audit_ts = now - timedelta(minutes=10)

    assert (
        accuracy_hash_attestation_start(
            now=now,
            latest_audit_ts=latest_audit_ts,
        )
        == latest_audit_ts - ACCURACY_HASH_ATTESTATION_OVERLAP
    )


def test_accuracy_hash_attestation_respects_window_and_active_mismatch() -> None:
    now = datetime(2026, 7, 28, 12, tzinfo=UTC)
    lower_bound = now - ACCURACY_HASH_ATTESTATION_WINDOW

    assert (
        accuracy_hash_attestation_start(
            now=now,
            latest_audit_ts=now - timedelta(days=181),
        )
        == lower_bound
    )
    assert (
        accuracy_hash_attestation_start(
            now=now,
            latest_audit_ts=now - timedelta(minutes=10),
            latest_audit_issues=[
                {
                    "code": "accuracy_evidence_hash_mismatch",
                    "detail": "1 invalid",
                }
            ],
        )
        == lower_bound
    )


def _official_summary(**overrides: object) -> OfficialResolutionEvidenceSummary:
    values: dict[str, object] = {
        "runtime_opened_at": datetime(2026, 7, 15, tzinfo=UTC),
        "definition_version": "official-resolution-evidence-v1",
        "definition_hash": "a" * 64,
        "license_protocol": "official-resolution-license-v1",
        "response_protocol": "official-resolution-source-v1",
        "extraction_protocol": "official-resolution-extraction-v1",
        "active_contracts": 155,
        "licensed_contracts": 0,
        "expired_contracts": 0,
        "license_attestations": 0,
        "source_responses": 0,
        "exact_actuals": 0,
        "projected_settlement_actuals": 0,
        "raw_response_bytes": 0,
        "latest_response_at": None,
        "latest_exact_actual_at": None,
    }
    values.update(overrides)
    return OfficialResolutionEvidenceSummary(**values)  # type: ignore[arg-type]


def test_missing_external_resolution_authority_is_honest_but_nonblocking() -> None:
    issues = official_resolution_health_issues(_official_summary())
    assert [issue["code"] for issue in issues] == [
        "official_resolution_authorization_blocked"
    ]
    assert health_severity_for_code(issues[0]["code"]) is HealthSeverity.INFO


def test_broken_official_resolution_chain_is_blocking() -> None:
    issues = official_resolution_health_issues(
        _official_summary(
            licensed_contracts=2,
            license_attestations=2,
            source_responses=2,
            exact_actuals=1,
            projected_settlement_actuals=0,
            expired_contracts=1,
        )
    )
    assert [issue["code"] for issue in issues] == [
        "official_resolution_evidence_integrity",
        "official_resolution_evidence_backlog",
        "official_resolution_license_expired",
    ]
    assert health_severity_for_code(issues[0]["code"]) is HealthSeverity.BLOCKING
    assert health_status_requires_kill_switch(
        health_severity_for_code(issues[0]["code"]).value
    )


def test_probability_sum_health_check() -> None:
    assert invalid_event_probability_sums({"good": [0.2, 0.3, 0.5], "bad": [0.2, 0.3]}) == ["bad"]


def test_forecast_provenance_health_applies_only_after_runtime_boundary() -> None:
    boundary = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)
    complete = {
        "forecast_provenance": {
            "protocol": "forecast-input-union-v1",
            "complete": True,
            "input_count": 1,
            "missing_evidence_count": 0,
            "ingest_ids": [5],
            "inputs": [
                {
                    "row_id": 7,
                    "run_id": 3,
                    "ingest_id": 5,
                    "normalized_manifest_sha256": "a" * 64,
                    "ingest_record_hash": "b" * 64,
                }
            ],
        }
    }
    assert invalid_forecast_input_provenance(
        [
            (1, boundary, complete),
            (2, boundary, {}),
            (3, boundary.replace(hour=5), {}),
        ],
        boundary=boundary,
    ) == [2]


def test_shadow_regime_shift_does_not_block_trading() -> None:
    assert is_trade_blocking_drift("residual_bias_shift") is False
    assert is_trade_blocking_drift("brier_degradation") is True


def test_exposure_warning_and_hard_stop_are_distinct() -> None:
    assert is_hard_exposure_breach(59.36, 53.16, 1.25) is False
    assert is_hard_exposure_breach(70.00, 53.16, 1.25) is True
    assert exposure_breach_severity(59.36, 53.16, 1.25) is HealthSeverity.WARNING
    assert exposure_breach_severity(70.00, 53.16, 1.25) is HealthSeverity.BLOCKING


def test_every_previous_kill_switch_condition_remains_blocking() -> None:
    assert LEGACY_KILL_SWITCH_ISSUE_CODES == {
        "weekly_loss_limit",
        "resolution_contract_missing",
        "gamma_outcome_evidence_hash_mismatch",
        "clob_evidence_hash_mismatch",
        "gamma_outcome_projection_mismatch",
        "accuracy_model_drift",
        "live_orders_unconfirmed",
        "accuracy_promotion_provenance",
    }
    assert all(
        health_severity_for_code(code) is HealthSeverity.BLOCKING
        for code in LEGACY_KILL_SWITCH_ISSUE_CODES
    )
    assert health_severity_for_code("accuracy_evidence_integrity") is HealthSeverity.CRITICAL
    assert health_severity_for_code("forecast_stale") is HealthSeverity.WARNING
    assert health_severity_for_code("settlement_stale") is HealthSeverity.WARNING
    assert health_severity_for_code("resolution_exact_source_mismatch") is HealthSeverity.BLOCKING
    assert (
        health_severity_for_code("resolution_reconciliation_integrity")
        is HealthSeverity.BLOCKING
    )
    assert (
        health_severity_for_code("settlement_actual_cell_conflict")
        is HealthSeverity.BLOCKING
    )
    assert health_severity_for_code("resolution_clear_mismatch") is HealthSeverity.CRITICAL
    assert (
        health_severity_for_code("resolution_clear_mismatch_contained")
        is HealthSeverity.WARNING
    )
    assert health_severity_for_code("resolution_boundary_sensitive") is HealthSeverity.INFO
    assert health_severity_for_code("exact_truth_backlog") is HealthSeverity.INFO
    assert health_severity_for_code("reconciliation_stale") is HealthSeverity.WARNING
    assert health_severity_for_code("settlement_poll_stale") is HealthSeverity.WARNING
    assert (
        health_status_requires_kill_switch(health_severity_for_code("reconciliation_stale").value)
        is False
    )
    assert (
        health_status_requires_kill_switch(health_severity_for_code("settlement_poll_stale").value)
        is False
    )
    assert health_severity_for_code("settlement_manual_review") is HealthSeverity.CRITICAL
    assert health_severity_for_code("storage_capacity_exhausted") is HealthSeverity.BLOCKING
    assert health_severity_for_code("storage_capacity_missing") is HealthSeverity.BLOCKING
    assert health_severity_for_code("storage_capacity_stale") is HealthSeverity.BLOCKING
    assert health_severity_for_code("storage_capacity_free_disk") is HealthSeverity.BLOCKING
    assert (
        health_severity_for_code("probability_release_epoch_missing")
        is HealthSeverity.CRITICAL
    )
    assert (
        health_severity_for_code("probability_release_epoch_mismatch")
        is HealthSeverity.CRITICAL
    )
    assert (
        health_severity_for_code("evidence_lane_identity_mismatch")
        is HealthSeverity.CRITICAL
    )
    assert (
        health_status_requires_kill_switch(
            health_severity_for_code("evidence_lane_identity_mismatch").value
        )
        is False
    )
    assert (
        health_severity_for_code("prospective_validation_enrollment_gap")
        is HealthSeverity.BLOCKING
    )
    assert (
        health_severity_for_code("prospective_evidence_collecting")
        is HealthSeverity.INFO
    )


def test_clear_mismatch_requires_current_valid_station_containment() -> None:
    now = datetime(2026, 7, 20, 14, 0, tzinfo=UTC)
    checked_at = now - timedelta(minutes=10)
    snapshot = StationReliabilitySnapshot(
        station_id=7,
        evaluated_at=now - timedelta(minutes=5),
        status=StationReliabilityStatus.BLOCKED,
        latest_conflict_at=checked_at,
        resolution_cooldown_until=now + timedelta(hours=48),
        reasons=["station_resolution_conflict"],
    )

    assert _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=checked_at,
        reliability_snapshot=snapshot,
        reliability_snapshot_is_valid=True,
        station_execution_disabled=False,
        now=now,
    )

    snapshot.status = StationReliabilityStatus.RELIABLE
    assert not _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=checked_at,
        reliability_snapshot=snapshot,
        reliability_snapshot_is_valid=True,
        station_execution_disabled=False,
        now=now,
    )

    snapshot.status = StationReliabilityStatus.BLOCKED
    snapshot.resolution_cooldown_until = now
    assert _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=checked_at,
        reliability_snapshot=snapshot,
        reliability_snapshot_is_valid=True,
        station_execution_disabled=False,
        now=now,
    )

    snapshot.resolution_cooldown_until = now + timedelta(hours=48)
    snapshot.evaluated_at = checked_at - timedelta(seconds=1)
    assert not _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=checked_at,
        reliability_snapshot=snapshot,
        reliability_snapshot_is_valid=True,
        station_execution_disabled=False,
        now=now,
    )

    snapshot.evaluated_at = now
    assert not _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=checked_at,
        reliability_snapshot=snapshot,
        reliability_snapshot_is_valid=False,
        station_execution_disabled=False,
        now=now,
    )
    assert _clear_mismatch_station_is_contained(
        station_id=7,
        latest_checked_at=now + timedelta(days=1),
        reliability_snapshot=None,
        reliability_snapshot_is_valid=False,
        station_execution_disabled=True,
        now=now,
    )


def test_execution_evidence_invariants_are_critical_without_kill_switch() -> None:
    summary = ExecutionCostHealthSummary(
        opened_at=datetime(2026, 7, 15, 6, 0, tzinfo=UTC),
        post_boundary_orders=9,
        orders_without_intent=1,
        terminal_without_obligation=2,
        overdue_obligations=3,
        overdue_marks=4,
        invalid_eligible_source_legs=5,
        reconciliation_mismatches=6,
        duplicate_identities=7,
        live_unverified_legs=2,
        live_unverified_reasons=(("live_schema_unverified", 2),),
        coverage=(
            ExecutionCostModeCoverage(
                mode=ExecutionMode.PAPER,
                attempts=10,
                sealed=8,
                eligible=7,
                filled_seals=6,
                mark_5m_complete=5,
                mark_30m_complete=4,
            ),
        ),
    )

    issues = execution_cost_health_issues(summary)
    by_code = {issue["code"]: issue for issue in issues}
    invariant_codes = {
        "execution_intent_missing",
        "execution_obligation_missing",
        "execution_obligation_overdue",
        "execution_mark_overdue",
        "execution_source_evidence_invalid",
        "execution_reconciliation_mismatch",
        "execution_identity_duplicate",
    }
    assert invariant_codes.issubset(by_code)
    assert all(by_code[code]["severity"] == "critical" for code in invariant_codes)
    assert all(
        health_severity_for_code(code) is HealthSeverity.CRITICAL for code in invariant_codes
    )
    assert all(
        not health_status_requires_kill_switch(by_code[code]["severity"])
        for code in invariant_codes
    )
    assert by_code["execution_live_evidence_unverified"]["severity"] == "warning"
    assert by_code["execution_eligible_seal_rate"]["severity"] == "warning"
    assert by_code["execution_markout_5m_coverage"]["severity"] == "warning"
    assert by_code["execution_markout_30m_coverage"]["severity"] == "warning"


def test_execution_evidence_complete_coverage_is_informative() -> None:
    summary = ExecutionCostHealthSummary(
        opened_at=datetime(2026, 7, 15, 6, 0, tzinfo=UTC),
        post_boundary_orders=4,
        orders_without_intent=0,
        terminal_without_obligation=0,
        overdue_obligations=0,
        overdue_marks=0,
        invalid_eligible_source_legs=0,
        reconciliation_mismatches=0,
        duplicate_identities=0,
        live_unverified_legs=0,
        live_unverified_reasons=(),
        coverage=(
            ExecutionCostModeCoverage(
                mode=ExecutionMode.PAPER,
                attempts=4,
                sealed=4,
                eligible=4,
                filled_seals=4,
                mark_5m_complete=4,
                mark_30m_complete=4,
            ),
            ExecutionCostModeCoverage(
                mode=ExecutionMode.LIVE,
                attempts=0,
                sealed=0,
                eligible=0,
                filled_seals=0,
                mark_5m_complete=0,
                mark_30m_complete=0,
            ),
        ),
    )

    issues = execution_cost_health_issues(summary)
    assert [issue["code"] for issue in issues] == [
        "execution_eligible_seal_rate",
        "execution_markout_5m_coverage",
        "execution_markout_30m_coverage",
    ]
    assert all(issue["severity"] == "info" for issue in issues)


def test_empirical_ops_issues_separate_exact_truth_from_metar_and_poll_freshness() -> None:
    checked_at = datetime(2026, 7, 14, 6, 0, tzinfo=UTC)
    issues = empirical_ops_health_issues(
        ResolutionReconciliationSummary(
            total=10,
            metar_available=10,
            metar_consistent=10,
            exact_available=3,
            exact_source_hash_available=2,
            exact_coverage=0.3,
            latest_checked_at=checked_at,
            oldest_checked_at=checked_at,
            stale_records=4,
            fresh=False,
        ),
        SettlementCasesSummary(
            total=5,
            open_total=2,
            oldest_open_at=checked_at,
            latest_checked_at=checked_at,
            stale_open_cases=1,
            fresh=False,
        ),
    )

    assert [issue["code"] for issue in issues] == [
        "exact_truth_backlog",
        "reconciliation_stale",
        "settlement_poll_stale",
    ]
    assert "2/10" in issues[0]["detail"]
    assert "raw exact values exist for 3/10" in issues[0]["detail"]
    assert "METAR" in issues[0]["detail"]
    assert "not official-source matches" in issues[0]["detail"]
    assert "4/10" in issues[1]["detail"]
    assert "1/2" in issues[2]["detail"]


def test_empirical_ops_issues_are_empty_when_exact_and_polls_are_current() -> None:
    checked_at = datetime(2026, 7, 14, 6, 0, tzinfo=UTC)
    assert (
        empirical_ops_health_issues(
            ResolutionReconciliationSummary(
                total=2,
                metar_available=2,
                metar_consistent=2,
                exact_available=2,
                exact_source_hash_available=2,
                exact_coverage=1.0,
                latest_checked_at=checked_at,
                oldest_checked_at=checked_at,
                stale_records=0,
                fresh=True,
            ),
            SettlementCasesSummary(
                total=1,
                open_total=0,
                oldest_open_at=None,
                latest_checked_at=checked_at,
                stale_open_cases=0,
                fresh=True,
            ),
        )
        == []
    )


def _delivery_summary(**overrides: object) -> NotificationDeliverySummary:
    values: dict[str, object] = {
        "channel": "telegram",
        "state": "healthy",
        "total_count": 4,
        "unacknowledged_count": 4,
        "oldest_unacknowledged_at": datetime(2026, 7, 14, tzinfo=UTC),
        "activated_at": datetime(2026, 7, 14, 12, 0, tzinfo=UTC),
        "heartbeat_at": datetime(2026, 7, 14, 12, 1, tzinfo=UTC),
        "heartbeat_stale": False,
        "last_success_at": None,
        "last_failure_at": None,
        "last_error": None,
        "pending_after_activation": 0,
        "oldest_pending_after_activation_at": None,
        "backlog_overdue": False,
        "dead_letter_count": 0,
    }
    values.update(overrides)
    return NotificationDeliverySummary(**values)  # type: ignore[arg-type]


def test_optional_delivery_absence_and_fresh_pending_are_not_health_warnings() -> None:
    assert (
        notification_delivery_health_issues(
            _delivery_summary(
                state="not_activated",
                activated_at=None,
                heartbeat_at=None,
                heartbeat_stale=False,
            )
        )
        == []
    )
    assert (
        notification_delivery_health_issues(
            _delivery_summary(
                pending_after_activation=1,
                oldest_pending_after_activation_at=datetime(2026, 7, 14, 12, 1, tzinfo=UTC),
            )
        )
        == []
    )


def test_delivery_health_reports_only_stale_or_failed_activated_state() -> None:
    stale = notification_delivery_health_issues(
        _delivery_summary(state="stale", heartbeat_at=None, heartbeat_stale=True)
    )
    assert [issue["code"] for issue in stale] == ["notification_delivery_stale"]

    degraded = notification_delivery_health_issues(
        _delivery_summary(
            state="degraded",
            pending_after_activation=2,
            oldest_pending_after_activation_at=datetime(2026, 7, 14, 11, 50, tzinfo=UTC),
            backlog_overdue=True,
            dead_letter_count=1,
        )
    )
    assert [issue["code"] for issue in degraded] == [
        "notification_delivery_backlog",
        "notification_delivery_dead_letter",
    ]
    assert all(
        not health_status_requires_kill_switch(health_severity_for_code(issue["code"]).value)
        for issue in stale + degraded
    )


def _promotion_manifest() -> EffectivePromotionManifest:
    return build_effective_promotion_manifest(
        [
            EffectivePromotion(
                key=EffectivePromotionKey(
                    expert="trajectory_ar1",
                    legacy_expert_version="hourly-ar1-v1",
                    expert_version="hourly-ar1-v1-sealed-v2",
                    experiment_fingerprint="f" * 64,
                    pipeline_cohort="accuracy-v4+test",
                    station_id=7,
                    horizon_bucket="6-24h",
                    kind="high",
                ),
                activation_id="activation-trajectory",
                definition_hash="d" * 64,
                decision_hash="e" * 64,
                activated_at=datetime(2026, 7, 14, tzinfo=UTC),
            )
        ]
    )


def test_promotion_provenance_requires_exact_scope_and_manifest() -> None:
    manifest = _promotion_manifest()
    components = {
        "pipeline": {"activation_manifest_hash": manifest.manifest_hash},
        "trajectory_ar1": {
            "applied": True,
            "promotion_active": True,
            "activation_id": "activation-trajectory",
            "promotion_definition_hash": "d" * 64,
            "promotion_decision_hash": "e" * 64,
        },
    }

    assert (
        invalid_promotion_provenance([(41, 12.0, components, 7, WeatherKind.HIGH)], manifest) == []
    )
    defects = invalid_promotion_provenance([(41, 12.0, components, 8, WeatherKind.HIGH)], manifest)
    assert defects == ["market=41:trajectory_ar1:scope"]


def test_rolling_application_without_activation_fails_closed() -> None:
    manifest = build_effective_promotion_manifest([])
    defects = invalid_promotion_provenance(
        [
            (
                42,
                12.0,
                {
                    "pipeline": {"activation_manifest_hash": manifest.manifest_hash},
                    "trajectory_ar1": {
                        "applied": True,
                        "rolling_report_eligible": True,
                    },
                },
                7,
                WeatherKind.HIGH,
            )
        ],
        manifest,
    )

    assert defects == ["market=42:trajectory_ar1:activation"]
