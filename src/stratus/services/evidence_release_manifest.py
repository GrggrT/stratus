from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Literal, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.config import Settings
from stratus.db.models import (
    EvidenceContinuityRuntime,
    ExecutionCostRuntime,
    ForecastProvenanceRuntime,
    Market,
    ObservationRecoveryRuntime,
    OfficialResolutionEvidenceRuntime,
    ProspectiveValidationRuntime,
    ResolutionContract,
    Station,
    WeatherKind,
)
from stratus.repositories.accuracy_promotions import (
    EffectivePromotionManifest,
    load_effective_accuracy_promotion_manifest,
)
from stratus.repositories.evidence_continuity import probability_pipeline_release_cohort
from stratus.services.execution_cost import source_payload_hash
from stratus.services.pipeline_identity import (
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
)
from stratus.services.resolution_contracts import (
    ResolutionContractError,
    parse_resolution_contract,
)
from stratus.services.selection_policy import (
    SHADOW_SELECTION_POLICIES,
    selection_policy_hash,
)

EVIDENCE_RELEASE_MANIFEST_PROTOCOL = "evidence-release-manifest-v1"
EVIDENCE_LANE_MANIFEST_PROTOCOL = "evidence-lane-manifest-v1"
ALEMBIC_HEAD = "20260730_0047"
LANE_NAMES = (
    "execution_evidence",
    "official_resolution_truth",
    "operational_legacy",
    "predictive_validation",
    "selection_policy",
    "station_lifecycle",
)
EvidenceLaneName = Literal[
    "predictive_validation",
    "selection_policy",
    "execution_evidence",
    "operational_legacy",
    "station_lifecycle",
    "official_resolution_truth",
]
EvidenceLaneAction = Literal["continue", "fork", "freeze"]

_ZERO_HASH = "0" * 64
_HEX_16 = re.compile(r"^[0-9a-f]{16}$")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_FORBIDDEN_KEY = re.compile(
    r"(?:secret|password|private[_-]?key|credential|api[_-]?key|bearer|token|"
    r"rules[_-]?raw|source[_-]?url|license[_-]?url|endpoint[_-]?url)",
    re.IGNORECASE,
)
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

_PREDICTIVE_SOURCE_FILES = (
    "collectors/clob.py",
    "collectors/gamma.py",
    "collectors/iem.py",
    "collectors/nws.py",
    "collectors/open_meteo.py",
    "collectors/open_meteo_ensemble.py",
    "collectors/open_meteo_previous_runs.py",
    "db/models.py",
    # The official-source operator workflow still owns reviewed license,
    # adapter, and attestation binding until it is fully extracted into a
    # dedicated service.  Hash the whole boundary so API refactors cannot
    # silently change predictive truth semantics.
    "operator_api.py",
    "jobs/audit_accuracy.py",
    "jobs/discover_markets.py",
    "jobs/evaluate_forecasts.py",
    "jobs/evaluate_promotion_trials.py",
    "jobs/bootstrap_iem.py",
    "jobs/bootstrap_previous_runs.py",
    "jobs/fetch_ensemble.py",
    "jobs/fetch_forecasts.py",
    "jobs/fetch_observations.py",
    "jobs/finalize_daily_actuals.py",
    "jobs/recover_observations.py",
    "jobs/recalibrate_emos.py",
    "jobs/generate_censored_challenger.py",
    "jobs/generate_weighted_ensemble.py",
    "jobs/recompute_probs.py",
    "parsers/market_rules.py",
    "repositories/accuracy_promotions.py",
    "repositories/accuracy.py",
    "repositories/analytics.py",
    "repositories/calibration.py",
    "repositories/clob_snapshots.py",
    "repositories/daily_actual_fallback.py",
    "repositories/forecast_provenance.py",
    "repositories/gamma_outcomes.py",
    "repositories/daily_actuals.py",
    "repositories/historical_forecasts.py",
    "repositories/model_probs.py",
    "repositories/markets.py",
    "repositories/observation_recovery.py",
    "repositories/official_resolution.py",
    "repositories/training_truth.py",
    "repositories/validation_ledger.py",
    "services/accuracy_models.py",
    "services/clob_evidence.py",
    "services/conformal_probability.py",
    "services/emos.py",
    "services/event_probability_coherence.py",
    "services/forecast_collection.py",
    "services/forecast_provenance.py",
    "services/hierarchical_probability.py",
    "services/daily_actuals.py",
    "services/live_analytics.py",
    "services/model_catalog.py",
    "services/observation_recovery.py",
    "services/official_resolution.py",
    "services/official_resolution_artifacts.py",
    "services/pipeline_identity.py",
    "services/probability.py",
    "services/promotion_protocol.py",
    "services/regime_probability.py",
    "services/resolution_contracts.py",
    "services/sealed_accuracy.py",
    "services/settlement_calibration.py",
    "services/trajectory_probability.py",
)

RESOLUTION_CONTRACT_SEMANTICS_PROTOCOL = "resolution-contract-semantic-profiles-v2"
RESOLUTION_CONTRACT_STATION_MAPPING_PROTOCOL = (
    "resolution-contract-source-station-mapping-v1"
)
_RESOLUTION_CONTRACT_EVENT_FIELDS = frozenset(
    {"event_id", "resolution_date", "rules_hash"}
)
_RESOLUTION_CONTRACT_SEMANTIC_FIELDS = (
    "rules_template_sha256",
    "source_family",
    "source_locator_sha256",
    "station_code",
    "local_timezone",
    "statistic",
    "source_unit",
    "decimal_places",
    "finalization_rule",
    "license_status",
    "storage_permitted",
    "license_locator_sha256",
)

_RESOLUTION_LICENSE_POLICY = {
    "wunderground": ("restricted_unlicensed", False, True),
    "hko": ("open_data_attribution", True, True),
    "noaa": ("us_government_open", True, True),
    "national": ("unreviewed", False, False),
}

