from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import unquote

from stratus.db.models import WeatherKind

_NUMBER = r"-?\d+(?:\.\d+)?"
_CITY_ALIASES = {
    "nyc": "New York City",
    "new york": "New York City",
    "new york city": "New York City",
}
_STATION_URL_PATTERN = re.compile(
    r"https?://[^\s)]+?/([A-Z][A-Z0-9]{3})(?:[/?#.)]|$)", re.IGNORECASE
)
_EXPLICIT_STATION_PATTERN = re.compile(
    r"\b(?:station|airport)\s+(?:id|code)\s*[:#-]?\s*([A-Z][A-Z0-9]{3})\b",
    re.IGNORECASE,
)
_EVENT_PATTERN = re.compile(
    r"^(highest|lowest)\s+temperature\s+in\s+(.+?)\s+on\s+", re.IGNORECASE
)


class MarketRulesError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class EventIdentity:
    city: str
    kind: WeatherKind
    market_date: date


@dataclass(frozen=True, slots=True)
class BucketBoundaries:
    low_c: Decimal | None
    high_c: Decimal | None


@dataclass(frozen=True, slots=True)
class OutcomeTokens:
    condition_id: str
    yes_token_id: str
    no_token_id: str


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    enabled: bool
    rate: Decimal
    exponent: int
    taker_only: bool


def parse_fee_schedule(market: dict[str, Any]) -> FeeSchedule:
    enabled = market.get("feesEnabled") is True
    raw = market.get("feeSchedule")
    if not enabled:
        return FeeSchedule(False, Decimal(0), 1, True)
    if not isinstance(raw, dict):
        raise MarketRulesError("fee-enabled market is missing feeSchedule")
    try:
        rate = Decimal(str(raw["rate"]))
        exponent = int(raw["exponent"])
        taker_only = bool(raw["takerOnly"])
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise MarketRulesError("market has invalid feeSchedule") from exc
    if rate < 0 or exponent < 0:
        raise MarketRulesError("market has out-of-range feeSchedule")
    return FeeSchedule(True, rate, exponent, taker_only)


def parse_outcome_tokens(market: dict[str, Any]) -> OutcomeTokens:
    condition_id = market.get("conditionId")
    if not isinstance(condition_id, str) or not condition_id:
        raise MarketRulesError("market is missing conditionId")

    outcomes = _parse_json_array(market.get("outcomes"), "outcomes")
    token_ids = _parse_json_array(market.get("clobTokenIds"), "clobTokenIds")
    if len(outcomes) != len(token_ids):
        raise MarketRulesError("outcomes and clobTokenIds are misaligned")
    mapped = {
        str(outcome).casefold(): str(token)
        for outcome, token in zip(outcomes, token_ids, strict=True)
    }
    if not mapped.get("yes") or not mapped.get("no"):
        raise MarketRulesError("market does not expose Yes and No CLOB tokens")
    return OutcomeTokens(
        condition_id=condition_id,
        yes_token_id=mapped["yes"],
        no_token_id=mapped["no"],
    )


def _parse_json_array(value: Any, field: str) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise MarketRulesError(f"market has invalid JSON in {field}") from exc
    if not isinstance(value, list):
        raise MarketRulesError(f"market is missing {field}")
    return value


def station_verification_reason(
    candidates: frozenset[str],
    *,
    station_icao: str | None,
    station_active: bool,
    station_city: str | None,
    expected_city: str,
    previous_icao: str | None,
) -> str | None:
    if not candidates:
        return "station_missing"
    if len(candidates) > 1:
        return "station_conflict"
    candidate = next(iter(candidates))
    if station_icao is None or candidate != station_icao:
        return "station_unknown"
    if not station_active:
        return "station_inactive"
    if station_city != expected_city:
        return "station_city_mismatch"
    if previous_icao is not None and previous_icao != station_icao:
        return "station_changed"
    return None


