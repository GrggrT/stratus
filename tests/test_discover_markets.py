from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import stratus.jobs.discover_markets as discovery_job
from stratus.config import Settings
from stratus.db.models import WeatherKind
from stratus.jobs.discover_markets import (
    _upsert_resolution_contract_for_market,
    _validate_resolution_contract_date,
)
from stratus.services.resolution_contracts import (
    ParsedResolutionContract,
    ResolutionContractError,
    parse_resolution_contract,
)


def _dated_contract() -> ParsedResolutionContract:
    rules = (
        "This market resolves on 17 Jul '26 using the daily high at KLGA from "
        "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA. "
        "Values use whole degrees Fahrenheit. Wait until all data for this date "
        "has been finalized."
    )
    return parse_resolution_contract(
        event_id="event-date-check",
        rules_raw=rules,
        resolution_source=None,
        station_code="KLGA",
        local_timezone="America/New_York",
        statistic=WeatherKind.HIGH,
    )


def test_discovery_accepts_contract_whose_rules_date_matches_market_date() -> None:
    _validate_resolution_contract_date(_dated_contract(), date(2026, 7, 17))


@pytest.mark.asyncio
async def test_discovery_blocks_contract_before_upsert_when_rules_date_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert = AsyncMock()
    monkeypatch.setattr(discovery_job, "upsert_resolution_contract", upsert)
    session = AsyncMock(spec=AsyncSession)

    with pytest.raises(ResolutionContractError, match="rules date does not match"):
        await _upsert_resolution_contract_for_market(
            session,
            _dated_contract(),
            date(2026, 7, 18),
        )

    upsert.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("markets", [None, [None]])
async def test_discovery_does_not_persist_contract_without_a_valid_market_write(
    monkeypatch: pytest.MonkeyPatch,
    markets: object,
) -> None:
    event: dict[str, object] = {
        "id": "event-no-valid-market",
        "description": _dated_contract().rules_raw,
        "resolutionSource": _dated_contract().source_url,
    }
    if markets is not None:
        event["markets"] = markets

    client = AsyncMock()
    client.fetch_active_weather_events.return_value = [event]
    gamma_context = MagicMock()
    gamma_context.__aenter__ = AsyncMock(return_value=client)
    gamma_context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(discovery_job, "GammaClient", MagicMock(return_value=gamma_context))

    station = SimpleNamespace(
        id=1,
        icao="KLGA",
        active=True,
        city="New York City",
        tz="America/New_York",
    )
    station_result = MagicMock()
    station_result.scalars.return_value.all.return_value = [station]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = station_result
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=session)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session_factory = MagicMock()
    session_factory.begin.return_value = transaction

    monkeypatch.setattr(
        discovery_job,
        "parse_event_identity",
        MagicMock(
            return_value=SimpleNamespace(
                city="New York City",
                market_date=date(2026, 7, 17),
                kind=WeatherKind.HIGH,
            )
        ),
    )
    monkeypatch.setattr(
        discovery_job,
        "extract_station_candidates",
        MagicMock(return_value={"KLGA"}),
    )
    monkeypatch.setattr(
        discovery_job,
        "station_verification_reason",
        MagicMock(return_value=None),
    )
    monkeypatch.setattr(
        discovery_job,
        "latest_station_before",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        discovery_job,
        "parse_resolution_contract",
        MagicMock(return_value=_dated_contract()),
    )
    contract_upsert = AsyncMock()
    market_upsert = AsyncMock()
    monkeypatch.setattr(discovery_job, "upsert_resolution_contract", contract_upsert)
    monkeypatch.setattr(discovery_job, "upsert_market", market_upsert)

    assert await discovery_job.discover_markets(
        Settings(target_cities=("New York City",)),
        session_factory,
    ) == (0, 0)
    contract_upsert.assert_not_awaited()
    market_upsert.assert_not_awaited()
