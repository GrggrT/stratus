from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ClobMarketSnapshot,
    ExecutionMode,
    ExecutionOrderIntent,
    Market,
    Order,
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
from stratus.repositories.clob_snapshots import (
    eligible_clob_snapshots_at_or_after,
    load_attested_token_book,
)
from stratus.repositories.evidence_continuity_gate import probability_release_gate
from stratus.repositories.execution import (
    PAPER_STATUS_PENDING,
    PAPER_STATUS_RESTING,
    MakerFillValue,
    PaperExecutionValue,
    RestingPaperOrder,
    expire_pending_signals,
    expire_resting_paper_orders,
)
from stratus.repositories.execution_cost_ledger import (
    PaperFillLegSpec,
    create_intent_for_order,
    record_paper_fill,
    record_terminal_no_fill,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control
from stratus.repositories.selection_policy import capture_selection_policy_decisions
from stratus.repositories.station_reliability import (
    acquire_station_reliability_gate_lock,
    latest_station_reliability_by_station,
    signal_station_reliability_gate_reasons,
)
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.execution import (
    PaperExecutionError,
    best_bid_ask,
    executable_ask_levels,
    maker_fill,
    maker_limit_price,
    taker_fee,
    usable_ask_depth_usd,
    walk_ask_book,
)
from stratus.services.execution_cost import (
    ExecutionCostIntegrityError,
    purchased_side_quote,
    walk_taker_ask_levels,
)

logger = logging.getLogger(__name__)


async def execute_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int, int]:
    """Dispatch execution by the runtime mode. LIVE places real orders on the
    CLOB; PAPER runs the simulator. Mode is only reachable through the
    validation gate and the /mode confirmation flow."""
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            mode = (await get_runtime_control(session)).mode
        if mode is ExecutionMode.LIVE:
            from stratus.jobs.execute_live_signals import execute_live_signals

            result = await execute_live_signals(settings, sessions)
        else:
            result = await execute_paper_signals(settings, sessions)
        async with sessions.begin() as session:
            recorded = await capture_selection_policy_decisions(session)
        if recorded:
            logger.info("recorded %d prospective selection-policy decisions", recorded)
        return result


def _paper_fee_rate(market: Market, *, taker: bool) -> Decimal:
    """Fee the simulator should charge. Maker fills on taker-only fee markets
    (the Polymarket default) pay nothing."""
    if not market.fees_enabled:
        return Decimal(0)
    if not taker and market.fee_taker_only:
        return Decimal(0)
    return market.fee_rate


