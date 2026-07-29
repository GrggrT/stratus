from __future__ import annotations

import asyncio
import hashlib
import logging
import math
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from statistics import fmean
from typing import Any, cast

from sqlalchemy import (
    DateTime,
    and_,
    case,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy import (
    cast as sql_cast,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import ColumnElement

from stratus.config import Settings, get_settings
from stratus.db.models import (
    AccuracyPrediction,
    DailyActual,
    Forecast,
    ForecastEvaluation,
    ForecastModelRegistry,
    ForecastRun,
    GammaOutcomeEvent,
    HistoricalForecast,
    Market,
    ModelDriftEvent,
    ModelProb,
    ModelSkillReport,
    Station,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    accuracy_prediction_record_hash,
)
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible
from stratus.repositories.forecast_runs import upsert_model_registry
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    gamma_outcome_map,
)
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)
from stratus.services.accuracy_models import block_bootstrap_mean_ci
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.model_catalog import (
    REGIONAL_SHADOW_MODELS,
    canonical_model,
    descriptor_for,
)
from stratus.services.regime_probability import DatedResidual, detect_regime_shift

logger = logging.getLogger(__name__)
WINDOWS = (30, 60, 90, 180)
HORIZON_ANCHORS = {"0-6h": 3.0, "6-24h": 12.0, "24-48h": 36.0, "48h+": 72.0}
UPSERT_BATCH_ROWS = 500
_RAW_BASELINE_TOTAL_TOLERANCE = Decimal("0.01")
_NORMALIZED_BASELINE_ATTESTATION_TOLERANCE = 0.00001
PROSPECTIVE_EXPERTS = (
    "regularized_stack",
    "trajectory_ar1",
    "trajectory_nowcast",
    "conformal_gaussian",
    "censored_gaussian",
    "historical_weighted",
    "historical_weighted_shrunk",
    "hierarchical_emos",
    "regime_emos",
    "qrf",
    "analog",
    "mixture",
)

BlockSignature = tuple[tuple[float, ...], ...]
BlockBootstrapCache = dict[BlockSignature, tuple[float, float] | None]
TruthDateIndex = dict[tuple[int, WeatherKind], tuple[date, ...]]


@dataclass(frozen=True, slots=True)
class SelectedForecastRow:
    registry_id: int
    station_id: int
    valid_date: date
    kind: WeatherKind
    horizon_bucket: str
    temp_c: Decimal
    init_time: datetime
    available_at: datetime
    registry_enabled: bool
    registry_shadow_only: bool
    expected_latency_minutes: int | None
    availability_verified: bool


@dataclass(frozen=True, slots=True)
class TrajectoryPredictionRow:
    market_id: int
    ts: datetime
    expert: str
    expert_version: str
    probability: Decimal
    mean_c: Decimal | None
    sigma_c: Decimal | None
    training_sample_n: int
    point_in_time_valid: bool
    generated_at: datetime
    training_cutoff: datetime | None
    baseline_model_prob_id: int | None
    input_hash: str | None
    experiment_fingerprint: str | None
    record_hash: str | None
    evidence_eligible: bool
    distribution: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TrajectoryMarketRow:
    id: int
    event_id: str
    station_id: int | None
    kind: WeatherKind
    market_date: date
    resolves_at: datetime


@dataclass(frozen=True, slots=True)
class TrajectoryModelProbRow:
    id: int
    market_id: int
    ts: datetime
    prob: Decimal
    horizon_h: Decimal
    components: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TrajectoryScoreRow:
    market_id: int
    candidate_probability: Decimal
    baseline_probability: float


@dataclass(frozen=True, slots=True)
class SelectedTrajectoryVector:
    expert: str
    expert_version: str
    experiment_fingerprint: str
    event_id: str
    station_id: int
    horizon_bucket: str
    kind: WeatherKind
    baseline_pipeline_version: str
    market_date: date
    resolves_at: datetime
    scores: tuple[TrajectoryScoreRow, ...]


TrajectorySnapshotRow = tuple[
    TrajectoryPredictionRow,
    TrajectoryMarketRow,
    TrajectoryModelProbRow,
]
TrajectorySnapshot = list[TrajectorySnapshotRow]
TrajectorySnapshotKey = tuple[str, str, str, str, str, datetime]
TrajectorySelectionKey = tuple[str, str, str, str, str]


def _row_batches(
    rows: list[dict[str, object]], batch_size: int = UPSERT_BATCH_ROWS
) -> list[list[dict[str, object]]]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [rows[offset : offset + batch_size] for offset in range(0, len(rows), batch_size)]


def _cached_block_bootstrap_mean_ci(
    values_by_block: dict[str, list[float]],
    cache: BlockBootstrapCache,
) -> tuple[float, float] | None:
    """Reuse an exact deterministic bootstrap result for an identical input.

    ``block_bootstrap_mean_ci`` starts a fresh RNG with the same seed on every
    call and observes only the ordered non-empty value lists (not the block
    names).  The tuple copy therefore preserves its output bit-for-bit while
    avoiding four identical resamples when all evidence is newer than the
    shortest 30-day reporting window.
    """

    signature = tuple(tuple(values) for values in values_by_block.values() if values)
    if signature not in cache:
        cache[signature] = block_bootstrap_mean_ci(values_by_block)
    return cache[signature]


def _truth_date_index(
    truth: dict[tuple[int, date, WeatherKind], float],
) -> TruthDateIndex:
    dates_by_scope: dict[tuple[int, WeatherKind], list[date]] = defaultdict(list)
    for station_id, actual_date, kind in truth:
        dates_by_scope[(station_id, kind)].append(actual_date)
    return {
        scope: tuple(sorted(dates)) for scope, dates in dates_by_scope.items()
    }


def _truth_day_count(
    index: TruthDateIndex,
    *,
    station_id: int,
    kind: WeatherKind,
    starting_on: date,
) -> int:
    dates = index.get((station_id, kind), ())
    return len(dates) - bisect_left(dates, starting_on)


def _daily_actual_truth_statement(
    *,
    as_of: datetime,
) -> Select[tuple[int, date, WeatherKind, Decimal]]:
    """Select current METAR fallback truth except reconciled conflicts."""

    return select(
        DailyActual.station_id,
        DailyActual.date,
        DailyActual.kind,
        DailyActual.temp_c,
    ).where(
        or_(
            DailyActual.first_seen_at.is_(None),
            DailyActual.first_seen_at <= as_of,
        ),
        daily_actual_fallback_eligible(
            station_id=DailyActual.station_id,
            actual_date=DailyActual.date,
            kind=DailyActual.kind,
        ),
        ~eligible_settlement_actual_exists(
            station_id=DailyActual.station_id,
            actual_date=DailyActual.date,
            kind=DailyActual.kind,
            as_of=as_of,
        ),
    )


