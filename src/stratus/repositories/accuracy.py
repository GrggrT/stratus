from __future__ import annotations

import hashlib
import json
import logging
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    AccuracyPrediction,
    AccuracyStackWeight,
    ForecastEvaluation,
    GammaOutcomeEvent,
    ModelProb,
    ResolutionContract,
    SettlementActual,
    SettlementProbabilityCalibrator,
    WeatherKind,
)
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    gamma_outcome_map,
)
from stratus.services.clob_evidence import LEGACY_MARKET_PROTOCOL
from stratus.services.resolution_contracts import (
    ParsedResolutionContract,
    ResolutionContractError,
)

logger = logging.getLogger(__name__)


class ResolutionContractIntegrityError(ResolutionContractError):
    """Raised when an immutable contract identity is replayed with new content."""


_RESOLUTION_CONTRACT_EQUALITY_FIELDS = (
    "event_id",
    "rules_hash",
    "source_family",
    "source_url",
    "station_code",
    "local_timezone",
    "statistic",
    "source_unit",
    "decimal_places",
    "finalization_rule",
    "license_status",
    "storage_permitted",
    "license_url",
    "rules_raw",
)


def _resolution_contract_values(value: ParsedResolutionContract) -> dict[str, object]:
    return {
        "event_id": value.event_id,
        "rules_hash": value.rules_hash,
        "source_family": value.source_family,
        "source_url": value.source_url,
        "station_code": value.station_code,
        "local_timezone": value.local_timezone,
        "statistic": value.statistic.value,
        "source_unit": value.source_unit,
        "decimal_places": value.decimal_places,
        "finalization_rule": value.finalization_rule,
        "license_status": value.license_status,
        "storage_permitted": value.storage_permitted,
        "license_url": value.license_url,
        "rules_raw": value.rules_raw,
    }


def _resolution_contract_matches(
    row: ResolutionContract, values: dict[str, object]
) -> bool:
    return all(
        getattr(row, field) == values[field]
        for field in _RESOLUTION_CONTRACT_EQUALITY_FIELDS
    )


async def upsert_resolution_contract(
    session: AsyncSession,
    value: ParsedResolutionContract,
    *,
    expected_market_date: date,
) -> ResolutionContract:
    """Insert a contract once, or prove that a retry is byte-for-byte equivalent.

    ``expected_market_date`` is the independently parsed market identity; it
    must agree with the sole date token extracted from the resolution rules.
    In particular, discovery is not allowed to rewrite source/licence fields or
    reactivate a retired contract under the same ``event_id + rules_hash`` key.
    """

    if value.resolution_date != expected_market_date:
        raise ResolutionContractIntegrityError(
            "resolution rules date does not match the expected market date: "
            f"rules={value.resolution_date.isoformat()} "
            f"market={expected_market_date.isoformat()}"
        )

    values = _resolution_contract_values(value)
    existing = await session.scalar(
        select(ResolutionContract).where(
            ResolutionContract.event_id == value.event_id,
            ResolutionContract.rules_hash == value.rules_hash,
        )
    )
    if existing is not None:
        if existing.retired_at is not None:
            raise ResolutionContractIntegrityError(
                "retired resolution contract cannot be reactivated by retry"
            )
        if not _resolution_contract_matches(existing, values):
            raise ResolutionContractIntegrityError(
                "resolution contract retry differs from immutable stored content"
            )
        return existing

    inserted_id = (
        await session.execute(
            insert(ResolutionContract)
            .values(**values)
            .on_conflict_do_nothing(constraint="uq_resolution_contract_event_rules")
            .returning(ResolutionContract.id)
        )
    ).scalar_one_or_none()
    stored = (
        await session.get(ResolutionContract, inserted_id)
        if inserted_id is not None
        else await session.scalar(
            select(ResolutionContract).where(
                ResolutionContract.event_id == value.event_id,
                ResolutionContract.rules_hash == value.rules_hash,
            )
        )
    )
    if (
        stored is None
        or stored.retired_at is not None
        or not _resolution_contract_matches(stored, values)
    ):
        raise ResolutionContractIntegrityError(
            "resolution contract retry differs from immutable stored content"
        )
    return stored


