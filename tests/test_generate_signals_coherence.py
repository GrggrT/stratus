from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from stratus.db.models import (
    ClobMarketSnapshot,
    Market,
    MarketStatus,
    ModelProb,
    WeatherKind,
)
from stratus.jobs.generate_signals import (
    _candidate_anchor_reason,
    _event_materialization_by_id,
    _materialized_coherence_reason,
    _risk_capped_order_size,
)
from stratus.services.clob_evidence import (
    CLOB_DUAL_PROTOCOL,
    LEGACY_MARKET_PROTOCOL,
)
from stratus.services.event_probability_coherence import (
    EVENT_PROBABILITY_COHERENCE_PROTOCOL,
    EventBucketProbability,
    EventCoherenceReason,
    assess_event_probability_coherence,
    event_probability_distribution_sha256,
)

NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)
PIPELINE_COHORT = "accuracy-v4+fingerprint"


def test_risk_cap_remainder_is_rounded_down_before_signal_admission() -> None:
    station_remaining = (
        Decimal("0.12") * Decimal("837.9901") - Decimal("100.5588")
    )
    assert station_remaining == Decimal("0.000012")
    assert _risk_capped_order_size(
        25.0,
        Decimal("41.899505"),
        station_remaining,
    ) == Decimal("0.0000")
    assert _risk_capped_order_size(
        25.0,
        Decimal("41.899505"),
        Decimal("0.000199"),
    ) == Decimal("0.0001")


def _market(
    market_id: int,
    *,
    low: str | None,
    high: str | None,
    event_id: str = "coherence-event",
) -> Market:
    return Market(
        id=market_id,
        polymarket_id=f"coherence-{market_id}",
        event_id=event_id,
        condition_id=f"0xcondition-{market_id}",
        yes_token_id=f"yes-token-{market_id}",
        no_token_id=f"no-token-{market_id}",
        station_id=17,
        market_date=date(2026, 7, 16),
        kind=WeatherKind.HIGH,
        rules_raw="coherent bucket rules",
        resolves_at=NOW + timedelta(days=2),
        resolution_source="https://example.test/final-observation",
        bucket_low_c=Decimal(low) if low is not None else None,
        bucket_high_c=Decimal(high) if high is not None else None,
        status=MarketStatus.ACTIVE,
        station_verified=True,
    )


def _probability(
    market_id: int,
    probability: str,
    *,
    ts: datetime = NOW,
    anchored: bool = True,
) -> ModelProb:
    return ModelProb(
        id=100 + market_id,
        market_id=market_id,
        ts=ts,
        prob=Decimal(probability),
        sigma=Decimal("1.2"),
        horizon_h=Decimal("36"),
        components={"pipeline": {"cohort": PIPELINE_COHORT}},
        market_snapshot_id=1000 + market_id if anchored else None,
        market_anchor_protocol=(
            CLOB_DUAL_PROTOCOL if anchored else LEGACY_MARKET_PROTOCOL
        ),
    )


def _snapshot(market: Market, *, eligible: bool = True) -> ClobMarketSnapshot:
    return ClobMarketSnapshot(
        id=1000 + market.id,
        market_id=market.id,
        batch_id=700,
        condition_id=market.condition_id,
        yes_token_id=market.yes_token_id,
        no_token_id=market.no_token_id,
        complete=eligible,
        eligible=eligible,
    )


def _partition(
    *,
    event_id: str = "coherence-event",
    first_market_id: int = 1,
) -> list[Market]:
    return [
        _market(first_market_id, low=None, high="20", event_id=event_id),
        _market(first_market_id + 1, low="20", high="21", event_id=event_id),
        _market(first_market_id + 2, low="21", high=None, event_id=event_id),
    ]


def _probabilities(
    *,
    first_market_id: int = 1,
    ts: datetime = NOW,
    anchored_market_ids: set[int] | None = None,
) -> dict[int, ModelProb]:
    anchored_market_ids = anchored_market_ids or set()
    values = ("0.200000", "0.300000", "0.500000")
    return {
        market_id: _probability(
            market_id,
            value,
            ts=ts,
            anchored=market_id in anchored_market_ids,
        )
        for market_id, value in zip(
            range(first_market_id, first_market_id + 3),
            values,
            strict=True,
        )
    }


