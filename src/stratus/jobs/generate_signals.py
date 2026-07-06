from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    BankrollLog,
    Market,
    MarketPrice,
    MarketStatus,
    ModelProb,
    Position,
    PositionStatus,
    Signal,
    SignalStatus,
    Station,
    TradeSide,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.signals import SignalValue, insert_signals
from stratus.services.signals import SignalInputs, ensemble_agreement, evaluate_signal

logger = logging.getLogger(__name__)


def _decimal(value: float, precision: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(precision))


async def generate_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory.begin() as session:
            control = await get_runtime_control(session)
            mode = control.mode
            loss_limits, _expired = await enforce_loss_limits(
                session,
                mode=mode,
                initial_bankroll_usd=settings.initial_bankroll_usd,
                timezone=settings.risk_timezone,
                now=now,
            )
            control = await get_runtime_control(session)
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
            market_ids = [market.id for market, _ in market_rows]
            probabilities = (
                await session.execute(
                    select(ModelProb)
                    .where(ModelProb.market_id.in_(market_ids))
                    .distinct(ModelProb.market_id)
                    .order_by(ModelProb.market_id, ModelProb.ts.desc())
                )
            ).scalars().all()
            prices = (
                await session.execute(
                    select(MarketPrice)
                    .where(MarketPrice.market_id.in_(market_ids))
                    .distinct(MarketPrice.market_id)
                    .order_by(MarketPrice.market_id, MarketPrice.ts.desc())
                )
            ).scalars().all()
            bankroll = (
                await session.execute(
                    select(BankrollLog.balance_usd)
                    .where(BankrollLog.mode == mode)
                    .order_by(BankrollLog.ts.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            open_positions = (
                await session.execute(
                    select(Position, Market)
                    .join(Market, Position.market_id == Market.id)
                    .where(Position.mode == mode, Position.status == PositionStatus.OPEN)
                )
            ).all()
            pending_signals = (
                await session.execute(
                    select(Signal, Market)
                    .join(Market, Signal.market_id == Market.id)
                    .where(Signal.status == SignalStatus.PENDING)
                )
            ).all()

        bankroll_usd = float(bankroll or settings.initial_bankroll_usd)
        latest_probs = {probability.market_id: probability for probability in probabilities}
        latest_prices = {price.market_id: price for price in prices}

        market_exposure: dict[int, float] = defaultdict(float)
        station_day_exposure: dict[tuple[int | None, object], float] = defaultdict(float)
        pending_market_ids = set()
        for position, market in open_positions:
            exposure = float(position.size_usd)
            market_exposure[market.id] += exposure
            station_day_exposure[(market.station_id, market.market_date)] += exposure
        for signal, market in pending_signals:
            exposure = float(signal.size_usd)
            pending_market_ids.add(market.id)
            market_exposure[market.id] += exposure
            station_day_exposure[(market.station_id, market.market_date)] += exposure

        global_reasons = []
        if control.kill_switch:
            global_reasons.append("kill_switch")
        if control.halted_reason:
            global_reasons.append("project_halted")
        if loss_limits.daily_breached:
            global_reasons.append("daily_loss_limit")
        if loss_limits.weekly_breached:
            global_reasons.append("weekly_loss_limit")

        candidates = []
        for market, _station in market_rows:
            probability = latest_probs.get(market.id)
            price = latest_prices.get(market.id)
            if probability is None or price is None:
                continue
            agreement_yes = ensemble_agreement(
                probability.components, TradeSide.BUY_YES
            )
            decision = evaluate_signal(
                SignalInputs(
                    model_probability=float(probability.prob),
                    best_bid=float(price.best_bid),
                    best_ask=float(price.best_ask),
                    bid_depth_usd=float(price.bid_depth_usd),
                    ask_depth_usd=float(price.ask_depth_usd),
                    horizon_h=float(probability.horizon_h),
                    market_created_at=market.created_at,
                    price_ts=price.ts,
                    probability_ts=probability.ts,
                    station_verified=market.station_verified,
                    ensemble_yes_agreement=agreement_yes,
                    bankroll_usd=bankroll_usd,
                    fee_rate=float(market.fee_rate) if market.fees_enabled else 0.0,
                ),
                now,
            )
            candidates.append((decision.edge, market, probability, price, decision))

        values = []
        accepted = 0
        for _, market, probability, price, decision in sorted(
            candidates, key=lambda item: item[0], reverse=True
        ):
            reasons = list(decision.reasons) + global_reasons
            if market.id in pending_market_ids:
                reasons.append("pending_signal_exists")
            market_remaining = 0.05 * bankroll_usd - market_exposure[market.id]
            station_key = (market.station_id, market.market_date)
            station_remaining = 0.12 * bankroll_usd - station_day_exposure[station_key]
            final_size = max(
                0.0,
                min(decision.size_usd, market_remaining, station_remaining),
            )
            if market_remaining <= 0:
                reasons.append("market_exposure_limit")
            if station_remaining <= 0:
                reasons.append("station_day_exposure_limit")

            # Liquidity must be checked against the final risk-capped order size.
            reasons = [reason for reason in reasons if reason != "insufficient_depth_3x"]
            selected_depth = (
                float(price.ask_depth_usd)
                if decision.side is TradeSide.BUY_YES
                else float(price.bid_depth_usd)
            )
            if selected_depth < 3 * final_size:
                reasons.append("insufficient_depth_3x")

            status = SignalStatus.SKIPPED if reasons else SignalStatus.PENDING
            if status is SignalStatus.PENDING:
                accepted += 1
                market_exposure[market.id] += final_size
                station_day_exposure[station_key] += final_size
                pending_market_ids.add(market.id)
            values.append(
                SignalValue(
                    market_id=market.id,
                    ts=max(probability.ts, price.ts),
                    side=decision.side,
                    model_prob=probability.prob,
                    market_price=_decimal(decision.entry_price, "0.000001"),
                    edge=_decimal(decision.edge, "0.000001"),
                    kelly_frac=_decimal(final_size / bankroll_usd, "0.000001"),
                    size_usd=_decimal(final_size, "0.0001"),
                    confidence=decision.confidence,
                    status=status,
                    skip_reason=";".join(dict.fromkeys(reasons)) if reasons else None,
                )
            )

        async with session_factory.begin() as session:
            written = await insert_signals(session, values)
        logger.info(
            "signal generation complete: evaluated=%d inserted=%d pending=%d skipped=%d",
            len(candidates),
            written,
            accepted,
            len(candidates) - accepted,
        )
        return accepted, len(candidates) - accepted


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(generate_signals(settings))


if __name__ == "__main__":
    main()
