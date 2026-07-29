from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_DOWN, Decimal
from enum import Enum
from statistics import fmean

from sqlalchemy import and_, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    AccuracyPrediction,
    AccuracyPromotionActivation,
    AccuracyPromotionEvidenceMember,
    AccuracyPromotionLook,
    AccuracyPromotionLookStatus,
    AccuracyPromotionTrial,
    AccuracyPromotionTrialStatus,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ModelProb,
    WeatherKind,
)
from stratus.repositories.accuracy import accuracy_prediction_record_is_valid
from stratus.repositories.analytics import probability_pipeline_version
from stratus.repositories.evidence_continuity import (
    acquire_probability_pipeline_release_lock,
)
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    gamma_outcome_map,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.emos import horizon_bucket
from stratus.services.pipeline_identity import pipeline_cohort_matches_base
from stratus.services.promotion_protocol import (
    PROMOTION_PROTOCOL_EPOCH,
    PROMOTION_PROTOCOL_VERSION,
    PROMOTION_TARGET_BLOCKS,
    PROMOTION_TEST_METHOD,
    PairedBrierBlock,
    PromotionDefinition,
    evaluate_fixed_promotion,
    validate_family_alpha_allocations,
)
from stratus.services.sealed_accuracy import (
    SEALED_EVIDENCE_PROTOCOL,
    sealed_experiment_fingerprint,
    sealed_expert_version,
)

PROMOTION_REGISTRATION_EPOCH = PROMOTION_PROTOCOL_EPOCH
PROMOTION_FAMILY_ALPHA = Decimal("0.05")
PROMOTION_ALPHA_SCALE = Decimal("0.000000000000000001")
PROMOTION_MINIMUM_BRIER_IMPROVEMENT = 0.0
PROMOTION_ACTIVATED_BY = "stratus.jobs.evaluate_promotion_trials"
CORE_PROMOTION_EXPERTS = frozenset(
    {
        "analog",
        "conformal_gaussian",
        "hierarchical_emos",
        "mixture",
        "qrf",
        "regularized_stack",
        "settlement_beta",
        "trajectory_ar1",
        "trajectory_nowcast",
    }
)
PROMOTABLE_ACCURACY_EXPERTS = CORE_PROMOTION_EXPERTS | {"regime_emos"}

_PROMOTION_LOCK_KEY = "stratus:accuracy-promotion:fixed-range-brier-v3"
_HORIZON_ANCHORS = {"0-6h": 3.0, "6-24h": 12.0, "24-48h": 36.0, "48h+": 72.0}
_BASELINE_TOTAL_TOLERANCE = Decimal("0.01")
_PROBABILITY_TOLERANCE = Decimal("0.000001")
_VECTOR_TOTAL_TOLERANCE = Decimal("0.0001")

CandidateRow = tuple[AccuracyPrediction, Market, ModelProb]
ResolvedCandidateRow = tuple[AccuracyPrediction, Market, ModelProb, GammaOutcomeEvent]


class AccuracyPromotionIntegrityError(RuntimeError):
    """Raised when immutable promotion evidence is incomplete or contradictory."""


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("promotion timestamps must be timezone-aware")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot hash promotion value {type(value)!r}")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            default=_json_default,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _aware_utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise AccuracyPromotionIntegrityError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _kind_value(kind: WeatherKind | str) -> str:
    return kind.value if isinstance(kind, WeatherKind) else str(kind)


def _legacy_version(prediction: AccuracyPrediction) -> str | None:
    stored = prediction.distribution.get("legacy_expert_version")
    if isinstance(stored, str) and stored.strip():
        legacy = stored.strip()
    else:
        suffix = f"-{SEALED_EVIDENCE_PROTOCOL}"
        if not prediction.expert_version.endswith(suffix):
            return None
        legacy = prediction.expert_version.removesuffix(suffix)
    if not legacy or sealed_expert_version(legacy) != prediction.expert_version:
        return None
    return legacy


def _exact_baseline_cohort(
    prediction: AccuracyPrediction,
    baseline: ModelProb,
) -> str | None:
    cohort = probability_pipeline_version(baseline.components)
    stored = prediction.distribution.get("baseline_pipeline_version")
    if not isinstance(stored, str) or not stored or stored != cohort:
        return None
    return cohort


def _exact_horizon_bucket(
    prediction: AccuracyPrediction,
    baseline: ModelProb,
) -> str | None:
    stored = prediction.distribution.get("horizon_bucket")
    if not isinstance(stored, str) or stored not in _HORIZON_ANCHORS:
        return None
    if horizon_bucket(math.ceil(float(baseline.horizon_h))) != stored:
        return None
    return stored


@dataclass(frozen=True, slots=True, order=True)
class PromotionScope:
    expert: str
    legacy_expert_version: str
    expert_version: str
    experiment_fingerprint: str
    baseline_pipeline_cohort: str
    station_id: int
    horizon_bucket: str
    kind: str

    @property
    def candidate_id(self) -> str:
        return f"{self.expert}:{self.expert_version}:{self.experiment_fingerprint}"

    @property
    def baseline_id(self) -> str:
        return f"production:{self.baseline_pipeline_cohort}"

    @property
    def scope_key(self) -> str:
        return f"{self.station_id}:{self.horizon_bucket}:{self.kind}"

    @property
    def experiment_id(self) -> str:
        identity = _canonical_hash(
            {
                "registration_epoch": PROMOTION_REGISTRATION_EPOCH,
                "candidate_id": self.candidate_id,
                "baseline_id": self.baseline_id,
                "scope_key": self.scope_key,
            }
        )
        return f"promotion-{identity}"


