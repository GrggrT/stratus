from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    AccuracyPrediction,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    MarketStatus,
    ModelProb,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    AccuracyPredictionIntegrityError,
    AccuracyPredictionValue,
    forecast_evaluation_cohort_is_complete,
    forecast_evaluation_evidence_is_valid,
    upsert_accuracy_predictions,
)
from stratus.services.censored_probability import (
    CensoredEventCase,
    CensoredGaussianAssessment,
    blocked_oof_censored_assessment,
)
from stratus.services.probability import Bucket
from stratus.services.sealed_accuracy import seal_legacy_accuracy_predictions

logger = logging.getLogger(__name__)
EXPERT = "censored_gaussian"
EXPERT_VERSION = "interval-censored-gaussian-v1"
MIN_PROSPECTIVE_BLOCKS = 60
HORIZON_ANCHORS = {"0-6h": 3.0, "6-24h": 12.0, "24-48h": 36.0, "48h+": 72.0}


def _blend_distribution(model_prob: ModelProb) -> tuple[float, float] | None:
    components = model_prob.components
    if not isinstance(components, dict):
        return None
    blend = components.get("blend")
    if not isinstance(blend, dict):
        return None
    mean_c = blend.get("mean_c")
    sigma_c = blend.get("sigma_c")
    if not isinstance(mean_c, int | float) or not isinstance(sigma_c, int | float):
        return None
    if float(sigma_c) <= 0:
        return None
    return float(mean_c), float(sigma_c)


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


def _build_censored_cases(
    rows: list[tuple[ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent]],
) -> list[CensoredEventCase]:
    snapshots: dict[
        tuple[str, int, datetime],
        list[tuple[ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent]],
    ] = defaultdict(list)
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

    best_by_event: dict[
        tuple[str, int],
        tuple[float, CensoredEventCase],
    ] = {}
    for (sample_key, resolution_event_id, _ts), snapshot_rows in snapshots.items():
        if any(row[0].market_id != row[2].id for row in snapshot_rows) or not (
            forecast_evaluation_cohort_is_complete(
                [(row[0], row[3]) for row in snapshot_rows]
            )
        ):
            continue
        ordered = sorted(snapshot_rows, key=lambda row: _bucket_sort_key(row[2]))
        if len(ordered) < 2 or sum(row[0].resolved_outcome for row in ordered) != 1:
            continue
        distributions = [_blend_distribution(row[1]) for row in ordered]
        if any(distribution is None for distribution in distributions):
            continue
        first = distributions[0]
        assert first is not None
        if any(
            distribution is not None
            and (
                abs(distribution[0] - first[0]) > 0.01
                or abs(distribution[1] - first[1]) > 0.01
            )
            for distribution in distributions[1:]
        ):
            continue
        horizon_bucket = ordered[0][0].details.get("horizon_bucket")
        if not isinstance(horizon_bucket, str) or horizon_bucket not in HORIZON_ANCHORS:
            continue
        average_horizon = sum(float(row[1].horizon_h) for row in ordered) / len(ordered)
        distance = abs(average_horizon - HORIZON_ANCHORS[horizon_bucket])
        case = CensoredEventCase(
            key=f"{sample_key}:{snapshot_rows[0][3].event_id}",
            mean_c=first[0],
            sigma_c=first[1],
            buckets=tuple(_bucket(row[2]) for row in ordered),
            baseline_probabilities=tuple(float(row[0].model_probability) for row in ordered),
            outcomes=tuple(row[0].resolved_outcome for row in ordered),
        )
        event_key = (sample_key, resolution_event_id)
        previous = best_by_event.get(event_key)
        if previous is None or distance < previous[0]:
            best_by_event[event_key] = (distance, case)
    return sorted((item[1] for item in best_by_event.values()), key=lambda case: case.key)


def _current_prediction_values(
    rows: list[tuple[ModelProb, Market]],
    assessment: CensoredGaussianAssessment,
) -> list[AccuracyPredictionValue]:
    grouped: dict[str, list[tuple[ModelProb, Market]]] = defaultdict(list)
    for model_prob, market in rows:
        grouped[market.event_id].append((model_prob, market))

    values: list[AccuracyPredictionValue] = []
    fit = assessment.fit
    oof_eligible = bool(
        assessment.blocks >= MIN_PROSPECTIVE_BLOCKS
        and assessment.delta_ci is not None
        and assessment.delta_ci[1] < 0
        and assessment.calibrated_brier < assessment.baseline_brier
    )
    for event_rows in grouped.values():
        ordered = sorted(event_rows, key=lambda row: _bucket_sort_key(row[1]))
        if len(ordered) < 2:
            continue
        distributions = [_blend_distribution(row[0]) for row in ordered]
        if any(distribution is None for distribution in distributions):
            continue
        first = distributions[0]
        assert first is not None
        if any(
            distribution is not None
            and (
                abs(distribution[0] - first[0]) > 0.01
                or abs(distribution[1] - first[1]) > 0.01
            )
            for distribution in distributions[1:]
        ):
            continue
        buckets = tuple(_bucket(market) for _, market in ordered)
        probabilities = fit.probabilities(buckets, first[0], first[1])
        for probability, (model_prob, market) in zip(
            probabilities,
            ordered,
            strict=True,
        ):
            distribution: dict[str, Any] = {
                "method": "interval_censored_winning_bucket",
                "baseline_probability": float(model_prob.prob),
                "bias_c": fit.bias_c,
                "sigma_scale": fit.sigma_scale,
                "oof_baseline_brier": assessment.baseline_brier,
                "oof_calibrated_brier": assessment.calibrated_brier,
                "oof_delta_ci_low": assessment.delta_ci[0] if assessment.delta_ci else None,
                "oof_delta_ci_high": assessment.delta_ci[1] if assessment.delta_ci else None,
                "historical_oof_eligible": oof_eligible,
                "minimum_prospective_blocks": MIN_PROSPECTIVE_BLOCKS,
                "applied": False,
                "source_truth": "polymarket_winning_bucket_only",
            }
            values.append(
                AccuracyPredictionValue(
                    market_id=market.id,
                    ts=model_prob.ts,
                    expert=EXPERT,
                    expert_version=EXPERT_VERSION,
                    probability=Decimal(str(probability)).quantize(Decimal("0.000001")),
                    mean_c=Decimal(str(first[0] + fit.bias_c)).quantize(Decimal("0.0001")),
                    sigma_c=Decimal(str(first[1] * fit.sigma_scale)).quantize(
                        Decimal("0.0001")
                    ),
                    training_sample_n=assessment.blocks,
                    point_in_time_valid=True,
                    distribution=distribution,
                )
            )
    return values


