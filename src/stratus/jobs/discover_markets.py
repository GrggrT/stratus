from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.gamma import GammaClient
from stratus.config import Settings, get_settings
from stratus.db.models import Station
from stratus.db.session import session_factory_scope
from stratus.parsers.market_rules import (
    MarketRulesError,
    extract_station_candidates,
    parse_bucket_boundaries,
    parse_event_identity,
    parse_fee_schedule,
    parse_outcome_tokens,
    station_verification_reason,
)
from stratus.repositories.accuracy import upsert_resolution_contract
from stratus.repositories.markets import (
    DiscoveredMarket,
    latest_station_before,
    upsert_market,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.services.resolution_contracts import (
    ParsedResolutionContract,
    ResolutionContractError,
    parse_resolution_contract,
)

logger = logging.getLogger(__name__)


def _parse_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise MarketRulesError(f"market is missing {field}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MarketRulesError(f"market has invalid {field}: {value}") from exc


def _validate_resolution_contract_date(
    contract: ParsedResolutionContract, market_date: date
) -> None:
    if contract.resolution_date != market_date:
        raise ResolutionContractError(
            "resolution rules date does not match the market date: "
            f"rules={contract.resolution_date.isoformat()} "
            f"market={market_date.isoformat()}"
        )


async def _upsert_resolution_contract_for_market(
    session: AsyncSession,
    contract: ParsedResolutionContract,
    market_date: date,
) -> None:
    _validate_resolution_contract_date(contract, market_date)
    await upsert_resolution_contract(
        session,
        contract,
        expected_market_date=market_date,
    )


async def discover_markets(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with GammaClient(
            events_url=settings.gamma_events_url,
            weather_tag_id=settings.gamma_weather_tag_id,
        ) as client:
            events = await client.fetch_active_weather_events()

        async with session_factory.begin() as session:
            stations = (await session.execute(select(Station))).scalars().all()
            stations_by_icao = {station.icao: station for station in stations}
            written = 0
            blocked = 0
            unparseable = 0

            parsed_events = []
            for event in events:
                try:
                    identity = parse_event_identity(event)
                except MarketRulesError:
                    continue
                if identity.city in settings.target_cities:
                    parsed_events.append((identity.market_date, identity, event))

            for _, identity, event in sorted(parsed_events, key=lambda item: item[0]):
                rules_raw = str(event.get("description") or "")
                event_resolution_source = event.get("resolutionSource")
                candidates = extract_station_candidates(rules_raw, event_resolution_source)
                station = (
                    stations_by_icao.get(next(iter(candidates))) if len(candidates) == 1 else None
                )
                previous = await latest_station_before(session, identity.city, identity.market_date)
                verification_reason = station_verification_reason(
                    candidates,
                    station_icao=station.icao if station else None,
                    station_active=station.active if station else False,
                    station_city=station.city if station else None,
                    expected_city=identity.city,
                    previous_icao=previous.icao if previous else None,
                )
                verified = verification_reason is None
                contract = None
                if verified and station is not None:
                    try:
                        contract = parse_resolution_contract(
                            event_id=str(event["id"]),
                            rules_raw=rules_raw,
                            resolution_source=event_resolution_source,
                            station_code=station.icao,
                            local_timezone=station.tz,
                            statistic=identity.kind,
                        )
                    except (KeyError, ResolutionContractError) as exc:
                        contract = None
                        verified = False
                        verification_reason = "resolution_contract_invalid"
                        logger.warning(
                            "resolution contract blocked event=%s city=%s error=%s",
                            event.get("id"),
                            identity.city,
                            exc,
                        )
                if not verified:
                    blocked += len(event.get("markets") or [])
                    logger.warning(
                        "station verification blocked event=%s city=%s candidates=%s "
                        "previous=%s reason=%s",
                        event.get("id"),
                        identity.city,
                        sorted(candidates),
                        previous.icao if previous else None,
                        verification_reason,
                    )

                markets = event.get("markets")
                if not isinstance(markets, list):
                    logger.warning("event %s has no markets array; skipped", event.get("id"))
                    unparseable += 1
                    continue
                contract_persisted = False
                for raw_market in markets:
                    # One malformed market must not abort the whole discovery
                    # run: fail closed for this market only and keep going.
                    try:
                        if not isinstance(raw_market, dict):
                            raise MarketRulesError(
                                f"event {event.get('id')} contains an invalid market"
                            )
                        bucket = parse_bucket_boundaries(
                            str(raw_market.get("groupItemTitle") or ""),
                            source_increment=(
                                Decimal(1).scaleb(-contract.decimal_places)
                                if contract is not None
                                else Decimal(1)
                            ),
                        )
                        tokens = parse_outcome_tokens(raw_market)
                        fees = parse_fee_schedule(raw_market)
                        created_at = _parse_timestamp(raw_market.get("createdAt"), "createdAt")
                        resolves_at = _parse_timestamp(raw_market.get("endDate"), "endDate")
                    except MarketRulesError as exc:
                        unparseable += 1
                        logger.warning(
                            "unparseable market skipped: event=%s market=%s error=%s",
                            event.get("id"),
                            raw_market.get("id") if isinstance(raw_market, dict) else None,
                            exc,
                        )
                        continue
                    # Do not create an active contract until the event has at
                    # least one parse-valid market that is about to be written.
                    # The surrounding discovery transaction then commits the
                    # contract and market atomically; missing/empty/all-invalid
                    # market arrays cannot leave an orphan contract behind.
                    if contract is not None and not contract_persisted:
                        try:
                            await _upsert_resolution_contract_for_market(
                                session,
                                contract,
                                identity.market_date,
                            )
                        except ResolutionContractError as exc:
                            contract = None
                            verified = False
                            verification_reason = "resolution_contract_invalid"
                            logger.warning(
                                "resolution contract blocked event=%s city=%s error=%s",
                                event.get("id"),
                                identity.city,
                                exc,
                            )
                        else:
                            contract_persisted = True
                    market_resolution_source = raw_market.get("resolutionSource")
                    # A market-level source disagreement is always blocking.
                    market_candidates = extract_station_candidates(
                        str(raw_market.get("description") or rules_raw), market_resolution_source
                    )
                    market_verified = verified and market_candidates == candidates
                    market_verification_reason = verification_reason
                    if verified and not market_verified:
                        blocked += 1
                        market_verification_reason = "market_station_mismatch"
                    value = DiscoveredMarket(
                        polymarket_id=str(raw_market["id"]),
                        event_id=str(event["id"]),
                        condition_id=tokens.condition_id,
                        yes_token_id=tokens.yes_token_id,
                        no_token_id=tokens.no_token_id,
                        station_id=station.id if station and len(candidates) == 1 else None,
                        market_date=identity.market_date,
                        kind=identity.kind,
                        bucket_low_c=bucket.low_c,
                        bucket_high_c=bucket.high_c,
                        resolution_source=(
                            str(market_resolution_source)
                            if market_resolution_source
                            else str(event_resolution_source or "") or None
                        ),
                        rules_raw=str(raw_market.get("description") or rules_raw),
                        station_verified=market_verified,
                        station_verification_reason=market_verification_reason,
                        created_at=created_at,
                        resolves_at=resolves_at,
                        fees_enabled=fees.enabled,
                        fee_rate=fees.rate,
                        fee_exponent=fees.exponent,
                        fee_taker_only=fees.taker_only,
                    )
                    market, revoked = await upsert_market(session, value)
                    if revoked:
                        await session.flush()
                        rules_digest = hashlib.sha256(value.rules_raw.encode()).hexdigest()[:12]
                        await enqueue_notification(
                            session,
                            f"verify-revoked:{market.id}:{rules_digest}",
                            (
                                "Station verification REVOKED (rules changed)\n"
                                f"Market {market.id}, external {value.polymarket_id}\n"
                                f"Reason: {market.station_verification_reason or 'rules_changed'}\n"
                                f"Use /verify {market.id} after re-checking rules."
                            ),
                        )
                        logger.warning(
                            "station verification revoked: market=%s reason=%s",
                            market.id,
                            market.station_verification_reason,
                        )
                    written += 1

        logger.info(
            "market discovery complete: gamma_events=%d target_events=%d markets=%d "
            "blocked=%d unparseable=%d",
            len(events),
            len(parsed_events),
            written,
            blocked,
            unparseable,
        )
        return written, blocked


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(discover_markets(settings))


if __name__ == "__main__":
    main()
