from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from sqlalchemy import case, exists, func, select
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ClobBookBatch,
    ClobMarketSnapshot,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    ModelProb,
    Station,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.accuracy import (
    ForecastEvaluationValue,
    upsert_forecast_evaluations,
)
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    gamma_outcome_map,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.forecast_evaluation import (
    DualPricePoint,
    DualPriceTimeline,
    dual_event_market_benchmark,
)

logger = logging.getLogger(__name__)

EVALUATION_MAX_ROWS_PER_RUN = 1000
EVALUATION_SNAPSHOT_BATCH_SIZE = 100


@dataclass(frozen=True, slots=True)
class SelectedProbability:
    id: int
    market_id: int
    ts: datetime
    probability: Decimal
    horizon_h: Decimal
    market_snapshot_id: int
    horizon_bucket: str


def _selected_probability_statement(
    *,
    market_ids: Sequence[int],
    cutoff: datetime,
    now: datetime,
    limit: int = EVALUATION_MAX_ROWS_PER_RUN,
) -> Select[tuple[int, int, datetime, Decimal, Decimal, int, str]]:
    """Select a bounded set of deterministic horizon winners.

    Ranking happens before the evaluation anti-join.  Therefore a retry never
    walks on to the second-best point in a horizon bucket after the winner has
    already been evaluated.
    """

    if not market_ids:
        raise ValueError("market_ids must not be empty")
    if limit <= 0:
        raise ValueError("limit must be positive")

    # ``horizon_bucket(round(hours))`` uses Python's half-to-even rounding.
    # Every boundary anchor is even, so an exact *.5 remains in the lower
    # bucket.  Numeric columns make these inclusive SQL boundaries exact.
    bucket = case(
        (ModelProb.horizon_h <= Decimal("6.5"), "0-6h"),
        (ModelProb.horizon_h <= Decimal("24.5"), "6-24h"),
        (ModelProb.horizon_h <= Decimal("48.5"), "24-48h"),
        else_="48h+",
    )
    anchor = case(
        (ModelProb.horizon_h <= Decimal("6.5"), Decimal("3")),
        (ModelProb.horizon_h <= Decimal("24.5"), Decimal("12")),
        (ModelProb.horizon_h <= Decimal("48.5"), Decimal("36")),
        else_=Decimal("72"),
    )
    ranked = (
        select(
            ModelProb.id.label("model_prob_id"),
            ModelProb.market_id.label("market_id"),
            ModelProb.ts.label("decision_at"),
            ModelProb.prob.label("probability"),
            ModelProb.horizon_h.label("horizon_h"),
            ModelProb.market_snapshot_id.label("market_snapshot_id"),
            bucket.label("horizon_bucket"),
            func.row_number()
            .over(
                partition_by=(ModelProb.market_id, bucket),
                order_by=(
                    func.abs(ModelProb.horizon_h - anchor),
                    ModelProb.id.asc(),
                ),
            )
            .label("selection_rank"),
        )
        .join(Market, Market.id == ModelProb.market_id)
        .join(
            GammaOutcomeEvent,
            (GammaOutcomeEvent.id == Market.resolution_event_id)
            & (GammaOutcomeEvent.event_id == Market.event_id),
        )
        .where(
            ModelProb.market_id.in_(set(market_ids)),
            ModelProb.ts <= now,
            ModelProb.ts <= Market.resolves_at,
            ModelProb.ts < GammaOutcomeEvent.first_outcome_at,
            ModelProb.market_anchor_protocol == CLOB_DUAL_PROTOCOL,
            ModelProb.market_snapshot_id.is_not(None),
            Market.resolves_at >= cutoff,
            GammaOutcomeEvent.fully_resolved_at >= cutoff,
        )
        .subquery()
    )
    return (
        select(
            ranked.c.model_prob_id,
            ranked.c.market_id,
            ranked.c.decision_at,
            ranked.c.probability,
            ranked.c.horizon_h,
            ranked.c.market_snapshot_id,
            ranked.c.horizon_bucket,
        )
        .where(
            ranked.c.selection_rank == 1,
            ~exists(
                select(ForecastEvaluation.id).where(
                    ForecastEvaluation.model_prob_id == ranked.c.model_prob_id,
                    ForecastEvaluation.resolution_event_id.is_not(None),
                    ForecastEvaluation.resolution_vector_hash.is_not(None),
                )
            ),
        )
        .order_by(ranked.c.model_prob_id)
        .limit(limit)
    )