async def execute_paper_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int, int]:
    """Execute prospective paper orders only from persisted CLOB evidence.

    The first transaction commits an order plus immutable 0038 intent.  A
    later price cycle must begin after that witness; only then may this job
    consume the first eligible attested book and create atomic fill legs.
    """

    settings = settings or get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.paper_order_ttl_minutes)
    expired = 0
    posted = 0
    unfilled = 0
    executed = 0
    async with session_factory_scope(settings, session_factory) as sessions:
        try:
            # Phase 1: make the intent durable.  No execution book is selected
            # in this transaction.
            async with sessions.begin() as session:
                await acquire_station_reliability_gate_lock(session)
                control = await get_runtime_control(session)
                if control.kill_switch or control.halted_reason:
                    logger.info("paper execution blocked by runtime control")
                    return 0, 0, 0
                loss_limits, risk_expired = await enforce_loss_limits(
                    session,
                    mode=ExecutionMode.PAPER,
                    initial_bankroll_usd=settings.initial_bankroll_usd,
                    timezone=settings.risk_timezone,
                    now=now,
                )
                expired += risk_expired
                if loss_limits.daily_breached or loss_limits.weekly_breached:
                    logger.warning(
                        "paper execution blocked by loss limit: daily=%s weekly=%s expired=%d",
                        loss_limits.daily_breached,
                        loss_limits.weekly_breached,
                        risk_expired,
                    )
                    return 0, expired, 0

                nonpositive_pending_ids = list(
                    (
                        await session.scalars(
                            select(Signal.id).where(
                                Signal.status == SignalStatus.PENDING,
                                Signal.size_usd <= 0,
                            )
                        )
                    ).all()
                )
                if nonpositive_pending_ids:
                    expired += await expire_pending_signals(
                        session,
                        nonpositive_pending_ids,
                        reason="paper_order_size_nonpositive",
                    )
                    await expire_resting_paper_orders(session, nonpositive_pending_ids)

                legacy_pending_ids = list(
                    (
                        await session.scalars(
                            select(Signal.id).where(
                                Signal.status == SignalStatus.PENDING,
                                (
                                    (Signal.benchmark_protocol != CLOB_DUAL_PROTOCOL)
                                    | Signal.token_id.is_(None)
                                    | Signal.clob_snapshot_id.is_(None)
                                ),
                            )
                        )
                    ).all()
                )
                if legacy_pending_ids:
                    expired += await expire_pending_signals(
                        session,
                        legacy_pending_ids,
                        reason="legacy_price_protocol",
                    )
                    await expire_resting_paper_orders(session, legacy_pending_ids)

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
                    expired += await expire_pending_signals(
                        session,
                        unproven_pending_ids,
                        reason="station_reliability_provenance_missing",
                    )
                    await expire_resting_paper_orders(session, unproven_pending_ids)

                rows = (
                    await session.execute(
                        select(Signal, Market, Station, Order, ExecutionOrderIntent)
                        .join(Market, Signal.market_id == Market.id)
                        .join(Station, Market.station_id == Station.id)
                        .outerjoin(
                            Order,
                            and_(
                                Order.signal_id == Signal.id,
                                Order.mode == ExecutionMode.PAPER,
                            ),
                        )
                        .outerjoin(
                            ExecutionOrderIntent,
                            ExecutionOrderIntent.order_id == Order.id,
                        )
                        .where(
                            Signal.status == SignalStatus.PENDING,
                            Signal.benchmark_protocol == CLOB_DUAL_PROTOCOL,
                            Signal.token_id.is_not(None),
                            Signal.clob_snapshot_id.is_not(None),
                            Station.active.is_(True),
                            Station.stage.in_((StationStage.PAPER, StationStage.APPROVED)),
                        )
                        .order_by(Signal.ts, Signal.id)
                        .with_for_update(of=Signal, skip_locked=True)
                    )
                ).all()

                current_reliability = await latest_station_reliability_by_station(
                    session,
                    sorted({station.id for _signal, _market, station, _order, _intent in rows}),
                )

                # Expiration is safe before the capacity preflight: it cannot
                # create exposure and prevents stale signals from lingering.
                active: list[
                    tuple[Signal, Market, Station, Order | None, ExecutionOrderIntent | None]
                ] = []
                for signal, market, station, order, intent in rows:
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
                        require_reliable=False,
                    )
                    if reliability_reasons:
                        failure_reason = reliability_reasons[0]
                        if intent is not None:
                            await record_terminal_no_fill(
                                session,
                                intent.id,
                                terminal_status="rejected",
                                failure_reason=failure_reason,
                            )
                        else:
                            signal.status = SignalStatus.EXPIRED
                            signal.skip_reason = failure_reason
                            if order is not None:
                                order.status = "rejected"
                        expired += 1
                        continue
                    if now - signal.ts <= ttl:
                        active.append((signal, market, station, order, intent))
                        continue
                    if intent is not None:
                        await record_terminal_no_fill(
                            session,
                            intent.id,
                            terminal_status=(
                                "unfilled_maker"
                                if intent.order_style == "maker"
                                else "expired"
                            ),
                            failure_reason=(
                                "unfilled_maker"
                                if intent.order_style == "maker"
                                else "paper_execution_timeout"
                            ),
                        )
                    else:
                        signal.status = SignalStatus.EXPIRED
                        signal.skip_reason = "paper_execution_timeout"
                        if order is not None:
                            order.status = "unfilled_maker"
                    expired += 1

                if active:
                    capacity_gate = await acquire_execution_capacity_gate(
                        session,
                        settings,
                        now=datetime.now(UTC),
                    )
                    if not capacity_gate.allowed:
                        return 0, expired, 0
                    control = await get_runtime_control(session)
                    if (
                        control.kill_switch
                        or control.halted_reason
                        or control.mode is not ExecutionMode.PAPER
                    ):
                        logger.info("paper intent blocked by authoritative runtime control")
                        return 0, expired, 0
                    if any(
                        intent is None and order is None
                        for _, _, _, order, intent in active
                    ):
                        release_gate = await probability_release_gate(session, settings)
                        if not release_gate.allowed:
                            logger.error(
                                "paper intent blocked by probability release epoch: "
                                "reason=%s active=%s candidate=%s",
                                release_gate.reason,
                                release_gate.active_cohort,
                                release_gate.candidate_cohort,
                            )
                            return 0, expired, 0

                for signal, market, _station, order, intent in active:
                    if intent is not None:
                        continue
                    if order is not None:
                        # A committed post-boundary order without an intent is
                        # an invariant violation.  Never try to infer/backfill
                        # its missing decision evidence.
                        order.status = "rejected"
                        signal.status = SignalStatus.EXPIRED
                        signal.skip_reason = "execution_intent_missing"
                        expired += 1
                        logger.error(
                            "paper order %d has no immutable execution intent; rejected",
                            order.id,
                        )
                        continue
                    try:
                        async with session.begin_nested():
                            snapshot = await session.get(
                                ClobMarketSnapshot,
                                signal.clob_snapshot_id,
                            )
                            if snapshot is None:
                                raise ExecutionCostIntegrityError(
                                    "signal decision snapshot is unavailable"
                                )
                            quote = purchased_side_quote(
                                snapshot,
                                signal.side,
                                signal.model_prob,
                            )
                            style = "maker" if settings.paper_maker_execution else "taker"
                            limit_price = (
                                maker_limit_price(quote.best_bid, quote.best_ask)
                                if style == "maker"
                                else min(
                                    Decimal("0.999999"),
                                    signal.market_price + Decimal("0.01"),
                                )
                            )
                            if limit_price is None:
                                raise ExecutionCostIntegrityError(
                                    "decision book cannot support a passive limit"
                                )
                            requested_shares = (signal.size_usd / limit_price).quantize(
                                Decimal("0.000001")
                            )
                            expected_fee = taker_fee(
                                requested_shares,
                                limit_price,
                                _paper_fee_rate(market, taker=style == "taker"),
                            )
                            order = Order(
                                signal_id=signal.id,
                                mode=ExecutionMode.PAPER,
                                clob_order_id=f"paper-{signal.id}",
                                token_id=quote.token_id,
                                side=signal.side,
                                price=limit_price,
                                size_usd=signal.size_usd,
                                filled_usd=Decimal(0),
                                fee_usd=None,
                                status=(
                                    PAPER_STATUS_RESTING
                                    if style == "maker"
                                    else PAPER_STATUS_PENDING
                                ),
                            )
                            session.add(order)
                            await session.flush()
                            await create_intent_for_order(
                                session,
                                order.id,
                                release_image_digest=settings.release_image_digest,
                                order_style=style,
                                expected_fee_usd=expected_fee,
                                expected_fee_unavailable_reason=None,
                            )
                            posted += 1
                    except (ExecutionCostIntegrityError, PaperExecutionError) as exc:
                        signal.status = SignalStatus.EXPIRED
                        signal.skip_reason = "execution_intent_invalid"
                        expired += 1
                        logger.error(
                            "paper intent rejected for signal %d: %s",
                            signal.id,
                            exc,
                        )

            # Phase 2: use only the first eligible request that began after
            # the committed intent.  This is a persisted evidence read; the
            # executor performs no unaudited HTTP request.
            async with sessions.begin() as session:
                await acquire_station_reliability_gate_lock(session)
                capacity_gate = await acquire_execution_capacity_gate(
                    session,
                    settings,
                    now=datetime.now(UTC),
                )
                if not capacity_gate.allowed:
                    return 0, expired, 0
                control = await get_runtime_control(session)
                if (
                    control.kill_switch
                    or control.halted_reason
                    or control.mode is not ExecutionMode.PAPER
                ):
                    logger.info("paper fill blocked by authoritative runtime control")
                    return 0, expired, 0
                pending = (
                    await session.execute(
                        select(Signal, Market, Station, Order, ExecutionOrderIntent)
                        .join(Market, Market.id == Signal.market_id)
                        .join(Station, Station.id == Market.station_id)
                        .join(
                            Order,
                            and_(
                                Order.signal_id == Signal.id,
                                Order.mode == ExecutionMode.PAPER,
                            ),
                        )
                        .join(
                            ExecutionOrderIntent,
                            ExecutionOrderIntent.order_id == Order.id,
                        )
                        .where(
                            Signal.status == SignalStatus.PENDING,
                            Order.status.in_((PAPER_STATUS_PENDING, PAPER_STATUS_RESTING)),
                        )
                        .order_by(ExecutionOrderIntent.intent_at, ExecutionOrderIntent.id)
                        .with_for_update(of=Order, skip_locked=True)
                    )
                ).all()
                current_reliability = await latest_station_reliability_by_station(
                    session,
                    sorted(
                        {
                            station.id
                            for _signal, _market, station, _order, _intent in pending
                        }
                    ),
                )
                for signal, market, station, order, intent in pending:
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
                        now=datetime.now(UTC),
                        require_reliable=False,
                    )
                    if reliability_reasons:
                        await record_terminal_no_fill(
                            session,
                            intent.id,
                            terminal_status="rejected",
                            failure_reason=reliability_reasons[0],
                        )
                        expired += 1
                        continue
                    try:
                        async with session.begin_nested():
                            snapshots = await eligible_clob_snapshots_at_or_after(
                                session,
                                market_id=market.id,
                                not_before=intent.intent_at,
                            )
                            if market.fees_enabled and market.fee_exponent != 1:
                                raise PaperExecutionError(
                                    f"unsupported fee exponent {market.fee_exponent}"
                                )
                            qualifying: tuple[
                                ClobMarketSnapshot,
                                tuple[PaperFillLegSpec, ...],
                            ] | None = None
                            for snapshot in snapshots:
                                # Re-attest candidates in strict witness order.
                                # A corrupt earlier row raises; it is never
                                # silently skipped in favour of a later book.
                                _batch, _stored_snapshot, book = (
                                    await load_attested_token_book(
                                        session,
                                        snapshot_id=snapshot.id,
                                        token_id=intent.token_id,
                                    )
                                )
                                specs = _paper_fill_leg_specs(
                                    signal,
                                    market,
                                    order,
                                    intent,
                                    book,
                                )
                                if specs:
                                    qualifying = (snapshot, specs)
                                    break
                            if qualifying is None:
                                unfilled += 1
                                continue
                            snapshot, specs = qualifying
                            await record_paper_fill(
                                session,
                                intent.id,
                                snapshot.id,
                                specs,
                            )
                            executed += 1
                    except (ExecutionCostIntegrityError, PaperExecutionError) as exc:
                        unfilled += 1
                        logger.warning(
                            "invalid attested paper book for signal %d: %s",
                            signal.id,
                            exc,
                        )
        except DBAPIError:
            logger.exception("paper execution capacity/evidence transaction failed")
            try:
                async with sessions.begin() as session:
                    await engage_capacity_failure(
                        session,
                        reason="capacity_or_execution_evidence_unavailable",
                    )
            except DBAPIError:
                logger.exception("paper execution could not persist capacity kill switch")
            return 0, expired, unfilled

    logger.info(
        "paper execution complete: executed=%d posted=%d expired=%d unfilled=%d",
        executed,
        posted,
        expired,
        unfilled,
    )
    return executed, expired, unfilled


