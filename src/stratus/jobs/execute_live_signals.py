from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob_trader import ClobTradeError, ClobTrader, PlacedOrder
from stratus.collectors.geoblock import GeoblockStatus, fetch_geoblock
from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    Market,
    MarketStatus,
    Signal,
    SignalStatus,
    TradeSide,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import (
    LIVE_STATUS_CANCELED,
    LIVE_STATUS_FILLED,
    LIVE_STATUS_SUBMIT_FAILED,
    LIVE_STATUS_UNCONFIRMED,
    LiveFillValue,
    LiveOrderClaim,
    OpenLiveOrder,
    claim_cancellable_live_orders,
    claim_live_order,
    mark_live_order,
    open_live_orders,
    record_live_bankroll,
    record_live_placement,
    stale_submitting_orders,
    upsert_live_position,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control, set_kill_switch
from stratus.repositories.telegram import enqueue_notification
from stratus.services.live_execution import (
    LiveOrderRejected,
    OrderFill,
    TradeFill,
    aggregate_order_trades,
    evaluate_live_readiness,
    live_executor_configured,
    parse_order_fill,
    placed_order_status,
    plan_live_buy,
)

logger = logging.getLogger(__name__)
LIVE_ORDER_TTL = timedelta(minutes=10)
MIN_ORDER_SHARES = Decimal("5")


class Trader(Protocol):
    async def tick_size(self, token_id: str) -> Decimal: ...
    async def neg_risk(self, token_id: str) -> bool: ...
    async def place_gtc_buy(
        self,
        *,
        token_id: str,
        price: Decimal,
        size_shares: Decimal,
        tick_size: Decimal,
        neg_risk: bool,
    ) -> PlacedOrder: ...
    async def cancel(self, order_ids: list[str]) -> None: ...
    async def order(self, order_id: str) -> dict[str, object]: ...
    async def trades(self, asset_id: str) -> list[dict[str, object]]: ...
    async def collateral_balance(self) -> Decimal: ...


async def _connect_trader(settings: Settings) -> ClobTrader:
    trader = ClobTrader(
        host=settings.clob_api_url,
        chain_id=settings.polygon_chain_id,
        private_key=settings.polygon_private_key or "",
        signature_type=settings.polymarket_signature_type,
        funder=settings.polymarket_funder,
    )
    await trader.connect()
    return trader


async def _halt(session: AsyncSession, key: str, message: str) -> None:
    await set_kill_switch(session, True)
    await enqueue_notification(session, key, message)
    logger.error("live execution halted: %s", message)


async def execute_live_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    trader: Trader | None = None,
    geoblock: GeoblockStatus | None = None,
) -> tuple[int, int, int]:
    """Place live GTC limit orders for pending signals.

    Crash-safe: the order row is committed as `submitting` before the network
    call, so a signal that already has an order is never re-submitted; a
    submission whose outcome is unknown halts trading for manual review.

    `trader`/`geoblock` are injected in tests; in production they are built
    from settings and the geoblock endpoint is queried fresh.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)
    placed = 0
    failed = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            control = await get_runtime_control(session)
            geo = geoblock or await fetch_geoblock(settings.geoblock_url)
            readiness = evaluate_live_readiness(
                legal_approved=settings.live_legal_approved,
                tax_approved=settings.live_tax_approved,
                private_key_present=live_executor_configured(settings.polygon_private_key),
                mode_is_live=control.mode is ExecutionMode.LIVE,
                kill_switch=control.kill_switch,
                halted_reason=control.halted_reason,
                geoblock_blocked=geo.blocked,
            )
            if not readiness.ready:
                logger.info("live execution not ready: %s", ",".join(readiness.reasons))
                return 0, 0, 0

            # A prior run crashed between claim and submit: the real state on
            # the exchange is unknown, so escalate and stop — never re-send.
            orphaned = await stale_submitting_orders(session)
            if orphaned:
                for order in orphaned:
                    order.status = LIVE_STATUS_UNCONFIRMED
                await _halt(
                    session,
                    f"live-unconfirmed:{now:%Y%m%dT%H%M}",
                    "CRITICAL: STRATUS live orders left unconfirmed by a crash; "
                    f"{len(orphaned)} order(s) need manual reconciliation.",
                )
                return 0, 0, len(orphaned)

            loss_limits, _expired = await enforce_loss_limits(
                session,
                mode=ExecutionMode.LIVE,
                initial_bankroll_usd=settings.initial_bankroll_usd,
                timezone=settings.risk_timezone,
                now=now,
            )
            if loss_limits.daily_breached or loss_limits.weekly_breached:
                logger.warning("live execution blocked by loss limit")
                return 0, 0, 0

            rows = (
                await session.execute(
                    select(Signal, Market)
                    .join(Market, Signal.market_id == Market.id)
                    .where(Signal.status == SignalStatus.PENDING)
                    .order_by(Signal.ts)
                )
            ).all()

        active = [
            (signal, market) for signal, market in rows if now - signal.ts <= LIVE_ORDER_TTL
        ]
        if not active:
            return 0, 0, 0

        live_trader: Trader = trader or await _connect_trader(settings)
        for signal, market in active:
            token_id = (
                market.yes_token_id
                if signal.side is TradeSide.BUY_YES
                else market.no_token_id
            )
            if not token_id:
                failed += 1
                continue
            max_price = min(Decimal("0.999"), signal.market_price + Decimal("0.01"))
            try:
                tick = await live_trader.tick_size(token_id)
                neg_risk = await live_trader.neg_risk(token_id)
                plan = plan_live_buy(
                    token_id=token_id,
                    max_price=max_price,
                    size_usd=signal.size_usd,
                    tick_size=tick,
                    min_size_shares=MIN_ORDER_SHARES,
                    max_order_usd=Decimal(str(settings.live_max_order_usd)),
                )
            except (LiveOrderRejected, ClobTradeError) as exc:
                logger.warning("live order rejected for signal %d: %s", signal.id, exc)
                failed += 1
                continue

            # Durable marker, committed before the network call.
            async with sessions.begin() as session:
                order_id = await claim_live_order(
                    session,
                    LiveOrderClaim(
                        signal_id=signal.id,
                        market_id=market.id,
                        side=signal.side,
                        price=plan.price,
                        size_usd=signal.size_usd,
                    ),
                )
            if order_id is None:
                continue  # already attempted; reconciliation owns it

            try:
                result = await live_trader.place_gtc_buy(
                    token_id=plan.token_id,
                    price=plan.price,
                    size_shares=plan.size_shares,
                    tick_size=tick,
                    neg_risk=neg_risk,
                )
            except ClobTradeError as exc:
                # The exchange explicitly rejected it: no order on the book.
                async with sessions.begin() as session:
                    await mark_live_order(session, order_id, status=LIVE_STATUS_SUBMIT_FAILED)
                logger.warning("live placement failed for signal %d: %s", signal.id, exc)
                failed += 1
                continue
            except Exception as exc:
                # Unknown outcome (timeout, etc.): the order MAY be live.
                async with sessions.begin() as session:
                    await mark_live_order(session, order_id, status=LIVE_STATUS_UNCONFIRMED)
                    await _halt(
                        session,
                        f"live-unconfirmed:{signal.id}",
                        "CRITICAL: STRATUS live order outcome unknown for signal "
                        f"{signal.id} ({type(exc).__name__}); trading halted for "
                        "manual reconciliation.",
                    )
                logger.exception("live placement unconfirmed for signal %d", signal.id)
                return placed, failed, 1

            async with sessions.begin() as session:
                await record_live_placement(
                    session,
                    order_id=order_id,
                    clob_order_id=result.order_id,
                    status=placed_order_status(result.status),
                    placed_at=datetime.now(UTC),
                )
            placed += 1
            logger.info(
                "live order placed: signal=%d order=%s status=%s",
                signal.id,
                result.order_id,
                result.status,
            )
    return placed, failed, 0


@dataclass(slots=True)
class PreflightReport:
    configured: bool = False
    connected: bool = False
    geoblock_blocked: bool | None = None
    geoblock_country: str | None = None
    balance_usd: Decimal | None = None
    sample_tick: Decimal | None = None
    checks: dict[str, str] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return (
            self.configured
            and self.connected
            and self.geoblock_blocked is False
            and self.balance_usd is not None
        )


async def preflight_live(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    trader: Trader | None = None,
    geoblock: GeoblockStatus | None = None,
) -> PreflightReport:
    """Validate the live wiring against the real server WITHOUT placing an
    order: wallet auth, geoblock, collateral balance, and a tick-size lookup.
    The one thing the fake-trader tests cannot cover before real money moves.
    """
    settings = settings or get_settings()
    report = PreflightReport()
    report.configured = live_executor_configured(settings.polygon_private_key)
    if not report.configured:
        report.checks["configured"] = "POLYGON_PRIVATE_KEY is not set"
        return report
    report.checks["configured"] = "ok"

    geo = geoblock or await fetch_geoblock(settings.geoblock_url)
    report.geoblock_blocked = geo.blocked
    report.geoblock_country = geo.country
    report.checks["geoblock"] = (
        "blocked" if geo.blocked else f"allowed ({geo.country or 'unknown'})"
    )

    try:
        live_trader: Trader = trader or await _connect_trader(settings)
        report.connected = True
        report.checks["connect"] = "ok"
    except Exception as exc:  # noqa: BLE001 - preflight reports, never raises
        report.checks["connect"] = f"{type(exc).__name__}: {exc}"
        return report

    try:
        report.balance_usd = await live_trader.collateral_balance()
        report.checks["balance"] = f"${report.balance_usd}"
    except Exception as exc:  # noqa: BLE001
        report.checks["balance"] = f"{type(exc).__name__}: {exc}"

    token = await _sample_token(settings, session_factory)
    if token is None:
        report.checks["tick_size"] = "no active verified market to sample"
    else:
        try:
            report.sample_tick = await live_trader.tick_size(token)
            report.checks["tick_size"] = f"{report.sample_tick} ({token[:10]}...)"
        except Exception as exc:  # noqa: BLE001
            report.checks["tick_size"] = f"{type(exc).__name__}: {exc}"

    logger.info(
        "live preflight: ready=%s checks=%s", report.ready, report.checks
    )
    return report


async def _sample_token(
    settings: Settings, session_factory: async_sessionmaker[AsyncSession] | None
) -> str | None:
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            return await session.scalar(
                select(Market.yes_token_id)
                .where(
                    Market.status == MarketStatus.ACTIVE,
                    Market.station_verified.is_(True),
                    Market.yes_token_id.is_not(None),
                )
                .limit(1)
            )


async def _trade_fill(trader: Trader, target: OpenLiveOrder) -> TradeFill | None:
    """Best-effort per-trade VWAP; None on any failure so the caller falls
    back to the order's conservative limit price."""
    if target.token_id is None:
        return None
    try:
        trades = await trader.trades(target.token_id)
    except ClobTradeError as exc:
        logger.warning("could not fetch trades for %s: %s", target.clob_order_id, exc)
        return None
    return aggregate_order_trades(trades, target.clob_order_id)


