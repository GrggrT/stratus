from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Annotated, Any, Literal, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient
from stratus.config import Settings
from stratus.db.models import (
    AccuracyPrediction,
    AccuracyPromotionEvidenceMember,
    AccuracyPromotionLook,
    AccuracyPromotionTrial,
    BankrollLog,
    Calibration,
    ClobBookBatch,
    ClobMarketSnapshot,
    ExecutionMode,
    ExecutionOrderIntent,
    ForecastEvaluation,
    ForecastModelRegistry,
    ForecastTrajectory,
    GammaOutcomeEvent,
    HealthAudit,
    JobRun,
    Market,
    MarketPrice,
    MarketStatus,
    ModelDriftEvent,
    ModelProb,
    ModelSkillReport,
    Observation,
    OfficialResolutionLicenseAttestation,
    Order,
    Position,
    PositionStatus,
    ProbabilityPipelineReleaseEpoch,
    ProbabilityPipelineReleasePlanRecord,
    ResolutionContract,
    ResolutionReconciliation,
    ResolutionReconciliationClassification,
    SettlementActual,
    SettlementCase,
    SettlementCaseClassification,
    SettlementProbabilityCalibrator,
    Signal,
    SignalStatus,
    Station,
    StationStage,
    ValidationCheckpoint,
)
from stratus.db.models import (
    EvidenceContinuityLane as EvidenceContinuityLaneRecord,
)
from stratus.jobs.generate_weighted_ensemble import (
    EXPERT_VERSION as WEIGHTED_EXPERT_VERSION,
)
from stratus.jobs.generate_weighted_ensemble import (
    SHRUNK_EXPERT_VERSION as WEIGHTED_SHRUNK_EXPERT_VERSION,
)
from stratus.repositories.accuracy import accuracy_prediction_record_is_valid
from stratus.repositories.accuracy_promotions import (
    PROMOTION_REGISTRATION_EPOCH,
    load_effective_accuracy_promotion_manifest,
)
from stratus.repositories.analytics import (
    actual_order_fill,
    execution_fill_aggregate_subquery,
    resolved_trade_outcomes,
)
from stratus.repositories.empirical_ops import (
    resolution_reconciliation_summary,
    settlement_cases_summary,
)
from stratus.repositories.evidence_continuity import (
    EvidenceLanePlan,
    ProbabilityPipelineReleaseIntegrityError,
    ProbabilityPipelineReleasePlan,
    acquire_probability_pipeline_release_lock,
    append_probability_pipeline_release_epoch,
    build_probability_pipeline_release_plan,
    current_evidence_continuity_lanes,
    current_probability_pipeline_release_epoch,
    current_probability_pipeline_release_plan_record,
    list_evidence_continuity_lanes,
    list_probability_pipeline_release_epochs,
    persist_probability_pipeline_release_plan,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.evidence_continuity_reporting import (
    EvidenceCollectionWindow,
    EvidenceContinuityLane,
    evidence_continuity_summary,
)
from stratus.repositories.execution_cost_reporting import (
    ExecutionCostMetrics,
    ExecutionCostOrderSummary,
    ExecutionCostSummary,
)
from stratus.repositories.execution_cost_reporting import (
    execution_cost_orders as load_execution_cost_orders,
)
from stratus.repositories.execution_cost_reporting import (
    execution_cost_report as load_execution_cost_report,
)
from stratus.repositories.forecast_provenance_reporting import (
    forecast_provenance_summary,
)
from stratus.repositories.model_probs import (
    latest_active_market_probability_components,
)
from stratus.repositories.official_resolution import (
    AUTHORIZED_ARTIFACT_ACQUISITION,
    LicenseAttestationInput,
    OfficialResolutionIntegrityError,
    OfficialSourceAcquisitionInput,
    list_official_resolution_contract_evidence,
    official_resolution_evidence_summary,
    persist_official_resolution_evidence,
    register_license_attestation,
)
from stratus.repositories.operator_commands import enqueue_command, recent_commands
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.selection_policy import (
    selection_policy_ledger_counts,
    valid_selection_policy_decisions,
)
from stratus.repositories.station_reliability import (
    latest_station_reliability_by_station,
    station_reliability_record_is_valid,
)
from stratus.repositories.storage_capacity import (
    capacity_baseline,
    latest_capacity_samples,
    sample_capacity_point,
)
from stratus.repositories.telegram import (
    acknowledge_deliveries_through,
    acknowledge_delivery,
    list_delivery_alerts,
    notification_delivery_summary,
)
from stratus.repositories.validation_ledger import (
    SOURCE_INTEGRITY_LABEL,
    ProspectiveValidationDataset,
    load_prospective_validation_dataset,
)
from stratus.repositories.venue_runtime_control import list_venue_runtime_controls
from stratus.services.clob_evidence import (
    CLOB_DUAL_PROTOCOL,
    LEGACY_MARKET_PROTOCOL,
)
from stratus.services.evidence_continuity import EvidenceDueCounts
from stratus.services.evidence_release_manifest import (
    EvidenceReleaseManifest,
    EvidenceReleaseManifestError,
    build_evidence_release_manifest,
    decide_evidence_lane_actions,
)
from stratus.services.forecast_provenance import model_input_provenance_is_complete
from stratus.services.live_analytics import (
    BrierSlice,
    ClvSlice,
    LivePerformance,
    PositionStat,
    ProspectiveValidationTrade,
    TradeOutcome,
    brier_comparison,
    brier_root_cause,
    clv_root_cause,
    clv_summary,
    cumulative_equity,
    edge_calibration,
    edge_slope,
    price_band,
    prospective_clv_outcomes,
    prospective_position_stats,
    prospective_trade_outcomes,
    raw_model_brier,
    summarize_live_performance,
    summarize_prospective_validation,
)
from stratus.services.official_resolution import (
    LICENSED_JSON_ADAPTER_NAME,
    LICENSED_JSON_ADAPTER_VERSION,
    OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
    WUNDERGROUND_ADAPTER_NAME,
    WUNDERGROUND_ADAPTER_VERSION,
    ContractBindingError,
    ExtractionError,
    LicensedContractBinding,
    LicensedJsonFieldMapAdapter,
    LicensedJsonFieldMapConfig,
    LicensePolicyError,
    OfficialResolutionContract,
    OfficialResolutionError,
    OfficialResolutionLicense,
    OfficialSourceResponse,
    ResponseEnvelopeError,
    WundergroundOfficialResolutionAdapter,
    license_policy_payload,
    license_policy_sha256,
    parser_config_sha256,
    reviewed_official_resolution_contract,
    seal_license_policy,
    validate_contract_binding,
)
from stratus.services.official_resolution_artifacts import (
    AuthorizedArtifactError,
    read_authorized_artifact,
)
from stratus.services.operator_commands import validate_command
from stratus.services.pipeline_identity import (
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
)
from stratus.services.portfolio_marks import PriceMark, calculate_position_mark
from stratus.services.promotion_protocol import (
    PROMOTION_PROTOCOL_VERSION,
    PROMOTION_TARGET_BLOCKS,
    PROMOTION_TEST_METHOD,
)
from stratus.services.selection_policy import (
    SelectionPolicyScore,
    evaluate_recorded_selection_policies,
)
from stratus.services.station_reliability import (
    station_reliability_policy_hash,
    station_signal_gate_reasons,
)
from stratus.services.storage_capacity import (
    CAPACITY_POLICY_VERSION,
    assess_capacity_collection_gate,
    assess_clob_ledger_capacity,
)
from stratus.services.telegram_reports import calculate_live_gate
from stratus.services.uptime import MaintenanceWindow

router = APIRouter(prefix="/api/v1", tags=["operator-monitoring"])

_PROSPECTIVE_SAMPLE_SCOPE = "prospective"
_HISTORICAL_SAMPLE_SCOPE = "historical"
_ALL_PROTOCOL_HISTORY_SCOPE = "all-protocol-history"
SessionFactory = async_sessionmaker[AsyncSession]


def require_configured_operator_key(request: Request) -> None:
    """Mutations are refused outright until an API key is configured.

    Read-only monitoring may run keyless on loopback, but anything that can
    stop or resume trading must never be an unauthenticated HTTP call.
    """
    settings = cast(Settings, request.app.state.settings)
    if settings.operator_api_key is None:
        raise HTTPException(
            status_code=403,
            detail="mutations are disabled until OPERATOR_API_KEY is configured",
        )


class RuntimeDto(BaseModel):
    mode: str
    kill_switch: bool
    halted_reason: str | None


class VenueRuntimeItem(BaseModel):
    venue: str
    enabled: bool
    mode: str
    kill_switch: bool
    halted_reason: str | None
    live_confirmation_until: datetime | None
    strategy_version: str | None
    risk_config_version: str
    last_reconciled_at: datetime | None
    updated_at: datetime | None


class VenueRuntimeResponse(BaseModel):
    generated_at: datetime
    items: list[VenueRuntimeItem]


class HealthAuditDto(BaseModel):
    status: str
    timestamp: datetime
    issues: list[dict[str, Any]]


class ValidationDto(BaseModel):
    checkpoint_day: int
    evaluated_at: datetime
    eligible: bool
    passed: bool
    metrics: dict[str, Any]


class OverviewResponse(BaseModel):
    generated_at: datetime
    runtime: RuntimeDto
    active_markets: int
    blocked_markets: int
    open_positions: int
    pending_signals: int
    bankroll_usd: float | None
    open_exposure_usd: float
    health_audit: HealthAuditDto | None
    validation: ValidationDto | None


class AlertItem(BaseModel):
    id: int
    event_key: str
    category: str
    payload: str
    created_at: datetime
    acknowledged_at: datetime | None
    sent_at: datetime | None
    attempts: int
    last_attempt_at: datetime | None
    next_attempt_at: datetime | None
    lease_until: datetime | None
    dead_lettered_at: datetime | None
    error: str | None
    external_state: str


class AlertsResponse(BaseModel):
    generated_at: datetime
    query_state: str
    total_count: int
    unacknowledged_count: int
    oldest_unacknowledged_at: datetime | None
    oldest_unacknowledged_age_seconds: int | None
    channel: str
    channel_state: str
    activated_at: datetime | None
    heartbeat_at: datetime | None
    heartbeat_stale: bool
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_error: str | None
    pending_after_activation: int
    oldest_pending_after_activation_at: datetime | None
    backlog_overdue: bool
    dead_letter_count: int
    items: list[AlertItem]


class AlertAckThroughRequest(BaseModel):
    through_id: int = Field(ge=1)


class AlertAcknowledgementResponse(BaseModel):
    acknowledged: int
    through_id: int | None = None


class MarketItem(BaseModel):
    id: int
    polymarket_id: str
    station_icao: str | None
    city: str | None
    market_date: date
    kind: str
    bucket_low_c: float | None
    bucket_high_c: float | None
    status: str
    station_verified: bool
    verification_reason: str | None
    resolves_at: datetime


class MarketsResponse(BaseModel):
    generated_at: datetime
    items: list[MarketItem]


class SignalItem(BaseModel):
    id: int
    market_id: int
    timestamp: datetime
    polymarket_id: str
    station_icao: str | None
    side: str
    model_probability: float
    market_price: float
    edge: float
    kelly_fraction: float
    size_usd: float
    confidence: str
    status: str
    skip_reason: str | None
    benchmark_protocol: str
    sample_scope: str


class SignalsResponse(BaseModel):
    generated_at: datetime
    items: list[SignalItem]


class PositionItem(BaseModel):
    id: int
    market_id: int
    polymarket_id: str
    station_icao: str | None
    market_date: date
    kind: str
    mode: str
    side: str
    average_price: float
    size_usd: float
    fee_usd: float | None
    status: str
    pnl_usd: float | None
    clv: float | None
    closed_at: datetime | None
    benchmark_protocol: str
    sample_scope: str
    # Open positions only: the side-adjusted latest midpoint and the
    # mark-to-market against it. Null when closed or no price snapshot.
    mark_price: float | None
    mark_at: datetime | None
    mark_age_seconds: int | None
    mark_stale: bool
    unrealized_pnl_usd: float | None
    liquidation_price: float | None
    liquidation_fee_usd: float | None
    liquidation_pnl_usd: float | None
    # Mark-move impact on the current holdings. This intentionally excludes
    # position-size changes, which remain visible through the open count.
    pnl_change_1m_usd: float | None
    pnl_change_5m_usd: float | None
    pnl_change_60m_usd: float | None


class PositionsResponse(BaseModel):
    generated_at: datetime
    items: list[PositionItem]


class CalibrationItem(BaseModel):
    station_icao: str
    model: str
    horizon_bucket: str
    kind: str
    bias_c: float
    sigma_c: float
    sample_count: int
    brier: float | None
    updated_at: datetime


class CalibrationsResponse(BaseModel):
    generated_at: datetime
    items: list[CalibrationItem]


class StationLifecycleItem(BaseModel):
    icao: str
    name: str
    city: str
    stage: str
    active: bool
    iem_network: str | None
    calibration_rows: int
    min_calibration_samples: int | None
    active_markets: int
    closed_paper_positions: int
    latest_observation_at: datetime | None
    data_ready_for_paper: bool
    reliability_status: str
    reliability_score: float | None
    reliability_lower_bound: float | None
    reliability_samples: int
    reliability_conflicts: int
    reliability_boundary_samples: int
    reliability_unavailable_samples: int
    reliability_evaluated_at: datetime | None
    reliability_reasons: list[str]
    latest_conflict_at: datetime | None
    latest_conflict_date: date | None
    latest_conflict_kind: str | None
    resolution_cooldown_until: datetime | None
    observation_cooldown_until: datetime | None
    reliability_policy_hash: str | None
    reliability_source_hash: str | None
    reliability_record_hash: str | None
    selection_evaluated_for_date: date
    selection_blocked: bool
    selection_reasons: list[str]


class StationLifecycleResponse(BaseModel):
    generated_at: datetime
    paper_stations: int
    shadow_stations: int
    approved_stations: int
    inactive_stations: int
    items: list[StationLifecycleItem]


class LiveSummaryResponse(BaseModel):
    generated_at: datetime
    # Raw execution/PnL fields retain the full history. Every field derived
    # from a market benchmark (Brier, CLV, skill/luck) is restricted to the
    # prospective dual-token sample described by the following fields.
    performance_sample_scope: str
    benchmark_protocol: str
    benchmark_sample_scope: str
    benchmark_positions: int
    benchmark_roi: float | None
    benchmark_roi_ci_low: float | None
    benchmark_roi_ci_high: float | None
    positions: int
    closed: int
    open: int
    wins: int
    win_rate: float | None
    total_pnl_usd: float
    robust_pnl_usd: float | None
    total_cost_usd: float
    total_fees_usd: float
    roi: float | None
    robust_roi: float | None
    avg_clv: float | None
    clv_beat_rate: float | None
    # Skill/luck decomposition and bootstrap CIs: the anti-luck view.
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    decomposed: int
    roi_ci_low: float | None
    roi_ci_high: float | None
    skill_pnl_ci_low: float | None
    skill_pnl_ci_high: float | None
    statistically_positive: bool | None
    brier_model: float | None
    brier_market: float | None
    n_resolved_trades: int


class EdgeBucketItem(BaseModel):
    low: float
    high: float | None
    count: int
    avg_predicted_edge: float | None
    avg_realized_return: float | None


class GateCheckItem(BaseModel):
    name: str
    passed: bool
    detail: str


class EdgeReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    benchmark_positions: int
    clv_samples: int
    n_resolved_trades: int
    brier_model: float | None
    brier_market: float | None
    pipeline_version: str | None
    pipeline_resolved_trades: int
    pipeline_brier_model: float | None
    pipeline_brier_market: float | None
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    roi_ci_low: float | None
    roi_ci_high: float | None
    skill_pnl_ci_low: float | None
    skill_pnl_ci_high: float | None
    statistically_positive: bool | None
    edge_slope: float | None
    buckets: list[EdgeBucketItem]
    # Formal "edge is proven" checklist gating live mode (always computed
    # from the paper track record, regardless of the mode filter above).
    gate_passed: bool
    gate_checks: list[GateCheckItem]


class ProspectiveValidationPointItem(BaseModel):
    sequence: int
    pipeline_cohort: str
    station_icao: str
    market_date: date
    kind: str
    recognized_at: datetime
    orders: int
    block_pnl_usd: float
    block_cost_usd: float
    block_skill_pnl_usd: float | None
    block_luck_pnl_usd: float | None
    cumulative_pnl_usd: float
    cumulative_skill_pnl_usd: float | None
    cumulative_luck_pnl_usd: float | None
    equity_usd: float
    drawdown_usd: float
    max_drawdown_usd: float


class ProspectiveValidationLedgerResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    source_integrity: str
    registration_opened_at: datetime
    effective_sample_opened_at: datetime
    predictive_lane_id: int | None
    predictive_lane_identity_hash: str | None
    predictive_lane_boundary_at: datetime | None
    execution_lane_id: int | None
    execution_lane_identity_hash: str | None
    execution_lane_boundary_at: datetime | None
    definition_version: str
    definition_hash: str
    cohort_scope: str
    requested_pipeline_cohort: str | None
    current_pipeline_cohort: str | None
    selected_pipeline_cohort: str | None
    selected_is_current: bool
    enrolled_orders: int
    eligible_enrollments: int
    excluded_enrollments: int
    pending_outcomes: int
    pending_blocks: int
    closed_orders: int
    independent_blocks: int
    initial_bankroll_usd: float
    total_pnl_usd: float
    total_cost_usd: float
    roi: float | None
    skill_pnl_usd: float | None
    luck_pnl_usd: float | None
    decomposed_orders: int
    robust_pnl_usd: float | None
    robust_roi: float | None
    max_drawdown_usd: float
    max_drawdown_fraction: float
    largest_order_id: int | None
    largest_order_pnl_usd: float | None
    largest_order_abs_pnl_share: float | None
    pnl_excluding_largest_usd: float | None
    roi_excluding_largest: float | None
    points: list[ProspectiveValidationPointItem]


class EvidenceReleaseEpochItem(BaseModel):
    id: int
    opened_at: datetime
    pipeline_version: str
    material_fingerprint: str
    activation_manifest_hash: str
    cohort: str
    image_digest: str
    plan_id: int
    previous_epoch_id: int | None
    approval_reason: str
    plan_hash: str
    record_hash: str


class EvidenceSkipReasonItem(BaseModel):
    reason: str
    count: int


class EvidenceContinuityLaneItem(BaseModel):
    cohort: str
    epoch_id: int | None
    scope: str
    opened_at: datetime | None
    probability_rows: int
    materialized_events: int
    coherent_events: int
    execution_intents: int
    filled_orders: int
    no_fill_orders: int
    open_orders: int
    cost_seals: int
    eligible_cost_seals: int
    required_marks: int
    observed_marks: int
    enrollments: int
    eligible_enrollments: int
    outcomes: int
    independent_blocks: int
    first_evidence_at: datetime | None
    last_evidence_at: datetime | None


class EvidenceContinuityControlLaneItem(BaseModel):
    id: int
    epoch_id: int
    lane_name: str
    action: str
    identity_hash: str
    parent_lane_id: int | None
    boundary_at: datetime
    manifest: dict[str, Any]
    rationale: str
    record_hash: str


class EvidenceDueCountsItem(BaseModel):
    validation_waiting_for_due: int
    validation_due_pending: int
    validation_overdue: int
    validation_classified: int
    execution_waiting_for_due: int
    execution_due_pending: int
    execution_overdue: int
    execution_classified: int
    mark_waiting_for_due: int
    mark_due_pending: int
    mark_overdue: int
    mark_classified: int
    waiting_for_due: int
    due_pending: int
    overdue: int
    classified: int
    total: int


class EvidenceCollectionWindowItem(BaseModel):
    started_at: datetime | None
    ended_at: datetime | None
    latest_day_started_at: datetime | None
    latest_day_ended_at: datetime | None
    latest_day_grace_until: datetime | None
    latest_complete_day_ended_at: datetime | None
    grace_seconds: int
    candidate_full_days: int
    evaluated_days: int
    maintenance_excluded_days: int
    complete_days: int
    waiting_for_due_days: int
    due_pending_days: int
    collecting_on_schedule_days: int
    overdue_days: int
    stalled_days: int
    required_scheduler_jobs: list[str]
    missing_scheduler_jobs: list[str]
    due_counts: EvidenceDueCountsItem


class EvidenceContinuityResponse(BaseModel):
    generated_at: datetime
    state: str
    candidate_cohort: str
    candidate_image_digest: str
    candidate_release_manifest_hash: str | None
    active_cohort: str | None
    active_release_manifest_hash: str | None
    cohort_match: bool
    image_match: bool
    execution_gate_allowed: bool
    execution_gate_reason: str
    lane_identity_mismatches: list[str]
    frozen_lanes: list[str]
    active_epoch: EvidenceReleaseEpochItem | None
    epoch_count: int
    cohort_changes_30d: int
    current_bottleneck: str
    current_bottleneck_detail: str
    liveness: str
    eta_reason: str | None
    collection_window: EvidenceCollectionWindowItem
    complete_days: int
    outcome_velocity_per_day: float | None
    eta_100_at: datetime | None
    eta_200_at: datetime | None
    paper_stations: int
    shadow_stations: int
    open_positions: int
    open_exposure_usd: float
    bankroll_usd: float
    signal_rows_since_epoch: int
    pending_signals: int
    top_skip_reasons: list[EvidenceSkipReasonItem]
    continuity_lanes: list[EvidenceContinuityControlLaneItem]
    lanes: list[EvidenceContinuityLaneItem]


class EvidenceContinuityResponseCache:
    """Per-app bounded cache with one in-flight full-report computation."""

    __slots__ = (
        "_clock",
        "_expires_at",
        "_generation",
        "_lock",
        "_response",
        "_ttl_seconds",
    )

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("evidence continuity cache TTL must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._response: EvidenceContinuityResponse | None = None
        self._expires_at = 0.0
        self._generation = 0

    async def get_or_create(
        self,
        factory: Callable[[], Awaitable[EvidenceContinuityResponse]],
    ) -> EvidenceContinuityResponse:
        cached = self._response
        if cached is not None and self._clock() < self._expires_at:
            return cached
        while True:
            async with self._lock:
                cached = self._response
                if cached is not None and self._clock() < self._expires_at:
                    return cached
                generation = self._generation
                response = await factory()
                # Activation invalidation may race an informational read. Do
                # not repopulate the cache with a response from before cutover.
                if generation != self._generation:
                    continue
                self._response = response
                self._expires_at = self._clock() + self._ttl_seconds
                return response

    def invalidate(self) -> None:
        self._generation += 1
        self._response = None
        self._expires_at = 0.0


class EvidenceReleasePlanRequest(BaseModel):
    reviewed_by: str = Field(min_length=3, max_length=128)
    approval_reason: str = Field(min_length=16, max_length=2000)


class EvidenceReleaseActivationRequest(EvidenceReleasePlanRequest):
    expected_plan_hash: str = Field(pattern="^[0-9a-f]{64}$")


class EvidenceReleasePlanResponse(BaseModel):
    generated_at: datetime
    requires_activation: bool
    persisted_plan_id: int | None
    persisted_record_hash: str | None
    planned_at: datetime | None
    active_cohort: str | None
    pipeline_version: str
    material_fingerprint: str
    activation_manifest_hash: str
    cohort: str
    image_digest: str
    previous_epoch_id: int | None
    release_manifest: dict[str, Any]
    release_manifest_hash: str
    lane_plans: list[dict[str, Any]]
    lane_manifest_hash: str
    reviewed_by: str
    approval_reason: str
    plan_hash: str


class EvidenceReleaseActivationResponse(BaseModel):
    generated_at: datetime
    activated: EvidenceReleaseEpochItem


class EvidenceReleaseEpochHistoryResponse(BaseModel):
    generated_at: datetime
    count: int
    epochs: list[EvidenceReleaseEpochItem]


class EvidenceContinuityLaneHistoryResponse(BaseModel):
    generated_at: datetime
    count: int
    lanes: list[EvidenceContinuityControlLaneItem]


class SelectionPolicyItem(BaseModel):
    name: str
    description: str
    is_reference: bool
    max_predicted_edge: float | None
    opportunities: int
    accepted: int
    retention: float
    independent_blocks: int
    brier_model: float | None
    brier_market: float | None
    model_minus_market: float | None
    total_pnl_usd: float
    avg_realized_return: float | None
    information_value: float
    delta_information_value: float
    delta_ci_low: float | None
    delta_ci_high: float | None
    eligible: bool


class SelectionPolicyReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    pipeline_version: str | None
    historical_benchmark_protocol: str
    historical_sample_scope: str
    historical_opportunities: int
    historical_edge_slope: float | None
    historical_model_minus_market: float | None
    recorded_opportunities: int
    current_opportunities: int
    candidates: list[SelectionPolicyItem]


class BrierSliceItem(BaseModel):
    dimension: str
    value: str
    count: int
    brier_model: float
    brier_market: float
    model_minus_market: float
    brier_raw_model: float | None
    raw_model_minus_market: float | None
    raw_count: int
    avg_predicted_edge: float
    avg_realized_return: float
    win_rate: float


class BrierReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    n_resolved_trades: int
    brier_model: float | None
    brier_market: float | None
    brier_raw_model: float | None
    raw_count: int
    model_minus_market: float | None
    raw_model_minus_market: float | None
    slices: list[BrierSliceItem]


class ClvSliceItem(BaseModel):
    dimension: str
    value: str
    count: int
    avg_clv: float
    clv_beat_rate: float
    avg_entry_price: float
    avg_fee_usd: float
    avg_realized_return: float
    win_rate: float


class ClvReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    n_closed_positions: int
    avg_clv: float | None
    clv_beat_rate: float | None
    slices: list[ClvSliceItem]


class RootCauseTradeItem(BaseModel):
    id: int
    record_type: str
    order_id: int | None
    signal_id: int | None
    position_id: int | None
    market_id: int
    polymarket_id: str
    station_icao: str | None
    market_date: date
    kind: str
    side: str
    confidence: str | None
    horizon_bucket: str
    timestamp: datetime | None
    closed_at: datetime | None
    entry_price: float
    closing_line_price: float | None
    model_probability: float | None
    market_probability: float | None
    raw_model_probability: float | None
    predicted_edge: float | None
    brier_model_error: float | None
    brier_market_error: float | None
    brier_delta: float | None
    clv: float | None
    pnl_usd: float
    cost_usd: float
    fee_usd: float | None
    won: bool
    resolved_outcome: bool | None
    benchmark_protocol: str


class RootCauseDrilldownResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    metric: str
    dimension: str
    value: str
    count: int
    items: list[RootCauseTradeItem]


class RootCauseActionItem(BaseModel):
    metric: str
    dimension: str
    value: str
    count: int
    action: str
    severity: str
    detail: str


class RootCauseActionsResponse(BaseModel):
    generated_at: datetime
    mode: str
    benchmark_protocol: str
    sample_scope: str
    items: list[RootCauseActionItem]


class EquityPointItem(BaseModel):
    t: datetime
    cumulative_pnl_usd: float


class EquityResponse(BaseModel):
    generated_at: datetime
    points: list[EquityPointItem]


class OrderItem(BaseModel):
    id: int
    signal_id: int
    polymarket_id: str
    station_icao: str | None
    mode: str
    clob_order_id: str | None
    side: str
    # ``price`` remains the compatibility field for the requested limit.
    price: float
    requested_limit_price: float
    actual_fill_vwap: float | None
    size_usd: float
    filled_usd: float
    fee_usd: float | None
    status: str
    created_at: datetime
    filled_at: datetime | None


class OrdersResponse(BaseModel):
    generated_at: datetime
    items: list[OrderItem]


class JobItem(BaseModel):
    id: int
    job_id: str
    scheduled_at: datetime
    finished_at: datetime
    status: str
    error: str | None


class JobsResponse(BaseModel):
    generated_at: datetime
    items: list[JobItem]


class MarketPricePoint(BaseModel):
    t: datetime
    midpoint: float
    best_bid: float
    best_ask: float
    bid_depth_usd: float
    ask_depth_usd: float


class MarketPriceSeriesResponse(BaseModel):
    generated_at: datetime
    market_id: int
    benchmark_protocol: str
    sample_scope: str
    points: list[MarketPricePoint]


class ProbabilityPoint(BaseModel):
    t: datetime
    source: str
    model_probability: float | None
    market_price: float | None
    edge: float | None
    sigma: float | None
    horizon_h: float | None
    confidence: str | None
    status: str | None
    benchmark_protocol: str
    sample_scope: str


class ProbabilitySeriesResponse(BaseModel):
    generated_at: datetime
    market_id: int
    benchmark_protocol: str
    sample_scope: str
    points: list[ProbabilityPoint]


class OrderBookLevel(BaseModel):
    price: float
    size: float
    notional_usd: float


class OrderBookResponse(BaseModel):
    generated_at: datetime
    market_id: int
    side: str
    token_id: str | None
    source_status: str
    error: str | None
    midpoint: float | None
    best_bid: float | None
    best_ask: float | None
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]


class CommandRequest(BaseModel):
    request_id: str = Field(min_length=8, max_length=64)
    command: str = Field(max_length=32)
    reason: str = Field(min_length=4, max_length=2000)
    actor: str = Field(default="console", min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)


class CommandItem(BaseModel):
    id: int
    request_id: str
    command: str
    payload: dict[str, Any]
    actor: str
    reason: str
    status: str
    detail: str | None
    created_at: datetime
    executed_at: datetime | None


class CommandAccepted(BaseModel):
    created: bool
    item: CommandItem


class CommandsResponse(BaseModel):
    generated_at: datetime
    items: list[CommandItem]


class OfficialResolutionParserConfigRequest(BaseModel):
    schema_id: str = Field(min_length=1, max_length=128)
    series_mode: Literal["scalar", "parallel_arrays"]
    schema_path: tuple[str, ...] | None = None
    event_id_path: tuple[str, ...] | None = None
    rules_hash_path: tuple[str, ...] | None = None
    source_family_path: tuple[str, ...] | None = None
    source_url_path: tuple[str, ...] | None = None
    station_path: tuple[str, ...] | None = None
    date_path: tuple[str, ...]
    high_path: tuple[str, ...]
    low_path: tuple[str, ...]
    unit_path: tuple[str, ...] | None = None
    decimal_places_path: tuple[str, ...] | None = None
    finalization_rule_path: tuple[str, ...] | None = None
    window_start_at_path: tuple[str, ...] | None = None
    window_end_at_path: tuple[str, ...] | None = None
    finalization_witness_at_path: tuple[str, ...] | None = None
    published_at_path: tuple[str, ...] | None = None
    station_code_literal: str = Field(min_length=1, max_length=16)
    source_unit_literal: Literal["C", "F"]
    decimal_places_literal: Literal[0, 1]
    finalization_rule_literal: Literal[
        "next_day_first_point",
        "source_finalized",
        "source_published",
    ]
    series_anchor_local_time: time | None = None
    source_window_start_local: time = time(0, 0)
    source_window_end_local: time = time(0, 0)
    source_window_end_day_offset: Literal[0, 1] = 1
    max_series_items: int = Field(default=400, ge=1, le=10_000)
    max_body_bytes: int = Field(default=1_000_000, ge=1_024, le=10_000_000)
    max_json_depth: int = Field(default=16, ge=1, le=32)
    max_json_nodes: int = Field(default=20_000, ge=1, le=100_000)


class OfficialResolutionLicenseDraftRequest(BaseModel):
    contract_id: int = Field(gt=0)
    rules_hash: str = Field(pattern="^[0-9a-f]{64}$")
    license_id: str = Field(min_length=1, max_length=64)
    grantee: str = Field(min_length=1, max_length=128)
    endpoint_url: str = Field(min_length=8, max_length=2000)
    adapter_name: Literal[
        "licensed-json-field-map",
        "wunderground-licensed-json-field-map",
    ]
    adapter_version: str = Field(min_length=1, max_length=64)
    parser_config: OfficialResolutionParserConfigRequest
    terms_url: str = Field(min_length=8, max_length=2000)
    terms_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    valid_from: datetime
    expires_at: datetime
    storage_permitted: Literal[True]
    commercial_use_permitted: Literal[True]
    max_response_age_seconds: int = Field(ge=1, le=86_400)


class OfficialResolutionLicenseAttestationRequest(OfficialResolutionLicenseDraftRequest):
    approved_policy_sha256: str = Field(pattern="^[0-9a-f]{64}$")
    reviewer: str = Field(min_length=1, max_length=128)
    reviewed_at: datetime
    rationale: str = Field(min_length=16, max_length=4000)


class OfficialResolutionLicensePolicyResponse(BaseModel):
    contract_id: int
    license_protocol: str
    policy_sha256: str
    parser_config_sha256: str
    canonical_policy: dict[str, Any]


class OfficialResolutionLicenseAttestationResponse(BaseModel):
    id: int
    contract_id: int
    license_id: str
    policy_sha256: str
    parser_config_sha256: str
    record_hash: str
    attested_at: datetime
    expires_at: datetime


class OfficialResolutionImportRequest(BaseModel):
    contract_id: int = Field(gt=0)
    rules_hash: str = Field(pattern="^[0-9a-f]{64}$")
    relative_artifact_path: str = Field(min_length=1, max_length=500)
    actor: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=16, max_length=1000)