def _paper_fill_leg_specs(
    signal: Signal,
    market: Market,
    order: Order,
    intent: ExecutionOrderIntent,
    book: dict[str, Any],
) -> tuple[PaperFillLegSpec, ...]:
    quote = best_bid_ask(book)
    if quote is None:
        return ()
    _best_bid, best_ask = quote
    fee_rate = _paper_fee_rate(market, taker=intent.order_style == "taker")
    if intent.order_style == "maker":
        if best_ask > order.price:
            return ()
        if usable_ask_depth_usd(book, max_price=order.price) < signal.size_usd:
            return ()
        fill = maker_fill(
            limit_price=order.price,
            target_usd=signal.size_usd,
            fee_rate=fee_rate,
        )
        return (
            PaperFillLegSpec(
                sequence=1,
                shares=fill.size_shares,
                price=order.price,
                fee_usd=fill.fee_usd,
                liquidity_role="maker",
            ),
        )

    if usable_ask_depth_usd(book, max_price=order.price) < 3 * signal.size_usd:
        return ()
    walk = walk_taker_ask_levels(
        executable_ask_levels(book, max_price=order.price),
        target_notional_usd=signal.size_usd,
        fee_fn=lambda shares, price: taker_fee(shares, price, fee_rate),
    )
    return tuple(
        PaperFillLegSpec(
            sequence=leg.sequence,
            shares=leg.shares,
            price=leg.price,
            fee_usd=leg.fee_usd,
            liquidity_role="taker",
        )
        for leg in walk.legs
    )


