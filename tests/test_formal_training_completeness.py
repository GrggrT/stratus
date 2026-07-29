from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from stratus.collectors.gamma import (
    CompleteGammaResolution,
    GammaEventSnapshot,
    ResolvedMarket,
)
from stratus.db.models import (
    Forecast,
    ForecastEvaluation,
    GammaOutcomeEvent,
    Market,
    ModelProb,
    WeatherKind,
)
from stratus.jobs.fit_settlement_calibration import _build_cases
from stratus.jobs.generate_censored_challenger import _build_censored_cases
from stratus.jobs.generate_weighted_ensemble import MODELS, _build_settlement_cases
from stratus.repositories.accuracy import forecast_evaluation_cohort_is_complete
from stratus.repositories.gamma_outcomes import (
    build_gamma_outcome_event_value,
    gamma_outcome_event_record_hash,
)

DECISION_AT = datetime(2026, 7, 12, 0, tzinfo=UTC)
RESOLVED_AT = datetime(2026, 7, 12, 12, tzinfo=UTC)
MARKET_DATE = date(2026, 7, 12)

FormalRow = tuple[ForecastEvaluation, ModelProb, Market, GammaOutcomeEvent]


def _formal_rows() -> tuple[list[FormalRow], list[Forecast]]:
    boundaries = (
        (None, Decimal("19")),
        (Decimal("19"), Decimal("20")),
        (Decimal("20"), Decimal("21")),
        (Decimal("21"), None),
    )
    probabilities = (
        Decimal("0.4"),
        Decimal("0.2"),
        Decimal("0.2"),
        Decimal("0.2"),
    )
    markets: list[Market] = []
    for index, (low, high) in enumerate(boundaries, start=1):
        markets.append(
            Market(
                id=index,
                polymarket_id=f"formal-market-{index}",
                event_id="formal-event",
                condition_id=f"condition-{index}",
                yes_token_id=f"yes-token-{index}",
                no_token_id=f"no-token-{index}",
                station_id=7,
                market_date=MARKET_DATE,
                kind=WeatherKind.HIGH,
                bucket_low_c=low,
                bucket_high_c=high,
                rules_raw=f"formal rules {index}",
                resolves_at=RESOLVED_AT,
            )
        )

    outcomes = tuple(
        ResolvedMarket(
            polymarket_id=market.polymarket_id,
            outcome_yes=index == 0,
            closed_at=RESOLVED_AT,
            yes_price="1" if index == 0 else "0",
            no_price="0" if index == 0 else "1",
            condition_id=market.condition_id,
            question_id=f"question-{index}",
            yes_token_id=str(market.yes_token_id),
            no_token_id=str(market.no_token_id),
            uma_resolution_status="resolved",
            resolved_by="unit-fixture",
        )
        for index, market in enumerate(markets)
    )
    payload = {
        "id": "formal-event",
        "closedTime": "2026-07-12T12:00:00Z",
        "updatedAt": "2026-07-12T12:00:00Z",
        "markets": [
            {
                "id": market.polymarket_id,
                "closed": True,
                "umaResolutionStatus": "resolved",
                "closedTime": "2026-07-12T12:00:00Z",
                "outcomes": json.dumps(["Yes", "No"]),
                "outcomePrices": json.dumps(
                    ["1", "0"] if index == 0 else ["0", "1"]
                ),
                "clobTokenIds": json.dumps(
                    [market.yes_token_id, market.no_token_id]
                ),
                "conditionId": market.condition_id,
                "questionID": f"question-{index}",
                "resolvedBy": "unit-fixture",
            }
            for index, market in enumerate(markets)
        ],
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    snapshot = GammaEventSnapshot(
        event_id="formal-event",
        payload=payload,
        source_url="https://gamma.test/events/formal-event",
        response_body=raw,
        response_sha256=hashlib.sha256(raw).hexdigest(),
        response_bytes=len(raw),
        fetched_at=RESOLVED_AT,
        http_date=RESOLVED_AT,
        last_modified=RESOLVED_AT,
        etag='"formal-event"',
    )
    resolution = CompleteGammaResolution(
        event_id="formal-event",
        outcomes=outcomes,
        first_outcome_at=RESOLVED_AT,
        fully_resolved_at=RESOLVED_AT,
        event_closed_at=RESOLVED_AT,
        event_updated_at=RESOLVED_AT,
    )
    value = build_gamma_outcome_event_value(snapshot, resolution, markets)
    outcome_event = GammaOutcomeEvent(
        id=91,
        **asdict(value),
        record_hash=gamma_outcome_event_record_hash(value),
    )

    rows: list[FormalRow] = []
    for index, (market, probability) in enumerate(
        zip(markets, probabilities, strict=True),
        start=1,
    ):
        outcome = index == 1
        model_prob = ModelProb(
            id=100 + index,
            market_id=market.id,
            ts=DECISION_AT,
            prob=probability,
            sigma=Decimal("0.8"),
            horizon_h=Decimal("12"),
            components={"blend": {"mean_c": 20.0, "sigma_c": 0.8}},
        )
        evaluation = ForecastEvaluation(
            id=200 + index,
            market_id=market.id,
            model_prob_id=model_prob.id,
            decision_at=DECISION_AT,
            independent_sample_key="KLGA:2026-07-12:high:6-24h",
            model_probability=probability,
            resolved_outcome=outcome,
            resolution_event_id=outcome_event.id,
            resolution_vector_hash=outcome_event.outcome_vector_hash,
            model_brier=Decimal("0"),
            evaluated_at=RESOLVED_AT,
            details={"horizon_bucket": "6-24h"},
        )
        rows.append((evaluation, model_prob, market, outcome_event))

    forecasts = [
        Forecast(
            station_id=7,
            model=model,
            run_time=DECISION_AT - timedelta(hours=2),
            valid_date=MARKET_DATE,
            kind=WeatherKind.HIGH,
            temp_c=Decimal(str(19 + index)),
            fetched_at=DECISION_AT - timedelta(hours=1),
            available_at=DECISION_AT - timedelta(hours=1),
        )
        for index, model in enumerate(MODELS)
    ]
    return rows, forecasts


def _calibration_rows(rows: list[FormalRow]) -> list[tuple[ForecastEvaluation, GammaOutcomeEvent]]:
    return [(evaluation, outcome_event) for evaluation, _, _, outcome_event in rows]


def test_formal_training_builders_accept_only_the_complete_gamma_vector() -> None:
    rows, forecasts = _formal_rows()

    assert forecast_evaluation_cohort_is_complete(_calibration_rows(rows))
    assert len(_build_censored_cases(rows)) == 1
    assert len(_build_settlement_cases(rows, forecasts)) == 1
    assert len(_build_cases(_calibration_rows(rows))) == 1

    partial = rows[:-1]
    # 0.4 + 0.2 + 0.2 == 0.8 and there is still exactly one winner. The old
    # heuristics accepted this despite the immutable ledger declaring 4 markets.
    assert sum(float(row[0].model_probability) for row in partial) == 0.8
    assert sum(row[0].resolved_outcome for row in partial) == 1
    assert not forecast_evaluation_cohort_is_complete(_calibration_rows(partial))
    assert _build_censored_cases(partial) == []
    assert _build_settlement_cases(partial, forecasts) == []
    assert _build_cases(_calibration_rows(partial)) == []


def test_formal_training_builders_reject_duplicate_bucket_rows() -> None:
    rows, forecasts = _formal_rows()
    duplicate = [*rows[:-1], rows[1]]

    assert len(duplicate) == 4
    assert not forecast_evaluation_cohort_is_complete(_calibration_rows(duplicate))
    assert _build_censored_cases(duplicate) == []
    assert _build_settlement_cases(duplicate, forecasts) == []
    assert _build_cases(_calibration_rows(duplicate)) == []