def _reconcile_fill(
    trade_fill: TradeFill | None, order_fill: OrderFill, target: OpenLiveOrder
) -> LiveFillValue:
    """Prefer the trade-derived VWAP and fee, but only when it agrees with the
    order's authoritative matched size (within 1%); otherwise use the order's
    size at its limit price (a conservative upper bound on cost)."""
    if trade_fill is not None and order_fill.matched_shares > 0:
        drift = abs(trade_fill.matched_shares - order_fill.matched_shares)
        if drift <= order_fill.matched_shares * Decimal("0.01"):
            return LiveFillValue(
                order_id=target.order_id,
                market_id=target.market_id,
                side=target.side,
                size_shares=trade_fill.matched_shares,
                avg_price=trade_fill.avg_price,
                cost_usd=trade_fill.cost_usd,
                fee_usd=trade_fill.fee_usd,
            )
    return LiveFillValue(
        order_id=target.order_id,
        market_id=target.market_id,
        side=target.side,
        size_shares=order_fill.matched_shares,
        avg_price=order_fill.avg_price,
        cost_usd=order_fill.cost_usd,
    )


async def poll_live_fills(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    trader: Trader | None = None,
) -> tuple[int, int]:
    """Recompute live positions from each open order's cumulative fills and
    snapshot the live bankroll. Idempotent: re-reading an order and re-writing
    its position is safe, so a missed or repeated poll self-heals."""
    settings = settings or get_settings()
    if not live_executor_configured(settings.polygon_private_key):
        return 0, 0
    updated = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            targets = await open_live_orders(session)
        live_trader: Trader = trader or await _connect_trader(settings)
        for target in targets:
            try:
                order_fill = parse_order_fill(await live_trader.order(target.clob_order_id))
            except ClobTradeError as exc:
                logger.warning("could not poll order %s: %s", target.clob_order_id, exc)
                continue
            if order_fill is None or order_fill.matched_shares <= 0:
                continue
            value = _reconcile_fill(
                await _trade_fill(live_trader, target), order_fill, target
            )
            async with sessions.begin() as session:
                await upsert_live_position(session, value)
                if order_fill.terminal and order_fill.status == "matched":
                    await mark_live_order(session, target.order_id, status=LIVE_STATUS_FILLED)
                elif order_fill.terminal:
                    await mark_live_order(session, target.order_id, status=LIVE_STATUS_CANCELED)
            updated += 1

        logged = 0
        try:
            balance = await live_trader.collateral_balance()
        except ClobTradeError as exc:
            logger.warning("live balance snapshot failed: %s", exc)
        else:
            async with sessions.begin() as session:
                logged = await record_live_bankroll(session, balance=balance, now=datetime.now(UTC))
    logger.info("live fill poll complete: positions=%d bankroll_logged=%d", updated, logged)
    return updated, logged


