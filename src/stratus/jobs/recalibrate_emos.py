from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from stratus.config import Settings, get_settings
from stratus.db.models import (
    DailyActual,
    HistoricalForecast,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.calibration import CalibrationValue, upsert_calibrations
from stratus.repositories.daily_actual_fallback import daily_actual_fallback_eligible
from stratus.repositories.training_truth import (
    canonical_settlement_actuals,
    eligible_settlement_actual_exists,
)
from stratus.services.emos import (
    EmosFit,
    HorizonSigmaFit,
    fit_emos,
    fit_horizon_sigma,
    horizon_bucket,
)

logger = logging.getLogger(__name__)


def _decimal(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.00000001"))


def _historical_training_statement(
    *,
    earliest_date: date,
    truth_cutoff: datetime,
) -> Select[tuple[HistoricalForecast, Decimal]]:
    """Select temporally eligible exact truth, then undisputed METAR fallback."""

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
            HistoricalForecast.valid_date >= earliest_date,
            HistoricalForecast.fetched_at <= truth_cutoff,
            or_(
                HistoricalForecast.first_seen_at.is_(None),
                HistoricalForecast.first_seen_at <= truth_cutoff,
            ),
            actual_temp.is_not(None),
        )
    )


async def recalibrate_emos(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    now: datetime | None = None,
) -> int:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    cutoff = now.date() - timedelta(days=settings.calibration_window_days)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            actual_cutoff = await session.scalar(select(func.clock_timestamp()))
            if actual_cutoff is None:
                raise RuntimeError("database did not return a daily-actual training cutoff")
            truth_cutoff = min(actual_cutoff, now)
            rows = (
                await session.execute(
                    _historical_training_statement(
                        earliest_date=cutoff,
                        truth_cutoff=truth_cutoff,
                    )
                )
            ).all()
        grouped: dict[tuple[int, str, str, WeatherKind], list[tuple[float, float, int]]] = (
            defaultdict(list)
        )
        for forecast, actual_temp in rows:
            key = (
                forecast.station_id,
                forecast.model,
                horizon_bucket(forecast.horizon_h),
                forecast.kind,
            )
            grouped[key].append((float(forecast.temp_c), float(actual_temp), forecast.horizon_h))

        fitted: dict[tuple[int, str, str, WeatherKind], tuple[EmosFit, float]] = {}
        skipped = 0
        for (station_id, model, bucket, kind), samples in grouped.items():
            if len(samples) < settings.calibration_min_samples:
                skipped += 1
                continue
            try:
                fit = fit_emos([(raw, actual) for raw, actual, _ in samples])
            except ValueError:
                skipped += 1
                continue
            mean_horizon = sum(horizon for _, _, horizon in samples) / len(samples)
            fitted[(station_id, model, bucket, kind)] = (fit, mean_horizon)

        sigma_points: dict[tuple[int, str, WeatherKind], list[tuple[float, float, int]]] = (
            defaultdict(list)
        )
        for (station_id, model, _bucket, kind), (fit, mean_horizon) in fitted.items():
            sigma_points[(station_id, model, kind)].append(
                (mean_horizon, fit.sigma_c, fit.sample_n)
            )
        sigma_curves: dict[tuple[int, str, WeatherKind], HorizonSigmaFit] = {}
        for sigma_key, points in sigma_points.items():
            try:
                sigma_curves[sigma_key] = fit_horizon_sigma(points)
            except ValueError:
                continue

        values = []
        for (station_id, model, bucket, kind), (fit, _mean_horizon) in fitted.items():
            sigma_curve = sigma_curves.get((station_id, model, kind))
            if sigma_curve is None:
                skipped += 1
                continue
            values.append(
                CalibrationValue(
                    station_id=station_id,
                    model=model,
                    horizon_bucket=bucket,
                    kind=kind,
                    bias_c=_decimal(fit.bias_c),
                    sigma_c=_decimal(fit.sigma_c),
                    emos_a=_decimal(fit.intercept),
                    emos_b=_decimal(fit.slope),
                    emos_c=_decimal(sigma_curve.intercept),
                    emos_d=_decimal(sigma_curve.slope),
                    sample_n=fit.sample_n,
                    updated_at=now,
                )
            )
        async with session_factory.begin() as session:
            written = await upsert_calibrations(session, values)
        logger.info(
            "EMOS recalibration complete: matched_samples=%d fits=%d skipped_groups=%d",
            len(rows),
            written,
            skipped,
        )
        return written


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(recalibrate_emos(settings))


if __name__ == "__main__":
    main()