class OfficialResolutionImportResponse(BaseModel):
    contract_id: int
    license_attestation_id: int
    response_id: int
    exact_actual_id: int
    settlement_actual_id: int
    event_id: str
    station_icao: str
    market_date: date
    kind: str
    source_token: str
    source_value: float
    source_unit: str
    value_c: float
    response_sha256: str
    exact_record_hash: str


class OfficialResolutionSourcesResponse(BaseModel):
    generated_at: datetime
    state: str
    source_integrity: str
    artifact_import_enabled: bool
    opened_at: datetime
    definition_version: str
    definition_hash: str
    license_protocol: str
    response_protocol: str
    extraction_protocol: str
    active_contracts: int
    licensed_contracts: int
    expired_contracts: int
    blocked_contracts: int
    license_attestations: int
    source_responses: int
    exact_actuals: int
    projected_settlement_actuals: int
    exact_coverage: float | None
    raw_response_bytes: int
    latest_response_at: datetime | None
    latest_exact_actual_at: datetime | None


class OfficialResolutionContractEvidenceItem(BaseModel):
    contract_id: int
    event_id: str
    rules_hash: str
    source_family: str
    settlement_source_url: str
    station_code: str
    kind: str
    retired_at: datetime | None
    license_attestation_id: int | None
    attestation_version: str | None
    license_status: str | None
    license_valid_from: datetime | None
    license_expires_at: datetime | None
    adapter_name: str | None
    adapter_version: str | None
    parser_config_hash: str | None
    license_policy_hash: str | None
    license_record_hash: str | None
    response_count: int
    latest_response_id: int | None
    acquisition_protocol: str | None
    request_method: str | None
    request_sha256: str | None
    response_sha256: str | None
    response_bytes: int | None
    response_first_seen_at: datetime | None
    source_published_at: datetime | None
    response_record_hash: str | None
    exact_actual_id: int | None
    station_id: int | None
    market_date: date | None
    source_value: float | None
    source_unit: str | None
    value_c: float | None
    exact_record_hash: str | None
    settlement_actual_id: int | None


class OfficialResolutionContractsResponse(BaseModel):
    generated_at: datetime
    limit: int
    offset: int
    items: list[OfficialResolutionContractEvidenceItem]


class ResolutionReconciliationItem(BaseModel):
    market_id: int
    event_id: str
    station_icao: str | None
    market_date: date
    kind: str
    classification: str
    resolved_yes: bool
    metar_temp_c: float | None
    metar_implied_yes: bool | None
    exact_temp_c: float | None
    exact_implied_yes: bool | None
    boundary_margin_c: float | None
    exact_truth_gap_c: float | None
    resolution_event_id: int
    settlement_actual_id: int | None
    resolution_vector_hash: str
    exact_source_hash: str | None
    checked_at: datetime
    record_hash: str


class ResolutionReconciliationResponse(BaseModel):
    generated_at: datetime
    total: int
    metar_available: int
    metar_consistent: int
    exact_available: int
    exact_source_hash_available: int
    exact_coverage: float = Field(ge=0.0, le=1.0)
    latest_checked_at: datetime | None
    oldest_checked_at: datetime | None
    stale_records: int
    fresh: bool
    stale_after_seconds: int
    classification_counts: dict[str, int]
    items: list[ResolutionReconciliationItem]


class SettlementCaseItem(BaseModel):
    event_id: str
    classification: str
    previous_classification: str | None
    reason_code: str
    event_resolves_at: datetime
    age_seconds: int
    first_detected_at: datetime
    last_checked_at: datetime
    classification_changed_at: datetime
    attempt_count: int
    transition_count: int
    source_url: str | None
    source_response_sha256: str | None
    resolution_event_id: int | None
    resolution_vector_hash: str | None
    last_error: str | None
    details: dict[str, Any]
    resolved_at: datetime | None
    record_hash: str


class SettlementCasesResponse(BaseModel):
    generated_at: datetime
    total: int
    open_total: int
    oldest_open_at: datetime | None
    latest_checked_at: datetime | None
    stale_open_cases: int
    fresh: bool
    stale_after_seconds: int
    classification_counts: dict[str, int]
    items: list[SettlementCaseItem]


class StorageCapacityResponse(BaseModel):
    generated_at: datetime
    sample_at: datetime | None
    sample_age_seconds: float | None
    sample_fresh: bool
    severity: str
    reason: str
    measurement_complete: bool
    observed_hours: float | None
    database_bytes: int | None
    ledger_bytes: int | None
    clob_book_batches_bytes: int | None
    clob_market_snapshots_bytes: int | None
    clob_batch_count: int | None
    clob_snapshot_count: int | None
    free_disk_bytes: int | None
    minimum_free_disk_bytes: int
    growth_bytes_per_day: float | None
    projected_ledger_bytes: float | None
    days_to_budget: float | None
    budget_bytes: int
    policy_version: str
    automatic_deletion: bool
    retention_rule: str


class ForecastProvenanceCollectorItem(BaseModel):
    collector: str
    request_model: str
    ingests: int
    stations: int
    responses: int
    raw_bytes: int
    verified_upstream_inits: int
    latest_first_seen_at: datetime | None


class ForecastProvenanceResponse(BaseModel):
    generated_at: datetime
    state: str
    source_integrity: str
    opened_at: datetime
    definition_version: str
    definition_hash: str
    ingests: int
    stations: int
    responses: int
    raw_bytes: int
    verified_upstream_inits: int
    single_run_ingests: int
    pipeline_fingerprints: int
    materialized_forecasts: int
    materialized_trajectories: int
    latest_first_seen_at: datetime | None
    latest_age_seconds: float | None
    latest_active_probabilities: int
    complete_active_probabilities: int
    average_http_latency_ms: float | None
    p95_http_latency_ms: float | None
    average_ingestion_lag_ms: float | None
    p95_ingestion_lag_ms: float | None
    average_verified_upstream_latency_minutes: float | None
    p95_verified_upstream_latency_minutes: float | None
    collectors: list[ForecastProvenanceCollectorItem]


class ExecutionCostCoverageItem(BaseModel):
    known: int
    total: int
    ratio: float | None


class ExecutionCostMetricsItem(BaseModel):
    eligible_orders: int
    eligible_shares: float
    gross_model_edge: float | None
    latency_movement: float | None
    taker_spread: float | None
    taker_slippage: float | None
    maker_improvement: float | None
    fee_drag: float | None
    realized_net_edge: float | None
    adverse_selection_5m: float | None
    adverse_selection_30m: float | None


class ExecutionCostReasonItem(BaseModel):
    reason: str
    orders: int


class ExecutionCostSliceItem(BaseModel):
    dimension: str
    value: str
    attempts: int
    filled: int
    sealed: int
    eligible: int
    metrics: ExecutionCostMetricsItem


class ExecutionCostReportResponse(BaseModel):
    generated_at: datetime
    mode: str
    state: str
    source_integrity: str
    opened_at: datetime
    definition_version: str
    definition_hash: str
    attempts: int
    filled_attempts: int
    no_fill_attempts: int
    pending_attempts: int
    sealed_attempts: int
    eligible_seals: int
    ineligible_seals: int
    maker_attempts: int
    maker_fills: int
    maker_fill_rate: float | None
    maker_seals: int
    taker_seals: int
    mixed_seals: int
    unknown_role_seals: int
    expected_slippage_coverage: ExecutionCostCoverageItem
    exact_fee_coverage: ExecutionCostCoverageItem
    markout_5m_coverage: ExecutionCostCoverageItem
    markout_30m_coverage: ExecutionCostCoverageItem
    metrics: ExecutionCostMetricsItem
    reasons: list[ExecutionCostReasonItem]
    slices: list[ExecutionCostSliceItem]


class ExecutionCostOrderItem(BaseModel):
    order_id: int
    intent_id: int
    signal_id: int
    market_id: int
    station_icao: str | None
    market_date: date
    kind: str
    confidence: str
    horizon_h: float | None
    mode: str
    side: str
    order_style: str
    requested_price: float
    requested_notional_usd: float
    intent_at: datetime
    terminal_status: str | None
    obligation_state: str | None
    eligible: bool | None
    eligibility_reason: str | None
    aggregate_role: str | None
    leg_count: int
    total_shares: float | None
    vwap_fill_price: float | None
    total_fee_usd: float | None
    gross_model_edge: float | None
    latency_movement: float | None
    taker_spread: float | None
    taker_slippage: float | None
    maker_improvement: float | None
    fee_drag: float | None
    realized_net_edge: float | None
    adverse_selection_5m: float | None
    adverse_selection_30m: float | None
    sealed_at: datetime | None


class ExecutionCostOrdersResponse(BaseModel):
    generated_at: datetime
    mode: str
    total: int
    limit: int
    offset: int
    items: list[ExecutionCostOrderItem]


class AccuracyModelItem(BaseModel):
    provider: str
    model: str
    model_version: str
    region: str
    enabled: bool
    shadow_only: bool
    expected_latency_minutes: int | None
    source_url: str
    license_status: str


class AccuracyDriftItem(BaseModel):
    station_id: int | None
    detected_at: datetime
    window_days: int
    metric: str
    baseline_value: float
    recent_value: float
    delta: float
    severity: str


class AccuracyPromotionTrialItem(BaseModel):
    expert: str
    station_id: int
    station_icao: str
    horizon_bucket: str
    kind: str
    status: str
    evidence_blocks: int
    target_blocks: int
    mean_delta_brier: float | None
    wins: int | None
    losses: int | None
    ties: int | None
    confidence_radius: float | None
    mean_improvement_lcb: float | None
    one_sided_p_bound: float | None
    allocated_alpha: float
    activated: bool


class AccuracyPromotionOverviewItem(BaseModel):
    protocol_version: str
    registration_epoch: str
    test_method: str
    family_alpha: float
    family_size: int
    registered_trials: int
    collecting_trials: int
    passed_trials: int
    failed_trials: int
    activated_trials: int
    maximum_evidence_blocks: int
    target_blocks: int
    activation_manifest_hash: str
    trials: list[AccuracyPromotionTrialItem]


class AccuracyOverviewResponse(BaseModel):
    generated_at: datetime
    benchmark_protocol: str
    sample_scope: str
    resolution_contracts: int
    licensed_contracts: int
    exact_settlement_actuals: int
    forecast_evaluations: int
    independent_samples: int
    mean_model_brier: float | None
    mean_market_brier: float | None
    qualified_skill_reports: int
    open_drift_events: int
    active_stack_experts: int
    promotion: AccuracyPromotionOverviewItem
    settlement_calibrator: SettlementCalibratorItem | None
    censored_challenger: CensoredChallengerItem
    weighted_ensemble: WeightedEnsembleItem
    trajectory_challenger: TrajectoryChallengerItem
    conformal_guardrail: ConformalGuardrailItem
    nowcast_challenger: NowcastChallengerItem
    hierarchical_challenger: HierarchicalChallengerItem
    regime_challenger: RegimeChallengerItem
    stack_challenger: StackChallengerItem
    nonlinear_challengers: NonlinearChallengerItem
    models: list[AccuracyModelItem]
    drift: list[AccuracyDriftItem]


class AccuracyOverviewResponseCache:
    """Per-app bounded cache with one in-flight dashboard computation."""

    __slots__ = (
        "_clock",
        "_expires_at",
        "_failure_backoff_seconds",
        "_failure_expires_at",
        "_inflight",
        "_lock",
        "_response",
        "_ttl_seconds",
    )

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        failure_backoff_seconds: float = 5.0,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("accuracy overview cache TTL must be positive")
        if failure_backoff_seconds <= 0:
            raise ValueError("accuracy overview cache failure backoff must be positive")
        self._ttl_seconds = ttl_seconds
        self._failure_backoff_seconds = failure_backoff_seconds
        self._clock = clock
        self._lock = asyncio.Lock()
        self._response: AccuracyOverviewResponse | None = None
        self._expires_at = 0.0
        self._inflight: asyncio.Task[AccuracyOverviewResponse] | None = None
        self._failure_expires_at = 0.0

    async def get_or_create(
        self,
        factory: Callable[[], Awaitable[AccuracyOverviewResponse]],
    ) -> AccuracyOverviewResponse:
        cached = self._response
        if cached is not None and self._clock() < self._expires_at:
            return cached
        async with self._lock:
            cached = self._response
            if cached is not None and self._clock() < self._expires_at:
                return cached

            task = self._inflight
            if (
                task is None
                or (task.done() and self._clock() >= self._failure_expires_at)
            ):
                task = asyncio.create_task(self._populate(factory))
                self._inflight = task

        # The computation belongs to the application cache, not to any one
        # HTTP request. Cancelling a disconnected follower must not cancel the
        # shared SQL work for all remaining waiters.
        return await asyncio.shield(task)

    async def _populate(
        self,
        factory: Callable[[], Awaitable[AccuracyOverviewResponse]],
    ) -> AccuracyOverviewResponse:
        task = asyncio.current_task()
        try:
            response = await factory()
        except Exception:
            async with self._lock:
                if self._inflight is task:
                    self._failure_expires_at = (
                        self._clock() + self._failure_backoff_seconds
                    )
            raise

        async with self._lock:
            if self._inflight is task:
                self._response = response
                self._expires_at = self._clock() + self._ttl_seconds
                self._inflight = None
                self._failure_expires_at = 0.0
        return response

    def invalidate(self) -> None:
        self._response = None
        self._expires_at = 0.0
        task = self._inflight
        self._inflight = None
        self._failure_expires_at = 0.0
        if task is not None and not task.done():
            task.cancel()


class SettlementCalibratorItem(BaseModel):
    scope_key: str
    alpha: float
    beta: float
    training_blocks: int
    training_buckets: int
    minimum_blocks: int
    baseline_brier: float | None
    calibrated_brier: float | None
    baseline_ece: float | None
    calibrated_ece: float | None
    delta_ci_low: float | None
    delta_ci_high: float | None
    eligible: bool
    applied: bool
    trained_through: datetime


class CensoredChallengerItem(BaseModel):
    current_markets: int
    training_blocks: int
    bias_c: float | None
    sigma_scale: float | None
    historical_baseline_brier: float | None
    historical_calibrated_brier: float | None
    historical_ci_low: float | None
    historical_ci_high: float | None
    historical_oof_eligible: bool
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class WeightedEnsembleItem(BaseModel):
    current_markets: int
    fitted_scopes: int
    minimum_training_sample_n: int
    maximum_training_sample_n: int
    historical_samples: int
    historical_blocks: int
    historical_baseline_rmse_c: float | None
    historical_candidate_rmse_c: float | None
    historical_delta_mse: float | None
    historical_ci_low: float | None
    historical_ci_high: float | None
    historical_oof_eligible: bool
    settlement_available_events: int
    settlement_fitted_events: int
    settlement_backtest_blocks: int
    settlement_backtest_market_rows: int
    settlement_baseline_brier: float | None
    settlement_candidate_brier: float | None
    settlement_delta_brier: float | None
    settlement_ci_low: float | None
    settlement_ci_high: float | None
    settlement_aligned_eligible: bool
    settlement_empirical_candidate_brier: float | None
    settlement_empirical_delta_brier: float | None
    settlement_empirical_ci_low: float | None
    settlement_empirical_ci_high: float | None
    settlement_shrunk_empirical_weight: float
    settlement_shrunk_empirical_brier: float | None
    settlement_shrunk_empirical_delta_brier: float | None
    settlement_shrunk_empirical_ci_low: float | None
    settlement_shrunk_empirical_ci_high: float | None
    settlement_shrunk_empirical_eligible: bool
    minimum_settlement_backtest_blocks: int
    shrunk_current_markets: int
    shrunk_legacy_markets: int
    shrunk_expert_version: str
    shrunk_evidence_protocol: str
    shrunk_latest_generated_at: datetime | None
    shrunk_minimum_blocks: int
    shrunk_maximum_blocks: int
    shrunk_evaluated_slices: int
    shrunk_eligible_slices: int
    shrunk_delta_brier: float | None
    shrunk_ci_low: float | None
    shrunk_ci_high: float | None
    weights: dict[str, float]
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class TrajectoryChallengerItem(BaseModel):
    stored_trajectories: int
    latest_available_at: datetime | None
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    brier: float | None
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class ConformalGuardrailItem(BaseModel):
    residual_samples: int
    scale: float | None
    target_coverage: float
    empirical_coverage_before: float | None
    empirical_coverage_after: float | None
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class NowcastChallengerItem(BaseModel):
    current_markets: int
    recent_bias_c: float | None
    extreme_shift_c: float | None
    matched_hours: int
    latest_observed_hour: str | None
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class HierarchicalChallengerItem(BaseModel):
    current_markets: int
    local_weight: float | None
    local_sample_n: int
    peer_sample_n: int
    peer_stations: int
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class RegimeChallengerItem(BaseModel):
    active_shifts: int
    critical_shifts: int
    current_markets: int
    strongest_adjustment_c: float | None
    strongest_model: str | None
    model_version: str | None
    boundary_kind: str | None
    boundary_date: str | None
    latest_detected_at: datetime | None
    minimum_blocks: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class NonlinearExpertItem(BaseModel):
    expert: str
    current_markets: int
    training_sample_n: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


