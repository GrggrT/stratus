from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient
from stratus.config import Settings, get_settings
from stratus.db.models import ExecutionMode, Market, Signal, SignalStatus, TradeSide
from stratus.db.session import session_factory_scope
from stratus.repositories.execution import (
    PaperExecutionValue,
    expire_pending_signals,
    persist_paper_executions,
)
from stratus.repositories.risk import enforce_loss_limits
from stratus.repositories.runtime_control import get_runtime_control
from stratus.services.execution import (
    PaperExecutionError,
    usable_ask_depth_usd,
    walk_ask_book,
)

logger = logging.getLogger(__name__)
PAPER_ORDER_TTL = timedelta(minutes=10)


async def execute_signals(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory.begin() as session:
            control = await get_runtime_control(session)
            if control.kill_switch or control.halted_reason:
                logger.info("paper execution blocked by runtime control")
                return 0, 0, 0
            if control.mode is not ExecutionMode.PAPER:
                raise RuntimeError(
                    "live execution is disabled until the validation gate passes"
                )
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

        expired_ids = [signal.id for signal, _ in rows if now - signal.ts > PAPER_ORDER_TTL]
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

        books_by_token = {}
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

        executions = []
        unfilled = 0
        for signal, market in active_rows:
            token_id = token_by_signal.get(signal.id)
            book = books_by_token.get(token_id) if token_id else None
            if book is None:
                unfilled += 1
                continue
            max_price = min(Decimal("0.999999"), signal.market_price + Decimal("0.01"))
            try:
                if market.fees_enabled and market.fee_exponent != 1:
                    raise PaperExecutionError(
                        f"unsupported fee exponent {market.fee_exponent}"
                    )
                # The generation-side depth gate uses the YES snapshot as a
                # proxy; re-check 3x depth against the book this order would
                # actually execute on. The signal stays pending until TTL.
                executed_depth = usable_ask_depth_usd(book, max_price=max_price)
                if executed_depth < 3 * signal.size_usd:
                    logger.info(
                        "insufficient executed-book depth for signal %d: $%s < 3x $%s",
                        signal.id,
                        executed_depth,
                        signal.size_usd,
                    )
                    unfilled += 1
                    continue
                fill = walk_ask_book(
                    book,
                    target_usd=signal.size_usd,
                    max_price=max_price,
                    fee_rate=market.fee_rate if market.fees_enabled else Decimal(0),
                )
            except PaperExecutionError as exc:
                logger.warning("invalid paper book for signal %d: %s", signal.id, exc)
                unfilled += 1
                continue
            if fill is None:
                unfilled += 1
                continue
            executions.append(
                PaperExecutionValue(
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
            )

        async with session_factory.begin() as session:
            expired = await expire_pending_signals(session, expired_ids)
            executed = await persist_paper_executions(session, executions)
        logger.info(
            "paper execution complete: pending=%d executed=%d expired=%d unfilled=%d",
            len(rows),
            executed,
            expired,
            unfilled,
        )
        return executed, expired, unfilled


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(execute_signals(settings))


if __name__ == "__main__":
    main()
