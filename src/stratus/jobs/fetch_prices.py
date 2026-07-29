from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.collectors.clob import ClobClient, ClobError
from stratus.config import Settings, get_settings
from stratus.db.session import session_factory_scope
from stratus.repositories.clob_snapshots import (
    ClobSnapshotIntegrityError,
    persist_clob_batch,
)
from stratus.repositories.gamma_outcomes import lock_gamma_event
from stratus.repositories.market_prices import (
    EventPriceCohort,
    PriceTarget,
    group_price_targets_by_event,
    mark_prices_fetched,
    markets_due_for_price_fetch,
    project_eligible_clob_prices,
)
from stratus.repositories.runtime_control import (
    get_runtime_control,
    set_kill_switch,
)
from stratus.repositories.storage_capacity import (
    acquire_capacity_write_lock,
    authoritative_capacity_check,
    latest_capacity_samples,
    sample_capacity_point,
)
from stratus.services.storage_capacity import assess_capacity_collection_gate

logger = logging.getLogger(__name__)


async def _engage_capacity_kill_switch(
    session: AsyncSession,
    reason: str,
) -> None:
    control = await get_runtime_control(session)
    if not control.kill_switch:
        await set_kill_switch(
            session,
            True,
            expiration_reason=f"storage_capacity:{reason}",
        )


@dataclass(frozen=True, slots=True)
class PackedEventBatch:
    cohorts: tuple[EventPriceCohort, ...]

    @property
    def targets(self) -> tuple[PriceTarget, ...]:
        return tuple(target for cohort in self.cohorts for target in cohort.targets)

    @property
    def token_ids(self) -> tuple[str, ...]:
        return tuple(sorted(token_id for cohort in self.cohorts for token_id in cohort.token_ids))


def pack_event_cohorts(
    cohorts: tuple[EventPriceCohort, ...],
    *,
    batch_size: int,
) -> tuple[PackedEventBatch, ...]:
    """Greedily pack whole event cohorts without ever splitting one event."""

    if batch_size < 2:
        raise ValueError("paired CLOB batch_size must be at least 2")
    packed: list[PackedEventBatch] = []
    current: list[EventPriceCohort] = []
    current_tokens = 0
    seen_events: set[str] = set()
    seen_tokens: set[str] = set()
    for cohort in sorted(cohorts, key=lambda item: item.event_id):
        if not cohort.targets:
            raise ValueError(f"event {cohort.event_id} has no price targets")
        if cohort.event_id in seen_events:
            raise ValueError(f"duplicate event cohort {cohort.event_id}")
        seen_events.add(cohort.event_id)
        event_tokens = cohort.token_ids
        if len(event_tokens) > batch_size:
            raise ValueError(
                f"event {cohort.event_id} requires {len(event_tokens)} tokens, "
                f"exceeding CLOB batch limit {batch_size}"
            )
        if len(set(event_tokens)) != len(event_tokens):
            raise ValueError(f"event {cohort.event_id} reuses a CLOB token identity")
        duplicate_tokens = seen_tokens.intersection(event_tokens)
        if duplicate_tokens:
            raise ValueError(f"CLOB tokens occur in multiple cohorts: {sorted(duplicate_tokens)}")
        seen_tokens.update(event_tokens)
        if current and current_tokens + len(event_tokens) > batch_size:
            packed.append(PackedEventBatch(cohorts=tuple(current)))
            current = []
            current_tokens = 0
        current.append(cohort)
        current_tokens += len(event_tokens)
    if current:
        packed.append(PackedEventBatch(cohorts=tuple(current)))
    return tuple(packed)


async def lock_packed_gamma_events(
    session: AsyncSession,
    packed: PackedEventBatch,
) -> None:
    """Acquire Gamma event locks before any price-batch row locks.

    Discovery and settlement use the order ``Gamma event -> market row``.
    CLOB persistence also reads/updates ``markets`` and its database triggers
    acquire the same Gamma event lock.  Taking every event lock first, in a
    deterministic order, prevents the inverse ``market row -> Gamma event``
    edge that previously deadlocked the half-hourly discovery run.
    """

    for event_id in sorted({cohort.event_id for cohort in packed.cohorts}):
        await lock_gamma_event(session, event_id)


