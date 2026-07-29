from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    BankrollLog,
    ClobBookBatch,
    ClobMarketSnapshot,
    ExecutionMode,
    Market,
    MarketPrice,
    MarketStatus,
    Position,
    PositionStatus,
    Station,
    TradeSide,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.repositories.validation_ledger import seal_prospective_validation_outcomes
from stratus.services.clob_evidence import CLOB_DUAL_PROTOCOL
from stratus.services.settlement import closing_line_value, position_pnl


@dataclass(frozen=True, slots=True)
class SettlementResult:
    resolved: int
    paper_positions: int
    live_positions: int
    paper_pnl: Decimal
    live_pnl: Decimal


@dataclass(frozen=True, slots=True)
class ClosingLine:
    midpoint: Decimal
    snapshot_id: int | None = None
    snapshot_hash: str | None = None


class SettlementIntegrityError(RuntimeError):
    """Operational market projection conflicts with immutable resolution truth."""


_BANKROLL_LOG_LOCK_SEED = 71_020_260_715


async def _append_paper_bankroll_log(
    session: AsyncSession,
    *,
    pnl: Decimal,
    position_count: int,
    settled_at: datetime,
    initial_bankroll_usd: Decimal,
) -> None:
    """Append one cumulative paper balance without collisions or lost updates.

    Multiple independent Gamma events commonly resolve in the same settlement
    cycle and therefore share ``settled_at``.  ``bankroll_log`` is keyed by
    ``(ts, mode)``, so the timestamp must be made monotonically unique while a
    transaction-scoped lock serializes the read-modify-append operation across
    concurrent workers.
    """

    await session.execute(
        text(
            f"SELECT pg_advisory_xact_lock(hashtextextended(:lock_name, {_BANKROLL_LOG_LOCK_SEED}))"
        ),
        {"lock_name": "bankroll-log:paper"},
    )
    latest = await session.scalar(
        select(BankrollLog)
        .where(BankrollLog.mode == ExecutionMode.PAPER)
        .order_by(BankrollLog.ts.desc())
        .limit(1)
    )
    balance = Decimal(latest.balance_usd if latest is not None else initial_bankroll_usd) + pnl
    log_at = settled_at
    if latest is not None and latest.ts >= log_at:
        log_at = latest.ts + timedelta(microseconds=1)
    session.add(
        BankrollLog(
            ts=log_at,
            mode=ExecutionMode.PAPER,
            balance_usd=balance.quantize(Decimal("0.0001")),
            event=f"settled {position_count} paper position(s)",
        )
    )


async def _close_position(
    session: AsyncSession,
    position: Position,
    *,
    resolved_yes: bool,
    market_closed_at: datetime,
    settled_at: datetime,
) -> Decimal:
    pnl = position_pnl(
        side=position.side,
        resolved_yes=resolved_yes,
        size_shares=position.size_shares,
        cost_usd=position.size_usd,
    )
    position.pnl_usd = pnl
    if position.clv is None:
        closing_line = await _closing_midpoint(session, position, closed_at=market_closed_at)
        if closing_line is not None:
            position.clv = closing_line_value(
                traded_side_midpoint=closing_line.midpoint,
                entry_price=position.avg_price,
            )
            position.clv_snapshot_id = closing_line.snapshot_id
            position.clv_snapshot_hash = closing_line.snapshot_hash
    position.status = PositionStatus.CLOSED
    # Risk/accounting uses the time STRATUS actually recognized the P&L.
    # Gamma's market close is retained separately as the non-leaking CLV cut-off.
    position.closed_at = settled_at
    position.market_closed_at = market_closed_at
    return pnl


