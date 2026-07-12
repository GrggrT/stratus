from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient
from stratus.config import Settings, get_settings
from stratus.db.models import ExecutionMode, Market, Signal, SignalStatus, TradeSide
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import (
    MakerFillValue,
    PaperExecutionValue,
    RestingPaperOrder,
    expire_pending_signals,
    expire_resting_paper_orders,
    insert_resting_paper_orders,
    persist_maker_fills,
    persist_paper_executions,
    resting_paper_orders,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.execution import (
    PaperExecutionError,
    best_bid_ask,
    maker_fill,
    maker_limit_price,
    usable_ask_depth_usd,
    walk_ask_book,
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

            return await execute_live_signals(settings, sessions)
        return await execute_paper_signals(settings, sessions)


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
    settings = settings or get_settings()
    now = datetime.now(UTC)
    ttl = timedelta(minutes=settings.paper_order_ttl_minutes)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory.begin() as session:
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
            if loss_limits.daily_breached or loss_limits.weekly_breached:
                logger.warning(
                    "paper execution blocked by loss limit: daily=%s weekly=%s expired=%d",
                    loss_limits.daily_breached,
                    loss_limits.weekly_breached,
                    risk_expired,
                )
                return 0, risk_expired, 0
            rows = (
                await session.execute(
                    select(Signal, Market)
                    .join(Market, Signal.market_id == Market.id)
                    .where(Signal.status == SignalStatus.PENDING)
                    .order_by(Signal.ts)
                )
            ).all()
            resting_prices: dict[int, Decimal] = {}
            resting_ids: dict[int, int] = {}
            if settings.paper_maker_execution:
                for signal_id, order in (await resting_paper_orders(session)).items():
                    resting_prices[signal_id] = order.price
                    resting_ids[signal_id] = order.id

        expired_ids = [signal.id for signal, _ in rows if now - signal.ts > ttl]
        active_rows = [
            (signal, market)
            for signal, market in rows
            if signal.id not in set(expired_ids)
        ]
        token_by_signal: dict[int, str] = {}
        for signal, market in active_rows:
            token_id = (
                market.yes_token_id
                if signal.side is TradeSide.BUY_YES
                else market.no_token_id
            )
            if token_id:
                token_by_signal[signal.id] = token_id

        books_by_token: dict[str, dict[str, Any]] = {}
        if token_by_signal:
            async with ClobClient(
                books_url=settings.clob_books_url,
                batch_size=settings.clob_batch_size,
            ) as client:
                books = await client.fetch_books(list(dict.fromkeys(token_by_signal.values())))
            books_by_token = {
                str(book.get("asset_id")): book
                for book in books
                if isinstance(book.get("asset_id"), str)
            }

        executions: list[PaperExecutionValue] = []
        placements: list[RestingPaperOrder] = []
        maker_fills: list[MakerFillValue] = []
        unfilled = 0
        for signal, market in active_rows:
            token_id = token_by_signal.get(signal.id)
            book = books_by_token.get(token_id) if token_id else None
            if book is None:
                unfilled += 1
                continue
            try:
                if market.fees_enabled and market.fee_exponent != 1:
                    raise PaperExecutionError(
                        f"unsupported fee exponent {market.fee_exponent}"
                    )
                if settings.paper_maker_execution:
                    outcome = _process_maker_signal(
                        signal,
                        market,
                        book,
                        resting_price=resting_prices.get(signal.id),
                        resting_order_id=resting_ids.get(signal.id),
                        now=now,
                    )
                    if isinstance(outcome, RestingPaperOrder):
                        placements.append(outcome)
                    elif isinstance(outcome, MakerFillValue):
                        maker_fills.append(outcome)
                    elif isinstance(outcome, PaperExecutionValue):
                        executions.append(outcome)
                    else:
                        unfilled += 1
                    continue
                taker = _taker_execution(signal, market, book, now=now)
                if taker is None:
                    unfilled += 1
                else:
                    executions.append(taker)
            except PaperExecutionError as exc:
                logger.warning("invalid paper book for signal %d: %s", signal.id, exc)
                unfilled += 1
                continue

        async with session_factory.begin() as session:
            expired = await expire_pending_signals(
                session,
                expired_ids,
                reason=(
                    "unfilled_maker"
                    if settings.paper_maker_execution
                    else "paper_execution_timeout"
                ),
            )
            expired_orders = await expire_resting_paper_orders(session, expired_ids)
            posted = await insert_resting_paper_orders(session, placements)
            filled_maker = await persist_maker_fills(session, maker_fills)
            executed = await persist_paper_executions(session, executions)
        logger.info(
            "paper execution complete: pending=%d executed=%d maker_filled=%d "
            "posted=%d expired=%d expired_orders=%d unfilled=%d",
            len(rows),
            executed,
            filled_maker,
            posted,
            expired,
            expired_orders,
            unfilled,
        )
        return executed + filled_maker, expired, unfilled


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
