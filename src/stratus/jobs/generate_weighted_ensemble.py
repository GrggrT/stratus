from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from multiprocessing import get_context
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from stratus.config import Settings, get_settings
from stratus.db.models import (
    DailyActual,
    Forecast,
    ForecastEvaluation,
    GammaOutcomeEvent,
    HistoricalForecast,
    Market,
    MarketStatus,
    ModelProb,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    AccuracyPredictionValue,
    forecast_evaluation_cohort_is_complete,
    forecast_evaluation_evidence_is_valid,
    upsert_accuracy_predictions,
)
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)
from stratus.services.emos import horizon_bucket
from stratus.services.model_catalog import canonical_model
from stratus.services.probability import Bucket, gaussian_bucket_probability
from stratus.services.temperature_ensemble import (
    SettlementAlignedAssessment,
    SettlementAlignedCase,
    TemperatureEnsembleAssessment,
    TemperatureEnsembleCase,
    TemperatureEnsembleFit,
    fit_temperature_ensemble,
    residual_kernel_bucket_probability,
    settlement_aligned_temperature_assessment,
    walk_forward_temperature_assessment,
)

logger = logging.getLogger(__name__)
EXPERT = "historical_weighted"
EXPERT_VERSION = "convex-affine-temperature-canonical-exact-sealed-v5"
SHRUNK_EXPERT = "historical_weighted_shrunk"
SHRUNK_EXPERT_VERSION = "convex-affine-empirical-shrink25-canonical-exact-sealed-v5"
SHRUNK_EMPIRICAL_WEIGHT = 0.25
MODELS = ("ecmwf", "gfs", "icon", "gem")
PRIOR_WEIGHTS = {"ecmwf": 0.35, "gfs": 0.40, "icon": 0.15, "gem": 0.10}
MINIMUM_TRAINING_SAMPLES = 180
MINIMUM_PROSPECTIVE_BLOCKS = 60
MINIMUM_SETTLEMENT_BACKTEST_BLOCKS = 30
TRAINING_DAYS = 365
HISTORY_DAYS = 730
MAX_BASELINE_AGE = timedelta(minutes=5)
BUCKET_ORDER = ("0-6h", "6-24h", "24-48h", "48h+")
HORIZON_ANCHORS = {"0-6h": 3.0, "6-24h": 12.0, "24-48h": 36.0, "48h+": 72.0}
BASELINE_TOTAL_TOLERANCE = Decimal("0.01")
PROBABILITY_QUANTUM = Decimal("0.000001")
PROBABILITY_UNITS = 1_000_000

HistoricalRow = tuple[HistoricalForecast, Decimal]
CurrentRow = tuple[ModelProb, Market]
ResolvedRow = tuple[ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent]
Scope = tuple[int, str, WeatherKind]


def _baseline_total_is_valid(total: float) -> bool:
    return (
        math.isfinite(total)
        and total > 0
        and abs(Decimal(str(total)) - Decimal("1")) <= BASELINE_TOTAL_TOLERANCE
    )


def _quantize_probability_partition(
    values: Sequence[tuple[int, Decimal | float]],
) -> dict[int, Decimal]:
    """Normalize an event partition to an exact six-decimal simplex.

    Independent half-even quantization can lose or gain a few millionths.
    Hamilton apportionment floors every bucket and assigns the remaining
    millionth units by descending fractional remainder.  The market ID is the
    stable tie-breaker, so output is independent of query/input ordering.
    """

    if not values:
        raise ValueError("probability partition is empty")

    ordered: list[tuple[int, Decimal]] = []
    seen_market_ids: set[int] = set()
    for market_id, raw_value in sorted(values, key=lambda item: item[0]):
        if market_id in seen_market_ids:
            raise ValueError(f"duplicate market ID in probability partition: {market_id}")
        seen_market_ids.add(market_id)
        value = raw_value if isinstance(raw_value, Decimal) else Decimal(str(raw_value))
        if not value.is_finite() or value < 0:
            raise ValueError("probability partition contains an invalid value")
        ordered.append((market_id, value))

    total = sum((value for _market_id, value in ordered), start=Decimal("0"))
    if total <= 0:
        raise ValueError("probability partition has no positive mass")

    floors: dict[int, int] = {}
    remainders: list[tuple[Decimal, int]] = []
    for market_id, value in ordered:
        exact_units = value / total * PROBABILITY_UNITS
        floor_units = int(exact_units.to_integral_value(rounding=ROUND_FLOOR))
        floors[market_id] = floor_units
        remainders.append((exact_units - floor_units, market_id))

    remaining_units = PROBABILITY_UNITS - sum(floors.values())
    if not 0 <= remaining_units <= len(ordered):
        raise ArithmeticError("probability apportionment escaped its unit budget")
    for _remainder, market_id in sorted(
        remainders,
        key=lambda item: (-item[0], item[1]),
    )[:remaining_units]:
        floors[market_id] += 1

    result = {
        market_id: (Decimal(floors[market_id]) * PROBABILITY_QUANTUM)
        for market_id, _value in ordered
    }
    if (
        sum(result.values(), start=Decimal("0")) != Decimal("1.000000")
        or any(value < 0 or value > 1 for value in result.values())
    ):
        raise ArithmeticError("quantized probability partition is not a simplex")
    return result