_LANE_SOURCE_FILES: Mapping[str, tuple[str, ...]] = {
    "predictive_validation": _PREDICTIVE_SOURCE_FILES,
    "selection_policy": (
        "jobs/assess_station_reliability.py",
        "jobs/execute_live_signals.py",
        "jobs/execute_signals.py",
        "jobs/generate_signals.py",
        "jobs/reconcile_resolutions.py",
        "jobs/recover_observations.py",
        "repositories/resolution_reconciliation.py",
        "repositories/execution_cost_ledger.py",
        "repositories/selection_policy.py",
        "repositories/signals.py",
        "repositories/station_reliability.py",
        "runtime.py",
        "services/reconciliation.py",
        "services/selection_policy.py",
        "services/signals.py",
        "services/station_reliability.py",
    ),
    "execution_evidence": (
        "collectors/clob.py",
        "collectors/clob_trader.py",
        "jobs/execute_live_signals.py",
        "jobs/execute_signals.py",
        "jobs/finalize_execution_cost_obligations.py",
        "jobs/maintain_execution_cost_evidence.py",
        "jobs/quarantine_execution_state.py",
        "jobs/seal_execution_markouts.py",
        "repositories/clob_snapshots.py",
        "repositories/execution.py",
        "repositories/execution_cost_ledger.py",
        "services/clob_evidence.py",
        "services/execution.py",
        "services/execution_cost.py",
        "services/live_execution.py",
        "runtime.py",
    ),
    "operational_legacy": (
        "api.py",
        "config.py",
        "db/models.py",
        "db/session.py",
        "jobs/audit_gate.py",
        "jobs/audit_health.py",
        "jobs/audit_storage_capacity.py",
        "jobs/fetch_prices.py",
        "jobs/process_operator_commands.py",
        "jobs/reconcile_resolutions.py",
        "jobs/recover_observations.py",
        "jobs/settle_markets.py",
        "repositories/daily_actual_fallback.py",
        "repositories/evidence_continuity_reporting.py",
        "repositories/emergency_stop.py",
        "repositories/paper_canary_leases.py",
        "repositories/resolution_reconciliation.py",
        "repositories/risk.py",
        "repositories/runtime_control.py",
        "repositories/safety_lock.py",
        "repositories/settlement.py",
        "repositories/settlement_cases.py",
        "repositories/station_reliability.py",
        "repositories/storage_capacity.py",
        "repositories/training_truth.py",
        "repositories/venue_runtime_control.py",
        "services/evidence_release_manifest.py",
        "services/operator_commands.py",
        "services/portfolio_marks.py",
        "services/reconciliation.py",
        "services/risk.py",
        "services/safety_control.py",
        "services/settlement.py",
        "services/settlement_lifecycle.py",
        "services/station_reliability.py",
        "services/storage_capacity.py",
        "runtime_db_boundary.py",
        "runtime.py",
        "safety_api.py",
        "telegram_bot.py",
    ),
    "station_lifecycle": (
        "jobs/assess_station_reliability.py",
        "jobs/discover_markets.py",
        "jobs/execute_live_signals.py",
        "jobs/execute_signals.py",
        "jobs/reconcile_resolutions.py",
        "jobs/recover_observations.py",
        "parsers/market_rules.py",
        "repositories/markets.py",
        "repositories/execution_cost_ledger.py",
        "repositories/resolution_reconciliation.py",
        "repositories/selection_policy.py",
        "repositories/station_reliability.py",
        "runtime.py",
        "services/reconciliation.py",
        "services/resolution_contracts.py",
        "services/station_reliability.py",
    ),
    "official_resolution_truth": (
        "collectors/gamma.py",
        "db/models.py",
        "operator_api.py",
        "jobs/discover_markets.py",
        "parsers/market_rules.py",
        "repositories/accuracy.py",
        "repositories/markets.py",
        "repositories/official_resolution.py",
        "services/official_resolution.py",
        "services/official_resolution_artifacts.py",
        "services/resolution_contracts.py",
    ),
}

# SQL functions and triggers are executable evidence semantics just as much as
# their Python repository counterparts. Keep the focused migration ancestry
# that defines prospective fill projection and execution attestation in the
# affected lane identities. Paths here are repository-relative (unlike
# ``_LANE_SOURCE_FILES``, which are relative to ``src/stratus``).
_LANE_REPOSITORY_SOURCE_FILES: Mapping[str, tuple[str, ...]] = {
    "predictive_validation": (
        "pyproject.toml",
        "uv.lock",
        "migrations/versions/20260712_0021_accuracy_v2_foundation.py",
        "migrations/versions/20260714_0035_prospective_validation_ledger.py",
        "migrations/versions/20260715_0039_official_resolution_source_evidence.py",
        "migrations/versions/20260715_0040_evidence_continuity.py",
        "migrations/versions/20260715_0041_execution_fill_projection.py",
        "migrations/versions/20260717_0043_observation_recovery.py",
        "migrations/versions/20260719_0044_resolution_contract_integrity.py",
        "migrations/versions/20260728_0045_forecast_evaluation_close_precision.py",
    ),
    "execution_evidence": (
        "pyproject.toml",
        "uv.lock",
        "migrations/versions/20260714_0031_dual_token_clob_ledger.py",
        "migrations/versions/20260714_0032_dual_trade_immutability.py",
        "migrations/versions/20260715_0038_execution_cost_ledger.py",
        "migrations/versions/20260715_0040_evidence_continuity.py",
        "migrations/versions/20260715_0041_execution_fill_projection.py",
        "migrations/versions/20260717_0042_station_reliability.py",
    ),
    "selection_policy": (
        "pyproject.toml",
        "uv.lock",
        "migrations/versions/20260714_0034_resolution_reconciliation_lifecycle.py",
        "migrations/versions/20260717_0042_station_reliability.py",
        "migrations/versions/20260717_0043_observation_recovery.py",
    ),
    "station_lifecycle": (
        "pyproject.toml",
        "uv.lock",
        "migrations/versions/20260714_0034_resolution_reconciliation_lifecycle.py",
        "migrations/versions/20260717_0042_station_reliability.py",
        "migrations/versions/20260717_0043_observation_recovery.py",
        "migrations/versions/20260719_0044_resolution_contract_integrity.py",
    ),
    # Compose is deployment state outside the immutable image.  Service
    # liveness/readiness and database restart semantics therefore belong to
    # the operational continuity lane instead of disappearing behind a new
    # image digest.
    "operational_legacy": (
        ".github/workflows/ci.yml",
        ".dockerignore",
        "Dockerfile",
        "alembic.ini",
        "docker-compose.yml",
        "docs/RELEASE_CUTOVER_RUNBOOK.md",
        "migrations/env.py",
        "migrations/versions/20260714_0034_resolution_reconciliation_lifecycle.py",
        "migrations/versions/20260715_0039_official_resolution_source_evidence.py",
        "migrations/versions/20260717_0042_station_reliability.py",
        "migrations/versions/20260719_0044_resolution_contract_integrity.py",
        "migrations/versions/20260730_0046_accuracy_overview_indexes.py",
        "migrations/versions/20260730_0047_paper_canary_safety_lease.py",
        "ops/postgres/stratus-pg_hba.conf",
        "pyproject.toml",
        "scripts/Assert-StratusLoadGateArtifacts.ps1",
        "scripts/Assert-StratusPaperCanaryArtifacts.ps1",
        "scripts/Assert-StratusPreMigrationBackupGate.ps1",
        "scripts/Assert-StratusPreStartTopology.ps1",
        "scripts/Assert-StratusReleaseImage.ps1",
        "scripts/Invoke-StratusDashboardLoadGate.ps1",
        "scripts/Invoke-StratusPaperCanary.ps1",
        "scripts/Invoke-StratusPaperCanaryStopBarrier.ps1",
        "scripts/Invoke-StratusRuntimeLoadObserver.ps1",
        "scripts/tests/Test-StratusDashboardLoadGate.ps1",
        "scripts/tests/Test-StratusLoadGateArtifacts.ps1",
        "scripts/tests/Test-StratusPaperCanary.ps1",
        "scripts/tests/Test-StratusPaperCanaryArtifacts.ps1",
        "scripts/tests/Test-StratusPaperCanaryStopBarrier.ps1",
        "scripts/tests/Test-StratusPreMigrationBackupGate.ps1",
        "scripts/tests/Test-StratusPreStartTopology.ps1",
        "scripts/tests/Test-StratusReleaseImage.ps1",
        "scripts/tests/Test-StratusReleaseRunbook.ps1",
        "scripts/tests/Test-StratusRuntimeLoadObserver.ps1",
        "scripts/Start-StratusDevApi.ps1",
        "scripts/stratus-runtime-entrypoint.sh",
        "uv.lock",
    ),
    "official_resolution_truth": (
        "pyproject.toml",
        "uv.lock",
        "migrations/versions/20260712_0021_accuracy_v2_foundation.py",
        "migrations/versions/20260715_0039_official_resolution_source_evidence.py",
        "migrations/versions/20260719_0044_resolution_contract_integrity.py",
    ),
}

