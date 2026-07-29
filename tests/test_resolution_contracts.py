from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from stratus.db.models import WeatherKind
from stratus.services.resolution_contracts import (
    ResolutionContractError,
    canonical_resolution_source_url,
    parse_resolution_contract,
    quantize_source_value,
    resolution_rules_date,
    resolution_rules_template_sha256,
    source_quantum_c,
    to_celsius,
)


def test_wunderground_contract_is_parsed_but_storage_is_fail_closed() -> None:
    rules = (
        "This market resolves on 17 Jul '26 using the daily high at KLGA from "
        "https://www.wunderground.com/history/daily/us/ny/new-york-city/KLGA. "
        "Values use whole degrees Fahrenheit. Wait until all data for this date "
        "has been finalized."
    )
    contract = parse_resolution_contract(
        event_id="event-1",
        rules_raw=rules,
        resolution_source=None,
        station_code="KLGA",
        local_timezone="America/New_York",
        statistic=WeatherKind.HIGH,
    )
    assert contract.source_family == "wunderground"
    assert contract.source_unit == "F"
    assert contract.decimal_places == 0
    assert contract.storage_permitted is False
    assert contract.resolution_date == date(2026, 7, 17)
    assert contract.rules_template_sha256 == resolution_rules_template_sha256(rules)


def test_hko_decimal_contract_allows_attributed_storage() -> None:
    rules = (
        "Resolve on 17 Jul '26 from "
        "https://data.weather.gov.hk/weatherAPI/opendata/ using "
        "Celsius to one decimal place until data for this date has been published."
    )
    contract = parse_resolution_contract(
        event_id="event-hko",
        rules_raw=rules,
        resolution_source=None,
        station_code="HKO",
        local_timezone="Asia/Hong_Kong",
        statistic=WeatherKind.LOW,
    )
    assert contract.source_family == "hko"
    assert contract.source_unit == "C"
    assert contract.decimal_places == 1
    assert contract.storage_permitted is True


def test_contract_rejects_ambiguous_precision_or_finalization() -> None:
    with pytest.raises(ResolutionContractError):
        parse_resolution_contract(
            event_id="bad",
            rules_raw="Use 17 Jul '26 https://example.com/weather for the result.",
            resolution_source=None,
            station_code="TEST",
            local_timezone="UTC",
            statistic=WeatherKind.HIGH,
        )


def test_rules_template_normalizes_only_one_valid_market_date() -> None:
    first = (
        "Resolve 7 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA?edition=1. "
        "Use whole degrees Celsius until all data for this date has been finalized."
    )
    next_day = first.replace("7 Jul '26", "8 Jul '26")
    changed_clause = next_day.replace("edition=1", "edition=2")

    assert resolution_rules_template_sha256(first) == resolution_rules_template_sha256(
        next_day
    )
    assert resolution_rules_template_sha256(first) != resolution_rules_template_sha256(
        changed_clause
    )
    assert resolution_rules_date(first) == date(2026, 7, 7)
    assert resolution_rules_date(next_day) == date(2026, 7, 8)


@pytest.mark.parametrize(
    "rules",
    [
        "No dated rule text",
        "Resolve 7 Jul '26, superseding 8 Jul '26",
        "Resolve 31 Feb '26",
        "Resolve 7 July '26",
        "Resolve 7 jul '26",
        "Resolve 07  Jul '26",
    ],
)
def test_rules_template_rejects_missing_multiple_or_malformed_date(rules: str) -> None:
    with pytest.raises(ResolutionContractError):
        resolution_rules_template_sha256(rules)


def test_source_locator_is_https_canonical_and_keeps_path_and_query_material() -> None:
    source = canonical_resolution_source_url(
        "HTTPS://WWW.WUNDERGROUND.COM:443/history/daily/test/KAAA/date/2026-7-17"
        "?revision=2"
    )
    assert source == (
        "https://www.wunderground.com/history/daily/test/KAAA/date/2026-7-17"
        "?revision=2"
    )
    assert canonical_resolution_source_url(source.replace("revision=2", "revision=3")) != source
    encoded_identity = (
        "https://www.wunderground.com/history/daily/test/%4BAAA/date/2026-7-17"
        "?resource=%2Fdaily%2Fexact"
    )
    assert canonical_resolution_source_url(encoded_identity) == encoded_identity