def _attach_materialization_proof(
    probabilities: dict[int, ModelProb],
    *,
    event_id: str,
    probability_ts: datetime,
    passed_trade_context: bool = False,
) -> None:
    distribution = {
        market_id: probability.prob
        for market_id, probability in probabilities.items()
    }
    distribution_hash = event_probability_distribution_sha256(distribution)
    for probability in probabilities.values():
        probability.components["event_coherence"] = {
            "protocol": EVENT_PROBABILITY_COHERENCE_PROTOCOL,
            "event_id": event_id,
            "materialized": True,
            "materialization_reason": None,
            # These v1 fields describe a stricter all-sibling CLOB context.
            # Candidate-anchor-v2 admission deliberately does not substitute
            # them for the independently attested model vector.
            "passed": passed_trade_context,
            "reason": (
                None
                if passed_trade_context
                else EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value
            ),
            "detail": None,
            "bucket_count": len(probabilities),
            "probability_count": len(probabilities),
            "raw_probability": str(probability.prob),
            "final_probability": str(probability.prob),
            "event_total_before": "1.000000",
            "event_total_final": "1.000000",
            "normalization_scale": "1",
            "applied": False,
            "probability_ts": probability_ts.isoformat(),
            "distribution_sha256": distribution_hash,
            "pipeline_cohort": PIPELINE_COHORT,
            "anchor_batch_id": 700 if passed_trade_context else None,
        }


def test_complete_materialized_vector_admits_one_eligible_candidate_anchor() -> None:
    markets = _partition()
    probabilities = _probabilities(anchored_market_ids={markets[0].id})
    _attach_materialization_proof(
        probabilities,
        event_id=markets[0].event_id,
        probability_ts=NOW,
    )

    materialization = _event_materialization_by_id(
        markets,
        latest_probs=probabilities,
    )[markets[0].event_id]

    assert materialization.passed
    assert materialization.probability_ts == NOW
    assert sum(materialization.probability_by_market.values(), Decimal("0")) == 1
    assert _materialized_coherence_reason(
        materialization,
        market_id=markets[0].id,
        stored_probability=probabilities[markets[0].id].prob,
        probability_ts=probabilities[markets[0].id].ts,
        publication_ts=materialization.probability_ts,
        components=probabilities[markets[0].id].components,
    ) is None
    candidate_snapshot = _snapshot(markets[0])
    assert (
        _candidate_anchor_reason(
            probabilities[markets[0].id],
            (candidate_snapshot, NOW),
            market=markets[0],
        )
        is None
    )

    # Sibling books are not invented or promoted into candidates. Their model
    # values prove the event vector, while their missing own anchors stay
    # explicitly non-executable.
    for sibling in markets[1:]:
        assert _candidate_anchor_reason(
            probabilities[sibling.id],
            None,
            market=sibling,
        ) == EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("missing", EventCoherenceReason.PROBABILITY_VECTOR_INCOMPLETE),
        ("time", EventCoherenceReason.PROBABILITY_TIME_MISMATCH),
        ("cohort", EventCoherenceReason.PROBABILITY_COHORT_MISMATCH),
    ],
)
def test_incomplete_or_mixed_model_vector_still_blocks_candidate_admission(
    mutation: str,
    reason: EventCoherenceReason,
) -> None:
    markets = _partition()
    probabilities = _probabilities(anchored_market_ids={markets[0].id})
    if mutation == "missing":
        probabilities.pop(markets[1].id)
    elif mutation == "time":
        probabilities[markets[1].id].ts = NOW + timedelta(seconds=1)
    else:
        probabilities[markets[1].id].components = {
            "pipeline": {"cohort": "other-cohort"}
        }

    result = _event_materialization_by_id(
        markets,
        latest_probs=probabilities,
    )[markets[0].event_id]

    assert not result.passed
    assert result.reason is reason
    assert result.probabilities == ()