def extract_station_candidates(rules_raw: str, resolution_source: str | None) -> frozenset[str]:
    candidates: set[str] = set()
    for text in (resolution_source or "", rules_raw):
        decoded = unquote(text)
        candidates.update(match.upper() for match in _STATION_URL_PATTERN.findall(decoded))
        candidates.update(match.upper() for match in _EXPLICIT_STATION_PATTERN.findall(decoded))
    return frozenset(candidates)


def parse_event_identity(event: dict[str, Any]) -> EventIdentity:
    title = event.get("title")
    if not isinstance(title, str):
        raise MarketRulesError("event is missing a title")
    match = _EVENT_PATTERN.search(title)
    if match is None:
        raise MarketRulesError(f"not a daily temperature event: {title}")

    kind = WeatherKind.HIGH if match.group(1).lower() == "highest" else WeatherKind.LOW
    raw_city = match.group(2).strip()
    city = _CITY_ALIASES.get(raw_city.casefold(), raw_city)

    raw_date = event.get("eventDate")
    try:
        if isinstance(raw_date, str):
            market_date = date.fromisoformat(raw_date[:10])
        else:
            slug = str(event.get("slug", ""))
            date_match = re.search(
                r"-on-([a-z]+)-(\d{1,2})-(\d{4})$", slug, re.IGNORECASE
            )
            if date_match is None:
                raise ValueError
            market_date = datetime.strptime(
                " ".join(date_match.groups()), "%B %d %Y"
            ).date()
    except ValueError as exc:
        raise MarketRulesError(f"cannot parse market date for event: {title}") from exc
    return EventIdentity(city=city, kind=kind, market_date=market_date)


def parse_bucket_boundaries(title: str) -> BucketBoundaries:
    normalized = title.replace("Â°", "°").strip()

    below = re.fullmatch(
        rf"\s*({_NUMBER})\s*°?\s*([FC])\s+or\s+(?:below|lower|less)\s*",
        normalized,
        re.IGNORECASE,
    )
    if below:
        value, unit = below.groups()
        return BucketBoundaries(None, _to_celsius_boundary(Decimal(value) + Decimal("0.5"), unit))

    above = re.fullmatch(
        rf"\s*({_NUMBER})\s*°?\s*([FC])\s+or\s+(?:higher|above|more)\s*",
        normalized,
        re.IGNORECASE,
    )
    if above:
        value, unit = above.groups()
        return BucketBoundaries(_to_celsius_boundary(Decimal(value) - Decimal("0.5"), unit), None)

    interval = re.fullmatch(
        rf"\s*({_NUMBER})\s*(?:-|–|to)\s*({_NUMBER})\s*°?\s*([FC])\s*",
        normalized,
        re.IGNORECASE,
    )
    if interval:
        low, high, unit = interval.groups()
        low_value, high_value = Decimal(low), Decimal(high)
        if low_value > high_value:
            raise MarketRulesError(f"bucket lower bound exceeds upper bound: {title}")
        return BucketBoundaries(
            _to_celsius_boundary(low_value - Decimal("0.5"), unit),
            _to_celsius_boundary(high_value + Decimal("0.5"), unit),
        )

    exact = re.fullmatch(
        rf"\s*({_NUMBER})\s*°?\s*([FC])\s*", normalized, re.IGNORECASE
    )
    if exact:
        value, unit = exact.groups()
        numeric_value = Decimal(value)
        return BucketBoundaries(
            _to_celsius_boundary(numeric_value - Decimal("0.5"), unit),
            _to_celsius_boundary(numeric_value + Decimal("0.5"), unit),
        )
    raise MarketRulesError(f"unsupported temperature bucket: {title}")


def _to_celsius_boundary(value: Decimal, unit: str) -> Decimal:
    if unit.upper() == "F":
        value = (value - Decimal(32)) * Decimal(5) / Decimal(9)
    return value.quantize(Decimal("0.0001"))