def _daily_actual_truth_exists(*, as_of: datetime) -> ColumnElement[bool]:
    """Correlated availability check for undisputed METAR fallback truth."""

    return exists(
        select(literal(1)).where(
            DailyActual.station_id == Forecast.station_id,
            DailyActual.date == Forecast.valid_date,
            DailyActual.kind == Forecast.kind,
            or_(
                DailyActual.first_seen_at.is_(None),
                DailyActual.first_seen_at <= as_of,
            ),
            daily_actual_fallback_eligible(
                station_id=DailyActual.station_id,
                actual_date=DailyActual.date,
                kind=DailyActual.kind,
            ),
            ~eligible_settlement_actual_exists(
                station_id=DailyActual.station_id,
                actual_date=DailyActual.date,
                kind=DailyActual.kind,
                as_of=as_of,
            ),
        )
    )


def _canonical_exact_truth_exists(*, as_of: datetime) -> ColumnElement[bool]:
    exact_actuals = canonical_settlement_actuals(as_of=as_of)
    return exists(
        select(literal(1))
        .select_from(exact_actuals)
        .where(
            exact_actuals.c.station_id == Forecast.station_id,
            exact_actuals.c.date == Forecast.valid_date,
            exact_actuals.c.kind == Forecast.kind,
        )
    )


async def _selected_forecast_rows(
    session: AsyncSession,
    *,
    as_of: datetime,
    cutoff: date,
) -> list[SelectedForecastRow]:
    """Select one nearest run per deterministic accuracy-reporting cell.

    PostgreSQL performs the high-cardinality scan and ranking, while Python
    receives only the narrow winning rows.  Truth existence and availability
    are evaluated at ``as_of`` so a late observation or forecast cannot leak
    into an earlier audit.
    """

    local_day_end = func.timezone(
        Station.tz,
        sql_cast(Forecast.valid_date + literal(1), DateTime(timezone=False)),
    )
    horizon_hours = func.extract(
        "epoch", local_day_end - ForecastRun.available_at
    ) / literal(3600.0)
    horizon_bucket = case(
        (horizon_hours <= 6, literal("0-6h")),
        (horizon_hours <= 24, literal("6-24h")),
        (horizon_hours <= 48, literal("24-48h")),
        else_=literal("48h+"),
    )
    horizon_anchor = case(
        (horizon_hours <= 6, literal(3.0)),
        (horizon_hours <= 24, literal(12.0)),
        (horizon_hours <= 48, literal(36.0)),
        else_=literal(72.0),
    )
    daily_truth_exists = _daily_actual_truth_exists(as_of=as_of)
    exact_truth_exists = _canonical_exact_truth_exists(as_of=as_of)
    ranked = (
        select(
            Forecast.id.label("forecast_id"),
            horizon_bucket.label("horizon_bucket"),
            func.row_number()
            .over(
                partition_by=(
                    ForecastRun.registry_id,
                    Forecast.station_id,
                    Forecast.valid_date,
                    Forecast.kind,
                    horizon_bucket,
                ),
                order_by=(
                    func.abs(horizon_hours - horizon_anchor),
                    Forecast.id,
                ),
            )
            .label("selection_rank"),
        )
        .join(ForecastRun, Forecast.run_id == ForecastRun.id)
        .join(
            ForecastModelRegistry,
            ForecastRun.registry_id == ForecastModelRegistry.id,
        )
        .join(Station, Forecast.station_id == Station.id)
        .where(
            Forecast.valid_date >= cutoff,
            ForecastRun.available_at <= as_of,
            horizon_hours >= 0,
            or_(daily_truth_exists, exact_truth_exists),
        )
        .subquery()
    )
    winners = (
        select(ranked.c.forecast_id, ranked.c.horizon_bucket)
        .where(ranked.c.selection_rank == 1)
        .subquery()
    )
    rows = (
        (
            await session.execute(
                select(
                    ForecastRun.registry_id.label("registry_id"),
                    Forecast.station_id.label("station_id"),
                    Forecast.valid_date.label("valid_date"),
                    Forecast.kind.label("kind"),
                    winners.c.horizon_bucket,
                    Forecast.temp_c.label("temp_c"),
                    ForecastRun.init_time.label("init_time"),
                    ForecastRun.available_at.label("available_at"),
                    ForecastRun.metadata_.label("run_metadata"),
                    ForecastModelRegistry.enabled.label("registry_enabled"),
                    ForecastModelRegistry.shadow_only.label("registry_shadow_only"),
                    ForecastModelRegistry.expected_latency_minutes.label(
                        "expected_latency_minutes"
                    ),
                )
                .select_from(Forecast)
                .join(winners, Forecast.id == winners.c.forecast_id)
                .join(ForecastRun, Forecast.run_id == ForecastRun.id)
                .join(
                    ForecastModelRegistry,
                    ForecastRun.registry_id == ForecastModelRegistry.id,
                )
            )
        )
        .mappings()
        .all()
    )
    selected: list[SelectedForecastRow] = []
    for row in rows:
        metadata = row["run_metadata"]
        availability_verified = bool(
            isinstance(metadata, dict)
            and metadata.get("upstream_availability_verified") is True
            and metadata.get("init_time_semantics") == "upstream_run"
        )
        selected.append(
            SelectedForecastRow(
                registry_id=int(row["registry_id"]),
                station_id=int(row["station_id"]),
                valid_date=row["valid_date"],
                kind=row["kind"],
                horizon_bucket=str(row["horizon_bucket"]),
                temp_c=row["temp_c"],
                init_time=row["init_time"],
                available_at=row["available_at"],
                registry_enabled=bool(row["registry_enabled"]),
                registry_shadow_only=bool(row["registry_shadow_only"]),
                expected_latency_minutes=row["expected_latency_minutes"],
                availability_verified=availability_verified,
            )
        )
    return selected


def _horizon_bucket(hours: float) -> str:
    if hours <= 6:
        return "0-6h"
    if hours <= 24:
        return "6-24h"
    if hours <= 48:
        return "24-48h"
    return "48h+"


def _percentile_95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round(0.95 * (len(ordered) - 1))]


def _model_prob_pipeline_version(
    model_prob: ModelProb | TrajectoryModelProbRow,
) -> str | None:
    pipeline = model_prob.components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    cohort = pipeline.get("cohort")
    if isinstance(cohort, str) and cohort:
        return cohort
    version = pipeline.get("version")
    return version if isinstance(version, str) and version else None


