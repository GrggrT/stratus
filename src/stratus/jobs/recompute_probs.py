from __future__ import annotations

import asyncio
import logging
import math
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    Calibration,
    DailyActual,
    Forecast,
    Market,
    MarketPrice,
    MarketStatus,
    ModelProb,
    Station,
    WeatherKind,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.model_probs import ModelProbabilityValue, upsert_model_probs
from stratus.services.emos import horizon_bucket
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
    renormalize_event,
    shrink_to_market,
)

logger = logging.getLogger(__name__)

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
    target_index = BUCKET_ORDER.index(target_bucket)
    candidates = sorted(
        (
            (abs(BUCKET_ORDER.index(bucket) - target_index), calibration)
            for (candidate_station, candidate_model, bucket, candidate_kind), calibration
            in calibrations.items()
            if candidate_station == station_id
            and candidate_model == model
            and candidate_kind == kind
        ),
        key=lambda item: item[0],
    )
    if not candidates:
        return CalibrationParams(
            bias_c=0.0,
            sigma_c=2.0,
            intercept=0.0,
            slope=1.0,
            sigma_intercept=2.0,
            sigma_slope=0.0,
            source_bucket="default",
        )
    calibration = candidates[0][1]
    return CalibrationParams(
        bias_c=float(calibration.bias_c),
        sigma_c=float(calibration.sigma_c),
        intercept=float(calibration.emos_a),
        slope=float(calibration.emos_b),
        sigma_intercept=float(calibration.emos_c),
        sigma_slope=float(calibration.emos_d),
        source_bucket=calibration.horizon_bucket,
    )


def _seasonal_values(
    actuals: list[DailyActual], target_date: date, window_days: int = 15
) -> list[float]:
    target_day = target_date.timetuple().tm_yday
    values = []
    for actual in actuals:
        actual_day = actual.date.timetuple().tm_yday
        distance = abs(target_day - actual_day)
        if min(distance, 366 - distance) <= window_days:
            values.append(float(actual.temp_c))
    return values


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