def _historical_training_statement(
    *,
    earliest_date: date,
    truth_cutoff: datetime,
) -> Select[tuple[HistoricalForecast, Decimal]]:
    """Select eligible exact labels before undisputed METAR fallback labels."""

    exact_actuals = canonical_settlement_actuals(as_of=truth_cutoff)
    actual_temp = func.coalesce(exact_actuals.c.value_c, DailyActual.temp_c)
    return (
        select(HistoricalForecast, actual_temp)
        .outerjoin(
            exact_actuals,
            and_(
                exact_actuals.c.station_id == HistoricalForecast.station_id,
                exact_actuals.c.date == HistoricalForecast.valid_date,
                exact_actuals.c.kind == HistoricalForecast.kind,
            ),
        )
        .outerjoin(
            DailyActual,
            and_(
                DailyActual.station_id == HistoricalForecast.station_id,
                DailyActual.date == HistoricalForecast.valid_date,
                DailyActual.kind == HistoricalForecast.kind,
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
                    station_id=HistoricalForecast.station_id,
                    actual_date=HistoricalForecast.valid_date,
                    kind=HistoricalForecast.kind,
                    as_of=truth_cutoff,
                ),
            ),
        )
        .where(
            HistoricalForecast.model.in_(MODELS),
            HistoricalForecast.valid_date >= earliest_date,
            HistoricalForecast.fetched_at <= truth_cutoff,
            or_(
                HistoricalForecast.first_seen_at.is_(None),
                HistoricalForecast.first_seen_at <= truth_cutoff,
            ),
            actual_temp.is_not(None),
        )
    )


def _scope_key(scope: Scope) -> str:
    return f"{scope[0]}:{scope[1]}:{scope[2].value}"


def _build_cases(rows: list[HistoricalRow]) -> list[TemperatureEnsembleCase]:
    grouped: dict[tuple[int, str, WeatherKind, date], dict[str, float]] = defaultdict(dict)
    actuals: dict[tuple[int, str, WeatherKind, date], float] = {}
    for forecast, actual in rows:
        model = canonical_model(forecast.model)
        if model not in MODELS:
            continue
        bucket = horizon_bucket(forecast.horizon_h)
        key = (forecast.station_id, bucket, forecast.kind, forecast.valid_date)
        grouped[key][model] = float(forecast.temp_c)
        actuals[key] = float(actual)
    cases = []
    for (station_id, bucket, kind, valid_date), predictions in grouped.items():
        if any(model not in predictions for model in MODELS):
            continue
        cases.append(
            TemperatureEnsembleCase(
                scope_key=_scope_key((station_id, bucket, kind)),
                # Horizons from one weather event remain one bootstrap block.
                block_key=f"{station_id}:{valid_date.isoformat()}:{kind.value}",
                valid_date=valid_date,
                predictions=tuple((model, predictions[model]) for model in MODELS),
                actual_c=actuals[(station_id, bucket, kind, valid_date)],
            )
        )
    return sorted(cases, key=lambda item: (item.scope_key, item.valid_date))


def _fit_current_scopes(
    cases: list[TemperatureEnsembleCase],
    *,
    current_scopes: set[Scope],
    as_of: date,
) -> dict[Scope, tuple[Scope, TemperatureEnsembleFit]]:
    cases_by_scope: dict[str, list[TemperatureEnsembleCase]] = defaultdict(list)
    cutoff = as_of - timedelta(days=TRAINING_DAYS)
    for case in cases:
        if cutoff <= case.valid_date < as_of:
            cases_by_scope[case.scope_key].append(case)
    fits: dict[Scope, tuple[Scope, TemperatureEnsembleFit]] = {}
    for target_scope in current_scopes:
        station_id, target_bucket, kind = target_scope
        ordered_buckets = sorted(
            BUCKET_ORDER,
            key=lambda candidate: abs(
                BUCKET_ORDER.index(candidate) - BUCKET_ORDER.index(target_bucket)
            ),
        )
        for candidate_bucket in ordered_buckets:
            source_scope = (station_id, candidate_bucket, kind)
            training = cases_by_scope.get(_scope_key(source_scope), [])
            if len(training) < MINIMUM_TRAINING_SAMPLES:
                continue
            fits[target_scope] = (
                source_scope,
                fit_temperature_ensemble(training, prior=PRIOR_WEIGHTS),
            )
            break
    return fits