class NonlinearChallengerItem(BaseModel):
    minimum_training_samples: int
    minimum_blocks: int
    experts: list[NonlinearExpertItem]


class StackChallengerItem(BaseModel):
    minimum_training_samples: int
    minimum_blocks: int
    current_markets: int
    training_sample_n: int
    fit_source: str | None
    local_sample_n: int
    peer_sample_n: int
    peer_stations: int
    maximum_blocks: int
    evaluated_slices: int
    eligible_slices: int
    delta_brier: float | None
    ci_low: float | None
    ci_high: float | None
    applied: bool


def _sessions(request: Request) -> SessionFactory:
    return cast(SessionFactory, request.app.state.sessions)


def _settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def _official_parser_config(
    value: OfficialResolutionParserConfigRequest,
) -> LicensedJsonFieldMapConfig:
    return LicensedJsonFieldMapConfig(**value.model_dump())


def _official_contract(
    contract: ResolutionContract,
) -> OfficialResolutionContract:
    return reviewed_official_resolution_contract(
        event_id=contract.event_id,
        rules_hash=contract.rules_hash,
        source_family=contract.source_family,
        source_url=contract.source_url,
        station_code=contract.station_code,
        local_timezone=contract.local_timezone,
        statistic=cast(Literal["high", "low"], contract.statistic),
        source_unit=contract.source_unit,
        decimal_places=contract.decimal_places,
        finalization_rule=contract.finalization_rule,
    )


def _official_license_draft(
    body: OfficialResolutionLicenseDraftRequest,
    contract: ResolutionContract,
) -> OfficialResolutionLicense:
    if contract.rules_hash != body.rules_hash:
        raise ContractBindingError("request rules hash differs from the resolution contract")
    config = _official_parser_config(body.parser_config)
    official_contract = _official_contract(contract)
    # Construction performs the source-family-independent syntax checks;
    # adapter validation below proves the exact window/station/unit binding.
    draft = OfficialResolutionLicense(
        license_id=body.license_id,
        grantee=body.grantee,
        source_family=contract.source_family,
        endpoint_url=body.endpoint_url,
        adapter_name=body.adapter_name,
        adapter_version=body.adapter_version,
        parser_config=config,
        parser_config_sha256=parser_config_sha256(config),
        contract_bindings=(
            LicensedContractBinding(
                event_id=contract.event_id,
                rules_hash=contract.rules_hash,
                source_url=contract.source_url,
                station_code=contract.station_code,
            ),
        ),
        terms_url=body.terms_url,
        terms_sha256=body.terms_sha256,
        valid_from=body.valid_from,
        expires_at=body.expires_at,
        storage_permitted=body.storage_permitted,
        commercial_use_permitted=body.commercial_use_permitted,
        max_response_age_seconds=body.max_response_age_seconds,
    )
    if body.adapter_name == LICENSED_JSON_ADAPTER_NAME:
        if body.adapter_version != LICENSED_JSON_ADAPTER_VERSION:
            raise LicensePolicyError("licensed JSON adapter version is unsupported")
    else:
        if body.adapter_version != WUNDERGROUND_ADAPTER_VERSION:
            raise LicensePolicyError("Wunderground adapter version is unsupported")
    # A preview never parses data, but it must prove semantic equivalence now;
    # otherwise an invalid product/window could be legally attested and only
    # fail much later during import.
    validate_contract_binding(
        official_contract,
        license_policy=draft,
        config=config,
    )
    return draft


def _official_policy_from_attestation(
    attestation: OfficialResolutionLicenseAttestation,
    contract: ResolutionContract,
) -> OfficialResolutionLicense:
    parser_request = OfficialResolutionParserConfigRequest.model_validate(attestation.parser_config)
    config = _official_parser_config(parser_request)
    policy = OfficialResolutionLicense(
        license_id=attestation.attestation_version,
        grantee=attestation.grantee,
        source_family=attestation.source_family,
        endpoint_url=attestation.endpoint_url,
        adapter_name=attestation.adapter_name,
        adapter_version=attestation.adapter_version,
        parser_config=config,
        parser_config_sha256=attestation.parser_config_hash,
        contract_bindings=(
            LicensedContractBinding(
                event_id=contract.event_id,
                rules_hash=contract.rules_hash,
                source_url=contract.source_url,
                station_code=contract.station_code,
            ),
        ),
        terms_url=attestation.license_url,
        terms_sha256=attestation.terms_sha256,
        valid_from=attestation.valid_from,
        expires_at=attestation.expires_at,
        storage_permitted=(
            attestation.value_retention_permitted and attestation.raw_response_retention_permitted
        ),
        commercial_use_permitted=attestation.commercial_use_permitted,
        max_response_age_seconds=attestation.max_response_age_seconds,
        policy_sha256=attestation.policy_hash,
    )
    if license_policy_sha256(policy) != attestation.policy_hash:
        raise LicensePolicyError("stored license policy no longer reproduces its seal")
    return policy


def _official_adapter(
    policy: OfficialResolutionLicense,
) -> LicensedJsonFieldMapAdapter | WundergroundOfficialResolutionAdapter:
    if (
        policy.adapter_name == LICENSED_JSON_ADAPTER_NAME
        and policy.adapter_version == LICENSED_JSON_ADAPTER_VERSION
    ):
        return LicensedJsonFieldMapAdapter(policy.parser_config)
    if (
        policy.adapter_name == WUNDERGROUND_ADAPTER_NAME
        and policy.adapter_version == WUNDERGROUND_ADAPTER_VERSION
    ):
        return WundergroundOfficialResolutionAdapter(policy.parser_config)
    raise LicensePolicyError("stored official-resolution adapter is not supported")


def _number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _benchmark_sample_scope(protocol: str) -> str:
    if protocol == CLOB_DUAL_PROTOCOL:
        return _PROSPECTIVE_SAMPLE_SCOPE
    if protocol == LEGACY_MARKET_PROTOCOL:
        return _HISTORICAL_SAMPLE_SCOPE
    return "unclassified"


def _json_number(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _nonlinear_expert_summary(
    expert: str,
    reports: list[ModelSkillReport],
    coverage: tuple[int, int],
    *,
    applied: bool,
) -> NonlinearExpertItem:
    representative = max(reports, key=lambda row: row.sample_n, default=None)
    return NonlinearExpertItem(
        expert=expert,
        current_markets=coverage[0],
        training_sample_n=coverage[1],
        maximum_blocks=representative.sample_n if representative is not None else 0,
        evaluated_slices=len(reports),
        eligible_slices=sum(row.eligible for row in reports),
        delta_brier=_number(representative.delta_brier) if representative is not None else None,
        ci_low=_number(representative.ci_low) if representative is not None else None,
        ci_high=_number(representative.ci_high) if representative is not None else None,
        applied=applied,
    )


async def _price_marks_at(
    session: AsyncSession,
    market_ids: set[int],
    at: datetime | None = None,
) -> dict[int, PriceMark]:
    if not market_ids:
        return {}
    dual_statement = (
        select(ClobMarketSnapshot, ClobBookBatch.first_seen_at)
        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
        .where(
            ClobMarketSnapshot.market_id.in_(market_ids),
            ClobMarketSnapshot.eligible.is_(True),
        )
    )
    if at is not None:
        dual_statement = dual_statement.where(ClobBookBatch.first_seen_at <= at)
    dual_rows = (
        await session.execute(
            dual_statement.distinct(ClobMarketSnapshot.market_id).order_by(
                ClobMarketSnapshot.market_id,
                ClobBookBatch.first_seen_at.desc(),
            )
        )
    ).all()
    result = {
        snapshot.market_id: PriceMark(
            yes_midpoint=snapshot.yes_midpoint,
            yes_best_bid=snapshot.yes_best_bid,
            yes_best_ask=snapshot.yes_best_ask,
            no_midpoint=snapshot.no_midpoint,
            no_best_bid=snapshot.no_best_bid,
            no_best_ask=snapshot.no_best_ask,
            ts=first_seen_at,
        )
        for snapshot, first_seen_at in dual_rows
        if snapshot.yes_midpoint is not None
        and snapshot.yes_best_bid is not None
        and snapshot.yes_best_ask is not None
        and snapshot.no_midpoint is not None
        and snapshot.no_best_bid is not None
        and snapshot.no_best_ask is not None
    }
    missing_market_ids = market_ids - set(result)
    if not missing_market_ids:
        return result

    # Historical fallback only. New trading/evaluation never consumes this
    # synthetic projection, but it keeps pre-0031 positions visible until the
    # first dual-token snapshot is collected for their market.
    legacy_statement = select(
        MarketPrice.market_id,
        MarketPrice.midpoint,
        MarketPrice.best_bid,
        MarketPrice.best_ask,
        MarketPrice.ts,
    ).where(MarketPrice.market_id.in_(missing_market_ids))
    if at is not None:
        legacy_statement = legacy_statement.where(MarketPrice.ts <= at)
    legacy_rows = (
        await session.execute(
            legacy_statement.distinct(MarketPrice.market_id).order_by(
                MarketPrice.market_id, MarketPrice.ts.desc()
            )
        )
    ).all()
    result.update(
        {
            market_id: PriceMark(
                yes_midpoint=midpoint,
                yes_best_bid=best_bid,
                yes_best_ask=best_ask,
                no_midpoint=Decimal(1) - midpoint,
                no_best_bid=Decimal(1) - best_ask,
                no_best_ask=Decimal(1) - best_bid,
                ts=timestamp,
            )
            for market_id, midpoint, best_bid, best_ask, timestamp in legacy_rows
        }
    )
    return result


_BRIER_DRILLDOWN_DIMENSIONS = {
    "station",
    "kind",
    "horizon",
    "confidence",
    "side",
    "pipeline_version",
}
_CLV_DRILLDOWN_DIMENSIONS = {
    "station",
    "kind",
    "horizon",
    "confidence",
    "side",
    "price_band",
}


def _book_levels(raw: Any, *, reverse: bool, limit: int = 10) -> list[OrderBookLevel]:
    if not isinstance(raw, list):
        raise ValueError("book side is missing or not a list")
    parsed: list[tuple[Decimal, Decimal]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("book level is not an object")
        try:
            price = Decimal(str(item["price"]))
            size = Decimal(str(item["size"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("book level contains an invalid price or size") from exc
        if not Decimal(0) < price < Decimal(1) or size <= 0:
            raise ValueError("book level is out of range")
        parsed.append((price, size))
    parsed.sort(key=lambda level: level[0], reverse=reverse)
    return [
        OrderBookLevel(
            price=float(price),
            size=float(size),
            notional_usd=float((price * size).quantize(Decimal("0.0001"))),
        )
        for price, size in parsed[:limit]
    ]


def _empty_orderbook(
    *,
    market_id: int,
    side: str,
    token_id: str | None,
    error: str,
) -> OrderBookResponse:
    return OrderBookResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        side=side,
        token_id=token_id,
        source_status="unavailable",
        error=error,
        midpoint=None,
        best_bid=None,
        best_ask=None,
        bids=[],
        asks=[],
    )


@router.get("/venues", response_model=VenueRuntimeResponse)
async def venues(request: Request) -> VenueRuntimeResponse:
    async with _sessions(request)() as session:
        controls = await list_venue_runtime_controls(session)
    return VenueRuntimeResponse(
        generated_at=datetime.now(UTC),
        items=[
            VenueRuntimeItem(
                venue=control.venue.value,
                enabled=control.enabled,
                mode=control.mode.value,
                kill_switch=control.kill_switch,
                halted_reason=control.halted_reason,
                live_confirmation_until=control.live_confirmation_until,
                strategy_version=control.strategy_version,
                risk_config_version=control.risk_config_version,
                last_reconciled_at=control.last_reconciled_at,
                updated_at=control.updated_at,
            )
            for control in controls
        ],
    )


@router.get("/overview", response_model=OverviewResponse)
async def overview(request: Request) -> OverviewResponse:
    async with _sessions(request)() as session:
        runtime = await get_runtime_control(session)
        market_rows = (
            await session.execute(
                select(Market.status, func.count(Market.id)).group_by(Market.status)
            )
        ).all()
        market_counts = {status: int(count) for status, count in market_rows}
        open_positions = int(
            await session.scalar(
                select(func.count(Position.id)).where(
                    Position.status == PositionStatus.OPEN,
                    Position.mode == runtime.mode,
                )
            )
            or 0
        )
        pending_signals = int(
            await session.scalar(
                select(func.count(Signal.id)).where(Signal.status == SignalStatus.PENDING)
            )
            or 0
        )
        bankroll = await session.scalar(
            select(BankrollLog.balance_usd)
            .where(BankrollLog.mode == runtime.mode)
            .order_by(BankrollLog.ts.desc())
            .limit(1)
        )
        exposure = await session.scalar(
            select(func.coalesce(func.sum(Position.size_usd), 0)).where(
                Position.status == PositionStatus.OPEN,
                Position.mode == runtime.mode,
            )
        )
        health = await session.scalar(select(HealthAudit).order_by(HealthAudit.ts.desc()).limit(1))
        validation = await session.scalar(
            select(ValidationCheckpoint).order_by(ValidationCheckpoint.evaluated_at.desc()).limit(1)
        )

    return OverviewResponse(
        generated_at=datetime.now(UTC),
        runtime=RuntimeDto(
            mode=runtime.mode.value,
            kill_switch=runtime.kill_switch,
            halted_reason=runtime.halted_reason,
        ),
        active_markets=market_counts.get(MarketStatus.ACTIVE, 0),
        blocked_markets=market_counts.get(MarketStatus.BLOCKED, 0),
        open_positions=open_positions,
        pending_signals=pending_signals,
        bankroll_usd=_number(bankroll),
        open_exposure_usd=float(exposure or 0),
        health_audit=(
            HealthAuditDto(status=health.status, timestamp=health.ts, issues=health.issues)
            if health is not None
            else None
        ),
        validation=(
            ValidationDto(
                checkpoint_day=validation.checkpoint_day,
                evaluated_at=validation.evaluated_at,
                eligible=validation.eligible,
                passed=validation.passed,
                metrics=validation.metrics,
            )
            if validation is not None
            else None
        ),
    )


@router.get("/markets", response_model=MarketsResponse)
async def markets(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MarketsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Market, Station.icao, Station.city)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Market.resolves_at.desc())
                .limit(limit)
            )
        ).all()
    return MarketsResponse(
        generated_at=datetime.now(UTC),
        items=[
            MarketItem(
                id=market.id,
                polymarket_id=market.polymarket_id,
                station_icao=icao,
                city=city,
                market_date=market.market_date,
                kind=market.kind,
                bucket_low_c=_number(market.bucket_low_c),
                bucket_high_c=_number(market.bucket_high_c),
                status=market.status.value,
                station_verified=market.station_verified,
                verification_reason=market.station_verification_reason,
                resolves_at=market.resolves_at,
            )
            for market, icao, city in rows
        ],
    )


@router.get("/signals", response_model=SignalsResponse)
async def signals(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> SignalsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Signal, Market.polymarket_id, Station.icao)
                .join(Market, Market.id == Signal.market_id)
                .outerjoin(Station, Station.id == Market.station_id)
                .order_by(Signal.ts.desc())
                .limit(limit)
            )
        ).all()
    return SignalsResponse(
        generated_at=datetime.now(UTC),
        items=[
            SignalItem(
                id=signal.id,
                market_id=signal.market_id,
                timestamp=signal.ts,
                polymarket_id=polymarket_id,
                station_icao=icao,
                side=signal.side.value,
                model_probability=float(signal.model_prob),
                market_price=float(signal.market_price),
                edge=float(signal.edge),
                kelly_fraction=float(signal.kelly_frac),
                size_usd=float(signal.size_usd),
                confidence=signal.confidence.value,
                status=signal.status.value,
                skip_reason=signal.skip_reason,
                benchmark_protocol=signal.benchmark_protocol,
                sample_scope=_benchmark_sample_scope(signal.benchmark_protocol),
            )
            for signal, polymarket_id, icao in rows
        ],
    )


@router.get("/positions", response_model=PositionsResponse)
async def positions(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> PositionsResponse:
    now = datetime.now(UTC)
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Position, Market)
                .join(Market, Market.id == Position.market_id)
                .order_by(Position.id.desc())
                .limit(limit)
            )
        ).all()
        market_ids = {market.id for _, market in rows}
        station_rows = (
            await session.execute(
                select(Market.id, Station.icao)
                .join(Station, Station.id == Market.station_id)
                .where(Market.id.in_(market_ids))
            )
        ).all()
        station_names: dict[int, str] = {mid: icao for mid, icao in station_rows}
        latest_marks = await _price_marks_at(session, market_ids)
        marks_1m = await _price_marks_at(session, market_ids, now - timedelta(minutes=1))
        marks_5m = await _price_marks_at(session, market_ids, now - timedelta(minutes=5))
        marks_60m = await _price_marks_at(session, market_ids, now - timedelta(minutes=60))

    items = []
    for position, market in rows:
        position_mark = None
        latest = latest_marks.get(market.id)
        if position.status is PositionStatus.OPEN and latest is not None:
            position_mark = calculate_position_mark(
                side=position.side,
                shares=position.size_shares,
                cost_usd=position.size_usd,
                latest=latest,
                at=now,
                one_minute=marks_1m.get(market.id),
                five_minutes=marks_5m.get(market.id),
                sixty_minutes=marks_60m.get(market.id),
                fees_enabled=market.fees_enabled,
                fee_rate=market.fee_rate,
                fee_exponent=market.fee_exponent,
            )
        items.append(
            PositionItem(
                id=position.id,
                market_id=market.id,
                polymarket_id=market.polymarket_id,
                station_icao=station_names.get(market.id),
                market_date=market.market_date,
                kind=market.kind,
                mode=position.mode.value,
                side=position.side.value,
                average_price=float(position.avg_price),
                size_usd=float(position.size_usd),
                fee_usd=_number(position.fee_usd),
                status=position.status.value,
                pnl_usd=_number(position.pnl_usd),
                clv=_number(position.clv),
                closed_at=position.closed_at,
                benchmark_protocol=position.benchmark_protocol,
                sample_scope=_benchmark_sample_scope(position.benchmark_protocol),
                mark_price=_number(position_mark.mark_price) if position_mark else None,
                mark_at=position_mark.mark_at if position_mark else None,
                mark_age_seconds=(position_mark.mark_age_seconds if position_mark else None),
                mark_stale=position_mark.mark_stale if position_mark else False,
                unrealized_pnl_usd=(
                    _number(position_mark.unrealized_pnl_usd) if position_mark else None
                ),
                liquidation_price=(
                    _number(position_mark.liquidation_price) if position_mark else None
                ),
                liquidation_fee_usd=(
                    _number(position_mark.liquidation_fee_usd) if position_mark else None
                ),
                liquidation_pnl_usd=(
                    _number(position_mark.liquidation_pnl_usd) if position_mark else None
                ),
                pnl_change_1m_usd=(
                    _number(position_mark.pnl_change_1m_usd) if position_mark else None
                ),
                pnl_change_5m_usd=(
                    _number(position_mark.pnl_change_5m_usd) if position_mark else None
                ),
                pnl_change_60m_usd=(
                    _number(position_mark.pnl_change_60m_usd) if position_mark else None
                ),
            )
        )
    return PositionsResponse(generated_at=now, items=items)