async def _closing_midpoint(
    session: AsyncSession, position: Position, *, closed_at: datetime
) -> ClosingLine | None:
    """Return a non-leaking closing-line midpoint for a position.

    The previous implementation required a price snapshot inside a two-minute
    window around T-15m. In production the collector cadence can drift or be
    interrupted; that narrow window made CLV null for otherwise valid settled
    positions. The robust rule is:

    1. prefer the latest snapshot at or before T-15m;
    2. if the market was created/observed too late for that, fall back to the
       latest snapshot at or before the recorded close time;
    3. never use a price after close, because that would leak settlement info.
    """
    clv_target = closed_at - timedelta(minutes=15)
    if position.benchmark_protocol == CLOB_DUAL_PROTOCOL:
        side_midpoint = (
            ClobMarketSnapshot.yes_midpoint
            if position.side is TradeSide.BUY_YES
            else ClobMarketSnapshot.no_midpoint
        )
        for cutoff in (clv_target, closed_at):
            row = (
                await session.execute(
                    select(
                        ClobMarketSnapshot.id,
                        ClobMarketSnapshot.record_hash,
                        side_midpoint,
                    )
                    .join(
                        ClobBookBatch,
                        ClobBookBatch.id == ClobMarketSnapshot.batch_id,
                    )
                    .where(
                        ClobMarketSnapshot.market_id == position.market_id,
                        ClobMarketSnapshot.eligible.is_(True),
                        side_midpoint.is_not(None),
                        ClobBookBatch.first_seen_at <= cutoff,
                    )
                    .order_by(ClobBookBatch.first_seen_at.desc())
                    .limit(1)
                )
            ).one_or_none()
            if row is not None:
                snapshot_id, snapshot_hash, midpoint = row
                return ClosingLine(
                    midpoint=midpoint,
                    snapshot_id=snapshot_id,
                    snapshot_hash=snapshot_hash,
                )
        return None

    midpoint = await session.scalar(
        select(MarketPrice)
        .where(
            MarketPrice.market_id == position.market_id,
            MarketPrice.ts <= clv_target,
        )
        .order_by(MarketPrice.ts.desc())
        .limit(1)
    )
    if midpoint is not None:
        legacy_midpoint = (
            midpoint.midpoint
            if position.side is TradeSide.BUY_YES
            else Decimal(1) - midpoint.midpoint
        )
        return ClosingLine(midpoint=legacy_midpoint)
    fallback = await session.scalar(
        select(MarketPrice)
        .where(
            MarketPrice.market_id == position.market_id,
            MarketPrice.ts <= closed_at,
        )
        .order_by(MarketPrice.ts.desc())
        .limit(1)
    )
    if fallback is None:
        return None
    legacy_midpoint = (
        fallback.midpoint if position.side is TradeSide.BUY_YES else Decimal(1) - fallback.midpoint
    )
    return ClosingLine(midpoint=legacy_midpoint)


