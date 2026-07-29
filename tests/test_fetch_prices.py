from __future__ import annotations

import pytest

import stratus.jobs.fetch_prices as fetch_prices_job
from stratus.jobs.fetch_prices import (
    PackedEventBatch,
    lock_packed_gamma_events,
    pack_event_cohorts,
)
from stratus.repositories.market_prices import (
    EventPriceCohort,
    PriceTarget,
    group_price_targets_by_event,
)


def _target(event_id: str, market_id: int) -> PriceTarget:
    return PriceTarget(
        market_id=market_id,
        event_id=event_id,
        condition_id=f"condition-{market_id}",
        yes_token_id=f"yes-{market_id}",
        no_token_id=f"no-{market_id}",
    )


def test_grouping_is_deterministic_and_keeps_complete_events() -> None:
    targets = [
        _target("event-b", 4),
        _target("event-a", 2),
        _target("event-b", 3),
        _target("event-a", 1),
    ]

    cohorts = group_price_targets_by_event(targets)

    assert tuple(cohort.event_id for cohort in cohorts) == ("event-a", "event-b")
    assert tuple(target.market_id for target in cohorts[0].targets) == (1, 2)
    assert cohorts[0].token_ids == ("yes-1", "no-1", "yes-2", "no-2")


def test_packer_never_splits_event_and_sorts_requested_tokens() -> None:
    cohorts = group_price_targets_by_event(
        [
            _target("event-c", 4),
            _target("event-a", 1),
            _target("event-b", 2),
            _target("event-b", 3),
        ]
    )

    packed = pack_event_cohorts(cohorts, batch_size=6)

    assert tuple(tuple(cohort.event_id for cohort in batch.cohorts) for batch in packed) == (
        ("event-a", "event-b"),
        ("event-c",),
    )
    assert tuple(target.market_id for target in packed[0].targets) == (1, 2, 3)
    assert packed[0].token_ids == tuple(sorted(packed[0].token_ids))
    assert set(packed[0].token_ids) == {
        "yes-1",
        "no-1",
        "yes-2",
        "no-2",
        "yes-3",
        "no-3",
    }


def test_packer_rejects_event_larger_than_exchange_batch_limit() -> None:
    cohort = EventPriceCohort(
        event_id="oversized",
        targets=tuple(_target("oversized", market_id) for market_id in range(1, 4)),
    )

    with pytest.raises(ValueError, match="requires 6 tokens"):
        pack_event_cohorts((cohort,), batch_size=4)


def test_grouping_rejects_reused_token_identity() -> None:
    first = _target("event-a", 1)
    second = PriceTarget(
        market_id=2,
        event_id="event-a",
        condition_id="condition-2",
        yes_token_id=first.yes_token_id,
        no_token_id="no-2",
    )

    with pytest.raises(ValueError, match="reuses a CLOB token"):
        group_price_targets_by_event([first, second])


@pytest.mark.asyncio
async def test_price_batch_locks_gamma_events_once_in_deterministic_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str]] = []

    async def fake_lock(session: object, event_id: str) -> None:
        calls.append((session, event_id))

    monkeypatch.setattr(fetch_prices_job, "lock_gamma_event", fake_lock)
    session = object()
    packed = PackedEventBatch(
        cohorts=(
            EventPriceCohort(event_id="event-z", targets=(_target("event-z", 3),)),
            EventPriceCohort(event_id="event-a", targets=(_target("event-a", 1),)),
            EventPriceCohort(event_id="event-z", targets=(_target("event-z", 2),)),
        )
    )

    await lock_packed_gamma_events(session, packed)  # type: ignore[arg-type]

    assert calls == [(session, "event-a"), (session, "event-z")]