def _scope_for_row(
    prediction: AccuracyPrediction,
    market: Market,
    baseline: ModelProb,
    *,
    pipeline_fingerprint: str,
) -> PromotionScope | None:
    legacy_version = _legacy_version(prediction)
    baseline_cohort = _exact_baseline_cohort(prediction, baseline)
    bucket = _exact_horizon_bucket(prediction, baseline)
    fingerprint = prediction.experiment_fingerprint
    if (
        prediction.expert not in PROMOTABLE_ACCURACY_EXPERTS
        or market.station_id is None
        or legacy_version is None
        or baseline_cohort is None
        or bucket is None
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or baseline.id != prediction.baseline_model_prob_id
        or baseline.market_id != prediction.market_id
        or baseline.ts != prediction.ts
        or baseline.market_anchor_protocol != CLOB_DUAL_PROTOCOL
        or baseline.market_snapshot_id is None
        or prediction.distribution.get("evidence_protocol") != SEALED_EVIDENCE_PROTOCOL
    ):
        return None
    expected_fingerprint = sealed_experiment_fingerprint(
        pipeline_fingerprint=pipeline_fingerprint,
        expert=prediction.expert,
        legacy_version=legacy_version,
        sealed_version=prediction.expert_version,
        pipeline_version=baseline_cohort,
    )
    if fingerprint != expected_fingerprint:
        return None
    return PromotionScope(
        expert=prediction.expert,
        legacy_expert_version=legacy_version,
        expert_version=prediction.expert_version,
        experiment_fingerprint=fingerprint,
        baseline_pipeline_cohort=baseline_cohort,
        station_id=market.station_id,
        horizon_bucket=bucket,
        kind=_kind_value(market.kind),
    )


def _normalized_baseline_vector(rows: list[CandidateRow]) -> dict[int, Decimal] | None:
    raw = [baseline.prob for _prediction, _market, baseline in rows]
    if any(
        not probability.is_finite() or probability < Decimal("0") or probability > Decimal("1")
        for probability in raw
    ):
        return None
    total = sum(raw, start=Decimal("0"))
    if total <= 0 or abs(total - Decimal("1")) > _BASELINE_TOTAL_TOLERANCE:
        return None
    result: dict[int, Decimal] = {}
    for prediction, market, baseline in rows:
        stored_raw = prediction.distribution.get("baseline_probability")
        stored_normalized = prediction.distribution.get("normalized_baseline_probability")
        if (
            isinstance(stored_raw, bool)
            or not isinstance(stored_raw, int | float)
            or not math.isfinite(float(stored_raw))
            or abs(Decimal(str(stored_raw)) - baseline.prob) > _PROBABILITY_TOLERANCE
            or isinstance(stored_normalized, bool)
            or not isinstance(stored_normalized, int | float)
            or not math.isfinite(float(stored_normalized))
        ):
            return None
        normalized = baseline.prob / total
        if abs(Decimal(str(stored_normalized)) - normalized) > Decimal("0.00001"):
            return None
        result[market.id] = normalized
    return result


def _complete_unresolved_vector(
    rows: list[CandidateRow],
    *,
    expected_market_ids: set[int],
    registered_at: datetime,
) -> bool:
    if not rows or len(rows) != len(expected_market_ids) or len(rows) < 2:
        return False
    market_ids = [market.id for _prediction, market, _baseline in rows]
    if len(set(market_ids)) != len(market_ids) or set(market_ids) != expected_market_ids:
        return False
    if any(
        prediction.generated_at > registered_at
        or prediction.generated_at >= market.resolves_at
        or market.resolves_at <= registered_at
        or market.resolution_event_id is not None
        or market.resolved_outcome is not None
        for prediction, market, _baseline in rows
    ):
        return False
    candidate_total = sum(
        (prediction.probability for prediction, _market, _baseline in rows),
        start=Decimal("0"),
    )
    return bool(
        abs(candidate_total - Decimal("1")) <= _VECTOR_TOTAL_TOLERANCE
        and _normalized_baseline_vector(rows) is not None
    )


def promotion_scopes_from_unresolved_rows(
    rows: Iterable[CandidateRow],
    *,
    event_market_ids: Mapping[str, set[int]],
    pipeline_fingerprint: str,
    registered_at: datetime,
) -> tuple[PromotionScope, ...]:
    """Return only current-code, complete, still-unresolved candidate scopes."""

    boundary = _aware_utc(registered_at, field="registered_at")
    snapshots: dict[tuple[PromotionScope, str, datetime], list[CandidateRow]] = defaultdict(list)
    invalid: set[tuple[PromotionScope, str, datetime]] = set()
    for prediction, market, baseline in rows:
        if not prediction.evidence_eligible or not prediction.point_in_time_valid:
            continue
        if not accuracy_prediction_record_is_valid(prediction):
            continue
        scope = _scope_for_row(
            prediction,
            market,
            baseline,
            pipeline_fingerprint=pipeline_fingerprint,
        )
        if scope is None:
            continue
        key = (scope, market.event_id, prediction.ts)
        if any(existing[1].id == market.id for existing in snapshots[key]):
            invalid.add(key)
        snapshots[key].append((prediction, market, baseline))

    scopes: set[PromotionScope] = set()
    for (scope, event_id, _prediction_ts), snapshot in snapshots.items():
        expected = event_market_ids.get(event_id)
        if (
            (scope, event_id, _prediction_ts) not in invalid
            and expected is not None
            and _complete_unresolved_vector(
                snapshot,
                expected_market_ids=expected,
                registered_at=boundary,
            )
        ):
            scopes.add(scope)
    return tuple(sorted(scopes))