def test_candidate_anchor_rejects_wrong_or_ineligible_snapshot_provenance() -> None:
    market = _partition()[0]
    probability = _probability(market.id, "0.200000")

    assert _candidate_anchor_reason(
        probability,
        None,
        market=market,
    ) == EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value

    wrong_id = _snapshot(market)
    wrong_id.id += 1
    wrong_market = _snapshot(market)
    wrong_market.market_id += 1
    wrong_condition = _snapshot(market)
    wrong_condition.condition_id = "0xother-condition"
    wrong_yes_token = _snapshot(market)
    wrong_yes_token.yes_token_id = "other-yes-token"
    wrong_no_token = _snapshot(market)
    wrong_no_token.no_token_id = "other-no-token"
    incomplete = _snapshot(market)
    incomplete.complete = False
    ineligible = _snapshot(market)
    ineligible.eligible = False

    for snapshot in (
        wrong_id,
        wrong_market,
        wrong_condition,
        wrong_yes_token,
        wrong_no_token,
        incomplete,
        ineligible,
    ):
        assert _candidate_anchor_reason(
            probability,
            (snapshot, NOW),
            market=market,
        ) == EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value

    probability.market_anchor_protocol = LEGACY_MARKET_PROTOCOL
    assert _candidate_anchor_reason(
        probability,
        (_snapshot(market), NOW),
        market=market,
    ) == EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value

    probability.market_anchor_protocol = CLOB_DUAL_PROTOCOL
    assert _candidate_anchor_reason(
        probability,
        (_snapshot(market), NOW + timedelta(microseconds=1)),
        market=market,
    ) == EventCoherenceReason.PROBABILITY_TIME_MISMATCH.value


def test_each_event_uses_its_own_materialization_clock() -> None:
    first_markets = _partition(event_id="event-a", first_market_id=1)
    second_markets = _partition(event_id="event-b", first_market_id=4)
    second_ts = NOW + timedelta(minutes=7)
    first_probabilities = _probabilities(
        first_market_id=1,
        ts=NOW,
        anchored_market_ids={1},
    )
    second_probabilities = _probabilities(
        first_market_id=4,
        ts=second_ts,
        anchored_market_ids={4},
    )
    _attach_materialization_proof(
        first_probabilities,
        event_id="event-a",
        probability_ts=NOW,
    )
    _attach_materialization_proof(
        second_probabilities,
        event_id="event-b",
        probability_ts=second_ts,
    )
    probabilities = {**first_probabilities, **second_probabilities}

    materializations = _event_materialization_by_id(
        [*first_markets, *second_markets],
        latest_probs=probabilities,
    )

    assert materializations["event-a"].passed
    assert materializations["event-b"].passed
    assert materializations["event-a"].probability_ts == NOW
    assert materializations["event-b"].probability_ts == second_ts
    for markets, event_probabilities in (
        (first_markets, first_probabilities),
        (second_markets, second_probabilities),
    ):
        candidate = markets[0]
        result = materializations[candidate.event_id]
        probability = event_probabilities[candidate.id]
        assert _materialized_coherence_reason(
            result,
            market_id=candidate.id,
            stored_probability=probability.prob,
            probability_ts=probability.ts,
            publication_ts=result.probability_ts,
            components=probability.components,
        ) is None


def test_old_anchored_sibling_is_not_needed_by_current_materialized_vector() -> None:
    markets = _partition()
    old_probabilities = _probabilities(
        ts=NOW - timedelta(minutes=1),
        anchored_market_ids={market.id for market in markets},
    )
    current_probabilities = _probabilities(
        anchored_market_ids={markets[0].id},
    )
    _attach_materialization_proof(
        current_probabilities,
        event_id=markets[0].event_id,
        probability_ts=NOW,
    )

    current = _event_materialization_by_id(
        markets,
        latest_probs=current_probabilities,
    )[markets[0].event_id]

    assert current.passed
    assert current.probability_ts == NOW
    assert all(
        probability.ts < current.probability_ts
        for probability in old_probabilities.values()
    )
    assert _candidate_anchor_reason(
        current_probabilities[markets[1].id],
        (_snapshot(markets[1]), NOW - timedelta(minutes=1)),
        market=markets[1],
    ) == EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value