def _taker_execution(
    signal: Signal, market: Market, book: dict[str, Any], *, now: datetime
) -> PaperExecutionValue | None:
    """Legacy taker simulation: walk the ask book at signal price + 1c."""
    max_price = min(Decimal("0.999999"), signal.market_price + Decimal("0.01"))
    # The generation-side depth gate uses the YES snapshot as a proxy;
    # re-check 3x depth against the book this order would actually execute
    # on. The signal stays pending until TTL.
    executed_depth = usable_ask_depth_usd(book, max_price=max_price)
    if executed_depth < 3 * signal.size_usd:
        logger.info(
            "insufficient executed-book depth for signal %d: $%s < 3x $%s",
            signal.id,
            executed_depth,
            signal.size_usd,
        )
        return None
    fill = walk_ask_book(
        book,
        target_usd=signal.size_usd,
        max_price=max_price,
        fee_rate=_paper_fee_rate(market, taker=True),
    )
    if fill is None:
        return None
    return PaperExecutionValue(
        signal_id=signal.id,
        market_id=market.id,
        side=signal.side,
        requested_usd=signal.size_usd,
        filled_usd=fill.filled_usd,
        avg_price=fill.avg_price,
        size_shares=fill.size_shares,
        fee_usd=fill.fee_usd,
        total_cost_usd=fill.total_cost_usd,
        filled_at=now,
    )