def promotion_registration_is_ready(scopes: Iterable[PromotionScope]) -> bool:
    """Require the full always-on roster before freezing the global family.

    ``regime_emos`` remains optional because it exists only while an active
    structural-break event is being modelled.  If present in the coherent
    snapshot it is included in the frozen family; it can never be added later.
    """

    present = {scope.expert for scope in scopes}
    return CORE_PROMOTION_EXPERTS.issubset(present)


@dataclass(frozen=True, slots=True)
class PromotionEvidenceBlock:
    trial_id: int
    independent_block_key: str
    resolved_cutoff: datetime
    prediction_ts: datetime
    mean_horizon_h: float
    accuracy_prediction_ids: tuple[int, ...]
    candidate_brier: float
    baseline_brier: float
    predictable_improvement_lower: float
    predictable_improvement_upper: float
    evidence_hash: str

    def as_service_block(self) -> PairedBrierBlock:
        return PairedBrierBlock(
            block_key=self.independent_block_key,
            resolved_cutoff=self.resolved_cutoff,
            baseline_brier=self.baseline_brier,
            candidate_brier=self.candidate_brier,
            predictable_improvement_lower=self.predictable_improvement_lower,
            predictable_improvement_upper=self.predictable_improvement_upper,
            evidence_hash=self.evidence_hash,
        )


def _resolved_vector_block(
    trial: AccuracyPromotionTrial,
    rows: list[ResolvedCandidateRow],
    *,
    outcomes: Mapping[int, bool],
) -> PromotionEvidenceBlock | None:
    if not rows or trial.id is None:
        return None
    base_rows: list[CandidateRow] = [
        (prediction, market, baseline) for prediction, market, baseline, _outcome_event in rows
    ]
    first_prediction, first_market, _first_baseline, event = rows[0]
    market_ids = [market.id for _prediction, market, _baseline, _event in rows]
    if (
        len(rows) < 2
        or len(set(market_ids)) != len(market_ids)
        or set(market_ids) != set(outcomes)
        or sum(bool(value) for value in outcomes.values()) != 1
        or any(
            candidate_event.id != event.id
            or market.event_id != event.event_id
            or market.resolution_event_id != event.id
            or market.station_id != trial.station_id
            or market.market_date != first_market.market_date
            or _kind_value(market.kind) != _kind_value(trial.kind)
            or market.status != MarketStatus.RESOLVED
            or market.resolved_outcome is not outcomes.get(market.id)
            or prediction.generated_at < trial.starts_at
            or prediction.generated_at < trial.created_at
            or prediction.generated_at >= event.first_outcome_at
            or prediction.ts >= event.first_outcome_at
            or prediction.generated_at >= market.resolves_at
            for prediction, market, _baseline, candidate_event in rows
        )
    ):
        return None
    candidate_total = sum(
        (prediction.probability for prediction, _market, _baseline, _event in rows),
        start=Decimal("0"),
    )
    normalized_baseline = _normalized_baseline_vector(base_rows)
    if abs(candidate_total - Decimal("1")) > _VECTOR_TOTAL_TOLERANCE or normalized_baseline is None:
        return None

    ordered = sorted(rows, key=lambda item: item[1].id)
    candidate_scores: list[float] = []
    baseline_scores: list[float] = []
    candidate_probabilities: list[Decimal] = []
    baseline_probabilities: list[Decimal] = []
    evidence_rows: list[dict[str, object]] = []
    for prediction, market, baseline, _candidate_event in ordered:
        if prediction.id is None or baseline.id is None:
            return None
        outcome = float(outcomes[market.id])
        candidate_probability = float(prediction.probability)
        baseline_probability = float(normalized_baseline[market.id])
        candidate_probabilities.append(prediction.probability)
        baseline_probabilities.append(normalized_baseline[market.id])
        candidate_scores.append((candidate_probability - outcome) ** 2)
        baseline_scores.append((baseline_probability - outcome) ** 2)
        evidence_rows.append(
            {
                "market_id": market.id,
                "accuracy_prediction_id": prediction.id,
                "accuracy_prediction_record_hash": prediction.record_hash,
                "baseline_model_prob_id": baseline.id,
                "baseline_probability": baseline.prob,
                "candidate_probability": prediction.probability,
                "outcome": bool(outcomes[market.id]),
            }
        )
    bucket_count = Decimal(len(ordered))
    square_difference_mean = (
        sum(
            (
                baseline_probability * baseline_probability
                - candidate_probability * candidate_probability
                for candidate_probability, baseline_probability in zip(
                    candidate_probabilities,
                    baseline_probabilities,
                    strict=True,
                )
            ),
            start=Decimal("0"),
        )
        / bucket_count
    )
    possible_improvements = tuple(
        square_difference_mean
        + Decimal("2") * (candidate_probability - baseline_probability) / bucket_count
        for candidate_probability, baseline_probability in zip(
            candidate_probabilities,
            baseline_probabilities,
            strict=True,
        )
    )
    predictable_lower = min(possible_improvements)
    predictable_upper = max(possible_improvements)
    block_key = (
        f"{trial.station_id}:{first_market.market_date.isoformat()}:"
        f"{_kind_value(trial.kind)}:{trial.horizon_bucket}"
    )
    evidence_hash = _canonical_hash(
        {
            "schema": "accuracy-promotion-evidence-vector-v1",
            "trial_definition_hash": trial.definition_hash,
            "independent_block_key": block_key,
            "event_id": event.event_id,
            "gamma_outcome_event_id": event.id,
            "gamma_record_hash": event.record_hash,
            "resolution_vector_hash": event.outcome_vector_hash,
            "resolved_cutoff": event.first_outcome_at,
            "prediction_ts": first_prediction.ts,
            "rows": evidence_rows,
            "predictable_improvement_lower": predictable_lower,
            "predictable_improvement_upper": predictable_upper,
        }
    )
    return PromotionEvidenceBlock(
        trial_id=trial.id,
        independent_block_key=block_key,
        resolved_cutoff=event.first_outcome_at,
        prediction_ts=first_prediction.ts,
        mean_horizon_h=fmean(
            float(baseline.horizon_h) for _prediction, _market, baseline, _event in ordered
        ),
        accuracy_prediction_ids=tuple(
            prediction.id for prediction, _market, _baseline, _event in ordered
        ),
        candidate_brier=fmean(candidate_scores),
        baseline_brier=fmean(baseline_scores),
        predictable_improvement_lower=float(predictable_lower),
        predictable_improvement_upper=float(predictable_upper),
        evidence_hash=evidence_hash,
    )


