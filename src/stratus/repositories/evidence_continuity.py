from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    EvidenceContinuityLane,
    ProbabilityPipelineReleaseEpoch,
    ProbabilityPipelineReleasePlanRecord,
)
from stratus.services.execution_cost import source_payload_hash

RELEASE_PLAN_PROTOCOL = "evidence-release-plan-v2"
RELEASE_RECORD_PROTOCOL = "probability-pipeline-release-record-v1"
PROBABILITY_PIPELINE_RELEASE_LOCK_KEY = "stratus:probability-pipeline-release-epoch-v1"
_ZERO_HASH = "0" * 64
_VERSION_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_HEX_16_RE = re.compile(r"^[0-9a-f]{16}$")
_HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_REASON_LENGTH = 2_000
_FORBIDDEN_MANIFEST_KEY_PARTS = (
    "secret",
    "password",
    "privatekey",
    "credential",
    "apikey",
    "bearer",
)


class ProbabilityPipelineReleaseIntegrityError(RuntimeError):
    """The requested release would break the immutable cohort chain."""


async def acquire_probability_pipeline_release_lock(session: AsyncSession) -> None:
    """Serialize material promotion changes, release activation, and intent admission."""

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))").bindparams(
            key=PROBABILITY_PIPELINE_RELEASE_LOCK_KEY
        )
    )


@dataclass(frozen=True, slots=True)
class EvidenceLanePlan:
    lane_name: str
    action: str
    identity_hash: str
    manifest: Mapping[str, Any]
    rationale: str


@dataclass(frozen=True, slots=True)
class ProbabilityPipelineReleasePlan:
    pipeline_version: str
    material_fingerprint: str
    activation_manifest_hash: str
    cohort: str
    image_digest: str
    previous_epoch_id: int | None
    release_manifest: Mapping[str, Any]
    release_manifest_hash: str
    lane_plans: tuple[EvidenceLanePlan, ...]
    lane_manifest_hash: str
    reviewed_by: str
    approval_reason: str
    plan_hash: str


def _require_hex(value: str, *, length: int, field: str) -> str:
    pattern = _HEX_16_RE if length == 16 else _HEX_64_RE
    if pattern.fullmatch(value) is None:
        raise ValueError(f"{field} must be {length} lowercase hexadecimal characters")
    return value


def _approval_reason(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) < 16
        or len(normalized) > _MAX_REASON_LENGTH
        or len(normalized.split()) < 3
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError(
            "approval_reason must be 16-2000 characters, contain at least three words, "
            "and contain no control characters"
        )
    return normalized


def _reviewed_by(value: str) -> str:
    normalized = value.strip()
    if (
        len(normalized) < 3
        or len(normalized) > 128
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
    ):
        raise ValueError("reviewed_by must be 3-128 characters without control characters")
    return normalized


def _contains_forbidden_manifest_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower().replace("_", "").replace("-", "")
            if any(part in normalized for part in _FORBIDDEN_MANIFEST_KEY_PARTS):
                return True
            if _contains_forbidden_manifest_key(nested):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_manifest_key(item) for item in value)
    return False


def probability_pipeline_release_cohort(
    pipeline_version: str,
    material_fingerprint: str,
    activation_manifest_hash: str,
) -> str:
    """Build the only cohort identity admitted by revision 0040."""

    if _VERSION_RE.fullmatch(pipeline_version) is None:
        raise ValueError("pipeline_version is not a canonical lowercase release identifier")
    fingerprint = _require_hex(
        material_fingerprint,
        length=16,
        field="material_fingerprint",
    )
    manifest_hash = _require_hex(
        activation_manifest_hash,
        length=64,
        field="activation_manifest_hash",
    )
    cohort = f"{pipeline_version}+{fingerprint}+promotions-{manifest_hash[:16]}"
    if len(cohort) > 128:
        raise ValueError("cohort exceeds the execution evidence identity limit")
    return cohort


def _hex_text(value: str) -> str:
    return value.encode("utf-8").hex()