def _latest_forecasts(
    rows: list[Forecast],
) -> dict[tuple[int, str, date, WeatherKind], list[Forecast]]:
    grouped: dict[tuple[int, str, date, WeatherKind], list[Forecast]] = defaultdict(list)
    for forecast in rows:
        model = canonical_model(forecast.model)
        if model in MODELS:
            grouped[
                (
                    forecast.station_id,
                    model,
                    forecast.valid_date,
                    forecast.kind,
                )
            ].append(forecast)
    for values in grouped.values():
        values.sort(key=lambda item: (item.available_at, item.run_time), reverse=True)
    return grouped


def _point_in_time_forecasts(
    forecasts: dict[tuple[int, str, date, WeatherKind], list[Forecast]],
    *,
    market: Market,
    decision_at: datetime,
) -> dict[str, Forecast] | None:
    if market.station_id is None:
        return None
    selected: dict[str, Forecast] = {}
    for model in MODELS:
        candidates = forecasts.get(
            (market.station_id, model, market.market_date, market.kind),
            [],
        )
        available = next(
            (forecast for forecast in candidates if forecast.available_at <= decision_at),
            None,
        )
        if available is None:
            return None
        selected[model] = available
    return selected


def _point_in_time_models(
    forecasts: dict[tuple[int, str, date, WeatherKind], list[Forecast]],
    *,
    market: Market,
    decision_at: datetime,
) -> dict[str, float] | None:
    selected = _point_in_time_forecasts(
        forecasts,
        market=market,
        decision_at=decision_at,
    )
    if selected is None:
        return None
    return {model: float(forecast.temp_c) for model, forecast in selected.items()}


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _pipeline_version(model_prob: ModelProb) -> str | None:
    pipeline = model_prob.components.get("pipeline")
    if not isinstance(pipeline, dict):
        return None
    cohort = pipeline.get("cohort")
    if isinstance(cohort, str) and cohort:
        return cohort
    version = pipeline.get("version")
    return version if isinstance(version, str) and version else None


def _experiment_fingerprint(expert_version: str, pipeline_version: str) -> str:
    return _canonical_hash(
        {
            "expert_version": expert_version,
            "pipeline_version": pipeline_version,
            "models": MODELS,
            "prior_weights": PRIOR_WEIGHTS,
            "training_days": TRAINING_DAYS,
            "minimum_training_samples": MINIMUM_TRAINING_SAMPLES,
            "empirical_blend_weight": SHRUNK_EMPIRICAL_WEIGHT,
        }
    )


def _bucket(market: Market) -> Bucket:
    return Bucket(
        low_c=float(market.bucket_low_c) if market.bucket_low_c is not None else None,
        high_c=float(market.bucket_high_c) if market.bucket_high_c is not None else None,
    )


def _bucket_sort_key(market: Market) -> tuple[float, float]:
    return (
        float(market.bucket_low_c) if market.bucket_low_c is not None else float("-inf"),
        float(market.bucket_high_c) if market.bucket_high_c is not None else float("inf"),
    )


def _build_settlement_cases(
    rows: list[ResolvedRow],
    forecast_rows: list[Forecast],
) -> list[SettlementAlignedCase]:
    forecasts = _latest_forecasts(forecast_rows)
    snapshots: dict[tuple[str, int, datetime], list[ResolvedRow]] = defaultdict(list)
    for evaluation, model_prob, market, outcome_event in rows:
        if evaluation.resolution_event_id is None:
            continue
        snapshots[
            (
                evaluation.independent_sample_key,
                evaluation.resolution_event_id,
                model_prob.ts,
            )
        ].append((evaluation, model_prob, market, outcome_event))
    best_by_sample: dict[tuple[str, int], tuple[float, SettlementAlignedCase]] = {}
    for (sample_key, resolution_event_id, decision_at), snapshot_rows in snapshots.items():
        if any(row[0].market_id != row[2].id for row in snapshot_rows) or not (
            forecast_evaluation_cohort_is_complete([(row[0], row[3]) for row in snapshot_rows])
        ):
            continue
        ordered = sorted(snapshot_rows, key=lambda row: _bucket_sort_key(row[2]))
        if len(ordered) < 2 or sum(row[0].resolved_outcome for row in ordered) != 1:
            continue
        evaluation, _model_prob, market, _outcome_event = ordered[0]
        if market.station_id is None:
            continue
        target_bucket = evaluation.details.get("horizon_bucket")
        if not isinstance(target_bucket, str) or target_bucket not in HORIZON_ANCHORS:
            continue
        raw_models = _point_in_time_models(
            forecasts,
            market=market,
            decision_at=decision_at,
        )
        if raw_models is None:
            continue
        scope_candidates = tuple(
            _scope_key((market.station_id, candidate, market.kind))
            for candidate in sorted(
                BUCKET_ORDER,
                key=lambda candidate: abs(
                    BUCKET_ORDER.index(candidate) - BUCKET_ORDER.index(target_bucket)
                ),
            )
        )
        average_horizon = sum(float(row[1].horizon_h) for row in ordered) / len(ordered)
        candidate = SettlementAlignedCase(
            block_key=f"{market.station_id}:{market.market_date.isoformat()}:{market.kind.value}",
            valid_date=market.market_date,
            scope_candidates=scope_candidates,
            predictions=tuple((model, raw_models[model]) for model in MODELS),
            buckets=tuple(_bucket(row[2]) for row in ordered),
            baseline_probabilities=tuple(float(row[0].model_probability) for row in ordered),
            outcomes=tuple(bool(row[0].resolved_outcome) for row in ordered),
        )
        distance = abs(average_horizon - HORIZON_ANCHORS[target_bucket])
        key = (sample_key, resolution_event_id)
        previous = best_by_sample.get(key)
        if previous is None or distance < previous[0]:
            best_by_sample[key] = (distance, candidate)
    return sorted(
        (value[1] for value in best_by_sample.values()),
        key=lambda item: (item.block_key, item.valid_date, item.scope_candidates[0]),
    )