@router.get("/market-timeseries", response_model=MarketPriceSeriesResponse)
async def market_timeseries(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    limit: Annotated[int, Query(ge=1, le=500)] = 240,
) -> MarketPriceSeriesResponse:
    async with _sessions(request)() as session:
        exists = await session.scalar(select(Market.id).where(Market.id == market_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="market not found")
        rows = (
            (
                await session.execute(
                    select(MarketPrice)
                    .where(MarketPrice.market_id == market_id)
                    .order_by(MarketPrice.ts.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return MarketPriceSeriesResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        benchmark_protocol=LEGACY_MARKET_PROTOCOL,
        sample_scope=_HISTORICAL_SAMPLE_SCOPE,
        points=[
            MarketPricePoint(
                t=row.ts,
                midpoint=float(row.midpoint),
                best_bid=float(row.best_bid),
                best_ask=float(row.best_ask),
                bid_depth_usd=float(row.bid_depth_usd),
                ask_depth_usd=float(row.ask_depth_usd),
            )
            for row in reversed(rows)
        ],
    )


@router.get("/probability-timeseries", response_model=ProbabilitySeriesResponse)
async def probability_timeseries(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    benchmark_protocol: Annotated[
        str,
        Query(pattern="^(clob-dual-v1|legacy-synthetic-yes-v0)$"),
    ] = CLOB_DUAL_PROTOCOL,
    limit: Annotated[int, Query(ge=1, le=500)] = 240,
) -> ProbabilitySeriesResponse:
    async with _sessions(request)() as session:
        exists = await session.scalar(select(Market.id).where(Market.id == market_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="market not found")
        model_rows = (
            (
                await session.execute(
                    select(ModelProb)
                    .where(
                        ModelProb.market_id == market_id,
                        ModelProb.market_anchor_protocol == benchmark_protocol,
                    )
                    .order_by(ModelProb.ts.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        signal_rows = (
            (
                await session.execute(
                    select(Signal)
                    .where(
                        Signal.market_id == market_id,
                        Signal.benchmark_protocol == benchmark_protocol,
                    )
                    .order_by(Signal.ts.desc())
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

    points = [
        ProbabilityPoint(
            t=row.ts,
            source="model",
            model_probability=float(row.prob),
            market_price=None,
            edge=None,
            sigma=float(row.sigma),
            horizon_h=float(row.horizon_h),
            confidence=None,
            status=None,
            benchmark_protocol=row.market_anchor_protocol,
            sample_scope=_benchmark_sample_scope(row.market_anchor_protocol),
        )
        for row in model_rows
    ]
    points.extend(
        ProbabilityPoint(
            t=row.ts,
            source="signal",
            model_probability=float(row.model_prob),
            market_price=float(row.market_price),
            edge=float(row.edge),
            sigma=None,
            horizon_h=None,
            confidence=row.confidence.value,
            status=row.status.value,
            benchmark_protocol=row.benchmark_protocol,
            sample_scope=_benchmark_sample_scope(row.benchmark_protocol),
        )
        for row in signal_rows
    )
    points.sort(key=lambda point: (point.t, point.source))
    return ProbabilitySeriesResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        benchmark_protocol=benchmark_protocol,
        sample_scope=_benchmark_sample_scope(benchmark_protocol),
        points=points[-limit:],
    )


@router.get("/orderbook", response_model=OrderBookResponse)
async def orderbook(
    request: Request,
    market_id: Annotated[int, Query(ge=1)],
    side: Annotated[str, Query(pattern="^(yes|no)$")] = "yes",
) -> OrderBookResponse:
    settings = cast(Settings, request.app.state.settings)
    async with _sessions(request)() as session:
        market = await session.scalar(select(Market).where(Market.id == market_id))
    if market is None:
        raise HTTPException(status_code=404, detail="market not found")

    token_id = market.yes_token_id if side == "yes" else market.no_token_id
    if not token_id:
        return _empty_orderbook(
            market_id=market_id,
            side=side,
            token_id=None,
            error=f"market has no {side.upper()} token id",
        )

    try:
        async with ClobClient(
            books_url=settings.clob_books_url,
            batch_size=settings.clob_batch_size,
            max_response_bytes=settings.clob_response_max_bytes,
        ) as client:
            books = await client.fetch_books([token_id])
        if not books:
            return _empty_orderbook(
                market_id=market_id,
                side=side,
                token_id=token_id,
                error="CLOB returned no book for token",
            )
        book = books[0]
        bids = _book_levels(book.get("bids"), reverse=True)
        asks = _book_levels(book.get("asks"), reverse=False)
        if not bids or not asks:
            return _empty_orderbook(
                market_id=market_id,
                side=side,
                token_id=token_id,
                error="book has no two-sided liquidity",
            )
        best_bid = bids[0].price
        best_ask = asks[0].price
        midpoint = (best_bid + best_ask) / 2
    except Exception as exc:  # noqa: BLE001 - external CLOB snapshot must fail soft.
        return _empty_orderbook(
            market_id=market_id,
            side=side,
            token_id=token_id,
            error=f"CLOB unavailable: {exc}",
        )

    return OrderBookResponse(
        generated_at=datetime.now(UTC),
        market_id=market_id,
        side=side,
        token_id=token_id,
        source_status="ok",
        error=None,
        midpoint=midpoint,
        best_bid=best_bid,
        best_ask=best_ask,
        bids=bids,
        asks=asks,
    )


@router.get("/calibration", response_model=CalibrationsResponse)
async def calibrations(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
) -> CalibrationsResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Calibration, Station.icao)
                .join(Station, Station.id == Calibration.station_id)
                .order_by(Station.icao, Calibration.model, Calibration.horizon_bucket)
                .limit(limit)
            )
        ).all()
    return CalibrationsResponse(
        generated_at=datetime.now(UTC),
        items=[
            CalibrationItem(
                station_icao=icao,
                model=calibration.model,
                horizon_bucket=calibration.horizon_bucket,
                kind=calibration.kind.value,
                bias_c=float(calibration.bias_c),
                sigma_c=float(calibration.sigma_c),
                sample_count=calibration.sample_n,
                brier=_number(calibration.brier),
                updated_at=calibration.updated_at,
            )
            for calibration, icao in rows
        ],
    )


@router.get("/stations", response_model=StationLifecycleResponse)
async def station_lifecycle(request: Request) -> StationLifecycleResponse:
    """Station research pool, readiness and trading lifecycle."""
    settings = _settings(request)
    generated_at = datetime.now(UTC)
    async with _sessions(request)() as session:
        stations = (
            (await session.execute(select(Station).order_by(Station.stage, Station.city)))
            .scalars()
            .all()
        )
        calibration_rows = (
            await session.execute(
                select(
                    Calibration.station_id,
                    func.count(),
                    func.min(Calibration.sample_n),
                ).group_by(Calibration.station_id)
            )
        ).all()
        market_rows = (
            await session.execute(
                select(Market.station_id, func.count())
                .where(Market.status == MarketStatus.ACTIVE)
                .group_by(Market.station_id)
            )
        ).all()
        position_rows = (
            await session.execute(
                select(Market.station_id, func.count(Position.id))
                .join(Position, Position.market_id == Market.id)
                .where(
                    Position.mode == ExecutionMode.PAPER,
                    Position.status == PositionStatus.CLOSED,
                )
                .group_by(Market.station_id)
            )
        ).all()
        observation_rows = (
            await session.execute(
                select(Observation.station_id, func.max(Observation.obs_time)).group_by(
                    Observation.station_id
                )
            )
        ).all()
        reliability_by_station = await latest_station_reliability_by_station(
            session,
            [station.id for station in stations],
        )

    calibration_by_station = {
        station_id: (int(row_count), int(minimum))
        for station_id, row_count, minimum in calibration_rows
        if minimum is not None
    }
    markets_by_station = {station_id: int(count) for station_id, count in market_rows}
    positions_by_station = {station_id: int(count) for station_id, count in position_rows}
    observations_by_station: dict[int, datetime] = {
        int(station_id): observed_at for station_id, observed_at in observation_rows
    }
    items = []
    reliability_policy_hash = station_reliability_policy_hash(settings)
    for station in stations:
        row_count, minimum = calibration_by_station.get(station.id, (0, 0))
        latest_observation = observations_by_station.get(station.id)
        observation_fresh = bool(
            latest_observation is not None
            and generated_at - latest_observation
            <= timedelta(minutes=settings.station_observation_stale_minutes)
            and latest_observation.astimezone(ZoneInfo(station.tz)).date()
            == generated_at.astimezone(ZoneInfo(station.tz)).date()
        )
        active_markets = markets_by_station.get(station.id, 0)
        reliability = reliability_by_station.get(station.id)
        reliability_gate_reasons = (
            ("station_reliability_evidence_invalid",)
            if reliability is not None and not station_reliability_record_is_valid(reliability)
            else station_signal_gate_reasons(
                reliability,
                market_date=(
                    reliability.observation_local_date
                    if reliability is not None
                    else generated_at.astimezone(ZoneInfo(station.tz)).date()
                ),
                now=generated_at,
                expected_policy_hash=reliability_policy_hash,
                max_age=timedelta(minutes=settings.station_reliability_snapshot_max_age_minutes),
                observation_stale_after=timedelta(
                    minutes=settings.station_observation_stale_minutes
                ),
                station_timezone=station.tz,
            )
        )
        items.append(
            StationLifecycleItem(
                icao=station.icao,
                name=station.name,
                city=station.city,
                stage=station.stage.value,
                active=station.active,
                iem_network=station.iem_network,
                calibration_rows=row_count,
                min_calibration_samples=minimum if row_count else None,
                active_markets=active_markets,
                closed_paper_positions=positions_by_station.get(station.id, 0),
                latest_observation_at=latest_observation,
                data_ready_for_paper=bool(
                    station.active
                    and station.iem_network
                    and row_count
                    and minimum >= settings.min_traded_calibration_samples
                    and observation_fresh
                    and active_markets
                    and not reliability_gate_reasons
                ),
                reliability_status=(
                    reliability.status.value if reliability is not None else "missing"
                ),
                reliability_score=(
                    _number(reliability.reliability_score) if reliability is not None else None
                ),
                reliability_lower_bound=(
                    _number(reliability.reliability_lower_bound)
                    if reliability is not None
                    else None
                ),
                reliability_samples=(
                    reliability.comparable_units if reliability is not None else 0
                ),
                reliability_conflicts=(
                    reliability.conflict_units if reliability is not None else 0
                ),
                reliability_boundary_samples=(
                    reliability.boundary_units if reliability is not None else 0
                ),
                reliability_unavailable_samples=(
                    reliability.unavailable_units if reliability is not None else 0
                ),
                reliability_evaluated_at=(
                    reliability.evaluated_at if reliability is not None else None
                ),
                reliability_reasons=(
                    list(reliability.reasons) if reliability is not None else []
                ),
                latest_conflict_at=(
                    reliability.latest_conflict_at if reliability is not None else None
                ),
                latest_conflict_date=(
                    reliability.latest_conflict_date if reliability is not None else None
                ),
                latest_conflict_kind=(
                    reliability.latest_conflict_kind.value
                    if reliability is not None and reliability.latest_conflict_kind is not None
                    else None
                ),
                resolution_cooldown_until=(
                    reliability.resolution_cooldown_until if reliability is not None else None
                ),
                observation_cooldown_until=(
                    reliability.observation_cooldown_until if reliability is not None else None
                ),
                reliability_policy_hash=(
                    reliability.policy_hash if reliability is not None else None
                ),
                reliability_source_hash=(
                    reliability.source_hash if reliability is not None else None
                ),
                reliability_record_hash=(
                    reliability.record_hash if reliability is not None else None
                ),
                selection_evaluated_for_date=generated_at.astimezone(
                    ZoneInfo(station.tz)
                ).date(),
                selection_blocked=bool(
                    station.active
                    and station.stage in {StationStage.PAPER, StationStage.APPROVED}
                    and reliability_gate_reasons
                ),
                selection_reasons=(
                    list(reliability_gate_reasons)
                    if station.active
                    and station.stage in {StationStage.PAPER, StationStage.APPROVED}
                    else []
                ),
            )
        )
    return StationLifecycleResponse(
        generated_at=generated_at,
        paper_stations=sum(
            item.active and item.stage == StationStage.PAPER.value for item in items
        ),
        shadow_stations=sum(
            item.active and item.stage == StationStage.SHADOW.value for item in items
        ),
        approved_stations=sum(
            item.active and item.stage == StationStage.APPROVED.value for item in items
        ),
        inactive_stations=sum(not item.active for item in items),
        items=items,
    )


def _command_item(row: Any) -> CommandItem:
    return CommandItem(
        id=row.id,
        request_id=row.request_id,
        command=row.command,
        payload=row.payload,
        actor=row.actor,
        reason=row.reason,
        status=row.status,
        detail=row.detail,
        created_at=row.created_at,
        executed_at=row.executed_at,
    )


def _alert_external_state(row: Any, activated_at: datetime | None, now: datetime) -> str:
    if row.dead_lettered_at is not None:
        return "dead_letter"
    if row.sent_at is not None:
        return "sent"
    if activated_at is None or row.created_at < activated_at:
        return "local_only"
    if row.lease_until is not None and row.lease_until > now:
        return "in_flight"
    if row.attempts > 0:
        return "retry_scheduled" if row.next_attempt_at is not None else "pending"
    return "pending"


@router.get("/alerts", response_model=AlertsResponse)
async def alerts(
    request: Request,
    state: Annotated[
        str,
        Query(pattern="^(unacknowledged|all)$"),
    ] = "unacknowledged",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AlertsResponse:
    """Durable local alert inbox; Telegram activation is optional."""

    async with _sessions(request)() as session:
        generated_at = cast(
            datetime,
            await session.scalar(select(func.clock_timestamp())),
        )
        summary = await notification_delivery_summary(session)
        rows = await list_delivery_alerts(
            session,
            unacknowledged_only=state == "unacknowledged",
            limit=limit,
        )
    oldest_age = (
        max(0, int((generated_at - summary.oldest_unacknowledged_at).total_seconds()))
        if summary.oldest_unacknowledged_at is not None
        else None
    )
    return AlertsResponse(
        generated_at=generated_at,
        query_state=state,
        total_count=summary.total_count,
        unacknowledged_count=summary.unacknowledged_count,
        oldest_unacknowledged_at=summary.oldest_unacknowledged_at,
        oldest_unacknowledged_age_seconds=oldest_age,
        channel=summary.channel,
        channel_state=summary.state,
        activated_at=summary.activated_at,
        heartbeat_at=summary.heartbeat_at,
        heartbeat_stale=summary.heartbeat_stale,
        last_success_at=summary.last_success_at,
        last_failure_at=summary.last_failure_at,
        last_error=summary.last_error,
        pending_after_activation=summary.pending_after_activation,
        oldest_pending_after_activation_at=summary.oldest_pending_after_activation_at,
        backlog_overdue=summary.backlog_overdue,
        dead_letter_count=summary.dead_letter_count,
        items=[
            AlertItem(
                id=row.id,
                event_key=row.event_key,
                category=row.event_key.split(":", 1)[0],
                payload=row.payload,
                created_at=row.created_at,
                acknowledged_at=row.acknowledged_at,
                sent_at=row.sent_at,
                attempts=row.attempts,
                last_attempt_at=row.last_attempt_at,
                next_attempt_at=row.next_attempt_at,
                lease_until=row.lease_until,
                dead_lettered_at=row.dead_lettered_at,
                error=row.error,
                external_state=_alert_external_state(
                    row,
                    summary.activated_at,
                    generated_at,
                ),
            )
            for row in rows
        ],
    )


@router.post(
    "/alerts/ack-through",
    response_model=AlertAcknowledgementResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def acknowledge_alerts_through(
    request: Request,
    body: AlertAckThroughRequest,
) -> AlertAcknowledgementResponse:
    async with _sessions(request).begin() as session:
        acknowledged = await acknowledge_deliveries_through(
            session,
            body.through_id,
        )
    return AlertAcknowledgementResponse(
        acknowledged=acknowledged,
        through_id=body.through_id,
    )


@router.post(
    "/alerts/{delivery_id}/ack",
    response_model=AlertAcknowledgementResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def acknowledge_alert(
    request: Request,
    delivery_id: int,
) -> AlertAcknowledgementResponse:
    if delivery_id < 1:
        raise HTTPException(status_code=422, detail="delivery id must be positive")
    async with _sessions(request).begin() as session:
        acknowledged = await acknowledge_delivery(session, delivery_id)
    if not acknowledged:
        raise HTTPException(status_code=404, detail="alert not found")
    return AlertAcknowledgementResponse(acknowledged=1)


@router.post(
    "/commands",
    response_model=CommandAccepted,
    status_code=202,
    dependencies=[Depends(require_configured_operator_key)],
)
async def submit_command(request: Request, body: CommandRequest) -> CommandAccepted:
    error = validate_command(body.command, body.payload)
    if error is not None:
        raise HTTPException(status_code=422, detail=error)
    async with _sessions(request).begin() as session:
        row, created = await enqueue_command(
            session,
            request_id=body.request_id,
            command=body.command,
            payload=body.payload,
            actor=body.actor,
            reason=body.reason,
        )
        item = _command_item(row)
    return CommandAccepted(created=created, item=item)


@router.get("/commands", response_model=CommandsResponse)
async def commands(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CommandsResponse:
    async with _sessions(request)() as session:
        rows = await recent_commands(session, limit=limit)
    return CommandsResponse(
        generated_at=datetime.now(UTC),
        items=[_command_item(row) for row in rows],
    )


def _official_resolution_http_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        (
            AuthorizedArtifactError,
            ContractBindingError,
            LicensePolicyError,
            OfficialResolutionIntegrityError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (ExtractionError, ResponseEnvelopeError, OfficialResolutionError)):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="official-resolution workflow failed")


async def _active_resolution_contract(
    session: AsyncSession,
    *,
    contract_id: int,
    rules_hash: str,
) -> ResolutionContract:
    contract = await session.scalar(
        select(ResolutionContract).where(
            ResolutionContract.id == contract_id,
            ResolutionContract.rules_hash == rules_hash,
            ResolutionContract.retired_at.is_(None),
        )
    )
    if contract is None:
        raise HTTPException(
            status_code=404,
            detail="active resolution contract with this rules hash was not found",
        )
    return contract


@router.post(
    "/official-resolution-sources/license-policy-hash",
    response_model=OfficialResolutionLicensePolicyResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def official_resolution_license_policy_hash(
    request: Request,
    body: OfficialResolutionLicenseDraftRequest,
) -> OfficialResolutionLicensePolicyResponse:
    async with _sessions(request)() as session:
        contract = await _active_resolution_contract(
            session,
            contract_id=body.contract_id,
            rules_hash=body.rules_hash,
        )
    try:
        policy = _official_license_draft(body, contract)
        sealed = seal_license_policy(policy)
    except Exception as exc:
        raise _official_resolution_http_error(exc) from exc
    return OfficialResolutionLicensePolicyResponse(
        contract_id=contract.id,
        license_protocol=OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
        policy_sha256=sealed.policy_sha256,
        parser_config_sha256=sealed.parser_config_sha256,
        canonical_policy=license_policy_payload(sealed),
    )


@router.post(
    "/official-resolution-sources/license-attestations",
    response_model=OfficialResolutionLicenseAttestationResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def official_resolution_license_attestation(
    request: Request,
    body: OfficialResolutionLicenseAttestationRequest,
) -> OfficialResolutionLicenseAttestationResponse:
    try:
        async with _sessions(request).begin() as session:
            contract = await _active_resolution_contract(
                session,
                contract_id=body.contract_id,
                rules_hash=body.rules_hash,
            )
            draft = _official_license_draft(body, contract)
            expected_hash = license_policy_sha256(draft)
            if body.approved_policy_sha256 != expected_hash:
                raise LicensePolicyError(
                    "approved policy hash differs from the current canonical draft"
                )
            policy = replace(draft, policy_sha256=body.approved_policy_sha256)
            row = await register_license_attestation(
                session,
                LicenseAttestationInput(
                    contract_id=contract.id,
                    license_policy=policy,
                    reviewer=body.reviewer,
                    reviewed_at=body.reviewed_at,
                    rationale=body.rationale,
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise _official_resolution_http_error(exc) from exc
    return OfficialResolutionLicenseAttestationResponse(
        id=row.id,
        contract_id=row.contract_id,
        license_id=row.attestation_version,
        policy_sha256=row.policy_hash,
        parser_config_sha256=row.parser_config_hash,
        record_hash=row.record_hash,
        attested_at=row.attested_at,
        expires_at=row.expires_at,
    )


@router.post(
    "/official-resolution-sources/import",
    response_model=OfficialResolutionImportResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def import_official_resolution_source(
    request: Request,
    body: OfficialResolutionImportRequest,
) -> OfficialResolutionImportResponse:
    settings = _settings(request)
    now = datetime.now(UTC)
    try:
        async with _sessions(request).begin() as session:
            contract = await _active_resolution_contract(
                session,
                contract_id=body.contract_id,
                rules_hash=body.rules_hash,
            )
            station = await session.scalar(
                select(Station).where(Station.icao == contract.station_code)
            )
            market = await session.scalar(
                select(Market)
                .where(Market.event_id == contract.event_id)
                .order_by(Market.id)
                .limit(1)
            )
            attestation = await session.scalar(
                select(OfficialResolutionLicenseAttestation)
                .where(
                    OfficialResolutionLicenseAttestation.contract_id == contract.id,
                    OfficialResolutionLicenseAttestation.license_status == "approved",
                    OfficialResolutionLicenseAttestation.valid_from <= now,
                    OfficialResolutionLicenseAttestation.expires_at > now,
                )
                .order_by(
                    OfficialResolutionLicenseAttestation.attested_at.desc(),
                    OfficialResolutionLicenseAttestation.id.desc(),
                )
                .limit(1)
            )
            if station is None or market is None:
                raise OfficialResolutionIntegrityError(
                    "contract station or market scope is unavailable"
                )
            if attestation is None:
                raise LicensePolicyError(
                    "no active contract-scoped license attestation is available"
                )
            artifact = read_authorized_artifact(
                settings.official_resolution_artifact_root,
                body.relative_artifact_path,
                expected_source_family=contract.source_family,
                expected_contract_id=contract.id,
                max_bytes=settings.official_resolution_artifact_max_bytes,
            )
            policy = _official_policy_from_attestation(attestation, contract)
            adapter = _official_adapter(policy)
            official_contract = _official_contract(contract)
            source_response = OfficialSourceResponse(
                acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
                request_url=None,
                requested_at=now,
                received_at=now,
                request_method="IMPORT",
                status_code=None,
                media_type="application/json",
                body=artifact.body,
                body_sha256=artifact.sha256,
                tls_verified=None,
            )
            record = adapter.extract(
                contract=official_contract,
                target_date=market.market_date,
                response=source_response,
                license_policy=policy,
                approved_policy_sha256=attestation.policy_hash,
                evaluated_at=now,
            )
            result = await persist_official_resolution_evidence(
                session,
                license_attestation_id=attestation.id,
                contract_id=contract.id,
                station_id=station.id,
                response=source_response,
                record=record,
                acquisition=OfficialSourceAcquisitionInput(
                    acquisition_protocol=AUTHORIZED_ARTIFACT_ACQUISITION,
                    request_method="IMPORT",
                    request_manifest={
                        "relative_path": artifact.relative_path,
                        "actor": body.actor,
                        "reason": body.reason,
                    },
                    response_headers={},
                ),
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise _official_resolution_http_error(exc) from exc
    return OfficialResolutionImportResponse(
        contract_id=contract.id,
        license_attestation_id=attestation.id,
        response_id=result.response.id,
        exact_actual_id=result.exact_actual.id,
        settlement_actual_id=result.settlement_actual.id,
        event_id=contract.event_id,
        station_icao=station.icao,
        market_date=result.exact_actual.market_date,
        kind=result.exact_actual.kind.value,
        source_token=record.source_token,
        source_value=float(result.exact_actual.source_value),
        source_unit=result.exact_actual.source_unit,
        value_c=float(result.exact_actual.value_c),
        response_sha256=result.response.response_sha256,
        exact_record_hash=result.exact_actual.record_hash,
    )


@router.get(
    "/official-resolution-sources",
    response_model=OfficialResolutionSourcesResponse,
)
async def official_resolution_sources(request: Request) -> OfficialResolutionSourcesResponse:
    async with _sessions(request)() as session:
        summary = await official_resolution_evidence_summary(session)
    integrity_ok = (
        summary.source_responses == summary.exact_actuals == summary.projected_settlement_actuals
    )
    if not integrity_ok:
        state = "integrity_error"
    elif summary.licensed_contracts == 0:
        state = "disabled_external_authorization"
    elif summary.exact_actuals == 0:
        state = "awaiting_official_evidence"
    else:
        state = "collecting"
    coverage = (
        summary.exact_actuals / summary.active_contracts if summary.active_contracts else None
    )
    return OfficialResolutionSourcesResponse(
        generated_at=datetime.now(UTC),
        state=state,
        source_integrity="valid" if integrity_ok else "invalid",
        artifact_import_enabled=(_settings(request).official_resolution_artifact_root is not None),
        opened_at=summary.runtime_opened_at,
        definition_version=summary.definition_version,
        definition_hash=summary.definition_hash,
        license_protocol=summary.license_protocol,
        response_protocol=summary.response_protocol,
        extraction_protocol=summary.extraction_protocol,
        active_contracts=summary.active_contracts,
        licensed_contracts=summary.licensed_contracts,
        expired_contracts=summary.expired_contracts,
        blocked_contracts=max(0, summary.active_contracts - summary.licensed_contracts),
        license_attestations=summary.license_attestations,
        source_responses=summary.source_responses,
        exact_actuals=summary.exact_actuals,
        projected_settlement_actuals=summary.projected_settlement_actuals,
        exact_coverage=coverage,
        raw_response_bytes=summary.raw_response_bytes,
        latest_response_at=summary.latest_response_at,
        latest_exact_actual_at=summary.latest_exact_actual_at,
    )


@router.get(
    "/official-resolution-sources/contracts",
    response_model=OfficialResolutionContractsResponse,
)
async def official_resolution_source_contracts(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
    contract_id: Annotated[int | None, Query(gt=0)] = None,
) -> OfficialResolutionContractsResponse:
    async with _sessions(request)() as session:
        rows = await list_official_resolution_contract_evidence(
            session,
            limit=limit,
            offset=offset,
            contract_id=contract_id,
        )
    return OfficialResolutionContractsResponse(
        generated_at=datetime.now(UTC),
        limit=limit,
        offset=offset,
        items=[
            OfficialResolutionContractEvidenceItem(
                **{
                    **asdict(row),
                    "source_value": _number(row.source_value),
                    "value_c": _number(row.value_c),
                }
            )
            for row in rows
        ],
    )


@router.post(
    "/settlement-actuals",
    deprecated=True,
    dependencies=[Depends(require_configured_operator_key)],
)
async def submit_settlement_actual() -> None:
    raise HTTPException(
        status_code=410,
        detail=(
            "direct settlement values are retired by official-resolution-evidence-v1; "
            "submit a licensed hash-addressed source artifact instead"
        ),
    )


@router.get(
    "/resolution-reconciliation",
    response_model=ResolutionReconciliationResponse,
)
async def resolution_reconciliation(
    request: Request,
    classification: Annotated[ResolutionReconciliationClassification | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> ResolutionReconciliationResponse:
    generated_at = datetime.now(UTC)
    async with _sessions(request)() as session:
        summary = await resolution_reconciliation_summary(
            session,
            now=generated_at,
        )
        count_rows = (
            await session.execute(
                select(
                    ResolutionReconciliation.classification,
                    func.count(ResolutionReconciliation.id),
                ).group_by(ResolutionReconciliation.classification)
            )
        ).all()
        statement = (
            select(ResolutionReconciliation, Station.icao)
            .outerjoin(Station, Station.id == ResolutionReconciliation.station_id)
            .order_by(
                ResolutionReconciliation.checked_at.desc(),
                ResolutionReconciliation.market_id,
            )
            .limit(limit)
        )
        if classification is not None:
            statement = statement.where(ResolutionReconciliation.classification == classification)
        rows = (await session.execute(statement)).all()
    counts = {str(getattr(item, "value", item)): int(count) for item, count in count_rows}
    return ResolutionReconciliationResponse(
        generated_at=generated_at,
        total=summary.total,
        metar_available=summary.metar_available,
        metar_consistent=summary.metar_consistent,
        exact_available=summary.exact_available,
        exact_source_hash_available=summary.exact_source_hash_available,
        exact_coverage=summary.exact_coverage,
        latest_checked_at=summary.latest_checked_at,
        oldest_checked_at=summary.oldest_checked_at,
        stale_records=summary.stale_records,
        fresh=summary.fresh,
        stale_after_seconds=summary.stale_after_seconds,
        classification_counts=counts,
        items=[
            ResolutionReconciliationItem(
                market_id=row.market_id,
                event_id=row.event_id,
                station_icao=station_icao,
                market_date=row.market_date,
                kind=row.kind.value,
                classification=row.classification.value,
                resolved_yes=row.resolved_yes,
                metar_temp_c=_number(row.metar_temp_c),
                metar_implied_yes=row.metar_implied_yes,
                exact_temp_c=_number(row.exact_temp_c),
                exact_implied_yes=row.exact_implied_yes,
                boundary_margin_c=_number(row.boundary_margin_c),
                exact_truth_gap_c=_number(row.exact_truth_gap_c),
                resolution_event_id=row.resolution_event_id,
                settlement_actual_id=row.settlement_actual_id,
                resolution_vector_hash=row.resolution_vector_hash,
                exact_source_hash=row.exact_source_hash,
                checked_at=row.checked_at,
                record_hash=row.record_hash,
            )
            for row, station_icao in rows
        ],
    )


@router.get("/settlement-cases", response_model=SettlementCasesResponse)
async def settlement_cases(
    request: Request,
    classification: Annotated[SettlementCaseClassification | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> SettlementCasesResponse:
    generated_at = datetime.now(UTC)
    async with _sessions(request)() as session:
        summary = await settlement_cases_summary(
            session,
            now=generated_at,
        )
        count_rows = (
            await session.execute(
                select(
                    SettlementCase.classification,
                    func.count(SettlementCase.id),
                ).group_by(SettlementCase.classification)
            )
        ).all()
        statement = select(SettlementCase).order_by(
            SettlementCase.last_checked_at.desc(), SettlementCase.event_id
        )
        if classification is not None:
            statement = statement.where(SettlementCase.classification == classification)
        rows = list((await session.scalars(statement.limit(limit))).all())
    counts = {str(getattr(item, "value", item)): int(count) for item, count in count_rows}
    return SettlementCasesResponse(
        generated_at=generated_at,
        total=summary.total,
        open_total=summary.open_total,
        oldest_open_at=summary.oldest_open_at,
        latest_checked_at=summary.latest_checked_at,
        stale_open_cases=summary.stale_open_cases,
        fresh=summary.fresh,
        stale_after_seconds=summary.stale_after_seconds,
        classification_counts=counts,
        items=[
            SettlementCaseItem(
                event_id=row.event_id,
                classification=row.classification.value,
                previous_classification=(
                    row.previous_classification.value
                    if row.previous_classification is not None
                    else None
                ),
                reason_code=row.reason_code,
                event_resolves_at=row.event_resolves_at,
                age_seconds=max(0, int((generated_at - row.event_resolves_at).total_seconds())),
                first_detected_at=row.first_detected_at,
                last_checked_at=row.last_checked_at,
                classification_changed_at=row.classification_changed_at,
                attempt_count=row.attempt_count,
                transition_count=row.transition_count,
                source_url=row.source_url,
                source_response_sha256=row.source_response_sha256,
                resolution_event_id=row.resolution_event_id,
                resolution_vector_hash=row.resolution_vector_hash,
                last_error=row.last_error,
                details=row.details,
                resolved_at=row.resolved_at,
                record_hash=row.record_hash,
            )
            for row in rows
        ],
    )


@router.get("/storage-capacity", response_model=StorageCapacityResponse)
async def storage_capacity(request: Request) -> StorageCapacityResponse:
    generated_at = datetime.now(UTC)
    settings = _settings(request)
    async with _sessions(request)() as session:
        samples = await latest_capacity_samples(session, limit=1)
        sample = samples[0] if samples else None
        baseline = (
            await capacity_baseline(
                session,
                at_or_before=(
                    sample.captured_at
                    - timedelta(hours=settings.clob_ledger_capacity_min_window_hours)
                ),
            )
            if sample is not None
            else None
        )
    retention_rule = (
        "Immutable evidence is retained until an attested archive and "
        "referential-integrity proof exist."
    )
    if sample is None:
        return StorageCapacityResponse(
            generated_at=generated_at,
            sample_at=None,
            sample_age_seconds=None,
            sample_fresh=False,
            severity="blocking",
            reason="capacity_sample_missing",
            measurement_complete=False,
            observed_hours=None,
            database_bytes=None,
            ledger_bytes=None,
            clob_book_batches_bytes=None,
            clob_market_snapshots_bytes=None,
            clob_batch_count=None,
            clob_snapshot_count=None,
            free_disk_bytes=None,
            minimum_free_disk_bytes=settings.clob_ledger_capacity_min_free_bytes,
            growth_bytes_per_day=None,
            projected_ledger_bytes=None,
            days_to_budget=None,
            budget_bytes=settings.clob_ledger_capacity_budget_bytes,
            policy_version=CAPACITY_POLICY_VERSION,
            automatic_deletion=False,
            retention_rule=retention_rule,
        )

    assessment = assess_clob_ledger_capacity(
        sample_capacity_point(sample),
        sample_capacity_point(baseline) if baseline is not None else None,
        budget_bytes=settings.clob_ledger_capacity_budget_bytes,
        warning_fraction=settings.clob_ledger_capacity_warning_fraction,
        projection_days=settings.clob_ledger_capacity_projection_days,
        minimum_window_hours=settings.clob_ledger_capacity_min_window_hours,
        minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
    )
    sample_ttl = timedelta(minutes=settings.clob_ledger_capacity_sample_ttl_minutes)
    capacity_gate = assess_capacity_collection_gate(
        sample_capacity_point(sample),
        now=generated_at,
        budget_bytes=settings.clob_ledger_capacity_budget_bytes,
        minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
        sample_ttl=sample_ttl,
    )
    sample_age_seconds = capacity_gate.sample_age_seconds
    sample_fresh = (
        sample_age_seconds is not None and 0 <= sample_age_seconds <= sample_ttl.total_seconds()
    )
    return StorageCapacityResponse(
        generated_at=generated_at,
        sample_at=sample.captured_at,
        sample_age_seconds=sample_age_seconds,
        sample_fresh=sample_fresh,
        severity=assessment.severity if capacity_gate.allowed else "blocking",
        reason=assessment.reason if capacity_gate.allowed else capacity_gate.reason,
        measurement_complete=(
            capacity_gate.allowed and assessment.growth_bytes_per_day is not None
        ),
        observed_hours=assessment.observed_hours,
        database_bytes=sample.database_bytes,
        ledger_bytes=assessment.ledger_bytes,
        clob_book_batches_bytes=sample.clob_book_batches_bytes,
        clob_market_snapshots_bytes=sample.clob_market_snapshots_bytes,
        clob_batch_count=sample.clob_batch_count,
        clob_snapshot_count=sample.clob_snapshot_count,
        free_disk_bytes=sample.free_disk_bytes,
        minimum_free_disk_bytes=settings.clob_ledger_capacity_min_free_bytes,
        growth_bytes_per_day=assessment.growth_bytes_per_day,
        projected_ledger_bytes=assessment.projected_ledger_bytes,
        days_to_budget=assessment.days_to_budget,
        budget_bytes=assessment.budget_bytes,
        policy_version=assessment.policy_version,
        automatic_deletion=False,
        retention_rule=retention_rule,
    )


@router.get("/forecast-provenance", response_model=ForecastProvenanceResponse)
async def forecast_provenance(request: Request) -> ForecastProvenanceResponse:
    generated_at = datetime.now(UTC)
    async with _sessions(request)() as session:
        summary = await forecast_provenance_summary(session)
        active_probability_components = await latest_active_market_probability_components(session)
    complete_active_probabilities = sum(
        model_input_provenance_is_complete(components)
        for components in active_probability_components
    )
    latest_age_seconds = (
        max(0.0, (generated_at - summary.latest_first_seen_at).total_seconds())
        if summary.latest_first_seen_at is not None
        else None
    )
    state = (
        "not_collecting"
        if summary.ingests == 0
        else "stale"
        if latest_age_seconds is not None and latest_age_seconds > 7200
        else "healthy"
    )
    return ForecastProvenanceResponse(
        generated_at=generated_at,
        state=state,
        source_integrity=(
            "Immutable exact HTTP response bytes plus deterministic parsed-value "
            "manifest; prospective zero-backfill cohort."
        ),
        opened_at=summary.opened_at,
        definition_version=summary.definition_version,
        definition_hash=summary.definition_hash,
        ingests=summary.ingests,
        stations=summary.stations,
        responses=summary.responses,
        raw_bytes=summary.raw_bytes,
        verified_upstream_inits=summary.verified_upstream_inits,
        single_run_ingests=summary.single_run_ingests,
        pipeline_fingerprints=summary.pipeline_fingerprints,
        materialized_forecasts=summary.materialized_forecasts,
        materialized_trajectories=summary.materialized_trajectories,
        latest_first_seen_at=summary.latest_first_seen_at,
        latest_age_seconds=latest_age_seconds,
        latest_active_probabilities=len(active_probability_components),
        complete_active_probabilities=complete_active_probabilities,
        average_http_latency_ms=summary.average_http_latency_ms,
        p95_http_latency_ms=summary.p95_http_latency_ms,
        average_ingestion_lag_ms=summary.average_ingestion_lag_ms,
        p95_ingestion_lag_ms=summary.p95_ingestion_lag_ms,
        average_verified_upstream_latency_minutes=(
            summary.average_verified_upstream_latency_minutes
        ),
        p95_verified_upstream_latency_minutes=(summary.p95_verified_upstream_latency_minutes),
        collectors=[
            ForecastProvenanceCollectorItem(
                collector=item.collector,
                request_model=item.request_model,
                ingests=item.ingests,
                stations=item.stations,
                responses=item.responses,
                raw_bytes=item.raw_bytes,
                verified_upstream_inits=item.verified_upstream_inits,
                latest_first_seen_at=item.latest_first_seen_at,
            )
            for item in summary.collectors
        ],
    )


def _execution_cost_metrics_item(
    metrics: ExecutionCostMetrics,
) -> ExecutionCostMetricsItem:
    return ExecutionCostMetricsItem(
        eligible_orders=metrics.eligible_orders,
        eligible_shares=float(metrics.eligible_shares),
        gross_model_edge=_number(metrics.gross_model_edge),
        latency_movement=_number(metrics.latency_movement),
        taker_spread=_number(metrics.taker_spread),
        taker_slippage=_number(metrics.taker_slippage),
        maker_improvement=_number(metrics.maker_improvement),
        fee_drag=_number(metrics.fee_drag),
        realized_net_edge=_number(metrics.realized_net_edge),
        adverse_selection_5m=_number(metrics.adverse_selection_5m),
        adverse_selection_30m=_number(metrics.adverse_selection_30m),
    )


def _execution_cost_coverage(known: int, total: int) -> ExecutionCostCoverageItem:
    return ExecutionCostCoverageItem(
        known=known,
        total=total,
        ratio=(known / total if total else None),
    )


def _execution_cost_state(summary: ExecutionCostSummary) -> str:
    if summary.attempts == 0:
        return "collecting"
    if summary.pending_attempts > 0:
        return "pending"
    if summary.sealed_attempts > 0 and summary.eligible_seals == 0:
        return "evidence_incomplete"
    if summary.ineligible_seals > 0:
        return "partial"
    return "healthy"


def _execution_cost_order_item(
    order: ExecutionCostOrderSummary,
) -> ExecutionCostOrderItem:
    return ExecutionCostOrderItem(
        order_id=order.order_id,
        intent_id=order.intent_id,
        signal_id=order.signal_id,
        market_id=order.market_id,
        station_icao=order.station_icao,
        market_date=order.market_date,
        kind=order.kind,
        confidence=order.confidence,
        horizon_h=_number(order.horizon_h),
        mode=order.mode.value,
        side=order.side,
        order_style=order.order_style,
        requested_price=float(order.requested_price),
        requested_notional_usd=float(order.requested_notional_usd),
        intent_at=order.intent_at,
        terminal_status=order.terminal_status,
        obligation_state=order.obligation_state,
        eligible=order.eligible,
        eligibility_reason=order.eligibility_reason,
        aggregate_role=order.aggregate_role,
        leg_count=order.leg_count,
        total_shares=_number(order.total_shares),
        vwap_fill_price=_number(order.vwap_fill_price),
        total_fee_usd=_number(order.total_fee_usd),
        gross_model_edge=_number(order.gross_model_edge),
        latency_movement=_number(order.latency_movement),
        taker_spread=_number(order.taker_spread),
        taker_slippage=_number(order.taker_slippage),
        maker_improvement=_number(order.maker_improvement),
        fee_drag=_number(order.fee_drag),
        realized_net_edge=_number(order.realized_net_edge),
        adverse_selection_5m=_number(order.adverse_selection_5m),
        adverse_selection_30m=_number(order.adverse_selection_30m),
        sealed_at=order.sealed_at,
    )


@router.get("/execution-cost-report", response_model=ExecutionCostReportResponse)
async def execution_cost_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")],
) -> ExecutionCostReportResponse:
    """Prospective execution-cost waterfall and evidence coverage.

    Paper and live evidence are never pooled. Unsupported fee, slippage, and
    markout values remain null instead of being represented as zero.
    """

    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        summary = await load_execution_cost_report(session, selected_mode)
    return ExecutionCostReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        state=_execution_cost_state(summary),
        source_integrity=(
            "Prospective immutable order-intent, fill-leg, fee, and post-fill "
            "CLOB evidence; zero backfill."
        ),
        opened_at=summary.opened_at,
        definition_version=summary.definition_version,
        definition_hash=summary.definition_hash,
        attempts=summary.attempts,
        filled_attempts=summary.filled_attempts,
        no_fill_attempts=summary.no_fill_attempts,
        pending_attempts=summary.pending_attempts,
        sealed_attempts=summary.sealed_attempts,
        eligible_seals=summary.eligible_seals,
        ineligible_seals=summary.ineligible_seals,
        maker_attempts=summary.maker_attempts,
        maker_fills=summary.maker_fills,
        maker_fill_rate=_number(summary.maker_fill_rate),
        maker_seals=summary.maker_seals,
        taker_seals=summary.taker_seals,
        mixed_seals=summary.mixed_seals,
        unknown_role_seals=summary.unknown_role_seals,
        expected_slippage_coverage=_execution_cost_coverage(
            summary.expected_slippage_known,
            summary.expected_slippage_total,
        ),
        exact_fee_coverage=_execution_cost_coverage(
            summary.exact_fee_known,
            summary.exact_fee_total,
        ),
        markout_5m_coverage=_execution_cost_coverage(
            summary.mark_5m_known,
            summary.mark_5m_total,
        ),
        markout_30m_coverage=_execution_cost_coverage(
            summary.mark_30m_known,
            summary.mark_30m_total,
        ),
        metrics=_execution_cost_metrics_item(summary.metrics),
        reasons=[
            ExecutionCostReasonItem(reason=item.reason, orders=item.orders)
            for item in summary.reasons
        ],
        slices=[
            ExecutionCostSliceItem(
                dimension=item.dimension,
                value=item.value,
                attempts=item.attempts,
                filled=item.filled,
                sealed=item.sealed,
                eligible=item.eligible,
                metrics=_execution_cost_metrics_item(item.metrics),
            )
            for item in summary.slices
        ],
    )


@router.get(
    "/execution-cost-report/orders",
    response_model=ExecutionCostOrdersResponse,
)
async def execution_cost_report_orders(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    eligible: Annotated[bool | None, Query()] = None,
    role: Annotated[
        str | None,
        Query(pattern="^(maker|taker|mixed|unknown)$"),
    ] = None,
    station: Annotated[str | None, Query(min_length=1, max_length=16)] = None,
    kind: Annotated[str | None, Query(min_length=1, max_length=32)] = None,
    horizon: Annotated[
        str | None,
        Query(pattern=r"^(0-6h|6-24h|24h\+|unknown)$"),
    ] = None,
    side: Annotated[str | None, Query(pattern="^(buy_yes|buy_no)$")] = None,
    reason: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
) -> ExecutionCostOrdersResponse:
    """Concrete immutable attempts behind the execution-quality summary."""

    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        page = await load_execution_cost_orders(
            session,
            mode=selected_mode,
            limit=limit,
            offset=offset,
            eligible=eligible,
            role=role,
            station=station,
            kind=kind,
            horizon=horizon,
            side=side,
            reason=reason,
        )
    return ExecutionCostOrdersResponse(
        generated_at=datetime.now(UTC),
        mode=page.mode.value,
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        items=[_execution_cost_order_item(item) for item in page.orders],
    )


_ACCURACY_OVERVIEW_LATEST_EXPERTS = (
    "censored_gaussian",
    "conformal_gaussian",
    "trajectory_nowcast",
    "hierarchical_emos",
    "regime_emos",
    "regularized_stack",
    "qrf",
    "analog",
    "mixture",
)
_ACCURACY_OVERVIEW_TIMEOUT_SECONDS = 2.75


async def _latest_accuracy_prediction_batches(
    session: AsyncSession,
) -> dict[str, list[AccuracyPrediction]]:
    """Load every expert's latest eligible timestamp in one bounded query."""

    latest_by_expert = (
        select(
            AccuracyPrediction.expert.label("expert"),
            func.max(AccuracyPrediction.ts).label("latest_ts"),
        )
        .where(
            AccuracyPrediction.expert.in_(_ACCURACY_OVERVIEW_LATEST_EXPERTS),
            AccuracyPrediction.evidence_eligible.is_(True),
        )
        .group_by(AccuracyPrediction.expert)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(AccuracyPrediction).join(
                    latest_by_expert,
                    and_(
                        AccuracyPrediction.expert == latest_by_expert.c.expert,
                        AccuracyPrediction.ts == latest_by_expert.c.latest_ts,
                    ),
                ).where(AccuracyPrediction.evidence_eligible.is_(True))
            )
        )
        .scalars()
        .all()
    )
    batches: dict[str, list[AccuracyPrediction]] = {
        expert: [] for expert in _ACCURACY_OVERVIEW_LATEST_EXPERTS
    }
    for row in rows:
        batches[row.expert].append(row)
    return batches


def _accuracy_overview_response_cache(
    request: Request,
) -> AccuracyOverviewResponseCache:
    cache = getattr(request.app.state, "accuracy_overview_response_cache", None)
    if not isinstance(cache, AccuracyOverviewResponseCache):
        cache = AccuracyOverviewResponseCache()
        request.app.state.accuracy_overview_response_cache = cache
    return cache


async def _uncached_accuracy_overview_response(
    request: Request,
) -> AccuracyOverviewResponse:
    now = datetime.now(UTC)
    settings = _settings(request)
    pipeline_fingerprint = probability_pipeline_fingerprint(settings)
    async with _sessions(request)() as session:
        promotion_manifest = await load_effective_accuracy_promotion_manifest(
            session,
            pipeline_fingerprint=pipeline_fingerprint,
            current_pipeline_base_cohort=probability_pipeline_base_cohort(
                settings,
                fingerprint=pipeline_fingerprint,
            ),
        )
        promotion_trials = list(
            (
                await session.execute(
                    select(AccuracyPromotionTrial).order_by(
                        AccuracyPromotionTrial.family_member_index
                    )
                )
            )
            .scalars()
            .all()
        )
        promotion_member_counts = {
            int(trial_id): int(count)
            for trial_id, count in (
                await session.execute(
                    select(
                        AccuracyPromotionEvidenceMember.trial_id,
                        func.count(AccuracyPromotionEvidenceMember.id),
                    ).group_by(AccuracyPromotionEvidenceMember.trial_id)
                )
            ).all()
        }
        promotion_looks = {
            row.trial_id: row
            for row in (
                (
                    await session.execute(
                        select(AccuracyPromotionLook).order_by(AccuracyPromotionLook.trial_id)
                    )
                )
                .scalars()
                .all()
            )
        }
        promotion_station_names = {
            int(station_id): str(icao)
            for station_id, icao in (await session.execute(select(Station.id, Station.icao))).all()
        }
        active_promotion_scopes = {
            (
                item.key.expert,
                item.key.legacy_expert_version,
                item.key.expert_version,
                item.key.experiment_fingerprint,
                item.key.station_id,
                item.key.horizon_bucket,
                item.key.kind,
            )
            for item in promotion_manifest.promotions
        }
        active_promotion_experts = {item.key.expert for item in promotion_manifest.promotions}
        contract_count, licensed_count, actual_count, qualified = (
            await session.execute(
                select(
                    select(func.count()).select_from(ResolutionContract).scalar_subquery(),
                    select(func.count())
                    .select_from(ResolutionContract)
                    .where(ResolutionContract.storage_permitted.is_(True))
                    .scalar_subquery(),
                    select(func.count()).select_from(SettlementActual).scalar_subquery(),
                    select(func.count())
                    .select_from(ModelSkillReport)
                    .where(
                        ModelSkillReport.eligible.is_(True),
                        ModelSkillReport.evaluated_at >= now - timedelta(days=2),
                    )
                    .scalar_subquery(),
                )
            )
        ).one()
        contract_count = int(contract_count or 0)
        licensed_count = int(licensed_count or 0)
        actual_count = int(actual_count or 0)
        qualified = int(qualified or 0)
        # Dashboard metrics are formal evidence, not a blend with legacy rows.
        # The database triggers make every joined component immutable; these
        # predicates mirror the predictive/provenance identity used by formal
        # accuracy jobs without transferring raw Gamma bodies on every refresh.
        attested_evaluations = (
            select(
                ForecastEvaluation.independent_sample_key.label("sample_key"),
                ForecastEvaluation.model_brier.label("model_brier"),
                ForecastEvaluation.market_brier.label("market_brier"),
            )
            .join(ModelProb, ForecastEvaluation.model_prob_id == ModelProb.id)
            .join(Market, ForecastEvaluation.market_id == Market.id)
            .join(
                GammaOutcomeEvent,
                ForecastEvaluation.resolution_event_id == GammaOutcomeEvent.id,
            )
            .where(
                ForecastEvaluation.market_benchmark_protocol == CLOB_DUAL_PROTOCOL,
                ForecastEvaluation.market_snapshot_id.is_not(None),
                ForecastEvaluation.market_snapshot_hash.is_not(None),
                ForecastEvaluation.market_brier.is_not(None),
                ForecastEvaluation.resolution_event_id.is_not(None),
                ForecastEvaluation.resolution_vector_hash == GammaOutcomeEvent.outcome_vector_hash,
                ForecastEvaluation.decision_at < GammaOutcomeEvent.first_outcome_at,
                ForecastEvaluation.market_id == ModelProb.market_id,
                ForecastEvaluation.decision_at == ModelProb.ts,
                ForecastEvaluation.model_probability == ModelProb.prob,
                Market.resolution_event_id == GammaOutcomeEvent.id,
                Market.event_id == GammaOutcomeEvent.event_id,
            )
            .subquery()
        )
        (
            evaluation_count,
            independent_count,
            mean_model_brier,
            mean_market_brier,
        ) = (
            await session.execute(
                select(
                    func.count(),
                    func.count(func.distinct(attested_evaluations.c.sample_key)),
                    func.avg(attested_evaluations.c.model_brier),
                    func.avg(attested_evaluations.c.market_brier),
                ).select_from(attested_evaluations)
            )
        ).one()
        evaluation_count = int(evaluation_count or 0)
        independent_count = int(independent_count or 0)
        recent_60_day_reports = list(
            (
                await session.execute(
                    select(ModelSkillReport).where(
                        ModelSkillReport.window_days == 60,
                        ModelSkillReport.evaluated_at >= now - timedelta(days=2)
                    )
                )
            )
            .scalars()
            .all()
        )
        settlement_calibrator = await session.scalar(
            select(SettlementProbabilityCalibrator).where(
                SettlementProbabilityCalibrator.scope_key == "global:all:all"
            )
        )
        latest_prediction_batches = await _latest_accuracy_prediction_batches(session)
        recent_censored_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "censored_gaussian"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_censored_predictions = [
            row for row in latest_prediction_batches["censored_gaussian"] if row.evidence_eligible
        ]
        censored_coverage = (
            len(latest_censored_predictions),
            max(
                (row.training_sample_n for row in latest_censored_predictions),
                default=0,
            ),
        )
        latest_censored_prediction = max(
            latest_censored_predictions,
            key=lambda row: row.id,
            default=None,
        )
        recent_weighted_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "historical_weighted"
            and row.details.get("expert_version") == WEIGHTED_EXPERT_VERSION
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        recent_weighted_shrunk_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "historical_weighted_shrunk"
            and row.details.get("expert_version") == WEIGHTED_SHRUNK_EXPERT_VERSION
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        weighted_prediction_rows = (
            (
                await session.execute(
                    select(AccuracyPrediction)
                    .join(Market, AccuracyPrediction.market_id == Market.id)
                    .where(
                        (
                            (AccuracyPrediction.expert == "historical_weighted")
                            & (AccuracyPrediction.expert_version == WEIGHTED_EXPERT_VERSION)
                        )
                        | (
                            (AccuracyPrediction.expert == "historical_weighted_shrunk")
                            & (AccuracyPrediction.expert_version == WEIGHTED_SHRUNK_EXPERT_VERSION)
                        ),
                        AccuracyPrediction.evidence_eligible.is_(True),
                        Market.status == MarketStatus.ACTIVE,
                        Market.resolves_at > now,
                    )
                    .distinct(
                        AccuracyPrediction.expert,
                        AccuracyPrediction.market_id,
                    )
                    .order_by(
                        AccuracyPrediction.expert,
                        AccuracyPrediction.market_id,
                        AccuracyPrediction.ts.desc(),
                    )
                )
            )
            .scalars()
            .all()
        )
        weighted_current_predictions = [
            row
            for row in weighted_prediction_rows
            if row.expert == "historical_weighted" and accuracy_prediction_record_is_valid(row)
        ]
        latest_weighted_prediction = max(
            weighted_current_predictions,
            key=lambda row: row.generated_at,
            default=None,
        )
        weighted_fingerprint = (
            latest_weighted_prediction.experiment_fingerprint
            if latest_weighted_prediction is not None
            else None
        )
        weighted_current_predictions = [
            row
            for row in weighted_current_predictions
            if row.experiment_fingerprint == weighted_fingerprint
        ]
        recent_weighted_reports = [
            row
            for row in recent_weighted_reports
            if weighted_fingerprint is not None
            and row.details.get("experiment_fingerprint") == weighted_fingerprint
        ]
        weighted_shrunk_current_predictions = [
            row
            for row in weighted_prediction_rows
            if row.expert == "historical_weighted_shrunk"
            and accuracy_prediction_record_is_valid(row)
        ]
        latest_weighted_shrunk_prediction = max(
            weighted_shrunk_current_predictions,
            key=lambda row: row.generated_at,
            default=None,
        )
        weighted_shrunk_fingerprint = (
            latest_weighted_shrunk_prediction.experiment_fingerprint
            if latest_weighted_shrunk_prediction is not None
            else None
        )
        weighted_shrunk_current_predictions = [
            row
            for row in weighted_shrunk_current_predictions
            if row.experiment_fingerprint == weighted_shrunk_fingerprint
        ]
        recent_weighted_shrunk_reports = [
            row
            for row in recent_weighted_shrunk_reports
            if weighted_shrunk_fingerprint is not None
            and row.details.get("experiment_fingerprint") == weighted_shrunk_fingerprint
        ]
        weighted_shrunk_legacy_markets = int(
            await session.scalar(
                select(func.count(func.distinct(AccuracyPrediction.market_id)))
                .select_from(AccuracyPrediction)
                .join(Market, AccuracyPrediction.market_id == Market.id)
                .where(
                    AccuracyPrediction.expert == "historical_weighted_shrunk",
                    AccuracyPrediction.expert_version != WEIGHTED_SHRUNK_EXPERT_VERSION,
                    Market.status == MarketStatus.ACTIVE,
                    Market.resolves_at > now,
                )
            )
            or 0
        )
        stored_trajectories, latest_trajectory_at = (
            await session.execute(
                select(
                    func.count(ForecastTrajectory.id),
                    func.max(ForecastTrajectory.available_at),
                )
            )
        ).one()
        stored_trajectories = int(stored_trajectories or 0)
        recent_trajectory_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "trajectory_ar1"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        recent_conformal_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "conformal_gaussian"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_conformal_prediction = max(
            (
                row
                for row in latest_prediction_batches["conformal_gaussian"]
                if row.evidence_eligible
            ),
            key=lambda row: row.id,
            default=None,
        )
        recent_nowcast_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "trajectory_nowcast"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_nowcast_predictions = [
            row for row in latest_prediction_batches["trajectory_nowcast"] if row.evidence_eligible
        ]
        current_nowcast_count = len(latest_nowcast_predictions)
        latest_nowcast_prediction = max(
            latest_nowcast_predictions,
            key=lambda row: row.id,
            default=None,
        )
        recent_hierarchical_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "hierarchical_emos"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_hierarchical_predictions = [
            row for row in latest_prediction_batches["hierarchical_emos"] if row.evidence_eligible
        ]
        current_hierarchical_count = len(latest_hierarchical_predictions)
        latest_hierarchical_prediction = max(
            latest_hierarchical_predictions,
            key=lambda row: row.id,
            default=None,
        )
        recent_regime_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "regime_emos"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_regime_predictions = latest_prediction_batches["regime_emos"]
        current_regime_count = sum(row.evidence_eligible for row in latest_regime_predictions)
        latest_regime_prediction = max(
            latest_regime_predictions,
            key=lambda row: abs(
                _json_number(row.distribution.get("strongest_adjustment_c")) or 0.0
            ),
            default=None,
        )
        recent_stack_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") == "regularized_stack"
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        active_stack_experts = sum(
            item.key.expert == "regularized_stack" for item in promotion_manifest.promotions
        )
        latest_stack_predictions = [
            row for row in latest_prediction_batches["regularized_stack"] if row.evidence_eligible
        ]
        stack_coverage = (
            len(latest_stack_predictions),
            max(
                (row.training_sample_n for row in latest_stack_predictions),
                default=0,
            ),
        )
        representative_stack_prediction = max(
            latest_stack_predictions,
            key=lambda row: row.training_sample_n,
            default=None,
        )
        nonlinear_expert_names = ("qrf", "analog", "mixture")
        recent_nonlinear_reports = [
            row
            for row in recent_60_day_reports
            if row.details.get("expert") in nonlinear_expert_names
            and row.details.get("evidence_protocol") == "sealed-v2"
        ]
        latest_nonlinear_at = max(
            (
                row.ts
                for expert in nonlinear_expert_names
                for row in latest_prediction_batches[expert]
                if row.evidence_eligible
            ),
            default=None,
        )
        nonlinear_coverage_rows = [
            (
                expert,
                len(rows),
                max((row.training_sample_n for row in rows), default=0),
            )
            for expert in nonlinear_expert_names
            if (
                rows := [
                    row
                    for row in latest_prediction_batches[expert]
                    if row.evidence_eligible and row.ts == latest_nonlinear_at
                ]
            )
        ]
        nonlinear_coverage = {
            expert: (int(count), int(training_sample_n or 0))
            for expert, count, training_sample_n in nonlinear_coverage_rows
        }
        model_rows = (
            (
                await session.execute(
                    select(ForecastModelRegistry).order_by(
                        ForecastModelRegistry.provider, ForecastModelRegistry.model
                    )
                )
            )
            .scalars()
            .all()
        )
        drift_page = (
            await session.execute(
                select(ModelDriftEvent, func.count().over())
                .where(ModelDriftEvent.detected_at >= now - timedelta(days=7))
                .order_by(ModelDriftEvent.detected_at.desc())
                .limit(50)
            )
        ).all()
        drift_rows = [row for row, _count in drift_page]
        recent_drift_count = int(drift_page[0][1]) if drift_page else 0
        active_regime_candidates = (
            (
                await session.execute(
                    select(ModelDriftEvent).where(
                        ModelDriftEvent.metric == "residual_bias_shift",
                        ModelDriftEvent.detected_at >= now - timedelta(days=2),
                    )
                )
            )
            .scalars()
            .all()
        )
        active_regime_by_scope: dict[tuple[int | None, str, str, str], ModelDriftEvent] = {}
        for row in active_regime_candidates:
            scope = (
                row.station_id,
                str(row.details.get("model", "")),
                str(row.details.get("horizon_bucket", "")),
                str(row.details.get("kind", "")),
            )
            previous = active_regime_by_scope.get(scope)
            if previous is None or row.detected_at > previous.detected_at:
                active_regime_by_scope[scope] = row
        active_regime_rows = list(active_regime_by_scope.values())
        latest_regime_drift = max(
            active_regime_rows,
            key=lambda row: row.detected_at,
            default=None,
        )
    regime_distribution: dict[str, Any] = (
        latest_regime_prediction.distribution
        if latest_regime_prediction is not None
        else latest_regime_drift.details
        if latest_regime_drift is not None
        else {}
    )
    regime_adjustment = regime_distribution.get("strongest_adjustment_c")
    if regime_adjustment is None:
        regime_adjustment = regime_distribution.get("adjustment_c")
    regime_model = regime_distribution.get("strongest_model")
    if regime_model is None:
        regime_model = regime_distribution.get("model")
    censored_distribution = (
        latest_censored_prediction.distribution if latest_censored_prediction is not None else {}
    )
    censored_report = max(
        recent_censored_reports,
        key=lambda row: row.sample_n,
        default=None,
    )
    weighted_distribution = (
        latest_weighted_prediction.distribution if latest_weighted_prediction is not None else {}
    )
    weighted_report = max(
        recent_weighted_reports,
        key=lambda row: row.sample_n,
        default=None,
    )
    weighted_shrunk_report = max(
        recent_weighted_shrunk_reports,
        key=lambda row: row.sample_n,
        default=None,
    )
    weighted_by_scope: dict[str, AccuracyPrediction] = {}
    for prediction in weighted_current_predictions:
        fit_scope = prediction.distribution.get("fit_scope")
        if isinstance(fit_scope, str):
            weighted_by_scope.setdefault(fit_scope, prediction)
    weight_samples: dict[str, list[float]] = {}
    for prediction in weighted_by_scope.values():
        values = prediction.distribution.get("weights")
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if isinstance(value, int | float):
                weight_samples.setdefault(str(name), []).append(float(value))
    weighted_weights = {
        name: sum(samples) / len(samples)
        for name, samples in sorted(weight_samples.items())
        if samples
    }
    promotion_trial_items: list[AccuracyPromotionTrialItem] = []
    for trial in promotion_trials:
        look = promotion_looks.get(trial.id)
        promotion_scope = (
            trial.expert,
            trial.legacy_expert_version,
            trial.expert_version,
            trial.experiment_fingerprint,
            trial.station_id,
            trial.horizon_bucket,
            trial.kind.value,
        )
        activated = promotion_scope in active_promotion_scopes
        stored_status = trial.status.value
        display_status = (
            "activated"
            if activated
            else "stale_activation"
            if stored_status == "activated"
            else stored_status
        )
        promotion_trial_items.append(
            AccuracyPromotionTrialItem(
                expert=trial.expert,
                station_id=trial.station_id,
                station_icao=promotion_station_names.get(
                    trial.station_id, f"station #{trial.station_id}"
                ),
                horizon_bucket=trial.horizon_bucket,
                kind=trial.kind.value,
                status=display_status,
                evidence_blocks=promotion_member_counts.get(trial.id, 0),
                target_blocks=trial.target_blocks,
                mean_delta_brier=(float(look.mean_delta_brier) if look is not None else None),
                wins=look.wins if look is not None else None,
                losses=look.losses if look is not None else None,
                ties=look.ties if look is not None else None,
                confidence_radius=(float(look.confidence_radius) if look is not None else None),
                mean_improvement_lcb=(
                    float(look.mean_improvement_lcb) if look is not None else None
                ),
                one_sided_p_bound=(float(look.one_sided_p_bound) if look is not None else None),
                allocated_alpha=float(trial.allocated_alpha),
                activated=activated,
            )
        )
    promotion_statuses = [item.status for item in promotion_trial_items]
    promotion_overview = AccuracyPromotionOverviewItem(
        protocol_version=(
            promotion_trials[0].protocol_version if promotion_trials else PROMOTION_PROTOCOL_VERSION
        ),
        registration_epoch=PROMOTION_REGISTRATION_EPOCH,
        test_method=(
            promotion_trials[0].test_method if promotion_trials else PROMOTION_TEST_METHOD
        ),
        family_alpha=(float(promotion_trials[0].family_alpha) if promotion_trials else 0.05),
        family_size=(promotion_trials[0].family_size if promotion_trials else 0),
        registered_trials=len(promotion_trials),
        collecting_trials=promotion_statuses.count("collecting"),
        passed_trials=sum(status in {"passed", "activated"} for status in promotion_statuses),
        failed_trials=promotion_statuses.count("failed"),
        activated_trials=sum(item.activated for item in promotion_trial_items),
        maximum_evidence_blocks=max(
            (item.evidence_blocks for item in promotion_trial_items), default=0
        ),
        target_blocks=(
            promotion_trials[0].target_blocks if promotion_trials else PROMOTION_TARGET_BLOCKS
        ),
        activation_manifest_hash=promotion_manifest.manifest_hash,
        trials=promotion_trial_items,
    )
    return AccuracyOverviewResponse(
        generated_at=now,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        resolution_contracts=contract_count,
        licensed_contracts=licensed_count,
        exact_settlement_actuals=actual_count,
        forecast_evaluations=evaluation_count,
        independent_samples=independent_count,
        mean_model_brier=_number(mean_model_brier),
        mean_market_brier=_number(mean_market_brier),
        qualified_skill_reports=qualified,
        open_drift_events=recent_drift_count,
        active_stack_experts=active_stack_experts,
        promotion=promotion_overview,
        settlement_calibrator=(
            SettlementCalibratorItem(
                scope_key=settlement_calibrator.scope_key,
                alpha=float(settlement_calibrator.alpha),
                beta=float(settlement_calibrator.beta),
                training_blocks=settlement_calibrator.training_blocks,
                training_buckets=settlement_calibrator.training_buckets,
                minimum_blocks=_settings(request).settlement_calibrator_min_blocks,
                baseline_brier=_number(settlement_calibrator.oof_baseline_brier),
                calibrated_brier=_number(settlement_calibrator.oof_calibrated_brier),
                baseline_ece=_number(settlement_calibrator.oof_baseline_ece),
                calibrated_ece=_number(settlement_calibrator.oof_calibrated_ece),
                delta_ci_low=_number(settlement_calibrator.delta_brier_ci_low),
                delta_ci_high=_number(settlement_calibrator.delta_brier_ci_high),
                eligible=settlement_calibrator.eligible,
                applied="settlement_beta" in active_promotion_experts,
                trained_through=settlement_calibrator.trained_through,
            )
            if settlement_calibrator is not None
            else None
        ),
        censored_challenger=CensoredChallengerItem(
            current_markets=int(censored_coverage[0] or 0),
            training_blocks=int(censored_coverage[1] or 0),
            bias_c=_json_number(censored_distribution.get("bias_c")),
            sigma_scale=_json_number(censored_distribution.get("sigma_scale")),
            historical_baseline_brier=_json_number(censored_distribution.get("oof_baseline_brier")),
            historical_calibrated_brier=_json_number(
                censored_distribution.get("oof_calibrated_brier")
            ),
            historical_ci_low=_json_number(censored_distribution.get("oof_delta_ci_low")),
            historical_ci_high=_json_number(censored_distribution.get("oof_delta_ci_high")),
            historical_oof_eligible=bool(
                censored_distribution.get("historical_oof_eligible", False)
            ),
            minimum_blocks=60,
            maximum_blocks=censored_report.sample_n if censored_report else 0,
            evaluated_slices=len(recent_censored_reports),
            eligible_slices=sum(row.eligible for row in recent_censored_reports),
            delta_brier=(_number(censored_report.delta_brier) if censored_report else None),
            ci_low=_number(censored_report.ci_low) if censored_report else None,
            ci_high=_number(censored_report.ci_high) if censored_report else None,
            applied=False,
        ),
        weighted_ensemble=WeightedEnsembleItem(
            current_markets=len(weighted_current_predictions),
            fitted_scopes=len(weighted_by_scope),
            minimum_training_sample_n=min(
                (row.training_sample_n for row in weighted_by_scope.values()),
                default=0,
            ),
            maximum_training_sample_n=max(
                (row.training_sample_n for row in weighted_by_scope.values()),
                default=0,
            ),
            historical_samples=int(
                _json_number(weighted_distribution.get("historical_oof_samples")) or 0
            ),
            historical_blocks=int(
                _json_number(weighted_distribution.get("historical_oof_blocks")) or 0
            ),
            historical_baseline_rmse_c=_json_number(
                weighted_distribution.get("historical_baseline_rmse_c")
            ),
            historical_candidate_rmse_c=_json_number(
                weighted_distribution.get("historical_candidate_rmse_c")
            ),
            historical_delta_mse=_json_number(weighted_distribution.get("historical_delta_mse")),
            historical_ci_low=_json_number(weighted_distribution.get("historical_delta_ci_low")),
            historical_ci_high=_json_number(weighted_distribution.get("historical_delta_ci_high")),
            historical_oof_eligible=bool(
                weighted_distribution.get("historical_oof_eligible", False)
            ),
            settlement_available_events=int(
                _json_number(weighted_distribution.get("settlement_available_events")) or 0
            ),
            settlement_fitted_events=int(
                _json_number(weighted_distribution.get("settlement_fitted_events")) or 0
            ),
            settlement_backtest_blocks=int(
                _json_number(weighted_distribution.get("settlement_backtest_blocks")) or 0
            ),
            settlement_backtest_market_rows=int(
                _json_number(weighted_distribution.get("settlement_backtest_market_rows")) or 0
            ),
            settlement_baseline_brier=_json_number(
                weighted_distribution.get("settlement_baseline_brier")
            ),
            settlement_candidate_brier=_json_number(
                weighted_distribution.get("settlement_candidate_brier")
            ),
            settlement_delta_brier=_json_number(
                weighted_distribution.get("settlement_delta_brier")
            ),
            settlement_ci_low=_json_number(weighted_distribution.get("settlement_delta_ci_low")),
            settlement_ci_high=_json_number(weighted_distribution.get("settlement_delta_ci_high")),
            settlement_aligned_eligible=bool(
                weighted_distribution.get("settlement_aligned_eligible", False)
            ),
            settlement_empirical_candidate_brier=_json_number(
                weighted_distribution.get("settlement_empirical_candidate_brier")
            ),
            settlement_empirical_delta_brier=_json_number(
                weighted_distribution.get("settlement_empirical_delta_brier")
            ),
            settlement_empirical_ci_low=_json_number(
                weighted_distribution.get("settlement_empirical_delta_ci_low")
            ),
            settlement_empirical_ci_high=_json_number(
                weighted_distribution.get("settlement_empirical_delta_ci_high")
            ),
            settlement_shrunk_empirical_weight=float(
                _json_number(weighted_distribution.get("settlement_shrunk_empirical_weight"))
                or 0.25
            ),
            settlement_shrunk_empirical_brier=_json_number(
                weighted_distribution.get("settlement_shrunk_empirical_brier")
            ),
            settlement_shrunk_empirical_delta_brier=_json_number(
                weighted_distribution.get("settlement_shrunk_empirical_delta_brier")
            ),
            settlement_shrunk_empirical_ci_low=_json_number(
                weighted_distribution.get("settlement_shrunk_empirical_delta_ci_low")
            ),
            settlement_shrunk_empirical_ci_high=_json_number(
                weighted_distribution.get("settlement_shrunk_empirical_delta_ci_high")
            ),
            settlement_shrunk_empirical_eligible=bool(
                weighted_distribution.get("settlement_shrunk_empirical_eligible", False)
            ),
            minimum_settlement_backtest_blocks=int(
                _json_number(weighted_distribution.get("minimum_settlement_backtest_blocks")) or 30
            ),
            shrunk_current_markets=len(weighted_shrunk_current_predictions),
            shrunk_legacy_markets=weighted_shrunk_legacy_markets,
            shrunk_expert_version=WEIGHTED_SHRUNK_EXPERT_VERSION,
            shrunk_evidence_protocol="sealed-v2",
            shrunk_latest_generated_at=max(
                (row.generated_at for row in weighted_shrunk_current_predictions),
                default=None,
            ),
            shrunk_minimum_blocks=60,
            shrunk_maximum_blocks=(
                weighted_shrunk_report.sample_n if weighted_shrunk_report else 0
            ),
            shrunk_evaluated_slices=len(recent_weighted_shrunk_reports),
            shrunk_eligible_slices=sum(row.eligible for row in recent_weighted_shrunk_reports),
            shrunk_delta_brier=(
                _number(weighted_shrunk_report.delta_brier) if weighted_shrunk_report else None
            ),
            shrunk_ci_low=(
                _number(weighted_shrunk_report.ci_low) if weighted_shrunk_report else None
            ),
            shrunk_ci_high=(
                _number(weighted_shrunk_report.ci_high) if weighted_shrunk_report else None
            ),
            weights=weighted_weights,
            minimum_blocks=60,
            maximum_blocks=weighted_report.sample_n if weighted_report else 0,
            evaluated_slices=len(recent_weighted_reports),
            eligible_slices=sum(row.eligible for row in recent_weighted_reports),
            delta_brier=(_number(weighted_report.delta_brier) if weighted_report else None),
            ci_low=_number(weighted_report.ci_low) if weighted_report else None,
            ci_high=_number(weighted_report.ci_high) if weighted_report else None,
            applied=False,
        ),
        trajectory_challenger=TrajectoryChallengerItem(
            stored_trajectories=stored_trajectories,
            latest_available_at=latest_trajectory_at,
            minimum_blocks=_settings(request).accuracy_trajectory_min_blocks,
            maximum_blocks=max((row.sample_n for row in recent_trajectory_reports), default=0),
            evaluated_slices=len(recent_trajectory_reports),
            eligible_slices=sum(row.eligible for row in recent_trajectory_reports),
            brier=(
                _number(max(recent_trajectory_reports, key=lambda row: row.sample_n).brier)
                if recent_trajectory_reports
                else None
            ),
            delta_brier=(
                _number(max(recent_trajectory_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_trajectory_reports
                else None
            ),
            ci_low=(
                _number(max(recent_trajectory_reports, key=lambda row: row.sample_n).ci_low)
                if recent_trajectory_reports
                else None
            ),
            ci_high=(
                _number(max(recent_trajectory_reports, key=lambda row: row.sample_n).ci_high)
                if recent_trajectory_reports
                else None
            ),
            applied="trajectory_ar1" in active_promotion_experts,
        ),
        conformal_guardrail=ConformalGuardrailItem(
            residual_samples=(
                latest_conformal_prediction.training_sample_n
                if latest_conformal_prediction is not None
                else 0
            ),
            scale=(
                float(latest_conformal_prediction.distribution["scale"])
                if latest_conformal_prediction is not None
                and isinstance(latest_conformal_prediction.distribution.get("scale"), int | float)
                else None
            ),
            target_coverage=_settings(request).accuracy_conformal_coverage,
            empirical_coverage_before=(
                float(latest_conformal_prediction.distribution["coverage_before"])
                if latest_conformal_prediction is not None
                and isinstance(
                    latest_conformal_prediction.distribution.get("coverage_before"), int | float
                )
                else None
            ),
            empirical_coverage_after=(
                float(latest_conformal_prediction.distribution["coverage_after"])
                if latest_conformal_prediction is not None
                and isinstance(
                    latest_conformal_prediction.distribution.get("coverage_after"), int | float
                )
                else None
            ),
            minimum_blocks=_settings(request).accuracy_conformal_min_blocks,
            maximum_blocks=max((row.sample_n for row in recent_conformal_reports), default=0),
            evaluated_slices=len(recent_conformal_reports),
            eligible_slices=sum(row.eligible for row in recent_conformal_reports),
            delta_brier=(
                _number(max(recent_conformal_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_conformal_reports
                else None
            ),
            ci_low=(
                _number(max(recent_conformal_reports, key=lambda row: row.sample_n).ci_low)
                if recent_conformal_reports
                else None
            ),
            ci_high=(
                _number(max(recent_conformal_reports, key=lambda row: row.sample_n).ci_high)
                if recent_conformal_reports
                else None
            ),
            applied="conformal_gaussian" in active_promotion_experts,
        ),
        nowcast_challenger=NowcastChallengerItem(
            current_markets=current_nowcast_count,
            recent_bias_c=(
                float(latest_nowcast_prediction.distribution["recent_bias_c"])
                if latest_nowcast_prediction is not None
                and isinstance(
                    latest_nowcast_prediction.distribution.get("recent_bias_c"), int | float
                )
                else None
            ),
            extreme_shift_c=(
                float(latest_nowcast_prediction.distribution["extreme_shift_c"])
                if latest_nowcast_prediction is not None
                and isinstance(
                    latest_nowcast_prediction.distribution.get("extreme_shift_c"), int | float
                )
                else None
            ),
            matched_hours=(
                int(latest_nowcast_prediction.distribution["matched_hours"])
                if latest_nowcast_prediction is not None
                and isinstance(latest_nowcast_prediction.distribution.get("matched_hours"), int)
                else 0
            ),
            latest_observed_hour=(
                str(latest_nowcast_prediction.distribution["latest_observed_hour"])
                if latest_nowcast_prediction is not None
                and isinstance(
                    latest_nowcast_prediction.distribution.get("latest_observed_hour"), str
                )
                else None
            ),
            minimum_blocks=_settings(request).accuracy_nowcast_min_blocks,
            maximum_blocks=max((row.sample_n for row in recent_nowcast_reports), default=0),
            evaluated_slices=len(recent_nowcast_reports),
            eligible_slices=sum(row.eligible for row in recent_nowcast_reports),
            delta_brier=(
                _number(max(recent_nowcast_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_nowcast_reports
                else None
            ),
            ci_low=(
                _number(max(recent_nowcast_reports, key=lambda row: row.sample_n).ci_low)
                if recent_nowcast_reports
                else None
            ),
            ci_high=(
                _number(max(recent_nowcast_reports, key=lambda row: row.sample_n).ci_high)
                if recent_nowcast_reports
                else None
            ),
            applied="trajectory_nowcast" in active_promotion_experts,
        ),
        hierarchical_challenger=HierarchicalChallengerItem(
            current_markets=current_hierarchical_count,
            local_weight=(
                float(latest_hierarchical_prediction.distribution["local_weight"])
                if latest_hierarchical_prediction is not None
                and isinstance(
                    latest_hierarchical_prediction.distribution.get("local_weight"), int | float
                )
                else None
            ),
            local_sample_n=(
                int(latest_hierarchical_prediction.distribution["local_sample_n"])
                if latest_hierarchical_prediction is not None
                and isinstance(
                    latest_hierarchical_prediction.distribution.get("local_sample_n"), int
                )
                else 0
            ),
            peer_sample_n=(
                int(latest_hierarchical_prediction.distribution["peer_sample_n"])
                if latest_hierarchical_prediction is not None
                and isinstance(
                    latest_hierarchical_prediction.distribution.get("peer_sample_n"), int
                )
                else 0
            ),
            peer_stations=(
                int(latest_hierarchical_prediction.distribution["peer_stations"])
                if latest_hierarchical_prediction is not None
                and isinstance(
                    latest_hierarchical_prediction.distribution.get("peer_stations"), int
                )
                else 0
            ),
            minimum_blocks=_settings(request).accuracy_hierarchical_min_blocks,
            maximum_blocks=max((row.sample_n for row in recent_hierarchical_reports), default=0),
            evaluated_slices=len(recent_hierarchical_reports),
            eligible_slices=sum(row.eligible for row in recent_hierarchical_reports),
            delta_brier=(
                _number(max(recent_hierarchical_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_hierarchical_reports
                else None
            ),
            ci_low=(
                _number(max(recent_hierarchical_reports, key=lambda row: row.sample_n).ci_low)
                if recent_hierarchical_reports
                else None
            ),
            ci_high=(
                _number(max(recent_hierarchical_reports, key=lambda row: row.sample_n).ci_high)
                if recent_hierarchical_reports
                else None
            ),
            applied="hierarchical_emos" in active_promotion_experts,
        ),
        regime_challenger=RegimeChallengerItem(
            active_shifts=len(active_regime_rows),
            critical_shifts=sum(row.severity == "critical" for row in active_regime_rows),
            current_markets=current_regime_count,
            strongest_adjustment_c=(
                float(regime_adjustment) if isinstance(regime_adjustment, int | float) else None
            ),
            strongest_model=(str(regime_model) if regime_model is not None else None),
            model_version=(
                str(regime_distribution["model_version"])
                if regime_distribution.get("model_version") is not None
                else None
            ),
            boundary_kind=(
                str(regime_distribution["boundary_kind"])
                if regime_distribution.get("boundary_kind") is not None
                else None
            ),
            boundary_date=(
                str(regime_distribution["boundary_date"])
                if regime_distribution.get("boundary_date") is not None
                else None
            ),
            latest_detected_at=(
                latest_regime_drift.detected_at if latest_regime_drift is not None else None
            ),
            minimum_blocks=_settings(request).accuracy_regime_min_blocks,
            maximum_blocks=max((row.sample_n for row in recent_regime_reports), default=0),
            evaluated_slices=len(recent_regime_reports),
            eligible_slices=sum(row.eligible for row in recent_regime_reports),
            delta_brier=(
                _number(max(recent_regime_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_regime_reports
                else None
            ),
            ci_low=(
                _number(max(recent_regime_reports, key=lambda row: row.sample_n).ci_low)
                if recent_regime_reports
                else None
            ),
            ci_high=(
                _number(max(recent_regime_reports, key=lambda row: row.sample_n).ci_high)
                if recent_regime_reports
                else None
            ),
            applied="regime_emos" in active_promotion_experts,
        ),
        stack_challenger=StackChallengerItem(
            minimum_training_samples=_settings(request).accuracy_stack_min_samples,
            minimum_blocks=_settings(request).accuracy_stack_min_blocks,
            current_markets=int(stack_coverage[0] or 0),
            training_sample_n=int(stack_coverage[1] or 0),
            fit_source=(
                str(representative_stack_prediction.distribution.get("fit_source"))
                if representative_stack_prediction is not None
                and representative_stack_prediction.distribution.get("fit_source") is not None
                else None
            ),
            local_sample_n=(
                int(representative_stack_prediction.distribution.get("local_sample_n") or 0)
                if representative_stack_prediction is not None
                else 0
            ),
            peer_sample_n=(
                int(representative_stack_prediction.distribution.get("peer_sample_n") or 0)
                if representative_stack_prediction is not None
                else 0
            ),
            peer_stations=(
                int(representative_stack_prediction.distribution.get("peer_stations") or 0)
                if representative_stack_prediction is not None
                else 0
            ),
            maximum_blocks=(
                max(recent_stack_reports, key=lambda row: row.sample_n).sample_n
                if recent_stack_reports
                else 0
            ),
            evaluated_slices=len(recent_stack_reports),
            eligible_slices=sum(row.eligible for row in recent_stack_reports),
            delta_brier=(
                _number(max(recent_stack_reports, key=lambda row: row.sample_n).delta_brier)
                if recent_stack_reports
                else None
            ),
            ci_low=(
                _number(max(recent_stack_reports, key=lambda row: row.sample_n).ci_low)
                if recent_stack_reports
                else None
            ),
            ci_high=(
                _number(max(recent_stack_reports, key=lambda row: row.sample_n).ci_high)
                if recent_stack_reports
                else None
            ),
            applied="regularized_stack" in active_promotion_experts,
        ),
        nonlinear_challengers=NonlinearChallengerItem(
            minimum_training_samples=_settings(request).accuracy_challenger_min_samples,
            minimum_blocks=_settings(request).accuracy_nonlinear_min_blocks,
            experts=[
                _nonlinear_expert_summary(
                    expert,
                    [
                        row
                        for row in recent_nonlinear_reports
                        if row.details.get("expert") == expert
                    ],
                    nonlinear_coverage.get(expert, (0, 0)),
                    applied=expert in active_promotion_experts,
                )
                for expert in nonlinear_expert_names
            ],
        ),
        models=[
            AccuracyModelItem(
                provider=row.provider,
                model=row.model,
                model_version=row.model_version,
                region=row.region,
                enabled=row.enabled,
                shadow_only=row.shadow_only,
                expected_latency_minutes=row.expected_latency_minutes,
                source_url=row.source_url,
                license_status=row.license_status,
            )
            for row in model_rows
        ],
        drift=[
            AccuracyDriftItem(
                station_id=row.station_id,
                detected_at=row.detected_at,
                window_days=row.window_days,
                metric=row.metric,
                baseline_value=float(row.baseline_value),
                recent_value=float(row.recent_value),
                delta=float(row.delta),
                severity=row.severity,
            )
            for row in drift_rows
        ],
    )


@router.get("/accuracy-overview", response_model=AccuracyOverviewResponse)
async def accuracy_overview(request: Request) -> AccuracyOverviewResponse:
    cache = _accuracy_overview_response_cache(request)

    async def bounded_factory() -> AccuracyOverviewResponse:
        try:
            async with asyncio.timeout(_ACCURACY_OVERVIEW_TIMEOUT_SECONDS):
                return await _uncached_accuracy_overview_response(request)
        except TimeoutError as error:
            raise HTTPException(
                status_code=503,
                detail="accuracy overview exceeded its query-time budget",
            ) from error

    return await cache.get_or_create(bounded_factory)


async def _position_stats(
    session: AsyncSession,
    mode: ExecutionMode,
    *,
    benchmark_protocol: str | None = None,
) -> list[PositionStat]:
    statement = select(
        Position.status,
        Position.pnl_usd,
        Position.clv,
        Position.size_usd,
        Position.fee_usd,
        Position.size_shares,
        Position.avg_price,
    ).where(Position.mode == mode)
    if benchmark_protocol is not None:
        statement = statement.where(Position.benchmark_protocol == benchmark_protocol)
    rows = (await session.execute(statement)).all()
    return [
        PositionStat(
            closed=status is PositionStatus.CLOSED,
            pnl_usd=_number(pnl),
            clv=_number(clv),
            cost_usd=float(cost),
            fee_usd=_number(fee),
            size_shares=_number(shares),
            avg_price=_number(price),
        )
        for status, pnl, clv, cost, fee, shares, price in rows
    ]


async def _current_prospective_dataset(
    session: AsyncSession,
    mode: ExecutionMode,
) -> tuple[str | None, ProspectiveValidationDataset]:
    """Load the one formal sample selected by the active evidence lanes.

    A report labelled ``prospective`` must never query positions/orders on its
    own: doing so would silently retain evidence after a reviewed lane fork.
    This helper keeps every operator surface on the immutable enrollment /
    outcome projection selected by the current predictive and execution lane
    identities.
    """

    active_epoch = await current_probability_pipeline_release_epoch(session)
    cohort = active_epoch.cohort if active_epoch is not None else None
    dataset = await load_prospective_validation_dataset(
        session,
        mode=mode,
        pipeline_cohort=cohort or "__no_current_pipeline__",
    )
    return cohort, dataset


def _summary_response(
    perf: LivePerformance,
    benchmark_perf: LivePerformance,
    trades: list[TradeOutcome],
) -> LiveSummaryResponse:
    briers = brier_comparison(trades)
    return LiveSummaryResponse(
        generated_at=datetime.now(UTC),
        performance_sample_scope=_ALL_PROTOCOL_HISTORY_SCOPE,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        benchmark_sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        benchmark_positions=benchmark_perf.positions,
        benchmark_roi=benchmark_perf.roi,
        benchmark_roi_ci_low=benchmark_perf.roi_ci_low,
        benchmark_roi_ci_high=benchmark_perf.roi_ci_high,
        positions=perf.positions,
        closed=perf.closed,
        open=perf.open,
        wins=perf.wins,
        win_rate=perf.win_rate,
        total_pnl_usd=perf.total_pnl_usd,
        robust_pnl_usd=perf.robust_pnl_usd,
        total_cost_usd=perf.total_cost_usd,
        total_fees_usd=perf.total_fees_usd,
        roi=perf.roi,
        robust_roi=perf.robust_roi,
        avg_clv=benchmark_perf.avg_clv,
        clv_beat_rate=benchmark_perf.clv_beat_rate,
        skill_pnl_usd=benchmark_perf.skill_pnl_usd,
        luck_pnl_usd=benchmark_perf.luck_pnl_usd,
        decomposed=benchmark_perf.decomposed,
        roi_ci_low=perf.roi_ci_low,
        roi_ci_high=perf.roi_ci_high,
        skill_pnl_ci_low=benchmark_perf.skill_pnl_ci_low,
        skill_pnl_ci_high=benchmark_perf.skill_pnl_ci_high,
        statistically_positive=benchmark_perf.statistically_positive,
        brier_model=briers[0] if briers else None,
        brier_market=briers[1] if briers else None,
        n_resolved_trades=len(trades),
    )


@router.get("/live-summary", response_model=LiveSummaryResponse)
async def live_summary(request: Request) -> LiveSummaryResponse:
    async with _sessions(request)() as session:
        perf = summarize_live_performance(await _position_stats(session, ExecutionMode.LIVE))
        _, dataset = await _current_prospective_dataset(session, ExecutionMode.LIVE)
        benchmark_perf = summarize_live_performance(prospective_position_stats(dataset.trades))
        trades = prospective_trade_outcomes(dataset.trades)
    return _summary_response(perf, benchmark_perf, trades)


@router.get("/performance-summary", response_model=LiveSummaryResponse)
async def performance_summary(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> LiveSummaryResponse:
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        perf = summarize_live_performance(await _position_stats(session, selected_mode))
        _, dataset = await _current_prospective_dataset(session, selected_mode)
        benchmark_perf = summarize_live_performance(prospective_position_stats(dataset.trades))
        trades = prospective_trade_outcomes(dataset.trades)
    return _summary_response(perf, benchmark_perf, trades)


def _evidence_epoch_item(
    epoch: ProbabilityPipelineReleaseEpoch,
) -> EvidenceReleaseEpochItem:
    return EvidenceReleaseEpochItem(
        id=epoch.id,
        opened_at=epoch.opened_at,
        pipeline_version=epoch.pipeline_version,
        material_fingerprint=epoch.material_fingerprint,
        activation_manifest_hash=epoch.activation_manifest_hash,
        cohort=epoch.cohort,
        image_digest=epoch.image_digest,
        plan_id=epoch.plan_id,
        previous_epoch_id=epoch.previous_epoch_id,
        approval_reason=epoch.approval_reason,
        plan_hash=epoch.plan_hash,
        record_hash=epoch.record_hash,
    )


def _evidence_lane_item(lane: EvidenceContinuityLane) -> EvidenceContinuityLaneItem:
    return EvidenceContinuityLaneItem(**asdict(lane))


def _evidence_control_lane_item(
    lane: EvidenceContinuityLaneRecord,
) -> EvidenceContinuityControlLaneItem:
    return EvidenceContinuityControlLaneItem(
        id=lane.id,
        epoch_id=lane.epoch_id,
        lane_name=lane.lane_name,
        action=lane.action,
        identity_hash=lane.identity_hash,
        parent_lane_id=lane.parent_lane_id,
        boundary_at=lane.boundary_at,
        manifest=lane.manifest,
        rationale=lane.rationale,
        record_hash=lane.record_hash,
    )


def _evidence_due_counts_item(counts: EvidenceDueCounts) -> EvidenceDueCountsItem:
    return EvidenceDueCountsItem(
        **asdict(counts),
        waiting_for_due=counts.waiting_for_due,
        due_pending=counts.due_pending,
        overdue=counts.overdue,
        classified=counts.classified,
        total=counts.total,
    )


def _evidence_collection_window_item(
    window: EvidenceCollectionWindow,
) -> EvidenceCollectionWindowItem:
    return EvidenceCollectionWindowItem(
        started_at=window.started_at,
        ended_at=window.ended_at,
        latest_day_started_at=window.latest_day_started_at,
        latest_day_ended_at=window.latest_day_ended_at,
        latest_day_grace_until=window.latest_day_grace_until,
        latest_complete_day_ended_at=window.latest_complete_day_ended_at,
        grace_seconds=window.grace_seconds,
        candidate_full_days=window.candidate_full_days,
        evaluated_days=window.evaluated_days,
        maintenance_excluded_days=window.maintenance_excluded_days,
        complete_days=window.complete_days,
        waiting_for_due_days=window.waiting_for_due_days,
        due_pending_days=window.due_pending_days,
        collecting_on_schedule_days=window.collecting_on_schedule_days,
        overdue_days=window.overdue_days,
        stalled_days=window.stalled_days,
        required_scheduler_jobs=list(window.required_scheduler_jobs),
        missing_scheduler_jobs=list(window.missing_scheduler_jobs),
        due_counts=_evidence_due_counts_item(window.due_counts),
    )


def _evidence_manifest_pipeline_identity(
    candidate: EvidenceReleaseManifest,
) -> tuple[str, str, str]:
    pipeline = candidate.manifest.get("pipeline")
    if not isinstance(pipeline, dict):
        raise EvidenceReleaseManifestError("release manifest pipeline identity is missing")
    fingerprint = pipeline.get("material_fingerprint")
    activation_manifest_hash = pipeline.get("activation_manifest_hash")
    cohort = pipeline.get("cohort")
    if not all(
        isinstance(value, str) and value
        for value in (fingerprint, activation_manifest_hash, cohort)
    ):
        raise EvidenceReleaseManifestError("release manifest pipeline identity is invalid")
    return cast(str, fingerprint), cast(str, activation_manifest_hash), cast(str, cohort)


async def _evidence_release_plan(
    session: AsyncSession,
    settings: Settings,
    *,
    reviewed_by: str,
    approval_reason: str,
) -> tuple[
    ProbabilityPipelineReleasePlan,
    ProbabilityPipelineReleaseEpoch | None,
    ProbabilityPipelineReleasePlanRecord | None,
    EvidenceReleaseManifest,
]:
    await acquire_probability_pipeline_release_lock(session)
    candidate = await build_evidence_release_manifest(settings, session)
    fingerprint, manifest_hash, _ = _evidence_manifest_pipeline_identity(candidate)
    active = await current_probability_pipeline_release_epoch(session)
    active_plan = await current_probability_pipeline_release_plan_record(session)
    active_lanes = await current_evidence_continuity_lanes(session)
    if active is not None and len(active_lanes) != 6:
        raise ProbabilityPipelineReleaseIntegrityError(
            "the active release has an incomplete continuity lane set"
        )
    decisions = decide_evidence_lane_actions(
        candidate,
        (
            {lane.lane_name: lane.identity_hash for lane in active_lanes}
            if active is not None
            else None
        ),
    )
    plan = build_probability_pipeline_release_plan(
        pipeline_version=settings.probability_pipeline_version,
        material_fingerprint=fingerprint,
        activation_manifest_hash=manifest_hash,
        image_digest=settings.release_image_digest,
        previous_epoch_id=active.id if active is not None else None,
        release_manifest=candidate.manifest,
        lane_plans=tuple(
            EvidenceLanePlan(
                lane_name=decision.lane_name,
                action=decision.action,
                identity_hash=decision.identity_hash,
                manifest=decision.manifest,
                rationale=decision.rationale,
            )
            for decision in decisions
        ),
        reviewed_by=reviewed_by,
        approval_reason=approval_reason,
    )
    return plan, active, active_plan, candidate


def _evidence_continuity_response_cache(
    request: Request,
) -> EvidenceContinuityResponseCache:
    cache = getattr(request.app.state, "evidence_continuity_response_cache", None)
    if not isinstance(cache, EvidenceContinuityResponseCache):
        cache = EvidenceContinuityResponseCache()
        request.app.state.evidence_continuity_response_cache = cache
    return cache


async def _uncached_evidence_continuity_response(
    request: Request,
) -> EvidenceContinuityResponse:
    settings = _settings(request)
    generated_at = datetime.now(UTC)
    maintenance_windows: tuple[MaintenanceWindow, ...] = ()
    if (
        settings.health_maintenance_starts_at is not None
        and settings.health_maintenance_ends_at is not None
        and settings.health_maintenance_reason is not None
    ):
        maintenance_windows = (
            MaintenanceWindow(
                starts_at=settings.health_maintenance_starts_at,
                ends_at=settings.health_maintenance_ends_at,
                reason=settings.health_maintenance_reason,
            ),
        )
    async with _sessions(request)() as session:
        gate = await probability_release_gate(
            session,
            settings,
            authoritative=False,
        )
        summary = await evidence_continuity_summary(
            session,
            candidate_cohort=(
                gate.candidate_cohort or gate.active_cohort or "release-manifest-unavailable"
            ),
            candidate_image_digest=settings.release_image_digest,
            now=generated_at,
            maintenance_windows=maintenance_windows,
            execution_due_after=timedelta(minutes=settings.paper_order_ttl_minutes),
        )
    return EvidenceContinuityResponse(
        generated_at=summary.generated_at,
        state="collecting" if gate.allowed else gate.reason,
        candidate_cohort=summary.candidate_cohort,
        candidate_image_digest=summary.candidate_image_digest,
        candidate_release_manifest_hash=gate.candidate_release_manifest_hash,
        active_cohort=summary.active_cohort,
        active_release_manifest_hash=gate.active_release_manifest_hash,
        cohort_match=summary.cohort_match,
        image_match=summary.image_match,
        execution_gate_allowed=gate.allowed,
        execution_gate_reason=gate.reason,
        lane_identity_mismatches=list(gate.lane_identity_mismatches),
        frozen_lanes=list(gate.frozen_lanes),
        active_epoch=(
            _evidence_epoch_item(summary.active_epoch) if summary.active_epoch is not None else None
        ),
        epoch_count=summary.epoch_count,
        cohort_changes_30d=summary.cohort_changes_30d,
        current_bottleneck=summary.bottleneck.code,
        current_bottleneck_detail=summary.bottleneck.detail,
        liveness=summary.liveness.value,
        eta_reason=summary.velocity.eta_reason,
        collection_window=_evidence_collection_window_item(summary.collection_window),
        complete_days=summary.velocity.complete_days,
        outcome_velocity_per_day=summary.velocity.outcomes_per_day,
        eta_100_at=summary.velocity.eta_100_at,
        eta_200_at=summary.velocity.eta_200_at,
        paper_stations=summary.paper_stations,
        shadow_stations=summary.shadow_stations,
        open_positions=summary.open_positions,
        open_exposure_usd=summary.open_exposure_usd,
        bankroll_usd=summary.bankroll_usd,
        signal_rows_since_epoch=summary.signal_rows_since_epoch,
        pending_signals=summary.pending_signals,
        top_skip_reasons=[
            EvidenceSkipReasonItem(**asdict(item)) for item in summary.top_skip_reasons
        ],
        continuity_lanes=[_evidence_control_lane_item(lane) for lane in summary.continuity_lanes],
        lanes=[_evidence_lane_item(lane) for lane in summary.lanes],
    )


@router.get(
    "/evidence-continuity",
    response_model=EvidenceContinuityResponse,
)
async def evidence_continuity(request: Request) -> EvidenceContinuityResponse:
    cache = _evidence_continuity_response_cache(request)
    return await cache.get_or_create(lambda: _uncached_evidence_continuity_response(request))


@router.post(
    "/evidence-continuity/plan-hash",
    response_model=EvidenceReleasePlanResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
@router.post(
    "/evidence-continuity/release-plan",
    response_model=EvidenceReleasePlanResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def evidence_continuity_release_plan(
    request: Request,
    payload: EvidenceReleasePlanRequest,
) -> EvidenceReleasePlanResponse:
    settings = _settings(request)
    try:
        async with _sessions(request).begin() as session:
            plan, active, active_plan, candidate = await _evidence_release_plan(
                session,
                settings,
                reviewed_by=payload.reviewed_by,
                approval_reason=payload.approval_reason,
            )
            requires_activation = (
                active is None
                or active_plan is None
                or active_plan.release_manifest_hash != candidate.manifest_hash
            )
            stored_plan = (
                await persist_probability_pipeline_release_plan(session, plan)
                if requires_activation
                else None
            )
    except (EvidenceReleaseManifestError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProbabilityPipelineReleaseIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return EvidenceReleasePlanResponse(
        generated_at=datetime.now(UTC),
        requires_activation=requires_activation,
        persisted_plan_id=stored_plan.id if stored_plan is not None else None,
        persisted_record_hash=(stored_plan.record_hash if stored_plan is not None else None),
        planned_at=stored_plan.planned_at if stored_plan is not None else None,
        active_cohort=active.cohort if active is not None else None,
        pipeline_version=plan.pipeline_version,
        material_fingerprint=plan.material_fingerprint,
        activation_manifest_hash=plan.activation_manifest_hash,
        cohort=plan.cohort,
        image_digest=plan.image_digest,
        previous_epoch_id=plan.previous_epoch_id,
        release_manifest=dict(plan.release_manifest),
        release_manifest_hash=plan.release_manifest_hash,
        lane_plans=[asdict(lane) for lane in plan.lane_plans],
        lane_manifest_hash=plan.lane_manifest_hash,
        reviewed_by=plan.reviewed_by,
        approval_reason=plan.approval_reason,
        plan_hash=plan.plan_hash,
    )


@router.post(
    "/evidence-continuity/activate",
    response_model=EvidenceReleaseActivationResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
@router.post(
    "/evidence-continuity/release-epochs",
    response_model=EvidenceReleaseActivationResponse,
    dependencies=[Depends(require_configured_operator_key)],
)
async def activate_evidence_continuity_release(
    request: Request,
    payload: EvidenceReleaseActivationRequest,
) -> EvidenceReleaseActivationResponse:
    settings = _settings(request)
    try:
        async with _sessions(request).begin() as session:
            plan, active, active_plan, candidate = await _evidence_release_plan(
                session,
                settings,
                reviewed_by=payload.reviewed_by,
                approval_reason=payload.approval_reason,
            )
            if (
                active is not None
                and active_plan is not None
                and active_plan.release_manifest_hash == candidate.manifest_hash
            ):
                raise ProbabilityPipelineReleaseIntegrityError(
                    "the running release manifest is already the active reviewed epoch"
                )
            epoch = await append_probability_pipeline_release_epoch(
                session,
                plan,
                expected_plan_hash=payload.expected_plan_hash,
            )
    except (EvidenceReleaseManifestError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProbabilityPipelineReleaseIntegrityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _evidence_continuity_response_cache(request).invalidate()
    return EvidenceReleaseActivationResponse(
        generated_at=datetime.now(UTC),
        activated=_evidence_epoch_item(epoch),
    )


@router.get(
    "/evidence-continuity/epochs",
    response_model=EvidenceReleaseEpochHistoryResponse,
)
async def evidence_continuity_epoch_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> EvidenceReleaseEpochHistoryResponse:
    async with _sessions(request)() as session:
        epochs = await list_probability_pipeline_release_epochs(session, limit=limit)
    return EvidenceReleaseEpochHistoryResponse(
        generated_at=datetime.now(UTC),
        count=len(epochs),
        epochs=[_evidence_epoch_item(epoch) for epoch in epochs],
    )


@router.get(
    "/evidence-continuity/lanes",
    response_model=EvidenceContinuityLaneHistoryResponse,
)
async def evidence_continuity_lane_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    epoch_id: Annotated[int | None, Query(ge=1)] = None,
) -> EvidenceContinuityLaneHistoryResponse:
    async with _sessions(request)() as session:
        lanes = await list_evidence_continuity_lanes(
            session,
            limit=limit,
            epoch_id=epoch_id,
        )
    return EvidenceContinuityLaneHistoryResponse(
        generated_at=datetime.now(UTC),
        count=len(lanes),
        lanes=[_evidence_control_lane_item(lane) for lane in lanes],
    )


@router.get(
    "/validation-ledger",
    response_model=ProspectiveValidationLedgerResponse,
)
async def prospective_validation_ledger(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
    cohort: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> ProspectiveValidationLedgerResponse:
    """Immutable, zero-backfill order-fill trajectory for one pipeline cohort."""
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    settings = _settings(request)
    async with _sessions(request)() as session:
        active_epoch = await current_probability_pipeline_release_epoch(session)
        current_cohort = active_epoch.cohort if active_epoch is not None else None
        selected_cohort = cohort if cohort is not None else current_cohort
        dataset = await load_prospective_validation_dataset(
            session,
            mode=selected_mode,
            pipeline_cohort=selected_cohort or "__no_current_pipeline__",
        )
    summary = summarize_prospective_validation(
        dataset.trades,
        initial_bankroll_usd=settings.initial_bankroll_usd,
    )
    return ProspectiveValidationLedgerResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        source_integrity=SOURCE_INTEGRITY_LABEL,
        registration_opened_at=dataset.opened_at,
        effective_sample_opened_at=dataset.effective_opened_at,
        predictive_lane_id=(
            dataset.predictive_lineage.lane_id if dataset.predictive_lineage is not None else None
        ),
        predictive_lane_identity_hash=(
            dataset.predictive_lineage.identity_hash
            if dataset.predictive_lineage is not None
            else None
        ),
        predictive_lane_boundary_at=(
            dataset.predictive_lineage.boundary_at
            if dataset.predictive_lineage is not None
            else None
        ),
        execution_lane_id=(
            dataset.execution_lineage.lane_id if dataset.execution_lineage is not None else None
        ),
        execution_lane_identity_hash=(
            dataset.execution_lineage.identity_hash
            if dataset.execution_lineage is not None
            else None
        ),
        execution_lane_boundary_at=(
            dataset.execution_lineage.boundary_at if dataset.execution_lineage is not None else None
        ),
        definition_version=dataset.definition_version,
        definition_hash=dataset.definition_hash,
        cohort_scope="explicit" if cohort is not None else "current",
        requested_pipeline_cohort=cohort,
        current_pipeline_cohort=current_cohort,
        selected_pipeline_cohort=selected_cohort,
        selected_is_current=(selected_cohort is not None and selected_cohort == current_cohort),
        enrolled_orders=dataset.enrolled_orders,
        eligible_enrollments=dataset.eligible_enrollments,
        excluded_enrollments=dataset.excluded_enrollments,
        pending_outcomes=dataset.pending_outcomes,
        pending_blocks=dataset.pending_blocks,
        closed_orders=summary.closed_orders,
        independent_blocks=summary.independent_blocks,
        initial_bankroll_usd=settings.initial_bankroll_usd,
        total_pnl_usd=summary.total_pnl_usd,
        total_cost_usd=summary.total_cost_usd,
        roi=summary.roi,
        skill_pnl_usd=summary.skill_pnl_usd,
        luck_pnl_usd=summary.luck_pnl_usd,
        decomposed_orders=summary.decomposed_orders,
        robust_pnl_usd=summary.robust_pnl_usd,
        robust_roi=summary.robust_roi,
        max_drawdown_usd=summary.max_drawdown_usd,
        max_drawdown_fraction=summary.max_drawdown_fraction,
        largest_order_id=summary.largest_order_id,
        largest_order_pnl_usd=summary.largest_order_pnl_usd,
        largest_order_abs_pnl_share=summary.largest_order_abs_pnl_share,
        pnl_excluding_largest_usd=summary.pnl_excluding_largest_usd,
        roi_excluding_largest=summary.roi_excluding_largest,
        points=[
            ProspectiveValidationPointItem(
                sequence=point.sequence,
                pipeline_cohort=point.pipeline_cohort,
                station_icao=point.station_icao,
                market_date=point.market_date,
                kind=point.kind,
                recognized_at=point.recognized_at,
                orders=point.orders,
                block_pnl_usd=point.block_pnl_usd,
                block_cost_usd=point.block_cost_usd,
                block_skill_pnl_usd=point.block_skill_pnl_usd,
                block_luck_pnl_usd=point.block_luck_pnl_usd,
                cumulative_pnl_usd=point.cumulative_pnl_usd,
                cumulative_skill_pnl_usd=point.cumulative_skill_pnl_usd,
                cumulative_luck_pnl_usd=point.cumulative_luck_pnl_usd,
                equity_usd=point.equity_usd,
                drawdown_usd=point.drawdown_usd,
                max_drawdown_usd=point.max_drawdown_usd,
            )
            for point in summary.points
        ],
    )


@router.get("/edge-report", response_model=EdgeReportResponse)
async def edge_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> EdgeReportResponse:
    """Is the predicted edge real? Brier model-vs-market, skill/luck split,
    bootstrap CIs and realised return per predicted-edge bucket."""
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    settings = _settings(request)
    async with _sessions(request)() as session:
        _, dataset = await _current_prospective_dataset(session, selected_mode)
        perf = summarize_live_performance(prospective_position_stats(dataset.trades))
        trades = prospective_trade_outcomes(dataset.trades)
        clv_outcomes = prospective_clv_outcomes(dataset.trades)
        gate = await calculate_live_gate(
            session,
            settings.initial_bankroll_usd,
            min_stations=settings.live_gate_min_stations,
            station_samples=settings.live_gate_station_samples,
            station_brier=settings.live_gate_station_brier,
            independent_samples_min=settings.live_gate_independent_samples,
        )
    briers = brier_comparison(trades)
    return EdgeReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        benchmark_positions=perf.positions,
        clv_samples=len(clv_outcomes),
        n_resolved_trades=len(trades),
        brier_model=briers[0] if briers else None,
        brier_market=briers[1] if briers else None,
        pipeline_version=gate.pipeline_version,
        pipeline_resolved_trades=gate.pipeline_resolved_trades,
        pipeline_brier_model=gate.pipeline_brier_model,
        pipeline_brier_market=gate.pipeline_brier_market,
        skill_pnl_usd=perf.skill_pnl_usd,
        luck_pnl_usd=perf.luck_pnl_usd,
        roi_ci_low=perf.roi_ci_low,
        roi_ci_high=perf.roi_ci_high,
        skill_pnl_ci_low=perf.skill_pnl_ci_low,
        skill_pnl_ci_high=perf.skill_pnl_ci_high,
        statistically_positive=perf.statistically_positive,
        edge_slope=edge_slope(trades),
        buckets=[
            EdgeBucketItem(
                low=bucket.low,
                high=bucket.high,
                count=bucket.count,
                avg_predicted_edge=bucket.avg_predicted_edge,
                avg_realized_return=bucket.avg_realized_return,
            )
            for bucket in edge_calibration(trades)
        ],
        gate_passed=gate.passed,
        gate_checks=[
            GateCheckItem(name=check.name, passed=check.passed, detail=check.detail)
            for check in gate.checks
        ],
    )


def _selection_policy_item(score: SelectionPolicyScore) -> SelectionPolicyItem:
    return SelectionPolicyItem(
        name=score.name,
        description=score.description,
        is_reference=score.is_reference,
        max_predicted_edge=score.max_predicted_edge,
        opportunities=score.opportunities,
        accepted=score.accepted,
        retention=score.retention,
        independent_blocks=score.independent_blocks,
        brier_model=score.brier_model,
        brier_market=score.brier_market,
        model_minus_market=score.model_minus_market,
        total_pnl_usd=score.total_pnl_usd,
        avg_realized_return=score.avg_realized_return,
        information_value=score.information_value,
        delta_information_value=score.delta_information_value,
        delta_ci_low=score.delta_ci_low,
        delta_ci_high=score.delta_ci_high,
        eligible=score.eligible,
    )


@router.get("/selection-policy-report", response_model=SelectionPolicyReportResponse)
async def selection_policy_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> SelectionPolicyReportResponse:
    """Shadow-only protection against adverse selection and weak slices.

    Candidate policies never change execution here. They must independently
    prove an information-value improvement on the current pipeline cohort.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        current_pipeline, dataset = await _current_prospective_dataset(
            session,
            selected_mode,
        )
        trades = prospective_trade_outcomes(dataset.trades)
        historical_trades = await resolved_trade_outcomes(
            session,
            selected_mode,
            benchmark_protocol=LEGACY_MARKET_PROTOCOL,
        )
        formal_order_ids = dataset.formal_order_ids
        formal_order_id_set = set(formal_order_ids)
        current_trades = [
            trade
            for trade in trades
            if trade.order_id is not None and trade.order_id in formal_order_id_set
        ]
        decisions = await valid_selection_policy_decisions(
            session,
            formal_order_ids,
            pipeline_version=current_pipeline,
        )
        ledger_counts = await selection_policy_ledger_counts(
            session,
            formal_order_ids,
            mode=selected_mode,
            pipeline_version=current_pipeline,
            benchmark_protocol=CLOB_DUAL_PROTOCOL,
        )
    historical_briers = brier_comparison(historical_trades)
    candidates = evaluate_recorded_selection_policies(current_trades, decisions)
    current_opportunities = candidates[0].opportunities if candidates else 0
    return SelectionPolicyReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        pipeline_version=current_pipeline,
        historical_benchmark_protocol=LEGACY_MARKET_PROTOCOL,
        historical_sample_scope=_HISTORICAL_SAMPLE_SCOPE,
        historical_opportunities=len(historical_trades),
        historical_edge_slope=edge_slope(historical_trades),
        historical_model_minus_market=(
            round(historical_briers[0] - historical_briers[1], 6)
            if historical_briers is not None
            else None
        ),
        recorded_opportunities=ledger_counts.recorded,
        current_opportunities=current_opportunities,
        candidates=[_selection_policy_item(score) for score in candidates],
    )


def _brier_slice_item(slice_: BrierSlice) -> BrierSliceItem:
    return BrierSliceItem(
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        brier_model=slice_.brier_model,
        brier_market=slice_.brier_market,
        model_minus_market=slice_.model_minus_market,
        brier_raw_model=slice_.brier_raw_model,
        raw_model_minus_market=slice_.raw_model_minus_market,
        raw_count=slice_.raw_count,
        avg_predicted_edge=slice_.avg_predicted_edge,
        avg_realized_return=slice_.avg_realized_return,
        win_rate=slice_.win_rate,
    )


@router.get("/brier-report", response_model=BrierReportResponse)
async def brier_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> BrierReportResponse:
    """Root-cause where the model is worse or better than the market.

    Positive *_minus_market values mean the model has higher Brier error than
    the market in that slice. raw_model fields use the pre-shrink probability
    when component metadata is available.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        _, dataset = await _current_prospective_dataset(session, selected_mode)
        trades = prospective_trade_outcomes(dataset.trades)
    briers = brier_comparison(trades)
    raw = raw_model_brier(trades)
    brier_model = briers[0] if briers else None
    brier_market = briers[1] if briers else None
    brier_raw_model = raw[0] if raw else None
    return BrierReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        n_resolved_trades=len(trades),
        brier_model=brier_model,
        brier_market=brier_market,
        brier_raw_model=brier_raw_model,
        raw_count=raw[1] if raw else 0,
        model_minus_market=(
            round(brier_model - brier_market, 6)
            if brier_model is not None and brier_market is not None
            else None
        ),
        raw_model_minus_market=(
            round(brier_raw_model - brier_market, 6)
            if brier_raw_model is not None and brier_market is not None
            else None
        ),
        slices=[_brier_slice_item(slice_) for slice_ in brier_root_cause(trades)],
    )


def _clv_slice_item(slice_: ClvSlice) -> ClvSliceItem:
    return ClvSliceItem(
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        avg_clv=slice_.avg_clv,
        clv_beat_rate=slice_.clv_beat_rate,
        avg_entry_price=slice_.avg_entry_price,
        avg_fee_usd=slice_.avg_fee_usd,
        avg_realized_return=slice_.avg_realized_return,
        win_rate=slice_.win_rate,
    )


def _brier_action_item(slice_: BrierSlice) -> RootCauseActionItem:
    if slice_.count < 10:
        action = "Watch"
        severity = "warning"
        detail = (
            "Action: keep collecting until n≥10 for this slice; "
            "current model-vs-market gap may be noise."
        )
    elif slice_.model_minus_market <= 0:
        action = "Keep"
        severity = "positive"
        detail = "Action: no model restriction; model is not worse than the market in this slice."
    elif slice_.count >= 30 and slice_.model_minus_market >= 0.05:
        action = "Block cand."
        severity = "negative"
        detail = (
            "Action: candidate for block/recalibration rule; model is "
            "materially worse than market with enough sample."
        )
    elif slice_.model_minus_market >= 0.02:
        action = "Reduce"
        severity = "warning"
        detail = "Action: reduce sizing or require stronger edge until Brier delta improves."
    else:
        action = "Watch"
        severity = "warning"
        detail = "Action: mild model drag; monitor before changing rules."

    return RootCauseActionItem(
        metric="brier",
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        action=action,
        severity=severity,
        detail=detail,
    )


def _clv_action_item(slice_: ClvSlice) -> RootCauseActionItem:
    if slice_.count < 10:
        action = "Watch"
        severity = "warning"
        detail = "Action: keep collecting until n≥10 for this slice; current CLV may be noisy."
    elif slice_.avg_clv >= 0:
        action = "Keep"
        severity = "positive"
        detail = "Action: execution quality acceptable; entries beat or match the closing line."
    elif slice_.count >= 30 and slice_.avg_clv <= -0.03:
        action = "Block cand."
        severity = "negative"
        detail = (
            "Action: candidate for block/maker-only rule; repeated entries "
            "are far worse than closing line."
        )
    elif slice_.avg_clv <= -0.01:
        action = "Maker only"
        severity = "warning"
        detail = (
            "Action: avoid taker-style fills; prefer passive midpoint/maker entry or reduce size."
        )
    else:
        action = "Watch"
        severity = "warning"
        detail = "Action: slight negative CLV; monitor entry quality before changing rules."

    return RootCauseActionItem(
        metric="clv",
        dimension=slice_.dimension,
        value=slice_.value,
        count=slice_.count,
        action=action,
        severity=severity,
        detail=detail,
    )


def _action_rank(item: RootCauseActionItem) -> tuple[int, str, str, str]:
    severity_rank = {"negative": 0, "warning": 1, "positive": 2}.get(item.severity, 3)
    return severity_rank, item.metric, item.dimension, item.value


@router.get("/clv-report", response_model=ClvReportResponse)
async def clv_report(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> ClvReportResponse:
    """Entry-quality root cause: where do we enter worse than the market?

    Closing-line value by station, kind, horizon, confidence, side and entry
    price band. Negative avg_clv in a slice means we systematically pay up
    (spread / slippage / adverse selection) entering those trades. Slices are
    sorted worst-CLV first within each dimension.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        _, dataset = await _current_prospective_dataset(session, selected_mode)
        outcomes = prospective_clv_outcomes(dataset.trades)
    summary = clv_summary(outcomes)
    return ClvReportResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        n_closed_positions=len(outcomes),
        avg_clv=summary[0] if summary else None,
        clv_beat_rate=summary[1] if summary else None,
        slices=[_clv_slice_item(slice_) for slice_ in clv_root_cause(outcomes)],
    )


@router.get("/root-cause-actions", response_model=RootCauseActionsResponse)
async def root_cause_actions(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> RootCauseActionsResponse:
    """Advisory actions for diagnostic slices.

    These are operator hints only: they do not change signal generation,
    sizing, station eligibility, or runtime state. Keeping the policy in the
    backend gives Cockpit, Telegram, and future reports one source of truth.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    async with _sessions(request)() as session:
        _, dataset = await _current_prospective_dataset(session, selected_mode)
        trades = prospective_trade_outcomes(dataset.trades)
        clv_outcomes = prospective_clv_outcomes(dataset.trades)
    items = [
        *[_brier_action_item(slice_) for slice_ in brier_root_cause(trades)],
        *[_clv_action_item(slice_) for slice_ in clv_root_cause(clv_outcomes)],
    ]
    return RootCauseActionsResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        items=sorted(items, key=_action_rank),
    )


def _prospective_dimension_value(
    trade: ProspectiveValidationTrade,
    dimension: str,
) -> str:
    if dimension == "station":
        return trade.station_icao or "unknown"
    if dimension == "kind":
        return trade.kind or "unknown"
    if dimension == "horizon":
        return trade.horizon_bucket or "unknown"
    if dimension == "confidence":
        return trade.confidence or "unknown"
    if dimension == "side":
        return trade.side or "unknown"
    if dimension == "pipeline_version":
        return trade.pipeline_cohort or "unknown"
    if dimension == "price_band" and trade.fill_vwap is not None:
        return price_band(trade.fill_vwap)
    return "unknown"


def _prospective_root_cause_item(
    trade: ProspectiveValidationTrade,
    *,
    metric: str,
) -> RootCauseTradeItem:
    """Render one immutable enrollment/outcome projection for drill-down."""

    assert trade.signal_id is not None
    assert trade.market_id is not None
    assert trade.polymarket_id is not None
    assert trade.side is not None
    assert trade.fill_vwap is not None
    assert trade.model_prob is not None
    assert trade.market_prob is not None
    assert trade.predicted_edge is not None
    assert trade.resolved_yes is not None
    assert trade.won is not None
    assert trade.fee_usd is not None
    outcome = float(trade.won)
    model_error = (trade.model_prob - outcome) ** 2
    market_error = (trade.market_prob - outcome) ** 2
    return RootCauseTradeItem(
        id=trade.enrollment_id,
        record_type="prospective_order_fill",
        order_id=trade.order_id,
        signal_id=trade.signal_id,
        position_id=None,
        market_id=trade.market_id,
        polymarket_id=trade.polymarket_id,
        station_icao=trade.station_icao,
        market_date=trade.market_date,
        kind=trade.kind,
        side=trade.side,
        confidence=trade.confidence,
        horizon_bucket=trade.horizon_bucket or "unknown",
        timestamp=trade.signal_ts,
        closed_at=trade.market_closed_at,
        entry_price=round(trade.fill_vwap, 6),
        closing_line_price=(round(trade.close_price, 6) if trade.close_price is not None else None),
        model_probability=round(trade.model_prob, 6),
        market_probability=round(trade.market_prob, 6),
        raw_model_probability=(
            round(trade.raw_model_prob, 6) if trade.raw_model_prob is not None else None
        ),
        predicted_edge=round(trade.predicted_edge, 6),
        brier_model_error=(round(model_error, 6) if metric == "brier" else None),
        brier_market_error=(round(market_error, 6) if metric == "brier" else None),
        brier_delta=(round(model_error - market_error, 6) if metric == "brier" else None),
        clv=(round(trade.clv, 6) if trade.clv is not None else None),
        pnl_usd=round(trade.pnl_usd, 4),
        cost_usd=round(trade.cost_usd, 4),
        fee_usd=round(trade.fee_usd, 5),
        won=trade.won,
        resolved_outcome=trade.resolved_yes,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
    )


async def _brier_drilldown_items(
    session: AsyncSession,
    mode: ExecutionMode,
    dimension: str,
    value: str,
    limit: int,
) -> tuple[int, list[RootCauseTradeItem]]:
    _, dataset = await _current_prospective_dataset(session, mode)
    scored_order_ids = {trade.order_id for trade in prospective_trade_outcomes(dataset.trades)}
    members = [
        trade
        for trade in dataset.trades
        if trade.order_id in scored_order_ids
        and _prospective_dimension_value(trade, dimension) == value
    ]
    items = [_prospective_root_cause_item(trade, metric="brier") for trade in members]
    items.sort(key=lambda item: item.brier_delta or 0, reverse=True)
    return len(items), items[:limit]


async def _clv_drilldown_items(
    session: AsyncSession,
    mode: ExecutionMode,
    dimension: str,
    value: str,
    limit: int,
) -> tuple[int, list[RootCauseTradeItem]]:
    _, dataset = await _current_prospective_dataset(session, mode)
    formally_scored_order_ids = {
        trade.order_id for trade in prospective_trade_outcomes(dataset.trades)
    }
    members = [
        trade
        for trade in dataset.trades
        if trade.order_id in formally_scored_order_ids
        and trade.clv is not None
        and _prospective_dimension_value(trade, dimension) == value
    ]
    items = [_prospective_root_cause_item(trade, metric="clv") for trade in members]
    items.sort(key=lambda item: item.clv if item.clv is not None else 0)
    return len(items), items[:limit]


@router.get("/root-cause-drilldown", response_model=RootCauseDrilldownResponse)
async def root_cause_drilldown(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
    metric: Annotated[str, Query(pattern="^(brier|clv)$")] = "brier",
    dimension: Annotated[str, Query(min_length=1, max_length=32)] = "station",
    value: Annotated[str, Query(min_length=1, max_length=64)] = "unknown",
    limit: Annotated[int, Query(ge=1, le=50)] = 25,
) -> RootCauseDrilldownResponse:
    """Concrete trades/positions behind a diagnostic slice.

    This powers Cockpit drill-down: clicking "LFPB", "6-24h", "buy_yes",
    etc. shows the rows that made that Brier or CLV slice bad.
    """
    selected_mode = ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    if metric == "brier":
        if dimension not in _BRIER_DRILLDOWN_DIMENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"dimension '{dimension}' is not available for brier",
            )
        async with _sessions(request)() as session:
            total_count, items = await _brier_drilldown_items(
                session, selected_mode, dimension, value, limit
            )
    else:
        if dimension not in _CLV_DRILLDOWN_DIMENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"dimension '{dimension}' is not available for clv",
            )
        async with _sessions(request)() as session:
            total_count, items = await _clv_drilldown_items(
                session, selected_mode, dimension, value, limit
            )
    return RootCauseDrilldownResponse(
        generated_at=datetime.now(UTC),
        mode=mode,
        benchmark_protocol=CLOB_DUAL_PROTOCOL,
        sample_scope=_PROSPECTIVE_SAMPLE_SCOPE,
        metric=metric,
        dimension=dimension,
        value=value,
        count=total_count,
        items=items,
    )


async def _equity_response(request: Request, mode: ExecutionMode) -> EquityResponse:
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(Position.closed_at, Position.pnl_usd).where(
                    Position.mode == mode,
                    Position.status == PositionStatus.CLOSED,
                    Position.closed_at.is_not(None),
                )
            )
        ).all()
    points = cumulative_equity([(ts, float(pnl or 0)) for ts, pnl in rows if ts is not None])
    return EquityResponse(
        generated_at=datetime.now(UTC),
        points=[EquityPointItem(t=p.ts, cumulative_pnl_usd=p.cumulative_pnl_usd) for p in points],
    )


@router.get("/live-equity", response_model=EquityResponse)
async def live_equity(request: Request) -> EquityResponse:
    return await _equity_response(request, ExecutionMode.LIVE)


@router.get("/equity", response_model=EquityResponse)
async def equity(
    request: Request,
    mode: Annotated[str, Query(pattern="^(paper|live)$")] = "paper",
) -> EquityResponse:
    """Cumulative realised PnL for either execution mode."""
    return await _equity_response(
        request, ExecutionMode.LIVE if mode == "live" else ExecutionMode.PAPER
    )


@router.get("/orders", response_model=OrdersResponse)
async def orders(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> OrdersResponse:
    fill_aggregate = execution_fill_aggregate_subquery()
    async with _sessions(request)() as session:
        rows = (
            await session.execute(
                select(
                    Order,
                    Market.polymarket_id,
                    Station.icao,
                    ExecutionOrderIntent.id,
                    fill_aggregate.c.vwap_fill_price,
                    fill_aggregate.c.filled_notional_usd,
                    fill_aggregate.c.total_fee_usd,
                    fill_aggregate.c.total_shares,
                )
                .join(Signal, Signal.id == Order.signal_id)
                .join(Market, Market.id == Signal.market_id)
                .outerjoin(Station, Station.id == Market.station_id)
                .outerjoin(ExecutionOrderIntent, ExecutionOrderIntent.order_id == Order.id)
                .outerjoin(fill_aggregate, fill_aggregate.c.order_id == Order.id)
                .order_by(Order.id.desc())
                .limit(limit)
            )
        ).all()
    items: list[OrderItem] = []
    for (
        order,
        polymarket_id,
        icao,
        execution_intent_id,
        leg_vwap,
        leg_notional,
        leg_fee,
        leg_shares,
    ) in rows:
        fill = actual_order_fill(
            order_price=order.price,
            order_filled_usd=order.filled_usd,
            order_fee_usd=order.fee_usd,
            execution_intent_id=execution_intent_id,
            leg_vwap_fill_price=leg_vwap,
            leg_filled_notional_usd=leg_notional,
            leg_fee_usd=leg_fee,
            leg_total_shares=leg_shares,
        )
        items.append(
            OrderItem(
                id=order.id,
                signal_id=order.signal_id,
                polymarket_id=polymarket_id,
                station_icao=icao,
                mode=order.mode.value,
                clob_order_id=order.clob_order_id,
                side=order.side.value,
                price=float(order.price),
                requested_limit_price=float(order.price),
                actual_fill_vwap=(float(fill.entry_price) if fill is not None else None),
                size_usd=float(order.size_usd),
                filled_usd=float(order.filled_usd),
                fee_usd=_number(order.fee_usd),
                status=order.status,
                created_at=order.created_at,
                filled_at=order.filled_at,
            )
        )
    return OrdersResponse(
        generated_at=datetime.now(UTC),
        items=items,
    )


@router.get("/jobs", response_model=JobsResponse)
async def jobs(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> JobsResponse:
    async with _sessions(request)() as session:
        rows = (
            (await session.execute(select(JobRun).order_by(JobRun.finished_at.desc()).limit(limit)))
            .scalars()
            .all()
        )
    return JobsResponse(
        generated_at=datetime.now(UTC),
        items=[
            JobItem(
                id=job.id,
                job_id=job.job_id,
                scheduled_at=job.scheduled_at,
                finished_at=job.finished_at,
                status=job.status,
                error=job.error,
            )
            for job in rows
        ],
    )