@dataclass(frozen=True, slots=True)
class SettlementActualValue:
    contract_id: int
    station_id: int
    date: date
    kind: WeatherKind
    source_value: Decimal
    source_unit: str
    value_c: Decimal
    is_final: bool
    source_published_at: datetime | None
    retrieved_at: datetime
    source_response_hash: str | None
    provenance: dict[str, Any]


async def upsert_settlement_actual(session: AsyncSession, value: SettlementActualValue) -> int:
    statement = (
        insert(SettlementActual)
        .values(**asdict(value))
        .on_conflict_do_update(
            constraint="uq_settlement_actual_contract",
            set_={
                "source_value": value.source_value,
                "source_unit": value.source_unit,
                "value_c": value.value_c,
                "is_final": value.is_final,
                "source_published_at": value.source_published_at,
                "retrieved_at": value.retrieved_at,
                "source_response_hash": value.source_response_hash,
                "provenance": value.provenance,
            },
        )
        .returning(SettlementActual.id)
    )
    return (await session.execute(statement)).scalar_one()


@dataclass(frozen=True, slots=True)
class AccuracyPredictionValue:
    market_id: int
    ts: datetime
    expert: str
    expert_version: str
    probability: Decimal
    mean_c: Decimal | None
    sigma_c: Decimal | None
    training_sample_n: int
    point_in_time_valid: bool
    distribution: dict[str, Any]
    generated_at: datetime | None = None
    training_cutoff: datetime | None = None
    baseline_model_prob_id: int | None = None
    input_hash: str | None = None
    experiment_fingerprint: str | None = None
    evidence_eligible: bool = False


ACCURACY_PREDICTION_BATCH_ROWS = 500


def _prediction_batches(
    values: list[AccuracyPredictionValue],
) -> list[list[AccuracyPredictionValue]]:
    return [
        values[offset : offset + ACCURACY_PREDICTION_BATCH_ROWS]
        for offset in range(0, len(values), ACCURACY_PREDICTION_BATCH_ROWS)
    ]


class AccuracyPredictionIntegrityError(RuntimeError):
    """Raised when a sealed evidence key is replayed with different content."""


def _hash_json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"cannot hash accuracy prediction value {type(value)!r}")


