from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    Order,
    RuntimeControl,
    ValidationCheckpoint,
)
from stratus.db.session import session_factory_scope
from stratus.repositories.runtime_control import set_kill_switch
from stratus.repositories.telegram import enqueue_notification
from stratus.services.telegram_reports import calculate_live_gate

logger = logging.getLogger(__name__)
CHECKPOINT_DAYS = (30, 60)


def due_checkpoint(elapsed_days: int, completed: set[int]) -> int | None:
    return next(
        (day for day in CHECKPOINT_DAYS if elapsed_days >= day and day not in completed),
        None,
    )


async def _halt_after_repeated_gate_failure(
    session: AsyncSession,
    *,
    reason: str,
    now: datetime,
) -> None:
    # Acquire the canonical exclusive PAPER safety lock and audited STOP before
    # persisting the terminal product halt. The following UPDATE is re-entrant
    # under that transaction lock and cannot race a deferred exposure commit.
    await set_kill_switch(
        session,
        True,
        actor="stratus-audit-gate",
        reason=reason,
        expiration_reason="validation_gate",
    )
    await session.execute(
        update(RuntimeControl)
        .where(RuntimeControl.id == 1)
        .values(halted_reason=reason, updated_at=now)
    )


async def audit_gate(
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> int | None:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    async with session_factory_scope(settings, session_factory) as sessions:
        async with sessions.begin() as session:
            paper_started_at = await session.scalar(
                select(Order.created_at)
                .where(Order.mode == ExecutionMode.PAPER)
                .order_by(Order.created_at)
                .limit(1)
            )
            if paper_started_at is None:
                logger.info("gate audit skipped: paper period has not started")
                return None
            completed = set(
                (await session.execute(select(ValidationCheckpoint.checkpoint_day))).scalars()
            )
            elapsed_days = (now - paper_started_at).days
            checkpoint_day = due_checkpoint(elapsed_days, completed)
            if checkpoint_day is None:
                logger.info(
                    "gate audit not due: paper_day=%d completed=%s",
                    elapsed_days,
                    sorted(completed),
                )
                return None

            gate = await calculate_live_gate(
                session,
                settings.initial_bankroll_usd,
                min_stations=settings.live_gate_min_stations,
                station_samples=settings.live_gate_station_samples,
                station_brier=settings.live_gate_station_brier,
                independent_samples_min=settings.live_gate_independent_samples,
            )
            eligible = gate.passed
            metrics = {
                "station_scores": [
                    {
                        "icao": score.icao,
                        "count": score.count,
                        "brier": score.brier,
                    }
                    for score in gate.station_scores
                ],
                "resolved_positions": gate.resolved_positions,
                "independent_samples": gate.independent_samples,
                "clv_samples": gate.clv_samples,
                "average_clv": gate.average_clv,
                "roi": gate.roi,
                "checks": [
                    {
                        "name": check.name,
                        "passed": check.passed,
                        "detail": check.detail,
                    }
                    for check in gate.checks
                ],
            }
            session.add(
                ValidationCheckpoint(
                    checkpoint_day=checkpoint_day,
                    evaluated_at=now,
                    eligible=eligible,
                    passed=gate.passed,
                    metrics=metrics,
                )
            )
            outcome = "PASS" if gate.passed else "FAIL"
            await enqueue_notification(
                session,
                f"gate-checkpoint:{checkpoint_day}",
                f"STRATUS day-{checkpoint_day} gate: {outcome}\n"
                f"Eligible: {eligible}; positions: {gate.resolved_positions}; "
                f"ROI: {gate.roi:+.2%}",
            )

            prior_failures = await session.scalar(
                select(ValidationCheckpoint.checkpoint_day)
                .where(ValidationCheckpoint.passed.is_(False))
                .order_by(ValidationCheckpoint.checkpoint_day)
                .limit(1)
            )
            if checkpoint_day == 60 and not gate.passed and prior_failures == 30:
                reason = "validation gate failed at day 30 and day 60"
                await _halt_after_repeated_gate_failure(
                    session,
                    reason=reason,
                    now=now,
                )
                await enqueue_notification(
                    session,
                    "project-halted:validation",
                    f"CRITICAL: STRATUS halted — {reason}.",
                )
            logger.info(
                "gate checkpoint complete: day=%d eligible=%s passed=%s",
                checkpoint_day,
                eligible,
                gate.passed,
            )
            return checkpoint_day


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_gate(settings))


if __name__ == "__main__":
    main()
