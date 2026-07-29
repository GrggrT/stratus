from __future__ import annotations

import os
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stratus.api import create_app
from stratus.config import Settings
from stratus.db.models import TelegramDelivery
from stratus.repositories.telegram import (
    acknowledge_deliveries_through,
    acknowledge_delivery,
    activate_notification_channel,
    claim_deliveries,
    enqueue_notification,
    mark_delivery_failed,
    mark_delivery_sent,
    notification_delivery_summary,
    record_notification_heartbeat,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not os.environ.get("STRATUS_TEST_DATABASE_URL"),
        reason="STRATUS_TEST_DATABASE_URL is not set",
    ),
]


async def _enqueue(
    sessions: async_sessionmaker[AsyncSession],
    event_key: str,
) -> int:
    async with sessions.begin() as session:
        assert await enqueue_notification(session, event_key, event_key)
        delivery_id = await session.scalar(
            select(TelegramDelivery.id).where(TelegramDelivery.event_key == event_key)
        )
        assert delivery_id is not None
        return delivery_id


async def _activate(sessions: async_sessionmaker[AsyncSession]) -> None:
    async with sessions.begin() as session:
        runtime = await activate_notification_channel(session)
        assert runtime.channel == "telegram"


async def test_local_inbox_works_without_activation_and_never_claims_legacy(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    delivery_id = await _enqueue(db_sessions, "legacy-alert")
    async with db_sessions.begin() as session:
        assert await claim_deliveries(session) == []
        summary = await notification_delivery_summary(session)
        assert summary.state == "not_activated"
        assert summary.total_count == 1
        assert summary.unacknowledged_count == 1
        assert summary.pending_after_activation == 0
        assert summary.dead_letter_count == 0
        assert await acknowledge_delivery(session, delivery_id)
        assert await acknowledge_delivery(session, delivery_id)

    async with db_sessions() as session:
        row = await session.get(TelegramDelivery, delivery_id)
        assert row is not None
        assert row.acknowledged_at is not None
        assert row.sent_at is None
        assert row.attempts == 0


async def test_activation_watermark_excludes_every_legacy_row_and_is_idempotent(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    legacy_id = await _enqueue(db_sessions, "legacy-before-watermark")
    await _activate(db_sessions)
    async with db_sessions.begin() as session:
        first = await activate_notification_channel(session)
        first_watermark = first.activated_at
    post_activation_id = await _enqueue(db_sessions, "new-after-watermark")

    async with db_sessions.begin() as session:
        second = await activate_notification_channel(session)
        assert second.activated_at == first_watermark
        claimed = await claim_deliveries(session)
        assert [item.id for item in claimed] == [post_activation_id]
        assert claimed[0].attempts == 1

    async with db_sessions() as session:
        legacy = await session.get(TelegramDelivery, legacy_id)
        assert legacy is not None
        assert legacy.attempts == 0
        assert legacy.lease_until is None


async def test_concurrent_claimers_use_skip_locked_and_never_share_a_row(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    first_id = await _enqueue(db_sessions, "concurrent-1")
    second_id = await _enqueue(db_sessions, "concurrent-2")

    first_session = db_sessions()
    second_session = db_sessions()
    try:
        await first_session.begin()
        first_claim = await claim_deliveries(first_session, limit=1)
        assert [item.id for item in first_claim] == [first_id]

        await second_session.begin()
        second_claim = await claim_deliveries(second_session, limit=1)
        assert [item.id for item in second_claim] == [second_id]
        assert {first_claim[0].id}.isdisjoint({second_claim[0].id})
        await second_session.commit()
        await first_session.commit()
    finally:
        await second_session.close()
        await first_session.close()


async def test_expired_lease_is_reclaimable_and_advances_attempt(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    delivery_id = await _enqueue(db_sessions, "abandoned-lease")
    async with db_sessions.begin() as session:
        first = (await claim_deliveries(session, lease_seconds=60))[0]
        assert first.id == delivery_id
        assert first.attempts == 1
    async with db_sessions.begin() as session:
        await session.execute(
            text(
                "UPDATE telegram_deliveries SET lease_until = "
                "clock_timestamp() - INTERVAL '1 second' WHERE id = :id"
            ),
            {"id": delivery_id},
        )
        second = (await claim_deliveries(session, lease_seconds=60))[0]
        assert second.id == delivery_id
        assert second.attempts == 2


async def test_send_and_failure_transitions_require_the_exact_claim_lease(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    delivery_id = await _enqueue(db_sessions, "lease-owner-only")
    async with db_sessions.begin() as session:
        claim = (await claim_deliveries(session, limit=1))[0]
        stale_lease = claim.lease_until + timedelta(seconds=1)
        assert not await mark_delivery_sent(session, delivery_id, stale_lease)
        assert not await mark_delivery_failed(
            session,
            delivery_id,
            "stale consumer",
            stale_lease,
        )

    async with db_sessions() as session:
        row = await session.get(TelegramDelivery, delivery_id)
        assert row is not None
        assert row.sent_at is None
        assert row.error is None
        assert row.dead_lettered_at is None
        assert row.lease_until == claim.lease_until


async def test_retry_backoff_and_fifth_failure_dead_letter_are_durable(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    delivery_id = await _enqueue(db_sessions, "retry-sequence")
    expected_delays = (10.0, 30.0, 120.0, 600.0)

    for attempt, expected_delay in enumerate(expected_delays, start=1):
        async with db_sessions.begin() as session:
            claim = (await claim_deliveries(session))[0]
            assert claim.attempts == attempt
            assert await mark_delivery_failed(
                session,
                delivery_id,
                f"failure-{attempt}",
                claim.lease_until,
            )
        async with db_sessions.begin() as session:
            row = await session.get(TelegramDelivery, delivery_id)
            assert row is not None
            assert row.lease_until is None
            assert row.dead_lettered_at is None
            assert row.next_attempt_at is not None
            database_now = await session.scalar(select(text("clock_timestamp()")))
            assert database_now is not None
            remaining = (row.next_attempt_at - database_now).total_seconds()
            assert expected_delay - 1.0 <= remaining <= expected_delay
            await session.execute(
                text(
                    "UPDATE telegram_deliveries SET next_attempt_at = "
                    "clock_timestamp() - INTERVAL '1 second' WHERE id = :id"
                ),
                {"id": delivery_id},
            )

    async with db_sessions.begin() as session:
        fifth = (await claim_deliveries(session))[0]
        assert fifth.attempts == 5
        assert await mark_delivery_failed(
            session,
            delivery_id,
            "final failure",
            fifth.lease_until,
        )
    async with db_sessions.begin() as session:
        row = await session.get(TelegramDelivery, delivery_id)
        assert row is not None
        assert row.dead_lettered_at is not None
        assert row.next_attempt_at is None
        assert row.lease_until is None
        assert await claim_deliveries(session) == []
        summary = await notification_delivery_summary(session)
        assert summary.dead_letter_count == 1
        assert summary.pending_after_activation == 0
        assert summary.state == "degraded"


async def test_send_and_local_acknowledgement_are_independent(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    sent_id = await _enqueue(db_sessions, "sent-not-acked")
    local_id = await _enqueue(db_sessions, "acked-not-sent")
    async with db_sessions.begin() as session:
        claims = await claim_deliveries(session, limit=2)
        assert [claim.id for claim in claims] == [sent_id, local_id]
        assert await mark_delivery_sent(
            session,
            sent_id,
            claims[0].lease_until,
        )
        assert await acknowledge_delivery(session, local_id)

    async with db_sessions() as session:
        sent = await session.get(TelegramDelivery, sent_id)
        local = await session.get(TelegramDelivery, local_id)
        assert sent is not None and local is not None
        assert sent.sent_at is not None
        assert sent.acknowledged_at is None
        assert local.acknowledged_at is not None
        assert local.sent_at is None


async def test_ack_through_is_bounded_and_enqueue_is_idempotent(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    first = await _enqueue(db_sessions, "ack-through-1")
    second = await _enqueue(db_sessions, "ack-through-2")
    third = await _enqueue(db_sessions, "ack-through-3")
    async with db_sessions.begin() as session:
        assert not await enqueue_notification(session, "ack-through-1", "duplicate")
        assert await acknowledge_deliveries_through(session, second) == 2
    async with db_sessions() as session:
        rows = list(
            (
                await session.scalars(
                    select(TelegramDelivery).order_by(TelegramDelivery.id)
                )
            ).all()
        )
        assert [row.id for row in rows] == [first, second, third]
        assert rows[0].acknowledged_at is not None
        assert rows[1].acknowledged_at is not None
        assert rows[2].acknowledged_at is None


async def test_channel_summary_tracks_heartbeat_and_post_activation_backlog(
    db_sessions: async_sessionmaker[AsyncSession],
) -> None:
    await _activate(db_sessions)
    async with db_sessions.begin() as session:
        summary = await notification_delivery_summary(session)
        assert summary.state == "stale"
        assert await record_notification_heartbeat(session)
    async with db_sessions.begin() as session:
        summary = await notification_delivery_summary(session)
        assert summary.state == "healthy"

    await _enqueue(db_sessions, "visible-backlog")
    async with db_sessions.begin() as session:
        summary = await notification_delivery_summary(session)
        assert summary.state == "healthy"
        assert summary.pending_after_activation == 1
        assert summary.backlog_overdue is False
        assert summary.oldest_pending_after_activation_at is not None
        assert summary.heartbeat_at is not None
        assert summary.activated_at is not None
        assert summary.heartbeat_at - summary.activated_at < timedelta(seconds=5)
        await session.execute(
            text(
                "UPDATE notification_delivery_runtime SET activated_at = "
                "clock_timestamp() - INTERVAL '4 minutes' WHERE channel = 'telegram'"
            )
        )
        await session.execute(
            text(
                "UPDATE telegram_deliveries SET created_at = "
                "clock_timestamp() - INTERVAL '3 minutes' "
                "WHERE event_key = 'visible-backlog'"
            )
        )
    async with db_sessions.begin() as session:
        summary = await notification_delivery_summary(session)
        assert summary.state == "degraded"
        assert summary.backlog_overdue is True


async def test_operator_alert_inbox_and_bounded_acknowledgement(
    db_sessions: async_sessionmaker[AsyncSession],
    migrated_database_url: str,
) -> None:
    first_id = await _enqueue(db_sessions, "health:api-first")
    second_id = await _enqueue(db_sessions, "risk:api-second")
    settings = Settings(
        database_url=migrated_database_url,
        operator_api_key="notification-api-test-key",
    )
    app = create_app(settings)
    app.state.sessions = db_sessions
    headers = {"X-API-Key": "notification-api-test-key"}

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        assert (await client.get("/api/v1/alerts")).status_code == 401
        response = await client.get("/api/v1/alerts", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["channel_state"] == "not_activated"
        assert payload["total_count"] == 2
        assert payload["unacknowledged_count"] == 2
        assert payload["heartbeat_stale"] is False
        assert payload["backlog_overdue"] is False
        assert [item["id"] for item in payload["items"]] == [second_id, first_id]
        assert {item["external_state"] for item in payload["items"]} == {
            "local_only"
        }

        unauthorized = await client.post(f"/api/v1/alerts/{first_id}/ack")
        assert unauthorized.status_code == 401
        acknowledged = await client.post(
            f"/api/v1/alerts/{first_id}/ack",
            headers=headers,
        )
        assert acknowledged.status_code == 200
        assert acknowledged.json()["acknowledged"] == 1

        bounded = await client.post(
            "/api/v1/alerts/ack-through",
            headers=headers,
            json={"through_id": second_id},
        )
        assert bounded.status_code == 200
        assert bounded.json() == {
            "acknowledged": 1,
            "through_id": second_id,
        }

        empty = await client.get("/api/v1/alerts", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["unacknowledged_count"] == 0
        assert empty.json()["items"] == []
