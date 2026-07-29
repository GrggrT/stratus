from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable
from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from stratus.config import Settings
from stratus.db.models import Market, ModelProb
from stratus.repositories.accuracy import AccuracyPredictionValue
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    event_probability_distribution_sha256,
)
from stratus.services.pipeline_identity import probability_pipeline_fingerprint

SEALED_EVIDENCE_PROTOCOL = "sealed-v2"
_PROBABILITY_QUANTUM = Decimal("0.000001")
_BASELINE_TOTAL_TOLERANCE = Decimal("0.01")

_CohortKey = tuple[str, str, str, datetime, str]


def _json_default(value: object) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, Enum):
        return str(value.value)
    raise TypeError(f"cannot hash sealed accuracy value {type(value)!r}")


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _pipeline_version(model_prob: ModelProb) -> str | None:
    components = model_prob.components
    if not isinstance(components, dict):
        return None
    pipeline = components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    cohort = pipeline.get("cohort")
    if isinstance(cohort, str) and cohort:
        return cohort
    version = pipeline.get("version")
    return version if isinstance(version, str) and version else None


def _finite_probability(value: Decimal) -> bool:
    return value.is_finite() and Decimal("0") <= value <= Decimal("1")


def _normalise_probabilities(values: list[Decimal]) -> list[Decimal] | None:
    if not values or any(not _finite_probability(value) for value in values):
        return None
    total = sum(values, start=Decimal("0"))
    if total <= 0:
        return None
    normalised = [(value / total).quantize(_PROBABILITY_QUANTUM) for value in values]
    # Quantisation can leave a few millionths of residual. Assigning it to the
    # largest bucket is deterministic and keeps the persisted vector exactly 1.
    residual = Decimal("1.000000") - sum(normalised, start=Decimal("0"))
    largest = max(range(len(normalised)), key=lambda index: (normalised[index], -index))
    normalised[largest] += residual
    if any(not _finite_probability(value) for value in normalised):
        return None
    return normalised


def _legacy_baseline_matches(
    legacy: AccuracyPredictionValue,
    baseline: ModelProb,
    pipeline_version: str,
) -> bool:
    if (
        legacy.baseline_model_prob_id is not None
        and legacy.baseline_model_prob_id != baseline.id
    ):
        return False
    distribution = legacy.distribution
    stored_id = distribution.get("baseline_model_prob_id")
    if stored_id is not None and stored_id != baseline.id:
        return False
    stored_pipeline = distribution.get("baseline_pipeline_version")
    if stored_pipeline is not None and stored_pipeline != pipeline_version:
        return False
    stored_probability = distribution.get("baseline_probability")
    has_authoritative_marker = (
        legacy.baseline_model_prob_id is not None
        or stored_id is not None
        or stored_pipeline is not None
    )
    if stored_probability is None or not has_authoritative_marker:
        return True
    if (
        isinstance(stored_probability, bool)
        or not isinstance(stored_probability, int | float)
        or not math.isfinite(float(stored_probability))
    ):
        return False
    return abs(Decimal(str(stored_probability)) - baseline.prob) <= _PROBABILITY_QUANTUM


def _materialized_baseline_vector_matches(
    cohort: list[tuple[AccuracyPredictionValue, ModelProb, Market]],
    *,
    event_id: str,
    cohort_ts: datetime,
    pipeline_version: str,
) -> bool:
    """Validate probability-vector provenance without trade-market coupling.

    Sealed accuracy is allowed without an executable CLOB anchor.  It still
    requires the exact event vector published by recompute_probs; a failed or
    incomplete probability materialization can never become accuracy evidence.
    """

    probabilities = {
        market.id: baseline.prob for _legacy, baseline, market in cohort
    }
    if sum(probabilities.values(), start=Decimal("0")) != Decimal("1"):
        return False
    try:
        distribution_hash = event_probability_distribution_sha256(probabilities)
    except ValueError:
        return False
    expected_ts = cohort_ts.isoformat()
    expected_count = len(cohort)
    for _legacy, baseline, market in cohort:
        components = baseline.components
        if not isinstance(components, dict):
            return False
        proof = components.get("event_coherence")
        if not isinstance(proof, dict):
            return False
        try:
            final_probability = Decimal(str(proof.get("final_probability")))
            final_total = Decimal(str(proof.get("event_total_final")))
        except (ArithmeticError, ValueError):
            return False
        if (
            proof.get("protocol") != EVENT_PROBABILITY_COHERENCE_PROTOCOL
            or proof.get("materialized") is not True
            or proof.get("materialization_reason") is not None
            or proof.get("event_id") != event_id
            or proof.get("probability_ts") != expected_ts
            or proof.get("pipeline_cohort") != pipeline_version
            or proof.get("distribution_sha256") != distribution_hash
            or proof.get("bucket_count") != expected_count
            or proof.get("probability_count") != expected_count
            or final_probability != baseline.prob
            or final_total != Decimal("1")
            or market.event_id != event_id
        ):
            return False
    return True


