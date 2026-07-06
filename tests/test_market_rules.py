from datetime import date
from decimal import Decimal

import pytest

from stratus.db.models import WeatherKind
from stratus.parsers.market_rules import (
    MarketRulesError,
    extract_station_candidates,
    parse_bucket_boundaries,
    parse_event_identity,
    parse_fee_schedule,
    parse_outcome_tokens,
    station_verification_reason,
)


def test_extract_station_cross_checks_rules_and_resolution_source() -> None:
    rules = (
        "Highest temperature at LaGuardia Airport Station. "
        "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA."
    )
    assert extract_station_candidates(
        rules, "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA"
    ) == {"KLGA"}


def test_extract_station_exposes_conflicting_sources() -> None:
    rules = "Source: https://www.wunderground.com/history/daily/gb/london/EGLC."
    assert extract_station_candidates(
        rules, "https://www.wunderground.com/history/daily/gb/london/EGLL"
    ) == {"EGLC", "EGLL"}


def test_station_change_requires_manual_verification() -> None:
    reason = station_verification_reason(
        frozenset({"KNYC"}),
        station_icao="KNYC",
        station_active=True,
        station_city="New York City",
        expected_city="New York City",
        previous_icao="KLGA",
    )
    assert reason == "station_changed"


def test_known_stable_station_is_verified() -> None:
    reason = station_verification_reason(
        frozenset({"KLGA"}),
        station_icao="KLGA",
        station_active=True,
        station_city="New York City",
        expected_city="New York City",
        previous_icao="KLGA",
    )
    assert reason is None


def test_parse_outcome_tokens_uses_outcome_mapping_not_array_assumption() -> None:
    result = parse_outcome_tokens(
        {
            "conditionId": "0xcondition",
            "outcomes": '["No", "Yes"]',
            "clobTokenIds": '["no-token", "yes-token"]',
        }
    )
    assert result.condition_id == "0xcondition"
    assert result.yes_token_id == "yes-token"
    assert result.no_token_id == "no-token"



def test_parse_event_identity_normalizes_nyc() -> None:
    identity = parse_event_identity(
        {
            "title": "Highest temperature in NYC on July 5?",
            "eventDate": "2026-07-05",
            "slug": "highest-temperature-in-nyc-on-july-5-2026",
        }
    )
    assert identity.city == "New York City"
    assert identity.kind is WeatherKind.HIGH
    assert identity.market_date == date(2026, 7, 5)


@pytest.mark.parametrize(
    ("title", "low", "high"),
    [
        ("24°C or below", None, Decimal("24.5000")),
        ("25°C", Decimal("24.5000"), Decimal("25.5000")),
        ("25-26°C", Decimal("24.5000"), Decimal("26.5000")),
        ("27°C or higher", Decimal("26.5000"), None),
        ("73°F or below", None, Decimal("23.0556")),
        ("between", None, None),
    ],
)
def test_parse_bucket_boundaries(title: str, low: Decimal | None, high: Decimal | None) -> None:
    if title == "between":
        with pytest.raises(MarketRulesError):
            parse_bucket_boundaries(title)
        return
    result = parse_bucket_boundaries(title)
    assert result.low_c == low
    assert result.high_c == high


def test_parse_negative_range() -> None:
    result = parse_bucket_boundaries("-2--1°C")
    assert result.low_c == Decimal("-2.5000")
    assert result.high_c == Decimal("-0.5000")


def test_parse_weather_fee_schedule() -> None:
    result = parse_fee_schedule(
        {
            "feesEnabled": True,
            "feeSchedule": {"rate": 0.05, "exponent": 1, "takerOnly": True},
        }
    )

    assert result.enabled is True
    assert result.rate == Decimal("0.05")
    assert result.exponent == 1
    assert result.taker_only is True
