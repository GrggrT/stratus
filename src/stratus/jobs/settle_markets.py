from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.gamma import (
    ExpectedGammaMarket,
    GammaClient,
    GammaError,
    GammaEventSnapshot,
    GammaResolutionPending,
    parse_complete_event_resolution,
)
from stratus.config import Settings, get_settings
from stratus.db.models import (
    Market,
    MarketStatus,
    SettlementCaseClassification,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.gamma_outcomes import (
    GammaOutcomeIntegrityError,
    build_gamma_outcome_event_value,
    gamma_closed_at_map,
    gamma_outcome_map,
    lock_gamma_event,
    upsert_gamma_outcome_event,
)
from stratus.repositories.settlement import (
    SettlementIntegrityError,
    settle_resolved_markets,
)
from stratus.repositories.settlement_cases import (
    SettlementCaseIntegrityError,
    SettlementCaseObservation,
    upsert_settlement_case,
)
from stratus.repositories.telegram import enqueue_notification
from stratus.services.settlement_lifecycle import classify_settlement_observation

logger = logging.getLogger(__name__)
OUTCOME_BACKFILL_BATCH = 25
OUTCOME_BACKFILL_DAYS = 365


def _expected_market(market: Market) -> ExpectedGammaMarket:
    return ExpectedGammaMarket(
        market_id=market.id,
        polymarket_id=market.polymarket_id,
        condition_id=market.condition_id,
        yes_token_id=market.yes_token_id,
        no_token_id=market.no_token_id,
        rules_hash=hashlib.sha256(market.rules_raw.encode("utf-8")).hexdigest(),
    )


async def _notify_integrity_rejection(
    sessions: async_sessionmaker[AsyncSession], event_id: str, error: Exception
) -> None:
    detail = str(error)
    digest = hashlib.sha256(detail.encode("utf-8")).hexdigest()[:16]
    async with sessions.begin() as session:
        await enqueue_notification(
            session,
            f"gamma-outcome-integrity:{event_id}:{digest}",
            "BLOCKING: immutable Gamma outcome rejected\n"
            f"Event: {event_id}\n"
            f"Reason: {detail}\n"
            "No market, position, bankroll, or accuracy evidence was changed.",
        )


def _case_observation(
    *,
    event_id: str,
    event_resolves_at: datetime,
    observed_at: datetime,
    base_classification: SettlementCaseClassification,
    reason_code: str,
    source_url: str | None = None,
    source_response_sha256: str | None = None,
    resolution_event_id: int | None = None,
    resolution_vector_hash: str | None = None,
    error: Exception | None = None,
    resolved_at: datetime | None = None,
) -> SettlementCaseObservation:
    classification = classify_settlement_observation(
        base_classification,
        observed_at=observed_at,
        event_resolves_at=event_resolves_at,
    )
    return SettlementCaseObservation(
        event_id=event_id,
        classification=classification,
        reason_code=reason_code,
        event_resolves_at=event_resolves_at,
        observed_at=observed_at,
        source_url=source_url,
        source_response_sha256=source_response_sha256,
        resolution_event_id=resolution_event_id,
        resolution_vector_hash=resolution_vector_hash,
        last_error=str(error)[:4000] if error is not None else None,
        details={
            "underlying_classification": base_classification.value,
            "manual_review_escalated": (
                classification is SettlementCaseClassification.MANUAL_REVIEW
            ),
        },
        resolved_at=resolved_at,
    )


async def _persist_case_observation(
    sessions: async_sessionmaker[AsyncSession],
    observation: SettlementCaseObservation,
) -> bool:
    try:
        async with sessions.begin() as session:
            await upsert_settlement_case(session, observation)
        return True
    except Exception:
        # Lifecycle persistence must never hide or replace the primary venue
        # failure. The scheduler will record this job failure only when the
        # settlement transaction itself cannot proceed.
        logger.exception(
            "failed to persist settlement lifecycle event=%s classification=%s",
            observation.event_id,
            observation.classification.value,
        )
        return False


async def settle_markets(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions() as session:
            unresolved_event_rows = (
                await session.execute(
                    select(Market.event_id, func.max(Market.resolves_at))
                    .where(
                        Market.status != MarketStatus.RESOLVED,
                        Market.resolves_at < now,
                    )
                    .group_by(Market.event_id)
                    .order_by(Market.event_id)
                )
            ).all()
            legacy_event_rows = (
                await session.execute(
                    select(Market.event_id, func.max(Market.resolves_at))
                    .where(
                        Market.status == MarketStatus.RESOLVED,
                        Market.resolved_outcome.is_not(None),
                        Market.resolution_event_id.is_(None),
                        Market.resolves_at >= now - timedelta(days=OUTCOME_BACKFILL_DAYS),
                    )
                    .group_by(Market.event_id)
                    .order_by(func.max(Market.resolves_at).desc())
                    .limit(OUTCOME_BACKFILL_BATCH)
                )
            ).all()
        event_resolves_at = {
            event_id: resolves_at
            for event_id, resolves_at in [*unresolved_event_rows, *legacy_event_rows]
        }
        event_ids = list(event_resolves_at)
        if not event_ids:
            logger.info(
                "market settlement complete: events=0 resolved=0 positions=0 ledgered=0"
            )
            return 0, 0

        snapshots: dict[str, GammaEventSnapshot] = {}
        fetch_errors: dict[str, GammaError] = {}
        async with GammaClient(
            events_url=settings.gamma_events_url,
            weather_tag_id=settings.gamma_weather_tag_id,
        ) as client:
            for event_id in event_ids:
                try:
                    snapshots[event_id] = await client.fetch_event_snapshot(event_id)
                except GammaError as exc:
                    # A failed event must not prevent other independent events
                    # from settling. Staleness is escalated by audit_health.
                    logger.warning(
                        "Gamma outcome fetch failed event=%s error=%s", event_id, exc
                    )
                    fetch_errors[event_id] = exc

        lifecycle_persistence_failures = 0
        for event_id, error in fetch_errors.items():
            persisted = await _persist_case_observation(
                sessions,
                _case_observation(
                    event_id=event_id,
                    event_resolves_at=event_resolves_at[event_id],
                    observed_at=now,
                    base_classification=SettlementCaseClassification.SOURCE_MISSING,
                    reason_code="gamma_fetch_failed",
                    source_url=f"{settings.gamma_events_url.rstrip('/')}/{event_id}",
                    error=error,
                ),
            )
            lifecycle_persistence_failures += int(not persisted)

        resolved_count = 0
        position_count = 0
        ledgered_count = 0
        rejected_count = 0
        for event_id, snapshot in snapshots.items():
            try:
                async with sessions.begin() as session:
                    # Separate statement before reading the universe ensures a
                    # fresh READ COMMITTED snapshot after any concurrent
                    # discovery insert commits.
                    await lock_gamma_event(session, event_id)
                    markets = (
                        (
                            await session.execute(
                                select(Market)
                                .where(Market.event_id == event_id)
                                .order_by(Market.id)
                                .with_for_update()
                            )
                        )
                        .scalars()
                        .all()
                    )
                    resolution = parse_complete_event_resolution(
                        snapshot.payload,
                        expected_event_id=event_id,
                        expected_markets=[_expected_market(market) for market in markets],
                    )
                    value = build_gamma_outcome_event_value(snapshot, resolution, markets)
                    outcome_event = await upsert_gamma_outcome_event(session, value)
                    result = await settle_resolved_markets(
                        session,
                        resolutions=gamma_outcome_map(outcome_event),
                        closing_times=gamma_closed_at_map(outcome_event),
                        settled_at=now,
                        initial_bankroll_usd=Decimal(str(settings.initial_bankroll_usd)),
                        resolution_event_id=outcome_event.id,
                    )
                    await upsert_settlement_case(
                        session,
                        _case_observation(
                            event_id=event_id,
                            event_resolves_at=event_resolves_at[event_id],
                            observed_at=now,
                            base_classification=SettlementCaseClassification.RESOLVED,
                            reason_code="gamma_outcome_attested",
                            source_url=snapshot.source_url,
                            source_response_sha256=snapshot.response_sha256,
                            resolution_event_id=outcome_event.id,
                            resolution_vector_hash=outcome_event.outcome_vector_hash,
                            resolved_at=outcome_event.fully_resolved_at,
                        ),
                    )
                ledgered_count += 1
                resolved_count += result.resolved
                position_count += result.paper_positions + result.live_positions
            except GammaResolutionPending as exc:
                # Non-terminal or partial Gamma propagation is expected for a
                # while after market end. Reject the whole vector and retry.
                rejected_count += 1
                logger.info("Gamma outcome incomplete event=%s error=%s", event_id, exc)
                persisted = await _persist_case_observation(
                    sessions,
                    _case_observation(
                        event_id=event_id,
                        event_resolves_at=event_resolves_at[event_id],
                        observed_at=now,
                        base_classification=SettlementCaseClassification.VENUE_PENDING,
                        reason_code="gamma_resolution_pending",
                        source_url=snapshot.source_url,
                        source_response_sha256=snapshot.response_sha256,
                        error=exc,
                    ),
                )
                lifecycle_persistence_failures += int(not persisted)
            except (
                GammaError,
                GammaOutcomeIntegrityError,
                SettlementIntegrityError,
                SettlementCaseIntegrityError,
                DBAPIError,
            ) as exc:
                rejected_count += 1
                logger.error(
                    "Gamma outcome integrity rejection event=%s error=%s",
                    event_id,
                    exc,
                    exc_info=True,
                )
                persisted = await _persist_case_observation(
                    sessions,
                    _case_observation(
                        event_id=event_id,
                        event_resolves_at=event_resolves_at[event_id],
                        observed_at=now,
                        base_classification=SettlementCaseClassification.AMBIGUOUS,
                        reason_code=(
                            "settlement_database_error"
                            if isinstance(exc, DBAPIError)
                            else "gamma_outcome_ambiguous"
                        ),
                        source_url=snapshot.source_url,
                        source_response_sha256=snapshot.response_sha256,
                        error=exc,
                    ),
                )
                lifecycle_persistence_failures += int(not persisted)
                await _notify_integrity_rejection(sessions, event_id, exc)

        logger.info(
            "market settlement complete: events=%d resolved=%d positions=%d "
            "ledgered=%d rejected=%d fetch_failed=%d",
            len(event_ids),
            resolved_count,
            position_count,
            ledgered_count,
            rejected_count,
            len(event_ids) - len(snapshots),
        )
        if lifecycle_persistence_failures:
            raise RuntimeError(
                "settlement lifecycle persistence failed for "
                f"{lifecycle_persistence_failures} event(s)"
            )
        return resolved_count, position_count


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(settle_markets(settings))


if __name__ == "__main__":
    main()
