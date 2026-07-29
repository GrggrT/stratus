from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob_trader import (
    ClobTradeError,
    ClobTrader,
    ClobVenueRejected,
    PlacedOrder,
)
from stratus.collectors.geoblock import GeoblockStatus, fetch_geoblock
from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    ExecutionOrderIntent,
    Market,
    MarketStatus,
    Signal,
    SignalStatus,
    Station,
    StationReliabilitySnapshot,
    StationStage,
)
from stratus.db.session import session_factory_scope
from stratus.jobs.execution_capacity import (
    acquire_execution_capacity_gate,
    engage_capacity_failure,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.execution import (
    LIVE_STATUS_CANCEL_REQUESTED,
    LIVE_STATUS_CANCELED,
    LIVE_STATUS_FILLED,
    LIVE_STATUS_SUBMIT_FAILED,
    LIVE_STATUS_UNCONFIRMED,
    LiveFillValue,
    LiveOrderClaim,
    OpenLiveOrder,
    claim_cancellable_live_orders,
    claim_live_order,
    expire_pending_signals,
    mark_live_order,
    open_live_orders,
    record_live_bankroll,
    record_live_placement,
    stale_submitting_orders,
    upsert_live_position,
)
from stratus.repositories.execution_cost_ledger import (
    append_live_reconciliation_observation,
    create_intent_for_order,
    record_terminal_no_fill,
    record_terminal_unverified_live_execution,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control, set_kill_switch
from stratus.repositories.selection_policy import capture_selection_policy_decisions
from stratus.repositories.station_reliability import (
    acquire_station_reliability_gate_lock,
    latest_station_reliability_by_station,
    signal_station_reliability_gate_reasons,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.execution_cost import ExecutionCostIntegrityError
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


async def _seal_live_no_fill(
    session: AsyncSession,
    *,
    order_id: int,
    terminal_status: str,
    failure_reason: str,
) -> None:
    intent = await session.scalar(
        select(ExecutionOrderIntent).where(
            ExecutionOrderIntent.order_id == order_id
        )
    )
    if intent is not None:
        await record_terminal_no_fill(
            session,
            intent.id,
            terminal_status=terminal_status,
            failure_reason=failure_reason,
        )


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
            await acquire_station_reliability_gate_lock(session)
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

            legacy_pending_ids = list(
                (
                    await session.execute(
                        select(Signal.id).where(
                            Signal.status == SignalStatus.PENDING,
                            (
                                (Signal.benchmark_protocol != CLOB_DUAL_PROTOCOL)
                                | Signal.token_id.is_(None)
                                | Signal.clob_snapshot_id.is_(None)
                            ),
                        )
                    )
                ).scalars()
            )
            if legacy_pending_ids:
                await expire_pending_signals(
                    session,
                    legacy_pending_ids,
                    reason="legacy_price_protocol",
                )

            unproven_pending_ids = list(
                (
                    await session.scalars(
                        select(Signal.id).where(
                            Signal.status == SignalStatus.PENDING,
                            Signal.station_reliability_snapshot_id.is_(None),
                        )
                    )
                ).all()
            )
            if unproven_pending_ids:
                await expire_pending_signals(
                    session,
                    unproven_pending_ids,
                    reason="station_reliability_provenance_missing",
                )

            rows = (
                await session.execute(
                    select(Signal, Market, Station)
                    .join(Market, Signal.market_id == Market.id)
                    .join(Station, Market.station_id == Station.id)
                    .where(
                        Signal.status == SignalStatus.PENDING,
                        Signal.benchmark_protocol == CLOB_DUAL_PROTOCOL,
                        Signal.token_id.is_not(None),
                        Signal.clob_snapshot_id.is_not(None),
                        Station.active.is_(True),
                        Station.stage == StationStage.APPROVED,
                    )
                    .order_by(Signal.ts)
                )
            ).all()
            current_reliability = await latest_station_reliability_by_station(
                session,
                sorted({station.id for _signal, _market, station in rows}),
            )
            reliability_valid_rows: list[tuple[Signal, Market, Station]] = []
            for signal, market, station in rows:
                attested_snapshot = await session.get(
                    StationReliabilitySnapshot,
                    signal.station_reliability_snapshot_id,
                )
                reliability_reasons = signal_station_reliability_gate_reasons(
                    signal=signal,
                    market=market,
                    station=station,
                    attested_snapshot=attested_snapshot,
                    current_snapshot=current_reliability.get(station.id),
                    settings=settings,
                    now=now,
                    require_reliable=True,
                )
                if reliability_reasons:
                    signal.status = SignalStatus.EXPIRED
                    signal.skip_reason = reliability_reasons[0]
                    logger.warning(
                        "live signal %d revoked by station reliability: %s",
                        signal.id,
                        reliability_reasons[0],
                    )
                    continue
                reliability_valid_rows.append((signal, market, station))
            # Expire signals too old to place, so they stop counting toward
            # exposure. Paper does this; live must too or they linger forever.
            stale_ids = [
                signal.id
                for signal, _market, _station in reliability_valid_rows
                if now - signal.ts > LIVE_ORDER_TTL
            ]
            await expire_pending_signals(session, stale_ids, reason="live_execution_timeout")

        active = [
            (signal, market, station)
            for signal, market, station in reliability_valid_rows
            if now - signal.ts <= LIVE_ORDER_TTL
        ]
        if not active:
            return 0, 0, 0

        live_trader: Trader = trader or await _connect_trader(settings)
        for signal, market, station in active:
            token_id = signal.token_id
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
            try:
                async with sessions.begin() as session:
                    await acquire_station_reliability_gate_lock(session)
                    capacity_gate = await acquire_execution_capacity_gate(
                        session,
                        settings,
                        now=datetime.now(UTC),
                    )
                    if not capacity_gate.allowed:
                        return placed, failed, 0
                    release_gate = await probability_release_gate(session, settings)
                    if not release_gate.allowed:
                        logger.error(
                            "live order intent blocked by probability release epoch: "
                            "reason=%s active=%s candidate=%s",
                            release_gate.reason,
                            release_gate.active_cohort,
                            release_gate.candidate_cohort,
                        )
                        return placed, failed, 0
                    fresh_signal = await session.get(
                        Signal,
                        signal.id,
                        with_for_update=True,
                    )
                    fresh_market = await session.get(Market, market.id)
                    fresh_station = await session.get(Station, station.id)
                    if (
                        fresh_signal is None
                        or fresh_market is None
                        or fresh_station is None
                        or fresh_signal.status is not SignalStatus.PENDING
                    ):
                        continue
                    attested_snapshot = await session.get(
                        StationReliabilitySnapshot,
                        fresh_signal.station_reliability_snapshot_id,
                    )
                    latest_reliability = await latest_station_reliability_by_station(
                        session,
                        [fresh_station.id],
                    )
                    reliability_reasons = signal_station_reliability_gate_reasons(
                        signal=fresh_signal,
                        market=fresh_market,
                        station=fresh_station,
                        attested_snapshot=attested_snapshot,
                        current_snapshot=latest_reliability.get(fresh_station.id),
                        settings=settings,
                        now=datetime.now(UTC),
                        require_reliable=True,
                    )
                    if reliability_reasons:
                        fresh_signal.status = SignalStatus.EXPIRED
                        fresh_signal.skip_reason = reliability_reasons[0]
                        logger.warning(
                            "live signal %d revoked immediately before order intent: %s",
                            fresh_signal.id,
                            reliability_reasons[0],
                        )
                        continue
                    # Re-evaluate readiness from the authoritative control row
                    # while the capacity lock remains held through order intent.
                    latest_control = await get_runtime_control(session)
                    latest_readiness = evaluate_live_readiness(
                        legal_approved=settings.live_legal_approved,
                        tax_approved=settings.live_tax_approved,
                        private_key_present=live_executor_configured(settings.polygon_private_key),
                        mode_is_live=latest_control.mode is ExecutionMode.LIVE,
                        kill_switch=latest_control.kill_switch,
                        halted_reason=latest_control.halted_reason,
                        geoblock_blocked=geo.blocked,
                    )
                    if not latest_readiness.ready:
                        logger.info(
                            "live order intent blocked by authoritative controls: %s",
                            ",".join(latest_readiness.reasons),
                        )
                        return placed, failed, 0
                    order_id = await claim_live_order(
                        session,
                        LiveOrderClaim(
                            signal_id=signal.id,
                            market_id=market.id,
                            side=signal.side,
                            price=plan.price,
                            size_usd=signal.size_usd,
                            token_id=token_id,
                        ),
                    )
                    if order_id is not None:
                        # The immutable intent is committed in the same
                        # transaction as the durable pre-submit marker and
                        # therefore always precedes signing/network I/O.
                        await create_intent_for_order(
                            session,
                            order_id,
                            release_image_digest=settings.release_image_digest,
                            order_style="taker",
                            expected_fee_usd=None,
                            expected_fee_unavailable_reason=(
                                "live_fee_quote_not_modeled"
                            ),
                        )
            except ExecutionCostIntegrityError as exc:
                logger.error(
                    "live execution intent rejected for signal %d: %s",
                    signal.id,
                    exc,
                )
                failed += 1
                continue
            except DBAPIError:
                logger.exception("live order capacity measurement failed")
                try:
                    async with sessions.begin() as session:
                        await engage_capacity_failure(
                            session,
                            reason="capacity_measurement_unavailable",
                        )
                except DBAPIError:
                    logger.exception("live execution could not persist capacity kill switch")
                return placed, failed, 0
            if order_id is None:
                continue  # already attempted; reconciliation owns it

            placement_request_started_at = datetime.now(UTC)
            try:
                result = await live_trader.place_gtc_buy(
                    token_id=plan.token_id,
                    price=plan.price,
                    size_shares=plan.size_shares,
                    tick_size=tick,
                    neg_risk=neg_risk,
                )
            except ClobVenueRejected as exc:
                # The exchange explicitly rejected it: no order on the book.
                async with sessions.begin() as session:
                    await mark_live_order(session, order_id, status=LIVE_STATUS_SUBMIT_FAILED)
                    await _seal_live_no_fill(
                        session,
                        order_id=order_id,
                        terminal_status=LIVE_STATUS_SUBMIT_FAILED,
                        failure_reason="venue_rejected",
                    )
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

            placement_response_received_at = datetime.now(UTC)
            async with sessions.begin() as session:
                await append_live_reconciliation_observation(
                    session,
                    order_id,
                    observation_kind="placement",
                    sdk_method="place_gtc_buy",
                    parser_version="placed-order-v1",
                    request_started_at=placement_request_started_at,
                    response_received_at=placement_response_received_at,
                    normalized_payload={
                        "order_id": result.order_id,
                        "status": result.status,
                    },
                )
                await record_live_placement(
                    session,
                    order_id=order_id,
                    clob_order_id=result.order_id,
                    status=placed_order_status(result.status),
                    placed_at=datetime.now(UTC),
                )
                # Close the kill-switch race: if a /stop fired after this
                # loop's readiness check but before/while placing, the fresh
                # order missed the kill switch's cancel sweep. Re-check and
                # queue it for cancellation so it cannot rest unattended.
                latest = await get_runtime_control(session)
                if latest.kill_switch or latest.halted_reason:
                    await mark_live_order(session, order_id, status=LIVE_STATUS_CANCEL_REQUESTED)
                    logger.warning(
                        "live order %s placed into an active kill switch; queued for cancellation",
                        result.order_id,
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

    logger.info("live preflight: ready=%s checks=%s", report.ready, report.checks)
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


@dataclass(frozen=True, slots=True)
class TradePollEvidence:
    fill: TradeFill | None
    normalized_payload: dict[str, object] | None
    request_started_at: datetime | None
    response_received_at: datetime | None


async def _trade_fill(trader: Trader, target: OpenLiveOrder) -> TradePollEvidence:
    """Best-effort trade aggregate plus the normalized SDK evidence used."""

    if target.token_id is None:
        return TradePollEvidence(None, None, None, None)
    request_started_at = datetime.now(UTC)
    try:
        trades = await trader.trades(target.token_id)
    except ClobTradeError as exc:
        logger.warning("could not fetch trades for %s: %s", target.clob_order_id, exc)
        return TradePollEvidence(None, None, None, None)
    response_received_at = datetime.now(UTC)
    return TradePollEvidence(
        fill=aggregate_order_trades(trades, target.clob_order_id),
        normalized_payload={"asset_id": target.token_id, "trades": trades},
        request_started_at=request_started_at,
        response_received_at=response_received_at,
    )


def _trade_fill_matches(trade_fill: TradeFill | None, order_fill: OrderFill) -> bool:
    if trade_fill is None or order_fill.matched_shares <= 0:
        return False
    drift = abs(trade_fill.matched_shares - order_fill.matched_shares)
    return drift <= order_fill.matched_shares * Decimal("0.01")


def _reconcile_fill(
    trade_fill: TradeFill | None, order_fill: OrderFill, target: OpenLiveOrder
) -> LiveFillValue:
    """Prefer the trade-derived VWAP and fee, but only when it agrees with the
    order's authoritative matched size (within 1%); otherwise use the order's
    size at its limit price (a conservative upper bound on cost)."""
    if _trade_fill_matches(trade_fill, order_fill):
        assert trade_fill is not None
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
            request_started_at = datetime.now(UTC)
            try:
                order_payload = await live_trader.order(target.clob_order_id)
            except ClobTradeError as exc:
                logger.warning("could not poll order %s: %s", target.clob_order_id, exc)
                continue
            response_received_at = datetime.now(UTC)
            async with sessions.begin() as session:
                order_observation = await append_live_reconciliation_observation(
                    session,
                    target.order_id,
                    observation_kind="order",
                    sdk_method="order",
                    parser_version="order-fill-v1",
                    request_started_at=request_started_at,
                    response_received_at=response_received_at,
                    normalized_payload=order_payload,
                )
                order_observation_id = order_observation.id
            order_fill = parse_order_fill(order_payload)
            if order_fill is None:
                continue
            if order_fill.matched_shares <= 0:
                if order_fill.terminal:
                    async with sessions.begin() as session:
                        intent_id = await session.scalar(
                            select(ExecutionOrderIntent.id).where(
                                ExecutionOrderIntent.order_id == target.order_id
                            )
                        )
                        if intent_id is None:
                            raise ExecutionCostIntegrityError(
                                "terminal live order lacks its immutable intent"
                            )
                        await record_terminal_unverified_live_execution(
                            session,
                            intent_id,
                            order_observation_id,
                            terminal_status=LIVE_STATUS_CANCELED,
                            matched_shares=Decimal(0),
                            failure_reason="venue_terminal_no_fill",
                        )
                    updated += 1
                continue
            trade_evidence = await _trade_fill(live_trader, target)
            trade_is_source = _trade_fill_matches(trade_evidence.fill, order_fill)
            value = _reconcile_fill(trade_evidence.fill, order_fill, target)
            async with sessions.begin() as session:
                trade_observation_id: int | None = None
                if (
                    trade_evidence.normalized_payload is not None
                    and trade_evidence.request_started_at is not None
                    and trade_evidence.response_received_at is not None
                ):
                    trade_observation = await append_live_reconciliation_observation(
                        session,
                        target.order_id,
                        observation_kind="trades",
                        sdk_method="trades",
                        parser_version="trade-aggregate-v1",
                        request_started_at=trade_evidence.request_started_at,
                        response_received_at=trade_evidence.response_received_at,
                        normalized_payload=trade_evidence.normalized_payload,
                    )
                    trade_observation_id = trade_observation.id
                if order_fill.terminal:
                    intent_id = await session.scalar(
                        select(ExecutionOrderIntent.id).where(
                            ExecutionOrderIntent.order_id == target.order_id
                        )
                    )
                    if intent_id is None:
                        raise ExecutionCostIntegrityError(
                            "terminal live order lacks its immutable intent"
                        )
                    await record_terminal_unverified_live_execution(
                        session,
                        intent_id,
                        (
                            trade_observation_id
                            if trade_is_source and trade_observation_id is not None
                            else order_observation_id
                        ),
                        terminal_status=(
                            LIVE_STATUS_FILLED
                            if order_fill.status == "matched"
                            else LIVE_STATUS_CANCELED
                        ),
                        matched_shares=value.size_shares,
                        avg_price=value.avg_price,
                        cost_usd=value.cost_usd,
                        fee_usd=value.fee_usd,
                        failure_reason="live_schema_unverified",
                    )
                else:
                    await upsert_live_position(session, value)
            updated += 1

        logged = 0
        try:
            balance = await live_trader.collateral_balance()
        except ClobTradeError as exc:
            logger.warning("live balance snapshot failed: %s", exc)
        else:
            async with sessions.begin() as session:
                logged = await record_live_bankroll(session, balance=balance, now=datetime.now(UTC))
        async with sessions.begin() as session:
            recorded = await capture_selection_policy_decisions(session)
        if recorded:
            logger.info("recorded %d prospective live selection-policy decisions", recorded)
    logger.info("live fill poll complete: positions=%d bankroll_logged=%d", updated, logged)
    return updated, logged


async def cancel_live_orders(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    *,
    trader: Trader | None = None,
) -> int:
    """Cancel requested orders and reconcile their final cumulative fills.

    A successful cancel request is not sufficient evidence that zero shares
    filled.  The order remains ``cancel_requested`` until a terminal CLOB
    snapshot is readable; this prevents a late partial fill from being erased.
    """
    settings = settings or get_settings()
    if not live_executor_configured(settings.polygon_private_key):
        return 0
    canceled = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            orders = await claim_cancellable_live_orders(session)
            market_by_signal = {
                signal_id: market_id
                for signal_id, market_id in (
                    await session.execute(
                        select(Signal.id, Signal.market_id).where(
                            Signal.id.in_([order.signal_id for order in orders])
                        )
                    )
                ).all()
            }
            targets = [
                OpenLiveOrder(
                    order_id=order.id,
                    clob_order_id=order.clob_order_id,
                    market_id=market_by_signal[order.signal_id],
                    side=order.side,
                    token_id=order.token_id,
                )
                for order in orders
                if order.clob_order_id and order.signal_id in market_by_signal
            ]
        if not targets:
            return 0
        cancel_trader: Trader = trader or await _connect_trader(settings)
        cancel_request_started_at = datetime.now(UTC)
        await cancel_trader.cancel([target.clob_order_id for target in targets])
        cancel_response_received_at = datetime.now(UTC)
        async with sessions.begin() as session:
            for target in targets:
                await append_live_reconciliation_observation(
                    session,
                    target.order_id,
                    observation_kind="cancel",
                    sdk_method="cancel",
                    parser_version="cancel-ack-v1",
                    request_started_at=cancel_request_started_at,
                    response_received_at=cancel_response_received_at,
                    normalized_payload={
                        "order_id": target.clob_order_id,
                        "result": "sdk_returned_without_error",
                    },
                )
        for target in targets:
            order_request_started_at = datetime.now(UTC)
            try:
                order_payload = await cancel_trader.order(target.clob_order_id)
            except Exception as exc:  # noqa: BLE001 - retain cancel_requested for retry
                logger.warning(
                    "canceled order %s could not be reconciled: %s",
                    target.clob_order_id,
                    exc,
                )
                continue
            order_response_received_at = datetime.now(UTC)
            order_fill = parse_order_fill(order_payload)
            if order_fill is None or not order_fill.terminal:
                logger.warning(
                    "canceled order %s has no terminal reconciliation yet",
                    target.clob_order_id,
                )
                continue
            trade_evidence = (
                await _trade_fill(cancel_trader, target)
                if order_fill.matched_shares > 0
                else TradePollEvidence(None, None, None, None)
            )
            trade_is_source = _trade_fill_matches(trade_evidence.fill, order_fill)
            value = (
                _reconcile_fill(trade_evidence.fill, order_fill, target)
                if order_fill.matched_shares > 0
                else None
            )
            async with sessions.begin() as session:
                order_observation = await append_live_reconciliation_observation(
                    session,
                    target.order_id,
                    observation_kind="order",
                    sdk_method="order",
                    parser_version="order-fill-v1",
                    request_started_at=order_request_started_at,
                    response_received_at=order_response_received_at,
                    normalized_payload=order_payload,
                )
                terminal_observation_id = order_observation.id
                if (
                    trade_evidence.normalized_payload is not None
                    and trade_evidence.request_started_at is not None
                    and trade_evidence.response_received_at is not None
                ):
                    trade_observation = await append_live_reconciliation_observation(
                        session,
                        target.order_id,
                        observation_kind="trades",
                        sdk_method="trades",
                        parser_version="trade-aggregate-v1",
                        request_started_at=trade_evidence.request_started_at,
                        response_received_at=trade_evidence.response_received_at,
                        normalized_payload=trade_evidence.normalized_payload,
                    )
                    if trade_is_source:
                        terminal_observation_id = trade_observation.id
                intent_id = await session.scalar(
                    select(ExecutionOrderIntent.id).where(
                        ExecutionOrderIntent.order_id == target.order_id
                    )
                )
                if intent_id is None:
                    raise ExecutionCostIntegrityError(
                        "terminal live order lacks its immutable intent"
                    )
                await record_terminal_unverified_live_execution(
                    session,
                    intent_id,
                    terminal_observation_id,
                    terminal_status=(
                        LIVE_STATUS_FILLED
                        if order_fill.status == "matched"
                        else LIVE_STATUS_CANCELED
                    ),
                    matched_shares=(value.size_shares if value is not None else Decimal(0)),
                    avg_price=(value.avg_price if value is not None else None),
                    cost_usd=(value.cost_usd if value is not None else None),
                    fee_usd=(value.fee_usd if value is not None else None),
                    failure_reason=(
                        "live_schema_unverified"
                        if value is not None
                        else "venue_cancel_no_fill"
                    ),
                )
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
