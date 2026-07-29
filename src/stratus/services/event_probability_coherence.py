from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from enum import StrEnum
from itertools import pairwise

EVENT_PROBABILITY_MASS_TOLERANCE = Decimal("0.001000")
EVENT_PROBABILITY_QUANTUM = Decimal("0.000001")
EVENT_PROBABILITY_COHERENCE_PROTOCOL = "event-probability-coherence-v1"


class EventCoherenceReason(StrEnum):
    BUCKET_COUNT_INSUFFICIENT = "event_bucket_count_insufficient"
    BUCKET_IDENTITY_DUPLICATE = "event_bucket_identity_duplicate"
    EVENT_IDENTITY_MISMATCH = "event_identity_mismatch"
    EVENT_SEMANTICS_UNVERIFIABLE = "event_semantics_unverifiable"
    EVENT_SEMANTICS_MISMATCH = "event_semantics_mismatch"
    BUCKET_BOUNDS_INVALID = "event_bucket_bounds_invalid"
    PARTITION_NOT_EXHAUSTIVE = "event_bucket_partition_not_exhaustive"
    PARTITION_GAP = "event_bucket_partition_gap"
    PARTITION_OVERLAP = "event_bucket_partition_overlap"
    BUCKET_INELIGIBLE = "event_bucket_ineligible"
    PROBABILITY_VECTOR_INCOMPLETE = "event_probability_vector_incomplete"
    PROBABILITY_INVALID = "event_probability_invalid"
    PROBABILITY_TIME_MISMATCH = "event_probability_time_mismatch"
    PROBABILITY_HORIZON_MISMATCH = "event_probability_horizon_mismatch"
    PROBABILITY_COHORT_MISMATCH = "event_probability_cohort_mismatch"
    PROBABILITY_ANCHOR_INCOMPLETE = "event_probability_anchor_incomplete"
    PROBABILITY_ANCHOR_BATCH_MISMATCH = (
        "event_probability_anchor_batch_mismatch"
    )
    PROBABILITY_MASS_OUT_OF_TOLERANCE = (
        "event_probability_mass_out_of_tolerance"
    )
    PROBABILITY_NOT_MATERIALIZED = "event_probability_not_materialized"
    PROBABILITY_COHORT_STALE = "event_probability_cohort_stale"


def pipeline_cohort_from_components(components: Mapping[str, object]) -> str | None:
    pipeline = components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    for key in ("cohort", "version"):
        value = pipeline.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def event_probability_distribution_sha256(
    probabilities: Mapping[int, Decimal],
) -> str:
    """Hash an exact, sorted six-decimal event distribution."""

    rows: list[list[int | str]] = []
    for market_id, probability in sorted(probabilities.items()):
        if market_id <= 0:
            raise ValueError("distribution market ids must be positive")
        if (
            not probability.is_finite()
            or probability < 0
            or probability > 1
            or probability != probability.quantize(EVENT_PROBABILITY_QUANTUM)
        ):
            raise ValueError("distribution probabilities must be exact six-decimal values")
        rows.append([market_id, format(probability, ".6f")])
    if not rows:
        raise ValueError("distribution cannot be empty")
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class EventBucketProbability:
    """One existing bucket and its latest material probability evidence.

    Missing probability or anchor fields are represented as ``None``. They are
    never inferred from neighbouring buckets: the event fails closed instead.
    """

    market_id: int
    event_id: str
    station_id: int | None
    market_date: date
    kind: str
    resolves_at: datetime
    resolution_source: str | None
    bucket_low_c: Decimal | None
    bucket_high_c: Decimal | None
    eligible: bool
    probability: Decimal | None
    probability_ts: datetime | None
    horizon_h: Decimal | None
    pipeline_cohort: str | None
    anchor_snapshot_id: int | None
    anchor_market_id: int | None
    anchor_batch_id: int | None
    anchor_eligible: bool


@dataclass(frozen=True, slots=True)
class EventCoherenceResult:
    event_id: str
    reason: EventCoherenceReason | None
    detail: str | None
    bucket_count: int
    probability_count: int
    probability_total_before: Decimal | None
    mass_deviation: Decimal | None
    normalized: bool
    normalization_scale: Decimal | None
    probability_ts: datetime | None
    pipeline_cohort: str | None
    anchor_batch_id: int | None
    probabilities: tuple[tuple[int, Decimal], ...]

    @property
    def passed(self) -> bool:
        return self.reason is None

    @property
    def probability_by_market(self) -> dict[int, Decimal]:
        return dict(self.probabilities)

    def metadata(self) -> dict[str, object]:
        """Return compact, JSON-safe diagnostics for operational logging."""

        return {
            "event_id": self.event_id,
            "reason": self.reason.value if self.reason is not None else None,
            "detail": self.detail,
            "bucket_count": self.bucket_count,
            "probability_count": self.probability_count,
            "probability_total_before": (
                str(self.probability_total_before)
                if self.probability_total_before is not None
                else None
            ),
            "mass_deviation": (
                str(self.mass_deviation)
                if self.mass_deviation is not None
                else None
            ),
            "normalized": self.normalized,
            "normalization_scale": (
                str(self.normalization_scale)
                if self.normalization_scale is not None
                else None
            ),
            "probability_ts": (
                self.probability_ts.isoformat()
                if self.probability_ts is not None
                else None
            ),
            "pipeline_cohort": self.pipeline_cohort,
            "anchor_batch_id": self.anchor_batch_id,
        }


