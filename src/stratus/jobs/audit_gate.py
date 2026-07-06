from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select, update

from stratus.config import Settings, get_settings
from stratus.db.models import (
    ExecutionMode,
    Order,
    RuntimeControl,
    ValidationCheckpoint,
)
from stratus.db.session import create_engine, create_session_factory
from stratus.repositories.telegram import enqueue_notification
from stratus.services.telegram_reports import calculate_live_gate

logger = logging.getLogger(__name__)
CHECKPOINT_DAYS = (30, 60)


def due_checkpoint(elapsed_days: int, completed: set[int]) -> int | None:
    return next(
        (day for day in CHECKPOINT_DAYS if elapsed_days >= day and day not in completed),
        None,
    )


async def audit_gate(settings: Settings | None = None) -> int | None:
    settings = settings or get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    now = datetime.now(UTC)
    try:
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
                (
                    await session.execute(
                        select(ValidationCheckpoint.checkpoint_day)
                    )
                ).scalars()
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

            gate = await calculate_live_gate(session, settings.initial_bankroll_usd)
            eligible = (
                len(gate.station_scores) == 3
                and all(score.count >= 100 for score in gate.station_scores)
                and gate.resolved_positions >= 100
                and gate.clv_samples >= 100
            )
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
                "clv_samples": gate.clv_samples,
                "average_clv": gate.average_clv,
                "roi": gate.roi,
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
                await session.execute(
                    update(RuntimeControl)
                    .where(RuntimeControl.id == 1)
                    .values(kill_switch=True, halted_reason=reason, updated_at=now)
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
    finally:
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(audit_gate(settings))


if __name__ == "__main__":
    main()