def _target_batch_snapshot_statement(
    *,
    target_snapshot_ids: Sequence[int],
    sibling_market_ids: Sequence[int],
) -> Select[
    tuple[
        int,
        int,
        int,
        datetime,
        str,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        bool,
    ]
]:
    """Project only same-batch benchmark fields, never raw CLOB payloads."""

    if not target_snapshot_ids or not sibling_market_ids:
        raise ValueError("target snapshots and sibling markets must not be empty")
    target_batch_ids = select(ClobMarketSnapshot.batch_id).where(
        ClobMarketSnapshot.id.in_(set(target_snapshot_ids))
    )
    return (
        select(
            ClobMarketSnapshot.id.label("id"),
            ClobMarketSnapshot.batch_id.label("batch_id"),
            ClobMarketSnapshot.market_id.label("market_id"),
            ClobBookBatch.first_seen_at.label("observed_at"),
            ClobMarketSnapshot.record_hash.label("record_hash"),
            ClobMarketSnapshot.binary_yes_probability.label("binary_yes_probability"),
            ClobMarketSnapshot.yes_midpoint.label("yes_midpoint"),
            ClobMarketSnapshot.yes_best_bid.label("yes_best_bid"),
            ClobMarketSnapshot.yes_best_ask.label("yes_best_ask"),
            ClobMarketSnapshot.no_midpoint.label("no_midpoint"),
            ClobMarketSnapshot.no_best_bid.label("no_best_bid"),
            ClobMarketSnapshot.no_best_ask.label("no_best_ask"),
            ClobMarketSnapshot.eligible.label("eligible"),
        )
        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
        .where(
            ClobMarketSnapshot.batch_id.in_(target_batch_ids),
            ClobMarketSnapshot.market_id.in_(set(sibling_market_ids)),
            ClobBookBatch.evidence_protocol == CLOB_DUAL_PROTOCOL,
        )
    )


def _close_snapshot_statement(
    *,
    market_ids: Sequence[int],
) -> Select[
    tuple[
        int,
        int,
        int,
        datetime,
        str,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        Decimal | None,
        bool,
    ]
]:
    """Return one last eligible pre-resolution quote per requested market."""

    if not market_ids:
        raise ValueError("market_ids must not be empty")
    ranked = (
        select(
            ClobMarketSnapshot.id.label("id"),
            ClobMarketSnapshot.batch_id.label("batch_id"),
            ClobMarketSnapshot.market_id.label("market_id"),
            ClobBookBatch.first_seen_at.label("observed_at"),
            ClobMarketSnapshot.record_hash.label("record_hash"),
            ClobMarketSnapshot.binary_yes_probability.label("binary_yes_probability"),
            ClobMarketSnapshot.yes_midpoint.label("yes_midpoint"),
            ClobMarketSnapshot.yes_best_bid.label("yes_best_bid"),
            ClobMarketSnapshot.yes_best_ask.label("yes_best_ask"),
            ClobMarketSnapshot.no_midpoint.label("no_midpoint"),
            ClobMarketSnapshot.no_best_bid.label("no_best_bid"),
            ClobMarketSnapshot.no_best_ask.label("no_best_ask"),
            ClobMarketSnapshot.eligible.label("eligible"),
            func.row_number()
            .over(
                partition_by=ClobMarketSnapshot.market_id,
                order_by=(
                    ClobBookBatch.first_seen_at.desc(),
                    ClobMarketSnapshot.id.desc(),
                ),
            )
            .label("selection_rank"),
        )
        .join(ClobBookBatch, ClobBookBatch.id == ClobMarketSnapshot.batch_id)
        .join(Market, Market.id == ClobMarketSnapshot.market_id)
        .where(
            ClobMarketSnapshot.market_id.in_(set(market_ids)),
            ClobMarketSnapshot.eligible.is_(True),
            ClobBookBatch.evidence_protocol == CLOB_DUAL_PROTOCOL,
            ClobBookBatch.first_seen_at <= Market.resolves_at,
        )
        .subquery()
    )
    return select(
        ranked.c.id,
        ranked.c.batch_id,
        ranked.c.market_id,
        ranked.c.observed_at,
        ranked.c.record_hash,
        ranked.c.binary_yes_probability,
        ranked.c.yes_midpoint,
        ranked.c.yes_best_bid,
        ranked.c.yes_best_ask,
        ranked.c.no_midpoint,
        ranked.c.no_best_bid,
        ranked.c.no_best_ask,
        ranked.c.eligible,
    ).where(ranked.c.selection_rank == 1)