def probability_pipeline_release_plan_hash(
    *,
    pipeline_version: str,
    material_fingerprint: str,
    activation_manifest_hash: str,
    cohort: str,
    image_digest: str,
    previous_epoch_id: int | None,
    release_manifest_hash: str,
    lane_manifest_hash: str,
    reviewed_by: str,
    approval_reason: str,
) -> str:
    """Hash the exact candidate plan using the database's canonical encoding."""

    expected_cohort = probability_pipeline_release_cohort(
        pipeline_version,
        material_fingerprint,
        activation_manifest_hash,
    )
    if cohort != expected_cohort:
        raise ValueError("cohort does not match the exact release identity")
    digest = _require_hex(image_digest, length=64, field="image_digest")
    release_hash = _require_hex(
        release_manifest_hash,
        length=64,
        field="release_manifest_hash",
    )
    lane_hash = _require_hex(
        lane_manifest_hash,
        length=64,
        field="lane_manifest_hash",
    )
    reviewer = _reviewed_by(reviewed_by)
    reason = _approval_reason(approval_reason)
    if previous_epoch_id is not None and previous_epoch_id <= 0:
        raise ValueError("previous_epoch_id must be positive when present")
    payload = "|".join(
        (
            RELEASE_PLAN_PROTOCOL,
            _hex_text(pipeline_version),
            material_fingerprint,
            activation_manifest_hash,
            _hex_text(cohort),
            digest,
            str(previous_epoch_id) if previous_epoch_id is not None else "-",
            release_hash,
            lane_hash,
            _hex_text(reviewer),
            _hex_text(reason),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_probability_pipeline_release_plan(
    *,
    pipeline_version: str,
    material_fingerprint: str,
    activation_manifest_hash: str,
    image_digest: str,
    previous_epoch_id: int | None,
    release_manifest: Mapping[str, Any],
    lane_plans: tuple[EvidenceLanePlan, ...],
    reviewed_by: str,
    approval_reason: str,
) -> ProbabilityPipelineReleasePlan:
    """Create a pure, content-addressed plan without touching the database."""

    reason = _approval_reason(approval_reason)
    reviewer = _reviewed_by(reviewed_by)
    cohort = probability_pipeline_release_cohort(
        pipeline_version,
        material_fingerprint,
        activation_manifest_hash,
    )
    normalized_lanes = tuple(sorted(lane_plans, key=lambda lane: lane.lane_name))
    expected_names = {
        "predictive_validation",
        "selection_policy",
        "execution_evidence",
        "operational_legacy",
        "station_lifecycle",
        "official_resolution_truth",
    }
    if {lane.lane_name for lane in normalized_lanes} != expected_names or len(
        normalized_lanes
    ) != len(expected_names):
        raise ValueError("lane_plans must contain each required continuity lane exactly once")
    lane_values: list[dict[str, Any]] = []
    for lane in normalized_lanes:
        if lane.action not in {"continue", "fork", "freeze", "not_applicable"}:
            raise ValueError(f"unsupported continuity action for {lane.lane_name}")
        manifest = dict(lane.manifest)
        if _contains_forbidden_manifest_key(manifest):
            raise ValueError(
                f"lane manifest contains a forbidden secret-like key: {lane.lane_name}"
            )
        identity_hash = source_payload_hash(manifest)
        if not hmac.compare_digest(identity_hash, lane.identity_hash):
            raise ValueError(f"identity hash mismatch for lane {lane.lane_name}")
        rationale = lane.rationale.strip()
        if not 8 <= len(rationale) <= 1_000:
            raise ValueError(f"lane rationale is invalid for {lane.lane_name}")
        lane_values.append(
            {
                "lane_name": lane.lane_name,
                "action": lane.action,
                "identity_hash": identity_hash,
                "manifest": manifest,
                "rationale": rationale,
            }
        )
    normalized_manifest = dict(release_manifest)
    if _contains_forbidden_manifest_key(normalized_manifest):
        raise ValueError("release manifest contains a forbidden secret-like key")
    release_manifest_hash = source_payload_hash(normalized_manifest)
    lane_manifest_hash = source_payload_hash(lane_values)
    plan_hash = probability_pipeline_release_plan_hash(
        pipeline_version=pipeline_version,
        material_fingerprint=material_fingerprint,
        activation_manifest_hash=activation_manifest_hash,
        cohort=cohort,
        image_digest=image_digest,
        previous_epoch_id=previous_epoch_id,
        release_manifest_hash=release_manifest_hash,
        lane_manifest_hash=lane_manifest_hash,
        reviewed_by=reviewer,
        approval_reason=reason,
    )
    return ProbabilityPipelineReleasePlan(
        pipeline_version=pipeline_version,
        material_fingerprint=material_fingerprint,
        activation_manifest_hash=activation_manifest_hash,
        cohort=cohort,
        image_digest=image_digest,
        previous_epoch_id=previous_epoch_id,
        release_manifest=normalized_manifest,
        release_manifest_hash=release_manifest_hash,
        lane_plans=tuple(
            EvidenceLanePlan(
                lane_name=str(value["lane_name"]),
                action=str(value["action"]),
                identity_hash=str(value["identity_hash"]),
                manifest=cast(dict[str, Any], value["manifest"]),
                rationale=str(value["rationale"]),
            )
            for value in lane_values
        ),
        lane_manifest_hash=lane_manifest_hash,
        reviewed_by=reviewer,
        approval_reason=reason,
        plan_hash=plan_hash,
    )


async def current_probability_pipeline_release_epoch(
    session: AsyncSession,
) -> ProbabilityPipelineReleaseEpoch | None:
    return cast(
        ProbabilityPipelineReleaseEpoch | None,
        await session.scalar(
            select(ProbabilityPipelineReleaseEpoch)
            .order_by(ProbabilityPipelineReleaseEpoch.id.desc())
            .limit(1)
        ),
    )


async def list_probability_pipeline_release_epochs(
    session: AsyncSession,
    *,
    limit: int = 100,
) -> tuple[ProbabilityPipelineReleaseEpoch, ...]:
    if not 1 <= limit <= 1_000:
        raise ValueError("limit must be between 1 and 1000")
    rows = (
        await session.scalars(
            select(ProbabilityPipelineReleaseEpoch)
            .order_by(ProbabilityPipelineReleaseEpoch.id.desc())
            .limit(limit)
        )
    ).all()
    return tuple(rows)


async def current_evidence_continuity_lanes(
    session: AsyncSession,
) -> tuple[EvidenceContinuityLane, ...]:
    current = await current_probability_pipeline_release_epoch(session)
    if current is None:
        return ()
    rows = (
        await session.scalars(
            select(EvidenceContinuityLane)
            .where(EvidenceContinuityLane.epoch_id == current.id)
            .order_by(EvidenceContinuityLane.lane_name)
        )
    ).all()
    return tuple(rows)


async def current_probability_pipeline_release_plan_record(
    session: AsyncSession,
) -> ProbabilityPipelineReleasePlanRecord | None:
    current = await current_probability_pipeline_release_epoch(session)
    if current is None:
        return None
    return cast(
        ProbabilityPipelineReleasePlanRecord | None,
        await session.get(ProbabilityPipelineReleasePlanRecord, current.plan_id),
    )


async def list_evidence_continuity_lanes(
    session: AsyncSession,
    *,
    limit: int = 100,
    epoch_id: int | None = None,
) -> tuple[EvidenceContinuityLane, ...]:
    if not 1 <= limit <= 1_000:
        raise ValueError("limit must be between 1 and 1000")
    statement = select(EvidenceContinuityLane)
    if epoch_id is not None:
        if epoch_id <= 0:
            raise ValueError("epoch_id must be positive when present")
        statement = statement.where(EvidenceContinuityLane.epoch_id == epoch_id)
    rows = (
        await session.scalars(
            statement.order_by(EvidenceContinuityLane.id.desc()).limit(limit)
        )
    ).all()
    return tuple(rows)


def _lane_plan_values(plan: ProbabilityPipelineReleasePlan) -> list[dict[str, Any]]:
    return [
        {
            "lane_name": lane.lane_name,
            "action": lane.action,
            "identity_hash": lane.identity_hash,
            "manifest": dict(lane.manifest),
            "rationale": lane.rationale,
        }
        for lane in plan.lane_plans
    ]


async def persist_probability_pipeline_release_plan(
    session: AsyncSession,
    plan: ProbabilityPipelineReleasePlan,
) -> ProbabilityPipelineReleasePlanRecord:
    """Persist an idempotent reviewed preview without activating execution."""

    values = {
        "pipeline_version": plan.pipeline_version,
        "material_fingerprint": plan.material_fingerprint,
        "activation_manifest_hash": plan.activation_manifest_hash,
        "cohort": plan.cohort,
        "image_digest": plan.image_digest,
        "previous_epoch_id": plan.previous_epoch_id,
        "release_manifest": dict(plan.release_manifest),
        "release_manifest_hash": plan.release_manifest_hash,
        "lane_plans": _lane_plan_values(plan),
        "lane_manifest_hash": plan.lane_manifest_hash,
        "reviewed_by": plan.reviewed_by,
        "approval_reason": plan.approval_reason,
        "plan_hash": plan.plan_hash,
        "record_hash": _ZERO_HASH,
    }
    statement = (
        pg_insert(ProbabilityPipelineReleasePlanRecord)
        .values(**values)
        .on_conflict_do_nothing(
            constraint="uq_probability_pipeline_release_plans_plan_hash"
        )
        .returning(ProbabilityPipelineReleasePlanRecord)
    )
    try:
        stored = await session.scalar(statement)
    except DBAPIError as exc:
        raise ProbabilityPipelineReleaseIntegrityError(
            "database rejected the evidence release plan"
        ) from exc
    if stored is None:
        stored = await session.scalar(
            select(ProbabilityPipelineReleasePlanRecord).where(
                ProbabilityPipelineReleasePlanRecord.plan_hash == plan.plan_hash
            )
        )
    if stored is None:
        raise ProbabilityPipelineReleaseIntegrityError(
            "evidence release plan persistence returned no row"
        )
    if (
        stored.release_manifest_hash != plan.release_manifest_hash
        or stored.lane_manifest_hash != plan.lane_manifest_hash
        or stored.reviewed_by != plan.reviewed_by
        or stored.approval_reason != plan.approval_reason
    ):
        raise ProbabilityPipelineReleaseIntegrityError(
            "persisted evidence release plan differs from the reviewed preview"
        )
    return stored


async def append_probability_pipeline_release_epoch(
    session: AsyncSession,
    plan: ProbabilityPipelineReleasePlan,
    *,
    expected_plan_hash: str,
) -> ProbabilityPipelineReleaseEpoch:
    """Append one exact reviewed epoch; stale plans fail without rebasing."""

    _require_hex(expected_plan_hash, length=64, field="expected_plan_hash")
    recomputed = probability_pipeline_release_plan_hash(
        pipeline_version=plan.pipeline_version,
        material_fingerprint=plan.material_fingerprint,
        activation_manifest_hash=plan.activation_manifest_hash,
        cohort=plan.cohort,
        image_digest=plan.image_digest,
        previous_epoch_id=plan.previous_epoch_id,
        release_manifest_hash=plan.release_manifest_hash,
        lane_manifest_hash=plan.lane_manifest_hash,
        reviewed_by=plan.reviewed_by,
        approval_reason=plan.approval_reason,
    )
    if not hmac.compare_digest(plan.plan_hash, recomputed) or not hmac.compare_digest(
        expected_plan_hash,
        recomputed,
    ):
        raise ProbabilityPipelineReleaseIntegrityError(
            "candidate release plan hash is stale or does not match the reviewed plan"
        )

    await acquire_probability_pipeline_release_lock(session)
    current = await current_probability_pipeline_release_epoch(session)
    current_id = current.id if current is not None else None
    if plan.previous_epoch_id != current_id:
        raise ProbabilityPipelineReleaseIntegrityError(
            "candidate release plan does not extend the latest epoch"
        )
    current_plan = (
        await session.get(ProbabilityPipelineReleasePlanRecord, current.plan_id)
        if current is not None
        else None
    )
    if current is not None and current_plan is None:
        raise ProbabilityPipelineReleaseIntegrityError(
            "latest release epoch has no reviewed plan"
        )
    parent_lanes: dict[str, EvidenceContinuityLane] = {}
    if current is not None:
        parent_lanes = {
            row.lane_name: row
            for row in (
                await session.scalars(
                    select(EvidenceContinuityLane).where(
                        EvidenceContinuityLane.epoch_id == current.id
                    )
                )
            ).all()
        }
        if len(parent_lanes) != 6:
            raise ProbabilityPipelineReleaseIntegrityError(
                "latest release epoch has an incomplete continuity lane set"
            )
    same_lane_identities = bool(parent_lanes) and all(
        (parent := parent_lanes.get(lane.lane_name)) is not None
        and parent.identity_hash == lane.identity_hash
        and parent.manifest == dict(lane.manifest)
        for lane in plan.lane_plans
    )
    if (
        current is not None
        and current_plan is not None
        and current.cohort == plan.cohort
        and current.image_digest == plan.image_digest
        and current_plan.release_manifest_hash == plan.release_manifest_hash
        and same_lane_identities
    ):
        raise ProbabilityPipelineReleaseIntegrityError(
            "candidate release plan would create an exact no-op manifest"
        )

    stored_plan = await session.scalar(
        select(ProbabilityPipelineReleasePlanRecord).where(
            ProbabilityPipelineReleasePlanRecord.plan_hash == plan.plan_hash
        )
    )
    if stored_plan is None:
        raise ProbabilityPipelineReleaseIntegrityError(
            "candidate release plan must be persisted before activation"
        )
    statement = (
        insert(ProbabilityPipelineReleaseEpoch)
        .values(
            pipeline_version=plan.pipeline_version,
            material_fingerprint=plan.material_fingerprint,
            activation_manifest_hash=plan.activation_manifest_hash,
            cohort=plan.cohort,
            image_digest=plan.image_digest,
            plan_id=stored_plan.id,
            previous_epoch_id=plan.previous_epoch_id,
            approval_reason=plan.approval_reason,
            plan_hash=plan.plan_hash,
            record_hash=_ZERO_HASH,
        )
        .returning(ProbabilityPipelineReleaseEpoch)
    )
    try:
        async with session.begin_nested():
            stored = await session.scalar(statement)
            if stored is None:
                raise ProbabilityPipelineReleaseIntegrityError(
                    "probability pipeline release epoch insert returned no row"
                )
            for lane in plan.lane_plans:
                parent = parent_lanes.get(lane.lane_name)
                boundary = (
                    parent.boundary_at
                    if parent is not None and lane.action == "continue"
                    else stored.opened_at
                )
                lane_row = await session.scalar(
                    insert(EvidenceContinuityLane)
                    .values(
                        epoch_id=stored.id,
                        lane_name=lane.lane_name,
                        action=lane.action,
                        identity_hash=lane.identity_hash,
                        parent_lane_id=parent.id if parent is not None else None,
                        boundary_at=boundary,
                        manifest=dict(lane.manifest),
                        rationale=lane.rationale,
                        record_hash=_ZERO_HASH,
                    )
                    .returning(EvidenceContinuityLane)
                )
                if lane_row is None:
                    raise ProbabilityPipelineReleaseIntegrityError(
                        f"continuity lane insert returned no row for {lane.lane_name}"
                    )
    except DBAPIError as exc:
        raise ProbabilityPipelineReleaseIntegrityError(
            "database rejected the probability pipeline release epoch"
        ) from exc
    return stored
