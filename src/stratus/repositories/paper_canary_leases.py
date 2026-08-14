from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import PaperCanarySafetyLease


class PaperCanaryLeaseError(RuntimeError):
    """Base error for fail-closed lease operations."""


class PaperCanaryLeaseCollisionError(PaperCanaryLeaseError):
    """A request or lease identity is already bound differently."""


class PaperCanaryLeaseAuthenticationError(PaperCanaryLeaseError):
    """The lease nonce or immutable run identity does not match."""


class PaperCanaryLeaseUnavailableError(PaperCanaryLeaseError):
    """The requested transition is unsafe for the current lease state."""


@dataclass(frozen=True, slots=True)
class PaperCanaryLeaseResult:
    lease: PaperCanarySafetyLease
    created: bool
    idempotent: bool


@dataclass(frozen=True, slots=True)
class PaperCanaryWatchdogResult:
    lease: PaperCanarySafetyLease | None
    terminalized: bool


def nonce_sha256(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()


def _translate_database_error(exc: DBAPIError) -> PaperCanaryLeaseError:
    message = str(exc.orig)
    lowered = message.lower()
    if "not found" in lowered or "run or nonce" in lowered:
        return PaperCanaryLeaseAuthenticationError(message)
    if (
        "identity" in lowered
        or "already exists" in lowered
        or "another active" in lowered
        or "collision" in lowered
    ):
        return PaperCanaryLeaseCollisionError(message)
    return PaperCanaryLeaseUnavailableError(message)


async def _lease_by_pk(
    session: AsyncSession,
    lease_pk: int,
) -> PaperCanarySafetyLease:
    lease = await session.scalar(
        select(PaperCanarySafetyLease).where(
            PaperCanarySafetyLease.id == lease_pk
        )
    )
    if lease is None:
        raise RuntimeError("database safety primitive returned a missing lease")
    return lease


async def safety_control_ready(session: AsyncSession) -> bool:
    """Return the DB-owned safety readiness bit without control-table ACLs."""
    value = await session.scalar(
        select(func.safety_paper_canary_ready())
    )
    return value is True


async def acquire_paper_canary_lease(
    session: AsyncSession,
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
    """Acquire through the exact safety-login SECURITY DEFINER primitive."""
    if not 1 <= ttl_seconds <= 30:
        raise ValueError(
            "PAPER canary lease TTL must be between 1 and 30 seconds"
        )
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM public.safety_paper_canary_acquire(
                        CAST(:lease_id AS text),
                        CAST(:run_id AS text),
                        CAST(:acquire_request_id AS text),
                        CAST(:resume_request_id AS text),
                        CAST(:direct_stop_request_id AS text),
                        CAST(:release_request_id AS text),
                        CAST(:resume_actor AS text),
                        CAST(:resume_reason AS text),
                        CAST(:nonce_sha256 AS text),
                        CAST(:ttl_seconds AS integer)
                    )
                    """
                ),
                {
                    "lease_id": lease_id,
                    "run_id": run_id,
                    "acquire_request_id": acquire_request_id,
                    "resume_request_id": resume_request_id,
                    "direct_stop_request_id": direct_stop_request_id,
                    "release_request_id": release_request_id,
                    "resume_actor": resume_actor,
                    "resume_reason": resume_reason,
                    "nonce_sha256": nonce_sha256(nonce),
                    "ttl_seconds": ttl_seconds,
                },
            )
        ).mappings().one()
    except DBAPIError as exc:
        raise _translate_database_error(exc) from exc
    lease = await _lease_by_pk(session, int(row["lease_pk"]))
    return PaperCanaryLeaseResult(
        lease=lease,
        created=bool(row["result_created"]),
        idempotent=bool(row["result_idempotent"]),
    )


async def heartbeat_paper_canary_lease(
    session: AsyncSession,
    *,
    lease_id: str,
    run_id: str,
    nonce: str,
    ttl_seconds: int,
    watchdog_fresh_seconds: float,
) -> PaperCanarySafetyLease:
    if not 1 <= ttl_seconds <= 30:
        raise ValueError(
            "PAPER canary lease TTL must be between 1 and 30 seconds"
        )
    if watchdog_fresh_seconds <= 0:
        raise ValueError("watchdog freshness must be positive")
    # Freshness authority is deliberately fixed at seven seconds inside
    # PostgreSQL; this compatibility parameter cannot relax it.
    try:
        lease_pk = await session.scalar(
            text(
                """
                SELECT public.safety_paper_canary_heartbeat(
                    CAST(:lease_id AS text),
                    CAST(:run_id AS text),
                    CAST(:nonce_sha256 AS text),
                    CAST(:ttl_seconds AS integer)
                )
                """
            ),
            {
                "lease_id": lease_id,
                "run_id": run_id,
                "nonce_sha256": nonce_sha256(nonce),
                "ttl_seconds": ttl_seconds,
            },
        )
    except DBAPIError as exc:
        raise _translate_database_error(exc) from exc
    if lease_pk is None:
        raise RuntimeError("database heartbeat returned no lease")
    return await _lease_by_pk(session, int(lease_pk))


async def resume_paper_canary_exact(
    session: AsyncSession,
    *,
    command_id: int,
) -> None:
    """Consume one exact runtime resume bound to the hidden observed lease."""
    authorized = await session.scalar(
        select(func.resume_paper_canary_exact(command_id))
    )
    if authorized is not True:
        raise PaperCanaryLeaseUnavailableError(
            "Polymarket PAPER resume lacks exact database safety authority"
        )


async def release_paper_canary_lease(
    session: AsyncSession,
    *,
    lease_id: str,
    run_id: str,
    nonce: str,
    release_request_id: str,
) -> PaperCanaryLeaseResult:
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM public.safety_paper_canary_release(
                        CAST(:lease_id AS text),
                        CAST(:run_id AS text),
                        CAST(:nonce_sha256 AS text),
                        CAST(:release_request_id AS text)
                    )
                    """
                ),
                {
                    "lease_id": lease_id,
                    "run_id": run_id,
                    "nonce_sha256": nonce_sha256(nonce),
                    "release_request_id": release_request_id,
                },
            )
        ).mappings().one()
    except DBAPIError as exc:
        raise _translate_database_error(exc) from exc
    lease = await _lease_by_pk(session, int(row["lease_pk"]))
    return PaperCanaryLeaseResult(
        lease=lease,
        created=bool(row["result_created"]),
        idempotent=bool(row["result_idempotent"]),
    )


async def stop_one_expired_paper_canary_lease(
    session: AsyncSession,
) -> PaperCanaryWatchdogResult:
    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT *
                    FROM public.safety_paper_canary_watchdog_tick()
                    """
                )
            )
        ).mappings().one()
    except DBAPIError as exc:
        raise _translate_database_error(exc) from exc
    raw_lease_pk = row["lease_pk"]
    lease = (
        None
        if raw_lease_pk is None
        else await _lease_by_pk(session, int(raw_lease_pk))
    )
    return PaperCanaryWatchdogResult(
        lease=lease,
        terminalized=bool(row["terminalized"]),
    )