def _utc(value: datetime) -> datetime | None:
    if value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(UTC)


def _result(
    buckets: tuple[EventBucketProbability, ...],
    *,
    reason: EventCoherenceReason | None,
    detail: str | None = None,
    probability_total: Decimal | None = None,
    normalized: bool = False,
    normalization_scale: Decimal | None = None,
    probability_ts: datetime | None = None,
    pipeline_cohort: str | None = None,
    anchor_batch_id: int | None = None,
    probabilities: tuple[tuple[int, Decimal], ...] = (),
) -> EventCoherenceResult:
    event_ids = sorted({bucket.event_id.strip() for bucket in buckets})
    event_id = event_ids[0] if len(event_ids) == 1 else "<mixed>"
    return EventCoherenceResult(
        event_id=event_id,
        reason=reason,
        detail=detail,
        bucket_count=len(buckets),
        probability_count=sum(bucket.probability is not None for bucket in buckets),
        probability_total_before=probability_total,
        mass_deviation=(
            abs(probability_total - Decimal("1"))
            if probability_total is not None
            else None
        ),
        normalized=normalized,
        normalization_scale=normalization_scale,
        probability_ts=probability_ts,
        pipeline_cohort=pipeline_cohort,
        anchor_batch_id=anchor_batch_id,
        probabilities=probabilities,
    )


def _semantic_signature(
    bucket: EventBucketProbability,
    *,
    require_resolution_source: bool,
) -> tuple[object, ...] | None:
    resolves_at = _utc(bucket.resolves_at)
    source = bucket.resolution_source.strip() if bucket.resolution_source else ""
    kind = bucket.kind.strip().lower()
    if (
        bucket.station_id is None
        or resolves_at is None
        or not kind
        or (require_resolution_source and not source)
    ):
        return None
    common: tuple[object, ...] = (
        bucket.station_id,
        bucket.market_date,
        kind,
        resolves_at,
    )
    return (*common, source) if require_resolution_source else common


def _ordered_partition(
    buckets: tuple[EventBucketProbability, ...],
) -> tuple[EventBucketProbability, ...]:
    return tuple(
        sorted(
            buckets,
            key=lambda bucket: (
                bucket.bucket_low_c is not None,
                bucket.bucket_low_c or Decimal("0"),
                bucket.market_id,
            ),
        )
    )


def _rounded_distribution(
    buckets: tuple[EventBucketProbability, ...],
    *,
    total: Decimal,
) -> tuple[tuple[int, Decimal], ...]:
    ordered = tuple(sorted(buckets, key=lambda bucket: bucket.market_id))
    rounded = [
        (bucket.probability / total).quantize(
            EVENT_PROBABILITY_QUANTUM,
            rounding=ROUND_HALF_EVEN,
        )
        for bucket in ordered
        if bucket.probability is not None
    ]
    residual = Decimal("1") - sum(rounded, start=Decimal("0"))
    adjustment_index = max(
        range(len(rounded)),
        key=lambda index: (rounded[index], -ordered[index].market_id),
    )
    rounded[adjustment_index] += residual
    if any(value < 0 or value > 1 for value in rounded):
        raise ArithmeticError("normalized event distribution escaped [0, 1]")
    return tuple(
        (bucket.market_id, probability)
        for bucket, probability in zip(ordered, rounded, strict=True)
    )