_PREDICTIVE_CONFIG = (
    "daily_actual_boundary_tolerance_minutes",
    "daily_actual_max_gap_minutes",
    "daily_actual_min_samples",
    "daily_actual_recovery_enabled",
    "daily_actual_recovery_lookback_days",
    "daily_actual_recovery_max_requests_per_run",
    "daily_actual_recovery_retry_hours",
    "daily_actual_recovery_local_hour",
    "daily_actual_recovery_max_response_bytes",
    "accuracy_challenger_min_samples",
    "accuracy_conformal_coverage",
    "accuracy_conformal_max_scale",
    "accuracy_conformal_min_blocks",
    "accuracy_conformal_min_samples",
    "accuracy_hierarchical_min_blocks",
    "accuracy_hierarchical_min_peer_stations",
    "accuracy_hierarchical_prior_samples",
    "accuracy_nonlinear_min_blocks",
    "accuracy_nowcast_bias_half_life_hours",
    "accuracy_nowcast_lookback_hours",
    "accuracy_nowcast_max_bias_c",
    "accuracy_nowcast_min_blocks",
    "accuracy_nowcast_min_observed_hours",
    "accuracy_qrf_trees",
    "accuracy_regime_baseline_days",
    "accuracy_regime_bootstrap_samples",
    "accuracy_regime_max_adjustment_c",
    "accuracy_regime_min_baseline",
    "accuracy_regime_min_blocks",
    "accuracy_regime_min_recent",
    "accuracy_regime_min_shift_c",
    "accuracy_regime_recent_days",
    "accuracy_regional_shadow_enabled",
    "accuracy_single_runs_backfill_days",
    "accuracy_single_runs_max_requests",
    "accuracy_stack_l2",
    "accuracy_stack_learning_rate",
    "accuracy_stack_min_blocks",
    "accuracy_stack_min_samples",
    "accuracy_trajectory_min_blocks",
    "accuracy_trajectory_min_models",
    "accuracy_trajectory_rho",
    "iem_recovery_terms_policy_hash",
    "accuracy_trajectory_samples",
    "calibration_min_samples",
    "calibration_window_days",
    "ensemble_models",
    "forecast_models",
    "min_traded_calibration_samples",
    "observation_lookback_hours",
    "previous_run_horizon_days",
    "probability_pipeline_version",
    "settlement_calibrator_ece_tolerance",
    "settlement_calibrator_l2",
    "settlement_calibrator_min_blocks",
    "shrinkage_prior_samples",
)

_LANE_CONFIG: Mapping[str, tuple[str, ...]] = {
    "predictive_validation": _PREDICTIVE_CONFIG,
    "selection_policy": (
        "edge_cost_buffer",
        "max_spread",
        "min_traded_calibration_samples",
        "signal_max_edge",
        "signal_min_edge",
        "station_observation_stale_minutes",
        "station_reliability_min_lower_bound",
        "station_reliability_min_samples",
        "station_reliability_snapshot_max_age_minutes",
        "station_reliability_window_days",
        "station_resolution_cooldown_hours",
        "tail_price_high",
        "tail_price_low",
    ),
    "execution_evidence": (
        "live_max_order_usd",
        "paper_maker_execution",
        "paper_order_ttl_minutes",
    ),
    "operational_legacy": (
        "exposure_hard_stop_multiplier",
        "initial_bankroll_usd",
        "kelly_multiplier",
        "market_exposure_fraction",
        "max_win_fraction",
        "risk_timezone",
        "station_day_exposure_fraction",
    ),
    "station_lifecycle": (
        "nws_stations",
        "station_observation_stale_minutes",
        "station_reliability_min_lower_bound",
        "station_reliability_min_samples",
        "station_reliability_snapshot_max_age_minutes",
        "station_reliability_window_days",
        "station_resolution_cooldown_hours",
        "target_cities",
    ),
    "official_resolution_truth": ("official_resolution_artifact_max_bytes",),
}


