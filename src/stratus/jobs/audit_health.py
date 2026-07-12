from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    BankrollLog,
    DailyActual,
    ExecutionMode,
    Forecast,
    HealthAudit,
    Market,
    MarketStatus,
    ModelProb,
    Observation,
    Order,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import LIVE_STATUS_UNCONFIRMED
from stratus.repositories.job_runs import collector_uptime
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import set_kill_switch
from stratus.repositories.telegram import enqueue_notification
from stratus.services.probability import ks_uniform_pvalue, pit_value
from stratus.services.reconciliation import clear_mismatches, find_resolution_mismatches

logger = logging.getLogger(__name__)


def invalid_event_probability_sums(
    probabilities: dict[str, list[float]], tolerance: float = 0.005
) -> list[str]:
    return sorted(
        event_id
        for event_id, values in probabilities.items()
        if values and abs(sum(values) - 1.0) > tolerance
    )


async def audit_health(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[str, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    issues: list[dict[str, str]] = []
    critical = False
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            uptime = await collector_uptime(session, now=now)
            if uptime.mature and not uptime.passed:
                failed = [metric.job_id for metric in uptime.metrics if not metric.passed]
                issues.append(
                    {
                        "code": "collector_uptime_slo",
                        "detail": ",".join(failed),
                    }
                )
            loss_limits, _risk_expired = await enforce_loss_limits(
                session,
                mode=ExecutionMode.PAPER,
                initial_bankroll_usd=settings.initial_bankroll_usd,
                timezone=settings.risk_timezone,
                now=now,
            )
            if loss_limits.daily_breached:
                issues.append(
                    {
                        "code": "daily_loss_limit",
                        "detail": f"P&L ${loss_limits.daily_pnl:+.2f}",
                    }
                )
            if loss_limits.weekly_breached:
                issues.append(
                    {
                        "code": "weekly_loss_limit",
                        "detail": f"P&L ${loss_limits.weekly_pnl:+.2f}",
                    }
                )
                critical = True
            latest_forecast = await session.scalar(select(func.max(Forecast.fetched_at)))
            if latest_forecast is None or now - latest_forecast > timedelta(hours=2):
                issues.append({"code": "forecast_stale", "detail": str(latest_forecast)})

            station_rows = (
                await session.execute(
                    select(Station.icao, func.max(Observation.obs_time))
                    .outerjoin(Observation, Observation.station_id == Station.id)
                    .where(Station.active.is_(True))
                    .group_by(Station.icao)
                )
            ).all()
            for icao, latest_observation in station_rows:
                if (
                    latest_observation is None
                    or now - latest_observation > timedelta(minutes=90)
                ):
                    issues.append(
                        {"code": "observation_stale", "detail": f"{icao}"}
                    )

            near_markets = (
                await session.execute(
                    select(Market).where(
                        Market.status == MarketStatus.ACTIVE,
                        Market.resolves_at <= now + timedelta(hours=6),
                    )
                )
            ).scalars().all()
            stale_prices = [
                market.id
                for market in near_markets
                if market.last_price_fetch_at is None
                or now - market.last_price_fetch_at > timedelta(minutes=3)
            ]
            if stale_prices:
                issues.append(
                    {
                        "code": "near_price_stale",
                        "detail": f"{len(stale_prices)} market(s)",
                    }
                )

            # Settlement escalation: a market this far past resolution that is
            # still unresolved needs a human (Gamma dropped it, or the outcome
            # never reached exact 1/0 prices).
            stale_settlements = await session.scalar(
                select(func.count())
                .select_from(Market)
                .where(
                    Market.status != MarketStatus.RESOLVED,
                    Market.resolves_at < now - timedelta(hours=12),
                )
            )
            if stale_settlements:
                issues.append(
                    {
                        "code": "settlement_stale",
                        "detail": f"{stale_settlements} market(s) >12h past resolution",
                    }
                )

            # A live order whose outcome could not be confirmed may be resting
            # on the book unaccounted for: the executor already engaged the
            # kill switch, but surface it as critical for the operator.
            unconfirmed_live = await session.scalar(
                select(func.count())
                .select_from(Order)
                .where(
                    Order.mode == ExecutionMode.LIVE,
                    Order.status == LIVE_STATUS_UNCONFIRMED,
                )
            )
            if unconfirmed_live:
                issues.append(
                    {
                        "code": "live_orders_unconfirmed",
                        "detail": f"{unconfirmed_live} live order(s) need manual reconciliation",
                    }
                )
                critical = True

            # Reconcile METAR-derived daily actuals with how markets actually
            # resolved: a clear disagreement means the observation pipeline
            # diverges from the official resolution source that Polymarket
            # uses — the model is being calibrated against the wrong truth.
            reconciliation_rows = (
                await session.execute(
                    select(
                        Market.id,
                        DailyActual.temp_c,
                        Market.bucket_low_c,
                        Market.bucket_high_c,
                        Market.resolved_outcome,
                    )
                    .join(
                        DailyActual,
                        and_(
                            DailyActual.station_id == Market.station_id,
                            DailyActual.date == Market.market_date,
                            DailyActual.kind == Market.kind,
                        ),
                    )
                    .where(
                        Market.resolved_outcome.is_not(None),
                        Market.resolves_at >= now - timedelta(days=30),
                        or_(
                            Market.bucket_low_c.is_not(None),
                            Market.bucket_high_c.is_not(None),
                        ),
                    )
                )
            ).all()
            mismatches = find_resolution_mismatches(
                (market_id, temp_c, low_c, high_c, bool(resolved))
                for market_id, temp_c, low_c, high_c, resolved in reconciliation_rows
            )
            clearly_wrong = clear_mismatches(mismatches)
            if clearly_wrong:
                worst = max(item.margin_c for item in clearly_wrong)
                issues.append(
                    {
                        "code": "actuals_resolution_mismatch",
                        "detail": (
                            f"{len(clearly_wrong)} market(s) resolved against our "
                            f"observations (worst margin {worst}°C)"
                        ),
                    }
                )

            # PIT uniformity: is the predicted temperature distribution
            # honest? One sample per station/day/kind (bucket markets of one
            # event share the same forecast and actual).
            pit_rows = (
                await session.execute(
                    select(
                        Market.station_id,
                        Market.market_date,
                        Market.kind,
                        DailyActual.temp_c,
                        ModelProb.components,
                    )
                    .join(
                        DailyActual,
                        and_(
                            DailyActual.station_id == Market.station_id,
                            DailyActual.date == Market.market_date,
                            DailyActual.kind == Market.kind,
                        ),
                    )
                    .join(ModelProb, ModelProb.market_id == Market.id)
                    .where(
                        Market.resolved_outcome.is_not(None),
                        Market.resolves_at >= now - timedelta(days=30),
                        ModelProb.ts <= Market.resolves_at,
                    )
                    .distinct(Market.id)
                    .order_by(Market.id, ModelProb.ts.desc())
                )
            ).all()
            pits: dict[tuple[int | None, object, object], float] = {}
            for station_id, market_date, kind, temp_c, details in pit_rows:
                key = (station_id, market_date, kind)
                if key in pits or not isinstance(details, dict):
                    continue
                blend = details.get("blend")
                if not isinstance(blend, dict):
                    continue
                mean_c = blend.get("mean_c")
                sigma_c = blend.get("sigma_c")
                if (
                    isinstance(mean_c, int | float)
                    and isinstance(sigma_c, int | float)
                    and sigma_c > 0
                ):
                    pits[key] = pit_value(float(temp_c), float(mean_c), float(sigma_c))
            if len(pits) >= 30:
                p_value = ks_uniform_pvalue(list(pits.values()))
                if p_value is not None and p_value < 0.01:
                    issues.append(
                        {
                            "code": "pit_miscalibrated",
                            "detail": (
                                f"forecast distribution fails uniformity: "
                                f"KS p={p_value:.4f} over {len(pits)} station-days"
                            ),
                        }
                    )

            active_markets = (
                await session.execute(
                    select(Market).where(Market.status == MarketStatus.ACTIVE)
                )
            ).scalars().all()
            market_by_id = {market.id: market for market in active_markets}
            probabilities = (
                await session.execute(
                    select(ModelProb)
                    .where(ModelProb.market_id.in_(market_by_id))
                    .distinct(ModelProb.market_id)
                    .order_by(ModelProb.market_id, ModelProb.ts.desc())
                )
            ).scalars().all()
            latest_probabilities = {
                probability.market_id: probability for probability in probabilities
            }
            stale_probability_count = sum(
                market_id not in latest_probabilities
                or now - latest_probabilities[market_id].ts > timedelta(minutes=15)
                for market_id in market_by_id
            )
            if stale_probability_count:
                issues.append(
                    {
                        "code": "probability_stale",
                        "detail": f"{stale_probability_count} market(s)",
                    }
                )
            by_event: dict[str, list[float]] = defaultdict(list)
            for market_id, probability in latest_probabilities.items():
                by_event[market_by_id[market_id].event_id].append(float(probability.prob))
            invalid_events = invalid_event_probability_sums(by_event)
            if invalid_events:
                issues.append(
                    {
                        "code": "event_probability_sum",
                        "detail": f"{len(invalid_events)} event(s)",
                    }
                )

            bankroll = await session.scalar(
                select(BankrollLog.balance_usd)
                .where(BankrollLog.mode == ExecutionMode.PAPER)
                .order_by(BankrollLog.ts.desc())
                .limit(1)
            )
            bankroll_usd = float(bankroll or settings.initial_bankroll_usd)
            position_rows = (
                await session.execute(
                    select(Position, Market)
                    .join(Market, Position.market_id == Market.id)
                    .where(
                        Position.mode == ExecutionMode.PAPER,
                        Position.status == PositionStatus.OPEN,
                    )
                )
            ).all()
            market_exposure: dict[int, float] = defaultdict(float)
            station_exposure: dict[tuple[int | None, object], float] = defaultdict(float)
            for position, market in position_rows:
                market_exposure[market.id] += float(position.size_usd)
                station_exposure[(market.station_id, market.market_date)] += float(
                    position.size_usd
                )
            market_limit = 0.05 * bankroll_usd
            material_market_excess = max(1.0, 0.001 * bankroll_usd)
            market_breaches = [
                (market_id, value)
                for market_id, value in market_exposure.items()
                if value > market_limit + 0.01
            ]
            if market_breaches:
                detail = "; ".join(
                    f"market_id={market_id} ${value:.2f}/${market_limit:.2f} "
                    f"(excess ${value - market_limit:.2f})"
                    for market_id, value in sorted(
                        market_breaches, key=lambda item: item[1], reverse=True
                    )[:5]
                )
                issues.append({"code": "market_exposure_breach", "detail": detail})
                if any(
                    value > market_limit + material_market_excess
                    for _market_id, value in market_breaches
                ):
                    critical = True
            station_limit = 0.12 * bankroll_usd
            material_station_excess = max(1.0, 0.001 * bankroll_usd)
            station_breaches = [
                (station_key, value)
                for station_key, value in station_exposure.items()
                if value > station_limit + 0.01
            ]
            if station_breaches:
                detail = "; ".join(
                    f"station_day={station_key} ${value:.2f}/${station_limit:.2f} "
                    f"(excess ${value - station_limit:.2f})"
                    for station_key, value in sorted(
                        station_breaches, key=lambda item: item[1], reverse=True
                    )[:5]
                )
                issues.append(
                    {"code": "station_day_exposure_breach", "detail": detail}
                )
                if any(
                    value > station_limit + material_station_excess
                    for _station_key, value in station_breaches
                ):
                    critical = True

            missing_clv = await session.scalar(
                select(func.count())
                .select_from(Position)
                .where(
                    Position.mode == ExecutionMode.PAPER,
                    Position.status == PositionStatus.CLOSED,
                    Position.clv.is_(None),
                )
            )
            if missing_clv:
                issues.append({"code": "clv_missing", "detail": str(missing_clv)})

            # Maker orders legitimately rest for the whole TTL; only signals
            # that outlived it plus a scheduler-cycle slack are stuck.
            stale_pending = await session.scalar(
                select(func.count())
                .select_from(Signal)
                .where(
                    Signal.status == SignalStatus.PENDING,
                    Signal.ts
                    < now - timedelta(minutes=settings.paper_order_ttl_minutes + 5),
                )
            )
            if stale_pending:
                issues.append(
                    {"code": "pending_signal_stale", "detail": str(stale_pending)}
                )

            status = "critical" if critical else ("warning" if issues else "healthy")
            session.add(HealthAudit(ts=now, status=status, issues=issues))
            if critical:
                await set_kill_switch(session, True)
            alert_bucket = now.replace(minute=0, second=0, microsecond=0).isoformat()
            for issue in issues:
                await enqueue_notification(
                    session,
                    f"health:{alert_bucket}:{issue['code']}",
                    f"STRATUS health {status}: {issue['code']}\n{issue['detail']}",
                )
        logger.info("health audit complete: status=%s issues=%d", status, len(issues))
        return status, len(issues)


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_health(settings))


if __name__ == "__main__":
    main()
