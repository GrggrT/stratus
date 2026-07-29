from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import (
    TradingVenue,
    VenueMode,
    VenueRuntimeControl,
)

_VENUE_ORDER = (TradingVenue.POLYMARKET, TradingVenue.BINANCE_SPOT)
_ALLOWED_MODES = {
    TradingVenue.POLYMARKET: frozenset({VenueMode.PAPER, VenueMode.LIVE}),
    TradingVenue.BINANCE_SPOT: frozenset(
        {
            VenueMode.DISABLED,
            VenueMode.BACKTEST,
            VenueMode.SHADOW,
            VenueMode.TESTNET,
            VenueMode.LIVE,
        }
    ),
}


@dataclass(frozen=True, slots=True)
class VenueRuntimeSnapshot:
    venue: TradingVenue
    enabled: bool
    mode: VenueMode
    kill_switch: bool
    halted_reason: str | None
    live_confirmation_until: datetime | None
    strategy_version: str | None
    risk_config_version: str
    last_reconciled_at: datetime | None
    updated_at: datetime | None


def venue_mode_allowed(venue: TradingVenue, mode: VenueMode) -> bool:
    return mode in _ALLOWED_MODES[venue]


def _fail_closed_snapshot(venue: TradingVenue) -> VenueRuntimeSnapshot:
    return VenueRuntimeSnapshot(
        venue=venue,
        enabled=False,
        mode=VenueMode.DISABLED,
        kill_switch=True,
        halted_reason="venue_runtime_control row missing",
        live_confirmation_until=None,
        strategy_version=None,
        risk_config_version="missing",
        last_reconciled_at=None,
        updated_at=None,
    )


def _snapshot(row: VenueRuntimeControl) -> VenueRuntimeSnapshot:
    return VenueRuntimeSnapshot(
        venue=row.venue,
        enabled=row.enabled,
        mode=row.mode,
        kill_switch=row.kill_switch,
        halted_reason=row.halted_reason,
        live_confirmation_until=row.live_confirmation_until,
        strategy_version=row.strategy_version,
        risk_config_version=row.risk_config_version,
        last_reconciled_at=row.last_reconciled_at,
        updated_at=row.updated_at,
    )


async def get_venue_runtime_control(
    session: AsyncSession, venue: TradingVenue
) -> VenueRuntimeSnapshot:
    row = await session.get(VenueRuntimeControl, venue)
    return _snapshot(row) if row is not None else _fail_closed_snapshot(venue)


async def list_venue_runtime_controls(
    session: AsyncSession,
) -> list[VenueRuntimeSnapshot]:
    rows = (await session.execute(select(VenueRuntimeControl))).scalars().all()
    by_venue = {row.venue: row for row in rows}
    return [
        _snapshot(by_venue[venue]) if venue in by_venue else _fail_closed_snapshot(venue)
        for venue in _VENUE_ORDER
    ]


async def _locked_control(session: AsyncSession, venue: TradingVenue) -> VenueRuntimeControl:
    row = await session.scalar(
        select(VenueRuntimeControl).where(VenueRuntimeControl.venue == venue).with_for_update()
    )
    if row is None:
        raise RuntimeError(f"venue control is missing: {venue.value}")
    return row


async def set_venue_enabled(session: AsyncSession, venue: TradingVenue, enabled: bool) -> None:
    row = await _locked_control(session, venue)
    row.enabled = enabled
    if not enabled:
        row.kill_switch = True
        row.live_confirmation_until = None
    row.updated_at = datetime.now(UTC)


async def set_venue_kill_switch(session: AsyncSession, venue: TradingVenue, enabled: bool) -> None:
    """Set the control bit only; execution-specific cancellation wraps this later."""
    row = await _locked_control(session, venue)
    if not enabled and row.halted_reason is not None:
        raise RuntimeError(f"venue is halted: {row.halted_reason}")
    if not enabled and (not row.enabled or row.mode is VenueMode.DISABLED):
        raise RuntimeError(f"venue is disabled: {venue.value}")
    row.kill_switch = enabled
    row.updated_at = datetime.now(UTC)


async def set_venue_mode(session: AsyncSession, venue: TradingVenue, mode: VenueMode) -> None:
    if not venue_mode_allowed(venue, mode):
        raise ValueError(f"mode {mode.value} is not valid for {venue.value}")
    row = await _locked_control(session, venue)
    row.mode = mode
    row.live_confirmation_until = None
    if mode is VenueMode.DISABLED:
        row.enabled = False
        row.kill_switch = True
    row.updated_at = datetime.now(UTC)


async def request_venue_live_confirmation(
    session: AsyncSession,
    venue: TradingVenue,
    *,
    now: datetime | None = None,
) -> datetime:
    row = await _locked_control(session, venue)
    current = now or datetime.now(UTC)
    deadline = current + timedelta(minutes=5)
    row.live_confirmation_until = deadline
    row.updated_at = current
    return deadline


async def halt_venue(session: AsyncSession, venue: TradingVenue, reason: str) -> None:
    if not reason.strip():
        raise ValueError("halt reason is required")
    row = await _locked_control(session, venue)
    row.kill_switch = True
    row.halted_reason = reason.strip()
    row.live_confirmation_until = None
    row.updated_at = datetime.now(UTC)


async def record_venue_reconciliation(
    session: AsyncSession,
    venue: TradingVenue,
    *,
    reconciled_at: datetime,
) -> None:
    row = await _locked_control(session, venue)
    row.last_reconciled_at = reconciled_at
    row.updated_at = datetime.now(UTC)
