from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import Market, MarketStatus, Station, WeatherKind


@dataclass(frozen=True, slots=True)
class DiscoveredMarket:
    polymarket_id: str
    event_id: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    station_id: int | None
    market_date: date
    kind: WeatherKind
    bucket_low_c: Decimal | None
    bucket_high_c: Decimal | None
    resolution_source: str | None
    rules_raw: str
    station_verified: bool
    station_verification_reason: str | None
    created_at: datetime
    resolves_at: datetime
    fees_enabled: bool
    fee_rate: Decimal
    fee_exponent: int
    fee_taker_only: bool


async def verify_market_station(
    session: AsyncSession, market_id: int
) -> tuple[bool, str]:
    """Manually approve a market's parsed station; shared by Telegram /verify
    and the operator command queue. Returns (unblocked, human message)."""
    market = (
        await session.execute(
            select(Market).where(Market.id == market_id).with_for_update()
        )
    ).scalar_one_or_none()
    if market is None:
        return False, "Market not found."
    if market.station_id is None:
        return False, "Cannot verify: station parser did not identify a station."
    if market.status is MarketStatus.RESOLVED:
        return False, "Market is already resolved."
    market.station_verified = True
    market.station_verification_reason = None
    market.status = MarketStatus.ACTIVE
    return True, f"Market {market_id} station verified; trading unblocked."


async def latest_station_before(
    session: AsyncSession, city: str, market_date: date
) -> Station | None:
    statement = (
        select(Station)
        .join(Market, Market.station_id == Station.id)
        .where(
            Station.city == city,
            Market.market_date < market_date,
            Market.station_verified.is_(True),
        )
        .order_by(Market.market_date.desc(), Market.created_at.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


async def upsert_market(
    session: AsyncSession, value: DiscoveredMarket
) -> tuple[Market, bool]:
    """Insert or update a market.

    Returns the market and whether a previously granted station verification
    was revoked. A verification applies to the exact rules text it was granted
    on: it stays sticky across re-discovery (so manual /verify approvals
    survive), but is revoked when the parser disagrees AND the rules text or
    resolution source changed since the verification.
    """
    market = (
        await session.execute(
            select(Market).where(Market.polymarket_id == value.polymarket_id).with_for_update()
        )
    ).scalar_one_or_none()
    is_new = market is None
    if market is None:
        market = Market(polymarket_id=value.polymarket_id)
        session.add(market)

    was_verified = bool(market.station_verified) if not is_new else False
    station_changed = market.station_id is not None and market.station_id != value.station_id
    rules_changed = not is_new and (
        market.rules_raw != value.rules_raw
        or (market.resolution_source or None) != (value.resolution_source or None)
    )
    if station_changed:
        station_verified = value.station_verified
    elif was_verified and not value.station_verified and rules_changed:
        station_verified = False
    else:
        station_verified = bool(market.station_verified or value.station_verified)
    revoked = was_verified and not station_verified

    market.event_id = value.event_id
    market.condition_id = value.condition_id
    market.yes_token_id = value.yes_token_id
    market.no_token_id = value.no_token_id
    market.station_id = value.station_id
    market.market_date = value.market_date
    market.kind = value.kind
    market.bucket_low_c = value.bucket_low_c
    market.bucket_high_c = value.bucket_high_c
    market.resolution_source = value.resolution_source
    market.rules_raw = value.rules_raw
    market.station_verified = station_verified
    market.station_verification_reason = (
        None
        if station_verified
        else (value.station_verification_reason or ("rules_changed" if revoked else None))
    )
    market.status = MarketStatus.ACTIVE if station_verified else MarketStatus.BLOCKED
    market.created_at = value.created_at
    market.resolves_at = value.resolves_at
    market.fees_enabled = value.fees_enabled
    market.fee_rate = value.fee_rate
    market.fee_exponent = value.fee_exponent
    market.fee_taker_only = value.fee_taker_only
    return market, revoked
