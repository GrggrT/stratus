from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.jobs.audit_health import execution_cost_health_issues
from stratus.repositories.execution_cost_health import execution_cost_health_summary
from stratus.services.health import health_status_requires_kill_switch

pytestmark = pytest.mark.skipif(
    not os.environ.get("STRATUS_TEST_DATABASE_URL"),
    reason="STRATUS_TEST_DATABASE_URL is not set",
)

SessionFactory = async_sessionmaker[AsyncSession]


async def test_post_boundary_order_without_intent_is_independently_critical(
    db_sessions: SessionFactory,
) -> None:
    async with db_sessions.begin() as session:
        opened_at = await session.scalar(
            text("SELECT opened_at FROM execution_cost_runtime WHERE id = 1")
        )
        assert opened_at is not None
        station_id = await session.scalar(
            text(
                """
                INSERT INTO stations (icao, name, city, lat, lon, tz, active, stage)
                VALUES ('EH38', 'Execution health', 'Execution health',
                        40, -73, 'UTC', true, 'paper')
                RETURNING id
                """
            )
        )
        market_id = await session.scalar(
            text(
                """
                INSERT INTO markets (
                    polymarket_id, event_id, condition_id, yes_token_id, no_token_id,
                    station_id, market_date, kind, rules_raw, station_verified,
                    status, created_at, resolves_at
                ) VALUES (
                    'execution-health-market', 'execution-health-event',
                    '0xexecution-health', 'execution-health-yes', 'execution-health-no',
                    :station_id, :market_date, 'high', 'health audit test', true,
                    'active', :created_at, :resolves_at
                ) RETURNING id
                """
            ),
            {
                "station_id": station_id,
                "market_date": opened_at.date(),
                "created_at": opened_at - timedelta(days=1),
                "resolves_at": opened_at + timedelta(days=1),
            },
        )
        assert market_id is not None
        for index, created_at in enumerate(
            (opened_at - timedelta(seconds=1), opened_at + timedelta(seconds=1)),
            start=1,
        ):
            signal_id = await session.scalar(
                text(
                    """
                    INSERT INTO signals (
                        market_id, ts, side, model_prob, market_price, edge,
                        kelly_frac, size_usd, confidence, status,
                        token_id, benchmark_protocol
                    ) VALUES (
                        :market_id, :ts, 'buy_yes', 0.60, 0.50, 0.10,
                        0.01, 5.00, 'A', 'pending',
                        'execution-health-yes', 'legacy-synthetic-yes-v0'
                    ) RETURNING id
                    """
                ),
                {
                    "market_id": market_id,
                    "ts": opened_at + timedelta(seconds=index),
                },
            )
            await session.execute(
                text(
                    """
                    INSERT INTO orders (
                        signal_id, mode, token_id, side, price, size_usd,
                        filled_usd, status, created_at
                    ) VALUES (
                        :signal_id, 'paper', 'execution-health-yes', 'buy_yes',
                        0.50, 5.00, 0, 'paper_resting', :created_at
                    )
                    """
                ),
                {"signal_id": signal_id, "created_at": created_at},
            )

        summary = await execution_cost_health_summary(
            session,
            now=datetime.now(UTC),
        )

    assert summary.post_boundary_orders == 1
    assert summary.orders_without_intent == 1
    assert summary.terminal_without_obligation == 0
    assert summary.overdue_obligations == 0
    assert summary.overdue_marks == 0
    assert summary.invalid_eligible_source_legs == 0
    assert summary.reconciliation_mismatches == 0
    assert summary.duplicate_identities == 0
    issues = execution_cost_health_issues(summary)
    assert issues == [
        {
            "code": "execution_intent_missing",
            "detail": (
                "1 post-boundary executable order(s) have no immutable intent; "
                f"boundary={opened_at.isoformat()}, orders=1"
            ),
            "severity": "critical",
        }
    ]
    assert health_status_requires_kill_switch(issues[0]["severity"]) is False
