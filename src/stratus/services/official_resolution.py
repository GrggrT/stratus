from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal, Protocol, cast
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OFFICIAL_RESOLUTION_LICENSE_PROTOCOL = "official-resolution-license-v1"
OFFICIAL_RESOLUTION_PROTOCOL = "official-resolution-source-v1"
OFFICIAL_RESOLUTION_CONTRACT_POLICY_PROTOCOL = (
    "official-resolution-contract-policy-v1"
)
LICENSED_JSON_ADAPTER_NAME = "licensed-json-field-map"
LICENSED_JSON_ADAPTER_VERSION = "1"
WUNDERGROUND_ADAPTER_NAME = "wunderground-licensed-json-field-map"
WUNDERGROUND_ADAPTER_VERSION = "1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_INTEGER_VALUE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_TENTH_VALUE_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)\.[0-9]$")
_JSON_FRACTION_RE = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)(?:[eE][+-]?[0-9]+)?$"
)
_MEDIA_TYPE_JSON = frozenset(
    {"application/json", "application/problem+json", "application/vnd.api+json"}
)
_MAX_PATH_DEPTH = 8
_SECRET_QUERY_NAMES = frozenset(
    {
        "accesstoken",
        "apikey",
        "authorization",
        "credential",
        "key",
        "sig",
        "signature",
        "token",
        "xapikey",
    }
)


@dataclass(frozen=True, slots=True)
class _ExactJsonFraction:
    token: str


@dataclass(frozen=True, slots=True)
class _ExactJsonInteger:
    token: str


class OfficialResolutionError(ValueError):
    """Base error for rejected official-resolution evidence."""


class LicensePolicyError(OfficialResolutionError):
    """The source licence is absent, expired, forged, or insufficient."""


class ContractBindingError(OfficialResolutionError):
    """The licence, endpoint, artifact, and market contract do not bind exactly."""


class ResponseEnvelopeError(OfficialResolutionError):
    """The captured HTTP response is not acceptable source evidence."""


class ExtractionError(OfficialResolutionError):
    """The licensed JSON artifact cannot be deterministically extracted."""


class OfficialResolutionUnavailable(OfficialResolutionError):
    """No reviewed licensed adapter configuration is available."""


JsonSeriesMode = Literal["scalar", "parallel_arrays"]
Statistic = Literal["high", "low"]
AcquisitionProtocol = Literal["licensed-http-v1", "authorized-artifact-v1"]


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise OfficialResolutionError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _require_naive_local_time(value: time, *, field: str) -> None:
    if value.tzinfo is not None:
        raise OfficialResolutionError(f"{field} must be a timezone-naive local wall time")


def _require_sha256(value: str, *, field: str) -> None:
    if _SHA256_RE.fullmatch(value) is None:
        raise OfficialResolutionError(f"{field} must be a lowercase SHA-256 digest")