def _settlement_assessment(
    history: list[TemperatureEnsembleCase],
    events: list[SettlementAlignedCase],
) -> SettlementAlignedAssessment | None:
    if not events:
        return None
    return settlement_aligned_temperature_assessment(
        history,
        events,
        prior=PRIOR_WEIGHTS,
        training_days=TRAINING_DAYS,
        minimum_training_samples=MINIMUM_TRAINING_SAMPLES,
    )


def _compute_ensemble_artifacts(
    cases: list[TemperatureEnsembleCase],
    current_scopes: set[Scope],
    as_of: date,
    settlement_cases: list[SettlementAlignedCase],
) -> tuple[
    TemperatureEnsembleAssessment,
    dict[Scope, tuple[Scope, TemperatureEnsembleFit]],
    SettlementAlignedAssessment | None,
]:
    """Run the CPU-heavy fit outside the scheduler process and its event loop."""
    return (
        walk_forward_temperature_assessment(cases, prior=PRIOR_WEIGHTS),
        _fit_current_scopes(cases, current_scopes=current_scopes, as_of=as_of),
        _settlement_assessment(cases, settlement_cases),
    )


def _prediction_values(
    current_rows: list[CurrentRow],
    forecast_rows: list[Forecast],
    expected_market_ids_by_event: dict[str, set[int]],
    fits: dict[Scope, tuple[Scope, TemperatureEnsembleFit]],
    assessment: TemperatureEnsembleAssessment,
    settlement_assessment: SettlementAlignedAssessment | None,
    *,
    input_snapshot_at: datetime,
    generated_at: datetime,
) -> list[AccuracyPredictionValue]:
    forecasts = _latest_forecasts(forecast_rows)
    eligible = bool(
        assessment.independent_blocks >= MINIMUM_PROSPECTIVE_BLOCKS
        and assessment.delta_mse < 0
        and assessment.delta_ci is not None
        and assessment.delta_ci[1] < 0
    )
    pending: dict[
        tuple[str, datetime, str],
        list[tuple[ModelProb, Market, float, float, float, float, dict[str, Any]]],
    ] = defaultdict(list)
    for model_prob, market in current_rows:
        if (
            market.station_id is None
            or market.resolves_at <= generated_at
            or model_prob.ts > input_snapshot_at
            or generated_at - model_prob.ts > MAX_BASELINE_AGE
        ):
            continue
        target_bucket = horizon_bucket(math.ceil(float(model_prob.horizon_h)))
        fitted = fits.get((market.station_id, target_bucket, market.kind))
        selected_forecasts = _point_in_time_forecasts(
            forecasts,
            market=market,
            decision_at=model_prob.ts,
        )
        pipeline_version = _pipeline_version(model_prob)
        if fitted is None or selected_forecasts is None or pipeline_version is None:
            continue
        raw_models = {
            model: float(forecast.temp_c) for model, forecast in selected_forecasts.items()
        }
        source_scope, fit = fitted
        mean_c = fit.predict(raw_models)
        sigma_c = fit.residual_sigma_c
        probability = gaussian_bucket_probability(mean_c, sigma_c, _bucket(market))
        empirical_probability = residual_kernel_bucket_probability(
            fit,
            raw_models,
            _bucket(market),
        )
        forecast_provenance = [
            {
                "id": forecast.id,
                "model": model,
                "temp_c": str(forecast.temp_c),
                "run_time": forecast.run_time.isoformat(),
                "available_at": forecast.available_at.isoformat(),
            }
            for model, forecast in sorted(selected_forecasts.items())
        ]
        input_hash = _canonical_hash(
            {
                "input_snapshot_at": input_snapshot_at.isoformat(),
                "baseline": {
                    "model_prob_id": model_prob.id,
                    "ts": model_prob.ts.isoformat(),
                    "probability": str(model_prob.prob),
                    "horizon_h": str(model_prob.horizon_h),
                    "pipeline_version": pipeline_version,
                },
                "market": {
                    "id": market.id,
                    "event_id": market.event_id,
                    "station_id": market.station_id,
                    "market_date": market.market_date.isoformat(),
                    "kind": market.kind.value,
                    "bucket_low_c": str(market.bucket_low_c),
                    "bucket_high_c": str(market.bucket_high_c),
                    "resolves_at": market.resolves_at.isoformat(),
                },
                "forecasts": forecast_provenance,
                "fit": {
                    "scope": _scope_key(source_scope),
                    "models": fit.models,
                    "weights": fit.weights,
                    "intercepts": fit.intercepts,
                    "slopes": fit.slopes,
                    "residual_sigma_c": fit.residual_sigma_c,
                    "training_samples": fit.training_samples,
                    "training_data_hash": fit.training_data_hash,
                },
            }
        )
        details: dict[str, Any] = {
            "method": "historical_convex_affine_temperature_ensemble",
            "baseline_probability": float(model_prob.prob),
            "horizon_bucket": target_bucket,
            "fit_scope": _scope_key(source_scope),
            "models": list(fit.models),
            "weights": fit.weight_mapping(),
            "intercepts": dict(zip(fit.models, fit.intercepts, strict=True)),
            "slopes": dict(zip(fit.models, fit.slopes, strict=True)),
            "prior_weights": PRIOR_WEIGHTS,
            "training_samples": fit.training_samples,
            "training_data_hash": fit.training_data_hash,
            "training_valid_before": input_snapshot_at.date().isoformat(),
            "input_snapshot_at": input_snapshot_at.isoformat(),
            "baseline_model_prob_id": model_prob.id,
            "baseline_pipeline_version": pipeline_version,
            "input_hash": input_hash,
            "input_forecasts": forecast_provenance,
            "evidence_protocol": "sealed-v2",
            "historical_oof_samples": assessment.samples,
            "historical_oof_blocks": assessment.independent_blocks,
            "historical_baseline_rmse_c": assessment.baseline_rmse_c,
            "historical_candidate_rmse_c": assessment.candidate_rmse_c,
            "historical_delta_mse": assessment.delta_mse,
            "historical_delta_ci_low": (assessment.delta_ci[0] if assessment.delta_ci else None),
            "historical_delta_ci_high": (assessment.delta_ci[1] if assessment.delta_ci else None),
            "historical_oof_eligible": eligible,
            "settlement_available_events": (
                settlement_assessment.available_events if settlement_assessment else 0
            ),
            "settlement_fitted_events": (
                settlement_assessment.fitted_events if settlement_assessment else 0
            ),
            "settlement_backtest_blocks": (
                settlement_assessment.independent_blocks if settlement_assessment else 0
            ),
            "settlement_backtest_market_rows": (
                settlement_assessment.market_rows if settlement_assessment else 0
            ),
            "settlement_baseline_brier": (
                settlement_assessment.baseline_brier if settlement_assessment else None
            ),
            "settlement_candidate_brier": (
                settlement_assessment.candidate_brier if settlement_assessment else None
            ),
            "settlement_delta_brier": (
                settlement_assessment.delta_brier if settlement_assessment else None
            ),
            "settlement_delta_ci_low": (
                settlement_assessment.delta_ci[0]
                if settlement_assessment and settlement_assessment.delta_ci
                else None
            ),
            "settlement_delta_ci_high": (
                settlement_assessment.delta_ci[1]
                if settlement_assessment and settlement_assessment.delta_ci
                else None
            ),
            "settlement_aligned_eligible": bool(
                settlement_assessment
                and settlement_assessment.independent_blocks >= MINIMUM_SETTLEMENT_BACKTEST_BLOCKS
                and settlement_assessment.candidate_brier < settlement_assessment.baseline_brier
                and settlement_assessment.delta_ci
                and settlement_assessment.delta_ci[1] < 0
            ),
            "settlement_empirical_candidate_brier": (
                settlement_assessment.empirical_candidate_brier if settlement_assessment else None
            ),
            "settlement_empirical_delta_brier": (
                settlement_assessment.empirical_delta_brier if settlement_assessment else None
            ),
            "settlement_empirical_delta_ci_low": (
                settlement_assessment.empirical_delta_ci[0]
                if settlement_assessment and settlement_assessment.empirical_delta_ci
                else None
            ),
            "settlement_empirical_delta_ci_high": (
                settlement_assessment.empirical_delta_ci[1]
                if settlement_assessment and settlement_assessment.empirical_delta_ci
                else None
            ),
            "settlement_empirical_eligible": bool(
                settlement_assessment
                and settlement_assessment.independent_blocks >= MINIMUM_SETTLEMENT_BACKTEST_BLOCKS
                and settlement_assessment.empirical_candidate_brier
                < settlement_assessment.baseline_brier
                and settlement_assessment.empirical_delta_ci
                and settlement_assessment.empirical_delta_ci[1] < 0
            ),
            "settlement_shrunk_empirical_weight": (
                settlement_assessment.shrunk_empirical_weight if settlement_assessment else 0.25
            ),
            "settlement_shrunk_empirical_brier": (
                settlement_assessment.shrunk_empirical_brier if settlement_assessment else None
            ),
            "settlement_shrunk_empirical_delta_brier": (
                settlement_assessment.shrunk_empirical_delta_brier
                if settlement_assessment
                else None
            ),
            "settlement_shrunk_empirical_delta_ci_low": (
                settlement_assessment.shrunk_empirical_delta_ci[0]
                if settlement_assessment and settlement_assessment.shrunk_empirical_delta_ci
                else None
            ),
            "settlement_shrunk_empirical_delta_ci_high": (
                settlement_assessment.shrunk_empirical_delta_ci[1]
                if settlement_assessment and settlement_assessment.shrunk_empirical_delta_ci
                else None
            ),
            "settlement_shrunk_empirical_eligible": bool(
                settlement_assessment
                and settlement_assessment.independent_blocks >= MINIMUM_SETTLEMENT_BACKTEST_BLOCKS
                and settlement_assessment.shrunk_empirical_brier
                < settlement_assessment.baseline_brier
                and settlement_assessment.shrunk_empirical_delta_ci
                and settlement_assessment.shrunk_empirical_delta_ci[1] < 0
            ),
            "minimum_settlement_backtest_blocks": MINIMUM_SETTLEMENT_BACKTEST_BLOCKS,
            "minimum_prospective_blocks": MINIMUM_PROSPECTIVE_BLOCKS,
            "truth_source": (
                "canonical_settlement_actual_then_current_reconciliation_clean_daily_actual"
            ),
            "applied": False,
        }
        pending[(market.event_id, model_prob.ts, pipeline_version)].append(
            (
                model_prob,
                market,
                probability,
                empirical_probability,
                mean_c,
                sigma_c,
                details,
            )
        )
    values = []
    for (event_id, cohort_ts, pipeline_version), event_rows in pending.items():
        expected_market_ids = expected_market_ids_by_event.get(event_id, set())
        actual_market_ids = {row[1].id for row in event_rows}
        if (
            len(expected_market_ids) < 2
            or actual_market_ids != expected_market_ids
            or any(row[0].ts != cohort_ts for row in event_rows)
        ):
            logger.warning(
                "weighted ensemble skipped incomplete event cohort event=%s expected=%d actual=%d",
                event_id,
                len(expected_market_ids),
                len(actual_market_ids),
            )
            continue
        total = sum(max(0.0, row[2]) for row in event_rows)
        empirical_total = sum(max(0.0, row[3]) for row in event_rows)
        baseline_total = sum(float(row[0].prob) for row in event_rows)
        if total <= 0 or empirical_total <= 0 or not _baseline_total_is_valid(baseline_total):
            if not _baseline_total_is_valid(baseline_total):
                logger.warning(
                    "weighted ensemble skipped invalid baseline partition event=%s total=%.6f",
                    event_id,
                    baseline_total,
                )
            continue
        event_input_hash = _canonical_hash(
            {
                "event_id": event_id,
                "cohort_ts": cohort_ts.isoformat(),
                "pipeline_version": pipeline_version,
                "market_ids": sorted(actual_market_ids),
                "bucket_input_hashes": sorted(
                    (row[1].id, str(row[6]["input_hash"])) for row in event_rows
                ),
                "normalization_totals": {
                    "candidate": str(total),
                    "empirical": str(empirical_total),
                    "baseline": str(baseline_total),
                },
            }
        )
        candidate_partition = _quantize_probability_partition(
            [(row[1].id, row[2]) for row in event_rows]
        )
        baseline_partition = _quantize_probability_partition(
            [(row[1].id, row[0].prob) for row in event_rows]
        )
        shrunk_raw = [
            (
                row[1].id,
                (1 - SHRUNK_EMPIRICAL_WEIGHT)
                * (float(row[0].prob) / baseline_total)
                + SHRUNK_EMPIRICAL_WEIGHT * (row[3] / empirical_total),
            )
            for row in event_rows
        ]
        shrunk_partition = _quantize_probability_partition(shrunk_raw)
        for (
            model_prob,
            market,
            _probability,
            empirical_probability,
            mean_c,
            sigma_c,
            details,
        ) in event_rows:
            normalized_empirical = empirical_probability / empirical_total
            normalized_baseline = baseline_partition[market.id]
            sealed_details = {
                **details,
                "baseline_probability": float(model_prob.prob),
                # Keep the exact stored scalar above for DB attestation, but
                # score event distributions against a normalized baseline.
                # Decimal quantization can otherwise make an otherwise complete
                # event sum to 1.0015 and silently discard prospective evidence.
                "normalized_baseline_probability": float(normalized_baseline),
                "bucket_input_hash": details["input_hash"],
                "input_hash": event_input_hash,
                "event_cohort": {
                    "event_id": event_id,
                    "cohort_ts": cohort_ts.isoformat(),
                    "pipeline_version": pipeline_version,
                    "market_count": len(actual_market_ids),
                    "market_ids": sorted(actual_market_ids),
                    "candidate_total": total,
                    "empirical_total": empirical_total,
                    "baseline_total": baseline_total,
                },
            }
            values.append(
                AccuracyPredictionValue(
                    market_id=market.id,
                    # Exact match is required by the prospective audit join.
                    ts=model_prob.ts,
                    expert=EXPERT,
                    expert_version=EXPERT_VERSION,
                    probability=candidate_partition[market.id],
                    mean_c=Decimal(str(mean_c)).quantize(Decimal("0.0001")),
                    sigma_c=Decimal(str(sigma_c)).quantize(Decimal("0.0001")),
                    training_sample_n=int(details.get("training_samples", 0)),
                    point_in_time_valid=True,
                    distribution=sealed_details,
                    generated_at=generated_at,
                    training_cutoff=input_snapshot_at,
                    baseline_model_prob_id=model_prob.id,
                    input_hash=event_input_hash,
                    experiment_fingerprint=_experiment_fingerprint(
                        EXPERT_VERSION,
                        str(details["baseline_pipeline_version"]),
                    ),
                    evidence_eligible=True,
                )
            )
            values.append(
                AccuracyPredictionValue(
                    market_id=market.id,
                    ts=model_prob.ts,
                    expert=SHRUNK_EXPERT,
                    expert_version=SHRUNK_EXPERT_VERSION,
                    probability=shrunk_partition[market.id],
                    mean_c=Decimal(str(mean_c)).quantize(Decimal("0.0001")),
                    sigma_c=Decimal(str(sigma_c)).quantize(Decimal("0.0001")),
                    training_sample_n=int(details.get("training_samples", 0)),
                    point_in_time_valid=True,
                    distribution={
                        **sealed_details,
                        "method": "production_empirical_weighted_shrinkage",
                        "parent_expert": EXPERT,
                        "parent_expert_version": EXPERT_VERSION,
                        "baseline_probability": float(model_prob.prob),
                        "empirical_probability": normalized_empirical,
                        "empirical_blend_weight": SHRUNK_EMPIRICAL_WEIGHT,
                        "weather_training_truth_source": (
                            "canonical_settlement_actual_then_current_reconciliation_clean_"
                            "daily_actual"
                        ),
                        "truth_source": "prospective_polymarket_resolution",
                        "historical_settlement_eligible": bool(
                            details.get("settlement_shrunk_empirical_eligible", False)
                        ),
                        "applied": False,
                    },
                    generated_at=generated_at,
                    training_cutoff=input_snapshot_at,
                    baseline_model_prob_id=model_prob.id,
                    input_hash=event_input_hash,
                    experiment_fingerprint=_experiment_fingerprint(
                        SHRUNK_EXPERT_VERSION,
                        str(details["baseline_pipeline_version"]),
                    ),
                    evidence_eligible=True,
                )
            )
    return values