def _process_maker_signal(
    signal: Signal,
    market: Market,
    book: dict[str, Any],
    *,
    resting_price: Decimal | None,
    resting_order_id: int | None,
    now: datetime,
) -> RestingPaperOrder | MakerFillValue | PaperExecutionValue | None:
    """One maker-simulation step for a pending signal.

    No resting order yet: post a passive limit at the floored midpoint; a
    limit that would cross the ask executes immediately as a marketable
    taker order instead. With a resting order: fill at our own limit only
    when the ask has traded through it with enough size, otherwise keep
    waiting until the signal's TTL.
    """
    quote = best_bid_ask(book)
    if resting_price is None:
        if quote is None:
            return None
        best_bid, best_ask = quote
        limit = maker_limit_price(best_bid, best_ask)
        if limit is None:
            return None
        if limit >= best_ask:
            return _taker_execution(signal, market, book, now=now)
        return RestingPaperOrder(
            signal_id=signal.id,
            side=signal.side,
            limit_price=limit,
            size_usd=signal.size_usd,
            token_id=signal.token_id,
        )

    assert resting_order_id is not None
    if quote is None:
        return None
    _, best_ask = quote
    if best_ask > resting_price:
        return None
    # Conservative fill model: the market must offer at or below our bid
    # with at least our full size available before we count the fill.
    if usable_ask_depth_usd(book, max_price=resting_price) < signal.size_usd:
        return None
    fill = maker_fill(
        limit_price=resting_price,
        target_usd=signal.size_usd,
        fee_rate=_paper_fee_rate(market, taker=False),
    )
    return MakerFillValue(
        order_id=resting_order_id,
        signal_id=signal.id,
        market_id=market.id,
        side=signal.side,
        limit_price=resting_price,
        size_shares=fill.size_shares,
        filled_usd=fill.filled_usd,
        fee_usd=fill.fee_usd,
        total_cost_usd=fill.total_cost_usd,
        filled_at=now,
    )


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(execute_signals(settings))


if __name__ == "__main__":
    main()