def _canonical_url(value: str, *, field: str) -> str:
    try:
        parts = urlsplit(value)
        hostname = parts.hostname
        port = parts.port
        query_items = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        raise OfficialResolutionError(f"{field} is not a valid redacted HTTPS URL") from None
    if (
        parts.scheme.lower() != "https"
        or not hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise OfficialResolutionError(f"{field} must be an absolute credential-free HTTPS URL")
    if ";" in parts.query or re.search(r"%(?![0-9A-Fa-f]{2})", parts.query):
        raise OfficialResolutionError(f"{field} has a non-canonical redacted query")
    for name, _value in query_items:
        normalized_name = re.sub(r"[-_]", "", name).lower()
        if normalized_name in _SECRET_QUERY_NAMES or any(
            marker in normalized_name
            for marker in (
                "accesstoken",
                "apikey",
                "authorization",
                "credential",
                "signature",
            )
        ):
            raise OfficialResolutionError(
                f"{field} must be a redacted URL without secret query parameters"
            )
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise OfficialResolutionError(f"{field} has an invalid HTTPS hostname") from None
    if ":" in host:
        host = f"[{host}]"
    if port not in (None, 443):
        host = f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit(("https", host, path, parts.query, ""))


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ExtractionError(f"JSON artifact contains duplicate key {key!r}")
        result[key] = value
    return result


def _parse_json_fraction(value: str) -> _ExactJsonFraction:
    if len(value) > 128 or _JSON_FRACTION_RE.fullmatch(value) is None:
        raise ExtractionError("JSON artifact contains an invalid fractional number")
    return _ExactJsonFraction(value)


def _parse_json_integer(value: str) -> _ExactJsonInteger:
    if len(value) > 128 or _INTEGER_VALUE_RE.fullmatch(value) is None:
        raise ExtractionError("JSON artifact contains an invalid integer")
    return _ExactJsonInteger(value)


def _reject_json_constant(_value: str) -> object:
    raise ExtractionError("JSON artifact contains a non-finite number")


def _validate_json_tree(
    value: object,
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    nodes = 0

    def visit(item: object, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise ExtractionError("JSON artifact exceeds the configured node limit")
        if depth > max_depth:
            raise ExtractionError("JSON artifact exceeds the configured depth limit")
        if item is None or isinstance(
            item,
            str | int | bool | _ExactJsonFraction | _ExactJsonInteger,
        ):
            return
        if isinstance(item, float | Decimal):
            raise ExtractionError("JSON fractional values must be represented as exact strings")
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ExtractionError("JSON object keys must be strings")
                visit(child, depth + 1)
            return
        raise ExtractionError(f"unsupported JSON value type {type(item)!r}")

    visit(value, 0)


def canonical_content_bytes(value: object) -> bytes:
    """Return stable JSON bytes for an already parsed, exact JSON-compatible value."""

    _validate_json_tree(value, max_depth=32, max_nodes=100_000)

    def encode(item: object) -> bytes:
        if item is None:
            return b"null"
        if item is True:
            return b"true"
        if item is False:
            return b"false"
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=True).encode("ascii")
        if isinstance(item, int):
            return str(item).encode("ascii")
        if isinstance(item, _ExactJsonFraction):
            return item.token.encode("ascii")
        if isinstance(item, _ExactJsonInteger):
            return item.token.encode("ascii")
        if isinstance(item, list):
            return b"[" + b",".join(encode(child) for child in item) + b"]"
        if isinstance(item, dict):
            pieces = (
                encode(key) + b":" + encode(item[key])
                for key in sorted(item)
            )
            return b"{" + b",".join(pieces) + b"}"
        raise ExtractionError("value cannot be canonically encoded")

    return encode(value)


def canonical_content_sha256(value: object) -> str:
    return _sha256_hex(canonical_content_bytes(value))


def _parse_json_artifact(
    body: bytes,
    *,
    max_depth: int,
    max_nodes: int,
) -> dict[str, object]:
    try:
        decoded = body.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_int=_parse_json_integer,
            parse_float=_parse_json_fraction,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ExtractionError("response is not a valid UTF-8 JSON artifact") from exc
    _validate_json_tree(value, max_depth=max_depth, max_nodes=max_nodes)
    if not isinstance(value, dict):
        raise ExtractionError("licensed JSON artifact must be a top-level object")
    return cast(dict[str, object], value)


def _validate_key_path(path: tuple[str, ...], *, field: str) -> None:
    if not path or len(path) > _MAX_PATH_DEPTH:
        raise OfficialResolutionError(
            f"{field} must contain between 1 and {_MAX_PATH_DEPTH} object keys"
        )
    if any(_KEY_RE.fullmatch(key) is None for key in path):
        raise OfficialResolutionError(f"{field} contains an unsafe object key")


def _path_payload(path: tuple[str, ...]) -> list[str]:
    return list(path)


def _optional_path_payload(path: tuple[str, ...] | None) -> list[str] | None:
    return _path_payload(path) if path is not None else None


def _field(root: dict[str, object], path: tuple[str, ...], *, field: str) -> object:
    current: object = root
    for key in path:
        if not isinstance(current, dict) or key not in current:
            raise ExtractionError(f"artifact is missing {field} at {'.'.join(path)}")
        current = current[key]
    return current


def _string_field(root: dict[str, object], path: tuple[str, ...], *, field: str) -> str:
    value = _field(root, path, field=field)
    if not isinstance(value, str) or not value.strip():
        raise ExtractionError(f"{field} must be a non-empty string")
    return value


def _int_field(root: dict[str, object], path: tuple[str, ...], *, field: str) -> int:
    value = _field(root, path, field=field)
    if isinstance(value, _ExactJsonInteger):
        return int(value.token)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ExtractionError(f"{field} must be an integer")
    return value


def _parse_iso_datetime(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ExtractionError(f"{field} must be an ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ExtractionError(f"{field} is not a valid ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ExtractionError(f"{field} must include an explicit UTC offset")
    return parsed


def _parse_target_date(
    value: object,
    *,
    timezone: ZoneInfo,
    field: str,
) -> tuple[date, time | None]:
    if not isinstance(value, str):
        raise ExtractionError(f"{field} must be an ISO date or datetime string")
    try:
        parsed_date = date.fromisoformat(value)
    except ValueError:
        parsed = _parse_iso_datetime(value, field=field).astimezone(timezone)
        return parsed.date(), parsed.timetz().replace(tzinfo=None)
    return parsed_date, None


@dataclass(frozen=True, slots=True)
class OfficialResolutionContract:
    event_id: str
    rules_hash: str
    source_family: str
    source_url: str
    station_code: str
    local_timezone: str
    statistic: Statistic
    source_unit: str
    decimal_places: int
    finalization_rule: str
    window_start_local: time
    window_end_local: time
    window_end_day_offset: int

    def __post_init__(self) -> None:
        if (
            not self.event_id.strip()
            or not self.source_family.strip()
            or not self.station_code.strip()
        ):
            raise OfficialResolutionError("resolution contract identity is incomplete")
        _require_sha256(self.rules_hash, field="contract.rules_hash")
        _canonical_url(self.source_url, field="contract.source_url")
        if self.statistic not in {"high", "low"}:
            raise OfficialResolutionError("contract.statistic must be high or low")
        if self.source_unit not in {"C", "F"}:
            raise OfficialResolutionError("contract.source_unit must be C or F")
        if self.decimal_places not in {0, 1}:
            raise OfficialResolutionError("contract.decimal_places must be 0 or 1")
        if self.finalization_rule not in {
            "next_day_first_point",
            "source_finalized",
            "source_published",
        }:
            raise OfficialResolutionError("unsupported contract.finalization_rule")
        if self.window_end_day_offset not in {0, 1}:
            raise OfficialResolutionError("contract window day offset must be 0 or 1")
        _require_naive_local_time(
            self.window_start_local,
            field="contract.window_start_local",
        )
        _require_naive_local_time(
            self.window_end_local,
            field="contract.window_end_local",
        )
        if (
            self.window_end_day_offset == 0
            and self.window_end_local <= self.window_start_local
        ):
            raise OfficialResolutionError("contract source window must have positive duration")
        try:
            ZoneInfo(self.local_timezone)
        except ZoneInfoNotFoundError as exc:
            raise OfficialResolutionError("contract.local_timezone is unknown") from exc


def reviewed_official_resolution_contract(
    *,
    event_id: str,
    rules_hash: str,
    source_family: str,
    source_url: str,
    station_code: str,
    local_timezone: str,
    statistic: Statistic,
    source_unit: str,
    decimal_places: int,
    finalization_rule: str,
) -> OfficialResolutionContract:
    """Bind parsed market rules to the independently reviewed source window.

    The Wunderground History daily product is interpreted as exactly one local
    calendar day.  Keeping that interpretation in this evidence-hashed service
    prevents an API/UI refactor from silently redefining settlement truth.
    Unknown source families remain fail-closed until they receive their own
    reviewed policy revision.
    """

    if source_family != "wunderground":
        raise ContractBindingError(
            "no independently reviewed source-window mapping exists for this source family"
        )
    return OfficialResolutionContract(
        event_id=event_id,
        rules_hash=rules_hash,
        source_family=source_family,
        source_url=source_url,
        station_code=station_code,
        local_timezone=local_timezone,
        statistic=statistic,
        source_unit=source_unit,
        decimal_places=decimal_places,
        finalization_rule=finalization_rule,
        window_start_local=time(0, 0),
        window_end_local=time(0, 0),
        window_end_day_offset=1,
    )


@dataclass(frozen=True, slots=True)
class LicensedJsonFieldMapConfig:
    schema_id: str
    series_mode: JsonSeriesMode
    schema_path: tuple[str, ...] | None
    event_id_path: tuple[str, ...] | None
    rules_hash_path: tuple[str, ...] | None
    source_family_path: tuple[str, ...] | None
    source_url_path: tuple[str, ...] | None
    station_path: tuple[str, ...] | None
    date_path: tuple[str, ...]
    high_path: tuple[str, ...]
    low_path: tuple[str, ...]
    unit_path: tuple[str, ...] | None
    decimal_places_path: tuple[str, ...] | None
    finalization_rule_path: tuple[str, ...] | None
    window_start_at_path: tuple[str, ...] | None
    window_end_at_path: tuple[str, ...] | None
    finalization_witness_at_path: tuple[str, ...] | None
    published_at_path: tuple[str, ...] | None
    station_code_literal: str
    source_unit_literal: str
    decimal_places_literal: int
    finalization_rule_literal: str
    series_anchor_local_time: time | None = None
    source_window_start_local: time = time(0, 0)
    source_window_end_local: time = time(0, 0)
    source_window_end_day_offset: int = 1
    max_series_items: int = 400
    max_body_bytes: int = 1_000_000
    max_json_depth: int = 16
    max_json_nodes: int = 20_000

    def __post_init__(self) -> None:
        if not self.schema_id.strip():
            raise OfficialResolutionError("parser schema_id is required")
        if self.series_mode not in {"scalar", "parallel_arrays"}:
            raise OfficialResolutionError("parser series_mode is unsupported")
        for name in ("date_path", "high_path", "low_path"):
            _validate_key_path(getattr(self, name), field=f"parser.{name}")
        for name in (
            "schema_path",
            "event_id_path",
            "rules_hash_path",
            "source_family_path",
            "source_url_path",
            "station_path",
            "unit_path",
            "decimal_places_path",
            "finalization_rule_path",
            "window_start_at_path",
            "window_end_at_path",
            "finalization_witness_at_path",
            "published_at_path",
        ):
            path = getattr(self, name)
            if path is not None:
                _validate_key_path(path, field=f"parser.{name}")
        if (self.window_start_at_path is None) != (self.window_end_at_path is None):
            raise OfficialResolutionError(
                "parser artifact window start/end paths must be configured together"
            )
        if not self.station_code_literal.strip():
            raise OfficialResolutionError("parser station_code_literal is required")
        if self.source_unit_literal not in {"C", "F"}:
            raise OfficialResolutionError("parser source_unit_literal must be C or F")
        if self.decimal_places_literal not in {0, 1}:
            raise OfficialResolutionError(
                "parser decimal_places_literal must be zero or one"
            )
        if self.finalization_rule_literal not in {
            "next_day_first_point",
            "source_finalized",
            "source_published",
        }:
            raise OfficialResolutionError(
                "parser finalization_rule_literal is unsupported"
            )
        if self.series_mode == "parallel_arrays" and self.series_anchor_local_time is None:
            raise OfficialResolutionError(
                "parallel-array parser requires an explicit series_anchor_local_time"
            )
        if self.series_mode == "scalar" and self.series_anchor_local_time is not None:
            raise OfficialResolutionError(
                "scalar parser must not declare series_anchor_local_time"
            )
        if self.series_anchor_local_time is not None:
            _require_naive_local_time(
                self.series_anchor_local_time,
                field="parser.series_anchor_local_time",
            )
        _require_naive_local_time(
            self.source_window_start_local,
            field="parser.source_window_start_local",
        )
        _require_naive_local_time(
            self.source_window_end_local,
            field="parser.source_window_end_local",
        )
        if self.source_window_end_day_offset not in {0, 1}:
            raise OfficialResolutionError("parser source window day offset must be 0 or 1")
        if (
            self.source_window_end_day_offset == 0
            and self.source_window_end_local <= self.source_window_start_local
        ):
            raise OfficialResolutionError("parser source window must have positive duration")
        if not 1 <= self.max_series_items <= 10_000:
            raise OfficialResolutionError("parser max_series_items is outside [1, 10000]")
        if not 1_024 <= self.max_body_bytes <= 10_000_000:
            raise OfficialResolutionError("parser max_body_bytes is outside [1024, 10000000]")
        if not 1 <= self.max_json_depth <= 32:
            raise OfficialResolutionError("parser max_json_depth is outside [1, 32]")
        if not 1 <= self.max_json_nodes <= 100_000:
            raise OfficialResolutionError("parser max_json_nodes is outside [1, 100000]")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "schema_id": self.schema_id,
            "series_mode": self.series_mode,
            "station_code_literal": self.station_code_literal,
            "source_unit_literal": self.source_unit_literal,
            "decimal_places_literal": self.decimal_places_literal,
            "finalization_rule_literal": self.finalization_rule_literal,
            **{
                name: _optional_path_payload(getattr(self, name))
                for name in (
                    "schema_path",
                    "event_id_path",
                    "rules_hash_path",
                    "source_family_path",
                    "source_url_path",
                    "station_path",
                    "unit_path",
                    "decimal_places_path",
                    "finalization_rule_path",
                    "window_start_at_path",
                    "window_end_at_path",
                    "finalization_witness_at_path",
                    "published_at_path",
                )
            },
            "date_path": _path_payload(self.date_path),
            "high_path": _path_payload(self.high_path),
            "low_path": _path_payload(self.low_path),
            "series_anchor_local_time": (
                self.series_anchor_local_time.isoformat()
                if self.series_anchor_local_time is not None
                else None
            ),
            "source_window_start_local": self.source_window_start_local.isoformat(),
            "source_window_end_local": self.source_window_end_local.isoformat(),
            "source_window_end_day_offset": self.source_window_end_day_offset,
            "max_series_items": self.max_series_items,
            "max_body_bytes": self.max_body_bytes,
            "max_json_depth": self.max_json_depth,
            "max_json_nodes": self.max_json_nodes,
        }


def parser_config_sha256(config: LicensedJsonFieldMapConfig) -> str:
    return canonical_content_sha256(config.canonical_payload())


@dataclass(frozen=True, slots=True)
class LicensedContractBinding:
    event_id: str
    rules_hash: str
    source_url: str
    station_code: str

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.station_code.strip():
            raise OfficialResolutionError("licensed contract binding is incomplete")
        _require_sha256(self.rules_hash, field="binding.rules_hash")
        _canonical_url(self.source_url, field="binding.source_url")


@dataclass(frozen=True, slots=True)
class OfficialResolutionLicense:
    license_id: str
    grantee: str
    source_family: str
    endpoint_url: str
    adapter_name: str
    adapter_version: str
    parser_config: LicensedJsonFieldMapConfig
    parser_config_sha256: str
    contract_bindings: tuple[LicensedContractBinding, ...]
    terms_url: str
    terms_sha256: str
    valid_from: datetime
    expires_at: datetime
    storage_permitted: bool
    commercial_use_permitted: bool
    max_response_age_seconds: int
    policy_sha256: str = ""

    def __post_init__(self) -> None:
        if not self.license_id.strip() or not self.grantee.strip():
            raise OfficialResolutionError("license identity is incomplete")
        if (
            not self.source_family.strip()
            or not self.adapter_name.strip()
            or not self.adapter_version
        ):
            raise OfficialResolutionError("license source or adapter identity is incomplete")
        _canonical_url(self.endpoint_url, field="license.endpoint_url")
        _canonical_url(self.terms_url, field="license.terms_url")
        _require_sha256(self.terms_sha256, field="license.terms_sha256")
        _require_sha256(self.parser_config_sha256, field="license.parser_config_sha256")
        if self.policy_sha256:
            _require_sha256(self.policy_sha256, field="license.policy_sha256")
        if not self.contract_bindings:
            raise OfficialResolutionError("license must bind at least one resolution contract")
        if len(set(self.contract_bindings)) != len(self.contract_bindings):
            raise OfficialResolutionError("license contains duplicate contract bindings")
        valid_from = _utc(self.valid_from, field="license.valid_from")
        expires_at = _utc(self.expires_at, field="license.expires_at")
        if expires_at <= valid_from:
            raise OfficialResolutionError("license validity interval is empty")
        if not 1 <= self.max_response_age_seconds <= 86_400:
            raise OfficialResolutionError("license max_response_age_seconds is outside [1, 86400]")


def _binding_payload(binding: LicensedContractBinding) -> dict[str, str]:
    return {
        "event_id": binding.event_id,
        "rules_hash": binding.rules_hash,
        "source_url": _canonical_url(binding.source_url, field="binding.source_url"),
        "station_code": binding.station_code,
    }


def license_policy_payload(license_policy: OfficialResolutionLicense) -> dict[str, object]:
    bindings = sorted(
        (_binding_payload(binding) for binding in license_policy.contract_bindings),
        key=lambda item: (
            item["event_id"],
            item["rules_hash"],
            item["source_url"],
            item["station_code"],
        ),
    )
    return {
        "protocol": OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
        "license_id": license_policy.license_id,
        "grantee": license_policy.grantee,
        "source_family": license_policy.source_family,
        "endpoint_url": _canonical_url(
            license_policy.endpoint_url, field="license.endpoint_url"
        ),
        "adapter_name": license_policy.adapter_name,
        "adapter_version": license_policy.adapter_version,
        "parser_config": license_policy.parser_config.canonical_payload(),
        "parser_config_sha256": license_policy.parser_config_sha256,
        "contract_bindings": bindings,
        "terms_url": _canonical_url(license_policy.terms_url, field="license.terms_url"),
        "terms_sha256": license_policy.terms_sha256,
        "valid_from": _utc(license_policy.valid_from, field="license.valid_from").isoformat(),
        "expires_at": _utc(license_policy.expires_at, field="license.expires_at").isoformat(),
        "storage_permitted": license_policy.storage_permitted,
        "commercial_use_permitted": license_policy.commercial_use_permitted,
        "max_response_age_seconds": license_policy.max_response_age_seconds,
    }


def license_policy_sha256(license_policy: OfficialResolutionLicense) -> str:
    return canonical_content_sha256(license_policy_payload(license_policy))


def seal_license_policy(license_policy: OfficialResolutionLicense) -> OfficialResolutionLicense:
    """Seal a reviewed draft; callers must separately pin the returned hash as approved."""

    if license_policy.policy_sha256:
        raise LicensePolicyError("license policy is already sealed")
    return replace(license_policy, policy_sha256=license_policy_sha256(license_policy))


def validate_license_policy(
    license_policy: OfficialResolutionLicense,
    *,
    approved_policy_sha256: str,
    evaluated_at: datetime,
    adapter_name: str,
    adapter_version: str,
) -> None:
    _require_sha256(approved_policy_sha256, field="approved_policy_sha256")
    evaluated = _utc(evaluated_at, field="evaluated_at")
    expected_config_hash = parser_config_sha256(license_policy.parser_config)
    expected_policy_hash = license_policy_sha256(license_policy)
    if (
        not license_policy.policy_sha256
        or license_policy.policy_sha256 != expected_policy_hash
        or approved_policy_sha256 != expected_policy_hash
    ):
        raise LicensePolicyError(
            "license policy hash is absent, forged, or not externally approved"
        )
    if license_policy.parser_config_sha256 != expected_config_hash:
        raise LicensePolicyError("license parser configuration hash does not match its content")
    if (
        license_policy.adapter_name != adapter_name
        or license_policy.adapter_version != adapter_version
    ):
        raise LicensePolicyError("license does not attest the selected adapter implementation")
    if not license_policy.storage_permitted or not license_policy.commercial_use_permitted:
        raise LicensePolicyError("license does not permit commercial storage of source evidence")
    valid_from = _utc(license_policy.valid_from, field="license.valid_from")
    expires_at = _utc(license_policy.expires_at, field="license.expires_at")
    if not valid_from <= evaluated < expires_at:
        raise LicensePolicyError("license is not active at evaluation time")


@dataclass(frozen=True, slots=True)
class OfficialSourceResponse:
    acquisition_protocol: AcquisitionProtocol
    request_url: str | None
    requested_at: datetime
    received_at: datetime
    request_method: str | None
    status_code: int | None
    media_type: str
    body: bytes
    body_sha256: str
    tls_verified: bool | None
    source_published_at: datetime | None = None
    finalization_witness_at: datetime | None = None


def validate_response_envelope(
    response: OfficialSourceResponse,
    *,
    license_policy: OfficialResolutionLicense,
    evaluated_at: datetime,
) -> None:
    if response.acquisition_protocol == "licensed-http-v1":
        if response.request_url is None:
            raise ResponseEnvelopeError("licensed HTTP response lacks its redacted endpoint")
        endpoint = _canonical_url(response.request_url, field="response.request_url")
        licensed_endpoint = _canonical_url(
            license_policy.endpoint_url, field="license.endpoint_url"
        )
        if endpoint != licensed_endpoint:
            raise ResponseEnvelopeError(
                "response endpoint does not match the licensed endpoint"
            )
        if response.request_method != "GET":
            raise ResponseEnvelopeError("licensed source request method must be GET")
        if response.tls_verified is not True:
            raise ResponseEnvelopeError("source response lacks verified TLS transport")
        if response.status_code != 200:
            raise ResponseEnvelopeError("source response status must be exactly 200")
    elif response.acquisition_protocol == "authorized-artifact-v1":
        if (
            response.request_url is not None
            or response.request_method != "IMPORT"
            or response.status_code is not None
            or response.tls_verified is not None
        ):
            raise ResponseEnvelopeError(
                "authorized artifact must use IMPORT without fabricated HTTP metadata"
            )
    else:
        raise ResponseEnvelopeError("source acquisition protocol is unsupported")
    media_type = response.media_type.split(";", 1)[0].strip().lower()
    if media_type not in _MEDIA_TYPE_JSON and not media_type.endswith("+json"):
        raise ResponseEnvelopeError("source response is not an explicitly structured JSON artifact")
    if not response.body or len(response.body) > license_policy.parser_config.max_body_bytes:
        raise ResponseEnvelopeError("source response body is empty or exceeds its attested limit")
    _require_sha256(response.body_sha256, field="response.body_sha256")
    if _sha256_hex(response.body) != response.body_sha256:
        raise ResponseEnvelopeError("source response raw-byte hash mismatch")
    requested = _utc(response.requested_at, field="response.requested_at")
    received = _utc(response.received_at, field="response.received_at")
    evaluated = _utc(evaluated_at, field="evaluated_at")
    valid_from = _utc(license_policy.valid_from, field="license.valid_from")
    expires_at = _utc(license_policy.expires_at, field="license.expires_at")
    if not valid_from <= requested <= received < expires_at:
        raise ResponseEnvelopeError(
            "response was not requested and received under the active licence"
        )
    if received > evaluated:
        raise ResponseEnvelopeError("response receipt cannot be later than evaluation time")
    if evaluated - received > timedelta(seconds=license_policy.max_response_age_seconds):
        raise ResponseEnvelopeError("source response is older than its attested ingestion age")


def _contract_binding_matches(
    binding: LicensedContractBinding,
    contract: OfficialResolutionContract,
) -> bool:
    return (
        binding.event_id == contract.event_id
        and binding.rules_hash == contract.rules_hash
        and _canonical_url(binding.source_url, field="binding.source_url")
        == _canonical_url(contract.source_url, field="contract.source_url")
        and binding.station_code == contract.station_code
    )


def validate_contract_binding(
    contract: OfficialResolutionContract,
    *,
    license_policy: OfficialResolutionLicense,
    config: LicensedJsonFieldMapConfig,
) -> None:
    if license_policy.source_family != contract.source_family:
        raise ContractBindingError("license source family differs from the resolution contract")
    if config != license_policy.parser_config:
        raise ContractBindingError("adapter parser configuration differs from the licensed content")
    if parser_config_sha256(config) != license_policy.parser_config_sha256:
        raise ContractBindingError("adapter parser configuration hash is not licensed")
    if config.station_code_literal != contract.station_code:
        raise ContractBindingError("licensed parser station differs from the contract")
    if config.source_unit_literal != contract.source_unit:
        raise ContractBindingError("licensed parser unit differs from the contract")
    if config.decimal_places_literal != contract.decimal_places:
        raise ContractBindingError("licensed parser precision differs from the contract")
    if config.finalization_rule_literal != contract.finalization_rule:
        raise ContractBindingError(
            "licensed parser finalization rule differs from the contract"
        )
    if not any(
        _contract_binding_matches(binding, contract)
        for binding in license_policy.contract_bindings
    ):
        raise ContractBindingError(
            "license does not bind the exact event/rules/source/station contract"
        )
    if (
        config.source_window_start_local != contract.window_start_local
        or config.source_window_end_local != contract.window_end_local
        or config.source_window_end_day_offset != contract.window_end_day_offset
    ):
        raise ContractBindingError(
            "licensed source summary window is not settlement-equivalent to the contract window"
        )


def _exact_source_decimal(
    value: object,
    *,
    decimal_places: int,
    field: str,
) -> tuple[Decimal, str]:
    if isinstance(value, bool):
        raise ExtractionError(f"{field} must be an exact numeric token")
    if isinstance(value, _ExactJsonInteger):
        if decimal_places != 0:
            raise ExtractionError(
                f"{field} integer token does not prove one-decimal source precision"
            )
        token = value.token
    elif isinstance(value, int):
        if decimal_places != 0:
            raise ExtractionError(
                f"{field} integer token does not prove one-decimal source precision"
            )
        token = str(value)
    elif isinstance(value, _ExactJsonFraction):
        token = value.token
    elif isinstance(value, str):
        token = value
    else:
        raise ExtractionError(f"{field} must be an exact JSON numeric or string token")
    pattern = _INTEGER_VALUE_RE if decimal_places == 0 else _TENTH_VALUE_RE
    if pattern.fullmatch(token) is None:
        raise ExtractionError(f"{field} does not match contract precision {decimal_places}")
    try:
        result = Decimal(token)
    except InvalidOperation as exc:
        raise ExtractionError(f"{field} is not a finite decimal") from exc
    quantum = Decimal(1).scaleb(-decimal_places)
    if not result.is_finite() or result != result.quantize(quantum):
        raise ExtractionError(f"{field} exceeds contract precision")
    return result, token


def source_value_to_celsius(value: Decimal, unit: str) -> Decimal:
    if not value.is_finite():
        raise OfficialResolutionError("source temperature must be finite")
    if unit == "C":
        converted = value
    elif unit == "F":
        converted = (value - Decimal(32)) * Decimal(5) / Decimal(9)
    else:
        raise OfficialResolutionError("source temperature unit must be C or F")
    return converted.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def _series_value(
    root: dict[str, object],
    *,
    config: LicensedJsonFieldMapConfig,
    target_date: date,
    timezone: ZoneInfo,
    value_path: tuple[str, ...],
    field: str,
) -> object:
    if config.series_mode == "scalar":
        artifact_date, artifact_time = _parse_target_date(
            _field(root, config.date_path, field="date"),
            timezone=timezone,
            field="date",
        )
        if artifact_date != target_date or artifact_time is not None:
            raise ContractBindingError("scalar artifact date does not exactly match target date")
        return _field(root, value_path, field=field)

    dates = _field(root, config.date_path, field="date series")
    values = _field(root, value_path, field=f"{field} series")
    if not isinstance(dates, list) or not isinstance(values, list):
        raise ExtractionError("parallel-array date and temperature fields must be arrays")
    if len(dates) != len(values) or not 1 <= len(dates) <= config.max_series_items:
        raise ExtractionError(
            "parallel arrays are empty, misaligned, or exceed their attested bound"
        )
    matches: list[int] = []
    for index, raw_date in enumerate(dates):
        item_date, item_time = _parse_target_date(
            raw_date,
            timezone=timezone,
            field=f"date[{index}]",
        )
        if item_date == target_date and item_time == config.series_anchor_local_time:
            matches.append(index)
    if len(matches) != 1:
        raise ExtractionError("parallel date series must contain exactly one target-date anchor")
    return values[matches[0]]


def _artifact_binding_checks(
    root: dict[str, object],
    *,
    config: LicensedJsonFieldMapConfig,
    contract: OfficialResolutionContract,
    target_date: date,
    response: OfficialSourceResponse,
) -> tuple[
    tuple[Decimal, str],
    tuple[Decimal, str],
    datetime,
    datetime,
    datetime,
    datetime,
]:
    expected_strings = (
        (config.schema_path, config.schema_id, "schema"),
        (config.event_id_path, contract.event_id, "event_id"),
        (config.rules_hash_path, contract.rules_hash, "rules_hash"),
        (config.source_family_path, contract.source_family, "source_family"),
        (config.station_path, contract.station_code, "station"),
        (config.unit_path, contract.source_unit, "source_unit"),
        (config.finalization_rule_path, contract.finalization_rule, "finalization_rule"),
    )
    for path, expected, field in expected_strings:
        if path is None:
            continue
        if _string_field(root, path, field=field) != expected:
            raise ContractBindingError(f"artifact {field} differs from the exact contract")
    if config.source_url_path is not None:
        if _canonical_url(
            _string_field(root, config.source_url_path, field="source_url"),
            field="artifact.source_url",
        ) != _canonical_url(contract.source_url, field="contract.source_url"):
            raise ContractBindingError("artifact source URL differs from the contract source")
    if config.decimal_places_path is not None:
        if (
            _int_field(root, config.decimal_places_path, field="decimal_places")
            != contract.decimal_places
        ):
            raise ContractBindingError(
                "artifact precision differs from the contract precision"
            )

    timezone = ZoneInfo(contract.local_timezone)
    high_raw = _series_value(
        root,
        config=config,
        target_date=target_date,
        timezone=timezone,
        value_path=config.high_path,
        field="high",
    )
    low_raw = _series_value(
        root,
        config=config,
        target_date=target_date,
        timezone=timezone,
        value_path=config.low_path,
        field="low",
    )
    high = _exact_source_decimal(
        high_raw, decimal_places=contract.decimal_places, field="high"
    )
    low = _exact_source_decimal(low_raw, decimal_places=contract.decimal_places, field="low")
    if low[0] > high[0]:
        raise ExtractionError("artifact daily low exceeds daily high")

    expected_start = datetime.combine(
        target_date, contract.window_start_local, tzinfo=timezone
    )
    expected_end = datetime.combine(
        target_date + timedelta(days=contract.window_end_day_offset),
        contract.window_end_local,
        tzinfo=timezone,
    )
    if config.window_start_at_path is None or config.window_end_at_path is None:
        window_start = expected_start
        window_end = expected_end
    else:
        window_start = _parse_iso_datetime(
            _field(root, config.window_start_at_path, field="window_start_at"),
            field="window_start_at",
        )
        window_end = _parse_iso_datetime(
            _field(root, config.window_end_at_path, field="window_end_at"),
            field="window_end_at",
        )
    if config.finalization_witness_at_path is None:
        if response.finalization_witness_at is None:
            raise ExtractionError(
                "licensed response metadata lacks finalization-witness evidence"
            )
        witness = response.finalization_witness_at
    else:
        witness = _parse_iso_datetime(
            _field(
                root,
                config.finalization_witness_at_path,
                field="finalization_witness_at",
            ),
            field="finalization_witness_at",
        )
        if (
            response.finalization_witness_at is not None
            and _utc(
                response.finalization_witness_at,
                field="response.finalization_witness_at",
            )
            != witness.astimezone(UTC)
        ):
            raise ExtractionError(
                "artifact and response finalization-witness evidence conflict"
            )
    if config.published_at_path is None:
        if response.source_published_at is None:
            raise ExtractionError(
                "licensed response metadata lacks source-publication evidence"
            )
        published = response.source_published_at
    else:
        published = _parse_iso_datetime(
            _field(root, config.published_at_path, field="published_at"),
            field="published_at",
        )
        if (
            response.source_published_at is not None
            and _utc(
                response.source_published_at,
                field="response.source_published_at",
            )
            != published.astimezone(UTC)
        ):
            raise ExtractionError(
                "artifact and response source-publication evidence conflict"
            )
    witness = _utc(witness, field="finalization_witness_at")
    published = _utc(published, field="source_published_at")
    if window_start != expected_start or window_end != expected_end:
        raise ContractBindingError(
            "artifact source window does not exactly match contract local time"
        )
    if contract.finalization_rule == "next_day_first_point":
        witness_local = witness.astimezone(timezone)
        if witness_local.date() != target_date + timedelta(days=1):
            raise ExtractionError(
                "next-day finalization witness is not on the required next local date"
            )
        if witness < window_end:
            raise ExtractionError("next-day finalization witness is premature")
    if not window_start < window_end <= witness <= published:
        raise ExtractionError("source window/finalization/publication ordering is invalid")
    return high, low, window_start, window_end, witness, published


@dataclass(frozen=True, slots=True)
class OfficialResolutionRecord:
    protocol: str
    acquisition_protocol: AcquisitionProtocol
    adapter_name: str
    adapter_version: str
    license_id: str
    license_policy_sha256: str
    parser_config_sha256: str
    event_id: str
    rules_hash: str
    source_family: str
    source_url: str
    endpoint_url: str
    station_code: str
    target_date: date
    statistic: Statistic
    source_token: str
    source_value: Decimal
    source_unit: str
    value_c: Decimal
    decimal_places: int
    finalization_rule: str
    window_start_at: datetime
    window_end_at: datetime
    finalization_witness_at: datetime
    source_published_at: datetime
    requested_at: datetime
    retrieved_at: datetime
    response_sha256: str
    canonical_content_sha256: str
    record_sha256: str


def _record_payload(record: OfficialResolutionRecord) -> dict[str, object]:
    payload = asdict(record)
    payload.pop("record_sha256")
    for field in (
        "source_value",
        "value_c",
    ):
        payload[field] = format(cast(Decimal, payload[field]), "f")
    payload["target_date"] = record.target_date.isoformat()
    for field in (
        "window_start_at",
        "window_end_at",
        "finalization_witness_at",
        "source_published_at",
        "requested_at",
        "retrieved_at",
    ):
        payload[field] = _utc(cast(datetime, payload[field]), field=field).isoformat()
    return cast(dict[str, object], payload)


def official_resolution_record_sha256(record: OfficialResolutionRecord) -> str:
    return canonical_content_sha256(_record_payload(record))


class OfficialResolutionAdapter(Protocol):
    adapter_name: str
    adapter_version: str

    def extract(
        self,
        *,
        contract: OfficialResolutionContract,
        target_date: date,
        response: OfficialSourceResponse,
        license_policy: OfficialResolutionLicense,
        approved_policy_sha256: str,
        evaluated_at: datetime,
    ) -> OfficialResolutionRecord: ...


class LicensedJsonFieldMapAdapter:
    """Safe reference adapter for a reviewed, explicitly mapped JSON artifact."""

    def __init__(
        self,
        config: LicensedJsonFieldMapConfig,
        *,
        adapter_name: str = LICENSED_JSON_ADAPTER_NAME,
        adapter_version: str = LICENSED_JSON_ADAPTER_VERSION,
    ) -> None:
        if not adapter_name.strip() or not adapter_version.strip():
            raise OfficialResolutionError("adapter identity is incomplete")
        self.config = config
        self.adapter_name = adapter_name
        self.adapter_version = adapter_version

    def extract(
        self,
        *,
        contract: OfficialResolutionContract,
        target_date: date,
        response: OfficialSourceResponse,
        license_policy: OfficialResolutionLicense,
        approved_policy_sha256: str,
        evaluated_at: datetime,
    ) -> OfficialResolutionRecord:
        validate_license_policy(
            license_policy,
            approved_policy_sha256=approved_policy_sha256,
            evaluated_at=evaluated_at,
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
        )
        validate_contract_binding(
            contract,
            license_policy=license_policy,
            config=self.config,
        )
        validate_response_envelope(
            response,
            license_policy=license_policy,
            evaluated_at=evaluated_at,
        )
        artifact = _parse_json_artifact(
            response.body,
            max_depth=self.config.max_json_depth,
            max_nodes=self.config.max_json_nodes,
        )
        high, low, window_start, window_end, witness, published = _artifact_binding_checks(
            artifact,
            config=self.config,
            contract=contract,
            target_date=target_date,
            response=response,
        )
        requested = _utc(response.requested_at, field="response.requested_at")
        received = _utc(response.received_at, field="response.received_at")
        if published.astimezone(UTC) > requested:
            raise ExtractionError("source publication occurs after the captured request")
        selected, source_token = high if contract.statistic == "high" else low
        draft = OfficialResolutionRecord(
            protocol=OFFICIAL_RESOLUTION_PROTOCOL,
            acquisition_protocol=response.acquisition_protocol,
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
            license_id=license_policy.license_id,
            license_policy_sha256=license_policy.policy_sha256,
            parser_config_sha256=license_policy.parser_config_sha256,
            event_id=contract.event_id,
            rules_hash=contract.rules_hash,
            source_family=contract.source_family,
            source_url=_canonical_url(contract.source_url, field="contract.source_url"),
            endpoint_url=_canonical_url(
                license_policy.endpoint_url,
                field="license.endpoint_url",
            ),
            station_code=contract.station_code,
            target_date=target_date,
            statistic=contract.statistic,
            source_token=source_token,
            source_value=selected,
            source_unit=contract.source_unit,
            value_c=source_value_to_celsius(selected, contract.source_unit),
            decimal_places=contract.decimal_places,
            finalization_rule=contract.finalization_rule,
            window_start_at=window_start.astimezone(UTC),
            window_end_at=window_end.astimezone(UTC),
            finalization_witness_at=witness.astimezone(UTC),
            source_published_at=published.astimezone(UTC),
            requested_at=requested,
            retrieved_at=received,
            response_sha256=response.body_sha256,
            canonical_content_sha256=canonical_content_sha256(artifact),
            record_sha256="",
        )
        return replace(draft, record_sha256=official_resolution_record_sha256(draft))


class WundergroundOfficialResolutionAdapter:
    """No HTML scraper and no implicit public-page schema.

    The adapter remains unavailable unless a reviewed licensed JSON field map
    is explicitly injected. A future TWC schema must also prove its daily
    window is settlement-equivalent; a 07:00-to-07:00 daily summary therefore
    cannot silently stand in for a local-calendar Wunderground History value.
    """

    adapter_name = WUNDERGROUND_ADAPTER_NAME
    adapter_version = WUNDERGROUND_ADAPTER_VERSION

    def __init__(self, config: LicensedJsonFieldMapConfig | None = None) -> None:
        self.config = config

    def extract(
        self,
        *,
        contract: OfficialResolutionContract,
        target_date: date,
        response: OfficialSourceResponse,
        license_policy: OfficialResolutionLicense,
        approved_policy_sha256: str,
        evaluated_at: datetime,
    ) -> OfficialResolutionRecord:
        if self.config is None:
            raise OfficialResolutionUnavailable(
                "Wunderground extraction is unavailable until a licensed JSON endpoint "
                "schema and settlement-equivalent source window are explicitly reviewed"
            )
        if contract.source_family != "wunderground":
            raise ContractBindingError("Wunderground adapter requires a Wunderground contract")
        delegate = LicensedJsonFieldMapAdapter(
            self.config,
            adapter_name=self.adapter_name,
            adapter_version=self.adapter_version,
        )
        return delegate.extract(
            contract=contract,
            target_date=target_date,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=approved_policy_sha256,
            evaluated_at=evaluated_at,
        )


__all__ = [
    "AcquisitionProtocol",
    "ContractBindingError",
    "ExtractionError",
    "LICENSED_JSON_ADAPTER_NAME",
    "LICENSED_JSON_ADAPTER_VERSION",
    "OFFICIAL_RESOLUTION_LICENSE_PROTOCOL",
    "OFFICIAL_RESOLUTION_CONTRACT_POLICY_PROTOCOL",
    "LicensePolicyError",
    "LicensedContractBinding",
    "LicensedJsonFieldMapAdapter",
    "LicensedJsonFieldMapConfig",
    "OFFICIAL_RESOLUTION_PROTOCOL",
    "OfficialResolutionAdapter",
    "OfficialResolutionContract",
    "OfficialResolutionError",
    "OfficialResolutionLicense",
    "OfficialResolutionRecord",
    "OfficialResolutionUnavailable",
    "OfficialSourceResponse",
    "ResponseEnvelopeError",
    "WUNDERGROUND_ADAPTER_NAME",
    "WUNDERGROUND_ADAPTER_VERSION",
    "WundergroundOfficialResolutionAdapter",
    "canonical_content_bytes",
    "canonical_content_sha256",
    "license_policy_payload",
    "license_policy_sha256",
    "official_resolution_record_sha256",
    "parser_config_sha256",
    "reviewed_official_resolution_contract",
    "seal_license_policy",
    "source_value_to_celsius",
    "validate_contract_binding",
    "validate_license_policy",
    "validate_response_envelope",
]
