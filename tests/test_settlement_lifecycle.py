from datetime import UTC, datetime, timedelta

from stratus.db.models import SettlementCaseClassification
from stratus.services.settlement_lifecycle import classify_settlement_observation

NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)


def test_pending_case_stays_operational_before_deadline() -> None:
    classification = classify_settlement_observation(
        SettlementCaseClassification.VENUE_PENDING,
        observed_at=NOW,
        event_resolves_at=NOW - timedelta(hours=23),
    )

    assert classification is SettlementCaseClassification.VENUE_PENDING


def test_every_nonterminal_failure_escalates_after_24_hours() -> None:
    for base in (
        SettlementCaseClassification.VENUE_PENDING,
        SettlementCaseClassification.SOURCE_MISSING,
        SettlementCaseClassification.AMBIGUOUS,
    ):
        classification = classify_settlement_observation(
            base,
            observed_at=NOW,
            event_resolves_at=NOW - timedelta(hours=24),
        )
        assert classification is SettlementCaseClassification.MANUAL_REVIEW


def test_resolved_case_never_escalates() -> None:
    classification = classify_settlement_observation(
        SettlementCaseClassification.RESOLVED,
        observed_at=NOW,
        event_resolves_at=NOW - timedelta(days=30),
    )

    assert classification is SettlementCaseClassification.RESOLVED