def resolved_promotion_blocks(
    trials: Iterable[AccuracyPromotionTrial],
    rows: Iterable[ResolvedCandidateRow],
    *,
    pipeline_fingerprint: str,
) -> dict[int, tuple[PromotionEvidenceBlock, ...]]:
    """Build deterministic full-vector blocks for exact registered identities."""

    trial_by_scope = {
        PromotionScope(
            expert=trial.expert,
            legacy_expert_version=trial.legacy_expert_version,
            expert_version=trial.expert_version,
            experiment_fingerprint=trial.experiment_fingerprint,
            baseline_pipeline_cohort=trial.pipeline_cohort,
            station_id=trial.station_id,
            horizon_bucket=trial.horizon_bucket,
            kind=_kind_value(trial.kind),
        ): trial
        for trial in trials
        if trial.id is not None and trial.status == AccuracyPromotionTrialStatus.COLLECTING
    }
    snapshots: dict[tuple[int, str, datetime], list[ResolvedCandidateRow]] = defaultdict(list)
    invalid: set[tuple[int, str, datetime]] = set()
    outcomes_by_event: dict[int, Mapping[int, bool]] = {}
    for prediction, market, baseline, outcome_event in rows:
        if not prediction.evidence_eligible or not prediction.point_in_time_valid:
            continue
        if not accuracy_prediction_record_is_valid(prediction):
            continue
        scope = _scope_for_row(
            prediction,
            market,
            baseline,
            pipeline_fingerprint=pipeline_fingerprint,
        )
        trial = trial_by_scope.get(scope) if scope is not None else None
        if trial is None or trial.id is None:
            continue
        if _aware_utc(outcome_event.first_outcome_at, field="resolved_cutoff") <= _aware_utc(
            trial.registered_at,
            field="registered_at",
        ):
            continue
        try:
            if outcome_event.id not in outcomes_by_event:
                outcomes_by_event[outcome_event.id] = gamma_outcome_map(outcome_event)
        except GammaOutcomeIntegrityError:
            continue
        key = (trial.id, outcome_event.event_id, prediction.ts)
        if any(existing[1].id == market.id for existing in snapshots[key]):
            invalid.add(key)
        snapshots[key].append((prediction, market, baseline, outcome_event))

    by_block: dict[tuple[int, str], list[PromotionEvidenceBlock]] = defaultdict(list)
    for key, snapshot in snapshots.items():
        if key in invalid:
            continue
        event = snapshot[0][3]
        trial = next(candidate for candidate in trial_by_scope.values() if candidate.id == key[0])
        block = _resolved_vector_block(
            trial,
            snapshot,
            outcomes=outcomes_by_event[event.id],
        )
        if block is not None:
            by_block[(block.trial_id, block.independent_block_key)].append(block)

    selected: dict[int, list[PromotionEvidenceBlock]] = defaultdict(list)
    for (trial_id, _block_key), candidates in by_block.items():
        trial = next(candidate for candidate in trial_by_scope.values() if candidate.id == trial_id)
        anchor = _HORIZON_ANCHORS[trial.horizon_bucket]
        winner = min(
            candidates,
            key=lambda block: (
                abs(block.mean_horizon_h - anchor),
                block.prediction_ts,
                block.evidence_hash,
            ),
        )
        selected[trial_id].append(winner)
    return {
        trial_id: tuple(
            sorted(
                blocks,
                key=lambda block: (block.resolved_cutoff, block.independent_block_key),
            )
        )
        for trial_id, blocks in selected.items()
    }


@dataclass(frozen=True, slots=True, order=True)
class EffectivePromotionKey:
    expert: str
    legacy_expert_version: str
    expert_version: str
    experiment_fingerprint: str
    pipeline_cohort: str
    station_id: int
    horizon_bucket: str
    kind: str


@dataclass(frozen=True, slots=True)
class EffectivePromotion:
    key: EffectivePromotionKey
    activation_id: str
    definition_hash: str
    decision_hash: str
    activated_at: datetime


@dataclass(frozen=True, slots=True)
class EffectivePromotionManifest:
    promotions: tuple[EffectivePromotion, ...]
    manifest_hash: str

    @property
    def index(self) -> dict[EffectivePromotionKey, EffectivePromotion]:
        return {promotion.key: promotion for promotion in self.promotions}