def accuracy_prediction_record_hash(value: AccuracyPredictionValue) -> str:
    """Hash immutable evidence content; generated_at is first-write metadata.

    Excluding generated_at makes a byte-identical retry idempotent while the
    database trigger still prevents changing the stored first-generation time.
    """
    payload = {
        "market_id": value.market_id,
        "ts": value.ts,
        "expert": value.expert,
        "expert_version": value.expert_version,
        "probability": value.probability,
        "mean_c": value.mean_c,
        "sigma_c": value.sigma_c,
        "training_sample_n": value.training_sample_n,
        "point_in_time_valid": value.point_in_time_valid,
        "training_cutoff": value.training_cutoff,
        "baseline_model_prob_id": value.baseline_model_prob_id,
        "input_hash": value.input_hash,
        "experiment_fingerprint": value.experiment_fingerprint,
        "evidence_eligible": value.evidence_eligible,
        "distribution": value.distribution,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=_hash_json_default,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def accuracy_prediction_record_is_valid(prediction: AccuracyPrediction) -> bool:
    if prediction.record_hash is None:
        return False
    value = AccuracyPredictionValue(
        market_id=prediction.market_id,
        ts=prediction.ts,
        expert=prediction.expert,
        expert_version=prediction.expert_version,
        probability=prediction.probability,
        mean_c=prediction.mean_c,
        sigma_c=prediction.sigma_c,
        training_sample_n=prediction.training_sample_n,
        point_in_time_valid=prediction.point_in_time_valid,
        distribution=prediction.distribution,
        generated_at=prediction.generated_at,
        training_cutoff=prediction.training_cutoff,
        baseline_model_prob_id=prediction.baseline_model_prob_id,
        input_hash=prediction.input_hash,
        experiment_fingerprint=prediction.experiment_fingerprint,
        evidence_eligible=prediction.evidence_eligible,
    )
    return accuracy_prediction_record_hash(value) == prediction.record_hash


def _is_sha256(value: str | None) -> bool:
    if value is None or len(value) != 64 or value != value.lower():
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _prediction_row(value: AccuracyPredictionValue) -> dict[str, Any]:
    generated_at = value.generated_at or datetime.now(UTC)
    experiment_fingerprint = value.experiment_fingerprint
    if not value.evidence_eligible:
        generated_at = value.generated_at or value.ts
        experiment_fingerprint = experiment_fingerprint or value.expert_version
    normalized = replace(
        value,
        probability=value.probability.quantize(Decimal("0.000001")),
        mean_c=(
            value.mean_c.quantize(Decimal("0.0001"))
            if value.mean_c is not None
            else None
        ),
        sigma_c=(
            value.sigma_c.quantize(Decimal("0.0001"))
            if value.sigma_c is not None
            else None
        ),
        generated_at=generated_at,
        experiment_fingerprint=experiment_fingerprint,
    )
    if normalized.evidence_eligible:
        baseline_probability = normalized.distribution.get("baseline_probability")
        normalized_baseline_probability = normalized.distribution.get(
            "normalized_baseline_probability"
        )
        baseline_pipeline_version = normalized.distribution.get(
            "baseline_pipeline_version"
        )
        if (
            not normalized.point_in_time_valid
            or normalized.training_cutoff is None
            or normalized.baseline_model_prob_id is None
            or not _is_sha256(normalized.input_hash)
            or not _is_sha256(normalized.experiment_fingerprint)
            or normalized.distribution.get("evidence_protocol") != "sealed-v2"
            or not isinstance(baseline_pipeline_version, str)
            or not baseline_pipeline_version
            or not isinstance(baseline_probability, int | float)
            or isinstance(baseline_probability, bool)
            or not math.isfinite(float(baseline_probability))
            or not 0 <= float(baseline_probability) <= 1
            or not isinstance(normalized_baseline_probability, int | float)
            or isinstance(normalized_baseline_probability, bool)
            or not math.isfinite(float(normalized_baseline_probability))
            or not 0 <= float(normalized_baseline_probability) <= 1
        ):
            raise ValueError("sealed accuracy evidence is missing required provenance")
        if normalized.ts > generated_at or normalized.training_cutoff > generated_at:
            raise ValueError("sealed accuracy evidence has future-dated inputs")
    row = asdict(normalized)
    row["record_hash"] = accuracy_prediction_record_hash(normalized)
    return row


async def upsert_accuracy_predictions(
    session: AsyncSession, values: list[AccuracyPredictionValue]
) -> int:
    if not values:
        return 0
    for batch in _prediction_batches(values):
        rows = [_prediction_row(value) for value in batch]
        statement = insert(AccuracyPrediction).values(rows).on_conflict_do_nothing(
            constraint="uq_accuracy_prediction"
        )
        await session.execute(statement)
        keys = [
            (row["market_id"], row["ts"], row["expert"], row["expert_version"])
            for row in rows
        ]
        stored_rows = (
            await session.execute(
                select(
                    AccuracyPrediction.market_id,
                    AccuracyPrediction.ts,
                    AccuracyPrediction.expert,
                    AccuracyPrediction.expert_version,
                    AccuracyPrediction.record_hash,
                    AccuracyPrediction.evidence_eligible,
                ).where(
                    tuple_(
                        AccuracyPrediction.market_id,
                        AccuracyPrediction.ts,
                        AccuracyPrediction.expert,
                        AccuracyPrediction.expert_version,
                    ).in_(keys)
                )
            )
        ).all()
        stored = {
            (market_id, ts, expert, expert_version): (record_hash, evidence_eligible)
            for market_id, ts, expert, expert_version, record_hash, evidence_eligible in stored_rows
        }
        for row in rows:
            key = (row["market_id"], row["ts"], row["expert"], row["expert_version"])
            existing = stored.get(key)
            if existing is None:
                raise AccuracyPredictionIntegrityError(
                    f"accuracy prediction insert disappeared for {key}"
                )
            stored_hash, stored_eligible = existing
            if stored_hash == row["record_hash"]:
                continue
            if bool(stored_eligible) or bool(row["evidence_eligible"]):
                raise AccuracyPredictionIntegrityError(
                    f"sealed accuracy prediction conflict for {key}"
                )
            logger.warning("legacy accuracy prediction conflict ignored for %s", key)
    return len(values)


@dataclass(frozen=True, slots=True)
class ForecastEvaluationValue:
    market_id: int
    model_prob_id: int
    decision_at: datetime
    independent_sample_key: str
    model_probability: Decimal
    horizon_market_probability: Decimal | None
    market_snapshot_at: datetime | None
    executable_bid: Decimal | None
    executable_ask: Decimal | None
    pre_resolution_close: Decimal | None
    close_snapshot_at: datetime | None
    event_probability_total: Decimal | None
    event_overround: Decimal | None
    resolved_outcome: bool
    resolution_event_id: int
    resolution_vector_hash: str
    model_brier: Decimal
    market_brier: Decimal | None
    evaluated_at: datetime
    details: dict[str, Any]
    market_benchmark_protocol: str = LEGACY_MARKET_PROTOCOL
    market_snapshot_id: int | None = None
    market_snapshot_hash: str | None = None
    close_snapshot_id: int | None = None
    close_snapshot_hash: str | None = None


class ForecastEvaluationIntegrityError(RuntimeError):
    """Raised when an evaluated probability is replayed with different truth."""


def forecast_evaluation_evidence_is_valid(
    evaluation: ForecastEvaluation,
    outcome_event: GammaOutcomeEvent,
    model_prob: ModelProb,
) -> bool:
    """Validate both immutable outcome provenance and predictive identity."""

    try:
        outcomes = gamma_outcome_map(outcome_event)
    except GammaOutcomeIntegrityError:
        return False
    return bool(
        evaluation.resolution_event_id == outcome_event.id
        and evaluation.resolution_vector_hash == outcome_event.outcome_vector_hash
        and evaluation.decision_at < outcome_event.first_outcome_at
        and evaluation.market_id == model_prob.market_id
        and evaluation.model_prob_id == model_prob.id
        and evaluation.decision_at == model_prob.ts
        and evaluation.model_probability == model_prob.prob
        and outcomes.get(evaluation.market_id) is evaluation.resolved_outcome
    )


def forecast_evaluation_cohort_is_complete(
    rows: Sequence[tuple[ForecastEvaluation, GammaOutcomeEvent]],
) -> bool:
    """Require one exact, duplicate-free Gamma bucket vector for training.

    Individual evaluation attestation is necessary but not sufficient: a SQL
    result can still contain only a subset of an otherwise valid event. Formal
    event-level learners must see every market sealed into the immutable Gamma
    vector, exactly once.
    """

    if not rows:
        return False
    first_evaluation, outcome_event = rows[0]
    resolution_event_id = first_evaluation.resolution_event_id
    if resolution_event_id is None or outcome_event.id != resolution_event_id:
        return False
    try:
        expected_outcomes = gamma_outcome_map(outcome_event)
    except GammaOutcomeIntegrityError:
        return False
    if len(expected_outcomes) != outcome_event.market_count:
        return False

    market_ids: list[int] = []
    for evaluation, candidate_event in rows:
        if (
            evaluation.resolution_event_id != resolution_event_id
            or candidate_event.id != resolution_event_id
            or candidate_event.event_id != outcome_event.event_id
            or candidate_event.market_count != outcome_event.market_count
            or candidate_event.outcome_vector_hash
            != outcome_event.outcome_vector_hash
            or candidate_event.record_hash != outcome_event.record_hash
            or evaluation.resolution_vector_hash
            != outcome_event.outcome_vector_hash
            or evaluation.decision_at >= outcome_event.first_outcome_at
            or expected_outcomes.get(evaluation.market_id)
            is not evaluation.resolved_outcome
        ):
            return False
        market_ids.append(evaluation.market_id)

    return bool(
        len(market_ids) == outcome_event.market_count
        and len(set(market_ids)) == len(market_ids)
        and set(market_ids) == set(expected_outcomes)
    )


async def upsert_forecast_evaluations(
    session: AsyncSession, values: list[ForecastEvaluationValue]
) -> int:
    if not values:
        return 0
    rows = [asdict(value) for value in values]
    for row in rows:
        vector_hash = row["resolution_vector_hash"]
        if not isinstance(vector_hash, str) or not _is_sha256(vector_hash):
            raise ValueError("forecast evaluation requires a valid resolution vector hash")
    statement = insert(ForecastEvaluation).values(rows).on_conflict_do_nothing(
        constraint="uq_forecast_evaluation_model_prob"
    )
    await session.execute(statement)

    model_prob_ids = [value.model_prob_id for value in values]
    existing_rows = (
        await session.execute(
            select(
                ForecastEvaluation.model_prob_id,
                ForecastEvaluation.market_id,
                ForecastEvaluation.decision_at,
                ForecastEvaluation.independent_sample_key,
                ForecastEvaluation.model_probability,
                ForecastEvaluation.resolved_outcome,
                ForecastEvaluation.resolution_event_id,
                ForecastEvaluation.resolution_vector_hash,
                ForecastEvaluation.model_brier,
                ForecastEvaluation.market_benchmark_protocol,
                ForecastEvaluation.market_snapshot_id,
                ForecastEvaluation.market_snapshot_hash,
                ForecastEvaluation.close_snapshot_id,
                ForecastEvaluation.close_snapshot_hash,
            ).where(ForecastEvaluation.model_prob_id.in_(model_prob_ids))
        )
    ).all()
    existing = {row.model_prob_id: row for row in existing_rows}
    incoming_by_model_prob_id = {
        int(row["model_prob_id"]): row
        for row in rows
    }
    for value in values:
        stored_value = existing.get(value.model_prob_id)
        expected_probability = value.model_probability.quantize(Decimal("0.000001"))
        if stored_value is None:
            raise ForecastEvaluationIntegrityError(
                f"forecast evaluation insert disappeared for model_prob_id={value.model_prob_id}"
            )
        legacy_unattested = (
            stored_value.resolution_event_id is None
            and stored_value.resolution_vector_hash is None
        )
        if legacy_unattested:
            if (
                stored_value.market_id != value.market_id
                or stored_value.decision_at != value.decision_at
                or stored_value.independent_sample_key != value.independent_sample_key
                or stored_value.model_probability != expected_probability
            ):
                raise ForecastEvaluationIntegrityError(
                    "legacy forecast evaluation identity conflict for "
                    f"model_prob_id={value.model_prob_id}"
                )
            replacement = dict(incoming_by_model_prob_id[value.model_prob_id])
            replacement.pop("model_prob_id")
            await session.execute(
                update(ForecastEvaluation)
                .where(
                    ForecastEvaluation.model_prob_id == value.model_prob_id,
                    ForecastEvaluation.resolution_event_id.is_(None),
                    ForecastEvaluation.resolution_vector_hash.is_(None),
                )
                .values(**replacement)
            )

    # Re-read after any one-way legacy upgrades. The database trigger validates
    # the ledger FK, vector hash, outcome and pre-outcome decision time.
    attested_rows = (
        await session.execute(
            select(
                ForecastEvaluation.model_prob_id,
                ForecastEvaluation.market_id,
                ForecastEvaluation.decision_at,
                ForecastEvaluation.independent_sample_key,
                ForecastEvaluation.model_probability,
                ForecastEvaluation.resolved_outcome,
                ForecastEvaluation.resolution_event_id,
                ForecastEvaluation.resolution_vector_hash,
                ForecastEvaluation.model_brier,
                ForecastEvaluation.market_benchmark_protocol,
                ForecastEvaluation.market_snapshot_id,
                ForecastEvaluation.market_snapshot_hash,
                ForecastEvaluation.close_snapshot_id,
                ForecastEvaluation.close_snapshot_hash,
            ).where(ForecastEvaluation.model_prob_id.in_(model_prob_ids))
        )
    ).all()
    attested = {row.model_prob_id: row for row in attested_rows}
    for value in values:
        stored_value = attested.get(value.model_prob_id)
        expected_probability = value.model_probability.quantize(Decimal("0.000001"))
        expected_brier = value.model_brier.quantize(Decimal("0.00000001"))
        if stored_value is None or (
            stored_value.market_id != value.market_id
            or stored_value.decision_at != value.decision_at
            or stored_value.independent_sample_key != value.independent_sample_key
            or stored_value.model_probability != expected_probability
            or stored_value.resolved_outcome != value.resolved_outcome
            or stored_value.resolution_event_id != value.resolution_event_id
            or stored_value.resolution_vector_hash != value.resolution_vector_hash
            or stored_value.model_brier != expected_brier
            or stored_value.market_benchmark_protocol
            != value.market_benchmark_protocol
            or stored_value.market_snapshot_id != value.market_snapshot_id
            or stored_value.market_snapshot_hash != value.market_snapshot_hash
            or stored_value.close_snapshot_id != value.close_snapshot_id
            or stored_value.close_snapshot_hash != value.close_snapshot_hash
        ):
            raise ForecastEvaluationIntegrityError(
                f"forecast evaluation truth conflict for model_prob_id={value.model_prob_id}"
            )
    return len(values)


async def upsert_daily_stack_weights(
    session: AsyncSession,
    *,
    station_id: int,
    horizon_bucket: str,
    kind: str,
    weights: dict[str, float],
    sample_n: int,
    effective_sample_n: Decimal,
    valid_from: datetime,
) -> int:
    if not weights:
        return 0
    await session.execute(
        update(AccuracyStackWeight)
        .where(
            AccuracyStackWeight.station_id == station_id,
            AccuracyStackWeight.horizon_bucket == horizon_bucket,
            AccuracyStackWeight.kind == kind,
            AccuracyStackWeight.valid_to.is_(None),
            AccuracyStackWeight.valid_from < valid_from,
        )
        .values(valid_to=valid_from)
    )
    rows = [
        {
            "station_id": station_id,
            "horizon_bucket": horizon_bucket,
            "kind": kind,
            "expert": expert,
            "weight": Decimal(str(weight)),
            "sample_n": sample_n,
            "effective_sample_n": effective_sample_n,
            "valid_from": valid_from,
        }
        for expert, weight in weights.items()
    ]
    statement = insert(AccuracyStackWeight).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_accuracy_stack_weight_version",
        set_={
            "weight": statement.excluded.weight,
            "sample_n": statement.excluded.sample_n,
            "effective_sample_n": statement.excluded.effective_sample_n,
            "out_of_fold_brier": statement.excluded.out_of_fold_brier,
            "valid_to": None,
        },
    )
    await session.execute(statement)
    return len(rows)


