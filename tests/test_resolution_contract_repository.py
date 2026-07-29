from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from stratus.db.models import WeatherKind
from stratus.repositories.accuracy import (
    ResolutionContractIntegrityError,
    upsert_resolution_contract,
)
from stratus.services.resolution_contracts import parse_resolution_contract


@pytest.mark.asyncio
async def test_repository_rejects_rules_date_before_touching_database() -> None:
    rules = (
        "Resolve 17 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA using whole degrees "
        "Celsius after the first data point for the following date."
    )
    contract = parse_resolution_contract(
        event_id="repository-date-boundary",
        rules_raw=rules,
        resolution_source=None,
        station_code="KAAA",
        local_timezone="UTC",
        statistic=WeatherKind.HIGH,
    )
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(
        ResolutionContractIntegrityError,
        match="rules date does not match the expected market date",
    ):
        await upsert_resolution_contract(
            session,
            contract,
            expected_market_date=date(2026, 7, 18),
        )

    session.scalar.assert_not_awaited()
    session.execute.assert_not_awaited()