def sealed_expert_version(legacy_version: str) -> str:
    """Return the protocol-bound version used by sealed challenger evidence."""

    return f"{legacy_version}-{SEALED_EVIDENCE_PROTOCOL}"


def sealed_experiment_fingerprint(
    *,
    pipeline_fingerprint: str,
    expert: str,
    legacy_version: str,
    sealed_version: str,
    pipeline_version: str,
) -> str:
    return _canonical_hash(
        {
            "schema": "sealed-legacy-accuracy-v2",
            "probability_pipeline_fingerprint": pipeline_fingerprint,
            "expert": expert,
            "legacy_expert_version": legacy_version,
            "sealed_expert_version": sealed_version,
            "baseline_pipeline_version": pipeline_version,
        }
    )


def _market_payload(market: Market) -> dict[str, Any]:
    return {
        "id": market.id,
        "event_id": market.event_id,
        "station_id": market.station_id,
        "market_date": market.market_date,
        "kind": market.kind,
        "bucket_low_c": market.bucket_low_c,
        "bucket_high_c": market.bucket_high_c,
        "resolves_at": market.resolves_at,
    }


def seal_legacy_accuracy_predictions(
    legacy_rows: Iterable[AccuracyPredictionValue],
    baseline_rows: Iterable[ModelProb],
    market_rows: Iterable[Market],
    settings: Settings,
    *,
    training_cutoff: datetime | None = None,
) -> list[AccuracyPredictionValue]:
    """Convert complete legacy challenger vectors into immutable sealed evidence.

    Inputs must already be the exact point-in-time baseline and market rows. The
    function is deliberately fail-closed per event cohort: missing/ambiguous
    markets, timestamps, baselines, pipeline metadata, or invalid probability
    vectors produce no sealed rows for that cohort.
    """
    markets_by_id: dict[int, Market] = {}
    ambiguous_market_ids: set[int] = set()
    expected_by_event: dict[str, set[int]] = defaultdict(set)
    for market in market_rows:
        if market.id in markets_by_id:
            ambiguous_market_ids.add(market.id)
            continue
        markets_by_id[market.id] = market
        expected_by_event[market.event_id].add(market.id)

    baselines_by_key: dict[tuple[int, datetime], ModelProb] = {}
    ambiguous_baseline_keys: set[tuple[int, datetime]] = set()
    for baseline in baseline_rows:
        baseline_key = (baseline.market_id, baseline.ts)
        if baseline_key in baselines_by_key:
            ambiguous_baseline_keys.add(baseline_key)
            continue
        baselines_by_key[baseline_key] = baseline

    pipeline_fingerprint = probability_pipeline_fingerprint(settings)
    grouped: dict[_CohortKey, list[tuple[AccuracyPredictionValue, ModelProb, Market]]] = (
        defaultdict(list)
    )
    invalid_group_keys: set[_CohortKey] = set()
    for legacy in legacy_rows:
        matched_market = markets_by_id.get(legacy.market_id)
        baseline_key = (legacy.market_id, legacy.ts)
        matched_baseline = baselines_by_key.get(baseline_key)
        if (
            matched_market is None
            or legacy.market_id in ambiguous_market_ids
            or matched_baseline is None
            or baseline_key in ambiguous_baseline_keys
        ):
            continue
        pipeline_version = _pipeline_version(matched_baseline)
        if pipeline_version is None:
            continue
        cohort_key: _CohortKey = (
            legacy.expert,
            legacy.expert_version,
            matched_market.event_id,
            legacy.ts,
            pipeline_version,
        )
        if (
            not legacy.expert
            or not legacy.expert_version
            or legacy.expert_version.endswith(f"-{SEALED_EVIDENCE_PROTOCOL}")
            or legacy.distribution.get("evidence_protocol") == SEALED_EVIDENCE_PROTOCOL
            or legacy.evidence_eligible
            or not legacy.point_in_time_valid
            or legacy.ts >= matched_market.resolves_at
            or matched_baseline.id is None
            or not _finite_probability(legacy.probability)
            or not _finite_probability(matched_baseline.prob)
            or not _legacy_baseline_matches(
                legacy,
                matched_baseline,
                pipeline_version,
            )
        ):
            invalid_group_keys.add(cohort_key)
            continue
        grouped[cohort_key].append((legacy, matched_baseline, matched_market))

    sealed: list[AccuracyPredictionValue] = []
    for cohort_key in sorted(grouped):
        if cohort_key in invalid_group_keys:
            continue
        expert, legacy_version, event_id, cohort_ts, pipeline_version = cohort_key
        cohort = sorted(grouped[cohort_key], key=lambda item: item[2].id)
        cohort_training_cutoff = training_cutoff or cohort_ts
        expected_market_ids = expected_by_event.get(event_id, set())
        actual_market_ids = {market.id for _legacy, _baseline, market in cohort}
        if (
            len(expected_market_ids) < 2
            or actual_market_ids != expected_market_ids
            or len(actual_market_ids) != len(cohort)
            or any(legacy.ts != cohort_ts for legacy, _baseline, _market in cohort)
            or any(
                _pipeline_version(baseline) != pipeline_version
                for _legacy, baseline, _market in cohort
            )
            or cohort_training_cutoff < cohort_ts
            or any(
                cohort_training_cutoff >= market.resolves_at
                for _legacy, _baseline, market in cohort
            )
        ):
            continue

        # This is intentionally independent from event_coherence["passed"].
        # That stricter flag includes execution-only conditions (verified
        # resolution source, bucket eligibility, and one CLOB anchor batch).
        # Accuracy evidence instead binds to the exhaustive materialized model
        # vector and its timestamp/pipeline identity.
        if not _materialized_baseline_vector_matches(
            cohort,
            event_id=event_id,
            cohort_ts=cohort_ts,
            pipeline_version=pipeline_version,
        ):
            continue

        baseline_total = sum(
            (baseline.prob for _legacy, baseline, _market in cohort),
            start=Decimal("0"),
        )
        if abs(baseline_total - Decimal("1")) > _BASELINE_TOTAL_TOLERANCE:
            continue
        normalised_baseline_probabilities = _normalise_probabilities(
            [baseline.prob for _legacy, baseline, _market in cohort]
        )
        if normalised_baseline_probabilities is None:
            continue
        candidate_probabilities = _normalise_probabilities(
            [legacy.probability for legacy, _baseline, _market in cohort]
        )
        if candidate_probabilities is None:
            continue

        sealed_version = sealed_expert_version(legacy_version)
        fingerprint = sealed_experiment_fingerprint(
            pipeline_fingerprint=pipeline_fingerprint,
            expert=expert,
            legacy_version=legacy_version,
            sealed_version=sealed_version,
            pipeline_version=pipeline_version,
        )
        market_ids = sorted(actual_market_ids)
        input_hash = _canonical_hash(
            {
                "schema": "sealed-legacy-accuracy-input-v2",
                "expert": expert,
                "legacy_expert_version": legacy_version,
                "sealed_expert_version": sealed_version,
                "event_id": event_id,
                "cohort_ts": cohort_ts,
                "training_cutoff": cohort_training_cutoff,
                "baseline_pipeline_version": pipeline_version,
                "probability_pipeline_fingerprint": pipeline_fingerprint,
                "buckets": [
                    {
                        "market": _market_payload(market),
                        "candidate": {
                            "probability": legacy.probability,
                            "mean_c": legacy.mean_c,
                            "sigma_c": legacy.sigma_c,
                            "training_sample_n": legacy.training_sample_n,
                            "point_in_time_valid": legacy.point_in_time_valid,
                            "distribution": legacy.distribution,
                        },
                        "baseline": {
                            "model_prob_id": baseline.id,
                            "ts": baseline.ts,
                            "probability": baseline.prob,
                            "pipeline_version": pipeline_version,
                        },
                    }
                    for legacy, baseline, market in cohort
                ],
            }
        )
        event_cohort = {
            "event_id": event_id,
            "cohort_ts": cohort_ts.isoformat(),
            "training_cutoff": cohort_training_cutoff.isoformat(),
            "pipeline_version": pipeline_version,
            "market_count": len(market_ids),
            "market_ids": market_ids,
            "candidate_total": float(
                sum(
                    (legacy.probability for legacy, _baseline, _market in cohort),
                    start=Decimal("0"),
                )
            ),
            "baseline_total": float(baseline_total),
            "normalized_baseline_total": 1.0,
        }
        for probability, normalised_baseline, (legacy, baseline, market) in zip(
            candidate_probabilities,
            normalised_baseline_probabilities,
            cohort,
            strict=True,
        ):
            distribution = {
                **deepcopy(legacy.distribution),
                "evidence_protocol": SEALED_EVIDENCE_PROTOCOL,
                "baseline_probability": float(baseline.prob),
                "normalized_baseline_probability": float(normalised_baseline),
                "baseline_pipeline_version": pipeline_version,
                "baseline_model_prob_id": baseline.id,
                "event_cohort": deepcopy(event_cohort),
                "legacy_expert": expert,
                "legacy_expert_version": legacy_version,
                "legacy_probability": float(legacy.probability),
                "input_hash": input_hash,
            }
            sealed.append(
                AccuracyPredictionValue(
                    market_id=market.id,
                    ts=cohort_ts,
                    expert=expert,
                    expert_version=sealed_version,
                    probability=probability,
                    mean_c=legacy.mean_c,
                    sigma_c=legacy.sigma_c,
                    training_sample_n=legacy.training_sample_n,
                    point_in_time_valid=True,
                    distribution=distribution,
                    generated_at=None,
                    training_cutoff=cohort_training_cutoff,
                    baseline_model_prob_id=baseline.id,
                    input_hash=input_hash,
                    experiment_fingerprint=fingerprint,
                    evidence_eligible=True,
                )
            )
    return sealed
