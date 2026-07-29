from __future__ import annotations

from datetime import date

import pytest

from stratus.db.models import ResolutionContract, WeatherKind
from stratus.services.evidence_release_manifest import (
    EvidenceReleaseManifestError,
    _resolution_contract_manifest_row,
)
from stratus.services.resolution_contracts import parse_resolution_contract


def _stored_contract() -> ResolutionContract:
    rules = (
        "Resolve 17 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA using whole degrees "
        "Celsius after the first data point for the following date."
    )
    parsed = parse_resolution_contract(
        event_id="manifest-date-boundary",
        rules_raw=rules,
        resolution_source=None,
        station_code="KAAA",
        local_timezone="UTC",
        statistic=WeatherKind.HIGH,
    )
    return ResolutionContract(
        id=77,
        event_id=parsed.event_id,
        rules_hash=parsed.rules_hash,
        source_family=parsed.source_family,
        source_url=parsed.source_url,
        station_code=parsed.station_code,
        local_timezone=parsed.local_timezone,
        statistic=parsed.statistic.value,
        source_unit=parsed.source_unit,
        decimal_places=parsed.decimal_places,
        finalization_rule=parsed.finalization_rule,
        license_status=parsed.license_status,
        storage_permitted=parsed.storage_permitted,
        license_url=parsed.license_url,
        rules_raw=parsed.rules_raw,
    )


def test_manifest_keeps_validated_resolution_date_event_specific() -> None:
    row = _resolution_contract_manifest_row(
        _stored_contract(),
        market_dates=(date(2026, 7, 17), date(2026, 7, 17)),
    )

    assert row["resolution_date"] == "2026-07-17"


@pytest.mark.parametrize(
    ("market_dates", "message"),
    [
        ((), "has no database markets"),
        (
            (date(2026, 7, 17), date(2026, 7, 18)),
            "has conflicting market dates",
        ),
        ((date(2026, 7, 18),), "rules date does not match database market date"),
    ],
)
def test_manifest_fails_closed_on_missing_or_inconsistent_market_dates(
    market_dates: tuple[date, ...],
    message: str,
) -> None:
    with pytest.raises(EvidenceReleaseManifestError, match=message):
        _resolution_contract_manifest_row(
            _stored_contract(),
            market_dates=market_dates,
        )