@dataclass(frozen=True, slots=True)
class SettlementCalibratorValue:
    scope_key: str
    station_id: int | None
    horizon_bucket: str | None
    kind: str | None
    alpha: Decimal
    beta: Decimal
    training_blocks: int
    training_buckets: int
    oof_baseline_brier: Decimal | None
    oof_calibrated_brier: Decimal | None
    oof_baseline_ece: Decimal | None
    oof_calibrated_ece: Decimal | None
    delta_brier_ci_low: Decimal | None
    delta_brier_ci_high: Decimal | None
    eligible: bool
    trained_through: datetime
    details: dict[str, Any]


async def upsert_settlement_calibrator(
    session: AsyncSession, value: SettlementCalibratorValue
) -> int:
    insert_statement = insert(SettlementProbabilityCalibrator).values(**asdict(value))
    statement = insert_statement.on_conflict_do_update(
        index_elements=[SettlementProbabilityCalibrator.scope_key],
        set_={
            "alpha": insert_statement.excluded.alpha,
            "beta": insert_statement.excluded.beta,
            "training_blocks": insert_statement.excluded.training_blocks,
            "training_buckets": insert_statement.excluded.training_buckets,
            "oof_baseline_brier": insert_statement.excluded.oof_baseline_brier,
            "oof_calibrated_brier": insert_statement.excluded.oof_calibrated_brier,
            "oof_baseline_ece": insert_statement.excluded.oof_baseline_ece,
            "oof_calibrated_ece": insert_statement.excluded.oof_calibrated_ece,
            "delta_brier_ci_low": insert_statement.excluded.delta_brier_ci_low,
            "delta_brier_ci_high": insert_statement.excluded.delta_brier_ci_high,
            "eligible": insert_statement.excluded.eligible,
            "trained_through": insert_statement.excluded.trained_through,
            "details": insert_statement.excluded.details,
            "updated_at": datetime.now(UTC),
        },
    ).returning(SettlementProbabilityCalibrator.id)
    result: int = (await session.execute(statement)).scalar_one()
    return result
