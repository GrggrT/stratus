from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings
from stratus.repositories.emergency_stop import (
    EmergencyStopResult,
    execute_polymarket_emergency_stop,
)
from stratus.repositories.paper_canary_leases import (
    PaperCanaryLeaseResult,
    acquire_paper_canary_lease,
    heartbeat_paper_canary_lease,
    release_paper_canary_lease,
    safety_control_ready,
    stop_one_expired_paper_canary_lease,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PaperCanaryReadinessSnapshot:
    success_age_seconds: float | None
    consecutive_failures: int
    control_state_ready: bool | None
    database_boundary: str
    latest_tick_succeeded: bool


@dataclass(slots=True)
class PaperCanaryWatchdogStatus:
    last_success_monotonic: float | None = None
    consecutive_failures: int = 0
    control_state_ready: bool | None = None
    database_boundary: str = "failed"
    latest_tick_succeeded: bool = False

    def record_success(
        self,
        *,
        tick_started_monotonic: float,
        control_state_ready: bool,
        database_boundary: str,
    ) -> None:
        self.last_success_monotonic = tick_started_monotonic
        self.consecutive_failures = 0
        self.control_state_ready = control_state_ready
        self.database_boundary = database_boundary
        self.latest_tick_succeeded = True

    def reset(self) -> None:
        """Discard every observation before a new process lifespan starts."""
        self.last_success_monotonic = None
        self.consecutive_failures = 0
        self.control_state_ready = None
        self.database_boundary = "failed"
        self.latest_tick_succeeded = False

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        self.database_boundary = "failed"
        self.latest_tick_succeeded = False

    def success_age_seconds(self) -> float | None:
        if self.last_success_monotonic is None:
            return None
        return max(monotonic() - self.last_success_monotonic, 0.0)

    def snapshot(self) -> PaperCanaryReadinessSnapshot:
        """Return one non-blocking view for readiness and lease admission."""
        return PaperCanaryReadinessSnapshot(
            success_age_seconds=self.success_age_seconds(),
            consecutive_failures=self.consecutive_failures,
            control_state_ready=self.control_state_ready,
            database_boundary=self.database_boundary,
            latest_tick_succeeded=self.latest_tick_succeeded,
        )


@dataclass(frozen=True, slots=True)
class PaperCanaryWatchdogTickResult:
    terminalized_lease_id: str | None
    control_state_ready: bool


async def stop_polymarket_atomically(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    request_id: str,
    actor: str,
    reason: str,
    now: datetime | None = None,
) -> EmergencyStopResult:
    """Own the complete audited emergency-stop transaction boundary."""
    async with session_factory.begin() as session:
        return await execute_polymarket_emergency_stop(
            session,
            request_id=request_id,
            actor=actor,
            reason=reason,
            now=now,
        )


async def acquire_paper_canary_lease_atomically(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lease_id: str,
    run_id: str,
    acquire_request_id: str,
    resume_request_id: str,
    direct_stop_request_id: str,
    release_request_id: str,
    resume_actor: str,
    resume_reason: str,
    nonce: str,
    ttl_seconds: int,
) -> PaperCanaryLeaseResult:
    async with session_factory.begin() as session:
        return await acquire_paper_canary_lease(
            session,
            lease_id=lease_id,
            run_id=run_id,
            acquire_request_id=acquire_request_id,
            resume_request_id=resume_request_id,
            direct_stop_request_id=direct_stop_request_id,
            release_request_id=release_request_id,
            resume_actor=resume_actor,
            resume_reason=resume_reason,
            nonce=nonce,
            ttl_seconds=ttl_seconds,
        )


async def heartbeat_paper_canary_lease_atomically(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lease_id: str,
    run_id: str,
    nonce: str,
    ttl_seconds: int,
    watchdog_fresh_seconds: float,
) -> PaperCanaryLeaseResult:
    async with session_factory.begin() as session:
        lease = await heartbeat_paper_canary_lease(
            session,
            lease_id=lease_id,
            run_id=run_id,
            nonce=nonce,
            ttl_seconds=ttl_seconds,
            watchdog_fresh_seconds=watchdog_fresh_seconds,
        )
        return PaperCanaryLeaseResult(
            lease=lease,
            created=False,
            idempotent=True,
        )


async def release_paper_canary_lease_atomically(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    lease_id: str,
    run_id: str,
    nonce: str,
    release_request_id: str,
) -> PaperCanaryLeaseResult:
    async with session_factory.begin() as session:
        return await release_paper_canary_lease(
            session,
            lease_id=lease_id,
            run_id=run_id,
            nonce=nonce,
            release_request_id=release_request_id,
        )


async def run_paper_canary_watchdog_once(
    session_factory: async_sessionmaker[AsyncSession],
) -> str | None:
    result = await run_paper_canary_watchdog_tick(session_factory)
    return result.terminalized_lease_id


async def run_paper_canary_watchdog_tick(
    session_factory: async_sessionmaker[AsyncSession],
) -> PaperCanaryWatchdogTickResult:
    """Run expiry and DB-owned readiness under one ordinary watchdog checkout."""
    async with session_factory.begin() as session:
        result = await stop_one_expired_paper_canary_lease(session)
        control_state_ready = await safety_control_ready(session)
        terminalized_lease_id = (
            result.lease.lease_id
            if result.lease is not None and result.terminalized
            else None
        )
        return PaperCanaryWatchdogTickResult(
            terminalized_lease_id=terminalized_lease_id,
            control_state_ready=control_state_ready,
        )


async def refresh_paper_canary_watchdog_status_once(
    session_factory: async_sessionmaker[AsyncSession],
    status: PaperCanaryWatchdogStatus,
    *,
    database_boundary: str = "exact",
) -> str | None:
    """Refresh the cache from one watchdog DB tick and fail closed on errors."""
    tick_started_monotonic = monotonic()
    try:
        result = await run_paper_canary_watchdog_tick(session_factory)
    except Exception:
        status.record_failure()
        raise
    status.record_success(
        tick_started_monotonic=tick_started_monotonic,
        control_state_ready=result.control_state_ready,
        database_boundary=database_boundary,
    )
    return result.terminalized_lease_id


async def run_paper_canary_watchdog(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    stop_event: asyncio.Event,
    status: PaperCanaryWatchdogStatus,
    *,
    database_boundary: str = "exact",
) -> None:
    """Continuously fail closed expired leases without worker involvement."""
    while not stop_event.is_set():
        try:
            terminalized = await refresh_paper_canary_watchdog_status_once(
                session_factory,
                status,
                database_boundary=database_boundary,
            )
            if terminalized is not None:
                logger.critical(
                    "PAPER canary safety lease terminalized: lease_id=%s",
                    terminalized,
                )
        except Exception:
            # A transient database outage must not terminate the independently
            # supervised watchdog. Readiness turns red and the loop retries.
            logger.exception("PAPER canary safety watchdog iteration failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(),
                timeout=settings.safety_watchdog_poll_seconds,
            )
        except TimeoutError:
            continue
