from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import ForecastEvaluation, GammaOutcomeEvent, Market, ModelProb
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    SettlementCalibratorValue,
    forecast_evaluation_cohort_is_complete,
    forecast_evaluation_evidence_is_valid,
    upsert_settlement_calibrator,
)
from stratus.repositories.analytics import (
    current_probability_pipeline_cohort,
    probability_pipeline_version,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.settlement_calibration import (
    CalibrationAssessment,
    SettlementEventCase,
    blocked_oof_assessment,
)

logger = logging.getLogger(__name__)


def _decimal(value: float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


FormalRow = tuple[ForecastEvaluation, GammaOutcomeEvent]


def _build_cases(rows: list[FormalRow]) -> list[SettlementEventCase]:
    grouped: dict[tuple[str, int, datetime], list[FormalRow]] = defaultdict(list)
    for evaluation, outcome_event in rows:
        if evaluation.resolution_event_id is None:
            continue
        grouped[
            (
                evaluation.independent_sample_key,
                evaluation.resolution_event_id,
                evaluation.decision_at,
            )
        ].append((evaluation, outcome_event))
    cases: list[SettlementEventCase] = []
    for (sample_key, resolution_event_id, decision_at), event_rows in grouped.items():
        if not forecast_evaluation_cohort_is_complete(event_rows):
            continue
        ordered = sorted(
            (evaluation for evaluation, _outcome_event in event_rows),
            key=lambda row: row.market_id,
        )
        probabilities = tuple(float(row.model_probability) for row in ordered)
        outcomes = tuple(row.resolved_outcome for row in ordered)
        total = sum(probabilities)
        if len(ordered) < 2 or sum(outcomes) != 1 or not 0.8 <= total <= 1.2:
            continue
        cases.append(
            SettlementEventCase(
                key=f"{sample_key}:{resolution_event_id}:{decision_at.isoformat()}",
                probabilities=probabilities,
                outcomes=outcomes,
            )
        )
    return sorted(cases, key=lambda case: case.key)


def _eligible(assessment: CalibrationAssessment, settings: Settings) -> bool:
    return bool(
        assessment.blocks >= settings.settlement_calibrator_min_blocks
        and assessment.delta_ci is not None
        and assessment.delta_ci[1] < 0
        and assessment.calibrated_brier < assessment.baseline_brier
        and assessment.calibrated_ece
        <= assessment.baseline_ece + settings.settlement_calibrator_ece_tolerance
    )


async def fit_settlement_calibration(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            current_pipeline = await current_probability_pipeline_cohort(
                session,
                market_anchor_protocol=CLOB_DUAL_PROTOCOL,
            )
            rows_raw = (
                await session.execute(
                    select(ForecastEvaluation, GammaOutcomeEvent, ModelProb)
                    .join(
                        GammaOutcomeEvent,
                        ForecastEvaluation.resolution_event_id == GammaOutcomeEvent.id,
                    )
                    .join(ModelProb, ForecastEvaluation.model_prob_id == ModelProb.id)
                    .join(Market, ForecastEvaluation.market_id == Market.id)
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
                        ModelProb.market_anchor_protocol == CLOB_DUAL_PROTOCOL,
                    )
                )
            ).all()
            rows: list[FormalRow] = [
                (evaluation, outcome_event)
                for evaluation, outcome_event, model_prob in rows_raw
                if outcome_event is not None
                and current_pipeline is not None
                and probability_pipeline_version(model_prob.components)
                == current_pipeline
                and forecast_evaluation_evidence_is_valid(
                    evaluation,
                    outcome_event,
                    model_prob,
                )
            ]
        cases = _build_cases(list(rows))
        assessment: CalibrationAssessment | None = None
        if len(cases) >= 20:
            assessment = blocked_oof_assessment(
                cases,
                l2=settings.settlement_calibrator_l2,
            )
        value = SettlementCalibratorValue(
            scope_key="global:all:all",
            station_id=None,
            horizon_bucket=None,
            kind=None,
            alpha=Decimal(str(assessment.calibrator.alpha if assessment else 1.0)),
            beta=Decimal(str(assessment.calibrator.beta if assessment else 0.0)),
            training_blocks=len(cases),
            training_buckets=sum(len(case.probabilities) for case in cases),
            oof_baseline_brier=(
                _decimal(assessment.baseline_brier) if assessment else None
            ),
            oof_calibrated_brier=(
                _decimal(assessment.calibrated_brier) if assessment else None
            ),
            oof_baseline_ece=_decimal(assessment.baseline_ece) if assessment else None,
            oof_calibrated_ece=(
                _decimal(assessment.calibrated_ece) if assessment else None
            ),
            delta_brier_ci_low=(
                _decimal(assessment.delta_ci[0])
                if assessment and assessment.delta_ci
                else None
            ),
            delta_brier_ci_high=(
                _decimal(assessment.delta_ci[1])
                if assessment and assessment.delta_ci
                else None
            ),
            eligible=_eligible(assessment, settings) if assessment else False,
            trained_through=now,
            details={
                "method": "partition_beta_alpha_logp_beta_log1mp",
                "validation": "deterministic_blocked_5_fold",
                "unit": "independent_station_day_kind_horizon",
                "minimum_blocks": settings.settlement_calibrator_min_blocks,
                "shadow_only_until_eligible": True,
                "market_benchmark_protocol": CLOB_DUAL_PROTOCOL,
                "probability_pipeline_cohort": current_pipeline,
            },
        )
        async with session_factory.begin() as session:
            calibrator_id = await upsert_settlement_calibrator(session, value)
        logger.info(
            "settlement calibration fit: id=%d blocks=%d eligible=%s brier=%s->%s",
            calibrator_id,
            len(cases),
            value.eligible,
            value.oof_baseline_brier,
            value.oof_calibrated_brier,
        )
        return calibrator_id


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fit_settlement_calibration(settings))


if __name__ == "__main__":
    main()