async def fetch_prices(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> tuple[int, int]:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as session_factory:
        async with session_factory() as session:
            targets = await markets_due_for_price_fetch(session, now)
            capacity_samples = await latest_capacity_samples(session, limit=1)
        latest_capacity = capacity_samples[0] if capacity_samples else None
        capacity_gate = assess_capacity_collection_gate(
            sample_capacity_point(latest_capacity) if latest_capacity is not None else None,
            now=now,
            budget_bytes=settings.clob_ledger_capacity_budget_bytes,
            minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
            sample_ttl=timedelta(minutes=settings.clob_ledger_capacity_sample_ttl_minutes),
        )
        if not capacity_gate.allowed:
            async with session_factory.begin() as session:
                await acquire_capacity_write_lock(session)
                await _engage_capacity_kill_switch(session, capacity_gate.reason)
            logger.error(
                "price collection blocked by immutable evidence capacity gate: "
                "reason=%s sample_age_seconds=%s",
                capacity_gate.reason,
                capacity_gate.sample_age_seconds,
            )
            return 0, len(targets)
        if not targets:
            logger.info("price collection complete: due=0 written=0 skipped=0")
            return 0, 0

        cohorts = group_price_targets_by_event(targets)
        packed_batches = pack_event_cohorts(cohorts, batch_size=settings.clob_batch_size)
        written = 0
        skipped = 0
        async with ClobClient(
            books_url=settings.clob_books_url,
            batch_size=settings.clob_batch_size,
            max_response_bytes=settings.clob_response_max_bytes,
        ) as client:
            for batch_index, packed in enumerate(packed_batches):
                try:
                    collected = await client.fetch_book_batch(list(packed.token_ids))
                except ClobError as exc:
                    logger.warning(
                        "strict CLOB batch rejected without marking fetched: events=%s error=%s",
                        [cohort.event_id for cohort in packed.cohorts],
                        exc,
                    )
                    skipped += len(packed.targets)
                    continue
                projected = 0
                ineligible = 0
                capacity_block_reason: str | None = None
                capacity_measurement_completed = False
                try:
                    async with session_factory.begin() as session:
                        await acquire_capacity_write_lock(session)
                        capacity_check = await authoritative_capacity_check(
                            session,
                            now=datetime.now(UTC),
                            budget_bytes=settings.clob_ledger_capacity_budget_bytes,
                            minimum_free_bytes=settings.clob_ledger_capacity_min_free_bytes,
                            sample_ttl=timedelta(
                                minutes=settings.clob_ledger_capacity_sample_ttl_minutes
                            ),
                            storage_path_override=settings.clob_ledger_capacity_path,
                            pending_bytes=(
                                collected.response_bytes
                                + settings.clob_ledger_capacity_write_reserve_bytes
                            ),
                        )
                        capacity_measurement_completed = True
                        if not capacity_check.decision.allowed:
                            capacity_block_reason = capacity_check.decision.reason
                            await _engage_capacity_kill_switch(
                                session, capacity_check.decision.reason
                            )
                        else:
                            # Lock order is part of the transaction contract:
                            # Gamma event before CLOB/market rows.  Do this
                            # before persist_clob_batch, whose triggers take
                            # market key-share locks.
                            await lock_packed_gamma_events(session, packed)
                            persisted = await persist_clob_batch(
                                session,
                                collected,
                                packed.targets,
                            )
                            projected = await project_eligible_clob_prices(
                                session,
                                persisted.batch,
                                persisted.snapshots,
                            )
                            await mark_prices_fetched(
                                session,
                                [target.market_id for target in packed.targets],
                                persisted.batch.first_seen_at,
                            )
                            ineligible = sum(
                                not snapshot.eligible for snapshot in persisted.snapshots
                            )
                except DBAPIError as exc:
                    if not capacity_measurement_completed:
                        try:
                            async with session_factory.begin() as session:
                                await acquire_capacity_write_lock(session)
                                await _engage_capacity_kill_switch(
                                    session, "capacity_measurement_unavailable"
                                )
                        except DBAPIError:
                            logger.exception(
                                "capacity measurement failed and kill switch could not be persisted"
                            )
                    logger.warning(
                        "CLOB evidence batch rolled back without marking fetched: "
                        "events=%s error=%s",
                        [cohort.event_id for cohort in packed.cohorts],
                        exc,
                    )
                    skipped += len(packed.targets)
                except ClobSnapshotIntegrityError as exc:
                    logger.warning(
                        "CLOB evidence batch rolled back without marking fetched: "
                        "events=%s error=%s",
                        [cohort.event_id for cohort in packed.cohorts],
                        exc,
                    )
                    skipped += len(packed.targets)
                else:
                    if capacity_block_reason is not None:
                        remaining_targets = sum(
                            len(batch.targets) for batch in packed_batches[batch_index + 1 :]
                        )
                        logger.error(
                            "CLOB evidence batch rejected by atomic capacity guard: "
                            "events=%s reason=%s response_bytes=%d "
                            "remaining_targets=%d",
                            [cohort.event_id for cohort in packed.cohorts],
                            capacity_block_reason,
                            collected.response_bytes,
                            remaining_targets,
                        )
                        skipped += len(packed.targets) + remaining_targets
                        break
                    # The deferred completeness trigger has committed here, so
                    # counters cannot claim rows that the database rolled back.
                    written += projected
                    skipped += ineligible
        logger.info(
            "price collection complete: due=%d batches=%d written=%d skipped=%d",
            len(targets),
            len(packed_batches),
            written,
            skipped,
        )
        return written, skipped


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(fetch_prices(settings))


if __name__ == "__main__":
    main()