class EvidenceReleaseManifestError(RuntimeError):
    """A candidate release cannot be represented without ambiguity."""


@dataclass(frozen=True, slots=True)
class EvidenceReleaseDatabaseSnapshot:
    alembic_heads: tuple[str, ...]
    evidence_continuity_runtime: Mapping[str, object] | None
    prospective_validation_runtime: Mapping[str, object] | None
    forecast_provenance_runtime: Mapping[str, object] | None
    observation_recovery_runtime: Mapping[str, object] | None
    execution_cost_runtime: Mapping[str, object] | None
    official_resolution_runtime: Mapping[str, object] | None
    active_stations: tuple[Mapping[str, object], ...]
    active_resolution_contracts: tuple[Mapping[str, object], ...]
    effective_promotions: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class EvidenceReleaseLaneManifest:
    lane_name: EvidenceLaneName
    manifest: Mapping[str, object]
    identity_hash: str
    missing_requirements: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing_requirements


@dataclass(frozen=True, slots=True)
class EvidenceReleaseManifest:
    manifest: Mapping[str, object]
    manifest_hash: str
    lanes: tuple[EvidenceReleaseLaneManifest, ...]

    @property
    def lane_identity_hashes(self) -> dict[str, str]:
        return {lane.lane_name: lane.identity_hash for lane in self.lanes}


@dataclass(frozen=True, slots=True)
class EvidenceLaneDecision:
    lane_name: EvidenceLaneName
    action: EvidenceLaneAction
    identity_hash: str
    manifest: Mapping[str, object]
    rationale: str


def _number_text(value: Decimal) -> str:
    if not value.is_finite():
        raise EvidenceReleaseManifestError("manifest numbers must be finite")
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _sort_key(value: object) -> tuple[str, str]:
    if isinstance(value, Mapping):
        return ("mapping", source_payload_hash(dict(value)))
    return (type(value).__name__, str(value))


def _canonical_value(value: object) -> object:
    if isinstance(value, Enum):
        return _canonical_value(value.value)
    if isinstance(value, Decimal):
        return _number_text(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise EvidenceReleaseManifestError("manifest numbers must be finite")
        return _number_text(Decimal(str(value)))
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        items = [_canonical_value(item) for item in value]
        return sorted(items, key=_sort_key)
    if value is None or isinstance(value, str | int | bool):
        return value
    raise EvidenceReleaseManifestError(
        f"unsupported manifest value type: {type(value).__name__}"
    )


def _assert_secret_free(value: object, *, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _FORBIDDEN_KEY.search(key_text):
                raise EvidenceReleaseManifestError(
                    f"forbidden secret/source key in release manifest: {path}.{key_text}"
                )
            _assert_secret_free(item, path=f"{path}.{key_text}")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, item in enumerate(value):
            _assert_secret_free(item, path=f"{path}[{index}]")
        return
    if isinstance(value, str) and "://" in value:
        raise EvidenceReleaseManifestError(
            f"raw source URL is forbidden in release manifest: {path}"
        )


def _hash_file(path: Path) -> str:
    if not path.is_file():
        raise EvidenceReleaseManifestError(f"required release file is unavailable: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_file_manifests(
    package_root: Path,
    repository_root: Path | None = None,
) -> tuple[dict[str, tuple[Mapping[str, object], ...]], dict[str, tuple[str, ...]]]:
    repository_root = repository_root or package_root.parents[1]
    manifests: dict[str, tuple[Mapping[str, object], ...]] = {}
    missing: dict[str, tuple[str, ...]] = {}
    for lane_name in LANE_NAMES:
        rows: list[Mapping[str, object]] = []
        absent: list[str] = []
        for relative in sorted(_LANE_SOURCE_FILES[lane_name]):
            path = package_root / relative
            if not path.is_file():
                absent.append(f"source_file:{relative}")
                continue
            rows.append({"path": relative, "sha256": _hash_file(path)})
        for relative in sorted(_LANE_REPOSITORY_SOURCE_FILES.get(lane_name, ())):
            path = repository_root / relative
            if not path.is_file():
                absent.append(f"source_file:{relative}")
                continue
            rows.append({"path": relative, "sha256": _hash_file(path)})
        manifests[lane_name] = tuple(rows)
        missing[lane_name] = tuple(absent)
    return manifests, missing


def _runtime_manifest(
    row: object | None,
    fields: tuple[str, ...],
) -> Mapping[str, object] | None:
    if row is None:
        return None
    return {field: cast(object, getattr(row, field)) for field in fields}


def _observation_recovery_runtime_manifest(
    row: ObservationRecoveryRuntime | None,
) -> Mapping[str, object] | None:
    if row is None:
        return None
    return {
        "definition_version": row.definition_version,
        "definition_hash": row.definition_hash,
        "source_protocol": row.source_protocol,
        # Hash only: raw source URLs are deliberately forbidden in manifests.
        "source_endpoint_sha256": row.source_url_sha256,
        "parser_version": row.parser_version,
        "completeness_version": row.completeness_version,
        "retention_policy_version": row.retention_policy_version,
        "retention_policy_sha256": row.retention_policy_sha256,
    }


def _promotion_rows(manifest: EffectivePromotionManifest) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "activation_id": item.activation_id,
            "definition_hash": item.definition_hash,
            "decision_hash": item.decision_hash,
            "expert": item.key.expert,
            "expert_version": item.key.expert_version,
            "experiment_fingerprint": item.key.experiment_fingerprint,
            "horizon_bucket": item.key.horizon_bucket,
            "kind": item.key.kind,
            "legacy_expert_version": item.key.legacy_expert_version,
            "pipeline_cohort": item.key.pipeline_cohort,
            "station_id": item.key.station_id,
        }
        for item in manifest.promotions
    )