def effective_promotion_for_current_source(
    row: AccuracyPromotionActivation,
    *,
    pipeline_fingerprint: str,
    current_pipeline_base_cohort: str,
) -> EffectivePromotion | None:
    """Project one DB activation only when its sealed identity is still exact."""

    expected_fingerprint = sealed_experiment_fingerprint(
        pipeline_fingerprint=pipeline_fingerprint,
        expert=row.expert,
        legacy_version=row.legacy_expert_version,
        sealed_version=row.expert_version,
        pipeline_version=row.pipeline_cohort,
    )
    if (
        not pipeline_cohort_matches_base(
            stored=row.pipeline_cohort,
            current_base=current_pipeline_base_cohort,
        )
        or row.experiment_fingerprint != expected_fingerprint
        or sealed_expert_version(row.legacy_expert_version) != row.expert_version
    ):
        return None
    return EffectivePromotion(
        key=EffectivePromotionKey(
            expert=row.expert,
            legacy_expert_version=row.legacy_expert_version,
            expert_version=row.expert_version,
            experiment_fingerprint=row.experiment_fingerprint,
            pipeline_cohort=row.pipeline_cohort,
            station_id=row.station_id,
            horizon_bucket=row.horizon_bucket,
            kind=_kind_value(row.kind),
        ),
        activation_id=row.activation_id,
        definition_hash=row.definition_hash,
        decision_hash=row.decision_hash,
        activated_at=row.activated_at,
    )


def build_effective_promotion_manifest(
    promotions: Iterable[EffectivePromotion],
) -> EffectivePromotionManifest:
    ordered = tuple(sorted(promotions, key=lambda item: (item.key, item.activation_id)))
    if len({promotion.key for promotion in ordered}) != len(ordered):
        raise AccuracyPromotionIntegrityError("multiple active promotions cover one exact scope")
    manifest_hash = _canonical_hash(
        {
            "schema": "effective-accuracy-promotions-v1",
            "registration_epoch": PROMOTION_REGISTRATION_EPOCH,
            "activations": [
                {
                    "key": {
                        "expert": item.key.expert,
                        "legacy_expert_version": item.key.legacy_expert_version,
                        "expert_version": item.key.expert_version,
                        "experiment_fingerprint": item.key.experiment_fingerprint,
                        "pipeline_cohort": item.key.pipeline_cohort,
                        "station_id": item.key.station_id,
                        "horizon_bucket": item.key.horizon_bucket,
                        "kind": item.key.kind,
                    },
                    "activation_id": item.activation_id,
                    "definition_hash": item.definition_hash,
                    "decision_hash": item.decision_hash,
                    "activated_at": item.activated_at,
                }
                for item in ordered
            ],
        }
    )
    return EffectivePromotionManifest(promotions=ordered, manifest_hash=manifest_hash)


@dataclass(frozen=True, slots=True)
class PromotionProtocolRun:
    family_id: str
    trials: int
    registered_trials: int
    recorded_members: int
    recorded_looks: int
    recorded_activations: int


def _allocated_alpha(family_size: int) -> float:
    if family_size <= 0:
        raise ValueError("promotion family must contain at least one scope")
    allocated = (PROMOTION_FAMILY_ALPHA / Decimal(family_size)).quantize(
        PROMOTION_ALPHA_SCALE,
        rounding=ROUND_DOWN,
    )
    if allocated <= 0 or allocated * family_size > PROMOTION_FAMILY_ALPHA:
        raise AccuracyPromotionIntegrityError("invalid family-wise alpha allocation")
    return float(allocated)


def _definition_for_scope(
    scope: PromotionScope,
    *,
    registered_at: datetime,
    family_size: int,
    family_member_index: int,
) -> PromotionDefinition:
    return PromotionDefinition(
        family_id=PROMOTION_REGISTRATION_EPOCH,
        experiment_id=scope.experiment_id,
        candidate_id=scope.candidate_id,
        baseline_id=scope.baseline_id,
        pipeline_cohort=scope.baseline_pipeline_cohort,
        registered_at=registered_at,
        starts_at=registered_at,
        target_blocks=PROMOTION_TARGET_BLOCKS,
        alpha=_allocated_alpha(family_size),
        expert=scope.expert,
        legacy_expert_version=scope.legacy_expert_version,
        expert_version=scope.expert_version,
        experiment_fingerprint=scope.experiment_fingerprint,
        station_id=scope.station_id,
        horizon_bucket=scope.horizon_bucket,
        kind=scope.kind,
        scope_key=scope.scope_key,
        family_member_index=family_member_index,
        family_size=family_size,
        family_alpha=float(PROMOTION_FAMILY_ALPHA),
        minimum_brier_improvement=PROMOTION_MINIMUM_BRIER_IMPROVEMENT,
    )


def _definition_from_trial(trial: AccuracyPromotionTrial) -> PromotionDefinition:
    return PromotionDefinition(
        family_id=trial.family_id,
        experiment_id=trial.experiment_id,
        candidate_id=trial.candidate_id,
        baseline_id=trial.baseline_id,
        pipeline_cohort=trial.pipeline_cohort,
        registered_at=trial.registered_at,
        starts_at=trial.starts_at,
        target_blocks=trial.target_blocks,
        alpha=float(trial.allocated_alpha),
        expert=trial.expert,
        legacy_expert_version=trial.legacy_expert_version,
        expert_version=trial.expert_version,
        experiment_fingerprint=trial.experiment_fingerprint,
        station_id=trial.station_id,
        horizon_bucket=trial.horizon_bucket,
        kind=_kind_value(trial.kind),
        scope_key=trial.scope_key,
        family_member_index=trial.family_member_index,
        family_size=trial.family_size,
        family_alpha=float(trial.family_alpha),
        minimum_brier_improvement=float(trial.minimum_brier_improvement),
    )


async def _advisory_lock(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": _PROMOTION_LOCK_KEY},
    )