async def backfill_missing_clv(session: AsyncSession, *, batch_size: int = 500) -> int:
    """Best-effort CLV repair for already-settled positions.

    Used by maintenance/backfills and safe to run repeatedly. It only fills
    null CLV values and uses the same non-leaking price selection as normal
    settlement.
    """
    positions = (
        (
            await session.execute(
                select(Position)
                .where(
                    Position.status == PositionStatus.CLOSED,
                    Position.closed_at.is_not(None),
                    Position.clv.is_(None),
                )
                .order_by(Position.id)
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    updated = 0
    for position in positions:
        clv_closed_at = position.market_closed_at or position.closed_at
        if clv_closed_at is None:
            continue
        closing_line = await _closing_midpoint(session, position, closed_at=clv_closed_at)
        if closing_line is None:
            continue
        position.clv = closing_line_value(
            traded_side_midpoint=closing_line.midpoint,
            entry_price=position.avg_price,
        )
        position.clv_snapshot_id = closing_line.snapshot_id
        position.clv_snapshot_hash = closing_line.snapshot_hash
        updated += 1
    if updated:
        await session.flush()
    return updated


async def settle_resolved_markets(
    session: AsyncSession,
    *,
    resolutions: dict[int, bool],
    closing_times: dict[int, datetime],
    settled_at: datetime,
    initial_bankroll_usd: Decimal,
    resolution_event_id: int | None = None,
) -> SettlementResult:
    """Resolve markets and close their open positions in both modes.

    Paper settlement synthesises a bankroll balance from realised PnL. Live
    settlement records PnL only: the real USDC balance does not change until
    the operator redeems winning tokens on-chain, so a redemption reminder is
    enqueued instead of a synthetic balance.
    """
    if not resolutions:
        return SettlementResult(0, 0, 0, Decimal(0), Decimal(0))
    markets = (
        (
            await session.execute(
                select(Market)
                .where(Market.id.in_(resolutions))
                .order_by(Market.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    if {market.id for market in markets} != set(resolutions):
        raise SettlementIntegrityError("settlement market set changed while locking event")
    newly_resolved = []
    for market in markets:
        outcome = resolutions[market.id]
        if market.status is MarketStatus.RESOLVED:
            if market.resolved_outcome is None or bool(market.resolved_outcome) != outcome:
                raise SettlementIntegrityError(
                    f"resolved market {market.id} conflicts with immutable event outcome"
                )
        else:
            market.status = MarketStatus.RESOLVED
            market.resolved_outcome = outcome
            newly_resolved.append(market.id)
        if resolution_event_id is not None:
            if (
                market.resolution_event_id is not None
                and market.resolution_event_id != resolution_event_id
            ):
                raise SettlementIntegrityError(
                    f"market {market.id} references another resolution event"
                )
            market.resolution_event_id = resolution_event_id

    positions = (
        (
            await session.execute(
                select(Position)
                .where(
                    Position.market_id.in_(resolutions),
                    Position.status == PositionStatus.OPEN,
                )
                .order_by(Position.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )

    paper_pnl = Decimal(0)
    live_pnl = Decimal(0)
    paper_count = 0
    live_count = 0
    live_winners: list[Position] = []
    for position in positions:
        resolved_yes = resolutions[position.market_id]
        market_closed_at = closing_times.get(position.market_id, settled_at)
        pnl = await _close_position(
            session,
            position,
            resolved_yes=resolved_yes,
            market_closed_at=market_closed_at,
            settled_at=settled_at,
        )
        if position.mode is ExecutionMode.LIVE:
            live_pnl += pnl
            live_count += 1
            won = (position.side.value == "buy_yes") == resolved_yes
            if won and position.size_shares > 0:
                live_winners.append(position)
        else:
            paper_pnl += pnl
            paper_count += 1

    if paper_count:
        await _append_paper_bankroll_log(
            session,
            pnl=paper_pnl,
            position_count=paper_count,
            settled_at=settled_at,
            initial_bankroll_usd=initial_bankroll_usd,
        )

    if live_winners:
        await _enqueue_redemption_reminder(
            session,
            live_winners,
            settled_at,
            resolution_event_id=resolution_event_id,
        )

    if resolution_event_id is not None:
        await session.flush()
        await seal_prospective_validation_outcomes(
            session,
            resolution_event_id=resolution_event_id,
        )

    await session.flush()
    return SettlementResult(
        resolved=len(newly_resolved),
        paper_positions=paper_count,
        live_positions=live_count,
        paper_pnl=paper_pnl.quantize(Decimal("0.0001")),
        live_pnl=live_pnl.quantize(Decimal("0.0001")),
    )


async def _enqueue_redemption_reminder(
    session: AsyncSession,
    winners: list[Position],
    settled_at: datetime,
    *,
    resolution_event_id: int | None,
) -> None:
    market_ids = {position.market_id for position in winners}
    rows = (
        await session.execute(
            select(Market.id, Station.icao)
            .join(Station, Station.id == Market.station_id)
            .where(Market.id.in_(market_ids))
        )
    ).all()
    stations: dict[int, str] = {market_id: icao for market_id, icao in rows}
    lines = [
        f"{stations.get(position.market_id, position.market_id)}: "
        f"{position.size_shares:.2f} winning shares"
        for position in winners
    ]
    await enqueue_notification(
        session,
        (
            f"live-redeem:event:{resolution_event_id}"
            if resolution_event_id is not None
            else f"live-redeem:{settled_at:%Y%m%dT%H%M%S%f}"
        ),
        "ACTION REQUIRED: redeem winning Polymarket positions manually via the "
        "UI. STRATUS does not redeem on-chain.\n" + "\n".join(lines),
    )