def _assess_event_probability_coherence(
    rows: list[EventBucketProbability] | tuple[EventBucketProbability, ...],
    *,
    mass_tolerance: Decimal = EVENT_PROBABILITY_MASS_TOLERANCE,
    require_trade_context: bool,
) -> EventCoherenceResult:
    """Prove event semantics and return an exact six-decimal distribution.

    Normalization is allowed only for a complete, contiguous, exhaustive
    partition whose probabilities share one timestamp, horizon, pipeline
    cohort, and CLOB batch. A missing bucket or material mass discrepancy is
    never repaired.
    """

    buckets = tuple(rows)
    if (
        not mass_tolerance.is_finite()
        or mass_tolerance < 0
        or mass_tolerance > Decimal("0.01")
    ):
        raise ValueError("mass_tolerance must be in [0, 0.01]")
    if len(buckets) < 2:
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_COUNT_INSUFFICIENT,
        )
    market_ids = [bucket.market_id for bucket in buckets]
    if len(set(market_ids)) != len(market_ids):
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_IDENTITY_DUPLICATE,
        )
    event_ids = {bucket.event_id.strip() for bucket in buckets}
    if len(event_ids) != 1 or not next(iter(event_ids), ""):
        return _result(
            buckets,
            reason=EventCoherenceReason.EVENT_IDENTITY_MISMATCH,
            detail=",".join(sorted(event_ids)),
        )
    signatures = [
        _semantic_signature(
            bucket,
            require_resolution_source=require_trade_context,
        )
        for bucket in buckets
    ]
    if any(signature is None for signature in signatures):
        return _result(
            buckets,
            reason=EventCoherenceReason.EVENT_SEMANTICS_UNVERIFIABLE,
        )
    if len(set(signatures)) != 1:
        return _result(
            buckets,
            reason=EventCoherenceReason.EVENT_SEMANTICS_MISMATCH,
            detail=",".join(
                str(bucket.market_id)
                for bucket in sorted(buckets, key=lambda row: row.market_id)
            ),
        )

    if any(
        bound is not None and not bound.is_finite()
        for bucket in buckets
        for bound in (bucket.bucket_low_c, bucket.bucket_high_c)
    ):
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_BOUNDS_INVALID,
            detail="non-finite bucket boundary",
        )

    partition = _ordered_partition(buckets)
    if any(
        bucket.bucket_low_c is not None
        and bucket.bucket_high_c is not None
        and bucket.bucket_low_c >= bucket.bucket_high_c
        for bucket in partition
    ):
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_BOUNDS_INVALID,
        )
    if partition[0].bucket_low_c is not None or partition[-1].bucket_high_c is not None:
        return _result(
            buckets,
            reason=EventCoherenceReason.PARTITION_NOT_EXHAUSTIVE,
        )
    if any(
        bucket.bucket_low_c is None and index != 0
        or bucket.bucket_high_c is None and index != len(partition) - 1
        for index, bucket in enumerate(partition)
    ):
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_BOUNDS_INVALID,
        )
    for left, right in pairwise(partition):
        if left.bucket_high_c is None or right.bucket_low_c is None:
            return _result(
                buckets,
                reason=EventCoherenceReason.BUCKET_BOUNDS_INVALID,
            )
        if left.bucket_high_c < right.bucket_low_c:
            return _result(
                buckets,
                reason=EventCoherenceReason.PARTITION_GAP,
                detail=f"{left.bucket_high_c} < {right.bucket_low_c}",
            )
        if left.bucket_high_c > right.bucket_low_c:
            return _result(
                buckets,
                reason=EventCoherenceReason.PARTITION_OVERLAP,
                detail=f"{left.bucket_high_c} > {right.bucket_low_c}",
            )
    if require_trade_context and not all(bucket.eligible for bucket in buckets):
        ineligible = sorted(bucket.market_id for bucket in buckets if not bucket.eligible)
        return _result(
            buckets,
            reason=EventCoherenceReason.BUCKET_INELIGIBLE,
            detail=",".join(str(market_id) for market_id in ineligible),
        )
    if any(
        bucket.probability is None
        or bucket.probability_ts is None
        or bucket.horizon_h is None
        or bucket.pipeline_cohort is None
        for bucket in buckets
    ):
        missing = sorted(
            bucket.market_id
            for bucket in buckets
            if bucket.probability is None
            or bucket.probability_ts is None
            or bucket.horizon_h is None
            or bucket.pipeline_cohort is None
        )
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE,
            detail=",".join(str(market_id) for market_id in missing),
        )
    probabilities = tuple(bucket.probability for bucket in buckets)
    horizons = tuple(bucket.horizon_h for bucket in buckets)
    if any(
        probability is None
        or not probability.is_finite()
        or probability < 0
        or probability > 1
        for probability in probabilities
    ) or any(horizon is None or not horizon.is_finite() or horizon < 0 for horizon in horizons):
        invalid = sorted(
            bucket.market_id
            for bucket in buckets
            if bucket.probability is None
            or not bucket.probability.is_finite()
            or bucket.probability < 0
            or bucket.probability > 1
            or bucket.horizon_h is None
            or not bucket.horizon_h.is_finite()
            or bucket.horizon_h < 0
        )
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_INVALID,
            detail=",".join(str(market_id) for market_id in invalid),
        )
    probability_times = {_utc(bucket.probability_ts) for bucket in buckets}  # type: ignore[arg-type]
    if None in probability_times or len(probability_times) != 1:
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_TIME_MISMATCH,
            detail=",".join(
                sorted(
                    "missing-or-naive" if value is None else value.isoformat()
                    for value in probability_times
                )
            ),
        )
    horizon_values = {bucket.horizon_h for bucket in buckets}
    if len(horizon_values) != 1:
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_HORIZON_MISMATCH,
            detail=",".join(sorted(str(value) for value in horizon_values)),
        )
    cohorts = {
        bucket.pipeline_cohort.strip()
        for bucket in buckets
        if bucket.pipeline_cohort is not None
    }
    if len(cohorts) != 1 or not next(iter(cohorts), ""):
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_COHORT_MISMATCH,
            detail=",".join(sorted(cohorts)),
        )
    anchor_batch_id: int | None = None
    if require_trade_context:
        invalid_anchor_markets = sorted(
            bucket.market_id
            for bucket in buckets
            if (
                bucket.anchor_snapshot_id is None
                or bucket.anchor_market_id != bucket.market_id
                or bucket.anchor_batch_id is None
                or not bucket.anchor_eligible
            )
        )
        anchor_snapshot_ids = [bucket.anchor_snapshot_id for bucket in buckets]
        duplicate_anchor_ids = sorted(
            {
                snapshot_id
                for snapshot_id in anchor_snapshot_ids
                if snapshot_id is not None
                and anchor_snapshot_ids.count(snapshot_id) > 1
            }
        )
        if invalid_anchor_markets or duplicate_anchor_ids:
            detail_parts = []
            if invalid_anchor_markets:
                detail_parts.append(
                    "markets="
                    + ",".join(str(market_id) for market_id in invalid_anchor_markets)
                )
            if duplicate_anchor_ids:
                detail_parts.append(
                    "duplicate_snapshots="
                    + ",".join(str(snapshot_id) for snapshot_id in duplicate_anchor_ids)
                )
            return _result(
                buckets,
                reason=EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE,
                detail=";".join(detail_parts),
            )
        anchor_batches = {bucket.anchor_batch_id for bucket in buckets}
        if len(anchor_batches) != 1:
            return _result(
                buckets,
                reason=EventCoherenceReason.PROBABILITY_ANCHOR_BATCH_MISMATCH,
                detail=",".join(sorted(str(batch_id) for batch_id in anchor_batches)),
            )
        anchor_batch_id = next(iter(anchor_batches))

    total = sum(
        (bucket.probability for bucket in buckets if bucket.probability is not None),
        start=Decimal("0"),
    )
    if total <= 0 or abs(total - Decimal("1")) > mass_tolerance:
        return _result(
            buckets,
            reason=EventCoherenceReason.PROBABILITY_MASS_OUT_OF_TOLERANCE,
            probability_total=total,
            probability_ts=next(iter(probability_times)),
            pipeline_cohort=next(iter(cohorts)),
            anchor_batch_id=anchor_batch_id,
        )
    normalized = total != Decimal("1")
    coherent = _rounded_distribution(buckets, total=total)
    if sum((value for _market_id, value in coherent), start=Decimal("0")) != 1:
        raise ArithmeticError("normalized event probabilities do not sum to one")
    return _result(
        buckets,
        reason=None,
        probability_total=total,
        normalized=normalized,
        normalization_scale=(Decimal("1") / total if normalized else Decimal("1")),
        probability_ts=next(iter(probability_times)),
        pipeline_cohort=next(iter(cohorts)),
        anchor_batch_id=anchor_batch_id,
        probabilities=coherent,
    )


def assess_event_probability_materialization(
    rows: list[EventBucketProbability] | tuple[EventBucketProbability, ...],
    *,
    mass_tolerance: Decimal = EVENT_PROBABILITY_MASS_TOLERANCE,
) -> EventCoherenceResult:
    """Prove the model vector independently of execution-market availability.

    Accuracy evidence needs an exhaustive, timestamp-bound probability cohort,
    but it must not depend on a CLOB witness or trade eligibility.  Those are a
    separate, stricter decision-time proof enforced by
    :func:`assess_event_probability_coherence` before any signal is actionable.
    """

    return _assess_event_probability_coherence(
        rows,
        mass_tolerance=mass_tolerance,
        require_trade_context=False,
    )


def assess_event_probability_coherence(
    rows: list[EventBucketProbability] | tuple[EventBucketProbability, ...],
    *,
    mass_tolerance: Decimal = EVENT_PROBABILITY_MASS_TOLERANCE,
) -> EventCoherenceResult:
    """Prove the probability vector and its executable trade context."""

    return _assess_event_probability_coherence(
        rows,
        mass_tolerance=mass_tolerance,
        require_trade_context=True,
    )