def _resolution_contract_semantic_profiles(
    contracts: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Collapse dated event contracts into their material resolution semantics.

    ``event_id``, the exact raw-rules hash, and the validated resolution date
    remain authoritative on every contract and downstream evidence row, but
    they are observations rather than release-definition identity.  New daily
    events must not reset an otherwise unchanged validation lane.
    """

    expected_fields = _RESOLUTION_CONTRACT_EVENT_FIELDS | frozenset(
        _RESOLUTION_CONTRACT_SEMANTIC_FIELDS
    )
    profiles_by_hash: dict[str, Mapping[str, object]] = {}
    for index, contract in enumerate(contracts):
        observed_fields = frozenset(str(key) for key in contract)
        if observed_fields != expected_fields:
            missing = sorted(expected_fields - observed_fields)
            unexpected = sorted(observed_fields - expected_fields)
            raise EvidenceReleaseManifestError(
                "resolution contract manifest fields are unclassified "
                f"at index {index}: missing={missing}, unexpected={unexpected}"
            )
        event_id = contract["event_id"]
        rules_hash = contract["rules_hash"]
        if not isinstance(event_id, str) or not event_id.strip():
            raise EvidenceReleaseManifestError(
                f"resolution contract event identity is invalid at index {index}"
            )
        if not isinstance(rules_hash, str) or _HEX_64.fullmatch(rules_hash) is None:
            raise EvidenceReleaseManifestError(
                f"resolution contract rules hash is invalid at index {index}"
            )
        resolution_date = contract["resolution_date"]
        if not isinstance(resolution_date, str):
            raise EvidenceReleaseManifestError(
                f"resolution contract resolution_date is invalid at index {index}"
            )
        try:
            parsed_resolution_date = date.fromisoformat(resolution_date)
        except ValueError as exc:
            raise EvidenceReleaseManifestError(
                f"resolution contract resolution_date is invalid at index {index}"
            ) from exc
        if parsed_resolution_date.isoformat() != resolution_date:
            raise EvidenceReleaseManifestError(
                f"resolution contract resolution_date is invalid at index {index}"
            )
        for field in (
            "source_family",
            "station_code",
            "local_timezone",
            "finalization_rule",
            "license_status",
        ):
            value = contract[field]
            if not isinstance(value, str) or not value.strip():
                raise EvidenceReleaseManifestError(
                    f"resolution contract {field} is invalid at index {index}"
                )
        for field in (
            "rules_template_sha256",
            "source_locator_sha256",
            "license_locator_sha256",
        ):
            value = contract[field]
            if value is None and field == "license_locator_sha256":
                continue
            if not isinstance(value, str) or _HEX_64.fullmatch(value) is None:
                raise EvidenceReleaseManifestError(
                    f"resolution contract {field} is invalid at index {index}"
                )
        source_family = contract["source_family"]
        if not isinstance(contract["storage_permitted"], bool):
            raise EvidenceReleaseManifestError(
                f"resolution contract storage_permitted is invalid at index {index}"
            )
        policy = (
            _RESOLUTION_LICENSE_POLICY.get(source_family)
            if isinstance(source_family, str)
            else None
        )
        if policy is None:
            raise EvidenceReleaseManifestError(
                f"resolution contract source_family is invalid at index {index}"
            )
        expected_license_status, expected_storage, license_required = policy
        if (
            contract["license_status"] != expected_license_status
            or contract["storage_permitted"] is not expected_storage
            or (contract["license_locator_sha256"] is not None) is not license_required
        ):
            raise EvidenceReleaseManifestError(
                f"resolution contract license policy is inconsistent at index {index}"
            )
        if contract["statistic"] not in {"high", "low"}:
            raise EvidenceReleaseManifestError(
                f"resolution contract statistic is invalid at index {index}"
            )
        if contract["source_unit"] not in {"C", "F"}:
            raise EvidenceReleaseManifestError(
                f"resolution contract source_unit is invalid at index {index}"
            )
        if contract["finalization_rule"] not in {
            "next_day_first_point",
            "source_finalized",
            "source_published",
        }:
            raise EvidenceReleaseManifestError(
                f"resolution contract finalization_rule is invalid at index {index}"
            )
        decimal_places = contract["decimal_places"]
        if (
            not isinstance(decimal_places, int)
            or isinstance(decimal_places, bool)
            or decimal_places not in {0, 1}
        ):
            raise EvidenceReleaseManifestError(
                f"resolution contract decimal_places is invalid at index {index}"
            )
        timezone = contract["local_timezone"]
        assert isinstance(timezone, str)
        try:
            ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise EvidenceReleaseManifestError(
                f"resolution contract local_timezone is invalid at index {index}"
            ) from exc
        profile = {
            field: contract[field] for field in _RESOLUTION_CONTRACT_SEMANTIC_FIELDS
        }
        canonical = _canonical_value(profile)
        assert isinstance(canonical, dict)
        profiles_by_hash[source_payload_hash(canonical)] = canonical
    return tuple(profiles_by_hash[key] for key in sorted(profiles_by_hash))


def _resolution_contract_station_mapping(
    profiles: Sequence[Mapping[str, object]],
    stations: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Bind every active resolution source to a matching active station."""

    stations_by_icao: dict[str, Mapping[str, object]] = {}
    for index, station in enumerate(stations):
        icao = station.get("icao")
        timezone = station.get("timezone")
        if not isinstance(icao, str) or not icao.strip():
            raise EvidenceReleaseManifestError(
                f"active station ICAO is invalid at index {index}"
            )
        if not isinstance(timezone, str) or not timezone.strip():
            raise EvidenceReleaseManifestError(
                f"active station timezone is invalid at index {index}"
            )
        if icao in stations_by_icao:
            raise EvidenceReleaseManifestError(
                f"active station ICAO is duplicated: {icao}"
            )
        stations_by_icao[icao] = station

    mappings_by_hash: dict[str, Mapping[str, object]] = {}
    for index, profile in enumerate(profiles):
        station_code = profile["station_code"]
        local_timezone = profile["local_timezone"]
        assert isinstance(station_code, str)
        assert isinstance(local_timezone, str)
        matched_station = stations_by_icao.get(station_code)
        if matched_station is None:
            raise EvidenceReleaseManifestError(
                "resolution contract references a non-active station "
                f"at profile {index}: {station_code}"
            )
        if matched_station["timezone"] != local_timezone:
            raise EvidenceReleaseManifestError(
                "resolution contract timezone differs from active station "
                f"at profile {index}: {station_code}"
            )
        mapping = {
            "local_timezone": local_timezone,
            "source_family": profile["source_family"],
            "source_locator_sha256": profile["source_locator_sha256"],
            "station_code": station_code,
            "statistic": profile["statistic"],
        }
        canonical = _canonical_value(mapping)
        assert isinstance(canonical, dict)
        mappings_by_hash[source_payload_hash(canonical)] = canonical
    return tuple(mappings_by_hash[key] for key in sorted(mappings_by_hash))


def _resolution_contract_manifest_row(
    row: ResolutionContract,
    *,
    market_dates: Sequence[date],
) -> Mapping[str, object]:
    """Reparse an immutable DB contract before admitting it to release identity."""

    try:
        statistic = WeatherKind(row.statistic)
        parsed = parse_resolution_contract(
            event_id=row.event_id,
            rules_raw=row.rules_raw,
            # The rule text itself is authoritative.  A separately supplied
            # Gamma locator may not override the URL embedded in that text.
            resolution_source=None,
            station_code=row.station_code,
            local_timezone=row.local_timezone,
            statistic=statistic,
        )
        ZoneInfo(row.local_timezone)
    except (ResolutionContractError, ValueError, ZoneInfoNotFoundError) as exc:
        raise EvidenceReleaseManifestError(
            f"active resolution contract {row.id} cannot be reparsed"
        ) from exc

    expected = {
        "rules_hash": parsed.rules_hash,
        "source_family": parsed.source_family,
        "source_url": parsed.source_url,
        "station_code": parsed.station_code,
        "local_timezone": parsed.local_timezone,
        "statistic": parsed.statistic.value,
        "source_unit": parsed.source_unit,
        "decimal_places": parsed.decimal_places,
        "finalization_rule": parsed.finalization_rule,
        "license_status": parsed.license_status,
        "storage_permitted": parsed.storage_permitted,
        "license_url": parsed.license_url,
        "rules_raw": parsed.rules_raw,
    }
    mismatches = tuple(
        field for field, value in expected.items() if getattr(row, field) != value
    )
    if mismatches:
        raise EvidenceReleaseManifestError(
            f"active resolution contract {row.id} differs from parsed rules: "
            f"{','.join(mismatches)}"
        )

    distinct_market_dates = tuple(sorted(set(market_dates)))
    if not distinct_market_dates:
        raise EvidenceReleaseManifestError(
            f"active resolution contract {row.id} has no database markets"
        )
    if len(distinct_market_dates) != 1:
        rendered_dates = ",".join(value.isoformat() for value in distinct_market_dates)
        raise EvidenceReleaseManifestError(
            f"active resolution contract {row.id} has conflicting market dates: "
            f"{rendered_dates}"
        )
    if distinct_market_dates[0] != parsed.resolution_date:
        raise EvidenceReleaseManifestError(
            f"active resolution contract {row.id} rules date does not match "
            "database market date: "
            f"rules={parsed.resolution_date.isoformat()} "
            f"market={distinct_market_dates[0].isoformat()}"
        )

    return {
        "decimal_places": parsed.decimal_places,
        "event_id": parsed.event_id,
        "finalization_rule": parsed.finalization_rule,
        "license_locator_sha256": (
            hashlib.sha256(parsed.license_url.encode("utf-8")).hexdigest()
            if parsed.license_url is not None
            else None
        ),
        "license_status": parsed.license_status,
        "local_timezone": parsed.local_timezone,
        "resolution_date": parsed.resolution_date.isoformat(),
        "rules_hash": parsed.rules_hash,
        "rules_template_sha256": parsed.rules_template_sha256,
        "source_family": parsed.source_family,
        "source_locator_sha256": hashlib.sha256(
            parsed.source_url.encode("utf-8")
        ).hexdigest(),
        "source_unit": parsed.source_unit,
        "station_code": parsed.station_code,
        "statistic": parsed.statistic.value,
        "storage_permitted": parsed.storage_permitted,
    }


async def _database_snapshot(
    session: AsyncSession,
    *,
    promotions: EffectivePromotionManifest,
) -> EvidenceReleaseDatabaseSnapshot:
    alembic_heads = tuple(
        str(value)
        for value in (
            await session.scalars(
                text("SELECT version_num FROM alembic_version ORDER BY version_num")
            )
        ).all()
    )
    control = await session.scalar(
        select(EvidenceContinuityRuntime).where(EvidenceContinuityRuntime.id == 1)
    )
    prospective = await session.scalar(
        select(ProspectiveValidationRuntime).where(ProspectiveValidationRuntime.id == 1)
    )
    forecast = await session.scalar(
        select(ForecastProvenanceRuntime).where(ForecastProvenanceRuntime.id == 1)
    )
    recovery = await session.scalar(
        select(ObservationRecoveryRuntime).where(ObservationRecoveryRuntime.id == 1)
    )
    execution = await session.scalar(
        select(ExecutionCostRuntime).where(ExecutionCostRuntime.id == 1)
    )
    official = await session.scalar(
        select(OfficialResolutionEvidenceRuntime).where(
            OfficialResolutionEvidenceRuntime.id == 1
        )
    )
    stations = (
        await session.scalars(
            select(Station).where(Station.active.is_(True)).order_by(Station.icao)
        )
    ).all()
    contract_market_rows = (
        await session.execute(
            select(ResolutionContract, Market.market_date)
            .outerjoin(Market, Market.event_id == ResolutionContract.event_id)
            .where(ResolutionContract.retired_at.is_(None))
            .order_by(
                ResolutionContract.event_id,
                ResolutionContract.rules_hash,
                Market.market_date,
                Market.id,
            )
        )
    ).all()
    contracts_by_id: dict[int, ResolutionContract] = {}
    market_dates_by_contract: dict[int, set[date]] = {}
    for contract, market_date in contract_market_rows:
        contracts_by_id[contract.id] = contract
        dates = market_dates_by_contract.setdefault(contract.id, set())
        if market_date is not None:
            dates.add(market_date)
    contracts = tuple(
        sorted(
            contracts_by_id.values(),
            key=lambda row: (row.event_id, row.rules_hash),
        )
    )
    return EvidenceReleaseDatabaseSnapshot(
        alembic_heads=alembic_heads,
        evidence_continuity_runtime=_runtime_manifest(
            control, ("definition_version", "definition_hash")
        ),
        prospective_validation_runtime=_runtime_manifest(
            prospective, ("definition_version", "definition_hash")
        ),
        forecast_provenance_runtime=_runtime_manifest(
            forecast, ("definition_version", "definition_hash")
        ),
        observation_recovery_runtime=_observation_recovery_runtime_manifest(recovery),
        execution_cost_runtime=_runtime_manifest(
            execution,
            (
                "definition_version",
                "definition_hash",
                "paper_execution_evidence",
                "live_reconciliation_evidence",
                "postfill_mark_protocol",
                "mark_horizons_seconds",
                "mark_grace_seconds",
                "live_fill_snapshot_max_age_seconds",
            ),
        ),
        official_resolution_runtime=_runtime_manifest(
            official,
            (
                "definition_version",
                "definition_hash",
                "license_protocol",
                "response_protocol",
                "extraction_protocol",
            ),
        ),
        active_stations=tuple(
            {
                "city": row.city,
                "icao": row.icao,
                "iem_network": row.iem_network,
                "iem_station_id": row.iem_station_id,
                "latitude": row.lat,
                "longitude": row.lon,
                "stage": row.stage,
                "timezone": row.tz,
            }
            for row in stations
        ),
        active_resolution_contracts=tuple(
            _resolution_contract_manifest_row(
                row,
                market_dates=tuple(sorted(market_dates_by_contract[row.id])),
            )
            for row in contracts
        ),
        effective_promotions=_promotion_rows(promotions),
    )


def _settings_manifest(settings: Settings, lane_name: str) -> Mapping[str, object]:
    manifest = {name: getattr(settings, name) for name in _LANE_CONFIG[lane_name]}
    if lane_name == "predictive_validation":
        manifest["iem_asos_endpoint_sha256"] = hashlib.sha256(
            settings.iem_asos_url.encode("utf-8")
        ).hexdigest()
    return manifest


def _selection_policy_manifest() -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "excluded_horizons": policy.excluded_horizons,
            "excluded_sides": policy.excluded_sides,
            "max_predicted_edge": policy.max_predicted_edge,
            "name": policy.name,
            "policy_hash": selection_policy_hash(policy),
        }
        for policy in SHADOW_SELECTION_POLICIES
    )


