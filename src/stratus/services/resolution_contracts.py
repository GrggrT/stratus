from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import SplitResult, unquote_to_bytes, urlsplit, urlunsplit

from stratus.db.models import WeatherKind

_URL_RE = re.compile(r"https?://[^\s)]+", re.IGNORECASE)
_WHOLE_UNIT_RE = re.compile(r"whole degrees?\s+(Celsius|Fahrenheit)", re.IGNORECASE)
_DECIMAL_UNIT_RE = re.compile(
    r"(?:in\s+)?(Celsius|Fahrenheit)\s+to\s+(?:one|1)\s+decimal place",
    re.IGNORECASE,
)
_MARKET_DATE_CANDIDATE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<day>\d{1,2}) (?P<month>[A-Za-z]{3}) '(?P<year>\d{2})(?!\d)"
)
_MONTH_NUMBERS = {
    "Jan": 1,
    "Feb": 2,
    "Mar": 3,
    "Apr": 4,
    "May": 5,
    "Jun": 6,
    "Jul": 7,
    "Aug": 8,
    "Sep": 9,
    "Oct": 10,
    "Nov": 11,
    "Dec": 12,
}
_RULES_MARKET_DATE_PLACEHOLDER = "<MARKET_DATE>"
_INVALID_PERCENT_ESCAPE_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_NUMERIC_HOST_LABEL_RE = re.compile(r"(?:0[xX][0-9A-Fa-f]+|[0-9]+)")
_WUNDERGROUND_HOSTS = frozenset({"wunderground.com", "www.wunderground.com"})
_HKO_HOSTS = frozenset(
    {
        "data.gov.hk",
        "data.weather.gov.hk",
        "hko.gov.hk",
        "weather.gov.hk",
        "www.hko.gov.hk",
        "www.weather.gov.hk",
    }
)


class ResolutionContractError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ParsedResolutionContract:
    event_id: str
    rules_hash: str
    rules_template_sha256: str
    resolution_date: date
    source_family: str
    source_url: str
    station_code: str
    local_timezone: str
    statistic: WeatherKind
    source_unit: str
    decimal_places: int
    finalization_rule: str
    license_status: str
    storage_permitted: bool
    license_url: str | None
    rules_raw: str


def _resolution_rules_date_and_template_sha256(rules_raw: str) -> tuple[date, str]:
    """Extract the sole rules date and hash the otherwise exact template.

    The deliberately narrow date grammar is the form observed in the upstream
    weather-market rules (for example ``17 Jul '26``).  Nothing except that
    token is normalized: whitespace, URLs, punctuation, and every other clause
    remain material to the template identity.
    """

    matches = list(_MARKET_DATE_CANDIDATE_RE.finditer(rules_raw))
    if len(matches) != 1:
        raise ResolutionContractError(
            "resolution rules must contain exactly one market date token "
            "in <day> <Mon> '<yy> form"
        )
    match = matches[0]
    month_text = match.group("month")
    month = _MONTH_NUMBERS.get(month_text)
    if month is None:
        raise ResolutionContractError("resolution rules contain an invalid market month")
    day = int(match.group("day"))
    year = 2000 + int(match.group("year"))
    try:
        resolution_date = date(year, month, day)
    except ValueError as exc:
        raise ResolutionContractError("resolution rules contain an invalid market date") from exc
    template = (
        rules_raw[: match.start()]
        + _RULES_MARKET_DATE_PLACEHOLDER
        + rules_raw[match.end() :]
    )
    return resolution_date, hashlib.sha256(template.encode("utf-8")).hexdigest()


def resolution_rules_date(rules_raw: str) -> date:
    """Return the one exact ``D Mon 'YY`` resolution date from market rules."""

    return _resolution_rules_date_and_template_sha256(rules_raw)[0]


def resolution_rules_template_sha256(rules_raw: str) -> str:
    """Hash the complete rules text after replacing exactly its market date."""

    return _resolution_rules_date_and_template_sha256(rules_raw)[1]


def _source_family_from_host(host: str) -> str:
    if host in _WUNDERGROUND_HOSTS:
        return "wunderground"
    if host in _HKO_HOSTS:
        return "hko"
    if host in {"weather.gov", "noaa.gov"} or host.endswith(
        (".weather.gov", ".noaa.gov")
    ):
        return "noaa"
    return "national"