def _selected_probability(row: RowMapping) -> SelectedProbability:
    return SelectedProbability(
        id=int(row["model_prob_id"]),
        market_id=int(row["market_id"]),
        ts=cast(datetime, row["decision_at"]),
        probability=cast(Decimal, row["probability"]),
        horizon_h=cast(Decimal, row["horizon_h"]),
        market_snapshot_id=int(row["market_snapshot_id"]),
        horizon_bucket=str(row["horizon_bucket"]),
    )


def _optional_decimal_float(value: object) -> float | None:
    return None if value is None else float(cast(Decimal, value))


def _dual_price_point(row: RowMapping) -> DualPricePoint:
    return DualPricePoint(
        id=int(row["id"]),
        batch_id=int(row["batch_id"]),
        market_id=int(row["market_id"]),
        observed_at=cast(datetime, row["observed_at"]),
        record_hash=str(row["record_hash"]),
        binary_yes_probability=_optional_decimal_float(row["binary_yes_probability"]),
        yes_midpoint=_optional_decimal_float(row["yes_midpoint"]),
        yes_best_bid=_optional_decimal_float(row["yes_best_bid"]),
        yes_best_ask=_optional_decimal_float(row["yes_best_ask"]),
        no_midpoint=_optional_decimal_float(row["no_midpoint"]),
        no_best_bid=_optional_decimal_float(row["no_best_bid"]),
        no_best_ask=_optional_decimal_float(row["no_best_ask"]),
        eligible=bool(row["eligible"]),
    )


async def _load_selected_probabilities(
    session: AsyncSession,
    *,
    market_ids: Sequence[int],
    cutoff: datetime,
    now: datetime,
) -> list[SelectedProbability]:
    rows = (
        (
            await session.execute(
                _selected_probability_statement(
                    market_ids=market_ids,
                    cutoff=cutoff,
                    now=now,
                )
            )
        )
        .mappings()
        .all()
    )
    return [_selected_probability(row) for row in rows]


async def _load_price_timeline(
    session: AsyncSession,
    *,
    probabilities: Sequence[SelectedProbability],
    sibling_market_ids: Sequence[int],
) -> DualPriceTimeline:
    target_rows = (
        (
            await session.execute(
                _target_batch_snapshot_statement(
                    target_snapshot_ids=[
                        probability.market_snapshot_id for probability in probabilities
                    ],
                    sibling_market_ids=sibling_market_ids,
                )
            )
        )
        .mappings()
        .all()
    )
    close_rows = (
        (
            await session.execute(
                _close_snapshot_statement(
                    market_ids=[probability.market_id for probability in probabilities]
                )
            )
        )
        .mappings()
        .all()
    )
    points = {
        point.id: point for point in (_dual_price_point(row) for row in (*target_rows, *close_rows))
    }
    return DualPriceTimeline(list(points.values()))


def _evaluation_values(
    *,
    probabilities: Sequence[SelectedProbability],
    markets: Mapping[int, tuple[Market, Station]],
    outcome_events: Mapping[str, GammaOutcomeEvent],
    outcomes_by_market: Mapping[int, bool],
    event_market_ids: Mapping[str, list[int]],
    timeline: DualPriceTimeline,
    evaluated_at: datetime,
) -> list[ForecastEvaluationValue]:
    values: list[ForecastEvaluationValue] = []
    for point in probabilities:
        market, station = markets[point.market_id]
        resolution_event = outcome_events[market.event_id]
        outcome = outcomes_by_market[market.id]
        benchmark = dual_event_market_benchmark(
            event_market_ids=event_market_ids[market.event_id],
            target_market_id=market.id,
            target_snapshot_id=point.market_snapshot_id,
            at=point.ts,
            prices=timeline,
        )
        close = timeline.latest_at(market.id, market.resolves_at)
        market_probability = benchmark.normalized_probability
        probability = float(point.probability)
        values.append(
            ForecastEvaluationValue(
                market_id=market.id,
                model_prob_id=point.id,
                decision_at=point.ts,
                independent_sample_key=(
                    f"{station.icao}:{market.market_date.isoformat()}:{market.kind.value}:"
                    f"{point.horizon_bucket}"
                ),
                model_probability=point.probability,
                horizon_market_probability=(
                    Decimal(str(market_probability)) if market_probability is not None else None
                ),
                market_snapshot_at=(
                    benchmark.own_price.observed_at if benchmark.own_price else None
                ),
                executable_bid=(
                    Decimal(str(benchmark.own_price.yes_best_bid))
                    if benchmark.own_price and benchmark.own_price.yes_best_bid is not None
                    else None
                ),
                executable_ask=(
                    Decimal(str(benchmark.own_price.yes_best_ask))
                    if benchmark.own_price and benchmark.own_price.yes_best_ask is not None
                    else None
                ),
                pre_resolution_close=(
                    Decimal(str(close.binary_yes_probability))
                    if close and close.binary_yes_probability is not None
                    else None
                ),
                close_snapshot_at=close.observed_at if close else None,
                event_probability_total=(
                    Decimal(str(benchmark.raw_total)) if benchmark.raw_total is not None else None
                ),
                event_overround=(
                    Decimal(str(benchmark.overround)) if benchmark.overround is not None else None
                ),
                resolved_outcome=outcome,
                resolution_event_id=resolution_event.id,
                resolution_vector_hash=resolution_event.outcome_vector_hash,
                model_brier=Decimal(str((probability - float(outcome)) ** 2)),
                market_brier=(
                    Decimal(str((market_probability - float(outcome)) ** 2))
                    if market_probability is not None
                    else None
                ),
                evaluated_at=evaluated_at,
                details={
                    "horizon_bucket": point.horizon_bucket,
                    "market_snapshot_complete": benchmark.complete,
                    "resolution_evidence_protocol": resolution_event.evidence_protocol,
                    "resolution_first_outcome_at": (resolution_event.first_outcome_at.isoformat()),
                    "market_benchmark_protocol": CLOB_DUAL_PROTOCOL,
                },
                market_benchmark_protocol=CLOB_DUAL_PROTOCOL,
                market_snapshot_id=(benchmark.own_price.id if benchmark.own_price else None),
                market_snapshot_hash=(
                    benchmark.own_price.record_hash if benchmark.own_price else None
                ),
                close_snapshot_id=close.id if close else None,
                close_snapshot_hash=close.record_hash if close else None,
            )
        )
    return values


