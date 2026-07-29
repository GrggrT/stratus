from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from stratus.config import Settings, get_settings
from stratus.db.models import (
    Calibration,
    ClobBookBatch,
    ClobMarketSnapshot,
    DailyActual,
    Forecast,
    ForecastIngest,
    ForecastTrajectory,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelDriftEvent,
    ModelProb,
    ModelSkillReport,
    Observation,
    SettlementProbabilityCalibrator,
    Station,
    StationStage,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    upsert_accuracy_predictions,
    upsert_daily_stack_weights,
)
from stratus.repositories.accuracy_promotions import (
    EffectivePromotion,
    EffectivePromotionManifest,
    load_effective_accuracy_promotion_manifest,
)
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs
from stratus.repositories.signals import expire_unclaimed_pending_signals_for_events
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)
from stratus.services.accuracy_models import (
    AnalogCase,
    AnalogForecast,
    AR1Fit,
    GaussianMixture,
    QuantileRegressionForest,
    analog_ensemble,
    ar1_adjustment,
    blend_experts,
    constrained_extreme_probability,
    fit_ar1,
    fit_regularized_stack,
    fit_two_gaussian_mixture,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL, LEGACY_MARKET_PROTOCOL
from stratus.services.conformal_probability import ConformalScale, fit_conformal_scale
from stratus.services.emos import horizon_bucket
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    EventBucketProbability,
    assess_event_probability_coherence,
    assess_event_probability_materialization,
    event_probability_distribution_sha256,
    pipeline_cohort_from_components,
)
from stratus.services.forecast_provenance import build_model_input_provenance
from stratus.services.hierarchical_probability import (
    CalibrationPeer,
    HierarchicalCalibration,
    partial_pool_calibration,
)
from stratus.services.model_catalog import canonical_model
from stratus.services.pipeline_identity import (
    probability_pipeline_base_cohort,
    probability_pipeline_fingerprint,
    probability_pipeline_metadata,
)
from stratus.services.probability import (
    Bucket,
    CalibrationParams,
    ComponentScore,
    ProbabilityComponent,
    apply_adaptive_weights,
    blend_probabilities,
    component_brier_scores,
    deterministic_component,
    ensemble_component,
    gaussian_bucket_probability,
    renormalize_event,
    shrink_to_market,
)
from stratus.services.regime_probability import apply_regime_shift_to_bucket
from stratus.services.sealed_accuracy import (
    seal_legacy_accuracy_predictions,
    sealed_expert_version,
)
from stratus.services.settlement_calibration import calibrate_partition
from stratus.services.trajectory_probability import (
    HourlyCurve,
    build_remaining_day_nowcast,
    empirical_trajectory_probability,
    sample_daily_extremes,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EventProbabilityMaterialization:
    values: tuple[ModelProbabilityValue, ...]
    valid_event_ids: frozenset[str]
    blocked_event_ids: frozenset[str]


def _ar_shift(
    ar_models: dict[tuple[int, str, str, WeatherKind], tuple[AR1Fit, float]],
    station_id: int,
    model: str,
    target_bucket: str,
    kind: WeatherKind,
) -> float:
    fitted = ar_models.get((station_id, model, target_bucket, kind))
    if fitted is None:
        return 0.0
    fit, latest_residual = fitted
    shift, _ = ar1_adjustment(fit, latest_residual)
    return shift


DEFAULT_WEIGHTS = {
    "ecmwf_ens": 0.35,
    "gefs": 0.30,
    "icon": 0.15,
    "gfs": 0.10,
    "gem": 0.10,
    "nws": 0.10,
}
BUCKET_ORDER = ("0-6h", "6-24h", "24-48h", "48h+")
BRIER_WEIGHT_K = 5.0
BRIER_WEIGHT_MIN_SAMPLES = 30
# A market anchor older than this is not a usable shrinkage target; the
# price cycle refreshes snapshots every minute on active markets.
SHRINKAGE_PRICE_MAX_AGE = timedelta(minutes=30)
STACK_EXPERT = "regularized_stack"
STACK_EXPERT_VERSION = "regularized-brier-v1"
StackKey = tuple[int, str, WeatherKind]
StackCase = tuple[dict[str, float], bool]
PromotionLookupKey = tuple[str, str, int, str, str]
PromotionLookup = dict[PromotionLookupKey, EffectivePromotion]


@dataclass(frozen=True, slots=True)
class StackFit:
    weights: dict[str, float]
    source: str
    training_sample_n: int
    local_sample_n: int
    peer_sample_n: int
    peer_stations: int


def _stack_gate_passed(
    *,
    has_fitted_weights: bool,
    promotion_active: bool,
) -> bool:
    """Apply a fitted stack only after an immutable promotion activation.

    Rolling skill reports remain useful diagnostics, but they are deliberately
    not an application authority: repeated rolling looks would otherwise let a
    challenger drift into production through optional stopping.
    """

    return bool(has_fitted_weights and promotion_active)


def _promotion_lookup(manifest: EffectivePromotionManifest) -> PromotionLookup:
    """Build the exact legacy-candidate lookup used by the probability loop."""

    result: PromotionLookup = {}
    for promotion in manifest.promotions:
        key = (
            promotion.key.expert,
            promotion.key.legacy_expert_version,
            promotion.key.station_id,
            promotion.key.horizon_bucket,
            promotion.key.kind,
        )
        if key in result:
            raise RuntimeError(f"multiple effective promotions for scope {key!r}")
        result[key] = promotion
    return result


def _promotion_for(
    promotions: PromotionLookup,
    *,
    expert: str,
    legacy_expert_version: str,
    station_id: int,
    target_bucket: str,
    kind: WeatherKind,
) -> EffectivePromotion | None:
    promotion = promotions.get(
        (expert, legacy_expert_version, station_id, target_bucket, kind.value)
    )
    if promotion is None:
        return None
    # This redundant check makes the legacy/sealed version binding explicit at
    # the application boundary, instead of trusting a merely matching label.
    if promotion.key.expert_version != sealed_expert_version(legacy_expert_version):
        return None
    return promotion


def _promotion_audit_fields(
    promotion: EffectivePromotion | None,
) -> dict[str, object]:
    if promotion is None:
        return {
            "promotion_active": False,
            "activation_id": None,
            "promotion_definition_hash": None,
            "promotion_decision_hash": None,
        }
    return {
        "promotion_active": True,
        "activation_id": promotion.activation_id,
        "promotion_definition_hash": promotion.definition_hash,
        "promotion_decision_hash": promotion.decision_hash,
    }


def _fit_stack_candidates(
    cases_by_scope: dict[StackKey, list[StackCase]],
    target_scopes: set[StackKey],
    *,
    minimum_samples: int,
    minimum_peer_stations: int,
    l2: float,
    learning_rate: float,
) -> dict[StackKey, StackFit]:
    """Fit local stacks first, otherwise a leave-one-station-out parent.

    A new station never contributes observations to its own parent fit. This
    creates useful shadow forecasts early without leaking local outcomes or
    weakening the independent prospective application gate.
    """
    fits: dict[StackKey, StackFit] = {}
    for target in target_scopes:
        station_id, bucket, kind = target
        local_cases = cases_by_scope.get(target, [])
        source = "local"
        training_cases = local_cases
        peer_station_ids: set[int] = set()
        peer_sample_n = 0
        if len(local_cases) < minimum_samples:
            source = "peer_pool"
            training_cases = []
            for (peer_station_id, peer_bucket, peer_kind), peer_cases in (
                cases_by_scope.items()
            ):
                if (
                    peer_station_id == station_id
                    or peer_bucket != bucket
                    or peer_kind != kind
                ):
                    continue
                peer_station_ids.add(peer_station_id)
                training_cases.extend(peer_cases)
            peer_sample_n = len(training_cases)
            if (
                peer_sample_n < minimum_samples
                or len(peer_station_ids) < minimum_peer_stations
            ):
                continue
        try:
            weights = fit_regularized_stack(
                [row for row, _ in training_cases],
                [outcome for _, outcome in training_cases],
                l2=l2,
                learning_rate=learning_rate,
            )
        except ValueError:
            logger.debug("stack training skipped for sparse expert overlap: %s", target)
            continue
        fits[target] = StackFit(
            weights=weights,
            source=source,
            training_sample_n=len(training_cases),
            local_sample_n=len(local_cases),
            peer_sample_n=peer_sample_n,
            peer_stations=len(peer_station_ids),
        )
    return fits


def _calibration_depth(
    calibrations: dict[tuple[int, str, str, Any], Calibration],
    *,
    station_id: int,
    target_bucket: str,
    kind: Any,
) -> int:
    """Sample depth of the calibration actually backing this probability.

    Mirrors _pick_calibration: each model contributes the row nearest to the
    trading horizon bucket (the same fallback used to compute the
    probability), and the deepest model wins. Zero when nothing is
    calibrated — the shrinkage weight and the trading gate both read this.
    """
    target_index = BUCKET_ORDER.index(target_bucket)
    nearest_by_model: dict[str, tuple[int, int]] = {}
    for (candidate_station, model, bucket, candidate_kind), row in calibrations.items():
        if candidate_station != station_id or candidate_kind != kind:
            continue
        distance = abs(BUCKET_ORDER.index(bucket) - target_index)
        current = nearest_by_model.get(model)
        if current is None or distance < current[0]:
            nearest_by_model[model] = (distance, row.sample_n)
    return max((sample_n for _, sample_n in nearest_by_model.values()), default=0)


def _pick_calibration(
    calibrations: dict[tuple[int, str, str, Any], Calibration],
    *,
    station_id: int,
    model: str,
    target_bucket: str,
    kind: Any,
) -> CalibrationParams:
    calibration = _pick_calibration_row(
        calibrations,
        station_id=station_id,
        model=model,
        target_bucket=target_bucket,
        kind=kind,
    )
    if calibration is None:
        return CalibrationParams(
            bias_c=0.0,
            sigma_c=2.0,
            intercept=0.0,
            slope=1.0,
            sigma_intercept=2.0,
            sigma_slope=0.0,
            source_bucket="default",
        )
    return _calibration_params(calibration)


def _pick_calibration_row(
    calibrations: dict[tuple[int, str, str, Any], Calibration],
    *,
    station_id: int,
    model: str,
    target_bucket: str,
    kind: Any,
) -> Calibration | None:
    target_index = BUCKET_ORDER.index(target_bucket)
    candidates = sorted(
        (
            (abs(BUCKET_ORDER.index(bucket) - target_index), calibration)
            for (
                candidate_station,
                candidate_model,
                bucket,
                candidate_kind,
            ), calibration in calibrations.items()
            if candidate_station == station_id
            and candidate_model == model
            and candidate_kind == kind
        ),
        key=lambda item: item[0],
    )
    return candidates[0][1] if candidates else None


def _calibration_params(calibration: Calibration) -> CalibrationParams:
    return CalibrationParams(
        bias_c=float(calibration.bias_c),
        sigma_c=float(calibration.sigma_c),
        intercept=float(calibration.emos_a),
        slope=float(calibration.emos_b),
        sigma_intercept=float(calibration.emos_c),
        sigma_slope=float(calibration.emos_d),
        source_bucket=calibration.horizon_bucket,
    )


def _hierarchical_calibration(
    calibrations: dict[tuple[int, str, str, Any], Calibration],
    *,
    station_id: int,
    model: str,
    target_bucket: str,
    kind: Any,
    prior_samples: int,
    minimum_peer_stations: int,
) -> HierarchicalCalibration | None:
    local_row = _pick_calibration_row(
        calibrations,
        station_id=station_id,
        model=model,
        target_bucket=target_bucket,
        kind=kind,
    )
    if local_row is None:
        return None
    peers = [
        CalibrationPeer(
            station_id=candidate_station,
            params=_calibration_params(row),
            sample_n=row.sample_n,
        )
        for (
            candidate_station,
            candidate_model,
            bucket,
            candidate_kind,
        ), row in calibrations.items()
        if candidate_station != station_id
        and candidate_model == model
        and candidate_kind == kind
        and bucket == local_row.horizon_bucket
    ]
    return partial_pool_calibration(
        CalibrationPeer(
            station_id=station_id,
            params=_calibration_params(local_row),
            sample_n=local_row.sample_n,
        ),
        peers,
        prior_samples=prior_samples,
        minimum_peer_stations=minimum_peer_stations,
    )


def _seasonal_values(
    actuals: Collection[tuple[date, float]], target_date: date, window_days: int = 15
) -> list[float]:
    target_day = target_date.timetuple().tm_yday
    values = []
    for actual_date, actual_temp_c in actuals:
        actual_day = actual_date.timetuple().tm_yday
        distance = abs(target_day - actual_day)
        if min(distance, 366 - distance) <= window_days:
            values.append(actual_temp_c)
    return values


def _daily_actual_training_statement(
    *,
    station_ids: Collection[int],
    earliest_date: date,
    truth_cutoff: datetime,
) -> Select[tuple[DailyActual]]:
    """Select only point-in-time visible, undisputed METAR fallback truth."""

    return select(DailyActual).where(
        DailyActual.station_id.in_(station_ids),
        DailyActual.date >= earliest_date,
        or_(
            DailyActual.first_seen_at.is_(None),
            DailyActual.first_seen_at <= truth_cutoff,
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
            as_of=truth_cutoff,
        ),
    )


def _merge_training_actuals(
    *,
    fallback_rows: Collection[tuple[int, date, WeatherKind, float | Decimal]],
    exact_rows: Collection[tuple[int, date, WeatherKind, float | Decimal]],
) -> tuple[
    dict[tuple[int, date, WeatherKind], float],
    dict[tuple[int, WeatherKind], list[tuple[date, float]]],
]:
    """Build one exact-first truth value per cell for residuals and climate."""

    actual_lookup = {
        (station_id, actual_date, kind): float(value_c)
        for station_id, actual_date, kind, value_c in fallback_rows
    }
    actual_lookup.update(
        {
            (station_id, actual_date, kind): float(value_c)
            for station_id, actual_date, kind, value_c in exact_rows
        }
    )
    actuals_by_station_kind: dict[
        tuple[int, WeatherKind], list[tuple[date, float]]
    ] = defaultdict(list)
    for (station_id, actual_date, kind), actual_c in actual_lookup.items():
        actuals_by_station_kind[(station_id, kind)].append((actual_date, actual_c))
    return actual_lookup, actuals_by_station_kind


def _component_json(component: ProbabilityComponent) -> dict[str, Any]:
    return {
        "probability": component.probability,
        "mean_c": component.mean_c,
        "sigma_c": component.sigma_c,
        "weight": component.weight,
        "is_ensemble": component.is_ensemble,
        "sample_n": component.sample_n,
        "calibration_bucket": component.calibration_bucket,
        "outlier": component.outlier,
    }


def _materialize_event_probability_coherence(
    values: list[ModelProbabilityValue],
    event_markets: list[Market],
    *,
    eligible_market_ids: set[int],
    latest_prices: Mapping[int, tuple[ClobMarketSnapshot, datetime]],
) -> EventProbabilityMaterialization:
    """Publish one exact, auditable probability vector per complete event.

    The input values are the final per-bucket outputs after every model,
    shrinkage, and promoted settlement-calibration step.  Invalid or incomplete
    event families are annotated but never repaired; downstream coherence
    checks therefore continue to block them.
    """

    value_by_market = {value.market_id: value for value in values}
    if len(value_by_market) != len(values):
        raise RuntimeError("probability cohort contains duplicate market values")

    grouped: dict[str, list[EventBucketProbability]] = defaultdict(list)
    event_id_by_market: dict[int, str] = {}
    for market in event_markets:
        if market.id in event_id_by_market:
            raise RuntimeError(f"event definitions duplicate market {market.id}")
        event_id_by_market[market.id] = market.event_id
        value = value_by_market.get(market.id)
        price_row = latest_prices.get(market.id)
        anchor = price_row[0] if price_row is not None else None
        grouped[market.event_id].append(
            EventBucketProbability(
                market_id=market.id,
                event_id=market.event_id,
                station_id=market.station_id,
                market_date=market.market_date,
                kind=market.kind.value,
                resolves_at=market.resolves_at,
                resolution_source=market.resolution_source,
                bucket_low_c=market.bucket_low_c,
                bucket_high_c=market.bucket_high_c,
                eligible=(
                    market.id in eligible_market_ids
                    and market.status is MarketStatus.ACTIVE
                    and market.station_verified
                ),
                probability=value.prob if value is not None else None,
                probability_ts=value.ts if value is not None else None,
                horizon_h=value.horizon_h if value is not None else None,
                pipeline_cohort=(
                    pipeline_cohort_from_components(value.components)
                    if value is not None
                    else None
                ),
                anchor_snapshot_id=(
                    value.market_snapshot_id if value is not None else None
                ),
                anchor_market_id=anchor.market_id if anchor is not None else None,
                anchor_batch_id=anchor.batch_id if anchor is not None else None,
                anchor_eligible=bool(
                    value is not None
                    and value.market_anchor_protocol == CLOB_DUAL_PROTOCOL
                    and anchor is not None
                    and anchor.id == value.market_snapshot_id
                    and anchor.market_id == market.id
                    and anchor.eligible
                ),
            )
        )

    materialization_assessments = {
        event_id: assess_event_probability_materialization(rows)
        for event_id, rows in grouped.items()
    }
    trade_assessments = {
        event_id: assess_event_probability_coherence(rows)
        for event_id, rows in grouped.items()
    }
    result: list[ModelProbabilityValue] = []
    for value in values:
        event_id = event_id_by_market.get(value.market_id)
        if (
            event_id is None
            or event_id not in materialization_assessments
            or event_id not in trade_assessments
        ):
            raise RuntimeError(
                f"probability market {value.market_id} lacks an event definition"
            )
        materialization = materialization_assessments[event_id]
        trade_assessment = trade_assessments[event_id]
        final_probability = value.prob
        if materialization.passed:
            coherent = materialization.probability_by_market.get(value.market_id)
            if coherent is None:
                raise RuntimeError(
                    f"passed event {event_id!r} lacks market {value.market_id}"
                )
            final_probability = coherent
        components = dict(value.components)
        components["event_coherence"] = {
            "protocol": EVENT_PROBABILITY_COHERENCE_PROTOCOL,
            "event_id": trade_assessment.event_id,
            # ``materialized`` proves the model probability vector. ``passed``
            # additionally proves executable market context and is the only
            # state accepted by signal generation.  Accuracy sealing consumes
            # the former and deliberately does not depend on CLOB availability.
            "materialized": materialization.passed,
            "materialization_reason": (
                materialization.reason.value
                if materialization.reason is not None
                else None
            ),
            "passed": trade_assessment.passed,
            "reason": (
                trade_assessment.reason.value
                if trade_assessment.reason is not None
                else None
            ),
            "detail": trade_assessment.detail,
            "bucket_count": materialization.bucket_count,
            "probability_count": materialization.probability_count,
            "raw_probability": str(value.prob),
            "final_probability": str(final_probability),
            "event_total_before": (
                str(materialization.probability_total_before)
                if materialization.probability_total_before is not None
                else None
            ),
            "event_total_final": "1.000000" if materialization.passed else None,
            "normalization_scale": (
                str(materialization.normalization_scale)
                if materialization.normalization_scale is not None
                else None
            ),
            "applied": materialization.normalized,
            "distribution_sha256": (
                event_probability_distribution_sha256(
                    materialization.probability_by_market
                )
                if materialization.passed
                else None
            ),
            "probability_ts": (
                materialization.probability_ts.isoformat()
                if materialization.probability_ts is not None
                else None
            ),
            "pipeline_cohort": materialization.pipeline_cohort,
            "anchor_batch_id": trade_assessment.anchor_batch_id,
        }
        result.append(
            replace(
                value,
                prob=final_probability,
                components=components,
            )
        )
    return EventProbabilityMaterialization(
        values=tuple(result),
        valid_event_ids=frozenset(
            event_id
            for event_id, assessment in trade_assessments.items()
            if assessment.passed
        ),
        blocked_event_ids=frozenset(
            event_id
            for event_id, assessment in trade_assessments.items()
            if not assessment.passed
        ),
    )


async def recompute_probs(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    include_shadow: bool = False,
    seal_accuracy_evidence: bool = False,
    now: datetime | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    pipeline_fingerprint = probability_pipeline_fingerprint(settings)
    pipeline_base_cohort = probability_pipeline_base_cohort(
        settings,
        fingerprint=pipeline_fingerprint,
    )
    # Preserve sub-second ordering.  A freshly committed CLOB witness carries
    # a PostgreSQL-attested ``first_seen_at``; flooring this decision to the
    # start of the second can make an earlier-looking ModelProb that the
    # provenance trigger must (correctly) reject.
    calculation_time = now
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            actual_cutoff = await session.scalar(select(func.clock_timestamp()))
            if actual_cutoff is None:
                raise RuntimeError("database did not return a daily-actual training cutoff")
            truth_cutoff = min(actual_cutoff, calculation_time)
            promotion_manifest = await load_effective_accuracy_promotion_manifest(
                session,
                pipeline_fingerprint=pipeline_fingerprint,
                current_pipeline_base_cohort=pipeline_base_cohort,
            )
            promotions = _promotion_lookup(promotion_manifest)
            # SHADOW coverage must be an explicit scheduling decision.  Keying
            # it to ``minute == 0`` loses the cohort whenever an upstream job
            # crosses the minute boundary, which in turn makes the later
            # challenger freshness gate permanently exclude those stations.
            stages = (
                (StationStage.PAPER, StationStage.APPROVED, StationStage.SHADOW)
                if include_shadow
                else (StationStage.PAPER, StationStage.APPROVED)
            )
            market_rows = (
                await session.execute(
                    select(Market, Station)
                    .join(Station, Market.station_id == Station.id)
                    .where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                        Station.active.is_(True),
                        Station.stage.in_(stages),
                    )
                )
            ).all()
            if not market_rows:
                return 0, 0
            evidence_event_ids = {market.event_id for market, _station in market_rows}
            # The prospective sealer must see every bucket belonging to each
            # candidate event, including blocked/unverified siblings that the
            # production probability loop correctly refuses to price.
            evidence_markets = (
                (
                    await session.execute(
                        select(Market).where(Market.event_id.in_(evidence_event_ids))
                    )
                )
                .scalars()
                .all()
            )
            station_ids = {station.id for _, station in market_rows}
            valid_dates = {market.market_date for market, _ in market_rows}
            forecasts = (
                (
                    await session.execute(
                        select(Forecast).where(
                            Forecast.station_id.in_(station_ids),
                            Forecast.valid_date.in_(valid_dates),
                            Forecast.available_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            trajectory_rows = (
                (
                    await session.execute(
                        select(ForecastTrajectory).where(
                            ForecastTrajectory.station_id.in_(station_ids),
                            ForecastTrajectory.valid_date.in_(valid_dates),
                            ForecastTrajectory.available_at <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            ingest_ids = {
                row.ingest_id for row in forecasts if row.ingest_id is not None
            }
            ingest_ids.update(
                row.ingest_id
                for row in trajectory_rows
                if row.ingest_id is not None
            )
            ingest_identities = {
                ingest_id: (manifest_hash, record_hash)
                for ingest_id, manifest_hash, record_hash in (
                    await session.execute(
                        select(
                            ForecastIngest.id,
                            ForecastIngest.normalized_manifest_sha256,
                            ForecastIngest.record_hash,
                        ).where(ForecastIngest.id.in_(ingest_ids))
                    )
                ).all()
            }
            calibration_rows = (
                await session.execute(
                    select(Calibration).where(Calibration.updated_at <= truth_cutoff)
                )
            ).scalars().all()
            earliest_actual_date = min(valid_dates) - timedelta(days=366 * 4)
            actual_rows = (
                (
                    await session.execute(
                        _daily_actual_training_statement(
                            station_ids=station_ids,
                            earliest_date=earliest_actual_date,
                            truth_cutoff=truth_cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            historical_rows = (
                (
                    await session.execute(
                        select(HistoricalForecast).where(
                            HistoricalForecast.station_id.in_(station_ids),
                            HistoricalForecast.valid_date
                            >= now.date() - timedelta(days=settings.calibration_window_days),
                            HistoricalForecast.fetched_at <= truth_cutoff,
                            or_(
                                HistoricalForecast.first_seen_at.is_(None),
                                HistoricalForecast.first_seen_at <= truth_cutoff,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            observation_rows = (
                (
                    await session.execute(
                        select(Observation).where(
                            Observation.station_id.in_(station_ids),
                            Observation.obs_time >= now - timedelta(hours=36),
                            Observation.obs_time <= now,
                        )
                    )
                )
                .scalars()
                .all()
            )
            canonical_actuals = canonical_settlement_actuals(as_of=truth_cutoff)
            settlement_actual_rows = (
                await session.execute(
                    select(
                        canonical_actuals.c.station_id,
                        canonical_actuals.c.date,
                        canonical_actuals.c.kind,
                        canonical_actuals.c.value_c,
                    ).where(
                        canonical_actuals.c.station_id.in_(station_ids),
                        canonical_actuals.c.date >= earliest_actual_date,
                    )
                )
            ).all()
            settlement_calibrator = await session.scalar(
                select(SettlementProbabilityCalibrator).where(
                    SettlementProbabilityCalibrator.scope_key == "global:all:all",
                    SettlementProbabilityCalibrator.updated_at <= truth_cutoff,
                    SettlementProbabilityCalibrator.trained_through <= truth_cutoff,
                )
            )
            expert_skill_rows = (
                (
                    await session.execute(
                        select(ModelSkillReport).where(
                            ModelSkillReport.window_days == 60,
                            ModelSkillReport.evaluated_at >= now - timedelta(days=2),
                            ModelSkillReport.evaluated_at <= truth_cutoff,
                        )
                    )
                )
                .scalars()
                .all()
            )
            regime_drift_rows = (
                (
                    await session.execute(
                        select(ModelDriftEvent).where(
                            ModelDriftEvent.metric == "residual_bias_shift",
                            ModelDriftEvent.detected_at >= now - timedelta(days=2),
                            ModelDriftEvent.detected_at <= truth_cutoff,
                            ModelDriftEvent.station_id.in_(station_ids),
                        )
                    )
                )
                .scalars()
                .all()
            )
            latest_prices: dict[int, tuple[ClobMarketSnapshot, datetime]] = {
                snapshot.market_id: (snapshot, first_seen_at)
                for snapshot, first_seen_at in (
                    await session.execute(
                        select(ClobMarketSnapshot, ClobBookBatch.first_seen_at)
                        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
                        .where(
                            ClobMarketSnapshot.market_id.in_(
                                [market.id for market, _ in market_rows]
                            ),
                            ClobMarketSnapshot.eligible.is_(True),
                            ClobMarketSnapshot.binary_yes_probability.is_not(None),
                        )
                        .distinct(ClobMarketSnapshot.market_id)
                        .order_by(
                            ClobMarketSnapshot.market_id,
                            ClobBookBatch.first_seen_at.desc(),
                        )
                    )
                ).all()
            }
            resolved_probability_rows = (
                await session.execute(
                    select(
                        Market.id,
                        Market.station_id,
                        Market.kind,
                        Market.resolved_outcome,
                        ModelProb.components,
                    )
                    .join(ModelProb, ModelProb.market_id == Market.id)
                    .where(
                        Market.resolved_outcome.is_not(None),
                        Market.station_id.in_(station_ids),
                        Market.resolves_at >= now - timedelta(days=180),
                        ModelProb.ts <= Market.resolves_at,
                    )
                    .distinct(Market.id)
                    .order_by(Market.id, ModelProb.ts.desc())
                )
            ).all()

        latest: dict[tuple[int, str, date, WeatherKind], Forecast] = {}
        for forecast in forecasts:
            forecast_key = (
                forecast.station_id,
                forecast.model,
                forecast.valid_date,
                forecast.kind,
            )
            if (
                forecast_key not in latest
                or forecast.run_time > latest[forecast_key].run_time
            ):
                latest[forecast_key] = forecast
        latest_trajectories: dict[tuple[int, str, date], ForecastTrajectory] = {}
        for trajectory in trajectory_rows:
            trajectory_key = (
                trajectory.station_id,
                canonical_model(trajectory.model),
                trajectory.valid_date,
            )
            current_trajectory = latest_trajectories.get(trajectory_key)
            if (
                current_trajectory is None
                or trajectory.run_time > current_trajectory.run_time
            ):
                latest_trajectories[trajectory_key] = trajectory
        trajectory_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        stack_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        nowcast_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        conformal_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        hierarchical_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        regime_skill: dict[tuple[int, str, WeatherKind], ModelSkillReport] = {}
        nonlinear_skill: dict[tuple[str, int, str, WeatherKind], ModelSkillReport] = {}
        for report in expert_skill_rows:
            expert_name = report.details.get("expert")
            if expert_name not in {
                STACK_EXPERT,
                "trajectory_ar1",
                "trajectory_nowcast",
                "conformal_gaussian",
                "hierarchical_emos",
                "regime_emos",
                "qrf",
                "analog",
                "mixture",
            }:
                continue
            try:
                report_kind = WeatherKind(report.kind)
            except ValueError:
                continue
            skill_key = (report.station_id, report.horizon_bucket, report_kind)
            if expert_name in {"qrf", "analog", "mixture"}:
                nonlinear_key = (expert_name, *skill_key)
                current_skill = nonlinear_skill.get(nonlinear_key)
                if current_skill is None or report.evaluated_at > current_skill.evaluated_at:
                    nonlinear_skill[nonlinear_key] = report
                continue
            if expert_name == STACK_EXPERT:
                skill_map = stack_skill
            elif expert_name == "trajectory_ar1":
                skill_map = trajectory_skill
            elif expert_name == "trajectory_nowcast":
                skill_map = nowcast_skill
            elif expert_name == "hierarchical_emos":
                skill_map = hierarchical_skill
            elif expert_name == "regime_emos":
                skill_map = regime_skill
            else:
                skill_map = conformal_skill
            current_skill = skill_map.get(skill_key)
            if current_skill is None or report.evaluated_at > current_skill.evaluated_at:
                skill_map[skill_key] = report
        regime_adjustments: dict[
            tuple[int, str, str, WeatherKind], ModelDriftEvent
        ] = {}
        for drift in regime_drift_rows:
            model = drift.details.get("model")
            bucket = drift.details.get("horizon_bucket")
            kind_value = drift.details.get("kind")
            adjustment = drift.details.get("adjustment_c")
            if (
                drift.station_id is None
                or not isinstance(model, str)
                or not isinstance(bucket, str)
                or not isinstance(kind_value, str)
                or not isinstance(adjustment, int | float)
            ):
                continue
            try:
                drift_kind = WeatherKind(kind_value)
            except ValueError:
                continue
            drift_key = (drift.station_id, model, bucket, drift_kind)
            current_drift = regime_adjustments.get(drift_key)
            if current_drift is None or drift.detected_at > current_drift.detected_at:
                regime_adjustments[drift_key] = drift
        calibrations = {
            (
                row.station_id,
                row.model,
                row.horizon_bucket,
                row.kind,
            ): row
            for row in calibration_rows
        }
        actual_lookup, actuals_by_station_kind = _merge_training_actuals(
            fallback_rows=[
                (actual.station_id, actual.date, actual.kind, actual.temp_c)
                for actual in actual_rows
            ],
            exact_rows=[
                (actual.station_id, actual.date, actual.kind, actual.value_c)
                for actual in settlement_actual_rows
            ],
        )
        residual_rows: dict[tuple[int, str, str, WeatherKind], list[tuple[date, float]]] = (
            defaultdict(list)
        )
        for historical_forecast in historical_rows:
            actual_c = actual_lookup.get(
                (
                    historical_forecast.station_id,
                    historical_forecast.valid_date,
                    historical_forecast.kind,
                )
            )
            if actual_c is None:
                continue
            residual_rows[
                (
                    historical_forecast.station_id,
                    historical_forecast.model,
                    horizon_bucket(historical_forecast.horizon_h),
                    historical_forecast.kind,
                )
            ].append(
                (
                    historical_forecast.valid_date,
                    actual_c - float(historical_forecast.temp_c),
                )
            )
        ar_models: dict[tuple[int, str, str, WeatherKind], tuple[AR1Fit, float]] = {}
        for residual_key, dated_residuals in residual_rows.items():
            ordered = [value for _, value in sorted(dated_residuals)]
            fit = fit_ar1(ordered)
            if fit is not None:
                ar_models[residual_key] = (fit, ordered[-1])

        conformal_scores: dict[tuple[int, str, WeatherKind], list[float]] = defaultdict(list)
        for historical_forecast in historical_rows:
            if historical_forecast.fetched_at > now:
                continue
            model = canonical_model(historical_forecast.model)
            if model not in {"ecmwf", "gfs", "icon", "gem"}:
                continue
            actual_c = actual_lookup.get(
                (
                    historical_forecast.station_id,
                    historical_forecast.valid_date,
                    historical_forecast.kind,
                )
            )
            if actual_c is None:
                continue
            calibration_bucket = horizon_bucket(historical_forecast.horizon_h)
            calibration = _pick_calibration(
                calibrations,
                station_id=historical_forecast.station_id,
                model=model,
                target_bucket=calibration_bucket,
                kind=historical_forecast.kind,
            )
            corrected_mean = calibration.intercept + calibration.slope * float(
                historical_forecast.temp_c
            )
            predicted_sigma = max(
                0.5,
                calibration.sigma_intercept
                + calibration.sigma_slope * historical_forecast.horizon_h,
            )
            conformal_scores[
                (
                    historical_forecast.station_id,
                    calibration_bucket,
                    historical_forecast.kind,
                )
            ].append(abs(actual_c - corrected_mean) / predicted_sigma)
        conformal_scales: dict[tuple[int, str, WeatherKind], ConformalScale] = {}
        for conformal_key, scores in conformal_scores.items():
            fitted_scale = fit_conformal_scale(
                scores,
                coverage=settings.accuracy_conformal_coverage,
                minimum_samples=settings.accuracy_conformal_min_samples,
                maximum_scale=settings.accuracy_conformal_max_scale,
            )
            if fitted_scale is not None:
                conformal_scales[conformal_key] = fitted_scale

        challenger_case_models: dict[tuple[int, str, WeatherKind, date], dict[str, float]] = (
            defaultdict(dict)
        )
        for historical_forecast in historical_rows:
            if historical_forecast.fetched_at > now:
                continue
            model = canonical_model(historical_forecast.model)
            if model not in {"ecmwf", "gfs", "icon", "gem"}:
                continue
            challenger_case_models[
                (
                    historical_forecast.station_id,
                    horizon_bucket(historical_forecast.horizon_h),
                    historical_forecast.kind,
                    historical_forecast.valid_date,
                )
            ][model] = float(historical_forecast.temp_c)

        stations_by_id = {station.id: station for _, station in market_rows}
        observed_extremes: dict[tuple[int, date, WeatherKind], float] = {}
        observed_hour_candidates: dict[tuple[int, date, str], tuple[datetime, float]] = {}
        for observation in observation_rows:
            station = stations_by_id.get(observation.station_id)
            if station is None:
                continue
            local_date = observation.obs_time.astimezone(ZoneInfo(station.tz)).date()
            temperature = float(observation.temp_c)
            local_time = observation.obs_time.astimezone(ZoneInfo(station.tz))
            local_hour = local_time.strftime("%Y-%m-%dT%H:00")
            observed_hour_key = (station.id, local_date, local_hour)
            existing_hour = observed_hour_candidates.get(observed_hour_key)
            if existing_hour is None or observation.obs_time > existing_hour[0]:
                observed_hour_candidates[observed_hour_key] = (
                    observation.obs_time,
                    temperature,
                )
            high_key = (station.id, local_date, WeatherKind.HIGH)
            low_key = (station.id, local_date, WeatherKind.LOW)
            observed_extremes[high_key] = max(
                observed_extremes.get(high_key, temperature), temperature
            )
            observed_extremes[low_key] = min(
                observed_extremes.get(low_key, temperature), temperature
            )
        observed_hours: dict[tuple[int, date], dict[str, float]] = defaultdict(dict)
        for (station_id, local_date, local_hour), (_obs_time, temperature) in (
            observed_hour_candidates.items()
        ):
            observed_hours[(station_id, local_date)][local_hour] = temperature
        component_scores = component_brier_scores(
            [
                (station_id, bool(outcome), details)
                for _market_id, station_id, _kind, outcome, details in resolved_probability_rows
                if station_id is not None and isinstance(details, dict)
            ]
        )
        stack_cases: dict[StackKey, list[StackCase]] = defaultdict(list)
        for _market_id, station_id, resolved_kind, outcome, details in resolved_probability_rows:
            if station_id is None or not isinstance(details, dict):
                continue
            blend = details.get("blend")
            if not isinstance(blend, dict):
                continue
            target = blend.get("target_calibration_bucket")
            if not isinstance(target, str):
                continue
            expert_row: dict[str, float] = {}
            for name, detail in details.items():
                if name in {"blend", "shrinkage", "intraday", "stacking"}:
                    continue
                if isinstance(detail, dict) and isinstance(detail.get("probability"), int | float):
                    expert_row[name] = float(detail["probability"])
            shrinkage = details.get("shrinkage")
            if isinstance(shrinkage, dict) and isinstance(shrinkage.get("p_market"), int | float):
                expert_row["market"] = float(shrinkage["p_market"])
            kind_raw = blend.get("kind")
            if isinstance(kind_raw, str):
                try:
                    market_kind = WeatherKind(kind_raw)
                except ValueError:
                    market_kind = resolved_kind
            else:
                market_kind = resolved_kind
            if market_kind is not None and len(expert_row) >= 2:
                stack_cases[(station_id, target, market_kind)].append((expert_row, bool(outcome)))
        target_stack_scopes = {
            (
                station.id,
                horizon_bucket(
                    math.ceil(max(0.0, (market.resolves_at - now).total_seconds() / 3600))
                ),
                market.kind,
            )
            for market, station in market_rows
        }
        stack_fits = _fit_stack_candidates(
            stack_cases,
            target_stack_scopes,
            minimum_samples=settings.accuracy_stack_min_samples,
            minimum_peer_stations=settings.accuracy_hierarchical_min_peer_stations,
            l2=settings.accuracy_stack_l2,
            learning_rate=settings.accuracy_stack_learning_rate,
        )
        pipeline_metadata = probability_pipeline_metadata(
            settings,
            activation_manifest_hash=promotion_manifest.manifest_hash,
        )

        values: list[ModelProbabilityValue] = []
        accuracy_values: list[AccuracyPredictionValue] = []
        challenger_cache: dict[
            tuple[int, date, WeatherKind, str],
            tuple[
                tuple[float, ...],
                AnalogForecast | None,
                GaussianMixture | None,
                int,
            ],
        ] = {}
        trajectory_cache: dict[
            tuple[int, date, WeatherKind, str],
            tuple[tuple[float, ...], dict[str, Any]],
        ] = {}
        nowcast_cache: dict[
            tuple[int, date, WeatherKind, str],
            tuple[tuple[float, ...], dict[str, Any]],
        ] = {}
        hourly_curve_cache: dict[
            tuple[int, date], tuple[list[HourlyCurve], list[str]]
        ] = {}
        skipped = 0
        for market, station in market_rows:
            if market.bucket_low_c is None and market.bucket_high_c is None:
                skipped += 1
                continue
            bucket = Bucket(
                low_c=float(market.bucket_low_c) if market.bucket_low_c is not None else None,
                high_c=(float(market.bucket_high_c) if market.bucket_high_c is not None else None),
            )
            horizon_h = max(0.0, (market.resolves_at - now).total_seconds() / 3600)
            target_bucket = horizon_bucket(math.ceil(horizon_h))
            components: list[ProbabilityComponent] = []
            hierarchical_components: list[ProbabilityComponent] = []
            hierarchical_fits: list[HierarchicalCalibration] = []

            ecmwf_member_rows = [
                latest_forecast
                for forecast_key, latest_forecast in latest.items()
                if forecast_key[0] == station.id
                and forecast_key[1].startswith("ecmwf_ens_m")
                and forecast_key[2] == market.market_date
                and forecast_key[3] == market.kind
            ]
            ecmwf_members = [float(row.temp_c) for row in ecmwf_member_rows]
            ecmwf_cal = _pick_calibration(
                calibrations,
                station_id=station.id,
                model="ecmwf",
                target_bucket=target_bucket,
                kind=market.kind,
            )
            ecmwf_hierarchical = _hierarchical_calibration(
                calibrations,
                station_id=station.id,
                model="ecmwf",
                target_bucket=target_bucket,
                kind=market.kind,
                prior_samples=settings.accuracy_hierarchical_prior_samples,
                minimum_peer_stations=settings.accuracy_hierarchical_min_peer_stations,
            )
            if len(ecmwf_members) >= 2:
                shift = _ar_shift(ar_models, station.id, "ecmwf", target_bucket, market.kind)
                shifted_members = [value + shift for value in ecmwf_members]
                components.append(
                    ensemble_component(
                        name="ecmwf_ens",
                        member_values_c=shifted_members,
                        calibration=ecmwf_cal,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                    )
                )
                if ecmwf_hierarchical is not None:
                    hierarchical_components.append(
                        ensemble_component(
                            name="ecmwf_ens",
                            member_values_c=shifted_members,
                            calibration=ecmwf_hierarchical.params,
                            bucket=bucket,
                            weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                        )
                    )
                    hierarchical_fits.append(ecmwf_hierarchical)
            else:
                raw = latest.get((station.id, "ecmwf", market.market_date, market.kind))
                if raw:
                    shift = _ar_shift(ar_models, station.id, "ecmwf", target_bucket, market.kind)
                    components.append(
                        deterministic_component(
                            name="ecmwf_ens",
                            raw_temp_c=float(raw.temp_c) + shift,
                            calibration=ecmwf_cal,
                            horizon_h=horizon_h,
                            bucket=bucket,
                            weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                        )
                    )
                    if ecmwf_hierarchical is not None:
                        hierarchical_components.append(
                            deterministic_component(
                                name="ecmwf_ens",
                                raw_temp_c=float(raw.temp_c) + shift,
                                calibration=ecmwf_hierarchical.params,
                                horizon_h=horizon_h,
                                bucket=bucket,
                                weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                            )
                        )
                        hierarchical_fits.append(ecmwf_hierarchical)

            gefs_member_rows = [
                latest_forecast
                for forecast_key, latest_forecast in latest.items()
                if forecast_key[0] == station.id
                and forecast_key[1].startswith("gefs_m")
                and forecast_key[2] == market.market_date
                and forecast_key[3] == market.kind
            ]
            gefs_members = [float(row.temp_c) for row in gefs_member_rows]
            gfs_cal = _pick_calibration(
                calibrations,
                station_id=station.id,
                model="gfs",
                target_bucket=target_bucket,
                kind=market.kind,
            )
            gfs_hierarchical = _hierarchical_calibration(
                calibrations,
                station_id=station.id,
                model="gfs",
                target_bucket=target_bucket,
                kind=market.kind,
                prior_samples=settings.accuracy_hierarchical_prior_samples,
                minimum_peer_stations=settings.accuracy_hierarchical_min_peer_stations,
            )
            if len(gefs_members) >= 2:
                shift = _ar_shift(ar_models, station.id, "gfs", target_bucket, market.kind)
                shifted_members = [value + shift for value in gefs_members]
                components.append(
                    ensemble_component(
                        name="gefs",
                        member_values_c=shifted_members,
                        calibration=gfs_cal,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS["gefs"],
                    )
                )
                if gfs_hierarchical is not None:
                    hierarchical_components.append(
                        ensemble_component(
                            name="gefs",
                            member_values_c=shifted_members,
                            calibration=gfs_hierarchical.params,
                            bucket=bucket,
                            weight=DEFAULT_WEIGHTS["gefs"],
                        )
                    )
                    hierarchical_fits.append(gfs_hierarchical)

            deterministic_models = ["icon", "gfs"]
            anchor_model = (
                "nws" if (station.id, "nws", market.market_date, market.kind) in latest else "gem"
            )
            deterministic_models.append(anchor_model)
            for model in deterministic_models:
                raw = latest.get((station.id, model, market.market_date, market.kind))
                if raw is None:
                    continue
                calibration = _pick_calibration(
                    calibrations,
                    station_id=station.id,
                    model=model,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                hierarchical_calibration = _hierarchical_calibration(
                    calibrations,
                    station_id=station.id,
                    model=model,
                    target_bucket=target_bucket,
                    kind=market.kind,
                    prior_samples=settings.accuracy_hierarchical_prior_samples,
                    minimum_peer_stations=settings.accuracy_hierarchical_min_peer_stations,
                )
                shift = _ar_shift(ar_models, station.id, model, target_bucket, market.kind)
                components.append(
                    deterministic_component(
                        name=model,
                        raw_temp_c=float(raw.temp_c) + shift,
                        calibration=calibration,
                        horizon_h=horizon_h,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS[model],
                    )
                )
                if hierarchical_calibration is not None:
                    hierarchical_components.append(
                        deterministic_component(
                            name=model,
                            raw_temp_c=float(raw.temp_c) + shift,
                            calibration=hierarchical_calibration.params,
                            horizon_h=horizon_h,
                            bucket=bucket,
                            weight=DEFAULT_WEIGHTS[model],
                        )
                    )
                    hierarchical_fits.append(hierarchical_calibration)
            if not components:
                skipped += 1
                continue

            current_models: dict[str, float] = {}
            if ecmwf_members:
                current_models["ecmwf"] = sum(ecmwf_members) / len(ecmwf_members)
            else:
                ecmwf_raw = latest.get((station.id, "ecmwf", market.market_date, market.kind))
                if ecmwf_raw is not None:
                    current_models["ecmwf"] = float(ecmwf_raw.temp_c)
            if gefs_members:
                current_models["gfs"] = sum(gefs_members) / len(gefs_members)
            else:
                gfs_raw = latest.get((station.id, "gfs", market.market_date, market.kind))
                if gfs_raw is not None:
                    current_models["gfs"] = float(gfs_raw.temp_c)
            for model in ("icon", "gem"):
                raw = latest.get((station.id, model, market.market_date, market.kind))
                if raw is not None:
                    current_models[model] = float(raw.temp_c)

            cache_key = (station.id, market.market_date, market.kind, target_bucket)
            if cache_key not in challenger_cache and len(current_models) >= 2:
                model_names = tuple(sorted(current_models))
                training_features: list[tuple[float, ...]] = []
                training_targets: list[float] = []
                analog_cases: list[AnalogCase] = []
                residuals: list[float] = []
                for (
                    case_station,
                    case_bucket,
                    case_kind,
                    case_date,
                ), case_models in challenger_case_models.items():
                    if (
                        case_station != station.id
                        or case_bucket != target_bucket
                        or case_kind != market.kind
                        or case_date >= market.market_date
                        or any(model not in case_models for model in model_names)
                    ):
                        continue
                    actual_c = actual_lookup.get((station.id, case_date, market.kind))
                    if actual_c is None:
                        continue
                    angle = 2 * math.pi * case_date.timetuple().tm_yday / 365.25
                    features = tuple(case_models[model] for model in model_names) + (
                        math.sin(angle),
                        math.cos(angle),
                    )
                    training_features.append(features)
                    training_targets.append(actual_c)
                    analog_cases.append(
                        AnalogCase(
                            features=features,
                            actual_c=actual_c,
                            age_days=max(0, (market.market_date - case_date).days),
                        )
                    )
                    residuals.append(
                        actual_c
                        - sum(case_models[model] for model in model_names) / len(model_names)
                    )
                if len(training_targets) >= settings.accuracy_challenger_min_samples:
                    current_angle = 2 * math.pi * market.market_date.timetuple().tm_yday / 365.25
                    current_features = tuple(current_models[model] for model in model_names) + (
                        math.sin(current_angle),
                        math.cos(current_angle),
                    )
                    qrf = QuantileRegressionForest(
                        trees=settings.accuracy_qrf_trees,
                        min_leaf=max(5, len(training_targets) // 30),
                    )
                    qrf.fit(training_features, training_targets)
                    qrf_values = qrf.distribution(current_features)
                    analog = analog_ensemble(
                        current_features,
                        analog_cases,
                        neighbors=min(30, len(analog_cases)),
                    )
                    residual_mixture = fit_two_gaussian_mixture(residuals)
                    current_mean = sum(current_models.values()) / len(current_models)
                    mixture = (
                        GaussianMixture(
                            weights=residual_mixture.weights,
                            means=(
                                current_mean + residual_mixture.means[0],
                                current_mean + residual_mixture.means[1],
                            ),
                            sigmas=residual_mixture.sigmas,
                            sample_n=residual_mixture.sample_n,
                        )
                        if residual_mixture is not None
                        else None
                    )
                    challenger_cache[cache_key] = (
                        qrf_values,
                        analog,
                        mixture,
                        len(training_targets),
                    )

            market_scores: dict[str, ComponentScore | None] = {
                component.name: component_scores.get((station.id, target_bucket, component.name))
                for component in components
            }
            components, adaptive_weights = apply_adaptive_weights(
                components,
                market_scores,
                min_samples=BRIER_WEIGHT_MIN_SAMPLES,
                k=BRIER_WEIGHT_K,
            )
            adaptive_weight_by_name = {component.name: component.weight for component in components}
            hierarchical_components = [
                replace(
                    component,
                    weight=adaptive_weight_by_name.get(component.name, component.weight),
                )
                for component in hierarchical_components
            ]
            regime_components: list[ProbabilityComponent] = []
            applied_regime_events: list[tuple[str, ModelDriftEvent, float]] = []
            for component in components:
                component_model = (
                    "ecmwf"
                    if component.name == "ecmwf_ens"
                    else "gfs"
                    if component.name == "gefs"
                    else component.name
                )
                component_regime_drift = regime_adjustments.get(
                    (station.id, component_model, target_bucket, market.kind)
                )
                adjustment = (
                    component_regime_drift.details.get("adjustment_c")
                    if component_regime_drift is not None
                    else None
                )
                if component_regime_drift is None or not isinstance(
                    adjustment, int | float
                ):
                    regime_components.append(component)
                    continue
                adjustment_value = float(adjustment)
                regime_components.append(
                    replace(
                        component,
                        probability=apply_regime_shift_to_bucket(
                            probability=component.probability,
                            mean_c=component.mean_c,
                            sigma_c=component.sigma_c,
                            adjustment_c=adjustment_value,
                            bucket=bucket,
                        ),
                        mean_c=component.mean_c + adjustment_value,
                    )
                )
                applied_regime_events.append(
                    (component_model, component_regime_drift, adjustment_value)
                )

            climate_values = _seasonal_values(
                actuals_by_station_kind[(station.id, market.kind)], market.market_date
            )
            result = blend_probabilities(
                components,
                horizon_h=horizon_h,
                climate_values_c=climate_values,
                bucket=bucket,
            )
            hierarchical_result = (
                blend_probabilities(
                    hierarchical_components,
                    horizon_h=horizon_h,
                    climate_values_c=climate_values,
                    bucket=bucket,
                )
                if hierarchical_components
                else None
            )
            regime_result = (
                blend_probabilities(
                    regime_components,
                    horizon_h=horizon_h,
                    climate_values_c=climate_values,
                    bucket=bucket,
                )
                if applied_regime_events
                else None
            )
            component_details: dict[str, object] = {
                "pipeline": pipeline_metadata
            }
            provenance_forecasts: dict[int, Forecast] = {}
            if len(ecmwf_member_rows) >= 2:
                provenance_forecasts.update(
                    {row.id: row for row in ecmwf_member_rows}
                )
            else:
                raw = latest.get(
                    (station.id, "ecmwf", market.market_date, market.kind)
                )
                if raw is not None:
                    provenance_forecasts[raw.id] = raw
            if len(gefs_member_rows) >= 2:
                provenance_forecasts.update({row.id: row for row in gefs_member_rows})
            for model in ("gfs", "icon", "gem", "nws"):
                raw = latest.get((station.id, model, market.market_date, market.kind))
                if raw is not None:
                    provenance_forecasts[raw.id] = raw
            provenance_trajectories = [
                row
                for (row_station_id, model, valid_date), row in latest_trajectories.items()
                if row_station_id == station.id
                and valid_date == market.market_date
                and model in {"ecmwf", "gfs", "icon", "gem"}
                and sum(
                    isinstance(point.get("local_time"), str)
                    and isinstance(point.get("temp_c"), int | float)
                    for point in row.points
                )
                >= 20
            ]
            component_details["forecast_provenance"] = build_model_input_provenance(
                list(provenance_forecasts.values()),
                provenance_trajectories,
                ingest_identities,
            )
            for component in result.components:
                detail = _component_json(component)
                score = market_scores.get(component.name)
                detail.update(
                    {
                        "historical_brier": score.brier if score else None,
                        "historical_brier_n": score.sample_n if score else 0,
                        "weight_source": "adaptive_brier" if adaptive_weights else "default",
                    }
                )
                component_details[component.name] = detail
            component_details["climatology"] = {
                "probability": result.climate_probability,
                "weight": result.climate_weight,
                "sample_n": result.climate_sample_n,
                "source": "iem_3y_window_15d",
            }
            component_details["blend"] = {
                "mean_c": result.mean_c,
                "sigma_c": result.sigma_c,
                "target_calibration_bucket": target_bucket,
                "kind": market.kind.value,
                "weight_method": (
                    f"exp(-{BRIER_WEIGHT_K:g}*brier)" if adaptive_weights else "default"
                ),
                "weight_min_samples": BRIER_WEIGHT_MIN_SAMPLES,
                "ar_emos": {
                    component.name: {
                        "applied": (
                            station.id,
                            (
                                "ecmwf"
                                if component.name == "ecmwf_ens"
                                else "gfs"
                                if component.name == "gefs"
                                else component.name
                            ),
                            target_bucket,
                            market.kind,
                        )
                        in ar_models
                    }
                    for component in result.components
                },
            }

            observed_extreme = observed_extremes.get((station.id, market.market_date, market.kind))
            constrained_probability = constrained_extreme_probability(
                future_mean_c=result.mean_c,
                future_sigma_c=result.sigma_c,
                observed_extreme_c=observed_extreme,
                kind=market.kind,
                bucket=bucket,
            )
            if observed_extreme is not None:
                result = replace(result, probability=constrained_probability)
            component_details["intraday"] = {
                "applied": observed_extreme is not None,
                "observed_extreme_c": observed_extreme,
                "probability_after_constraint": round(constrained_probability, 6),
            }

            challenger_probabilities: dict[str, float] = {}
            if hierarchical_result is not None and hierarchical_fits:
                hierarchical_probability = (
                    constrained_extreme_probability(
                        future_mean_c=hierarchical_result.mean_c,
                        future_sigma_c=hierarchical_result.sigma_c,
                        observed_extreme_c=observed_extreme,
                        kind=market.kind,
                        bucket=bucket,
                    )
                    if observed_extreme is not None
                    else hierarchical_result.probability
                )
                hierarchical_report = hierarchical_skill.get(
                    (station.id, target_bucket, market.kind)
                )
                hierarchical_promotion = _promotion_for(
                    promotions,
                    expert="hierarchical_emos",
                    legacy_expert_version="partial-pooling-v1",
                    station_id=station.id,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                hierarchical_eligible = hierarchical_promotion is not None
                median_fit = sorted(hierarchical_fits, key=lambda fit: fit.local_weight)[
                    len(hierarchical_fits) // 2
                ]
                component_details["hierarchical_emos"] = {
                    "probability": hierarchical_probability,
                    "mean_c": hierarchical_result.mean_c,
                    "sigma_c": hierarchical_result.sigma_c,
                    "local_weight": median_fit.local_weight,
                    "local_sample_n": median_fit.local_sample_n,
                    "peer_sample_n": median_fit.peer_sample_n,
                    "peer_stations": median_fit.peer_stations,
                    "component_count": len(hierarchical_components),
                    "eligible": hierarchical_eligible,
                    "applied": hierarchical_eligible,
                    **_promotion_audit_fields(hierarchical_promotion),
                    "rolling_report_eligible": bool(
                        hierarchical_report and hierarchical_report.eligible
                    ),
                    "skill_blocks": hierarchical_report.sample_n if hierarchical_report else 0,
                    "delta_brier": (
                        float(hierarchical_report.delta_brier)
                        if hierarchical_report and hierarchical_report.delta_brier is not None
                        else None
                    ),
                    "ci_high": (
                        float(hierarchical_report.ci_high)
                        if hierarchical_report and hierarchical_report.ci_high is not None
                        else None
                    ),
                }
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert="hierarchical_emos",
                        expert_version="partial-pooling-v1",
                        probability=Decimal(str(hierarchical_probability)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=Decimal(str(hierarchical_result.mean_c)).quantize(
                            Decimal("0.0001")
                        ),
                        sigma_c=Decimal(str(hierarchical_result.sigma_c)).quantize(
                            Decimal("0.0001")
                        ),
                        training_sample_n=median_fit.local_sample_n + median_fit.peer_sample_n,
                        point_in_time_valid=True,
                        distribution={
                            "horizon_bucket": target_bucket,
                            "local_weight": median_fit.local_weight,
                            "local_sample_n": median_fit.local_sample_n,
                            "peer_sample_n": median_fit.peer_sample_n,
                            "peer_stations": median_fit.peer_stations,
                            "eligible": hierarchical_eligible,
                            **_promotion_audit_fields(hierarchical_promotion),
                        },
                    )
                )
                if hierarchical_eligible:
                    challenger_probabilities["hierarchical_emos"] = hierarchical_probability
            if regime_result is not None and applied_regime_events:
                regime_probability = (
                    constrained_extreme_probability(
                        future_mean_c=regime_result.mean_c,
                        future_sigma_c=regime_result.sigma_c,
                        observed_extreme_c=observed_extreme,
                        kind=market.kind,
                        bucket=bucket,
                    )
                    if observed_extreme is not None
                    else regime_result.probability
                )
                regime_report = regime_skill.get(
                    (station.id, target_bucket, market.kind)
                )
                regime_promotion = _promotion_for(
                    promotions,
                    expert="regime_emos",
                    legacy_expert_version="version-break-v1",
                    station_id=station.id,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                regime_eligible = regime_promotion is not None
                strongest_model, strongest_drift, strongest_adjustment = max(
                    applied_regime_events, key=lambda item: abs(item[2])
                )
                component_details["regime_emos"] = {
                    "probability": regime_probability,
                    "mean_c": regime_result.mean_c,
                    "sigma_c": regime_result.sigma_c,
                    "adjusted_models": len(applied_regime_events),
                    "strongest_model": strongest_model,
                    "strongest_adjustment_c": strongest_adjustment,
                    "model_version": strongest_drift.details.get("model_version"),
                    "boundary_kind": strongest_drift.details.get("boundary_kind"),
                    "boundary_date": strongest_drift.details.get("boundary_date"),
                    "ci_low_c": strongest_drift.details.get("ci_low_c"),
                    "ci_high_c": strongest_drift.details.get("ci_high_c"),
                    "eligible": regime_eligible,
                    "applied": regime_eligible,
                    **_promotion_audit_fields(regime_promotion),
                    "rolling_report_eligible": bool(regime_report and regime_report.eligible),
                    "skill_blocks": regime_report.sample_n if regime_report else 0,
                    "delta_brier": (
                        float(regime_report.delta_brier)
                        if regime_report and regime_report.delta_brier is not None
                        else None
                    ),
                    "ci_high": (
                        float(regime_report.ci_high)
                        if regime_report and regime_report.ci_high is not None
                        else None
                    ),
                }
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert="regime_emos",
                        expert_version="version-break-v1",
                        probability=Decimal(str(regime_probability)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=Decimal(str(regime_result.mean_c)).quantize(
                            Decimal("0.0001")
                        ),
                        sigma_c=Decimal(str(regime_result.sigma_c)).quantize(
                            Decimal("0.0001")
                        ),
                        training_sample_n=sum(
                            int(event.details.get("recent_n", 0))
                            + int(event.details.get("baseline_n", 0))
                            for _model, event, _adjustment in applied_regime_events
                        ),
                        point_in_time_valid=True,
                        distribution={
                            "horizon_bucket": target_bucket,
                            "adjusted_models": len(applied_regime_events),
                            "strongest_model": strongest_model,
                            "strongest_adjustment_c": strongest_adjustment,
                            "model_version": strongest_drift.details.get("model_version"),
                            "boundary_kind": strongest_drift.details.get("boundary_kind"),
                            "boundary_date": strongest_drift.details.get("boundary_date"),
                            "eligible": regime_eligible,
                            **_promotion_audit_fields(regime_promotion),
                        },
                    )
                )
                if regime_eligible:
                    challenger_probabilities["regime_emos"] = regime_probability
            conformal_scale = conformal_scales.get(
                (station.id, target_bucket, market.kind)
            )
            if conformal_scale is not None:
                conformal_sigma = result.sigma_c * conformal_scale.scale
                conformal_probability = (
                    constrained_extreme_probability(
                        future_mean_c=result.mean_c,
                        future_sigma_c=conformal_sigma,
                        observed_extreme_c=observed_extreme,
                        kind=market.kind,
                        bucket=bucket,
                    )
                    if observed_extreme is not None
                    else gaussian_bucket_probability(result.mean_c, conformal_sigma, bucket)
                )
                conformal_report = conformal_skill.get(
                    (station.id, target_bucket, market.kind)
                )
                conformal_promotion = _promotion_for(
                    promotions,
                    expert="conformal_gaussian",
                    legacy_expert_version="split-conformal-v1",
                    station_id=station.id,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                conformal_eligible = conformal_promotion is not None
                component_details["conformal_gaussian"] = {
                    "probability": conformal_probability,
                    "scale": conformal_scale.scale,
                    "quantile": conformal_scale.quantile,
                    "coverage_target": conformal_scale.coverage,
                    "coverage_before": conformal_scale.empirical_coverage_before,
                    "coverage_after": conformal_scale.empirical_coverage_after,
                    "residual_sample_n": conformal_scale.sample_n,
                    "sigma_before": result.sigma_c,
                    "sigma_after": conformal_sigma,
                    "truth_source": (
                        "canonical_agreed_settlement_then_current_reconciliation_clean_"
                        "daily_actual_fallback"
                    ),
                    "eligible": conformal_eligible,
                    "applied": conformal_eligible,
                    **_promotion_audit_fields(conformal_promotion),
                    "rolling_report_eligible": bool(
                        conformal_report and conformal_report.eligible
                    ),
                    "skill_blocks": conformal_report.sample_n if conformal_report else 0,
                    "delta_brier": (
                        float(conformal_report.delta_brier)
                        if conformal_report and conformal_report.delta_brier is not None
                        else None
                    ),
                    "ci_high": (
                        float(conformal_report.ci_high)
                        if conformal_report and conformal_report.ci_high is not None
                        else None
                    ),
                }
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert="conformal_gaussian",
                        expert_version="split-conformal-v1",
                        probability=Decimal(str(conformal_probability)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=Decimal(str(result.mean_c)).quantize(Decimal("0.0001")),
                        sigma_c=Decimal(str(conformal_sigma)).quantize(Decimal("0.0001")),
                        training_sample_n=conformal_scale.sample_n,
                        point_in_time_valid=True,
                        distribution={
                            "horizon_bucket": target_bucket,
                            "scale": conformal_scale.scale,
                            "coverage_target": conformal_scale.coverage,
                            "coverage_before": conformal_scale.empirical_coverage_before,
                            "coverage_after": conformal_scale.empirical_coverage_after,
                            "eligible": conformal_eligible,
                            **_promotion_audit_fields(conformal_promotion),
                        },
                    )
                )
                if conformal_eligible:
                    challenger_probabilities["conformal_gaussian"] = conformal_probability
            trajectory_cache_key = (
                station.id,
                market.market_date,
                market.kind,
                target_bucket,
            )
            hourly_curve_key = (station.id, market.market_date)
            if hourly_curve_key not in hourly_curve_cache:
                trajectory_curves: list[HourlyCurve] = []
                trajectory_run_keys: list[str] = []
                for model in ("ecmwf", "gfs", "icon", "gem"):
                    trajectory_row = latest_trajectories.get(
                        (station.id, model, market.market_date)
                    )
                    if trajectory_row is None:
                        continue
                    points: list[tuple[str, float]] = []
                    for point in trajectory_row.points:
                        local_time_value = point.get("local_time")
                        temp_c = point.get("temp_c")
                        if isinstance(local_time_value, str) and isinstance(
                            temp_c, int | float
                        ):
                            points.append((local_time_value, float(temp_c)))
                    if len(points) < 20:
                        continue
                    trajectory_curves.append(HourlyCurve(model=model, points=tuple(points)))
                    trajectory_run_keys.append(
                        f"{model}:{trajectory_row.run_time.isoformat()}"
                    )
                hourly_curve_cache[hourly_curve_key] = (
                    trajectory_curves,
                    trajectory_run_keys,
                )
            trajectory_curves, trajectory_run_keys = hourly_curve_cache[hourly_curve_key]
            if trajectory_cache_key not in trajectory_cache:
                if len(trajectory_curves) >= settings.accuracy_trajectory_min_models:
                    distribution = sample_daily_extremes(
                        trajectory_curves,
                        high_mean_c=result.mean_c,
                        low_mean_c=result.mean_c,
                        high_sigma_c=result.sigma_c,
                        low_sigma_c=result.sigma_c,
                        observed_by_local_hour=observed_hours.get(
                            (station.id, market.market_date)
                        ),
                        samples=settings.accuracy_trajectory_samples,
                        rho=settings.accuracy_trajectory_rho,
                        seed_material=(
                            f"{station.id}:{market.market_date}:{market.kind.value}:"
                            f"{target_bucket}:{'|'.join(trajectory_run_keys)}"
                        ),
                    )
                    selected_distribution = (
                        distribution.highs_c
                        if market.kind == WeatherKind.HIGH
                        else distribution.lows_c
                    )
                    trajectory_cache[trajectory_cache_key] = (
                        selected_distribution,
                        {
                            "model_count": distribution.model_count,
                            "hour_count": distribution.hour_count,
                            "sample_count": distribution.sample_count,
                            "rho": distribution.rho,
                            "models": [curve.model for curve in trajectory_curves],
                        },
                    )
            trajectory_result = trajectory_cache.get(trajectory_cache_key)
            if trajectory_result is not None:
                trajectory_values, trajectory_diagnostics = trajectory_result
                trajectory_probability = empirical_trajectory_probability(
                    trajectory_values, bucket
                )
                skill_report = trajectory_skill.get(
                    (station.id, target_bucket, market.kind)
                )
                trajectory_promotion = _promotion_for(
                    promotions,
                    expert="trajectory_ar1",
                    legacy_expert_version="hourly-ar1-v1",
                    station_id=station.id,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                trajectory_eligible = trajectory_promotion is not None
                component_details["trajectory_ar1"] = {
                    "probability": trajectory_probability,
                    **trajectory_diagnostics,
                    "eligible": trajectory_eligible,
                    "applied": trajectory_eligible,
                    **_promotion_audit_fields(trajectory_promotion),
                    "rolling_report_eligible": bool(skill_report and skill_report.eligible),
                    "skill_blocks": skill_report.sample_n if skill_report else 0,
                    "delta_brier": (
                        float(skill_report.delta_brier)
                        if skill_report and skill_report.delta_brier is not None
                        else None
                    ),
                    "ci_high": (
                        float(skill_report.ci_high)
                        if skill_report and skill_report.ci_high is not None
                        else None
                    ),
                }
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert="trajectory_ar1",
                        expert_version="hourly-ar1-v1",
                        probability=Decimal(str(trajectory_probability)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=Decimal(str(result.mean_c)).quantize(Decimal("0.0001")),
                        sigma_c=Decimal(str(result.sigma_c)).quantize(Decimal("0.0001")),
                        training_sample_n=skill_report.sample_n if skill_report else 0,
                        point_in_time_valid=True,
                        distribution={
                            **trajectory_diagnostics,
                            "horizon_bucket": target_bucket,
                            "eligible": trajectory_eligible,
                            **_promotion_audit_fields(trajectory_promotion),
                        },
                    )
                )
                if trajectory_eligible:
                    challenger_probabilities["trajectory_ar1"] = trajectory_probability

            observed_for_day = observed_hours.get((station.id, market.market_date), {})
            if (
                trajectory_cache_key not in nowcast_cache
                and len(trajectory_curves) >= settings.accuracy_trajectory_min_models
                and observed_for_day
            ):
                nowcast_adjustment = build_remaining_day_nowcast(
                    trajectory_curves,
                    observed_for_day,
                    minimum_matched_hours=settings.accuracy_nowcast_min_observed_hours,
                    lookback_hours=settings.accuracy_nowcast_lookback_hours,
                    decay_half_life_hours=settings.accuracy_nowcast_bias_half_life_hours,
                    maximum_bias_c=settings.accuracy_nowcast_max_bias_c,
                )
                if nowcast_adjustment is not None:
                    extreme_shift = (
                        nowcast_adjustment.high_shift_c
                        if market.kind == WeatherKind.HIGH
                        else nowcast_adjustment.low_shift_c
                    )
                    nowcast_distribution = sample_daily_extremes(
                        list(nowcast_adjustment.curves),
                        high_mean_c=result.mean_c + extreme_shift,
                        low_mean_c=result.mean_c + extreme_shift,
                        high_sigma_c=result.sigma_c,
                        low_sigma_c=result.sigma_c,
                        observed_by_local_hour=observed_for_day,
                        samples=settings.accuracy_trajectory_samples,
                        rho=settings.accuracy_trajectory_rho,
                        seed_material=(
                            f"nowcast:{station.id}:{market.market_date}:"
                            f"{market.kind.value}:{target_bucket}:"
                            f"{nowcast_adjustment.latest_observed_hour}:"
                            f"{'|'.join(trajectory_run_keys)}"
                        ),
                    )
                    selected_nowcast_distribution = (
                        nowcast_distribution.highs_c
                        if market.kind == WeatherKind.HIGH
                        else nowcast_distribution.lows_c
                    )
                    nowcast_cache[trajectory_cache_key] = (
                        selected_nowcast_distribution,
                        {
                            "recent_bias_c": nowcast_adjustment.recent_bias_c,
                            "extreme_shift_c": extreme_shift,
                            "matched_hours": nowcast_adjustment.matched_hours,
                            "latest_observed_hour": (
                                nowcast_adjustment.latest_observed_hour
                            ),
                            "decay_half_life_hours": (
                                settings.accuracy_nowcast_bias_half_life_hours
                            ),
                            "model_count": nowcast_distribution.model_count,
                            "sample_count": nowcast_distribution.sample_count,
                            "rho": nowcast_distribution.rho,
                        },
                    )
            nowcast_result = nowcast_cache.get(trajectory_cache_key)
            if nowcast_result is not None:
                nowcast_values, nowcast_diagnostics = nowcast_result
                nowcast_probability = empirical_trajectory_probability(nowcast_values, bucket)
                nowcast_report = nowcast_skill.get(
                    (station.id, target_bucket, market.kind)
                )
                nowcast_promotion = _promotion_for(
                    promotions,
                    expert="trajectory_nowcast",
                    legacy_expert_version="remaining-day-bias-v1",
                    station_id=station.id,
                    target_bucket=target_bucket,
                    kind=market.kind,
                )
                nowcast_eligible = nowcast_promotion is not None
                component_details["trajectory_nowcast"] = {
                    "probability": nowcast_probability,
                    **nowcast_diagnostics,
                    "eligible": nowcast_eligible,
                    "applied": nowcast_eligible,
                    **_promotion_audit_fields(nowcast_promotion),
                    "rolling_report_eligible": bool(nowcast_report and nowcast_report.eligible),
                    "skill_blocks": nowcast_report.sample_n if nowcast_report else 0,
                    "delta_brier": (
                        float(nowcast_report.delta_brier)
                        if nowcast_report and nowcast_report.delta_brier is not None
                        else None
                    ),
                    "ci_high": (
                        float(nowcast_report.ci_high)
                        if nowcast_report and nowcast_report.ci_high is not None
                        else None
                    ),
                }
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert="trajectory_nowcast",
                        expert_version="remaining-day-bias-v1",
                        probability=Decimal(str(nowcast_probability)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=Decimal(
                            str(result.mean_c + float(nowcast_diagnostics["extreme_shift_c"]))
                        ).quantize(Decimal("0.0001")),
                        sigma_c=Decimal(str(result.sigma_c)).quantize(Decimal("0.0001")),
                        training_sample_n=int(nowcast_diagnostics["matched_hours"]),
                        point_in_time_valid=True,
                        distribution={
                            **nowcast_diagnostics,
                            "horizon_bucket": target_bucket,
                            "eligible": nowcast_eligible,
                            **_promotion_audit_fields(nowcast_promotion),
                        },
                    )
                )
                if nowcast_eligible:
                    challenger_probabilities["trajectory_nowcast"] = nowcast_probability
            challenger = challenger_cache.get(cache_key)
            if challenger is not None:
                qrf_values, analog, mixture, training_n = challenger
                qrf_probability = sum(
                    (bucket.low_c is None or value >= bucket.low_c)
                    and (bucket.high_c is None or value < bucket.high_c)
                    for value in qrf_values
                ) / len(qrf_values)
                nonlinear_outputs: list[tuple[str, str, float, dict[str, Any]]] = [
                    (
                        "qrf",
                        "qrf-v1",
                        qrf_probability,
                        {"distribution_n": len(qrf_values)},
                    )
                ]
                if analog is not None:
                    nonlinear_outputs.append(
                        (
                            "analog",
                            "analog-distance-v1",
                            analog.bucket_probability(bucket),
                            {
                                "neighbors": len(analog.values_c),
                                "mean_c": analog.mean_c,
                                "sigma_c": analog.sigma_c,
                            },
                        )
                    )
                if mixture is not None:
                    nonlinear_outputs.append(
                        (
                            "mixture",
                            "residual-mixture-v1",
                            mixture.bucket_probability(bucket),
                            {
                                "weights": mixture.weights,
                                "means": mixture.means,
                                "sigmas": mixture.sigmas,
                            },
                        )
                    )
                for expert, expert_version, probability, diagnostics in nonlinear_outputs:
                    skill_report = nonlinear_skill.get(
                        (expert, station.id, target_bucket, market.kind)
                    )
                    promotion = _promotion_for(
                        promotions,
                        expert=expert,
                        legacy_expert_version=expert_version,
                        station_id=station.id,
                        target_bucket=target_bucket,
                        kind=market.kind,
                    )
                    eligible = promotion is not None
                    component_details[expert] = {
                        "probability": probability,
                        "sample_n": training_n,
                        **diagnostics,
                        "eligible": eligible,
                        "applied": eligible,
                        **_promotion_audit_fields(promotion),
                        "rolling_report_eligible": bool(
                            skill_report and skill_report.eligible
                        ),
                        "skill_blocks": skill_report.sample_n if skill_report else 0,
                        "delta_brier": (
                            float(skill_report.delta_brier)
                            if skill_report and skill_report.delta_brier is not None
                            else None
                        ),
                        "ci_high": (
                            float(skill_report.ci_high)
                            if skill_report and skill_report.ci_high is not None
                            else None
                        ),
                    }
                    accuracy_values.append(
                        AccuracyPredictionValue(
                            market_id=market.id,
                            ts=calculation_time,
                            expert=expert,
                            expert_version=expert_version,
                            probability=Decimal(str(probability)).quantize(Decimal("0.000001")),
                            mean_c=None,
                            sigma_c=None,
                            training_sample_n=training_n,
                            point_in_time_valid=True,
                            distribution={
                                "horizon_bucket": target_bucket,
                                **diagnostics,
                                "eligible": eligible,
                                **_promotion_audit_fields(promotion),
                            },
                        )
                    )
                    if eligible:
                        challenger_probabilities[expert] = probability

            # Anti-luck shrinkage: until the calibration sample has earned
            # trust, the published probability stays near the market and the
            # edge collapses. Both raw and used values are recorded.
            calibration_n = _calibration_depth(
                calibrations,
                station_id=station.id,
                target_bucket=target_bucket,
                kind=market.kind,
            )
            price_row = latest_prices.get(market.id)
            price = price_row[0] if price_row is not None else None
            price_seen_at = price_row[1] if price_row is not None else None
            market_anchor = (
                float(price.binary_yes_probability)
                if price is not None
                and price.binary_yes_probability is not None
                and price_seen_at is not None
                and now - price_seen_at <= SHRINKAGE_PRICE_MAX_AGE
                else None
            )
            expert_probabilities = {
                component.name: component.probability for component in result.components
            }
            expert_probabilities.update(challenger_probabilities)
            if result.climate_probability is not None:
                expert_probabilities["climatology"] = result.climate_probability
            if market_anchor is not None:
                expert_probabilities["market"] = market_anchor
            stack_fit = stack_fits.get((station.id, target_bucket, market.kind))
            fitted_weights = stack_fit.weights if stack_fit is not None else None
            shrinkage = shrink_to_market(
                result.probability,
                market_anchor,
                calibration_samples=calibration_n,
                prior_samples=settings.shrinkage_prior_samples,
            )
            baseline_probability = shrinkage.probability
            shrink_weight = round(shrinkage.weight, 6)
            shrink_applied = shrinkage.applied
            stack_training_n = stack_fit.training_sample_n if stack_fit is not None else 0
            stack_report = stack_skill.get((station.id, target_bucket, market.kind))
            stack_promotion = _promotion_for(
                promotions,
                expert=STACK_EXPERT,
                legacy_expert_version=STACK_EXPERT_VERSION,
                station_id=station.id,
                target_bucket=target_bucket,
                kind=market.kind,
            )
            stack_eligible = _stack_gate_passed(
                has_fitted_weights=fitted_weights is not None,
                promotion_active=stack_promotion is not None,
            )
            stack_candidate: float | None = None
            if fitted_weights is not None:
                stack_candidate = blend_experts(expert_probabilities, fitted_weights)
                accuracy_values.append(
                    AccuracyPredictionValue(
                        market_id=market.id,
                        ts=calculation_time,
                        expert=STACK_EXPERT,
                        expert_version=STACK_EXPERT_VERSION,
                        probability=Decimal(str(stack_candidate)).quantize(
                            Decimal("0.000001")
                        ),
                        mean_c=None,
                        sigma_c=None,
                        training_sample_n=stack_training_n,
                        point_in_time_valid=True,
                        distribution={
                            "horizon_bucket": target_bucket,
                            "weights": fitted_weights,
                            "fit_source": stack_fit.source if stack_fit is not None else None,
                            "local_sample_n": (
                                stack_fit.local_sample_n if stack_fit is not None else 0
                            ),
                            "peer_sample_n": (
                                stack_fit.peer_sample_n if stack_fit is not None else 0
                            ),
                            "peer_stations": (
                                stack_fit.peer_stations if stack_fit is not None else 0
                            ),
                            "baseline_probability": round(baseline_probability, 6),
                            "skill_blocks": stack_report.sample_n if stack_report else 0,
                            "delta_brier": (
                                float(stack_report.delta_brier)
                                if stack_report and stack_report.delta_brier is not None
                                else None
                            ),
                            "ci_high": (
                                float(stack_report.ci_high)
                                if stack_report and stack_report.ci_high is not None
                                else None
                            ),
                            "eligible": stack_eligible,
                            **_promotion_audit_fields(stack_promotion),
                        },
                    )
                )
            stack_applied = stack_candidate is not None and stack_eligible
            final_probability = (
                stack_candidate if stack_applied and stack_candidate is not None
                else baseline_probability
            )
            component_details["stacking"] = {
                "applied": stack_applied,
                "eligible": stack_eligible,
                **_promotion_audit_fields(stack_promotion),
                "rolling_report_eligible": bool(stack_report and stack_report.eligible),
                "weights": fitted_weights or {},
                "fit_source": stack_fit.source if stack_fit is not None else None,
                "local_sample_n": stack_fit.local_sample_n if stack_fit is not None else 0,
                "peer_sample_n": stack_fit.peer_sample_n if stack_fit is not None else 0,
                "peer_stations": stack_fit.peer_stations if stack_fit is not None else 0,
                "candidate_probability": (
                    round(stack_candidate, 6) if stack_candidate is not None else None
                ),
                "baseline_probability": round(baseline_probability, 6),
                "training_sample_n": stack_training_n,
                "skill_blocks": stack_report.sample_n if stack_report else 0,
                "minimum_blocks": settings.accuracy_stack_min_blocks,
                "delta_brier": (
                    float(stack_report.delta_brier)
                    if stack_report and stack_report.delta_brier is not None
                    else None
                ),
                "ci_high": (
                    float(stack_report.ci_high)
                    if stack_report and stack_report.ci_high is not None
                    else None
                ),
                "experts_available": sorted(expert_probabilities),
            }
            component_details["shrinkage"] = {
                "p_model_raw": round(result.probability, 6),
                "p_market": market_anchor,
                "weight": shrink_weight,
                "n": calibration_n,
                "n0": settings.shrinkage_prior_samples,
                "applied": shrink_applied,
                "market_snapshot_id": (
                    price.id if price is not None and market_anchor is not None else None
                ),
                "market_snapshot_hash": (
                    price.record_hash
                    if price is not None and market_anchor is not None
                    else None
                ),
                "market_anchor_protocol": (
                    CLOB_DUAL_PROTOCOL
                    if market_anchor is not None
                    else LEGACY_MARKET_PROTOCOL
                ),
            }
            values.append(
                ModelProbabilityValue(
                    market_id=market.id,
                    ts=calculation_time,
                    prob=Decimal(str(final_probability)).quantize(Decimal("0.000001")),
                    sigma=Decimal(str(result.sigma_c)).quantize(Decimal("0.0001")),
                    horizon_h=Decimal(str(horizon_h)).quantize(Decimal("0.01")),
                    components=component_details,
                    market_snapshot_id=(
                        price.id if price is not None and market_anchor is not None else None
                    ),
                    market_anchor_protocol=(
                        CLOB_DUAL_PROTOCOL
                        if market_anchor is not None
                        else LEGACY_MARKET_PROTOCOL
                    ),
                )
            )

        event_by_market = {market.id: market.event_id for market, _ in market_rows}
        horizon_bucket_by_market = {
            market.id: horizon_bucket(
                math.ceil(max(0.0, (market.resolves_at - now).total_seconds() / 3600))
            )
            for market, _station in market_rows
        }
        settlement_promotions: dict[str, EffectivePromotion] = {}
        for market, station in market_rows:
            promotion = _promotion_for(
                promotions,
                expert="settlement_beta",
                legacy_expert_version="partition-beta-v1",
                station_id=station.id,
                target_bucket=horizon_bucket_by_market[market.id],
                kind=market.kind,
            )
            if promotion is None:
                continue
            existing = settlement_promotions.get(market.event_id)
            if existing is not None and existing.activation_id != promotion.activation_id:
                raise RuntimeError(
                    f"event {market.event_id!r} spans conflicting settlement promotions"
                )
            settlement_promotions[market.event_id] = promotion
        values = _renormalize_event_probabilities(
            values,
            event_by_market,
            settlement_calibrator=settlement_calibrator,
            settlement_promotions=settlement_promotions,
        )
        materialization = _materialize_event_probability_coherence(
            values,
            list(evidence_markets),
            eligible_market_ids={market.id for market, _station in market_rows},
            latest_prices=latest_prices,
        )
        values = list(materialization.values)
        for value in values:
            calibration_detail = value.components.get("settlement_calibration")
            if not isinstance(calibration_detail, dict):
                continue
            shadow_probability = calibration_detail.get("shadow_probability")
            training_blocks = calibration_detail.get("training_blocks")
            if not isinstance(shadow_probability, int | float) or not isinstance(
                training_blocks, int
            ):
                continue
            accuracy_values.append(
                AccuracyPredictionValue(
                    market_id=value.market_id,
                    ts=calculation_time,
                    expert="settlement_beta",
                    expert_version="partition-beta-v1",
                    probability=Decimal(str(shadow_probability)).quantize(Decimal("0.000001")),
                    mean_c=None,
                    sigma_c=None,
                    training_sample_n=training_blocks,
                    point_in_time_valid=True,
                    distribution={
                        "horizon_bucket": horizon_bucket_by_market.get(value.market_id),
                        "alpha": calibration_detail.get("alpha"),
                        "beta": calibration_detail.get("beta"),
                        "eligible": calibration_detail.get("eligible"),
                        "applied": calibration_detail.get("applied"),
                        "scope": "global:all:all",
                        "promotion_active": calibration_detail.get("promotion_active"),
                        "activation_id": calibration_detail.get("activation_id"),
                        "promotion_definition_hash": calibration_detail.get(
                            "promotion_definition_hash"
                        ),
                        "promotion_decision_hash": calibration_detail.get(
                            "promotion_decision_hash"
                        ),
                    },
                )
            )

        async with session_factory.begin() as session:
            written = await upsert_model_probs(session, values)
            revoked = await expire_unclaimed_pending_signals_for_events(
                session,
                materialization.blocked_event_ids,
                reason="event_probability_cohort_blocked",
            )
            sealed_accuracy_values: list[AccuracyPredictionValue] = []
            if seal_accuracy_evidence:
                baseline_rows = (
                    (
                        await session.execute(
                            select(ModelProb)
                            .where(
                                ModelProb.ts == calculation_time,
                                ModelProb.market_id.in_([value.market_id for value in values]),
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                # Accuracy sealing proves its own exhaustive materialized
                # probability vector.  Do not filter by trade-valid events:
                # that stricter domain additionally requires executable CLOB
                # context and is enforced only by signal/intent generation.
                sealed_accuracy_values = seal_legacy_accuracy_predictions(
                    accuracy_values,
                    baseline_rows,
                    list(evidence_markets),
                    settings,
                    training_cutoff=now,
                )
                await upsert_accuracy_predictions(session, sealed_accuracy_values)
            stack_valid_from = calculation_time.replace(hour=0, minute=0, second=0, microsecond=0)
            for (station_id, stack_bucket, stack_kind), stack_fit in stack_fits.items():
                stack_report = stack_skill.get((station_id, stack_bucket, stack_kind))
                stack_promotion = _promotion_for(
                    promotions,
                    expert=STACK_EXPERT,
                    legacy_expert_version=STACK_EXPERT_VERSION,
                    station_id=station_id,
                    target_bucket=stack_bucket,
                    kind=stack_kind,
                )
                if not _stack_gate_passed(
                    has_fitted_weights=True,
                    promotion_active=stack_promotion is not None,
                ):
                    continue
                sample_n = stack_fit.training_sample_n
                await upsert_daily_stack_weights(
                    session,
                    station_id=station_id,
                    horizon_bucket=stack_bucket,
                    kind=stack_kind.value,
                    weights=stack_fit.weights,
                    sample_n=sample_n,
                    effective_sample_n=Decimal(sample_n),
                    valid_from=stack_valid_from,
                )
        logger.info(
            "probability recompute complete: markets=%d written=%d skipped=%d "
            "sealed_predictions=%d revoked_pending=%d",
            len(market_rows),
            written,
            skipped,
            len(sealed_accuracy_values),
            revoked,
        )
        return written, skipped


def _renormalize_event_probabilities(
    values: list[ModelProbabilityValue],
    event_by_market: dict[int, str],
    *,
    settlement_calibrator: SettlementProbabilityCalibrator | None = None,
    settlement_promotions: dict[str, EffectivePromotion] | None = None,
) -> list[ModelProbabilityValue]:
    """Rescale each event's shrunk bucket probabilities back to the raw model
    total, so an event's buckets form a proper distribution again."""
    groups: dict[str, list[int]] = defaultdict(list)
    for index, value in enumerate(values):
        event_id = event_by_market.get(value.market_id)
        if event_id is not None:
            groups[event_id].append(index)

    result = list(values)
    promotions = settlement_promotions or {}
    for event_id, indices in groups.items():
        shrunk = [float(values[index].prob) for index in indices]
        raw_sum = sum(
            float(
                values[index].components.get("shrinkage", {}).get("p_model_raw")
                or values[index].prob
            )
            for index in indices
        )
        rescaled, scale = renormalize_event(shrunk, reference_sum=raw_sum)
        if scale is None:
            continue
        shadow_calibrated = (
            calibrate_partition(
                tuple(rescaled),
                alpha=float(settlement_calibrator.alpha),
                beta=float(settlement_calibrator.beta),
            )
            if settlement_calibrator is not None
            else None
        )
        promotion = promotions.get(event_id)
        applied = bool(settlement_calibrator is not None and promotion is not None)
        final_probabilities = (
            list(shadow_calibrated) if applied and shadow_calibrated is not None else rescaled
        )
        for position, index in enumerate(indices):
            components = dict(values[index].components)
            components["shrinkage"] = {
                **components.get("shrinkage", {}),
                "event_scale": round(scale, 6),
                "p_final": round(rescaled[position], 6),
            }
            if settlement_calibrator is not None and shadow_calibrated is not None:
                components["settlement_calibration"] = {
                    "method": "partition-beta-v1",
                    "scope": settlement_calibrator.scope_key,
                    "alpha": float(settlement_calibrator.alpha),
                    "beta": float(settlement_calibrator.beta),
                    "training_blocks": settlement_calibrator.training_blocks,
                    "eligible": applied,
                    "applied": applied,
                    **_promotion_audit_fields(promotion),
                    "rolling_report_eligible": settlement_calibrator.eligible,
                    "p_before": round(rescaled[position], 6),
                    "shadow_probability": round(shadow_calibrated[position], 6),
                    "p_final": round(final_probabilities[position], 6),
                }
            result[index] = replace(
                values[index],
                prob=Decimal(str(final_probabilities[position])).quantize(Decimal("0.000001")),
                components=components,
            )
    return result


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(recompute_probs(settings))


if __name__ == "__main__":
    main()