async def generate_weighted_ensemble(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            actual_cutoff = await session.scalar(select(func.clock_timestamp()))
            if actual_cutoff is None:
                raise RuntimeError("database did not return a daily-actual training cutoff")
            current_rows_raw = (
                await session.execute(
                    select(ModelProb, Market)
                    .join(Market, ModelProb.market_id == Market.id)
                    .where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                        Market.resolves_at > now,
                    )
                    .distinct(Market.id)
                    .order_by(Market.id, ModelProb.ts.desc())
                )
            ).all()
            current_rows: list[CurrentRow] = [
                (model_prob, market)
                for model_prob, market in current_rows_raw
                if model_prob.ts <= now and now - model_prob.ts <= MAX_BASELINE_AGE
            ]
            if not current_rows:
                logger.info("weighted ensemble collecting: no fresh baseline cohort")
                return 0
            # recompute_probs writes a cohort with one decision timestamp. Using
            # that timestamp as the physical input cutoff makes restarts over
            # the same cohort deterministic and excludes forecasts fetched later.
            input_snapshot_at = max(model_prob.ts for model_prob, _ in current_rows)
            truth_cutoff = min(actual_cutoff, input_snapshot_at)
            historical_rows = (
                await session.execute(
                    _historical_training_statement(
                        earliest_date=now.date() - timedelta(days=HISTORY_DAYS),
                        truth_cutoff=truth_cutoff,
                    )
                )
            ).all()
            active_market_rows = (
                await session.execute(
                    select(Market.id, Market.event_id).where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                        Market.resolves_at > now,
                    )
                )
            ).all()
            expected_market_ids_by_event: dict[str, set[int]] = defaultdict(set)
            for market_id, event_id in active_market_rows:
                expected_market_ids_by_event[event_id].add(market_id)
            resolved_rows_raw = (
                await session.execute(
                    select(ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent)
                    .join(ModelProb, ForecastEvaluation.model_prob_id == ModelProb.id)
                    .join(Market, ForecastEvaluation.market_id == Market.id)
                    .join(
                        GammaOutcomeEvent,
                        ForecastEvaluation.resolution_event_id == GammaOutcomeEvent.id,
                    )
                    .where(
                        ForecastEvaluation.decision_at >= now - timedelta(days=365),
                        ForecastEvaluation.resolution_event_id.is_not(None),
                        ForecastEvaluation.resolution_vector_hash.is_not(None),
                        ForecastEvaluation.resolution_vector_hash
                        == GammaOutcomeEvent.outcome_vector_hash,
                        ForecastEvaluation.decision_at < GammaOutcomeEvent.first_outcome_at,
                        Market.resolution_event_id == GammaOutcomeEvent.id,
                        Market.event_id == GammaOutcomeEvent.event_id,
                        ModelProb.ts <= Market.resolves_at,
                    )
                )
            ).all()
            resolved_rows = [
                (evaluation, model_prob, market, outcome_event)
                for evaluation, model_prob, market, outcome_event in resolved_rows_raw
                if outcome_event is not None
                and forecast_evaluation_evidence_is_valid(
                    evaluation,
                    outcome_event,
                    model_prob,
                )
            ]
            station_ids = {
                market.station_id for _, market in current_rows if market.station_id is not None
            }
            station_ids.update(
                market.station_id
                for _, _, market, _ in resolved_rows
                if market.station_id is not None
            )
            valid_dates = {market.market_date for _, market in current_rows}
            valid_dates.update(market.market_date for _, _, market, _ in resolved_rows)
            forecast_rows = (
                (
                    await session.execute(
                        select(Forecast).where(
                            Forecast.station_id.in_(station_ids),
                            Forecast.valid_date.in_(valid_dates),
                            Forecast.model.in_(MODELS),
                            Forecast.available_at <= input_snapshot_at,
                            Forecast.fetched_at <= input_snapshot_at,
                        )
                    )
                )
                .scalars()
                .all()
            )
        historical_data = [(forecast, actual) for forecast, actual in historical_rows]
        current_data = [(model_prob, market) for model_prob, market in current_rows]
        resolved_data = [
            (evaluation, model_prob, market, outcome_event)
            for evaluation, model_prob, market, outcome_event in resolved_rows
        ]
        cases = _build_cases(historical_data)
        if len(cases) < MINIMUM_TRAINING_SAMPLES:
            logger.info(
                "weighted ensemble challenger collecting: cases=%d/%d",
                len(cases),
                MINIMUM_TRAINING_SAMPLES,
            )
            return 0
        current_scopes = {
            (
                market.station_id,
                horizon_bucket(math.ceil(float(model_prob.horizon_h))),
                market.kind,
            )
            for model_prob, market in current_rows
            if market.station_id is not None
        }
        settlement_cases = _build_settlement_cases(
            resolved_data,
            [forecast for forecast in forecast_rows],
        )
        # These fits are pure Python and remain GIL-bound in asyncio.to_thread().
        # A spawned process keeps minute-critical price/execution/command jobs responsive.
        loop = asyncio.get_running_loop()
        with ProcessPoolExecutor(
            max_workers=1,
            mp_context=get_context("spawn"),
        ) as executor:
            assessment, fits, settlement_assessment = await loop.run_in_executor(
                executor,
                _compute_ensemble_artifacts,
                cases,
                current_scopes,
                input_snapshot_at.date(),
                settlement_cases,
            )
        values = _prediction_values(
            current_data,
            [forecast for forecast in forecast_rows],
            expected_market_ids_by_event,
            fits,
            assessment,
            settlement_assessment,
            input_snapshot_at=input_snapshot_at,
            generated_at=datetime.now(UTC),
        )
        async with sessions.begin() as session:
            written = await upsert_accuracy_predictions(session, values)
        logger.info(
            "historical weighted ensemble: predictions=%d scopes=%d samples=%d "
            "blocks=%d rmse=%.4f->%.4f delta_ci=%s settlement=%s applied=false",
            written,
            len(fits),
            assessment.samples,
            assessment.independent_blocks,
            assessment.baseline_rmse_c,
            assessment.candidate_rmse_c,
            assessment.delta_ci,
            (
                {
                    "events": settlement_assessment.fitted_events,
                    "blocks": settlement_assessment.independent_blocks,
                    "brier": (
                        settlement_assessment.baseline_brier,
                        settlement_assessment.candidate_brier,
                    ),
                    "delta_ci": settlement_assessment.delta_ci,
                    "empirical_brier": settlement_assessment.empirical_candidate_brier,
                    "empirical_delta_ci": settlement_assessment.empirical_delta_ci,
                    "shrunk_empirical_brier": settlement_assessment.shrunk_empirical_brier,
                    "shrunk_empirical_delta_ci": (settlement_assessment.shrunk_empirical_delta_ci),
                }
                if settlement_assessment
                else None
            ),
        )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(generate_weighted_ensemble(settings))


if __name__ == "__main__":
    main()
