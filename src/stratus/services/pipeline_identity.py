from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from stratus.config import Settings

# Conservative by design: a source-only refactor can create a new evidence
# cohort, but a material model change can never silently inherit old Brier.
_PIPELINE_SOURCE_FILES = (
    "collectors/clob.py",
    "collectors/gamma.py",
    "collectors/iem.py",
    "collectors/nws.py",
    "collectors/open_meteo.py",
    "collectors/open_meteo_ensemble.py",
    "collectors/open_meteo_previous_runs.py",
    "db/models.py",
    "jobs/bootstrap_previous_runs.py",
    "jobs/fetch_ensemble.py",
    "jobs/fetch_forecasts.py",
    "jobs/fetch_observations.py",
    "jobs/bootstrap_iem.py",
    "jobs/finalize_daily_actuals.py",
    "jobs/recover_observations.py",
    "jobs/recalibrate_emos.py",
    "jobs/evaluate_promotion_trials.py",
    "jobs/generate_censored_challenger.py",
    "jobs/generate_weighted_ensemble.py",
    "jobs/recompute_probs.py",
    "repositories/accuracy_promotions.py",
    "repositories/accuracy.py",
    "repositories/calibration.py",
    "repositories/clob_snapshots.py",
    "repositories/gamma_outcomes.py",
    "repositories/daily_actuals.py",
    "repositories/historical_forecasts.py",
    "repositories/model_probs.py",
    "repositories/observation_recovery.py",
    "repositories/official_resolution.py",
    "services/accuracy_models.py",
    "services/clob_evidence.py",
    "services/conformal_probability.py",
    "services/emos.py",
    "services/event_probability_coherence.py",
    "services/forecast_collection.py",
    "services/forecast_provenance.py",
    "services/hierarchical_probability.py",
    "services/daily_actuals.py",
    "services/model_catalog.py",
    "services/observation_recovery.py",
    "services/official_resolution.py",
    "services/official_resolution_artifacts.py",
    "services/pipeline_identity.py",
    "services/probability.py",
    "services/promotion_protocol.py",
    "services/regime_probability.py",
    "services/sealed_accuracy.py",
    "services/settlement_calibration.py",
    "services/trajectory_probability.py",
)

_MATERIAL_SETTINGS = (
    "probability_pipeline_version",
    "forecast_models",
    "ensemble_models",
    "calibration_window_days",
    "calibration_min_samples",
    "shrinkage_prior_samples",
    "previous_run_horizon_days",
    "iem_asos_url",
    "daily_actual_recovery_enabled",
    "daily_actual_recovery_lookback_days",
    "daily_actual_recovery_max_requests_per_run",
    "daily_actual_recovery_retry_hours",
    "daily_actual_recovery_local_hour",
    "daily_actual_recovery_max_response_bytes",
    "daily_actual_min_samples",
    "daily_actual_boundary_tolerance_minutes",
    "daily_actual_max_gap_minutes",
    "iem_recovery_terms_policy_hash",
)

_PROMOTION_SUFFIX_RE = re.compile(r"\+promotions-[0-9a-f]{16}\Z")


def probability_pipeline_fingerprint(settings: Settings) -> str:
    """Hash model source and material probability settings.

    The fingerprint is intentionally stricter than a hand-maintained semantic
    version: it prevents a rebuilt probability implementation from inheriting
    another implementation's prospective evidence by accident.
    """
    digest = hashlib.sha256()
    package_root = Path(__file__).resolve().parents[1]
    for relative in _PIPELINE_SOURCE_FILES:
        path = package_root / relative
        if not path.is_file():
            raise RuntimeError(f"probability pipeline source is unavailable: {relative}")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")

    material = {
        name: getattr(settings, name)
        for name in _MATERIAL_SETTINGS
    }
    material.update(
        {
            name: getattr(settings, name)
            for name in type(settings).model_fields
            if name.startswith("accuracy_")
        }
    )
    digest.update(
        json.dumps(
            material,
            default=str,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    return digest.hexdigest()[:16]


def probability_pipeline_metadata(
    settings: Settings,
    *,
    activation_manifest_hash: str | None = None,
) -> dict[str, str]:
    fingerprint = probability_pipeline_fingerprint(settings)
    version = settings.probability_pipeline_version
    cohort = f"{version}+{fingerprint}"
    metadata = {
        "version": version,
        "fingerprint": fingerprint,
        "cohort": cohort,
    }
    if activation_manifest_hash is None:
        return metadata
    if re.fullmatch(r"[0-9a-fA-F]{64}", activation_manifest_hash) is None:
        raise ValueError("activation_manifest_hash must be a 64-character SHA-256 hex digest")
    normalized_manifest_hash = activation_manifest_hash.lower()
    metadata["activation_manifest_hash"] = normalized_manifest_hash
    metadata["cohort"] = f"{cohort}+promotions-{normalized_manifest_hash[:16]}"
    return metadata


def probability_pipeline_base_cohort(
    settings: Settings,
    *,
    fingerprint: str | None = None,
) -> str:
    """Return the exact current cohort before an activation-manifest suffix."""

    current_fingerprint = fingerprint or probability_pipeline_fingerprint(settings)
    return f"{settings.probability_pipeline_version}+{current_fingerprint}"


def pipeline_cohort_matches_base(*, stored: str, current_base: str) -> bool:
    """Require an exact base identity, allowing only a valid manifest suffix."""

    if stored == current_base:
        return True
    if not stored.startswith(current_base):
        return False
    return _PROMOTION_SUFFIX_RE.fullmatch(stored[len(current_base) :]) is not None
