from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import DailyActual, HistoricalForecast, WeatherKind
from stratus.db.session import session_factory_scope
from stratus.repositories.calibration import CalibrationValue, upsert_calibrations
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


async def recalibrate_emos(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    cutoff = now.date() - timedelta(days=settings.calibration_window_days)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(HistoricalForecast, DailyActual.temp_c)
                    .join(
                        DailyActual,
                        and_(
                            DailyActual.station_id == HistoricalForecast.station_id,
                            DailyActual.date == HistoricalForecast.valid_date,
                            DailyActual.kind == HistoricalForecast.kind,
                        ),
                    )
                    .where(HistoricalForecast.valid_date >= cutoff)
                )
            ).all()
        grouped: dict[
            tuple[int, str, str, WeatherKind], list[tuple[float, float, int]]
        ] = defaultdict(list)
        for forecast, actual_temp in rows:
            key = (
                forecast.station_id,
                forecast.model,
                horizon_bucket(forecast.horizon_h),
                forecast.kind,
            )
            grouped[key].append(
                (float(forecast.temp_c), float(actual_temp), forecast.horizon_h)
            )

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

        sigma_points: dict[
            tuple[int, str, WeatherKind], list[tuple[float, float, int]]
        ] = defaultdict(list)
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
