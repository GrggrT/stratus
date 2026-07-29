from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from stratus.config import Settings
from stratus.repositories.evidence_continuity import (
    acquire_probability_pipeline_release_lock,
    current_evidence_continuity_lanes,
    current_probability_pipeline_release_epoch,
    current_probability_pipeline_release_plan_record,
)
from stratus.services.evidence_release_manifest import (
    EvidenceReleaseManifestError,
    build_evidence_release_manifest,
)


@dataclass(frozen=True, slots=True)
class ProbabilityReleaseGate:
    allowed: bool
    reason: str
    candidate_cohort: str
    active_cohort: str | None
    candidate_image_digest: str
    active_image_digest: str | None
    candidate_release_manifest_hash: str | None
    active_release_manifest_hash: str | None
    lane_identity_mismatches: tuple[str, ...]
    frozen_lanes: tuple[str, ...]


def _candidate_cohort(manifest: Mapping[str, object]) -> str:
    pipeline = manifest.get("pipeline")
    if not isinstance(pipeline, Mapping):
        raise EvidenceReleaseManifestError("release manifest pipeline identity is missing")
    cohort = pipeline.get("cohort")
    if not isinstance(cohort, str) or not cohort:
        raise EvidenceReleaseManifestError("release manifest cohort identity is missing")
    return cohort


def _gate(
    *,
    allowed: bool,
    reason: str,
    candidate_cohort: str,
    active_cohort: str | None,
    settings: Settings,
    active_image_digest: str | None,
    candidate_release_manifest_hash: str | None,
    active_release_manifest_hash: str | None,
    lane_identity_mismatches: tuple[str, ...] = (),
    frozen_lanes: tuple[str, ...] = (),
) -> ProbabilityReleaseGate:
    return ProbabilityReleaseGate(
        allowed=allowed,
        reason=reason,
        candidate_cohort=candidate_cohort,
        active_cohort=active_cohort,
        candidate_image_digest=settings.release_image_digest,
        active_image_digest=active_image_digest,
        candidate_release_manifest_hash=candidate_release_manifest_hash,
        active_release_manifest_hash=active_release_manifest_hash,
        lane_identity_mismatches=lane_identity_mismatches,
        frozen_lanes=frozen_lanes,
    )


async def probability_release_gate(
    session: AsyncSession,
    settings: Settings,
    *,
    authoritative: bool = True,
) -> ProbabilityReleaseGate:
    """Verify the running release and all six lanes under one DB lock."""

    # Promotion activation and release activation take this same lock.  The
    # manifest, active epoch, plan, and lane set are therefore one coherent
    # transaction-time view rather than a mixture around a concurrent cutover.
    if authoritative:
        await acquire_probability_pipeline_release_lock(session)
    active = await current_probability_pipeline_release_epoch(session)
    active_plan = await current_probability_pipeline_release_plan_record(session)
    active_lanes = await current_evidence_continuity_lanes(session)
    try:
        candidate = await build_evidence_release_manifest(settings, session)
        candidate_cohort = _candidate_cohort(candidate.manifest)
    except EvidenceReleaseManifestError:
        return _gate(
            allowed=False,
            reason="evidence_continuity_integrity_error",
            candidate_cohort="",
            active_cohort=active.cohort if active is not None else None,
            settings=settings,
            active_image_digest=active.image_digest if active is not None else None,
            candidate_release_manifest_hash=None,
            active_release_manifest_hash=(
                active_plan.release_manifest_hash if active_plan is not None else None
            ),
        )
    if active is None:
        return _gate(
            allowed=False,
            reason="evidence_continuity_unactivated",
            candidate_cohort=candidate_cohort,
            active_cohort=None,
            settings=settings,
            active_image_digest=None,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=None,
        )
    if active_plan is None or len(active_lanes) != 6:
        return _gate(
            allowed=False,
            reason="evidence_continuity_integrity_error",
            candidate_cohort=candidate_cohort,
            active_cohort=active.cohort,
            settings=settings,
            active_image_digest=active.image_digest,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=(
                active_plan.release_manifest_hash if active_plan is not None else None
            ),
        )
    candidate_hashes = candidate.lane_identity_hashes
    active_by_name = {lane.lane_name: lane for lane in active_lanes}
    expected_names = set(candidate_hashes)
    if set(active_by_name) != expected_names:
        return _gate(
            allowed=False,
            reason="evidence_continuity_integrity_error",
            candidate_cohort=candidate_cohort,
            active_cohort=active.cohort,
            settings=settings,
            active_image_digest=active.image_digest,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=active_plan.release_manifest_hash,
        )
    frozen = tuple(
        sorted(
            name
            for name, lane in active_by_name.items()
            if lane.action not in {"continue", "fork"}
        )
    )
    if frozen:
        return _gate(
            allowed=False,
            reason="evidence_lane_frozen",
            candidate_cohort=candidate_cohort,
            active_cohort=active.cohort,
            settings=settings,
            active_image_digest=active.image_digest,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=active_plan.release_manifest_hash,
            frozen_lanes=frozen,
        )
    mismatches = tuple(
        sorted(
            name
            for name, identity_hash in candidate_hashes.items()
            if active_by_name[name].identity_hash != identity_hash
        )
    )
    if mismatches:
        return _gate(
            allowed=False,
            reason="evidence_lane_identity_mismatch",
            candidate_cohort=candidate_cohort,
            active_cohort=active.cohort,
            settings=settings,
            active_image_digest=active.image_digest,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=active_plan.release_manifest_hash,
            lane_identity_mismatches=mismatches,
        )
    if (
        active.cohort != candidate_cohort
        or active.image_digest != settings.release_image_digest
        or active_plan.release_manifest_hash != candidate.manifest_hash
    ):
        return _gate(
            allowed=False,
            reason="evidence_release_identity_mismatch",
            candidate_cohort=candidate_cohort,
            active_cohort=active.cohort,
            settings=settings,
            active_image_digest=active.image_digest,
            candidate_release_manifest_hash=candidate.manifest_hash,
            active_release_manifest_hash=active_plan.release_manifest_hash,
        )
    return _gate(
        allowed=True,
        reason="evidence_continuity_match",
        candidate_cohort=candidate_cohort,
        active_cohort=active.cohort,
        settings=settings,
        active_image_digest=active.image_digest,
        candidate_release_manifest_hash=candidate.manifest_hash,
        active_release_manifest_hash=active_plan.release_manifest_hash,
    )


__all__ = ["ProbabilityReleaseGate", "probability_release_gate"]