def _missing_requirements(
    lane_name: str,
    snapshot: EvidenceReleaseDatabaseSnapshot,
    source_missing: tuple[str, ...],
) -> tuple[str, ...]:
    missing = list(source_missing)
    if snapshot.evidence_continuity_runtime is None:
        missing.append("evidence_continuity_runtime")
    if lane_name == "predictive_validation":
        if snapshot.prospective_validation_runtime is None:
            missing.append("prospective_validation_runtime")
        if snapshot.forecast_provenance_runtime is None:
            missing.append("forecast_provenance_runtime")
        if snapshot.observation_recovery_runtime is None:
            missing.append("observation_recovery_runtime")
        if snapshot.official_resolution_runtime is None:
            missing.append("official_resolution_runtime")
        if not snapshot.active_resolution_contracts:
            missing.append("active_resolution_contracts")
        if not snapshot.active_stations:
            missing.append("active_stations")
    elif lane_name == "execution_evidence" and snapshot.execution_cost_runtime is None:
        missing.append("execution_cost_runtime")
    elif lane_name == "station_lifecycle":
        if not snapshot.active_stations:
            missing.append("active_stations")
        if not snapshot.active_resolution_contracts:
            missing.append("active_resolution_contracts")
    elif lane_name == "official_resolution_truth":
        if snapshot.official_resolution_runtime is None:
            missing.append("official_resolution_runtime")
        if not snapshot.active_resolution_contracts:
            missing.append("active_resolution_contracts")
    return tuple(sorted(missing))