def _normalized_baseline_probabilities(
    snapshot_rows: TrajectorySnapshot,
) -> dict[int, float] | None:
    """Derive the scoring vector from immutable joined ``ModelProb`` rows.

    JSON values on the challenger are attestations only. Formal scoring never
    trusts them as the source of truth: raw probabilities and the effective
    pipeline cohort come from the FK-linked baseline rows, and the stored
    normalized copy must agree with the independently reconstructed vector.
    """

    if not snapshot_rows:
        return None
    raw_probabilities = [model_prob.prob for _, _, model_prob in snapshot_rows]
    if any(
        not probability.is_finite()
        or probability < Decimal("0")
        or probability > Decimal("1")
        for probability in raw_probabilities
    ):
        return None
    total = sum(raw_probabilities, start=Decimal("0"))
    if total <= 0 or abs(total - Decimal("1")) > _RAW_BASELINE_TOTAL_TOLERANCE:
        return None

    normalized: dict[int, float] = {}
    for prediction, market, model_prob in snapshot_rows:
        distribution = prediction.distribution
        stored_raw = distribution.get("baseline_probability")
        stored_normalized = distribution.get("normalized_baseline_probability")
        expected_pipeline = _model_prob_pipeline_version(model_prob)
        if (
            not isinstance(stored_raw, int | float)
            or isinstance(stored_raw, bool)
            or not math.isfinite(float(stored_raw))
            or abs(Decimal(str(stored_raw)) - model_prob.prob) > Decimal("0.000001")
            or expected_pipeline is None
            or distribution.get("baseline_pipeline_version") != expected_pipeline
            or not isinstance(stored_normalized, int | float)
            or isinstance(stored_normalized, bool)
            or not math.isfinite(float(stored_normalized))
        ):
            return None
        expected_normalized = float(model_prob.prob / total)
        if (
            not 0.0 <= float(stored_normalized) <= 1.0
            or abs(float(stored_normalized) - expected_normalized)
            > _NORMALIZED_BASELINE_ATTESTATION_TOLERANCE
        ):
            return None
        normalized[market.id] = expected_normalized
    return normalized


def _trajectory_prediction_record_is_valid(
    prediction: TrajectoryPredictionRow,
) -> bool:
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


def _trajectory_projection_from_mapping(
    row: RowMapping,
) -> TrajectorySnapshotRow:
    prediction = TrajectoryPredictionRow(
        market_id=int(cast(int, row["prediction_market_id"])),
        ts=cast(datetime, row["prediction_ts"]),
        expert=cast(str, row["prediction_expert"]),
        expert_version=cast(str, row["prediction_expert_version"]),
        probability=cast(Decimal, row["prediction_probability"]),
        mean_c=cast(Decimal | None, row["prediction_mean_c"]),
        sigma_c=cast(Decimal | None, row["prediction_sigma_c"]),
        training_sample_n=int(cast(int, row["prediction_training_sample_n"])),
        point_in_time_valid=bool(row["prediction_point_in_time_valid"]),
        generated_at=cast(datetime, row["prediction_generated_at"]),
        training_cutoff=cast(datetime | None, row["prediction_training_cutoff"]),
        baseline_model_prob_id=cast(
            int | None, row["prediction_baseline_model_prob_id"]
        ),
        input_hash=cast(str | None, row["prediction_input_hash"]),
        experiment_fingerprint=cast(
            str | None, row["prediction_experiment_fingerprint"]
        ),
        record_hash=cast(str | None, row["prediction_record_hash"]),
        evidence_eligible=bool(row["prediction_evidence_eligible"]),
        distribution=cast(dict[str, Any], row["prediction_distribution"]),
    )
    market = TrajectoryMarketRow(
        id=int(cast(int, row["market_id"])),
        event_id=cast(str, row["market_event_id"]),
        station_id=cast(int | None, row["market_station_id"]),
        kind=cast(WeatherKind, row["market_kind"]),
        market_date=cast(date, row["market_date"]),
        resolves_at=cast(datetime, row["market_resolves_at"]),
    )
    model_prob = TrajectoryModelProbRow(
        id=int(cast(int, row["model_prob_id"])),
        market_id=int(cast(int, row["model_prob_market_id"])),
        ts=cast(datetime, row["model_prob_ts"]),
        prob=cast(Decimal, row["model_prob_prob"]),
        horizon_h=cast(Decimal, row["model_prob_horizon_h"]),
        components={"pipeline": row["model_prob_pipeline"]},
    )
    return prediction, market, model_prob


def _consider_trajectory_snapshot(
    *,
    snapshot_key: TrajectorySnapshotKey,
    snapshot_rows: TrajectorySnapshot,
    resolved_market_ids_by_event: Mapping[str, set[int]],
    outcomes_by_market: Mapping[int, bool],
    selected_vectors: dict[TrajectorySelectionKey, SelectedTrajectoryVector],
    selected_distances: dict[TrajectorySelectionKey, float],
) -> None:
    expert_name, expert_version, fingerprint, event_id, bucket, _ts = snapshot_key
    expected_market_ids = resolved_market_ids_by_event.get(event_id, set())
    actual_market_ids = {market.id for _prediction, market, _prob in snapshot_rows}
    outcomes = [outcomes_by_market[market.id] for _, market, _ in snapshot_rows]
    candidate_total = sum(
        float(prediction.probability) for prediction, _, _ in snapshot_rows
    )
    baseline_probabilities = _normalized_baseline_probabilities(snapshot_rows)
    pipeline_versions = {
        prediction.distribution.get("baseline_pipeline_version")
        for prediction, _, _ in snapshot_rows
    }
    if (
        len(expected_market_ids) < 2
        or actual_market_ids != expected_market_ids
        or len(actual_market_ids) != len(snapshot_rows)
        or sum(outcomes) != 1
        or abs(candidate_total - 1.0) > 0.0001
        or baseline_probabilities is None
        or len(pipeline_versions) != 1
    ):
        return
    baseline_pipeline_version = next(iter(pipeline_versions))
    if not isinstance(baseline_pipeline_version, str):
        return
    selection_key = (
        expert_name,
        expert_version,
        fingerprint,
        event_id,
        bucket,
    )
    distance = abs(
        fmean(float(model_prob.horizon_h) for _, _, model_prob in snapshot_rows)
        - HORIZON_ANCHORS[bucket]
    )
    if (
        selection_key not in selected_vectors
        or distance < selected_distances[selection_key]
    ):
        prediction, market, _model_prob = snapshot_rows[0]
        if market.station_id is None:
            return
        selected_vectors[selection_key] = SelectedTrajectoryVector(
            expert=prediction.expert,
            expert_version=prediction.expert_version,
            experiment_fingerprint=fingerprint,
            event_id=event_id,
            station_id=market.station_id,
            horizon_bucket=bucket,
            kind=market.kind,
            baseline_pipeline_version=baseline_pipeline_version,
            market_date=market.market_date,
            resolves_at=market.resolves_at,
            scores=tuple(
                TrajectoryScoreRow(
                    market_id=row_market.id,
                    candidate_probability=row_prediction.probability,
                    baseline_probability=baseline_probabilities[row_market.id],
                )
                for row_prediction, row_market, _row_model_prob in snapshot_rows
            ),
        )
        selected_distances[selection_key] = distance


