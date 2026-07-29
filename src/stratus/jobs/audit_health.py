from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, not_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    AccuracyPrediction,
    BankrollLog,
    ClobBookBatch,
    ClobMarketSnapshot,
    DailyActual,
    ExecutionMode,
    Forecast,
    GammaOutcomeEvent,
    HealthAudit,
    Market,
    MarketStatus,
    ModelDriftEvent,
    ModelProb,
    Observation,
    Order,
    Position,
    PositionStatus,
    ResolutionContract,
    ResolutionReconciliation,
    ResolutionReconciliationClassification,
    SettlementActual,
    SettlementCase,
    SettlementCaseClassification,
    Signal,
    SignalStatus,
    Station,
    StationReliabilitySnapshot,
    StationReliabilityStatus,
    StationStage,
    StorageCapacitySample,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import accuracy_prediction_record_is_valid
from stratus.repositories.accuracy_promotions import (
    EffectivePromotionManifest,
    load_effective_accuracy_promotion_manifest,
)
from stratus.repositories.clob_snapshots import clob_batch_record_is_valid
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible
from stratus.repositories.empirical_ops import (
    ResolutionReconciliationSummary,
    SettlementCasesSummary,
    resolution_reconciliation_summary,
    settlement_cases_summary,
)
from stratus.repositories.evidence_continuity import (
    probability_pipeline_release_cohort,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.evidence_continuity_reporting import (
    evidence_continuity_summary,
)
from stratus.repositories.execution import LIVE_STATUS_UNCONFIRMED
from stratus.repositories.execution_cost_health import (
    ExecutionCostHealthSummary,
    execution_cost_health_summary,
)
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    gamma_outcome_map,
)
from stratus.repositories.job_runs import collector_uptime
from stratus.repositories.official_resolution import (
    OfficialResolutionEvidenceSummary,
    official_resolution_evidence_summary,
)
from stratus.repositories.resolution_reconciliation import (
    resolution_reconciliation_record_is_valid,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import set_kill_switch
from stratus.repositories.station_reliability import (
    latest_station_reliability_by_station,
    station_reliability_record_is_valid,
)
from stratus.repositories.storage_capacity import (
    acquire_capacity_write_lock,
    sample_capacity_point,
)
from stratus.repositories.telegram import (
    NotificationDeliverySummary,
    enqueue_notification,
    notification_delivery_summary,
)
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.emos import horizon_bucket
from stratus.services.forecast_provenance import model_input_provenance_is_complete
from stratus.services.health import (
    HealthSeverity,
    aggregate_health_status,
    health_status_requires_kill_switch,
    normalize_health_issue,
)
from stratus.services.pipeline_identity import (
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
)
from stratus.services.probability import ks_uniform_pvalue, pit_value
from stratus.services.reconciliation import clear_mismatches, find_resolution_mismatches
from stratus.services.station_reliability import (
    station_reliability_policy_hash,
    station_signal_gate_reasons,
)
from stratus.services.storage_capacity import assess_capacity_collection_gate
from stratus.services.uptime import CollectorSLOState, MaintenanceWindow

logger = logging.getLogger(__name__)
ACCURACY_HASH_ATTESTATION_WINDOW = timedelta(days=180)
ACCURACY_HASH_ATTESTATION_OVERLAP = timedelta(minutes=20)
ACCURACY_HASH_ATTESTATION_BATCH_SIZE = 500
SHADOW_DRIFT_METRICS = frozenset({"residual_bias_shift"})
PROMOTED_COMPONENTS = {
    "hierarchical_emos": ("hierarchical_emos", "partial-pooling-v1"),
    "regime_emos": ("regime_emos", "version-break-v1"),
    "conformal_gaussian": ("conformal_gaussian", "split-conformal-v1"),
    "trajectory_ar1": ("trajectory_ar1", "hourly-ar1-v1"),
    "trajectory_nowcast": ("trajectory_nowcast", "remaining-day-bias-v1"),
    "qrf": ("qrf", "qrf-v1"),
    "analog": ("analog", "analog-distance-v1"),
    "mixture": ("mixture", "residual-mixture-v1"),
    "stacking": ("regularized_stack", "regularized-brier-v1"),
    "settlement_calibration": ("settlement_beta", "partition-beta-v1"),
}

# Every condition that engaged the kill switch before the four-level split is
# explicitly retained at the new strongest severity. Dynamic hard exposure
# breaches are added to the per-run override set below.
LEGACY_KILL_SWITCH_ISSUE_CODES = frozenset(
    {
        "weekly_loss_limit",
        "resolution_contract_missing",
        "gamma_outcome_evidence_hash_mismatch",
        "clob_evidence_hash_mismatch",
        "gamma_outcome_projection_mismatch",
        "accuracy_model_drift",
        "live_orders_unconfirmed",
        "accuracy_promotion_provenance",
    }
)
NEW_BLOCKING_ISSUE_CODES = frozenset(
    {
        "resolution_exact_source_mismatch",
        "resolution_reconciliation_integrity",
        "settlement_actual_cell_conflict",
        "official_resolution_evidence_integrity",
        "prospective_validation_enrollment_gap",
        "storage_capacity_free_disk",
        "storage_capacity_exhausted",
        "storage_capacity_missing",
        "storage_capacity_stale",
    }
)

_INFO_ISSUE_CODES = frozenset(
    {
        "collector_uptime_startup_grace",
        "collector_uptime_collecting",
        "collector_uptime_maintenance",
        "execution_eligible_seal_rate",
        "execution_markout_30m_coverage",
        "execution_markout_5m_coverage",
        "gamma_outcome_evidence_missing",
        "exact_truth_backlog",
        "official_resolution_authorization_blocked",
        "official_resolution_evidence_backlog",
        "prospective_evidence_collecting",
        "resolution_boundary_sensitive",
        "resolution_truth_unavailable",
        "settlement_venue_pending",
        "storage_capacity_measurement",
    }
)
_CRITICAL_ISSUE_CODES = frozenset(
    {
        "accuracy_evidence_integrity",
        "accuracy_evidence_hash_mismatch",
        "event_probability_sum",
        "execution_identity_duplicate",
        "execution_intent_missing",
        "execution_mark_overdue",
        "execution_obligation_missing",
        "execution_obligation_overdue",
        "execution_reconciliation_mismatch",
        "execution_source_evidence_invalid",
        "evidence_collection_overdue",
        "evidence_collection_stalled",
        "evidence_continuity_integrity_error",
        "evidence_lane_frozen",
        "evidence_lane_identity_mismatch",
        "evidence_release_identity_mismatch",
        "probability_release_epoch_missing",
        "probability_release_epoch_mismatch",
        "probability_release_image_mismatch",
        "resolution_clear_mismatch",
        "station_reliability_evidence_invalid",
        "station_reliability_signal_provenance_invalid",
        "station_reliability_signal_provenance_missing",
        "settlement_ambiguous",
        "settlement_manual_review",
    }
)


def health_severity_for_code(code: str) -> HealthSeverity:
    if code in LEGACY_KILL_SWITCH_ISSUE_CODES or code in NEW_BLOCKING_ISSUE_CODES:
        return HealthSeverity.BLOCKING
    if code in _CRITICAL_ISSUE_CODES:
        return HealthSeverity.CRITICAL
    if code in _INFO_ISSUE_CODES:
        return HealthSeverity.INFO
    return HealthSeverity.WARNING


def _clear_mismatch_station_is_contained(
    *,
    station_id: int | None,
    latest_checked_at: datetime,
    reliability_snapshot: StationReliabilitySnapshot | None,
    reliability_snapshot_is_valid: bool,
    station_execution_disabled: bool,
    now: datetime,
) -> bool:
    """Return whether policy has absorbed a durable proxy-truth conflict.

    Reconciliation and station assessment run under one scheduler lock, but the
    reconciliation ``checked_at`` is a polling heartbeat while the reliability
    snapshot deliberately preserves the first conflict anchor.  Containment
    therefore requires a valid snapshot evaluated after the latest poll, not a
    newer conflict anchor.  A newly active cooldown must still be blocking; once
    that cooldown has been served, the permanently quarantined training label
    may remain historical without closing readiness forever.  A station that
    cannot execute is contained without a fresh lifecycle snapshot.
    """

    if station_id is None:
        return False
    if station_execution_disabled:
        return True
    if (
        reliability_snapshot is None
        or not reliability_snapshot_is_valid
        or reliability_snapshot.station_id != station_id
        or reliability_snapshot.evaluated_at < latest_checked_at
    ):
        return False
    cooldown_until = reliability_snapshot.resolution_cooldown_until
    if cooldown_until is None or cooldown_until <= now:
        return True
    return bool(
        reliability_snapshot.status == StationReliabilityStatus.BLOCKED
        and reliability_snapshot.latest_conflict_at is not None
        and "station_resolution_conflict" in reliability_snapshot.reasons
    )


def empirical_ops_health_issues(
    reconciliation: ResolutionReconciliationSummary,
    settlement: SettlementCasesSummary,
) -> list[dict[str, str]]:
    """Describe evidence coverage and polling lag without changing trade gates."""

    issues: list[dict[str, str]] = []
    if reconciliation.total > reconciliation.exact_source_hash_available:
        backlog = reconciliation.total - reconciliation.exact_source_hash_available
        issues.append(
            {
                "code": "exact_truth_backlog",
                "detail": (
                    "attested exact-source evidence is available for "
                    f"{reconciliation.exact_source_hash_available}/"
                    f"{reconciliation.total} reconciliation row(s); raw exact values "
                    f"exist for {reconciliation.exact_available}/{reconciliation.total}; "
                    f"{backlog} row(s) may have METAR or venue-consistency evidence "
                    "but are not official-source matches"
                ),
            }
        )
    if reconciliation.stale_records:
        oldest = (
            reconciliation.oldest_checked_at.isoformat()
            if reconciliation.oldest_checked_at is not None
            else "unknown"
        )
        issues.append(
            {
                "code": "reconciliation_stale",
                "detail": (
                    f"{reconciliation.stale_records}/{reconciliation.total} reconciliation "
                    f"row(s) are older than {reconciliation.stale_after_seconds}s; "
                    f"oldest checked_at={oldest}"
                ),
            }
        )
    if settlement.stale_open_cases:
        oldest_open = (
            settlement.oldest_open_at.isoformat()
            if settlement.oldest_open_at is not None
            else "unknown"
        )
        issues.append(
            {
                "code": "settlement_poll_stale",
                "detail": (
                    f"{settlement.stale_open_cases}/{settlement.open_total} non-terminal "
                    f"settlement case(s) were not checked within "
                    f"{settlement.stale_after_seconds}s; oldest open at={oldest_open}"
                ),
            }
        )
    return issues


def official_resolution_health_issues(
    summary: OfficialResolutionEvidenceSummary,
) -> list[dict[str, str]]:
    """Classify licensed exact-source readiness without inventing truth.

    Missing external authorization is expected and non-blocking. A broken
    immutable chain is different: it means the stored source response, exact
    extraction, and settlement projection no longer agree and must block.
    """

    issues: list[dict[str, str]] = []
    if not (
        summary.source_responses == summary.exact_actuals == summary.projected_settlement_actuals
    ):
        issues.append(
            {
                "code": "official_resolution_evidence_integrity",
                "detail": (
                    "official-resolution evidence chain count mismatch: "
                    f"responses={summary.source_responses}, "
                    f"exact={summary.exact_actuals}, "
                    f"settlement={summary.projected_settlement_actuals}"
                ),
            }
        )
    if summary.active_contracts and summary.licensed_contracts == 0:
        issues.append(
            {
                "code": "official_resolution_authorization_blocked",
                "detail": (
                    f"0/{summary.active_contracts} active resolution contract(s) "
                    "have a current contract-scoped permission and equivalence "
                    "attestation; exact truth remains fail-closed"
                ),
            }
        )
    elif summary.licensed_contracts > summary.exact_actuals:
        issues.append(
            {
                "code": "official_resolution_evidence_backlog",
                "detail": (
                    f"{summary.exact_actuals}/{summary.licensed_contracts} currently "
                    "licensed contract(s) have sealed exact-source evidence"
                ),
            }
        )
    if summary.expired_contracts:
        issues.append(
            {
                "code": "official_resolution_license_expired",
                "detail": (
                    f"{summary.expired_contracts} contract-scoped official-source "
                    "attestation(s) have no current replacement"
                ),
            }
        )
    return issues


def notification_delivery_health_issues(
    summary: NotificationDeliverySummary,
) -> list[dict[str, str]]:
    """Surface activated-channel failures without affecting safety gates.

    An absent runtime is the expected optional ``not_activated`` state.  Local
    inbox rows remain queryable, but no warning is emitted until an operator
    explicitly starts the external delivery channel.
    """

    if summary.activated_at is None:
        return []
    issues: list[dict[str, str]] = []
    if summary.heartbeat_stale:
        heartbeat = (
            summary.heartbeat_at.isoformat() if summary.heartbeat_at is not None else "never"
        )
        issues.append(
            {
                "code": "notification_delivery_stale",
                "detail": (
                    f"{summary.channel} delivery consumer heartbeat is stale; "
                    f"last heartbeat={heartbeat}"
                ),
            }
        )
    if summary.backlog_overdue:
        oldest = (
            summary.oldest_pending_after_activation_at.isoformat()
            if summary.oldest_pending_after_activation_at is not None
            else "unknown"
        )
        issues.append(
            {
                "code": "notification_delivery_backlog",
                "detail": (
                    f"{summary.pending_after_activation} post-activation "
                    f"{summary.channel} alert(s) await delivery; oldest={oldest}"
                ),
            }
        )
    if summary.dead_letter_count:
        issues.append(
            {
                "code": "notification_delivery_dead_letter",
                "detail": (
                    f"{summary.dead_letter_count} post-activation {summary.channel} "
                    "alert(s) exhausted delivery retries"
                ),
            }
        )
    if (
        summary.state == "degraded"
        and not summary.pending_after_activation
        and not summary.dead_letter_count
        and summary.last_error
    ):
        issues.append(
            {
                "code": "notification_delivery_failure",
                "detail": f"{summary.channel} last delivery failure: {summary.last_error}",
            }
        )
    return issues


def execution_cost_health_issues(
    summary: ExecutionCostHealthSummary,
) -> list[dict[str, str]]:
    """Render 0038 integrity failures separately from ordinary coverage gaps."""

    issues: list[dict[str, str]] = []
    invariants = (
        (
            "execution_intent_missing",
            summary.orders_without_intent,
            (
                "post-boundary executable order(s) have no immutable intent; "
                f"boundary={summary.opened_at.isoformat()}, "
                f"orders={summary.post_boundary_orders}"
            ),
        ),
        (
            "execution_obligation_missing",
            summary.terminal_without_obligation,
            "terminal intent/order pair(s) have no cost obligation",
        ),
        (
            "execution_obligation_overdue",
            summary.overdue_obligations,
            "unsealed obligation(s) are more than two scheduler minutes overdue",
        ),
        (
            "execution_mark_overdue",
            summary.overdue_marks,
            "frozen leg/horizon mark(s) remain absent after grace and scheduler slack",
        ),
        (
            "execution_source_evidence_invalid",
            summary.invalid_eligible_source_legs,
            (
                "eligible fill leg(s) failed independent identity, projection, "
                "record-hash, or raw-CLOB attestation"
            ),
        ),
        (
            "execution_reconciliation_mismatch",
            summary.reconciliation_mismatches,
            "fill/order/position/obligation/seal aggregate reconciliation mismatch(es)",
        ),
        (
            "execution_identity_duplicate",
            summary.duplicate_identities,
            "duplicate immutable execution identity row(s)",
        ),
    )
    for code, count, detail in invariants:
        if count:
            issues.append(
                {
                    "code": code,
                    "detail": f"{count} {detail}",
                    "severity": HealthSeverity.CRITICAL.value,
                }
            )
    if summary.live_unverified_legs:
        reason_detail = ", ".join(
            f"{reason}={count}" for reason, count in summary.live_unverified_reasons
        )
        issues.append(
            {
                "code": "execution_live_evidence_unverified",
                "detail": (
                    f"{summary.live_unverified_legs} live SDK fill leg(s) cannot yet prove "
                    "schema/fee/role/venue-time evidence"
                    + (f"; reasons: {reason_detail}" if reason_detail else "")
                ),
                "severity": HealthSeverity.WARNING.value,
            }
        )
    active = tuple(item for item in summary.coverage if item.attempts)
    if not active:
        return issues
    eligible_parts = [
        (
            f"{item.mode.value}={item.eligible}/{item.sealed} eligible seals, "
            f"{item.attempts - item.sealed} unsealed attempt(s)"
        )
        for item in active
    ]
    eligible_warning = any(item.sealed and item.eligible < item.sealed for item in active)
    issues.append(
        {
            "code": "execution_eligible_seal_rate",
            "detail": "; ".join(eligible_parts),
            "severity": (
                HealthSeverity.WARNING.value if eligible_warning else HealthSeverity.INFO.value
            ),
        }
    )
    for horizon, attribute, code in (
        ("5m", "mark_5m_complete", "execution_markout_5m_coverage"),
        ("30m", "mark_30m_complete", "execution_markout_30m_coverage"),
    ):
        detail_parts = [
            (f"{item.mode.value}={getattr(item, attribute)}/{item.filled_seals} complete")
            for item in active
        ]
        incomplete = any(getattr(item, attribute) < item.filled_seals for item in active)
        issues.append(
            {
                "code": code,
                "detail": f"{horizon} post-fill coverage: " + "; ".join(detail_parts),
                "severity": (
                    HealthSeverity.WARNING.value if incomplete else HealthSeverity.INFO.value
                ),
            }
        )
    return issues


PromotionProbabilityRow = tuple[int, float, dict[str, object], int | None, WeatherKind]


def invalid_promotion_provenance(
    rows: Iterable[PromotionProbabilityRow],
    manifest: EffectivePromotionManifest,
) -> list[str]:
    """Return fail-closed provenance defects in latest production probabilities."""

    by_activation_id = {promotion.activation_id: promotion for promotion in manifest.promotions}
    defects: list[str] = []
    for market_id, horizon_h, components, station_id, kind in rows:
        pipeline = components.get("pipeline")
        if not isinstance(pipeline, dict) or (
            pipeline.get("activation_manifest_hash") != manifest.manifest_hash
        ):
            defects.append(f"market={market_id}:manifest")
        target_bucket = horizon_bucket(math.ceil(max(0.0, horizon_h)))
        for component_name, (expert, legacy_version) in PROMOTED_COMPONENTS.items():
            detail = components.get(component_name)
            if not isinstance(detail, dict) or detail.get("applied") is not True:
                continue
            activation_id = detail.get("activation_id")
            promotion = (
                by_activation_id.get(activation_id) if isinstance(activation_id, str) else None
            )
            if promotion is None:
                defects.append(f"market={market_id}:{component_name}:activation")
                continue
            key = promotion.key
            exact_scope = (
                station_id is not None
                and key.expert == expert
                and key.legacy_expert_version == legacy_version
                and key.station_id == station_id
                and key.horizon_bucket == target_bucket
                and key.kind == kind.value
                and detail.get("promotion_active") is True
                and detail.get("promotion_definition_hash") == promotion.definition_hash
                and detail.get("promotion_decision_hash") == promotion.decision_hash
            )
            if not exact_scope:
                defects.append(f"market={market_id}:{component_name}:scope")
    return sorted(set(defects))


def invalid_forecast_input_provenance(
    rows: Iterable[tuple[int, datetime, object]],
    *,
    boundary: datetime,
) -> list[int]:
    """Return post-boundary probabilities missing immutable source evidence."""

    return sorted(
        market_id
        for market_id, probability_ts, components in rows
        if probability_ts >= boundary and not model_input_provenance_is_complete(components)
    )


def invalid_event_probability_sums(
    probabilities: dict[str, list[float]], tolerance: float = 0.005
) -> list[str]:
    return sorted(
        event_id
        for event_id, values in probabilities.items()
        if values and abs(sum(values) - 1.0) > tolerance
    )


def is_trade_blocking_drift(metric: str) -> bool:
    return metric not in SHADOW_DRIFT_METRICS


def is_hard_exposure_breach(value: float, limit: float, multiplier: float) -> bool:
    return value > limit * multiplier + 0.01


def exposure_breach_severity(
    value: float,
    limit: float,
    multiplier: float,
) -> HealthSeverity:
    return (
        HealthSeverity.BLOCKING
        if is_hard_exposure_breach(value, limit, multiplier)
        else HealthSeverity.WARNING
    )


def accuracy_hash_attestation_start(
    *,
    now: datetime,
    latest_audit_ts: datetime | None,
    latest_audit_issues: Iterable[object] = (),
) -> datetime:
    """Return the incremental sealed-evidence watermark.

    Accuracy predictions are database-append-only, so rows attested before a
    successfully persisted health audit do not need to be loaded again.  The
    overlap covers rows committed around the audit transaction boundary.  A
    previously detected mismatch forces a bounded full-window rescan: immutable
    corruption must remain visible until it ages out of the health window.
    """

    lower_bound = now - ACCURACY_HASH_ATTESTATION_WINDOW
    mismatch_is_active = any(
        isinstance(issue, dict) and issue.get("code") == "accuracy_evidence_hash_mismatch"
        for issue in latest_audit_issues
    )
    if latest_audit_ts is None or mismatch_is_active:
        return lower_bound
    watermark = min(latest_audit_ts, now) - ACCURACY_HASH_ATTESTATION_OVERLAP
    return max(lower_bound, watermark)


async def attest_accuracy_prediction_hashes(
    session: AsyncSession,
    *,
    now: datetime,
) -> tuple[int, int]:
    """Stream and attest new sealed accuracy evidence with bounded memory."""

    latest_audit = (
        await session.execute(
            select(HealthAudit.ts, HealthAudit.issues)
            .where(HealthAudit.ts <= now)
            .order_by(HealthAudit.ts.desc(), HealthAudit.id.desc())
            .limit(1)
        )
    ).one_or_none()
    latest_audit_ts = latest_audit.ts if latest_audit is not None else None
    latest_audit_issues = latest_audit.issues if latest_audit is not None else ()
    scan_start = accuracy_hash_attestation_start(
        now=now,
        latest_audit_ts=latest_audit_ts,
        latest_audit_issues=latest_audit_issues,
    )
    statement = (
        select(AccuracyPrediction)
        .where(
            AccuracyPrediction.evidence_eligible.is_(True),
            AccuracyPrediction.generated_at >= scan_start,
            AccuracyPrediction.generated_at <= now,
        )
        .order_by(AccuracyPrediction.generated_at, AccuracyPrediction.id)
        .execution_options(yield_per=ACCURACY_HASH_ATTESTATION_BATCH_SIZE)
    )
    predictions = await session.stream_scalars(statement)
    scanned = 0
    invalid = 0
    try:
        async for prediction in predictions:
            scanned += 1
            if not accuracy_prediction_record_is_valid(prediction):
                invalid += 1
            session.expunge(prediction)
    finally:
        await predictions.close()
    logger.info(
        "accuracy hash attestation complete: scanned=%d invalid=%d "
        "generated_at_start=%s previous_audit=%s",
        scanned,
        invalid,
        scan_start.isoformat(),
        latest_audit_ts.isoformat() if latest_audit_ts is not None else "none",
    )
    return scanned, invalid


async def audit_health(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    maintenance_windows: Iterable[MaintenanceWindow] = (),
) -> tuple[str, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    issues: list[dict[str, str]] = []
    severity_overrides: dict[str, HealthSeverity] = {}
    effective_maintenance_windows = tuple(maintenance_windows)
    if (
        not effective_maintenance_windows
        and settings.health_maintenance_starts_at is not None
        and settings.health_maintenance_ends_at is not None
        and settings.health_maintenance_reason is not None
    ):
        effective_maintenance_windows = (
            MaintenanceWindow(
                starts_at=settings.health_maintenance_starts_at,
                ends_at=settings.health_maintenance_ends_at,
                reason=settings.health_maintenance_reason,
            ),
        )
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            pipeline_fingerprint = probability_pipeline_fingerprint(settings)
            promotion_manifest = await load_effective_accuracy_promotion_manifest(
                session,
                pipeline_fingerprint=pipeline_fingerprint,
                current_pipeline_base_cohort=probability_pipeline_base_cohort(
                    settings,
                    fingerprint=pipeline_fingerprint,
                ),
            )
            release_candidate_cohort = probability_pipeline_release_cohort(
                settings.probability_pipeline_version,
                pipeline_fingerprint,
                promotion_manifest.manifest_hash,
            )
            release_gate = await probability_release_gate(
                session,
                settings,
                authoritative=False,
            )
            if release_gate.candidate_cohort:
                release_candidate_cohort = release_gate.candidate_cohort
            continuity = await evidence_continuity_summary(
                session,
                candidate_cohort=release_candidate_cohort,
                candidate_image_digest=settings.release_image_digest,
                now=now,
            )
            if release_gate.reason == "evidence_continuity_unactivated":
                issues.append(
                    {
                        "code": "probability_release_epoch_missing",
                        "detail": (
                            "no reviewed probability release epoch is active; "
                            f"candidate={release_candidate_cohort}; new execution intents "
                            "are database-blocked"
                        ),
                    }
                )
            elif release_gate.reason == "evidence_release_identity_mismatch":
                issues.append(
                    {
                        "code": "evidence_release_identity_mismatch",
                        "detail": (
                            f"active={continuity.active_cohort}; "
                            f"candidate={release_candidate_cohort}; "
                            f"active_manifest={release_gate.active_release_manifest_hash}; "
                            f"candidate_manifest="
                            f"{release_gate.candidate_release_manifest_hash}; exact reviewed "
                            "release activation is required before new execution intents"
                        ),
                    }
                )
            elif release_gate.reason == "evidence_lane_identity_mismatch":
                issues.append(
                    {
                        "code": "evidence_lane_identity_mismatch",
                        "detail": (
                            "running lane manifests differ from the reviewed epoch: "
                            + ",".join(release_gate.lane_identity_mismatches)
                        ),
                    }
                )
            elif release_gate.reason == "evidence_lane_frozen":
                issues.append(
                    {
                        "code": "evidence_lane_frozen",
                        "detail": (
                            "required continuity lane is frozen: "
                            + ",".join(release_gate.frozen_lanes)
                        ),
                    }
                )
            elif release_gate.reason == "evidence_continuity_integrity_error":
                issues.append(
                    {
                        "code": "evidence_continuity_integrity_error",
                        "detail": (
                            "release manifest or active epoch/lane evidence is incomplete; "
                            "new execution remains fail-closed"
                        ),
                    }
                )
            else:
                assert continuity.active_epoch is not None
                current_lane = next(
                    lane for lane in continuity.lanes if lane.cohort == release_candidate_cohort
                )
                issues.append(
                    {
                        "code": "prospective_evidence_collecting",
                        "detail": (
                            f"epoch={continuity.active_epoch.id}; "
                            f"age_days={continuity.velocity.complete_days}; "
                            f"intents={current_lane.execution_intents}; "
                            f"fills={current_lane.filled_orders}; "
                            f"enrollments={current_lane.eligible_enrollments}; "
                            f"outcomes={current_lane.outcomes}; "
                            f"blocks={current_lane.independent_blocks}; "
                            f"bottleneck={continuity.bottleneck.code}"
                        ),
                    }
                )
                if continuity.bottleneck.code == "validation_enrollment_gap":
                    issues.append(
                        {
                            "code": "prospective_validation_enrollment_gap",
                            "detail": continuity.bottleneck.detail,
                        }
                    )
                if continuity.liveness.value == "overdue":
                    issues.append(
                        {
                            "code": "evidence_collection_overdue",
                            "detail": (
                                f"overdue={continuity.collection_window.due_counts.overdue}; "
                                "naturally due continuity evidence crossed protocol grace"
                            ),
                        }
                    )
                if continuity.collection_window.missing_scheduler_jobs:
                    issues.append(
                        {
                            "code": "evidence_collection_stalled",
                            "detail": (
                                "required scheduler coverage is incomplete: "
                                + ",".join(continuity.collection_window.missing_scheduler_jobs)
                            ),
                        }
                    )
            if continuity.cohort_changes_30d > 1:
                issues.append(
                    {
                        "code": "probability_release_epoch_churn",
                        "detail": (
                            f"{continuity.cohort_changes_30d} reviewed cohort epoch(s) "
                            "opened in 30 days; prior evidence stays audit-only"
                        ),
                    }
                )
            uptime = await collector_uptime(
                session,
                now=now,
                maintenance_windows=effective_maintenance_windows,
            )
            failed = [metric.job_id for metric in uptime.metrics if not metric.passed]
            uptime_context = f"coverage={uptime.coverage}; failed={','.join(failed) or 'none'}"
            if uptime.state is CollectorSLOState.STARTUP_GRACE:
                issues.append(
                    {
                        "code": "collector_uptime_startup_grace",
                        "detail": (f"remaining={uptime.startup_grace_remaining}; {uptime_context}"),
                    }
                )
            elif uptime.state is CollectorSLOState.COLLECTING:
                issues.append(
                    {
                        "code": "collector_uptime_collecting",
                        "detail": uptime_context,
                    }
                )
            elif uptime.state is CollectorSLOState.PLANNED_MAINTENANCE:
                issues.append(
                    {
                        "code": "collector_uptime_maintenance",
                        "detail": (
                            f"excluded={uptime.excluded_maintenance}; reasons="
                            f"{','.join(uptime.maintenance_reasons)}; {uptime_context}"
                        ),
                    }
                )
            elif uptime.state is CollectorSLOState.FAILING:
                issues.append(
                    {
                        "code": "collector_uptime_slo",
                        "detail": (
                            f"{uptime_context}; planned_excluded={uptime.excluded_maintenance}"
                        ),
                    }
                )
            loss_limits, _risk_expired = await enforce_loss_limits(
                session,
                mode=ExecutionMode.PAPER,
                initial_bankroll_usd=settings.initial_bankroll_usd,
                timezone=settings.risk_timezone,
                now=now,
            )
            if loss_limits.daily_breached:
                issues.append(
                    {
                        "code": "daily_loss_limit",
                        "detail": f"P&L ${loss_limits.daily_pnl:+.2f}",
                    }
                )
            if loss_limits.weekly_breached:
                issues.append(
                    {
                        "code": "weekly_loss_limit",
                        "detail": f"P&L ${loss_limits.weekly_pnl:+.2f}",
                    }
                )
            latest_forecast = await session.scalar(select(func.max(Forecast.fetched_at)))
            if latest_forecast is None or now - latest_forecast > timedelta(hours=2):
                issues.append({"code": "forecast_stale", "detail": str(latest_forecast)})

            verified_event_ids = set(
                (
                    await session.execute(
                        select(Market.event_id).where(
                            Market.status == MarketStatus.ACTIVE,
                            Market.station_verified.is_(True),
                        )
                    )
                ).scalars()
            )
            contracted_event_ids = set(
                (
                    await session.execute(
                        select(ResolutionContract.event_id).where(
                            ResolutionContract.retired_at.is_(None)
                        )
                    )
                ).scalars()
            )
            missing_contracts = verified_event_ids - contracted_event_ids
            if missing_contracts:
                issues.append(
                    {
                        "code": "resolution_contract_missing",
                        "detail": f"{len(missing_contracts)} verified active event(s)",
                    }
                )

            missing_licensed_truth = await session.scalar(
                select(func.count(func.distinct(ResolutionContract.event_id)))
                .select_from(ResolutionContract)
                .join(Market, Market.event_id == ResolutionContract.event_id)
                .outerjoin(
                    SettlementActual,
                    SettlementActual.contract_id == ResolutionContract.id,
                )
                .where(
                    ResolutionContract.storage_permitted.is_(True),
                    Market.resolved_outcome.is_not(None),
                    Market.resolves_at >= now - timedelta(days=30),
                    SettlementActual.id.is_(None),
                )
            )
            if missing_licensed_truth:
                issues.append(
                    {
                        "code": "settlement_truth_missing",
                        "detail": f"{missing_licensed_truth} licensed resolved event(s)",
                    }
                )

            invalid_accuracy_evidence = await session.scalar(
                select(func.count())
                .select_from(AccuracyPrediction)
                .join(Market, AccuracyPrediction.market_id == Market.id)
                .outerjoin(
                    ModelProb,
                    ModelProb.id == AccuracyPrediction.baseline_model_prob_id,
                )
                .where(
                    AccuracyPrediction.evidence_eligible.is_(True),
                    or_(
                        AccuracyPrediction.point_in_time_valid.is_(False),
                        AccuracyPrediction.record_hash.is_(None),
                        AccuracyPrediction.input_hash.is_(None),
                        AccuracyPrediction.experiment_fingerprint.is_(None),
                        AccuracyPrediction.training_cutoff.is_(None),
                        func.length(AccuracyPrediction.record_hash) != 64,
                        func.length(AccuracyPrediction.input_hash) != 64,
                        func.length(AccuracyPrediction.experiment_fingerprint) != 64,
                        AccuracyPrediction.baseline_model_prob_id.is_(None),
                        AccuracyPrediction.ts > AccuracyPrediction.generated_at,
                        AccuracyPrediction.training_cutoff > AccuracyPrediction.generated_at,
                        AccuracyPrediction.generated_at >= Market.resolves_at,
                        ModelProb.id.is_(None),
                        ModelProb.market_id != AccuracyPrediction.market_id,
                        ModelProb.ts != AccuracyPrediction.ts,
                    ),
                )
            )
            if invalid_accuracy_evidence:
                issues.append(
                    {
                        "code": "accuracy_evidence_integrity",
                        "detail": (
                            f"{invalid_accuracy_evidence} sealed prediction(s) excluded "
                            "from prospective gates"
                        ),
                    }
                )

            (
                scanned_accuracy_hashes,
                invalid_accuracy_hashes,
            ) = await attest_accuracy_prediction_hashes(
                session,
                now=now,
            )
            if invalid_accuracy_hashes:
                issues.append(
                    {
                        "code": "accuracy_evidence_hash_mismatch",
                        "detail": (
                            f"{invalid_accuracy_hashes} sealed prediction record hash(es) "
                            "do not match stored content "
                            f"({scanned_accuracy_hashes} scanned)"
                        ),
                    }
                )

            gamma_outcome_events = (
                (
                    await session.execute(
                        # Projection integrity is timeless: linked markets are
                        # immutable for the lifetime of their ledger row, so a
                        # rolling evidence window would falsely mark older
                        # valid projections as missing.
                        select(GammaOutcomeEvent)
                    )
                )
                .scalars()
                .all()
            )
            valid_outcomes_by_event: dict[int, tuple[str, dict[int, bool]]] = {}
            invalid_outcome_hashes = 0
            for outcome_event in gamma_outcome_events:
                try:
                    event_outcomes = gamma_outcome_map(outcome_event)
                except GammaOutcomeIntegrityError:
                    invalid_outcome_hashes += 1
                    continue
                valid_outcomes_by_event[outcome_event.id] = (
                    outcome_event.event_id,
                    event_outcomes,
                )
            if invalid_outcome_hashes:
                issues.append(
                    {
                        "code": "gamma_outcome_evidence_hash_mismatch",
                        "detail": (
                            f"{invalid_outcome_hashes} immutable outcome event(s) "
                            "failed local hash attestation"
                        ),
                    }
                )

            # Re-attest a bounded recent window of the raw dual-token book
            # ledger.  The database makes older rows append-only; checking the
            # newest batches on every health cycle catches collector/parser or
            # deployment drift without repeatedly loading the full archive.
            recent_clob_batches = list(
                (
                    await session.scalars(
                        select(ClobBookBatch)
                        .where(ClobBookBatch.first_seen_at >= now - timedelta(hours=2))
                        .order_by(ClobBookBatch.first_seen_at.desc())
                        # One full 825-market refresh currently packs into
                        # about 38 requests; 64 keeps a complete latest cycle
                        # covered with margin as the station universe grows.
                        .limit(64)
                    )
                ).all()
            )
            snapshots_by_batch: dict[int, list[ClobMarketSnapshot]] = defaultdict(list)
            if recent_clob_batches:
                recent_clob_snapshots = (
                    await session.scalars(
                        select(ClobMarketSnapshot).where(
                            ClobMarketSnapshot.batch_id.in_(
                                [batch.id for batch in recent_clob_batches]
                            )
                        )
                    )
                ).all()
                for snapshot in recent_clob_snapshots:
                    snapshots_by_batch[snapshot.batch_id].append(snapshot)
            invalid_clob_batches = sum(
                not clob_batch_record_is_valid(
                    batch,
                    snapshots_by_batch.get(batch.id, []),
                )
                for batch in recent_clob_batches
            )
            if invalid_clob_batches:
                issues.append(
                    {
                        "code": "clob_evidence_hash_mismatch",
                        "detail": (
                            f"{invalid_clob_batches} recent immutable CLOB batch(es) "
                            "failed local raw/projection attestation"
                        ),
                    }
                )

            linked_market_rows = (
                await session.execute(
                    select(
                        Market.id,
                        Market.event_id,
                        Market.status,
                        Market.resolved_outcome,
                        Market.resolution_event_id,
                    ).where(Market.resolution_event_id.is_not(None))
                )
            ).all()
            projection_mismatches = 0
            for (
                market_id,
                event_id,
                status,
                resolved_outcome,
                resolution_event_id,
            ) in linked_market_rows:
                attested = valid_outcomes_by_event.get(resolution_event_id)
                if (
                    attested is None
                    or attested[0] != event_id
                    or market_id not in attested[1]
                    or status is not MarketStatus.RESOLVED
                    or resolved_outcome is None
                    or bool(resolved_outcome) != attested[1][market_id]
                ):
                    projection_mismatches += 1
            if projection_mismatches:
                issues.append(
                    {
                        "code": "gamma_outcome_projection_mismatch",
                        "detail": (
                            f"{projection_mismatches} market projection(s) disagree "
                            "with immutable Gamma evidence"
                        ),
                    }
                )

            unattested_resolved_events = await session.scalar(
                select(func.count(func.distinct(Market.event_id)))
                .select_from(Market)
                .where(
                    Market.status == MarketStatus.RESOLVED,
                    Market.resolved_outcome.is_not(None),
                    Market.resolution_event_id.is_(None),
                    Market.resolves_at >= now - timedelta(days=180),
                )
            )
            if unattested_resolved_events:
                issues.append(
                    {
                        "code": "gamma_outcome_evidence_missing",
                        "detail": (
                            f"{unattested_resolved_events} legacy resolved event(s) "
                            "remain diagnostic-only pending strict Gamma re-attestation"
                        ),
                    }
                )

            recent_critical_drift = await session.scalar(
                select(func.count())
                .select_from(ModelDriftEvent)
                .where(
                    ModelDriftEvent.severity == "critical",
                    ModelDriftEvent.detected_at >= now - timedelta(hours=36),
                    ModelDriftEvent.metric.not_in(SHADOW_DRIFT_METRICS),
                )
            )
            if recent_critical_drift:
                issues.append(
                    {
                        "code": "accuracy_model_drift",
                        "detail": f"{recent_critical_drift} critical drift event(s)",
                    }
                )
            recent_shadow_drift = await session.scalar(
                select(func.count())
                .select_from(ModelDriftEvent)
                .where(
                    ModelDriftEvent.severity == "critical",
                    ModelDriftEvent.detected_at >= now - timedelta(hours=36),
                    ModelDriftEvent.metric.in_(SHADOW_DRIFT_METRICS),
                )
            )
            if recent_shadow_drift:
                issues.append(
                    {
                        "code": "accuracy_shadow_drift",
                        "detail": (
                            f"{recent_shadow_drift} critical-magnitude shadow drift event(s); "
                            "prospective gates remain closed"
                        ),
                    }
                )

            station_rows = (
                await session.execute(
                    select(
                        Station.id,
                        Station.icao,
                        Station.tz,
                        func.max(Observation.obs_time),
                    )
                    .outerjoin(Observation, Observation.station_id == Station.id)
                    .where(
                        Station.active.is_(True),
                        Station.stage != StationStage.DISABLED,
                    )
                    .group_by(Station.id, Station.icao, Station.tz)
                )
            ).all()
            for _station_id, icao, _tz, latest_observation in station_rows:
                if latest_observation is None or now - latest_observation > timedelta(
                    minutes=settings.station_observation_stale_minutes
                ):
                    issues.append({"code": "observation_stale", "detail": f"{icao}"})

            reliability_by_station = await latest_station_reliability_by_station(
                session,
                [int(station_id) for station_id, _icao, _tz, _observed in station_rows],
            )
            reliability_policy_hash = station_reliability_policy_hash(settings)
            for station_id, icao, station_tz, _latest_observation in station_rows:
                reliability_snapshot = reliability_by_station.get(int(station_id))
                if reliability_snapshot is not None and not station_reliability_record_is_valid(
                    reliability_snapshot
                ):
                    issues.append(
                        {
                            "code": "station_reliability_evidence_invalid",
                            "detail": f"{icao}: record hash mismatch",
                        }
                    )
                    continue
                gate_reasons = station_signal_gate_reasons(
                    reliability_snapshot,
                    market_date=(
                        reliability_snapshot.observation_local_date
                        if reliability_snapshot is not None
                        else now.astimezone(ZoneInfo(station_tz)).date()
                    ),
                    now=now,
                    expected_policy_hash=reliability_policy_hash,
                    max_age=timedelta(
                        minutes=settings.station_reliability_snapshot_max_age_minutes
                    ),
                    observation_stale_after=timedelta(
                        minutes=settings.station_observation_stale_minutes
                    ),
                    station_timezone=station_tz,
                )
                for reason in gate_reasons:
                    issues.append({"code": reason, "detail": f"{icao}"})

            pending_station_rows = (
                await session.execute(
                    select(Signal, StationReliabilitySnapshot)
                    .outerjoin(
                        StationReliabilitySnapshot,
                        StationReliabilitySnapshot.id
                        == Signal.station_reliability_snapshot_id,
                    )
                    .where(Signal.status == SignalStatus.PENDING)
                )
            ).all()
            missing_station_provenance = sum(
                signal.station_reliability_snapshot_id is None or snapshot is None
                for signal, snapshot in pending_station_rows
            )
            invalid_station_provenance = sum(
                snapshot is not None
                and not station_reliability_record_is_valid(snapshot)
                for _signal, snapshot in pending_station_rows
            )
            if missing_station_provenance:
                issues.append(
                    {
                        "code": "station_reliability_signal_provenance_missing",
                        "detail": f"{missing_station_provenance} pending signal(s)",
                    }
                )
            if invalid_station_provenance:
                issues.append(
                    {
                        "code": "station_reliability_signal_provenance_invalid",
                        "detail": f"{invalid_station_provenance} pending signal(s)",
                    }
                )

            near_markets = (
                (
                    await session.execute(
                        select(Market).where(
                            Market.status == MarketStatus.ACTIVE,
                            Market.resolves_at <= now + timedelta(hours=6),
                        )
                    )
                )
                .scalars()
                .all()
            )
            stale_prices = [
                market.id
                for market in near_markets
                if market.last_price_fetch_at is None
                or now - market.last_price_fetch_at > timedelta(minutes=3)
            ]
            if stale_prices:
                issues.append(
                    {
                        "code": "near_price_stale",
                        "detail": f"{len(stale_prices)} market(s)",
                    }
                )

            latest_eligible_clob = (
                select(
                    ClobMarketSnapshot.market_id.label("market_id"),
                    func.max(ClobBookBatch.first_seen_at).label("last_seen_at"),
                )
                .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
                .where(ClobMarketSnapshot.eligible.is_(True))
                .group_by(ClobMarketSnapshot.market_id)
                .subquery()
            )
            near_dual_rows = (
                await session.execute(
                    select(Market.id, latest_eligible_clob.c.last_seen_at)
                    .join(Station, Market.station_id == Station.id)
                    .outerjoin(
                        latest_eligible_clob,
                        latest_eligible_clob.c.market_id == Market.id,
                    )
                    .where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                        Market.resolves_at <= now + timedelta(hours=6),
                        Station.active.is_(True),
                        Station.stage.in_((StationStage.PAPER, StationStage.APPROVED)),
                    )
                )
            ).all()
            unavailable_dual_books = [
                market_id
                for market_id, last_seen_at in near_dual_rows
                if last_seen_at is None or now - last_seen_at > timedelta(minutes=3)
            ]
            if unavailable_dual_books:
                issues.append(
                    {
                        "code": "near_dual_book_unavailable",
                        "detail": (
                            f"{len(unavailable_dual_books)} near-resolution market(s) "
                            "lack a fresh eligible independent YES/NO pair"
                        ),
                    }
                )

            # Settlement escalation: an event this far past resolution that is
            # still unresolved needs a human (Gamma dropped it, or the outcome
            # never reached an atomic binary winner vector). Real weather
            # events can finalize 18+ hours after their declared end, so 24h is
            # the operational threshold rather than the former noisy 12h.
            stale_settlements = await session.scalar(
                select(func.count(func.distinct(Market.event_id)))
                .select_from(Market)
                .where(
                    Market.status != MarketStatus.RESOLVED,
                    Market.resolves_at < now - timedelta(hours=24),
                )
            )
            if stale_settlements:
                issues.append(
                    {
                        "code": "settlement_stale",
                        "detail": f"{stale_settlements} event(s) >24h past resolution",
                    }
                )

            # A live order whose outcome could not be confirmed may be resting
            # on the book unaccounted for: the executor already engaged the
            # kill switch, but surface it as critical for the operator.
            unconfirmed_live = await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status == LIVE_STATUS_UNCONFIRMED,
                )
            )
            if unconfirmed_live:
                issues.append(
                    {
                        "code": "live_orders_unconfirmed",
                        "detail": f"{unconfirmed_live} live order(s) need manual reconciliation",
                    }
                )

            # Reconcile METAR-derived daily actuals with how markets actually
            # resolved: a clear disagreement means the observation pipeline
            # diverges from the official resolution source that Polymarket
            # uses — the model is being calibrated against the wrong truth.
            reconciliation_rows = (
                await session.execute(
                    select(
                        Market.id,
                        DailyActual.temp_c,
                        Market.bucket_low_c,
                        Market.bucket_high_c,
                        Market.resolved_outcome,
                    )
                    .join(
                        DailyActual,
                        and_(
                            DailyActual.station_id == Market.station_id,
                            DailyActual.date == Market.market_date,
                            DailyActual.kind == Market.kind,
                        ),
                    )
                    .where(
                        Market.resolved_outcome.is_not(None),
                        Market.resolves_at >= now - timedelta(days=30),
                        or_(
                            Market.bucket_low_c.is_not(None),
                            Market.bucket_high_c.is_not(None),
                        ),
                    )
                )
            ).all()
            mismatches = find_resolution_mismatches(
                (market_id, temp_c, low_c, high_c, bool(resolved))
                for market_id, temp_c, low_c, high_c, resolved in reconciliation_rows
            )
            clearly_wrong = clear_mismatches(mismatches)
            if clearly_wrong:
                worst = max(item.margin_c for item in clearly_wrong)
                issues.append(
                    {
                        "code": "actuals_resolution_mismatch",
                        "detail": (
                            f"{len(clearly_wrong)} market(s) resolved against our "
                            f"observations (worst margin {worst}°C)"
                        ),
                    }
                )

            truth_gap_rows = (
                await session.execute(
                    select(
                        SettlementActual.station_id,
                        SettlementActual.date,
                        SettlementActual.kind,
                        SettlementActual.value_c,
                        DailyActual.temp_c,
                    )
                    .join(
                        DailyActual,
                        and_(
                            DailyActual.station_id == SettlementActual.station_id,
                            DailyActual.date == SettlementActual.date,
                            DailyActual.kind == SettlementActual.kind,
                        ),
                    )
                    .where(
                        SettlementActual.is_final.is_(True),
                        SettlementActual.date >= (now - timedelta(days=30)).date(),
                    )
                )
            ).all()
            meaningful_truth_gaps = [
                abs(float(source_value) - float(metar_value))
                for _station_id, _date, _kind, source_value, metar_value in truth_gap_rows
                if abs(float(source_value) - float(metar_value)) >= 0.25
            ]
            if meaningful_truth_gaps:
                issues.append(
                    {
                        "code": "settlement_truth_bias",
                        "detail": (
                            f"{len(meaningful_truth_gaps)} exact-vs-METAR gap(s), "
                            f"worst {max(meaningful_truth_gaps):.2f}°C"
                        ),
                    }
                )

            conflicting_exact_cells = await session.scalar(
                select(func.count())
                .select_from(
                    select(
                        SettlementActual.station_id,
                        SettlementActual.date,
                        SettlementActual.kind,
                    )
                    .where(SettlementActual.is_final.is_(True))
                    .group_by(
                        SettlementActual.station_id,
                        SettlementActual.date,
                        SettlementActual.kind,
                    )
                    .having(
                        func.min(SettlementActual.value_c)
                        != func.max(SettlementActual.value_c)
                    )
                    .subquery()
                )
            )
            if conflicting_exact_cells:
                issues.append(
                    {
                        "code": "settlement_actual_cell_conflict",
                        "detail": (
                            f"{conflicting_exact_cells} station/date/kind cell(s) have "
                            "contradictory final exact values; canonical truth is withheld"
                        ),
                    }
                )

            empirical_reconciliation = await resolution_reconciliation_summary(
                session,
                now=now,
            )
            empirical_settlement = await settlement_cases_summary(
                session,
                now=now,
            )
            issues.extend(
                empirical_ops_health_issues(
                    empirical_reconciliation,
                    empirical_settlement,
                )
            )
            official_resolution = await official_resolution_evidence_summary(session)
            issues.extend(official_resolution_health_issues(official_resolution))
            execution_evidence = await execution_cost_health_summary(
                session,
                now=now,
            )
            issues.extend(execution_cost_health_issues(execution_evidence))
            delivery_summary = await notification_delivery_summary(session)
            issues.extend(notification_delivery_health_issues(delivery_summary))

            durable_reconciliation_counts = {
                classification: int(count)
                for classification, count in (
                    await session.execute(
                        select(
                            ResolutionReconciliation.classification,
                            func.count(ResolutionReconciliation.id),
                        ).group_by(ResolutionReconciliation.classification)
                    )
                ).all()
            }
            reconciliation_records = list(
                (
                    await session.scalars(
                        select(ResolutionReconciliation).order_by(
                            ResolutionReconciliation.id
                        )
                    )
                ).all()
            )
            invalid_reconciliation_ids = [
                row.id
                for row in reconciliation_records
                if not resolution_reconciliation_record_is_valid(row)
            ]
            if invalid_reconciliation_ids:
                issues.append(
                    {
                        "code": "resolution_reconciliation_integrity",
                        "detail": (
                            f"{len(invalid_reconciliation_ids)} reconciliation row(s) "
                            "failed record-hash verification; ids="
                            + ",".join(
                                str(row_id) for row_id in invalid_reconciliation_ids[:20]
                            )
                        ),
                    }
                )
            clear_mismatch_station_rows = (
                await session.execute(
                    select(
                        ResolutionReconciliation.station_id,
                        func.max(ResolutionReconciliation.checked_at),
                        func.count(ResolutionReconciliation.id),
                        Station.active,
                        Station.stage,
                    )
                    .outerjoin(
                        Station,
                        Station.id == ResolutionReconciliation.station_id,
                    )
                    .where(
                        ResolutionReconciliation.classification
                        == ResolutionReconciliationClassification.CLEAR_MISMATCH
                    )
                    .group_by(
                        ResolutionReconciliation.station_id,
                        Station.active,
                        Station.stage,
                    )
                )
            ).all()
            contained_clear_mismatches = 0
            uncontained_clear_mismatches = 0
            for (
                station_id,
                latest_checked_at,
                mismatch_count,
                station_active,
                station_stage,
            ) in clear_mismatch_station_rows:
                count = int(mismatch_count)
                reliability_snapshot = (
                    reliability_by_station.get(int(station_id))
                    if station_id is not None
                    else None
                )
                if (
                    latest_checked_at is not None
                    and _clear_mismatch_station_is_contained(
                        station_id=int(station_id) if station_id is not None else None,
                        latest_checked_at=latest_checked_at,
                        reliability_snapshot=reliability_snapshot,
                        reliability_snapshot_is_valid=bool(
                            reliability_snapshot is not None
                            and station_reliability_record_is_valid(reliability_snapshot)
                        ),
                        station_execution_disabled=bool(
                            station_active is False
                            or station_stage == StationStage.DISABLED
                        ),
                        now=now,
                    )
                ):
                    contained_clear_mismatches += count
                else:
                    uncontained_clear_mismatches += count
            reconciliation_issue_map = {
                ResolutionReconciliationClassification.EXACT_SOURCE_MISMATCH: (
                    "resolution_exact_source_mismatch",
                    "official exact truth contradicts the attested venue outcome",
                ),
                ResolutionReconciliationClassification.CLEAR_MISMATCH: (
                    "resolution_clear_mismatch",
                    "METAR-derived truth clearly contradicts the attested venue outcome",
                ),
                ResolutionReconciliationClassification.EXACT_TRUTH_GAP: (
                    "resolution_exact_truth_gap",
                    "official exact truth materially differs from METAR-derived truth",
                ),
                ResolutionReconciliationClassification.BOUNDARY_SENSITIVE: (
                    "resolution_boundary_sensitive",
                    "outcome mismatch is close enough to a bucket boundary to require review",
                ),
                ResolutionReconciliationClassification.TRUTH_UNAVAILABLE: (
                    "resolution_truth_unavailable",
                    "resolved market lacks a comparable local or official numeric truth",
                ),
            }
            for reconciliation_classification, (
                code,
                detail,
            ) in reconciliation_issue_map.items():
                if (
                    reconciliation_classification
                    == ResolutionReconciliationClassification.CLEAR_MISMATCH
                ):
                    continue
                count = durable_reconciliation_counts.get(reconciliation_classification, 0)
                if count:
                    issues.append(
                        {
                            "code": code,
                            "detail": f"{count} reconciliation row(s): {detail}",
                        }
                    )
            if uncontained_clear_mismatches:
                issues.append(
                    {
                        "code": "resolution_clear_mismatch",
                        "detail": (
                            f"{uncontained_clear_mismatches} reconciliation row(s): "
                            "METAR-derived truth clearly contradicts the attested venue outcome "
                            "without a current blocking station snapshot"
                        ),
                    }
                )
            if contained_clear_mismatches:
                issues.append(
                    {
                        "code": "resolution_clear_mismatch_contained",
                        "detail": (
                            f"{contained_clear_mismatches} reconciliation row(s): "
                            "proxy-truth conflict retained; the label is quarantined and "
                            "the affected station lifecycle has absorbed it"
                        ),
                    }
                )

            missing_durable_reconciliations = await session.scalar(
                select(func.count())
                .select_from(Market)
                .outerjoin(
                    ResolutionReconciliation,
                    ResolutionReconciliation.market_id == Market.id,
                )
                .where(
                    Market.status == MarketStatus.RESOLVED,
                    Market.resolution_event_id.is_not(None),
                    ResolutionReconciliation.id.is_(None),
                )
            )
            if missing_durable_reconciliations:
                issues.append(
                    {
                        "code": "resolution_reconciliation_missing",
                        "detail": (
                            f"{missing_durable_reconciliations} attested resolved market(s) "
                            "have not entered the durable reconciliation table"
                        ),
                    }
                )

            settlement_case_counts = {
                settlement_classification: int(count)
                for settlement_classification, count in (
                    await session.execute(
                        select(
                            SettlementCase.classification,
                            func.count(SettlementCase.id),
                        ).group_by(SettlementCase.classification)
                    )
                ).all()
            }
            settlement_issue_map = {
                SettlementCaseClassification.MANUAL_REVIEW: (
                    "settlement_manual_review",
                    "event(s) exceeded the automated settlement deadline",
                ),
                SettlementCaseClassification.AMBIGUOUS: (
                    "settlement_ambiguous",
                    "event(s) returned contradictory or invalid resolution evidence",
                ),
                SettlementCaseClassification.SOURCE_MISSING: (
                    "settlement_source_missing",
                    "event source could not be fetched",
                ),
                SettlementCaseClassification.VENUE_PENDING: (
                    "settlement_venue_pending",
                    "venue has not published a complete winner vector yet",
                ),
            }
            for settlement_classification, (code, detail) in settlement_issue_map.items():
                count = settlement_case_counts.get(settlement_classification, 0)
                if count:
                    issues.append({"code": code, "detail": f"{count} {detail}"})

            capacity_sample = await session.scalar(
                select(StorageCapacitySample)
                .order_by(StorageCapacitySample.captured_at.desc())
                .limit(1)
            )
            if capacity_sample is None:
                issues.append(
                    {
                        "code": "storage_capacity_missing",
                        "detail": (
                            "no capacity sample is available; immutable CLOB "
                            "collection is fail-closed"
                        ),
                    }
                )
            else:
                ledger_bytes = (
                    capacity_sample.clob_book_batches_bytes
                    + capacity_sample.clob_market_snapshots_bytes
                )
                capacity_gate = assess_capacity_collection_gate(
                    sample_capacity_point(capacity_sample),
                    now=now,
                    budget_bytes=settings.clob_ledger_capacity_budget_bytes,
                    minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
                    sample_ttl=timedelta(minutes=settings.clob_ledger_capacity_sample_ttl_minutes),
                )
                if not capacity_gate.allowed:
                    issue_code = {
                        "capacity_budget_exhausted": "storage_capacity_exhausted",
                        "free_disk_measurement_unavailable": "storage_capacity_free_disk",
                        "free_disk_reserve_breached": "storage_capacity_free_disk",
                        "capacity_sample_stale": "storage_capacity_stale",
                        "capacity_sample_from_future": "storage_capacity_stale",
                        "capacity_sample_time_invalid": "storage_capacity_stale",
                    }.get(capacity_gate.reason, "storage_capacity_missing")
                    issues.append(
                        {
                            "code": issue_code,
                            "detail": (
                                f"capacity gate rejected collection: {capacity_gate.reason}; "
                                f"ledger={ledger_bytes:,} bytes; free_disk="
                                f"{capacity_sample.free_disk_bytes!s}; sample_age_seconds="
                                f"{capacity_gate.sample_age_seconds!s}"
                            ),
                        }
                    )
                else:
                    capacity_reason = capacity_sample.details.get("reason")
                    capacity_severity = capacity_sample.details.get("severity")
                    if capacity_reason == "measurement_window_pending":
                        issues.append(
                            {
                                "code": "storage_capacity_measurement",
                                "detail": (
                                    "full-day growth window is still collecting; "
                                    f"ledger={ledger_bytes:,} bytes"
                                ),
                            }
                        )
                    elif capacity_severity == "warning":
                        issues.append(
                            {
                                "code": "storage_capacity_projection",
                                "detail": (
                                    f"{capacity_reason}; ledger={ledger_bytes:,} bytes; "
                                    "automatic evidence deletion remains disabled"
                                ),
                            }
                        )

            # PIT uniformity: is the predicted temperature distribution
            # honest? One sample per station/day/kind (bucket markets of one
            # event share the same forecast and actual).
            pit_exact_actuals = canonical_settlement_actuals(as_of=now)
            pit_rows = (
                await session.execute(
                    select(
                        Market.station_id,
                        Market.market_date,
                        Market.kind,
                        func.coalesce(
                            pit_exact_actuals.c.value_c,
                            DailyActual.temp_c,
                        ),
                        ModelProb.components,
                    )
                    .outerjoin(
                        pit_exact_actuals,
                        and_(
                            pit_exact_actuals.c.station_id == Market.station_id,
                            pit_exact_actuals.c.date == Market.market_date,
                            pit_exact_actuals.c.kind == Market.kind,
                        ),
                    )
                    .outerjoin(
                        DailyActual,
                        and_(
                            DailyActual.station_id == Market.station_id,
                            DailyActual.date == Market.market_date,
                            DailyActual.kind == Market.kind,
                            daily_actual_fallback_eligible(
                                station_id=DailyActual.station_id,
                                actual_date=DailyActual.date,
                                kind=DailyActual.kind,
                            ),
                            not_(
                                eligible_settlement_actual_exists(
                                    station_id=DailyActual.station_id,
                                    actual_date=DailyActual.date,
                                    kind=DailyActual.kind,
                                    as_of=now,
                                )
                            ),
                        ),
                    )
                    .join(ModelProb, ModelProb.market_id == Market.id)
                    .where(
                        Market.resolved_outcome.is_not(None),
                        Market.resolves_at >= now - timedelta(days=30),
                        ModelProb.ts <= Market.resolves_at,
                        or_(
                            pit_exact_actuals.c.value_c.is_not(None),
                            DailyActual.station_id.is_not(None),
                        ),
                    )
                    .distinct(Market.id)
                    .order_by(Market.id, ModelProb.ts.desc())
                )
            ).all()
            pits: dict[tuple[int | None, object, object], float] = {}
            for station_id, market_date, kind, temp_c, details in pit_rows:
                key = (station_id, market_date, kind)
                if key in pits or not isinstance(details, dict):
                    continue
                blend = details.get("blend")
                if not isinstance(blend, dict):
                    continue
                mean_c = blend.get("mean_c")
                sigma_c = blend.get("sigma_c")
                if (
                    isinstance(mean_c, int | float)
                    and isinstance(sigma_c, int | float)
                    and sigma_c > 0
                ):
                    pits[key] = pit_value(float(temp_c), float(mean_c), float(sigma_c))
            if len(pits) >= 30:
                p_value = ks_uniform_pvalue(list(pits.values()))
                if p_value is not None and p_value < 0.01:
                    issues.append(
                        {
                            "code": "pit_miscalibrated",
                            "detail": (
                                f"forecast distribution fails uniformity: "
                                f"KS p={p_value:.4f} over {len(pits)} station-days"
                            ),
                        }
                    )

            active_markets = (
                (await session.execute(select(Market).where(Market.status == MarketStatus.ACTIVE)))
                .scalars()
                .all()
            )
            market_by_id = {market.id: market for market in active_markets}
            probabilities = (
                (
                    await session.execute(
                        select(ModelProb)
                        .where(ModelProb.market_id.in_(market_by_id))
                        .distinct(ModelProb.market_id)
                        .order_by(ModelProb.market_id, ModelProb.ts.desc())
                    )
                )
                .scalars()
                .all()
            )
            latest_probabilities = {
                probability.market_id: probability for probability in probabilities
            }
            forecast_provenance_boundary = await session.scalar(
                text("SELECT opened_at FROM forecast_provenance_runtime WHERE id = 1")
            )
            if isinstance(forecast_provenance_boundary, datetime):
                forecast_provenance_defects = invalid_forecast_input_provenance(
                    (
                        (
                            probability.market_id,
                            probability.ts,
                            probability.components,
                        )
                        for probability in latest_probabilities.values()
                    ),
                    boundary=forecast_provenance_boundary,
                )
                if forecast_provenance_defects:
                    issues.append(
                        {
                            "code": "forecast_input_provenance_missing",
                            "detail": (
                                f"{len(forecast_provenance_defects)} latest post-boundary "
                                "active-market probability row(s) do not bind complete "
                                "immutable forecast source evidence; markets="
                                + ",".join(
                                    str(market_id) for market_id in forecast_provenance_defects[:10]
                                )
                            ),
                        }
                    )
            promotion_defects = invalid_promotion_provenance(
                (
                    (
                        probability.market_id,
                        float(probability.horizon_h),
                        probability.components,
                        market_by_id[probability.market_id].station_id,
                        market_by_id[probability.market_id].kind,
                    )
                    for probability in latest_probabilities.values()
                ),
                promotion_manifest,
            )
            if promotion_defects:
                issues.append(
                    {
                        "code": "accuracy_promotion_provenance",
                        "detail": (
                            f"{len(promotion_defects)} latest probability provenance "
                            f"defect(s): {'; '.join(promotion_defects[:5])}"
                        ),
                    }
                )
            stale_probability_count = sum(
                market_id not in latest_probabilities
                or now - latest_probabilities[market_id].ts > timedelta(minutes=15)
                for market_id in market_by_id
            )
            if stale_probability_count:
                issues.append(
                    {
                        "code": "probability_stale",
                        "detail": f"{stale_probability_count} market(s)",
                    }
                )
            non_dual_probability_count = sum(
                probability.market_anchor_protocol != CLOB_DUAL_PROTOCOL
                for probability in latest_probabilities.values()
            )
            if non_dual_probability_count:
                issues.append(
                    {
                        "code": "dual_probability_anchor_missing",
                        "detail": (
                            f"{non_dual_probability_count} latest active-market "
                            "probability row(s) are legacy/non-executable diagnostics"
                        ),
                    }
                )
            by_event: dict[str, list[float]] = defaultdict(list)
            for market_id, probability in latest_probabilities.items():
                by_event[market_by_id[market_id].event_id].append(float(probability.prob))
            invalid_events = invalid_event_probability_sums(by_event)
            if invalid_events:
                issues.append(
                    {
                        "code": "event_probability_sum",
                        "detail": f"{len(invalid_events)} event(s)",
                    }
                )

            bankroll = await session.scalar(
                select(BankrollLog.balance_usd)
                .where(BankrollLog.mode == ExecutionMode.PAPER)
                .order_by(BankrollLog.ts.desc())
                .limit(1)
            )
            bankroll_usd = float(bankroll or settings.initial_bankroll_usd)
            position_rows = (
                await session.execute(
                    select(Position, Market)
                    .join(Market, Position.market_id == Market.id)
                    .where(
                        Position.mode == ExecutionMode.PAPER,
                        Position.status == PositionStatus.OPEN,
                    )
                )
            ).all()
            market_exposure: dict[int, float] = defaultdict(float)
            station_exposure: dict[tuple[int | None, object], float] = defaultdict(float)
            for position, market in position_rows:
                market_exposure[market.id] += float(position.size_usd)
                station_exposure[(market.station_id, market.market_date)] += float(
                    position.size_usd
                )
            market_limit = settings.market_exposure_fraction * bankroll_usd
            market_breaches = [
                (market_id, value)
                for market_id, value in market_exposure.items()
                if value > market_limit + 0.01
            ]
            if market_breaches:
                detail = "; ".join(
                    f"market_id={market_id} ${value:.2f}/${market_limit:.2f} "
                    f"(excess ${value - market_limit:.2f})"
                    for market_id, value in sorted(
                        market_breaches, key=lambda item: item[1], reverse=True
                    )[:5]
                )
                issues.append({"code": "market_exposure_breach", "detail": detail})
                if any(
                    exposure_breach_severity(
                        value,
                        market_limit,
                        settings.exposure_hard_stop_multiplier,
                    )
                    is HealthSeverity.BLOCKING
                    for _market_id, value in market_breaches
                ):
                    severity_overrides["market_exposure_breach"] = HealthSeverity.BLOCKING
            station_limit = settings.station_day_exposure_fraction * bankroll_usd
            station_breaches = [
                (station_key, value)
                for station_key, value in station_exposure.items()
                if value > station_limit + 0.01
            ]
            if station_breaches:
                detail = "; ".join(
                    f"station_day={station_key} ${value:.2f}/${station_limit:.2f} "
                    f"(excess ${value - station_limit:.2f})"
                    for station_key, value in sorted(
                        station_breaches, key=lambda item: item[1], reverse=True
                    )[:5]
                )
                issues.append({"code": "station_day_exposure_breach", "detail": detail})
                if any(
                    exposure_breach_severity(
                        value,
                        station_limit,
                        settings.exposure_hard_stop_multiplier,
                    )
                    is HealthSeverity.BLOCKING
                    for _station_key, value in station_breaches
                ):
                    severity_overrides["station_day_exposure_breach"] = HealthSeverity.BLOCKING

            missing_clv = await session.scalar(
                select(func.count())
                .select_from(Position)
                .where(
                    Position.mode == ExecutionMode.PAPER,
                    Position.status == PositionStatus.CLOSED,
                    Position.clv.is_(None),
                )
            )
            if missing_clv:
                issues.append({"code": "clv_missing", "detail": str(missing_clv)})

            # Maker orders legitimately rest for the whole TTL; only signals
            # that outlived it plus a scheduler-cycle slack are stuck.
            stale_pending = await session.scalar(
                select(func.count())
                .select_from(Signal)
                .where(
                    Signal.status == SignalStatus.PENDING,
                    Signal.ts < now - timedelta(minutes=settings.paper_order_ttl_minutes + 5),
                )
            )
            if stale_pending:
                issues.append({"code": "pending_signal_stale", "detail": str(stale_pending)})

            issues = [
                normalize_health_issue(
                    issue,
                    default_severity=health_severity_for_code(issue["code"]),
                    severity_override=severity_overrides.get(issue["code"]),
                )
                for issue in issues
            ]
            status = aggregate_health_status(issues)
            session.add(HealthAudit(ts=now, status=status, issues=issues))
            if health_status_requires_kill_switch(status):
                await acquire_capacity_write_lock(session)
                await set_kill_switch(session, True)
        alert_bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
        try:
            async with sessions.begin() as session:
                for issue in issues:
                    await enqueue_notification(
                        session,
                        f"health:{alert_bucket}:{issue['code']}",
                        f"STRATUS health {status}: {issue['code']} "
                        f"[{issue['severity']}]\n{issue['detail']}",
                    )
        except Exception:  # noqa: BLE001 - health state/kill are already durable
            logger.exception("health notifications could not be queued")
        logger.info("health audit complete: status=%s issues=%d", status, len(issues))
        return status, len(issues)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_health(settings))


if __name__ == "__main__":
    main()
