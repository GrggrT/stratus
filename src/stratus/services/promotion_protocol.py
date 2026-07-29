from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext
from enum import StrEnum
from typing import Any

PROMOTION_PROTOCOL_VERSION = "fixed-range-brier-v3"
PROMOTION_PROTOCOL_EPOCH = "fixed-range-brier-v3-20260714"
PROMOTION_TEST_METHOD = "predictable-range-hoeffding-v1"
PROMOTION_TARGET_BLOCKS = 60
_MAX_FAMILY_ALPHA = Decimal("0.05")
# PostgreSQL's transcendental numeric functions expose fewer reliable decimal
# places than the storage columns.  Eighteen places remain far beyond any
# decision-relevant precision while allowing Python and PostgreSQL to apply the
# same conservative outward rounding before hashing the fixed decision.
_BOUND_SCALE = Decimal("0.000000000000000001")


class PromotionProtocolError(ValueError):
    """A fail-closed violation of the pre-registered promotion protocol."""


class PromotionDecisionStatus(StrEnum):
    INCOMPLETE = "incomplete"
    PASSED = "passed"
    FAILED = "failed"


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise PromotionProtocolError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime, *, field: str) -> str:
    return _utc(value, field=field).isoformat(timespec="microseconds")


def _decimal(value: float | Decimal, *, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PromotionProtocolError(f"{field} must be finite")
    decimal = value if isinstance(value, Decimal) else Decimal(str(value))
    if not decimal.is_finite():
        raise PromotionProtocolError(f"{field} must be finite")
    return decimal


def _number(value: float | Decimal, *, field: str) -> str:
    decimal = _decimal(value, field=field)
    if decimal == 0:
        return "0"
    # ``Decimal.normalize`` applies the ambient context and can round the exact
    # 60-place binomial tail. String trimming is context-free.
    rendered = format(decimal, "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_parts_hash(*parts: str) -> str:
    """Hash an ordered, unambiguous UTF-8 sequence.

    Each value is byte-length prefixed.  PostgreSQL revision 0033 implements
    the same representation so definition and decision hashes are derived by
    both trust domains rather than accepted as opaque application input.
    """

    payload = bytearray()
    for part in parts:
        encoded = part.encode("utf-8")
        payload.extend(str(len(encoded)).encode("ascii"))
        payload.extend(b":")
        payload.extend(encoded)
    return hashlib.sha256(payload).hexdigest()


def _required_text(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise PromotionProtocolError(f"{field} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class PromotionDefinition:
    """A complete, immutable fixed-look experiment definition.

    ``starts_at`` is the prospective evidence boundary. The statistical look
    happens only when ``target_blocks`` independent blocks are available.
    """

    family_id: str
    experiment_id: str
    candidate_id: str
    baseline_id: str
    pipeline_cohort: str
    registered_at: datetime
    starts_at: datetime
    target_blocks: int
    alpha: float
    expert: str
    legacy_expert_version: str
    expert_version: str
    experiment_fingerprint: str
    station_id: int
    horizon_bucket: str
    kind: str
    scope_key: str
    family_member_index: int
    family_size: int
    family_alpha: float
    minimum_brier_improvement: float = 0.0

    def __post_init__(self) -> None:
        for field in (
            "family_id",
            "experiment_id",
            "candidate_id",
            "baseline_id",
            "pipeline_cohort",
            "expert",
            "legacy_expert_version",
            "expert_version",
            "experiment_fingerprint",
            "horizon_bucket",
            "kind",
            "scope_key",
        ):
            _required_text(str(getattr(self, field)), field=field)
        registered_at = _utc(self.registered_at, field="registered_at")
        starts_at = _utc(self.starts_at, field="starts_at")
        if starts_at < registered_at:
            raise PromotionProtocolError("starts_at must not precede registration")
        if self.candidate_id == self.baseline_id:
            raise PromotionProtocolError("candidate_id and baseline_id must differ")
        if self.target_blocks != PROMOTION_TARGET_BLOCKS:
            raise PromotionProtocolError(
                f"target_blocks must equal the fixed protocol look {PROMOTION_TARGET_BLOCKS}"
            )
        alpha = _decimal(self.alpha, field="alpha")
        if alpha <= 0 or alpha > _MAX_FAMILY_ALPHA:
            raise PromotionProtocolError("alpha must be in (0, 0.05]")
        minimum = _decimal(
            self.minimum_brier_improvement,
            field="minimum_brier_improvement",
        )
        if minimum < 0 or minimum > 1:
            raise PromotionProtocolError("minimum_brier_improvement must be in [0, 1]")
        if self.station_id <= 0:
            raise PromotionProtocolError("station_id must be positive")
        if self.family_size <= 0:
            raise PromotionProtocolError("family_size must be positive")
        if not 1 <= self.family_member_index <= self.family_size:
            raise PromotionProtocolError("family_member_index must be in the frozen family")
        family_alpha = _decimal(self.family_alpha, field="family_alpha")
        if family_alpha <= 0 or family_alpha > _MAX_FAMILY_ALPHA:
            raise PromotionProtocolError("family_alpha must be in (0, 0.05]")
        if alpha * self.family_size > family_alpha:
            raise PromotionProtocolError("allocated alpha exceeds family alpha")

    @property
    def definition_hash(self) -> str:
        return promotion_definition_hash(self)


def _definition_payload(definition: PromotionDefinition) -> dict[str, object]:
    return {
        "protocol": PROMOTION_PROTOCOL_VERSION,
        "protocol_epoch": PROMOTION_PROTOCOL_EPOCH,
        "test_method": PROMOTION_TEST_METHOD,
        "family_id": definition.family_id.strip(),
        "experiment_id": definition.experiment_id.strip(),
        "candidate_id": definition.candidate_id.strip(),
        "baseline_id": definition.baseline_id.strip(),
        "pipeline_cohort": definition.pipeline_cohort.strip(),
        "registered_at": _timestamp(definition.registered_at, field="registered_at"),
        "starts_at": _timestamp(definition.starts_at, field="starts_at"),
        "target_blocks": definition.target_blocks,
        "alpha": _number(definition.alpha, field="alpha"),
        "minimum_brier_improvement": _number(
            definition.minimum_brier_improvement,
            field="minimum_brier_improvement",
        ),
        "expert": definition.expert.strip(),
        "legacy_expert_version": definition.legacy_expert_version.strip(),
        "expert_version": definition.expert_version.strip(),
        "experiment_fingerprint": definition.experiment_fingerprint.strip(),
        "station_id": definition.station_id,
        "horizon_bucket": definition.horizon_bucket.strip(),
        "kind": definition.kind.strip(),
        "scope_key": definition.scope_key.strip(),
        "family_member_index": definition.family_member_index,
        "family_size": definition.family_size,
        "family_alpha": _number(definition.family_alpha, field="family_alpha"),
    }


def promotion_definition_hash(definition: PromotionDefinition) -> str:
    """Return the content address of every pre-registered decision parameter."""

    payload = _definition_payload(definition)
    ordered_fields = (
        "protocol",
        "protocol_epoch",
        "test_method",
        "family_id",
        "experiment_id",
        "candidate_id",
        "baseline_id",
        "pipeline_cohort",
        "registered_at",
        "starts_at",
        "target_blocks",
        "alpha",
        "minimum_brier_improvement",
        "expert",
        "legacy_expert_version",
        "expert_version",
        "experiment_fingerprint",
        "station_id",
        "horizon_bucket",
        "kind",
        "scope_key",
        "family_member_index",
        "family_size",
        "family_alpha",
    )
    return _canonical_parts_hash(
        "accuracy-promotion-definition-v3",
        *(str(payload[field]) for field in ordered_fields),
    )


@dataclass(frozen=True, slots=True)
class PairedBrierBlock:
    """One independent, immutable candidate-versus-baseline score block."""

    block_key: str
    resolved_cutoff: datetime
    baseline_brier: float | Decimal
    candidate_brier: float | Decimal
    predictable_improvement_lower: float | Decimal
    predictable_improvement_upper: float | Decimal
    evidence_hash: str

    def __post_init__(self) -> None:
        _required_text(self.block_key, field="block_key")
        _utc(self.resolved_cutoff, field="resolved_cutoff")
        _required_text(self.evidence_hash, field="evidence_hash")
        baseline = _decimal(self.baseline_brier, field="baseline_brier")
        candidate = _decimal(self.candidate_brier, field="candidate_brier")
        if not Decimal("0") <= baseline <= Decimal("1"):
            raise PromotionProtocolError("baseline_brier must be in [0, 1]")
        if not Decimal("0") <= candidate <= Decimal("1"):
            raise PromotionProtocolError("candidate_brier must be in [0, 1]")
        lower = _decimal(
            self.predictable_improvement_lower,
            field="predictable_improvement_lower",
        )
        upper = _decimal(
            self.predictable_improvement_upper,
            field="predictable_improvement_upper",
        )
        observed = baseline - candidate
        if lower < Decimal("-1") or upper > Decimal("1") or lower > upper:
            raise PromotionProtocolError("predictable improvement range is invalid")
        if observed < lower or observed > upper:
            raise PromotionProtocolError("observed improvement is outside its predictable range")

    @property
    def delta_brier(self) -> float:
        return float(
            _decimal(self.candidate_brier, field="candidate_brier")
            - _decimal(self.baseline_brier, field="baseline_brier")
        )


def _block_payload(block: PairedBrierBlock) -> dict[str, object]:
    return {
        "block_key": block.block_key.strip(),
        "resolved_cutoff": _timestamp(block.resolved_cutoff, field="resolved_cutoff"),
        "baseline_brier": _number(block.baseline_brier, field="baseline_brier"),
        "candidate_brier": _number(block.candidate_brier, field="candidate_brier"),
        "evidence_hash": block.evidence_hash.strip(),
    }


def _deduplicated_blocks(blocks: list[PairedBrierBlock]) -> list[PairedBrierBlock]:
    by_key: dict[str, PairedBrierBlock] = {}
    payload_by_key: dict[str, dict[str, object]] = {}
    for block in blocks:
        key = block.block_key.strip()
        payload = _block_payload(block)
        previous = payload_by_key.get(key)
        if previous is not None and previous != payload:
            raise PromotionProtocolError(f"conflicting evidence for independent block {key!r}")
        by_key[key] = block
        payload_by_key[key] = payload
    return sorted(
        by_key.values(),
        key=lambda block: (
            _utc(block.resolved_cutoff, field="resolved_cutoff"),
            block.block_key.strip(),
        ),
    )


def _quantize_up(value: Decimal) -> Decimal:
    return value.quantize(_BOUND_SCALE, rounding=ROUND_CEILING)


def _quantize_down(value: Decimal) -> Decimal:
    return value.quantize(_BOUND_SCALE, rounding=ROUND_FLOOR)


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    definition_hash: str
    status: PromotionDecisionStatus
    target_blocks: int
    observed_blocks: int
    selected_block_keys: tuple[str, ...]
    mean_delta_brier: Decimal | None
    wins: int | None
    losses: int | None
    ties: int | None
    nonwins: int | None
    sum_predictable_range_squared: Decimal | None
    confidence_radius: Decimal | None
    mean_improvement_lcb: Decimal | None
    one_sided_p_bound: Decimal | None
    alpha: float
    minimum_brier_improvement: float
    decision_hash: str

    @property
    def passed(self) -> bool:
        return self.status is PromotionDecisionStatus.PASSED


def _decision_hash(
    definition: PromotionDefinition,
    *,
    status: PromotionDecisionStatus,
    observed_blocks: int,
    selected: tuple[PairedBrierBlock, ...],
    mean_delta_brier: Decimal | None,
    wins: int | None,
    losses: int | None,
    ties: int | None,
    nonwins: int | None,
    sum_predictable_range_squared: Decimal | None,
    confidence_radius: Decimal | None,
    mean_improvement_lcb: Decimal | None,
    one_sided_p_bound: Decimal | None,
) -> str:
    evidence_set_hash = hashlib.sha256(
        "".join(block.evidence_hash.strip() for block in selected).encode("ascii")
    ).hexdigest()
    payload: dict[str, Any] = {
        "protocol": PROMOTION_PROTOCOL_VERSION,
        "protocol_epoch": PROMOTION_PROTOCOL_EPOCH,
        "test_method": PROMOTION_TEST_METHOD,
        "definition_hash": definition.definition_hash,
        "status": status.value,
        "target_blocks": definition.target_blocks,
        "observed_blocks": observed_blocks,
        "evidence_set_hash": evidence_set_hash,
        "mean_delta_brier": (
            _number(mean_delta_brier, field="mean_delta_brier")
            if mean_delta_brier is not None
            else None
        ),
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "nonwins": nonwins,
        "sum_predictable_range_squared": (
            _number(
                sum_predictable_range_squared,
                field="sum_predictable_range_squared",
            )
            if sum_predictable_range_squared is not None
            else None
        ),
        "confidence_radius": (
            _number(confidence_radius, field="confidence_radius")
            if confidence_radius is not None
            else None
        ),
        "mean_improvement_lcb": (
            _number(mean_improvement_lcb, field="mean_improvement_lcb")
            if mean_improvement_lcb is not None
            else None
        ),
        "one_sided_p_bound": (
            _number(one_sided_p_bound, field="one_sided_p_bound")
            if one_sided_p_bound is not None
            else None
        ),
    }
    ordered_fields = (
        "protocol",
        "protocol_epoch",
        "test_method",
        "definition_hash",
        "status",
        "target_blocks",
        "observed_blocks",
        "evidence_set_hash",
        "mean_delta_brier",
        "wins",
        "losses",
        "ties",
        "nonwins",
        "sum_predictable_range_squared",
        "confidence_radius",
        "mean_improvement_lcb",
        "one_sided_p_bound",
    )
    return _canonical_parts_hash(
        "accuracy-promotion-decision-v3",
        *("" if payload[field] is None else str(payload[field]) for field in ordered_fields),
    )


def evaluate_fixed_promotion(
    definition: PromotionDefinition,
    blocks: list[PairedBrierBlock],
) -> PromotionDecision:
    """Evaluate one fixed statistical look without interim significance peeking.

    Identical duplicate rows count once. Conflicting duplicates fail closed.
    Blocks resolving before the prospective boundary are ignored. Once the
    target is reached, only the first fixed set ordered by resolution cutoff and
    block key is used; later observations can never reverse the decision.
    """

    starts_at = _utc(definition.starts_at, field="starts_at")
    eligible = [
        block
        for block in _deduplicated_blocks(blocks)
        if _utc(block.resolved_cutoff, field="resolved_cutoff") >= starts_at
    ]
    observed = min(len(eligible), definition.target_blocks)
    if observed < definition.target_blocks:
        status = PromotionDecisionStatus.INCOMPLETE
        decision_hash = _decision_hash(
            definition,
            status=status,
            observed_blocks=observed,
            selected=(),
            mean_delta_brier=None,
            wins=None,
            losses=None,
            ties=None,
            nonwins=None,
            sum_predictable_range_squared=None,
            confidence_radius=None,
            mean_improvement_lcb=None,
            one_sided_p_bound=None,
        )
        return PromotionDecision(
            definition_hash=definition.definition_hash,
            status=status,
            target_blocks=definition.target_blocks,
            observed_blocks=observed,
            selected_block_keys=(),
            mean_delta_brier=None,
            wins=None,
            losses=None,
            ties=None,
            nonwins=None,
            sum_predictable_range_squared=None,
            confidence_radius=None,
            mean_improvement_lcb=None,
            one_sided_p_bound=None,
            alpha=definition.alpha,
            minimum_brier_improvement=definition.minimum_brier_improvement,
            decision_hash=decision_hash,
        )

    selected = tuple(eligible[: definition.target_blocks])
    paired_decimals = tuple(
        (
            _decimal(block.candidate_brier, field="candidate_brier"),
            _decimal(block.baseline_brier, field="baseline_brier"),
        )
        for block in selected
    )
    decimal_deltas = tuple(candidate - baseline for candidate, baseline in paired_decimals)
    mean_delta_decimal = sum(decimal_deltas, start=Decimal("0")) / Decimal(
        definition.target_blocks
    )
    wins = sum(candidate < baseline for candidate, baseline in paired_decimals)
    ties = sum(candidate == baseline for candidate, baseline in paired_decimals)
    losses = definition.target_blocks - wins - ties
    nonwins = definition.target_blocks - wins
    ranges = tuple(
        _decimal(block.predictable_improvement_upper, field="upper")
        - _decimal(block.predictable_improvement_lower, field="lower")
        for block in selected
    )
    sum_range_squared = sum((width * width for width in ranges), start=Decimal("0"))
    mean_improvement = -mean_delta_decimal
    minimum = _decimal(
        definition.minimum_brier_improvement,
        field="minimum_brier_improvement",
    )
    with localcontext() as context:
        context.prec = 100
        if sum_range_squared == 0:
            confidence_radius = Decimal("0").quantize(_BOUND_SCALE)
            one_sided_p_bound = (
                Decimal("0").quantize(_BOUND_SCALE)
                if mean_improvement > minimum
                else Decimal("1").quantize(_BOUND_SCALE)
            )
        else:
            alpha = _decimal(definition.alpha, field="alpha")
            confidence_radius = _quantize_up(
                (
                    (Decimal("1") / alpha).ln()
                    * sum_range_squared
                    / (Decimal("2") * Decimal(definition.target_blocks) ** 2)
                ).sqrt()
            )
            gap = max(Decimal("0"), mean_improvement - minimum)
            one_sided_p_bound = _quantize_up(
                (
                    -Decimal("2")
                    * Decimal(definition.target_blocks) ** 2
                    * gap
                    * gap
                    / sum_range_squared
                ).exp()
            )
            one_sided_p_bound = min(Decimal("1"), one_sided_p_bound)
        mean_improvement_lcb = _quantize_down(mean_improvement - confidence_radius)
    status = (
        PromotionDecisionStatus.PASSED
        if mean_improvement_lcb > minimum
        else PromotionDecisionStatus.FAILED
    )
    decision_hash = _decision_hash(
        definition,
        status=status,
        observed_blocks=observed,
        selected=selected,
        mean_delta_brier=mean_delta_decimal,
        wins=wins,
        losses=losses,
        ties=ties,
        nonwins=nonwins,
        sum_predictable_range_squared=sum_range_squared,
        confidence_radius=confidence_radius,
        mean_improvement_lcb=mean_improvement_lcb,
        one_sided_p_bound=one_sided_p_bound,
    )
    return PromotionDecision(
        definition_hash=definition.definition_hash,
        status=status,
        target_blocks=definition.target_blocks,
        observed_blocks=observed,
        selected_block_keys=tuple(block.block_key.strip() for block in selected),
        mean_delta_brier=mean_delta_decimal,
        wins=wins,
        losses=losses,
        ties=ties,
        nonwins=nonwins,
        sum_predictable_range_squared=sum_range_squared,
        confidence_radius=confidence_radius,
        mean_improvement_lcb=mean_improvement_lcb,
        one_sided_p_bound=one_sided_p_bound,
        alpha=definition.alpha,
        minimum_brier_improvement=definition.minimum_brier_improvement,
        decision_hash=decision_hash,
    )


@dataclass(frozen=True, slots=True)
class FamilyAlphaAllocation:
    family_id: str
    total_alpha: float
    definition_hashes: tuple[str, ...]


def validate_family_alpha_allocations(
    definitions: list[PromotionDefinition],
    *,
    maximum_family_alpha: float = 0.05,
) -> tuple[FamilyAlphaAllocation, ...]:
    """Validate a Bonferroni allocation for each registered model family."""

    maximum = _decimal(maximum_family_alpha, field="maximum_family_alpha")
    if maximum <= 0 or maximum > _MAX_FAMILY_ALPHA:
        raise PromotionProtocolError("maximum_family_alpha must be in (0, 0.05]")
    seen_experiments: set[str] = set()
    grouped: dict[str, list[PromotionDefinition]] = {}
    for definition in definitions:
        experiment_id = definition.experiment_id.strip()
        if experiment_id in seen_experiments:
            raise PromotionProtocolError(f"duplicate experiment_id {experiment_id!r}")
        seen_experiments.add(experiment_id)
        grouped.setdefault(definition.family_id.strip(), []).append(definition)

    allocations: list[FamilyAlphaAllocation] = []
    for family_id in sorted(grouped):
        family = grouped[family_id]
        total = sum((Decimal(str(item.alpha)) for item in family), start=Decimal("0"))
        if total > maximum:
            raise PromotionProtocolError(
                f"family {family_id!r} alpha allocation {total} exceeds {maximum}"
            )
        allocations.append(
            FamilyAlphaAllocation(
                family_id=family_id,
                total_alpha=float(total),
                definition_hashes=tuple(sorted(item.definition_hash for item in family)),
            )
        )
    return tuple(allocations)


@dataclass(frozen=True, slots=True)
class PromotionActivation:
    activation_id: str
    definition_hash: str
    decision_hash: str
    family_id: str
    experiment_id: str
    candidate_id: str
    baseline_id: str
    pipeline_cohort: str
    activated_at: datetime
    activated_by: str

    def __post_init__(self) -> None:
        for field in (
            "activation_id",
            "definition_hash",
            "decision_hash",
            "family_id",
            "experiment_id",
            "candidate_id",
            "baseline_id",
            "pipeline_cohort",
            "activated_by",
        ):
            _required_text(str(getattr(self, field)), field=field)
        _utc(self.activated_at, field="activated_at")


def create_passed_activation(
    definition: PromotionDefinition,
    decision: PromotionDecision,
    *,
    activation_id: str,
    activated_at: datetime,
    activated_by: str,
) -> PromotionActivation:
    """Bind an operator activation to a completed, passing fixed look."""

    if decision.definition_hash != definition.definition_hash:
        raise PromotionProtocolError("decision does not belong to the definition")
    if not decision.passed:
        raise PromotionProtocolError("only a passed decision can be activated")
    return PromotionActivation(
        activation_id=activation_id,
        definition_hash=definition.definition_hash,
        decision_hash=decision.decision_hash,
        family_id=definition.family_id.strip(),
        experiment_id=definition.experiment_id.strip(),
        candidate_id=definition.candidate_id.strip(),
        baseline_id=definition.baseline_id.strip(),
        pipeline_cohort=definition.pipeline_cohort.strip(),
        activated_at=activated_at,
        activated_by=activated_by,
    )


def _activation_payload(activation: PromotionActivation) -> dict[str, object]:
    return {
        "activation_id": activation.activation_id.strip(),
        "definition_hash": activation.definition_hash.strip(),
        "decision_hash": activation.decision_hash.strip(),
        "family_id": activation.family_id.strip(),
        "experiment_id": activation.experiment_id.strip(),
        "candidate_id": activation.candidate_id.strip(),
        "baseline_id": activation.baseline_id.strip(),
        "pipeline_cohort": activation.pipeline_cohort.strip(),
        "activated_at": _timestamp(activation.activated_at, field="activated_at"),
        "activated_by": activation.activated_by.strip(),
    }


@dataclass(frozen=True, slots=True)
class ActivationManifest:
    protocol: str
    activations: tuple[PromotionActivation, ...]
    manifest_hash: str


def build_activation_manifest(
    activations: list[PromotionActivation],
) -> ActivationManifest:
    """Create a deterministic, content-addressed immutable activation set."""

    by_id: dict[str, PromotionActivation] = {}
    for activation in activations:
        activation_id = activation.activation_id.strip()
        previous = by_id.get(activation_id)
        if (
            previous is not None
            and _activation_payload(previous) != _activation_payload(activation)
        ):
            raise PromotionProtocolError(f"conflicting activation_id {activation_id!r}")
        by_id[activation_id] = activation
    ordered = tuple(
        sorted(
            by_id.values(),
            key=lambda item: (
                _utc(item.activated_at, field="activated_at"),
                item.activation_id.strip(),
            ),
        )
    )
    payload = {
        "protocol": PROMOTION_PROTOCOL_VERSION,
        "activations": [_activation_payload(item) for item in ordered],
    }
    return ActivationManifest(
        protocol=PROMOTION_PROTOCOL_VERSION,
        activations=ordered,
        manifest_hash=_canonical_hash(payload),
    )