def _lane_specific_manifest(
    lane_name: str,
    snapshot: EvidenceReleaseDatabaseSnapshot,
    *,
    pipeline: Mapping[str, object],
) -> Mapping[str, object]:
    contract_profiles = _resolution_contract_semantic_profiles(
        snapshot.active_resolution_contracts
    )
    contract_semantics = {
        "profiles": contract_profiles,
        "protocol": RESOLUTION_CONTRACT_SEMANTICS_PROTOCOL,
    }
    if lane_name == "predictive_validation":
        return {
            "pipeline": pipeline,
            "prospective_validation_runtime": snapshot.prospective_validation_runtime,
            "forecast_provenance_runtime": snapshot.forecast_provenance_runtime,
            "observation_recovery_runtime": snapshot.observation_recovery_runtime,
            "official_resolution_runtime": snapshot.official_resolution_runtime,
            "resolution_contract_semantics": contract_semantics,
            "active_stations": snapshot.active_stations,
            "effective_promotions": snapshot.effective_promotions,
        }
    if lane_name == "selection_policy":
        return {"policies": _selection_policy_manifest()}
    if lane_name == "execution_evidence":
        return {"execution_cost_runtime": snapshot.execution_cost_runtime}
    if lane_name == "operational_legacy":
        return {
            "inventory_semantics": (
                "monitor-cancel-settle-reconcile-existing-state-without-evidence-relabel-v1"
            )
        }
    if lane_name == "station_lifecycle":
        return {
            "active_stations": snapshot.active_stations,
            # The validated profiles bind each resolution source and its
            # timezone/statistic semantics to a station without including
            # daily event ids.  A source/station remap forks this lane, while
            # ordinary next-day inventory growth remains continuous.
            "resolution_contract_semantics": contract_semantics,
            "resolution_source_station_mapping": {
                "mappings": _resolution_contract_station_mapping(
                    contract_profiles,
                    snapshot.active_stations,
                ),
                "protocol": RESOLUTION_CONTRACT_STATION_MAPPING_PROTOCOL,
            },
        }
    return {
        "official_resolution_runtime": snapshot.official_resolution_runtime,
        "resolution_contract_semantics": contract_semantics,
    }