async def recompute_probs(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    calculation_time = now.replace(second=0, microsecond=0)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            market_rows = (
                await session.execute(
                    select(Market, Station)
                    .join(Station, Market.station_id == Station.id)
                    .where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.station_verified.is_(True),
                    )
                )
            ).all()
            if not market_rows:
                return 0, 0
            station_ids = {station.id for _, station in market_rows}
            valid_dates = {market.market_date for market, _ in market_rows}
            forecasts = (
                await session.execute(
                    select(Forecast).where(
                        Forecast.station_id.in_(station_ids),
                        Forecast.valid_date.in_(valid_dates),
                    )
                )
            ).scalars().all()
            calibration_rows = (await session.execute(select(Calibration))).scalars().all()
            actual_rows = (
                await session.execute(
                    select(DailyActual).where(
                        DailyActual.station_id.in_(station_ids),
                        DailyActual.date >= min(valid_dates) - timedelta(days=366 * 4),
                    )
                )
            ).scalars().all()
            latest_prices = {
                price.market_id: price
                for price in (
                    await session.execute(
                        select(MarketPrice)
                        .where(
                            MarketPrice.market_id.in_(
                                [market.id for market, _ in market_rows]
                            )
                        )
                        .distinct(MarketPrice.market_id)
                        .order_by(MarketPrice.market_id, MarketPrice.ts.desc())
                    )
                ).scalars()
            }
            resolved_probability_rows = (
                await session.execute(
                    select(
                        Market.id,
                        Market.station_id,
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
            key = (
                forecast.station_id,
                forecast.model,
                forecast.valid_date,
                forecast.kind,
            )
            if key not in latest or forecast.run_time > latest[key].run_time:
                latest[key] = forecast
        calibrations = {
            (
                row.station_id,
                row.model,
                row.horizon_bucket,
                row.kind,
            ): row
            for row in calibration_rows
        }
        actuals_by_station_kind: dict[tuple[int, WeatherKind], list[DailyActual]] = (
            defaultdict(list)
        )
        for actual in actual_rows:
            actuals_by_station_kind[(actual.station_id, actual.kind)].append(actual)
        component_scores = component_brier_scores(
            [
                (station_id, bool(outcome), details)
                for _market_id, station_id, outcome, details in resolved_probability_rows
                if station_id is not None and isinstance(details, dict)
            ]
        )

        values: list[ModelProbabilityValue] = []
        skipped = 0
        for market, station in market_rows:
            if market.bucket_low_c is None and market.bucket_high_c is None:
                skipped += 1
                continue
            bucket = Bucket(
                low_c=float(market.bucket_low_c) if market.bucket_low_c is not None else None,
                high_c=(
                    float(market.bucket_high_c)
                    if market.bucket_high_c is not None
                    else None
                ),
            )
            horizon_h = max(0.0, (market.resolves_at - now).total_seconds() / 3600)
            target_bucket = horizon_bucket(math.ceil(horizon_h))
            components: list[ProbabilityComponent] = []

            ecmwf_members = [
                float(forecast.temp_c)
                for key, forecast in latest.items()
                if key[0] == station.id
                and key[1].startswith("ecmwf_ens_m")
                and key[2] == market.market_date
                and key[3] == market.kind
            ]
            ecmwf_cal = _pick_calibration(
                calibrations,
                station_id=station.id,
                model="ecmwf",
                target_bucket=target_bucket,
                kind=market.kind,
            )
            if len(ecmwf_members) >= 2:
                components.append(
                    ensemble_component(
                        name="ecmwf_ens",
                        member_values_c=ecmwf_members,
                        calibration=ecmwf_cal,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                    )
                )
            else:
                raw = latest.get(
                    (station.id, "ecmwf", market.market_date, market.kind)
                )
                if raw:
                    components.append(
                        deterministic_component(
                            name="ecmwf_ens",
                            raw_temp_c=float(raw.temp_c),
                            calibration=ecmwf_cal,
                            horizon_h=horizon_h,
                            bucket=bucket,
                            weight=DEFAULT_WEIGHTS["ecmwf_ens"],
                        )
                    )

            gefs_members = [
                float(forecast.temp_c)
                for key, forecast in latest.items()
                if key[0] == station.id
                and key[1].startswith("gefs_m")
                and key[2] == market.market_date
                and key[3] == market.kind
            ]
            gfs_cal = _pick_calibration(
                calibrations,
                station_id=station.id,
                model="gfs",
                target_bucket=target_bucket,
                kind=market.kind,
            )
            if len(gefs_members) >= 2:
                components.append(
                    ensemble_component(
                        name="gefs",
                        member_values_c=gefs_members,
                        calibration=gfs_cal,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS["gefs"],
                    )
                )

            deterministic_models = ["icon", "gfs"]
            anchor_model = (
                "nws"
                if (station.id, "nws", market.market_date, market.kind) in latest
                else "gem"
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
                components.append(
                    deterministic_component(
                        name=model,
                        raw_temp_c=float(raw.temp_c),
                        calibration=calibration,
                        horizon_h=horizon_h,
                        bucket=bucket,
                        weight=DEFAULT_WEIGHTS[model],
                    )
                )
            if not components:
                skipped += 1
                continue

            market_scores: dict[str, ComponentScore | None] = {
                component.name: component_scores.get(
                    (station.id, target_bucket, component.name)
                )
                for component in components
            }
            components, adaptive_weights = apply_adaptive_weights(
                components,
                market_scores,
                min_samples=BRIER_WEIGHT_MIN_SAMPLES,
                k=BRIER_WEIGHT_K,
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
            component_details = {}
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
                "weight_method": (
                    f"exp(-{BRIER_WEIGHT_K:g}*brier)"
                    if adaptive_weights
                    else "default"
                ),
                "weight_min_samples": BRIER_WEIGHT_MIN_SAMPLES,
            }

            # Anti-luck shrinkage: until the calibration sample has earned
            # trust, the published probability stays near the market and the
            # edge collapses. Both raw and used values are recorded.
            calibration_n = _calibration_depth(
                calibrations,
                station_id=station.id,
                target_bucket=target_bucket,
                kind=market.kind,
            )
            price = latest_prices.get(market.id)
            market_anchor = (
                float(price.midpoint)
                if price is not None and now - price.ts <= SHRINKAGE_PRICE_MAX_AGE
                else None
            )
            shrinkage = shrink_to_market(
                result.probability,
                market_anchor,
                calibration_samples=calibration_n,
                prior_samples=settings.shrinkage_prior_samples,
            )
            component_details["shrinkage"] = {
                "p_model_raw": round(result.probability, 6),
                "p_market": market_anchor,
                "weight": round(shrinkage.weight, 6),
                "n": calibration_n,
                "n0": settings.shrinkage_prior_samples,
                "applied": shrinkage.applied,
            }
            values.append(
                ModelProbabilityValue(
                    market_id=market.id,
                    ts=calculation_time,
                    prob=Decimal(str(shrinkage.probability)).quantize(
                        Decimal("0.000001")
                    ),
                    sigma=Decimal(str(result.sigma_c)).quantize(Decimal("0.0001")),
                    horizon_h=Decimal(str(horizon_h)).quantize(Decimal("0.01")),
                    components=component_details,
                )
            )

        event_by_market = {market.id: market.event_id for market, _ in market_rows}
        values = _renormalize_event_probabilities(values, event_by_market)

        async with session_factory.begin() as session:
            written = await upsert_model_probs(session, values)
        logger.info(
            "probability recompute complete: markets=%d written=%d skipped=%d",
            len(market_rows),
            written,
            skipped,
        )
        return written, skipped


def _renormalize_event_probabilities(
    values: list[ModelProbabilityValue],
    event_by_market: dict[int, str],
) -> list[ModelProbabilityValue]:
    """Rescale each event's shrunk bucket probabilities back to the raw model
    total, so an event's buckets form a proper distribution again."""
    from dataclasses import replace

    groups: dict[str, list[int]] = defaultdict(list)
    for index, value in enumerate(values):
        event_id = event_by_market.get(value.market_id)
        if event_id is not None:
            groups[event_id].append(index)

    result = list(values)
    for indices in groups.values():
        shrunk = [float(values[index].prob) for index in indices]
        raw_sum = sum(
            float(values[index].components.get("shrinkage", {}).get("p_model_raw")
                  or values[index].prob)
            for index in indices
        )
        rescaled, scale = renormalize_event(shrunk, reference_sum=raw_sum)
        if scale is None:
            continue
        for position, index in enumerate(indices):
            components = dict(values[index].components)
            components["shrinkage"] = {
                **components.get("shrinkage", {}),
                "event_scale": round(scale, 6),
                "p_final": round(rescaled[position], 6),
            }
            result[index] = replace(
                values[index],
                prob=Decimal(str(rescaled[position])).quantize(Decimal("0.000001")),
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