def _validate_provider_locator(parts: SplitResult, source_family: str) -> None:
    path = parts.path
    if source_family == "wunderground" and not path.startswith("/history/daily/"):
        raise ResolutionContractError(
            "Wunderground resolution URL must identify a daily history resource"
        )
    if source_family == "hko" and path in {"", "/"}:
        raise ResolutionContractError("HKO resolution URL must identify a source resource")
    if source_family == "noaa" and path in {"", "/"}:
        raise ResolutionContractError("NOAA resolution URL must identify a source resource")


def _validate_url_characters(value: str) -> None:
    for character in value:
        category = unicodedata.category(character)
        if character.isspace() or category in {"Cc", "Cf"}:
            raise ResolutionContractError(
                "resolution source URL must not contain whitespace or control characters"
            )
    if _INVALID_PERCENT_ESCAPE_RE.search(value):
        raise ResolutionContractError("resolution source URL has an invalid percent escape")
    for match in re.finditer(r"%([0-9A-Fa-f]{2})", value):
        octet = int(match.group(1), 16)
        if octet <= 0x20 or 0x7F <= octet <= 0x9F:
            raise ResolutionContractError(
                "resolution source URL percent-encodes whitespace or a control character"
            )


def _is_nonstandard_numeric_host(host: str) -> bool:
    labels = host.split(".")
    return bool(labels) and all(_NUMERIC_HOST_LABEL_RE.fullmatch(label) for label in labels)


def _path_has_dot_segment(path: str) -> bool:
    return any(unquote_to_bytes(segment) in {b".", b".."} for segment in path.split("/"))


def canonical_resolution_source_url(value: str) -> str:
    """Return a security-checked, minimally canonical resolution locator.

    Only transport and authority syntax are normalized.  The complete path and
    query remain material, including provider dates and revision parameters.
    """

    if value != value.strip():
        raise ResolutionContractError(
            "resolution source URL must not contain surrounding whitespace"
        )
    _validate_url_characters(value)
    candidate = value
    if "\\" in candidate:
        raise ResolutionContractError("resolution source URL contains a backslash")
    try:
        parts = urlsplit(candidate)
        port = parts.port
    except ValueError as exc:
        raise ResolutionContractError("resolution source URL is malformed") from exc
    if parts.scheme.lower() != "https":
        raise ResolutionContractError("resolution source URL must use HTTPS")
    if not parts.hostname:
        raise ResolutionContractError("resolution source URL has no hostname")
    if parts.username is not None or parts.password is not None:
        raise ResolutionContractError("resolution source URL must not contain credentials")
    if "%" in parts.netloc:
        raise ResolutionContractError("resolution source URL hostname must not be encoded")
    if parts.fragment:
        raise ResolutionContractError("resolution source URL must not contain a fragment")
    if port not in {None, 443}:
        raise ResolutionContractError("resolution source URL must not use a non-default port")
    try:
        host = parts.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ResolutionContractError("resolution source URL hostname is invalid") from exc
    if not host or "." not in host or host.startswith(".") or host.endswith("."):
        raise ResolutionContractError("resolution source URL hostname is not stable")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ResolutionContractError("resolution source URL must use a stable hostname")
    if _is_nonstandard_numeric_host(host):
        raise ResolutionContractError("resolution source URL must not use a numeric hostname")
    if parts.path and not parts.path.startswith("/"):
        raise ResolutionContractError("resolution source URL path is malformed")
    if _path_has_dot_segment(parts.path):
        raise ResolutionContractError("resolution source URL path is not canonical")

    source_family = _source_family_from_host(host)
    canonical_parts = SplitResult(
        scheme="https",
        netloc=host,
        path=parts.path or "/",
        query=parts.query,
        fragment="",
    )
    _validate_provider_locator(canonical_parts, source_family)
    return urlunsplit(canonical_parts)


def _rules_source_url(rules_raw: str) -> str | None:
    urls = [match.group(0).rstrip(".,;") for match in _URL_RE.finditer(rules_raw)]
    preferred = [
        url
        for url in urls
        if (parts := urlsplit(url)).hostname is not None
        and _source_family_from_host(parts.hostname.lower()) != "national"
    ]
    if preferred:
        return canonical_resolution_source_url(preferred[0])
    if urls:
        return canonical_resolution_source_url(urls[0])
    return None


