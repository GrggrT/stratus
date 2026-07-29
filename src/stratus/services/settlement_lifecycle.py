from __future__ import annotations

from datetime import datetime, timedelta

from stratus.db.models import SettlementCaseClassification

MANUAL_REVIEW_AFTER = timedelta(hours=24)


def classify_settlement_observation(
    base_classification: SettlementCaseClassification,
    *,
    observed_at: datetime,
    event_resolves_at: datetime,
    manual_review_after: timedelta = MANUAL_REVIEW_AFTER,
) -> SettlementCaseClassification:
    """Escalate a non-terminal settlement after its operational deadline."""

    if base_classification is SettlementCaseClassification.RESOLVED:
        return base_classification
    if observed_at >= event_resolves_at + manual_review_after:
        return SettlementCaseClassification.MANUAL_REVIEW
    return base_classification
