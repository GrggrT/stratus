from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from stratus.services.forecast_evaluation import (
    DualPricePoint,
    DualPriceTimeline,
    dual_event_market_benchmark,
)

NOW = datetime(2026, 7, 14, 8, tzinfo=UTC)


def _point(
    snapshot_id: int,
    market_id: int,
    *,
    batch_id: int = 10,
    probability: float = 0.5,
    observed_at: datetime = NOW - timedelta(minutes=2),
    eligible: bool = True,
) -> DualPricePoint:
    return DualPricePoint(
        id=snapshot_id,
        batch_id=batch_id,
        market_id=market_id,
        observed_at=observed_at,
        record_hash=f"{snapshot_id:064x}",
        binary_yes_probability=probability,
        yes_midpoint=probability,
        yes_best_bid=probability - 0.01,
        yes_best_ask=probability + 0.01,
        # Deliberately not the algebraic complement of the YES book.
        no_midpoint=1.02 - probability,
        no_best_bid=1.01 - probability,
        no_best_ask=1.03 - probability,
        eligible=eligible,
    )


def test_dual_benchmark_requires_complete_same_batch_event_vector() -> None:
    prices = DualPriceTimeline(
        [
            _point(1, 101, probability=0.20),
            _point(2, 102, probability=0.30),
            _point(3, 103, probability=0.50),
            # Newer sibling from another response must not be mixed into the
            # decision vector bound to snapshot 1.
            _point(4, 102, batch_id=11, probability=0.80),
        ]
    )

    result = dual_event_market_benchmark(
        event_market_ids=[101, 102, 103],
        target_market_id=101,
        target_snapshot_id=1,
        at=NOW,
        prices=prices,
    )

    assert result.complete
    assert result.normalized_probability == pytest.approx(0.20)
    assert result.raw_total == pytest.approx(1.0)
    assert result.own_price is not None and result.own_price.id == 1


@pytest.mark.parametrize("failure", ["missing", "ineligible", "stale", "duplicate"])
def test_dual_benchmark_fails_closed_for_ambiguous_evidence(failure: str) -> None:
    points = [_point(1, 101, probability=0.4), _point(2, 102, probability=0.6)]
    if failure == "missing":
        points.pop()
    elif failure == "ineligible":
        points[1] = _point(2, 102, probability=0.6, eligible=False)
    elif failure == "stale":
        points = [
            _point(1, 101, probability=0.4, observed_at=NOW - timedelta(hours=1)),
            _point(2, 102, probability=0.6, observed_at=NOW - timedelta(hours=1)),
        ]
    else:
        points.append(_point(3, 102, probability=0.6))

    result = dual_event_market_benchmark(
        event_market_ids=[101, 102],
        target_market_id=101,
        target_snapshot_id=1,
        at=NOW,
        prices=DualPriceTimeline(points),
    )

    assert not result.complete
    assert result.normalized_probability is None


def test_latest_dual_snapshot_skips_ineligible_rows() -> None:
    timeline = DualPriceTimeline(
        [
            _point(1, 101, observed_at=NOW - timedelta(minutes=3)),
            _point(
                2,
                101,
                batch_id=11,
                observed_at=NOW - timedelta(minutes=1),
                eligible=False,
            ),
        ]
    )

    assert timeline.latest_at(101, NOW).id == 1  # type: ignore[union-attr]
    assert timeline.latest_at(101, NOW, eligible_only=False).id == 2  # type: ignore[union-attr]