def _source_url(rules_raw: str, resolution_source: object) -> str:
    rules_source = _rules_source_url(rules_raw)
    if isinstance(resolution_source, str):
        locator_candidate = resolution_source.lstrip().lower()
        if locator_candidate.startswith(("http://", "https://")):
            direct_source = canonical_resolution_source_url(resolution_source)
            if rules_source is not None and direct_source != rules_source:
                raise ResolutionContractError(
                    "direct resolution source disagrees with the source embedded in rules"
                )
            # Prefer the rules-derived value once equality is proven.  This
            # makes ingestion and later manifest re-parsing use one authority.
            return rules_source or direct_source
    if rules_source is not None:
        return rules_source
    raise ResolutionContractError("resolution rules contain no source URL")


def _source_family(source_url: str, rules_raw: str) -> str:
    del rules_raw
    host = urlsplit(source_url).hostname
    if host is None:  # pragma: no cover - canonicalization guarantees a host
        raise ResolutionContractError("resolution source URL has no hostname")
    return _source_family_from_host(host.lower())


def _precision(rules_raw: str) -> tuple[str, int]:
    match = _WHOLE_UNIT_RE.search(rules_raw)
    if match:
        return ("C" if match.group(1).lower() == "celsius" else "F", 0)
    match = _DECIMAL_UNIT_RE.search(rules_raw)
    if match:
        return ("C" if match.group(1).lower() == "celsius" else "F", 1)
    raise ResolutionContractError("resolution rules do not define source precision")


def _finalization_rule(rules_raw: str) -> str:
    lowered = " ".join(rules_raw.lower().split())
    if (
        "first data point for the following date" in lowered
        or "first datapoint for the following date" in lowered
    ):
        return "next_day_first_point"
    if "until all data for this date has been finalized" in lowered:
        return "source_finalized"
    if "until data for this date has been published" in lowered:
        return "source_published"
    raise ResolutionContractError("resolution rules do not define a supported finalization point")


def _license(source_family: str) -> tuple[str, bool, str | None]:
    if source_family == "hko":
        return (
            "open_data_attribution",
            True,
            "https://data.gov.hk/en/terms-and-conditions",
        )
    if source_family == "noaa":
        return ("us_government_open", True, "https://www.weather.gov/disclaimer")
    if source_family == "wunderground":
        return (
            "restricted_unlicensed",
            False,
            "https://www.wunderground.com/company/legal",
        )
    return ("unreviewed", False, None)


def parse_resolution_contract(
    *,
    event_id: str,
    rules_raw: str,
    resolution_source: object,
    station_code: str,
    local_timezone: str,
    statistic: WeatherKind,
) -> ParsedResolutionContract:
    if not event_id or not rules_raw.strip() or not station_code or not local_timezone:
        raise ResolutionContractError("resolution contract identity is incomplete")
    source_url = _source_url(rules_raw, resolution_source)
    source_family = _source_family(source_url, rules_raw)
    source_unit, decimal_places = _precision(rules_raw)
    license_status, storage_permitted, license_url = _license(source_family)
    resolution_date, rules_template_sha256 = _resolution_rules_date_and_template_sha256(
        rules_raw
    )
    return ParsedResolutionContract(
        event_id=event_id,
        rules_hash=hashlib.sha256(rules_raw.encode("utf-8")).hexdigest(),
        rules_template_sha256=rules_template_sha256,
        resolution_date=resolution_date,
        source_family=source_family,
        source_url=source_url,
        station_code=station_code,
        local_timezone=local_timezone,
        statistic=statistic,
        source_unit=source_unit,
        decimal_places=decimal_places,
        finalization_rule=_finalization_rule(rules_raw),
        license_status=license_status,
        storage_permitted=storage_permitted,
        license_url=license_url,
        rules_raw=rules_raw,
    )


def to_celsius(value: Decimal, unit: str) -> Decimal:
    if unit == "C":
        return value
    if unit == "F":
        return (value - Decimal(32)) * Decimal(5) / Decimal(9)
    raise ValueError("temperature unit must be C or F")


def source_quantum_c(unit: str, decimal_places: int) -> Decimal:
    quantum = Decimal(1).scaleb(-decimal_places)
    return quantum if unit == "C" else quantum * Decimal(5) / Decimal(9)


def quantize_source_value(value_c: Decimal, *, unit: str, decimal_places: int) -> Decimal:
    if unit == "C":
        source_value = value_c
    elif unit == "F":
        source_value = value_c * Decimal(9) / Decimal(5) + Decimal(32)
    else:
        raise ValueError("temperature unit must be C or F")
    quantum = Decimal(1).scaleb(-decimal_places)
    return source_value.quantize(quantum, rounding=ROUND_HALF_UP)