def _expert_minimum_blocks(settings: Settings, expert: str) -> int:
    if expert == "regularized_stack":
        return settings.accuracy_stack_min_blocks
    if expert == "conformal_gaussian":
        return settings.accuracy_conformal_min_blocks
    if expert == "hierarchical_emos":
        return settings.accuracy_hierarchical_min_blocks
    if expert == "regime_emos":
        return settings.accuracy_regime_min_blocks
    if expert == "trajectory_nowcast":
        return settings.accuracy_nowcast_min_blocks
    if expert in {"qrf", "analog", "mixture"}:
        return settings.accuracy_nonlinear_min_blocks
    return settings.accuracy_trajectory_min_blocks


async def audit_accuracy(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    as_of: datetime | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = as_of or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    now = now.astimezone(UTC)
    day_key = now.date().isoformat()
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory.begin() as session:
            for descriptor in REGIONAL_SHADOW_MODELS:
                await upsert_model_registry(session, descriptor)

        async with session_factory() as session:
            outcome_event_rows = (
                (
                    await session.execute(
                        select(GammaOutcomeEvent).where(
                            GammaOutcomeEvent.fully_resolved_at
                            >= now - timedelta(days=max(WINDOWS)),
                            GammaOutcomeEvent.fully_resolved_at <= now,
                            GammaOutcomeEvent.first_seen_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            outcome_events_by_id: dict[int, GammaOutcomeEvent] = {}
            outcome_events_by_external_id: dict[str, GammaOutcomeEvent] = {}
            outcomes_by_market: dict[int, bool] = {}
            resolved_market_ids_by_event: dict[str, set[int]] = defaultdict(set)
            for outcome_event in outcome_event_rows:
                try:
                    event_outcomes = gamma_outcome_map(outcome_event)
                except GammaOutcomeIntegrityError:
                    logger.exception(
                        "excluding invalid Gamma outcome evidence: event_id=%s",
                        outcome_event.event_id,
                    )
                    continue
                if any(market_id in outcomes_by_market for market_id in event_outcomes):
                    logger.error(
                        "excluding duplicate Gamma market provenance: event_id=%s",
                        outcome_event.event_id,
                    )
                    continue
                outcome_events_by_id[outcome_event.id] = outcome_event
                outcome_events_by_external_id[outcome_event.event_id] = outcome_event
                outcomes_by_market.update(event_outcomes)
                resolved_market_ids_by_event[outcome_event.event_id].update(event_outcomes)
            del outcome_event_rows

            evaluation_rows_raw = (
                await session.execute(
                    select(ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent)
                    .join(ModelProb, ForecastEvaluation.model_prob_id == ModelProb.id)
                    .join(Market, ForecastEvaluation.market_id == Market.id)
                    .join(
                        GammaOutcomeEvent,
                        ForecastEvaluation.resolution_event_id == GammaOutcomeEvent.id,
                    )
                    .where(
                        ForecastEvaluation.decision_at
                        >= now - timedelta(days=max(WINDOWS)),
                        ForecastEvaluation.decision_at <= now,
                        ForecastEvaluation.resolution_event_id.is_not(None),
                        ForecastEvaluation.resolution_vector_hash.is_not(None),
                        ForecastEvaluation.decision_at
                        < GammaOutcomeEvent.first_outcome_at,
                        ForecastEvaluation.resolution_vector_hash
                        == GammaOutcomeEvent.outcome_vector_hash,
                        ForecastEvaluation.market_benchmark_protocol
                        == CLOB_DUAL_PROTOCOL,
                    )
                )
            ).all()
            evaluation_rows: list[tuple[ForecastEvaluation, ModelProb, Market]] = []
            for evaluation, model_prob, market, outcome_event in evaluation_rows_raw:
                if outcome_event is None:
                    continue
                valid_event = outcome_events_by_id.get(outcome_event.id)
                if (
                    valid_event is None
                    or market.event_id != valid_event.event_id
                    or market.resolution_event_id != valid_event.id
                    or market.id not in outcomes_by_market
                    or evaluation.market_id != model_prob.market_id
                    or evaluation.decision_at != model_prob.ts
                    or evaluation.model_probability != model_prob.prob
                    or evaluation.resolved_outcome != outcomes_by_market[market.id]
                ):
                    continue
                evaluation_rows.append((evaluation, model_prob, market))
            del evaluation_rows_raw
            rows = [
                (evaluation, market)
                for evaluation, _model_prob, market in evaluation_rows
            ]
            trajectory_bucket = AccuracyPrediction.distribution[
                "horizon_bucket"
            ].as_string()
            trajectory_statement = (
                select(
                    AccuracyPrediction.market_id.label("prediction_market_id"),
                    AccuracyPrediction.ts.label("prediction_ts"),
                    AccuracyPrediction.expert.label("prediction_expert"),
                    AccuracyPrediction.expert_version.label(
                        "prediction_expert_version"
                    ),
                    AccuracyPrediction.probability.label(
                        "prediction_probability"
                    ),
                    AccuracyPrediction.mean_c.label("prediction_mean_c"),
                    AccuracyPrediction.sigma_c.label("prediction_sigma_c"),
                    AccuracyPrediction.training_sample_n.label(
                        "prediction_training_sample_n"
                    ),
                    AccuracyPrediction.point_in_time_valid.label(
                        "prediction_point_in_time_valid"
                    ),
                    AccuracyPrediction.generated_at.label(
                        "prediction_generated_at"
                    ),
                    AccuracyPrediction.training_cutoff.label(
                        "prediction_training_cutoff"
                    ),
                    AccuracyPrediction.baseline_model_prob_id.label(
                        "prediction_baseline_model_prob_id"
                    ),
                    AccuracyPrediction.input_hash.label("prediction_input_hash"),
                    AccuracyPrediction.experiment_fingerprint.label(
                        "prediction_experiment_fingerprint"
                    ),
                    AccuracyPrediction.record_hash.label("prediction_record_hash"),
                    AccuracyPrediction.evidence_eligible.label(
                        "prediction_evidence_eligible"
                    ),
                    AccuracyPrediction.distribution.label(
                        "prediction_distribution"
                    ),
                    Market.id.label("market_id"),
                    Market.event_id.label("market_event_id"),
                    Market.station_id.label("market_station_id"),
                    Market.kind.label("market_kind"),
                    Market.market_date.label("market_date"),
                    Market.resolves_at.label("market_resolves_at"),
                    ModelProb.id.label("model_prob_id"),
                    ModelProb.market_id.label("model_prob_market_id"),
                    ModelProb.ts.label("model_prob_ts"),
                    ModelProb.prob.label("model_prob_prob"),
                    ModelProb.horizon_h.label("model_prob_horizon_h"),
                    ModelProb.components["pipeline"].label(
                        "model_prob_pipeline"
                    ),
                )
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
                    AccuracyPrediction.expert.in_(PROSPECTIVE_EXPERTS),
                    AccuracyPrediction.point_in_time_valid.is_(True),
                    AccuracyPrediction.evidence_eligible.is_(True),
                    AccuracyPrediction.record_hash.is_not(None),
                    AccuracyPrediction.input_hash.is_not(None),
                    AccuracyPrediction.experiment_fingerprint.is_not(None),
                    AccuracyPrediction.training_cutoff.is_not(None),
                    AccuracyPrediction.ts <= AccuracyPrediction.generated_at,
                    AccuracyPrediction.ts <= now,
                    AccuracyPrediction.generated_at <= now,
                    AccuracyPrediction.training_cutoff
                    <= AccuracyPrediction.generated_at,
                    AccuracyPrediction.generated_at < Market.resolves_at,
                    AccuracyPrediction.ts >= now - timedelta(days=max(WINDOWS)),
                    Market.event_id.in_(outcome_events_by_external_id),
                )
                .order_by(
                    AccuracyPrediction.expert,
                    AccuracyPrediction.expert_version,
                    AccuracyPrediction.experiment_fingerprint,
                    Market.event_id,
                    trajectory_bucket,
                    AccuracyPrediction.ts,
                    Market.id,
                )
                .execution_options(yield_per=512)
            )
            selected_vectors: dict[
                TrajectorySelectionKey, SelectedTrajectoryVector
            ] = {}
            selected_distances: dict[TrajectorySelectionKey, float] = {}
            current_snapshot_key: TrajectorySnapshotKey | None = None
            current_snapshot_rows: TrajectorySnapshot = []
            trajectory_result = await session.stream(trajectory_statement)
            try:
                async for mapping in trajectory_result.mappings():
                    prediction, market, model_prob = (
                        _trajectory_projection_from_mapping(mapping)
                    )
                    if (
                        not _trajectory_prediction_record_is_valid(prediction)
                        or market.id not in outcomes_by_market
                        or prediction.generated_at
                        >= outcome_events_by_external_id[
                            market.event_id
                        ].first_outcome_at
                    ):
                        continue
                    fingerprint = prediction.experiment_fingerprint
                    bucket = prediction.distribution.get("horizon_bucket")
                    pipeline_version = prediction.distribution.get(
                        "baseline_pipeline_version"
                    )
                    stored_baseline = prediction.distribution.get(
                        "baseline_probability"
                    )
                    if (
                        market.station_id is None
                        or not isinstance(fingerprint, str)
                        or not isinstance(bucket, str)
                        or bucket not in HORIZON_ANCHORS
                        or not isinstance(pipeline_version, str)
                        or not isinstance(stored_baseline, int | float)
                        or isinstance(stored_baseline, bool)
                    ):
                        continue
                    snapshot_key: TrajectorySnapshotKey = (
                        prediction.expert,
                        prediction.expert_version,
                        fingerprint,
                        market.event_id,
                        bucket,
                        prediction.ts,
                    )
                    if (
                        current_snapshot_key is not None
                        and snapshot_key != current_snapshot_key
                    ):
                        _consider_trajectory_snapshot(
                            snapshot_key=current_snapshot_key,
                            snapshot_rows=current_snapshot_rows,
                            resolved_market_ids_by_event=resolved_market_ids_by_event,
                            outcomes_by_market=outcomes_by_market,
                            selected_vectors=selected_vectors,
                            selected_distances=selected_distances,
                        )
                        current_snapshot_rows = []
                    current_snapshot_key = snapshot_key
                    current_snapshot_rows.append((prediction, market, model_prob))
            finally:
                await trajectory_result.close()
            if current_snapshot_key is not None:
                _consider_trajectory_snapshot(
                    snapshot_key=current_snapshot_key,
                    snapshot_rows=current_snapshot_rows,
                    resolved_market_ids_by_event=resolved_market_ids_by_event,
                    outcomes_by_market=outcomes_by_market,
                    selected_vectors=selected_vectors,
                    selected_distances=selected_distances,
                )
            del current_snapshot_rows, selected_distances
            trajectory_cohort_rows = (
                await session.execute(
                    select(
                        AccuracyPrediction.expert,
                        AccuracyPrediction.expert_version,
                        AccuracyPrediction.experiment_fingerprint,
                        func.min(AccuracyPrediction.generated_at),
                    )
                    .where(
                        AccuracyPrediction.expert.in_(PROSPECTIVE_EXPERTS),
                        AccuracyPrediction.evidence_eligible.is_(True),
                        AccuracyPrediction.experiment_fingerprint.is_not(None),
                        AccuracyPrediction.generated_at <= now,
                    )
                    .group_by(
                        AccuracyPrediction.expert,
                        AccuracyPrediction.expert_version,
                        AccuracyPrediction.experiment_fingerprint,
                    )
                )
            ).all()
            selected_forecast_rows = await _selected_forecast_rows(
                session,
                as_of=now,
                cutoff=(now - timedelta(days=max(WINDOWS))).date(),
            )
            daily_actual_rows = (
                await session.execute(
                    _daily_actual_truth_statement(as_of=now)
                )
            ).all()
            canonical_actuals = canonical_settlement_actuals(as_of=now)
            exact_actual_rows = (
                await session.execute(
                    select(
                        canonical_actuals.c.station_id,
                        canonical_actuals.c.date,
                        canonical_actuals.c.kind,
                        canonical_actuals.c.value_c,
                    )
                )
            ).all()
            historical_rows = (
                (
                    await session.execute(
                        select(
                            HistoricalForecast.station_id,
                            HistoricalForecast.model,
                            HistoricalForecast.valid_date,
                            HistoricalForecast.kind,
                            HistoricalForecast.horizon_h,
                            HistoricalForecast.temp_c,
                        ).where(
                            HistoricalForecast.valid_date
                            >= now.date()
                            - timedelta(
                                days=(
                                    settings.accuracy_regime_recent_days
                                    + settings.accuracy_regime_baseline_days
                                )
                            ),
                            HistoricalForecast.fetched_at <= now,
                            or_(
                                HistoricalForecast.first_seen_at.is_(None),
                                HistoricalForecast.first_seen_at <= now,
                            ),
                        )
                    )
                )
                .all()
            )
            registry_rows = (
                (await session.execute(select(ForecastModelRegistry))).scalars().all()
            )

        grouped: dict[tuple[int, str, str], list[tuple[ForecastEvaluation, Market]]] = defaultdict(
            list
        )
        for evaluation, market in rows:
            bucket = evaluation.details.get("horizon_bucket")
            if market.station_id is None or not isinstance(bucket, str):
                continue
            grouped[(market.station_id, bucket, market.kind.value)].append((evaluation, market))

        bootstrap_cache: BlockBootstrapCache = {}
        report_rows: list[dict[str, object]] = []
        drift_rows: list[dict[str, object]] = []
        for (station_id, bucket, kind), cases in grouped.items():
            for window in WINDOWS:
                window_start = now - timedelta(days=window)
                window_cases = [
                    evaluation
                    for evaluation, _market in cases
                    if evaluation.decision_at >= window_start
                ]
                if not window_cases:
                    continue
                model_briers = [
                    (
                        float(row.model_probability)
                        - float(outcomes_by_market[row.market_id])
                    )
                    ** 2
                    for row in window_cases
                ]
                paired = [
                    row
                    for row in window_cases
                    if row.horizon_market_probability is not None
                ]
                deltas = [
                    (
                        float(row.model_probability)
                        - float(outcomes_by_market[row.market_id])
                    )
                    ** 2
                    - (
                        float(row.horizon_market_probability)  # type: ignore[arg-type]
                        - float(outcomes_by_market[row.market_id])
                    )
                    ** 2
                    for row in paired
                ]
                blocks: dict[str, list[float]] = defaultdict(list)
                for row in paired:
                    blocks[row.independent_sample_key].append(
                        (
                            float(row.model_probability)
                            - float(outcomes_by_market[row.market_id])
                        )
                        ** 2
                        - (
                            float(row.horizon_market_probability)  # type: ignore[arg-type]
                            - float(outcomes_by_market[row.market_id])
                        )
                        ** 2
                    )
                ci = _cached_block_bootstrap_mean_ci(blocks, bootstrap_cache)
                coverage = len(paired) / len(window_cases)
                delta_brier = fmean(deltas) if deltas else None
                report_rows.append(
                    {
                        "report_key": f"baseline:{station_id}:{bucket}:{kind}:{window}:{day_key}",
                        "registry_id": None,
                        "station_id": station_id,
                        "horizon_bucket": bucket,
                        "kind": kind,
                        "window_days": window,
                        "sample_n": len(window_cases),
                        "coverage": Decimal(str(coverage)),
                        "p95_latency_minutes": None,
                        "brier": Decimal(str(fmean(model_briers))),
                        "crps": None,
                        "delta_brier": (
                            Decimal(str(delta_brier)) if delta_brier is not None else None
                        ),
                        "delta_crps": None,
                        "ci_low": Decimal(str(ci[0])) if ci else None,
                        "ci_high": Decimal(str(ci[1])) if ci else None,
                        "eligible": bool(
                            len(blocks) >= 30
                            and coverage >= 0.99
                            and delta_brier is not None
                            and ci is not None
                            and ci[1] < 0
                        ),
                        "evaluated_at": now,
                        "details": {
                            "independent_blocks": len(blocks),
                            "benchmark": "same_horizon_market_snapshot",
                            "market_benchmark_protocol": CLOB_DUAL_PROTOCOL,
                            "resolution_evidence_protocol": "gamma-outcome-v1",
                        },
                    }
                )

                half = now - timedelta(days=window / 2)
                recent = [
                    (
                        float(row.model_probability)
                        - float(outcomes_by_market[row.market_id])
                    )
                    ** 2
                    for row in window_cases
                    if row.decision_at >= half
                ]
                baseline = [
                    (
                        float(row.model_probability)
                        - float(outcomes_by_market[row.market_id])
                    )
                    ** 2
                    for row in window_cases
                    if row.decision_at < half
                ]
                if len(recent) < 10 or len(baseline) < 10:
                    continue
                baseline_value = fmean(baseline)
                recent_value = fmean(recent)
                delta = recent_value - baseline_value
                relative = delta / baseline_value if baseline_value > 0 else 0.0
                if relative <= 0.05:
                    continue
                severity = "critical" if relative >= 0.20 else "warning"
                drift_rows.append(
                    {
                        "event_key": f"brier:{station_id}:{bucket}:{kind}:{window}:{day_key}",
                        "registry_id": None,
                        "station_id": station_id,
                        "detected_at": now,
                        "window_days": window,
                        "metric": "model_brier",
                        "baseline_value": Decimal(str(baseline_value)),
                        "recent_value": Decimal(str(recent_value)),
                        "delta": Decimal(str(delta)),
                        "severity": severity,
                        "details": {
                            "relative_change": relative,
                            "baseline_n": len(baseline),
                            "recent_n": len(recent),
                        },
                    }
                )

        # Prospective shadow bake-off for probability challengers. Evidence is
        # selected as an atomic event vector: one timestamp, one pipeline, every
        # bucket present, probabilities summing to one, and exactly one winner.
        cohort_starts = {
            (expert, expert_version, fingerprint): started_at
            for expert, expert_version, fingerprint, started_at in trajectory_cohort_rows
            if isinstance(fingerprint, str) and started_at is not None
        }
        trajectory_cases: dict[
            tuple[str, str, str, int, str, WeatherKind, str],
            list[SelectedTrajectoryVector],
        ] = defaultdict(list)
        for vector in selected_vectors.values():
            trajectory_cases[
                (
                    vector.expert,
                    vector.expert_version,
                    vector.experiment_fingerprint,
                    vector.station_id,
                    vector.horizon_bucket,
                    vector.kind,
                    vector.baseline_pipeline_version,
                )
            ].append(vector)
        del selected_vectors

        for (
            expert_name,
            expert_version,
            fingerprint,
            station_id,
            bucket,
            kind,
            pipeline_version,
        ), trajectory_group_cases in trajectory_cases.items():
            cohort_start = cohort_starts.get(
                (expert_name, expert_version, fingerprint)
            )
            if cohort_start is None:
                continue
            for window in WINDOWS:
                window_start = now - timedelta(days=window)
                evidence_start = max(window_start, cohort_start)
                trajectory_window_cases = [
                    vector
                    for vector in trajectory_group_cases
                    if vector.resolves_at >= evidence_start
                ]
                if not trajectory_window_cases:
                    continue
                trajectory_briers: list[float] = []
                trajectory_deltas: list[float] = []
                trajectory_blocks: dict[str, list[float]] = defaultdict(list)
                prediction_count = 0
                for vector in trajectory_window_cases:
                    block_key = (
                        f"{station_id}:{vector.market_date.isoformat()}:"
                        f"{kind.value}:{bucket}"
                    )
                    for score in vector.scores:
                        outcome = float(outcomes_by_market[score.market_id])
                        trajectory_brier = (
                            float(score.candidate_probability) - outcome
                        ) ** 2
                        baseline_brier = (
                            score.baseline_probability - outcome
                        ) ** 2
                        delta = trajectory_brier - baseline_brier
                        trajectory_briers.append(trajectory_brier)
                        trajectory_deltas.append(delta)
                        trajectory_blocks[block_key].append(delta)
                        prediction_count += 1
                expected_blocks = {
                    (
                        f"{station_id}:{market.market_date.isoformat()}:"
                        f"{kind.value}:{bucket}"
                    )
                    for evaluation, model_prob, market in evaluation_rows
                    if market.station_id == station_id
                    and market.kind == kind
                    and market.resolves_at >= evidence_start
                    and evaluation.details.get("horizon_bucket") == bucket
                    and _model_prob_pipeline_version(model_prob) == pipeline_version
                }
                covered_blocks = set(trajectory_blocks).intersection(expected_blocks)
                coverage = (
                    len(covered_blocks) / len(expected_blocks) if expected_blocks else 0.0
                )
                ci = _cached_block_bootstrap_mean_ci(
                    trajectory_blocks, bootstrap_cache
                )
                delta_brier = fmean(trajectory_deltas)
                independent_blocks = len(trajectory_blocks)
                minimum_blocks = _expert_minimum_blocks(settings, expert_name)
                trajectory_report_identity = hashlib.sha256(
                    (
                        f"{expert_name}:{expert_version}:{fingerprint}:{station_id}:"
                        f"{bucket}:{kind.value}:{window}"
                    ).encode()
                ).hexdigest()
                report_rows.append(
                    {
                        "report_key": f"expert:{trajectory_report_identity}:{day_key}",
                        "registry_id": None,
                        "station_id": station_id,
                        "horizon_bucket": bucket,
                        "kind": kind.value,
                        "window_days": window,
                        "sample_n": independent_blocks,
                        "coverage": Decimal(str(coverage)),
                        "p95_latency_minutes": None,
                        "brier": Decimal(str(fmean(trajectory_briers))),
                        "crps": None,
                        "delta_brier": Decimal(str(delta_brier)),
                        "delta_crps": None,
                        "ci_low": Decimal(str(ci[0])) if ci else None,
                        "ci_high": Decimal(str(ci[1])) if ci else None,
                        "eligible": bool(
                            independent_blocks >= minimum_blocks
                            and coverage >= 0.99
                            and delta_brier < 0
                            and ci is not None
                            and ci[1] < 0
                        ),
                        "evaluated_at": now,
                        "details": {
                            "expert": expert_name,
                            "expert_version": expert_version,
                            "experiment_fingerprint": fingerprint,
                            "baseline_pipeline_version": pipeline_version,
                            "evidence_protocol": "sealed-v2",
                            "resolution_evidence_protocol": "gamma-outcome-v1",
                            "baseline": "sealed_probability_same_timestamp",
                            "independent_blocks": independent_blocks,
                            "prediction_count": prediction_count,
                            "expected_blocks": len(expected_blocks),
                            "covered_blocks": len(covered_blocks),
                            "selection": "nearest_complete_event_vector_horizon_anchor",
                        },
                    }
                )

        # Point-in-time deterministic model bake-off. A degenerate predictive
        # distribution has CRPS equal to absolute error, so this remains a
        # proper scoring rule while regional models are still shadow-only.
        truth: dict[tuple[int, date, WeatherKind], float] = {
            (row.station_id, row.date, row.kind): float(row.temp_c) for row in daily_actual_rows
        }
        truth.update(
            {
                (row.station_id, row.date, row.kind): float(row.value_c)
                for row in exact_actual_rows
            }
        )
        del daily_actual_rows, exact_actual_rows
        truth_dates = _truth_date_index(truth)

        # Version-aware residual regime monitor. Exact known release boundaries
        # take priority; operational-unversioned models use a rolling change point.
        regime_residuals: dict[
            tuple[int, str, str, WeatherKind], list[DatedResidual]
        ] = defaultdict(list)
        for historical in historical_rows:
            model = canonical_model(historical.model)
            if model not in {"ecmwf", "gfs", "icon", "gem", "nws"}:
                continue
            actual_c = truth.get((historical.station_id, historical.valid_date, historical.kind))
            if actual_c is None:
                continue
            regime_residuals[
                (
                    historical.station_id,
                    model,
                    _horizon_bucket(float(historical.horizon_h)),
                    historical.kind,
                )
            ].append(
                DatedResidual(
                    day=historical.valid_date,
                    residual_c=actual_c - float(historical.temp_c),
                )
            )

        registry_by_version = {
            (row.model, row.model_version): row.id for row in registry_rows
        }
        for (station_id, model, bucket, kind), observations in regime_residuals.items():
            try:
                descriptor = descriptor_for(model, init_time=now, source_url="historical")
            except ValueError:
                continue
            introduced_on = (
                descriptor.introduced_at.date()
                if descriptor.introduced_at is not None
                else None
            )
            seed_material = f"{station_id}:{model}:{bucket}:{kind.value}:{day_key}"
            seed = int.from_bytes(
                hashlib.sha256(seed_material.encode()).digest()[:8], "big"
            )
            regime = detect_regime_shift(
                observations,
                as_of=now.date(),
                known_version_date=introduced_on,
                recent_days=settings.accuracy_regime_recent_days,
                baseline_days=settings.accuracy_regime_baseline_days,
                minimum_recent=settings.accuracy_regime_min_recent,
                minimum_baseline=settings.accuracy_regime_min_baseline,
                minimum_shift_c=settings.accuracy_regime_min_shift_c,
                maximum_adjustment_c=settings.accuracy_regime_max_adjustment_c,
                bootstrap_samples=settings.accuracy_regime_bootstrap_samples,
                seed=seed,
            )
            if regime is None or not regime.detected:
                continue
            drift_rows.append(
                {
                    "event_key": (
                        f"residual:{station_id}:{model}:{bucket}:{kind.value}:"
                        f"{regime.boundary_date.isoformat()}:{day_key}"
                    ),
                    "registry_id": registry_by_version.get(
                        (descriptor.model, descriptor.model_version)
                    ),
                    "station_id": station_id,
                    "detected_at": now,
                    "window_days": settings.accuracy_regime_recent_days,
                    "metric": "residual_bias_shift",
                    "baseline_value": Decimal(str(regime.baseline_bias_c)),
                    "recent_value": Decimal(str(regime.recent_bias_c)),
                    "delta": Decimal(str(regime.shift_c)),
                    "severity": "critical" if abs(regime.shift_c) >= 1.0 else "warning",
                    "details": {
                        "model": model,
                        "model_version": descriptor.model_version,
                        "horizon_bucket": bucket,
                        "kind": kind.value,
                        "boundary_kind": regime.boundary_kind,
                        "boundary_date": regime.boundary_date.isoformat(),
                        "adjustment_c": regime.adjustment_c,
                        "ci_low_c": regime.ci_low_c,
                        "ci_high_c": regime.ci_high_c,
                        "baseline_n": regime.baseline_n,
                        "recent_n": regime.recent_n,
                        "truth_source": (
                            "canonical_agreed_settlement_then_current_reconciliation_clean_"
                            "daily_actual_fallback"
                        ),
                    },
                }
            )
        del historical_rows

        baseline_errors: dict[tuple[int, date, WeatherKind, str], list[float]] = defaultdict(list)
        selected_cases: list[tuple[SelectedForecastRow, float, float, str]] = []
        for selected in selected_forecast_rows:
            actual_c = truth.get(
                (selected.station_id, selected.valid_date, selected.kind)
            )
            if actual_c is None:
                continue
            error = abs(float(selected.temp_c) - actual_c)
            latency = (
                selected.available_at - selected.init_time
            ).total_seconds() / 60
            selected_cases.append(
                (selected, error, latency, selected.horizon_bucket)
            )
        del selected_forecast_rows

        for selected, error, _latency, bucket in selected_cases:
            if selected.registry_enabled and not selected.registry_shadow_only:
                baseline_errors[
                    (selected.station_id, selected.valid_date, selected.kind, bucket)
                ].append(error)

        model_cases: dict[
            tuple[int, int, str, WeatherKind],
            list[tuple[date, float, float, float | None, bool, bool, int | None]],
        ] = defaultdict(list)
        for selected, error, latency, bucket in selected_cases:
            baseline_for_case = baseline_errors.get(
                (selected.station_id, selected.valid_date, selected.kind, bucket)
            )
            error_delta = error - fmean(baseline_for_case) if baseline_for_case else None
            model_cases[
                (
                    selected.registry_id,
                    selected.station_id,
                    bucket,
                    selected.kind,
                )
            ].append(
                (
                    selected.valid_date,
                    error,
                    latency,
                    error_delta,
                    selected.availability_verified,
                    selected.registry_shadow_only,
                    selected.expected_latency_minutes,
                )
            )
        del selected_cases

        for (
            registry_id,
            station_id,
            bucket,
            kind,
        ), model_case_rows in model_cases.items():
            registry_shadow_only = model_case_rows[0][5]
            expected_latency = model_case_rows[0][6]
            for window in WINDOWS:
                window_start_date = (now - timedelta(days=window)).date()
                model_window_cases = [
                    case for case in model_case_rows if case[0] >= window_start_date
                ]
                if not model_window_cases:
                    continue
                model_errors = [case[1] for case in model_window_cases]
                model_latencies = [case[2] for case in model_window_cases]
                model_deltas: list[float] = []
                model_blocks: dict[str, list[float]] = {}
                for case in model_window_cases:
                    case_delta = case[3]
                    if case_delta is None:
                        continue
                    model_deltas.append(case_delta)
                    model_blocks[str(case[0])] = [case_delta]
                model_ci = _cached_block_bootstrap_mean_ci(
                    model_blocks, bootstrap_cache
                )
                truth_day_count = _truth_day_count(
                    truth_dates,
                    station_id=station_id,
                    kind=kind,
                    starting_on=window_start_date,
                )
                model_coverage = len({case[0] for case in model_window_cases}) / max(
                    truth_day_count, 1
                )
                p95_latency = _percentile_95(model_latencies)
                delta_crps = fmean(model_deltas) if model_deltas else None
                availability_verified = all(case[4] for case in model_window_cases)
                eligible = bool(
                    registry_shadow_only
                    and availability_verified
                    and len(model_blocks) >= 30
                    and model_coverage >= 0.90
                    and p95_latency is not None
                    and expected_latency is not None
                    and p95_latency <= expected_latency + 60
                    and model_ci is not None
                    and model_ci[1] < 0
                )
                report_rows.append(
                    {
                        "report_key": (
                            f"model:{registry_id}:{station_id}:{bucket}:"
                            f"{kind.value}:{window}:{day_key}"
                        ),
                        "registry_id": registry_id,
                        "station_id": station_id,
                        "horizon_bucket": bucket,
                        "kind": kind.value,
                        "window_days": window,
                        "sample_n": len(model_window_cases),
                        "coverage": Decimal(str(min(1.0, model_coverage))),
                        "p95_latency_minutes": (
                            Decimal(str(p95_latency)) if p95_latency is not None else None
                        ),
                        "brier": None,
                        "crps": Decimal(str(fmean(model_errors))),
                        "delta_brier": None,
                        "delta_crps": (
                            Decimal(str(delta_crps)) if delta_crps is not None else None
                        ),
                        "ci_low": Decimal(str(model_ci[0])) if model_ci else None,
                        "ci_high": Decimal(str(model_ci[1])) if model_ci else None,
                        "eligible": eligible,
                        "evaluated_at": now,
                        "details": {
                            "metric": "deterministic_crps_equals_absolute_error",
                            "baseline": "mean_enabled_global_models_same_horizon",
                            "independent_blocks": len(model_blocks),
                            "availability_verified": availability_verified,
                        },
                    }
                )

        async with session_factory.begin() as session:
            for report_batch in _row_batches(report_rows):
                report_statement = insert(ModelSkillReport).values(report_batch)
                report_statement = report_statement.on_conflict_do_update(
                    index_elements=[ModelSkillReport.report_key],
                    set_={
                        "sample_n": report_statement.excluded.sample_n,
                        "coverage": report_statement.excluded.coverage,
                        "p95_latency_minutes": report_statement.excluded.p95_latency_minutes,
                        "brier": report_statement.excluded.brier,
                        "crps": report_statement.excluded.crps,
                        "delta_brier": report_statement.excluded.delta_brier,
                        "delta_crps": report_statement.excluded.delta_crps,
                        "ci_low": report_statement.excluded.ci_low,
                        "ci_high": report_statement.excluded.ci_high,
                        "eligible": report_statement.excluded.eligible,
                        "evaluated_at": report_statement.excluded.evaluated_at,
                        "details": report_statement.excluded.details,
                    },
                )
                await session.execute(report_statement)
            for drift_batch in _row_batches(drift_rows):
                drift_statement = insert(ModelDriftEvent).values(drift_batch)
                drift_statement = drift_statement.on_conflict_do_update(
                    index_elements=[ModelDriftEvent.event_key],
                    set_={
                        "detected_at": drift_statement.excluded.detected_at,
                        "baseline_value": drift_statement.excluded.baseline_value,
                        "recent_value": drift_statement.excluded.recent_value,
                        "delta": drift_statement.excluded.delta,
                        "severity": drift_statement.excluded.severity,
                        "details": drift_statement.excluded.details,
                    },
                )
                await session.execute(drift_statement)
        logger.info(
            "accuracy audit complete: skill_reports=%d drift_events=%d",
            len(report_rows),
            len(drift_rows),
        )
        return len(report_rows), len(drift_rows)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_accuracy(settings))


if __name__ == "__main__":
    main()