async def evaluate_forecasts(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    cutoff = now - timedelta(days=365)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            outcome_events_raw = (
                (
                    await session.execute(
                        select(GammaOutcomeEvent).where(
                            GammaOutcomeEvent.fully_resolved_at >= cutoff
                        )
                    )
                )
                .scalars()
                .all()
            )
            outcome_events: dict[str, GammaOutcomeEvent] = {}
            outcomes_by_market: dict[int, bool] = {}
            event_market_ids: dict[str, list[int]] = {}
            for event in outcome_events_raw:
                try:
                    event_outcomes = gamma_outcome_map(event)
                except GammaOutcomeIntegrityError:
                    logger.exception(
                        "excluding invalid Gamma outcome evidence: event_id=%s",
                        event.event_id,
                    )
                    continue
                outcome_events[event.event_id] = event
                event_market_ids[event.event_id] = sorted(event_outcomes)
                outcomes_by_market.update(event_outcomes)
            if not outcome_events:
                return 0

            market_rows = (
                await session.execute(
                    select(Market, Station)
                    .join(Station, Market.station_id == Station.id)
                    .where(
                        Market.event_id.in_(outcome_events),
                        Market.id.in_(outcomes_by_market),
                        Market.resolution_event_id.is_not(None),
                        Market.resolves_at >= cutoff,
                    )
                )
            ).all()
            if not market_rows:
                return 0
            markets = {
                market.id: (market, station)
                for market, station in market_rows
                if market.resolution_event_id == outcome_events[market.event_id].id
            }
            if not markets:
                return 0
            selected = await _load_selected_probabilities(
                session,
                market_ids=list(markets),
                cutoff=cutoff,
                now=now,
            )
        if not selected:
            return 0

        written_total = 0
        for offset in range(0, len(selected), EVALUATION_SNAPSHOT_BATCH_SIZE):
            probability_batch = selected[offset : offset + EVALUATION_SNAPSHOT_BATCH_SIZE]
            sibling_ids = sorted(
                {
                    market_id
                    for point in probability_batch
                    for market_id in event_market_ids[markets[point.market_id][0].event_id]
                }
            )
            async with session_factory() as session:
                timeline = await _load_price_timeline(
                    session,
                    probabilities=probability_batch,
                    sibling_market_ids=sibling_ids,
                )
            values = _evaluation_values(
                probabilities=probability_batch,
                markets=markets,
                outcome_events=outcome_events,
                outcomes_by_market=outcomes_by_market,
                event_market_ids=event_market_ids,
                timeline=timeline,
                evaluated_at=now,
            )
            async with session_factory.begin() as session:
                written_total += await upsert_forecast_evaluations(session, values)

        logger.info(
            "point-in-time forecast evaluation complete: selected=%d rows=%d max_per_run=%d",
            len(selected),
            written_total,
            EVALUATION_MAX_ROWS_PER_RUN,
        )
        return written_total


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(evaluate_forecasts(settings))


if __name__ == "__main__":
    main()