async def generate_censored_challenger(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            training_rows_raw = (
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
                        ForecastEvaluation.decision_at
                        < GammaOutcomeEvent.first_outcome_at,
                        Market.resolution_event_id == GammaOutcomeEvent.id,
                        Market.event_id == GammaOutcomeEvent.event_id,
                        ModelProb.ts <= Market.resolves_at,
                    )
                )
            ).all()
            training_rows = [
                (evaluation, model_prob, market, outcome_event)
                for evaluation, model_prob, market, outcome_event in training_rows_raw
                if outcome_event is not None
                and forecast_evaluation_evidence_is_valid(
                    evaluation,
                    outcome_event,
                    model_prob,
                )
            ]
            current_rows = (
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
            current_event_ids = {market.event_id for _model_prob, market in current_rows}
            active_markets = (
                await session.execute(
                    select(Market).where(Market.event_id.in_(current_event_ids))
                )
            ).scalars().all()
        cases = _build_censored_cases(
            [
                (evaluation, model_prob, market, outcome_event)
                for evaluation, model_prob, market, outcome_event in training_rows
            ]
        )
        if len(cases) < 20:
            logger.info(
                "censored Gaussian challenger collecting: blocks=%d/20",
                len(cases),
            )
            return 0
        # The deterministic blocked OOF grid search is CPU-bound. Running it
        # on the scheduler event loop can make minute-critical price and
        # execution jobs miss their deadlines, so isolate it in a worker
        # thread while the async runtime remains responsive.
        assessment = await asyncio.to_thread(blocked_oof_censored_assessment, cases)
        legacy_values = _current_prediction_values(
            [(model_prob, market) for model_prob, market in current_rows],
            assessment,
        )
        values = seal_legacy_accuracy_predictions(
            legacy_values,
            [model_prob for model_prob, _market in current_rows],
            list(active_markets),
            settings,
            training_cutoff=now,
        )
        if values:
            async with sessions() as session:
                existing_keys = set(
                    (
                        await session.execute(
                            select(
                                AccuracyPrediction.market_id,
                                AccuracyPrediction.ts,
                                AccuracyPrediction.expert,
                                AccuracyPrediction.expert_version,
                            ).where(
                                AccuracyPrediction.market_id.in_(
                                    value.market_id for value in values
                                ),
                                AccuracyPrediction.expert == EXPERT,
                            )
                        )
                    ).all()
                )
            values_by_cohort: dict[
                tuple[str, str, str, str], list[AccuracyPredictionValue]
            ] = defaultdict(list)
            for value in values:
                event_cohort = value.distribution.get("event_cohort")
                if not isinstance(event_cohort, dict):
                    raise AccuracyPredictionIntegrityError(
                        "sealed censored prediction is missing event cohort"
                    )
                event_id = event_cohort.get("event_id")
                cohort_ts = event_cohort.get("cohort_ts")
                if not isinstance(event_id, str) or not isinstance(cohort_ts, str):
                    raise AccuracyPredictionIntegrityError(
                        "sealed censored prediction has invalid event cohort"
                    )
                values_by_cohort[
                    (value.expert, value.expert_version, event_id, cohort_ts)
                ].append(value)

            new_values: list[AccuracyPredictionValue] = []
            for cohort, cohort_values in values_by_cohort.items():
                present = [
                    value
                    for value in cohort_values
                    if (
                        value.market_id,
                        value.ts,
                        value.expert,
                        value.expert_version,
                    )
                    in existing_keys
                ]
                if present and len(present) != len(cohort_values):
                    raise AccuracyPredictionIntegrityError(
                        f"partial sealed censored cohort already exists for {cohort}"
                    )
                if not present:
                    new_values.extend(cohort_values)
            values = new_values
        async with sessions.begin() as session:
            written = await upsert_accuracy_predictions(session, values)
        logger.info(
            "censored Gaussian challenger: predictions=%d blocks=%d bias=%+.3fC "
            "sigma_scale=%.3f oof_brier=%.5f->%.5f ci=%s applied=false",
            written,
            assessment.blocks,
            assessment.fit.bias_c,
            assessment.fit.sigma_scale,
            assessment.baseline_brier,
            assessment.calibrated_brier,
            assessment.delta_ci,
        )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(generate_censored_challenger(settings))


if __name__ == "__main__":
    main()