async def _load_family(session: AsyncSession) -> list[AccuracyPromotionTrial]:
    return list(
        (
            await session.execute(
                select(AccuracyPromotionTrial)
                .where(AccuracyPromotionTrial.registration_epoch == PROMOTION_REGISTRATION_EPOCH)
                .order_by(AccuracyPromotionTrial.family_member_index)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _register_family(
    session: AsyncSession,
    *,
    registered_at: datetime,
    pipeline_fingerprint: str,
) -> tuple[list[AccuracyPromotionTrial], int]:
    existing = await _load_family(session)
    if existing:
        definitions = [_definition_from_trial(trial) for trial in existing]
        validate_family_alpha_allocations(definitions)
        expected_size = existing[0].family_size
        if (
            len(existing) != expected_size
            or [trial.family_member_index for trial in existing]
            != list(range(1, expected_size + 1))
            or any(
                trial.definition_hash != definition.definition_hash
                for trial, definition in zip(existing, definitions, strict=True)
            )
        ):
            raise AccuracyPromotionIntegrityError("stored promotion family is incomplete")
        return existing, 0

    rows = (
        await session.execute(
            select(AccuracyPrediction, Market, ModelProb)
            .join(Market, AccuracyPrediction.market_id == Market.id)
            .join(
                ModelProb,
                and_(
                    ModelProb.id == AccuracyPrediction.baseline_model_prob_id,
                    ModelProb.market_id == AccuracyPrediction.market_id,
                    ModelProb.ts == AccuracyPrediction.ts,
                ),
            )
            .where(
                AccuracyPrediction.evidence_eligible.is_(True),
                AccuracyPrediction.point_in_time_valid.is_(True),
                AccuracyPrediction.generated_at <= registered_at,
                Market.resolution_event_id.is_(None),
                Market.resolved_outcome.is_(None),
                Market.resolves_at > registered_at,
                ModelProb.market_anchor_protocol == CLOB_DUAL_PROTOCOL,
                ModelProb.market_snapshot_id.is_not(None),
            )
        )
    ).all()
    event_ids = {market.event_id for _prediction, market, _baseline in rows}
    event_market_rows = (
        (
            await session.execute(
                select(Market.event_id, Market.id).where(Market.event_id.in_(event_ids))
            )
        ).all()
        if event_ids
        else []
    )
    event_market_ids: dict[str, set[int]] = defaultdict(set)
    for event_id, market_id in event_market_rows:
        event_market_ids[event_id].add(market_id)
    candidate_rows: list[CandidateRow] = [
        (prediction, market, baseline) for prediction, market, baseline in rows
    ]
    current_identity_rows = [
        row
        for row in candidate_rows
        if _scope_for_row(
            row[0],
            row[1],
            row[2],
            pipeline_fingerprint=pipeline_fingerprint,
        )
        is not None
    ]
    if not current_identity_rows:
        return [], 0
    latest_generation_ts = max(
        baseline.ts for _prediction, _market, baseline in current_identity_rows
    )
    current_generation_rows = [
        row for row in current_identity_rows if row[2].ts == latest_generation_ts
    ]
    scopes = promotion_scopes_from_unresolved_rows(
        current_generation_rows,
        event_market_ids=event_market_ids,
        pipeline_fingerprint=pipeline_fingerprint,
        registered_at=registered_at,
    )
    if not promotion_registration_is_ready(scopes):
        return [], 0
    family_size = len(scopes)
    definitions = [
        _definition_for_scope(
            scope,
            registered_at=registered_at,
            family_size=family_size,
            family_member_index=index,
        )
        for index, scope in enumerate(scopes, 1)
    ]
    validate_family_alpha_allocations(definitions)
    values: list[dict[str, object]] = []
    for index, (scope, definition) in enumerate(zip(scopes, definitions, strict=True), 1):
        values.append(
            {
                "registration_epoch": PROMOTION_REGISTRATION_EPOCH,
                "protocol_version": PROMOTION_PROTOCOL_VERSION,
                "family_id": definition.family_id,
                "experiment_id": definition.experiment_id,
                "candidate_id": definition.candidate_id,
                "baseline_id": definition.baseline_id,
                "pipeline_cohort": definition.pipeline_cohort,
                "registered_at": definition.registered_at,
                "starts_at": definition.starts_at,
                "target_blocks": definition.target_blocks,
                "allocated_alpha": Decimal(str(definition.alpha)),
                "minimum_brier_improvement": Decimal(str(definition.minimum_brier_improvement)),
                "test_method": PROMOTION_TEST_METHOD,
                "definition_hash": definition.definition_hash,
                "expert": scope.expert,
                "legacy_expert_version": scope.legacy_expert_version,
                "expert_version": scope.expert_version,
                "experiment_fingerprint": scope.experiment_fingerprint,
                "station_id": scope.station_id,
                "horizon_bucket": scope.horizon_bucket,
                "kind": scope.kind,
                "scope_key": scope.scope_key,
                "family_member_index": index,
                "family_size": family_size,
                "family_alpha": PROMOTION_FAMILY_ALPHA,
            }
        )
    await session.execute(insert(AccuracyPromotionTrial).values(values))
    stored = await _load_family(session)
    if len(stored) != family_size:
        raise AccuracyPromotionIntegrityError("promotion family registration was not atomic")
    return stored, family_size


async def _resolved_rows(
    session: AsyncSession,
    trials: list[AccuracyPromotionTrial],
) -> list[ResolvedCandidateRow]:
    if not trials:
        return []
    boundary = min(trial.registered_at for trial in trials)
    experts = sorted({trial.expert for trial in trials})
    rows = (
        await session.execute(
            select(AccuracyPrediction, Market, ModelProb, GammaOutcomeEvent)
            .join(Market, AccuracyPrediction.market_id == Market.id)
            .join(
                ModelProb,
                and_(
                    ModelProb.id == AccuracyPrediction.baseline_model_prob_id,
                    ModelProb.market_id == AccuracyPrediction.market_id,
                    ModelProb.ts == AccuracyPrediction.ts,
                ),
            )
            .join(
                GammaOutcomeEvent,
                and_(
                    GammaOutcomeEvent.id == Market.resolution_event_id,
                    GammaOutcomeEvent.event_id == Market.event_id,
                ),
            )
            .where(
                AccuracyPrediction.expert.in_(experts),
                AccuracyPrediction.evidence_eligible.is_(True),
                AccuracyPrediction.point_in_time_valid.is_(True),
                AccuracyPrediction.generated_at < GammaOutcomeEvent.first_outcome_at,
                AccuracyPrediction.ts < GammaOutcomeEvent.first_outcome_at,
                GammaOutcomeEvent.first_outcome_at > boundary,
                Market.status == MarketStatus.RESOLVED,
                Market.resolved_outcome.is_not(None),
                ModelProb.market_anchor_protocol == CLOB_DUAL_PROTOCOL,
                ModelProb.market_snapshot_id.is_not(None),
            )
        )
    ).all()
    return [
        (prediction, market, baseline, outcome_event)
        for prediction, market, baseline, outcome_event in rows
    ]


async def _evaluate_trials(
    session: AsyncSession,
    trials: list[AccuracyPromotionTrial],
    *,
    pipeline_fingerprint: str,
) -> tuple[int, int]:
    collecting = [
        trial for trial in trials if trial.status == AccuracyPromotionTrialStatus.COLLECTING
    ]
    blocks_by_trial = resolved_promotion_blocks(
        collecting,
        await _resolved_rows(session, collecting),
        pipeline_fingerprint=pipeline_fingerprint,
    )
    member_count = 0
    look_count = 0
    for trial in collecting:
        if trial.id is None:
            continue
        blocks = list(blocks_by_trial.get(trial.id, ()))
        definition = _definition_from_trial(trial)
        if len(blocks) < trial.target_blocks:
            continue
        selected = blocks[: trial.target_blocks]
        if len(selected) != trial.target_blocks:
            raise AccuracyPromotionIntegrityError("fixed look selected an incomplete block set")
        member_values = [
            {
                "trial_id": trial.id,
                "accuracy_prediction_ids": list(block.accuracy_prediction_ids),
            }
            for block in selected
        ]
        statement = (
            insert(AccuracyPromotionEvidenceMember)
            .values(member_values)
            .on_conflict_do_nothing(constraint="uq_accuracy_promotion_members_trial_block")
            .returning(AccuracyPromotionEvidenceMember.id)
        )
        inserted_member_ids = (await session.execute(statement)).scalars().all()
        stored_members = list(
            (
                await session.execute(
                    select(AccuracyPromotionEvidenceMember)
                    .where(AccuracyPromotionEvidenceMember.trial_id == trial.id)
                    .order_by(
                        AccuracyPromotionEvidenceMember.resolved_cutoff,
                        AccuracyPromotionEvidenceMember.independent_block_key,
                    )
                )
            )
            .scalars()
            .all()
        )
        if len(stored_members) != trial.target_blocks:
            raise AccuracyPromotionIntegrityError("database did not seal the fixed block set")
        persisted_decision = evaluate_fixed_promotion(
            definition,
            [
                PairedBrierBlock(
                    block_key=member.independent_block_key,
                    resolved_cutoff=member.resolved_cutoff,
                    baseline_brier=member.baseline_brier,
                    candidate_brier=member.candidate_brier,
                    predictable_improvement_lower=member.predictable_improvement_lower,
                    predictable_improvement_upper=member.predictable_improvement_upper,
                    evidence_hash=member.evidence_hash,
                )
                for member in stored_members
            ],
        )
        if (
            persisted_decision.mean_delta_brier is None
            or persisted_decision.wins is None
            or persisted_decision.losses is None
            or persisted_decision.ties is None
            or persisted_decision.nonwins is None
            or persisted_decision.sum_predictable_range_squared is None
            or persisted_decision.confidence_radius is None
            or persisted_decision.mean_improvement_lcb is None
            or persisted_decision.one_sided_p_bound is None
        ):
            raise AccuracyPromotionIntegrityError(
                "complete fixed look lacks range-bound statistics"
            )
        look_statement = (
            insert(AccuracyPromotionLook)
            .values(
                trial_id=trial.id,
                definition_hash=trial.definition_hash,
                decision_hash=persisted_decision.decision_hash,
            )
            .on_conflict_do_nothing(constraint="uq_accuracy_promotion_looks_trial")
            .returning(AccuracyPromotionLook)
        )
        look = (await session.execute(look_statement)).scalar_one_or_none()
        look_inserted = look is not None
        if look is None:
            look = await session.scalar(
                select(AccuracyPromotionLook).where(AccuracyPromotionLook.trial_id == trial.id)
            )
        expected_status = AccuracyPromotionLookStatus(persisted_decision.status.value)
        mean_difference = (
            abs(look.mean_delta_brier - persisted_decision.mean_delta_brier)
            if look is not None
            else Decimal("Infinity")
        )
        if (
            look is None
            or look.definition_hash != persisted_decision.definition_hash
            or look.decision_hash != persisted_decision.decision_hash
            or look.independent_block_count != persisted_decision.target_blocks
            or look.evidence_member_count != persisted_decision.target_blocks
            or look.wins != persisted_decision.wins
            or look.losses != persisted_decision.losses
            or look.ties != persisted_decision.ties
            or look.nonwins != persisted_decision.nonwins
            or look.sum_predictable_range_squared
            != persisted_decision.sum_predictable_range_squared
            or look.confidence_radius != persisted_decision.confidence_radius
            or look.mean_improvement_lcb != persisted_decision.mean_improvement_lcb
            or look.one_sided_p_bound != persisted_decision.one_sided_p_bound
            or mean_difference > Decimal("0.00000000005")
            or look.status != expected_status
        ):
            raise AccuracyPromotionIntegrityError("conflicting immutable promotion look")
        member_count += len(inserted_member_ids)
        look_count += int(look_inserted)
    return member_count, look_count


async def _activate_terminal_family(session: AsyncSession) -> int:
    """Activate all passing scopes together, only after the family is terminal.

    Deferring the activation manifest prevents its new probability-pipeline
    cohort from stranding sibling trials that are still collecting evidence
    against the preregistered baseline cohort.
    """

    # ``run_accuracy_promotion_protocol`` already holds the promotion lock.
    # Always acquire locks in promotion -> release order.  Intent admission and
    # release activation take only the release lock, so there is no reverse
    # dependency and no advisory-lock cycle.
    await acquire_probability_pipeline_release_lock(session)
    trials = await _load_family(session)
    if not promotion_family_is_terminal(trials):
        return 0
    looks = list(
        (
            await session.execute(
                select(AccuracyPromotionLook).where(
                    AccuracyPromotionLook.trial_id.in_([trial.id for trial in trials])
                )
            )
        )
        .scalars()
        .all()
    )
    if len(looks) != len(trials):
        raise AccuracyPromotionIntegrityError("terminal promotion family lacks fixed looks")
    look_by_trial = {look.trial_id: look for look in looks}
    activated_trial_ids = set(
        (
            await session.execute(
                select(AccuracyPromotionActivation.trial_id).where(
                    AccuracyPromotionActivation.family_id == PROMOTION_REGISTRATION_EPOCH
                )
            )
        ).scalars()
    )
    activation_time = await session.scalar(select(func.clock_timestamp()))
    if activation_time is None:
        raise AccuracyPromotionIntegrityError("database did not provide activation time")
    values: list[dict[str, object]] = []
    for trial in trials:
        if trial.status != AccuracyPromotionTrialStatus.PASSED or trial.id in activated_trial_ids:
            continue
        look = look_by_trial[trial.id]
        values.append(
            {
                "activation_id": f"activation-{look.decision_hash}",
                "trial_id": trial.id,
                "look_id": look.id,
                "definition_hash": trial.definition_hash,
                "decision_hash": look.decision_hash,
                "family_id": trial.family_id,
                "experiment_id": trial.experiment_id,
                "candidate_id": trial.candidate_id,
                "baseline_id": trial.baseline_id,
                "pipeline_cohort": trial.pipeline_cohort,
                "activated_at": activation_time,
                "activated_by": PROMOTION_ACTIVATED_BY,
            }
        )
    if not values:
        return 0
    statement = (
        insert(AccuracyPromotionActivation)
        .values(values)
        .on_conflict_do_nothing(constraint="uq_accuracy_promotion_activations_trial")
        .returning(AccuracyPromotionActivation.id)
    )
    inserted_ids = (await session.execute(statement)).scalars().all()
    return len(inserted_ids)


def promotion_family_is_terminal(trials: Iterable[AccuracyPromotionTrial]) -> bool:
    """Return true only when a non-empty frozen family has no interim trial."""

    materialized = tuple(trials)
    return bool(
        materialized
        and all(trial.status != AccuracyPromotionTrialStatus.COLLECTING for trial in materialized)
    )


async def run_accuracy_promotion_protocol(
    session: AsyncSession,
    *,
    evaluated_at: datetime,
    pipeline_fingerprint: str,
) -> PromotionProtocolRun:
    """Atomically preregister, collect and make each trial's sole fixed look."""

    boundary = _aware_utc(evaluated_at, field="evaluated_at")
    await _advisory_lock(session)
    trials, registered = await _register_family(
        session,
        registered_at=boundary,
        pipeline_fingerprint=pipeline_fingerprint,
    )
    members, looks = await _evaluate_trials(
        session,
        trials,
        pipeline_fingerprint=pipeline_fingerprint,
    )
    activations = await _activate_terminal_family(session)
    return PromotionProtocolRun(
        family_id=PROMOTION_REGISTRATION_EPOCH,
        trials=len(trials),
        registered_trials=registered,
        recorded_members=members,
        recorded_looks=looks,
        recorded_activations=activations,
    )


async def load_effective_accuracy_promotion_manifest(
    session: AsyncSession,
    *,
    pipeline_fingerprint: str,
    current_pipeline_base_cohort: str,
) -> EffectivePromotionManifest:
    """Load only activations compatible with the currently running source.

    An immutable activation from an older code fingerprint remains auditable in
    PostgreSQL but cannot affect the current pipeline or its manifest hash.
    """

    rows = list(
        (
            await session.execute(
                select(AccuracyPromotionActivation).where(
                    AccuracyPromotionActivation.family_id == PROMOTION_REGISTRATION_EPOCH
                )
            )
        )
        .scalars()
        .all()
    )
    compatible: list[EffectivePromotion] = []
    for row in rows:
        promotion = effective_promotion_for_current_source(
            row,
            pipeline_fingerprint=pipeline_fingerprint,
            current_pipeline_base_cohort=current_pipeline_base_cohort,
        )
        if promotion is not None:
            compatible.append(promotion)
    return build_effective_promotion_manifest(compatible)