@pytest.mark.parametrize(
    "source",
    [
        "http://www.wunderground.com/history/daily/test/KAAA",
        "https://user:secret@www.wunderground.com/history/daily/test/KAAA",
        "https://www.wunderground.com/history/daily/test/KAAA#observations",
        "https://www.wunderground.com:8443/history/daily/test/KAAA",
        "https://www.wunderground.com/forecast/test/KAAA",
        "https://data.weather.gov.hk/",
        "https://192.0.2.1/weather",
        "https://[2001:db8::1]/weather",
        "https://127.1/weather",
        "https://0177.0.0.1/weather",
        "https://0x7f.0x0.0x0.0x1/weather",
        "https://2130706433.1/weather",
        " https://www.wunderground.com/history/daily/test/KAAA",
        "https://www.wunderground.com/history/daily/test/KAAA ",
        "https://www.wunderground.com/history/daily/test/K AAA",
        "https://www.wunderground.com/history/daily/test/KAAA\t?revision=1",
        "https://www.wunderground.com/history/daily/test/KAAA\x00",
        "https://www.wunderground.com/history/daily/test/KAAA\u200b",
        "https://www.wunderground.com/history/daily/test/KAAA%",
        "https://www.wunderground.com/history/daily/test/KAAA%2",
        "https://www.wunderground.com/history/daily/test/KAAA%GG",
        "https://www.wunderground.com/history/daily/test/%2e/KAAA",
        "https://www.wunderground.com/history/daily/test/%2E%2E/KAAA",
        "https://www.wunderground.com/history/daily/test/.%2e/KAAA",
        "https://www.wunderground.com/history/daily/test/%2e./KAAA",
        "https://www.wunderground.com/history/daily/test/KAAA?revision=%20",
    ],
)
def test_source_locator_rejects_unsafe_or_provider_unstable_urls(source: str) -> None:
    with pytest.raises(ResolutionContractError):
        canonical_resolution_source_url(source)


def test_provider_identity_comes_from_exact_host_not_rules_brand_text() -> None:
    rules = (
        "Resolve 17 Jul '26 from https://wunderground.com.example.test/weather. "
        "Weather Underground is mentioned, values use whole degrees Celsius, "
        "until all data for this date has been finalized."
    )
    contract = parse_resolution_contract(
        event_id="event-lookalike",
        rules_raw=rules,
        resolution_source=None,
        station_code="KAAA",
        local_timezone="UTC",
        statistic=WeatherKind.HIGH,
    )
    assert contract.source_family == "national"
    assert contract.storage_permitted is False


def test_direct_resolution_source_is_not_silently_trimmed_or_replaced() -> None:
    rules = (
        "Resolve 17 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA using whole degrees "
        "Celsius until all data for this date has been finalized."
    )
    with pytest.raises(ResolutionContractError, match="surrounding whitespace"):
        parse_resolution_contract(
            event_id="event-whitespace-source",
            rules_raw=rules,
            resolution_source=(
                " https://www.wunderground.com/history/daily/test/KAAA"
            ),
            station_code="KAAA",
            local_timezone="UTC",
            statistic=WeatherKind.HIGH,
        )


def test_direct_resolution_source_must_match_the_rules_authority() -> None:
    rules = (
        "Resolve 17 Jul '26 from "
        "https://www.wunderground.com/history/daily/test/KAAA using whole degrees "
        "Celsius until all data for this date has been finalized."
    )

    with pytest.raises(ResolutionContractError, match="disagrees with"):
        parse_resolution_contract(
            event_id="event-conflicting-source",
            rules_raw=rules,
            resolution_source=(
                "https://www.wunderground.com/history/daily/test/KBBB"
            ),
            station_code="KAAA",
            local_timezone="UTC",
            statistic=WeatherKind.HIGH,
        )


def test_source_unit_conversion_and_quantization() -> None:
    assert to_celsius(Decimal("68"), "F") == Decimal("20")
    assert source_quantum_c("F", 0) == Decimal(5) / Decimal(9)
    assert quantize_source_value(Decimal("20.3"), unit="F", decimal_places=0) == Decimal("69")