def test_materialized_proof_tamper_still_fails_closed() -> None:
    markets = _partition()
    probabilities = _probabilities(anchored_market_ids={markets[0].id})
    _attach_materialization_proof(
        probabilities,
        event_id=markets[0].event_id,
        probability_ts=NOW,
    )
    materialization = _event_materialization_by_id(
        markets,
        latest_probs=probabilities,
    )[markets[0].event_id]
    probability = probabilities[markets[0].id]
    valid_proof = dict(probability.components["event_coherence"])

    for field, invalid in (
        ("materialized", False),
        ("materialization_reason", "event_probability_vector_incomplete"),
        ("event_id", "other-event"),
        ("probability_ts", (NOW + timedelta(seconds=1)).isoformat()),
        ("distribution_sha256", "0" * 64),
        ("final_probability", "0.200001"),
        ("pipeline_cohort", "other-cohort"),
        ("bucket_count", 2),
        ("probability_count", 2),
        ("event_total_final", "0.999999"),
    ):
        probability.components["event_coherence"] = {
            **valid_proof,
            field: invalid,
        }
        assert _materialized_coherence_reason(
            materialization,
            market_id=probability.market_id,
            stored_probability=probability.prob,
            probability_ts=probability.ts,
            publication_ts=materialization.probability_ts,
            components=probability.components,
        ) == EventCoherenceReason.PROBABILITY_NOT_MATERIALIZED.value

    # Old v1 trade-context diagnostics are explicitly not the vector proof.
    # Changing them cannot make an invalid candidate anchor executable, and
    # their failure cannot discard an otherwise attested model vector.
    probability.components["event_coherence"] = {
        **valid_proof,
        "passed": False,
        "reason": EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE.value,
        "anchor_batch_id": None,
    }
    assert _materialized_coherence_reason(
        materialization,
        market_id=probability.market_id,
        stored_probability=probability.prob,
        probability_ts=probability.ts,
        publication_ts=materialization.probability_ts,
        components=probability.components,
    ) is None


def test_strict_full_sibling_trade_context_remains_fail_closed() -> None:
    markets = _partition()
    probabilities = _probabilities(anchored_market_ids={markets[0].id})
    materialized = _event_materialization_by_id(
        markets,
        latest_probs=probabilities,
    )[markets[0].event_id]

    # The candidate-v2 selector uses the model materialization above, but the
    # existing full-sibling benchmark/trade-context primitive retains its
    # stricter semantics for callers that need a same-batch event market view.
    strict = assess_event_probability_coherence(
        [
            EventBucketProbability(
                market_id=market.id,
                event_id=market.event_id,
                station_id=market.station_id,
                market_date=market.market_date,
                kind=market.kind.value,
                resolves_at=market.resolves_at,
                resolution_source=market.resolution_source,
                bucket_low_c=market.bucket_low_c,
                bucket_high_c=market.bucket_high_c,
                eligible=True,
                probability=probabilities[market.id].prob,
                probability_ts=probabilities[market.id].ts,
                horizon_h=probabilities[market.id].horizon_h,
                pipeline_cohort=PIPELINE_COHORT,
                anchor_snapshot_id=probabilities[market.id].market_snapshot_id,
                anchor_market_id=(
                    market.id
                    if probabilities[market.id].market_snapshot_id is not None
                    else None
                ),
                anchor_batch_id=(
                    700
                    if probabilities[market.id].market_snapshot_id is not None
                    else None
                ),
                anchor_eligible=(
                    probabilities[market.id].market_snapshot_id is not None
                ),
            )
            for market in markets
        ]
    )

    assert materialized.passed
    assert strict.reason is EventCoherenceReason.PROBABILITY_ANCHOR_INCOMPLETE
    assert strict.probabilities == ()