def compose_evidence_release_manifest(
    settings: Settings,
    snapshot: EvidenceReleaseDatabaseSnapshot,
    *,
    material_fingerprint: str,
    activation_manifest_hash: str,
    uv_lock_sha256: str,
    lane_source_files: Mapping[str, tuple[Mapping[str, object], ...]],
    lane_source_missing: Mapping[str, tuple[str, ...]] | None = None,
) -> EvidenceReleaseManifest:
    """Purely compose all six lane identities and the image-bound global manifest."""

    if snapshot.alembic_heads != (ALEMBIC_HEAD,):
        observed = ", ".join(snapshot.alembic_heads) if snapshot.alembic_heads else "none"
        raise EvidenceReleaseManifestError(
            f"database Alembic head mismatch: expected {ALEMBIC_HEAD}, observed {observed}"
        )
    if settings.release_image_digest == _ZERO_HASH:
        raise EvidenceReleaseManifestError("release image digest must be nonzero")
    if _HEX_64.fullmatch(settings.release_image_digest) is None:
        raise EvidenceReleaseManifestError("release image digest is not canonical SHA-256")
    if _HEX_16.fullmatch(material_fingerprint) is None:
        raise EvidenceReleaseManifestError("material fingerprint is not canonical")
    if _HEX_64.fullmatch(activation_manifest_hash) is None:
        raise EvidenceReleaseManifestError("activation manifest hash is not canonical")
    if _HEX_64.fullmatch(uv_lock_sha256) is None:
        raise EvidenceReleaseManifestError("uv.lock hash is not canonical SHA-256")
    if set(lane_source_files) != set(LANE_NAMES):
        raise EvidenceReleaseManifestError("source manifest must cover all continuity lanes")

    cohort = probability_pipeline_release_cohort(
        settings.probability_pipeline_version,
        material_fingerprint,
        activation_manifest_hash,
    )
    pipeline: Mapping[str, object] = {
        "activation_manifest_hash": activation_manifest_hash,
        "cohort": cohort,
        "material_fingerprint": material_fingerprint,
        "version": settings.probability_pipeline_version,
    }
    source_missing = lane_source_missing or {name: () for name in LANE_NAMES}
    lanes: list[EvidenceReleaseLaneManifest] = []
    for lane_name_text in LANE_NAMES:
        lane_name = cast(EvidenceLaneName, lane_name_text)
        missing = _missing_requirements(
            lane_name,
            snapshot,
            tuple(source_missing.get(lane_name, ())),
        )
        payload = _canonical_value(
            {
                "config": _settings_manifest(settings, lane_name),
                "lane": lane_name,
                "lane_data": _lane_specific_manifest(
                    lane_name,
                    snapshot,
                    pipeline=pipeline,
                ),
                "protocol": EVIDENCE_LANE_MANIFEST_PROTOCOL,
                "requirements": {"complete": not missing, "missing": missing},
                "source_files": lane_source_files[lane_name],
            }
        )
        assert isinstance(payload, dict)
        _assert_secret_free(payload)
        lanes.append(
            EvidenceReleaseLaneManifest(
                lane_name=lane_name,
                manifest=payload,
                identity_hash=source_payload_hash(payload),
                missing_requirements=missing,
            )
        )

    global_payload = _canonical_value(
        {
            "alembic_head": snapshot.alembic_heads[0],
            "control_runtime": snapshot.evidence_continuity_runtime,
            "image_digest": settings.release_image_digest,
            "lane_identity_hashes": [
                {"identity_hash": lane.identity_hash, "lane": lane.lane_name}
                for lane in lanes
            ],
            "pipeline": pipeline,
            "protocol": EVIDENCE_RELEASE_MANIFEST_PROTOCOL,
            "uv_lock_sha256": uv_lock_sha256,
        }
    )
    assert isinstance(global_payload, dict)
    _assert_secret_free(global_payload)
    return EvidenceReleaseManifest(
        manifest=global_payload,
        manifest_hash=source_payload_hash(global_payload),
        lanes=tuple(lanes),
    )


async def build_evidence_release_manifest(
    settings: Settings,
    session: AsyncSession,
) -> EvidenceReleaseManifest:
    """Read the authoritative DB/source state and build a secret-free release manifest."""

    fingerprint = probability_pipeline_fingerprint(settings)
    promotions = await load_effective_accuracy_promotion_manifest(
        session,
        pipeline_fingerprint=fingerprint,
        current_pipeline_base_cohort=probability_pipeline_base_cohort(
            settings,
            fingerprint=fingerprint,
        ),
    )
    snapshot = await _database_snapshot(session, promotions=promotions)
    source_files, source_missing = _source_file_manifests(_PACKAGE_ROOT)
    return compose_evidence_release_manifest(
        settings,
        snapshot,
        material_fingerprint=fingerprint,
        activation_manifest_hash=promotions.manifest_hash,
        uv_lock_sha256=_hash_file(_REPOSITORY_ROOT / "uv.lock"),
        lane_source_files=source_files,
        lane_source_missing=source_missing,
    )


def decide_evidence_lane_actions(
    candidate: EvidenceReleaseManifest,
    parent_identity_hashes: Mapping[str, str] | None,
) -> tuple[EvidenceLaneDecision, ...]:
    """Make conservative, pure continuity decisions against one reviewed parent."""

    decisions: list[EvidenceLaneDecision] = []
    for lane in candidate.lanes:
        if lane.missing_requirements:
            missing = ", ".join(lane.missing_requirements)
            action: EvidenceLaneAction = "freeze"
            rationale = f"Required lane identity data is unavailable: {missing}."
        elif parent_identity_hashes is None:
            action = "fork"
            rationale = "First reviewed activation opens a zero-backfill lane boundary."
        else:
            parent_hash = parent_identity_hashes.get(lane.lane_name)
            if parent_hash is None or _HEX_64.fullmatch(parent_hash) is None:
                action = "freeze"
                rationale = "Parent lane identity is missing or invalid; continuity is unproven."
            elif parent_hash == lane.identity_hash:
                action = "continue"
                rationale = "Candidate identity exactly matches the reviewed parent lane."
            else:
                action = "fork"
                rationale = "Material lane identity changed; open a zero-backfill boundary."
        decisions.append(
            EvidenceLaneDecision(
                lane_name=lane.lane_name,
                action=action,
                identity_hash=lane.identity_hash,
                manifest=lane.manifest,
                rationale=rationale,
            )
        )
    return tuple(decisions)


__all__ = [
    "ALEMBIC_HEAD",
    "EVIDENCE_LANE_MANIFEST_PROTOCOL",
    "EVIDENCE_RELEASE_MANIFEST_PROTOCOL",
    "LANE_NAMES",
    "EvidenceLaneDecision",
    "EvidenceReleaseDatabaseSnapshot",
    "EvidenceReleaseLaneManifest",
    "EvidenceReleaseManifest",
    "EvidenceReleaseManifestError",
    "build_evidence_release_manifest",
    "compose_evidence_release_manifest",
    "decide_evidence_lane_actions",
]