async def cancel_live_orders(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    trader: Trader | None = None,
) -> int:
    """Cancel live orders the kill switch marked `cancel_requested`."""
    settings = settings or get_settings()
    if not live_executor_configured(settings.polygon_private_key):
        return 0
    canceled = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            orders = await claim_cancellable_live_orders(session)
            targets = [
                (order.id, order.clob_order_id) for order in orders if order.clob_order_id
            ]
        if not targets:
            return 0
        cancel_trader: Trader = trader or await _connect_trader(settings)
        await cancel_trader.cancel([clob_id for _, clob_id in targets])
        async with sessions.begin() as session:
            for order_id, _ in targets:
                await mark_live_order(session, order_id, status=LIVE_STATUS_CANCELED)
                canceled += 1
    logger.info("live order cancellation complete: canceled=%d", canceled)
    return canceled


def _configure_logging(settings: Settings) -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> None:
    settings = get_settings()
    _configure_logging(settings)
    asyncio.run(execute_live_signals(settings))


def poll_main() -> None:
    settings = get_settings()
    _configure_logging(settings)
    asyncio.run(poll_live_fills(settings))


def preflight_main() -> None:
    settings = get_settings()
    _configure_logging(settings)
    report = asyncio.run(preflight_live(settings))
    print("STRATUS live preflight")
    print(f"  ready: {report.ready}")
    for name, detail in report.checks.items():
        print(f"  {name}: {detail}")
    if not report.ready:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
