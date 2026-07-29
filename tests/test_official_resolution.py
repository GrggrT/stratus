from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from stratus.services.official_resolution import (
    LICENSED_JSON_ADAPTER_NAME,
    LICENSED_JSON_ADAPTER_VERSION,
    OFFICIAL_RESOLUTION_LICENSE_PROTOCOL,
    WUNDERGROUND_ADAPTER_NAME,
    WUNDERGROUND_ADAPTER_VERSION,
    ContractBindingError,
    ExtractionError,
    LicensedContractBinding,
    LicensedJsonFieldMapAdapter,
    LicensedJsonFieldMapConfig,
    LicensePolicyError,
    OfficialResolutionContract,
    OfficialResolutionError,
    OfficialResolutionLicense,
    OfficialResolutionRecord,
    OfficialResolutionUnavailable,
    OfficialSourceResponse,
    ResponseEnvelopeError,
    WundergroundOfficialResolutionAdapter,
    canonical_content_sha256,
    license_policy_payload,
    official_resolution_record_sha256,
    parser_config_sha256,
    reviewed_official_resolution_contract,
    seal_license_policy,
    source_value_to_celsius,
)

RULES_HASH = "a" * 64
TERMS_HASH = "b" * 64
TARGET_DATE = date(2026, 7, 14)
SOURCE_URL = "https://history.licensed.example/station/KAAA"
ENDPOINT_URL = "https://licensed.example/v1/official/KAAA"


def _scalar_config(
    *,
    source_unit: str = "C",
    decimal_places: int = 0,
    window_start: time = time(0),
    window_end: time = time(0),
    window_end_day_offset: int = 1,
) -> LicensedJsonFieldMapConfig:
    return LicensedJsonFieldMapConfig(
        schema_id="licensed-daily-v1",
        series_mode="scalar",
        schema_path=("schema",),
        event_id_path=("contract", "event_id"),
        rules_hash_path=("contract", "rules_hash"),
        source_family_path=("contract", "source_family"),
        source_url_path=("contract", "source_url"),
        station_path=("contract", "station"),
        date_path=("contract", "date"),
        high_path=("summary", "high"),
        low_path=("summary", "low"),
        unit_path=("contract", "unit"),
        decimal_places_path=("contract", "decimal_places"),
        finalization_rule_path=("contract", "finalization_rule"),
        window_start_at_path=("contract", "window_start_at"),
        window_end_at_path=("contract", "window_end_at"),
        finalization_witness_at_path=("contract", "finalization_witness_at"),
        published_at_path=("contract", "published_at"),
        station_code_literal="KAAA",
        source_unit_literal=source_unit,
        decimal_places_literal=decimal_places,
        finalization_rule_literal="next_day_first_point",
        source_window_start_local=window_start,
        source_window_end_local=window_end,
        source_window_end_day_offset=window_end_day_offset,
    )


def _parallel_config() -> LicensedJsonFieldMapConfig:
    return LicensedJsonFieldMapConfig(
        schema_id="licensed-series-v1",
        series_mode="parallel_arrays",
        schema_path=("schema",),
        event_id_path=("contract", "event_id"),
        rules_hash_path=("contract", "rules_hash"),
        source_family_path=("contract", "source_family"),
        source_url_path=("contract", "source_url"),
        station_path=("contract", "station"),
        date_path=("series", "validTimeLocal"),
        high_path=("series", "temperatureMax"),
        low_path=("series", "temperatureMin"),
        unit_path=("contract", "unit"),
        decimal_places_path=("contract", "decimal_places"),
        finalization_rule_path=("contract", "finalization_rule"),
        window_start_at_path=("contract", "window_start_at"),
        window_end_at_path=("contract", "window_end_at"),
        finalization_witness_at_path=("contract", "finalization_witness_at"),
        published_at_path=("contract", "published_at"),
        station_code_literal="KAAA",
        source_unit_literal="C",
        decimal_places_literal=0,
        finalization_rule_literal="next_day_first_point",
        series_anchor_local_time=time(7),
        source_window_start_local=time(7),
        source_window_end_local=time(7),
        source_window_end_day_offset=1,
    )


def _provider_parallel_config() -> LicensedJsonFieldMapConfig:
    """Attested map for raw provider arrays without injected market metadata."""

    return LicensedJsonFieldMapConfig(
        schema_id="provider-daily-summary-v3-reviewed",
        series_mode="parallel_arrays",
        schema_path=None,
        event_id_path=None,
        rules_hash_path=None,
        source_family_path=None,
        source_url_path=None,
        station_path=None,
        date_path=("validTimeLocal",),
        high_path=("temperatureMax",),
        low_path=("temperatureMin",),
        unit_path=None,
        decimal_places_path=None,
        finalization_rule_path=None,
        window_start_at_path=None,
        window_end_at_path=None,
        finalization_witness_at_path=None,
        published_at_path=None,
        station_code_literal="KAAA",
        source_unit_literal="C",
        decimal_places_literal=0,
        finalization_rule_literal="next_day_first_point",
        series_anchor_local_time=time(7),
        source_window_start_local=time(7),
        source_window_end_local=time(7),
        source_window_end_day_offset=1,
    )


def _contract(
    *,
    statistic: str = "high",
    source_family: str = "licensed_fixture",
    source_unit: str = "C",
    decimal_places: int = 0,
    window_start: time = time(0),
    window_end: time = time(0),
    window_end_day_offset: int = 1,
) -> OfficialResolutionContract:
    return OfficialResolutionContract(
        event_id="event-licensed-1",
        rules_hash=RULES_HASH,
        source_family=source_family,
        source_url=SOURCE_URL,
        station_code="KAAA",
        local_timezone="Europe/Berlin",
        statistic=statistic,  # type: ignore[arg-type]
        source_unit=source_unit,
        decimal_places=decimal_places,
        finalization_rule="next_day_first_point",
        window_start_local=window_start,
        window_end_local=window_end,
        window_end_day_offset=window_end_day_offset,
    )


def test_reviewed_wunderground_contract_uses_one_local_calendar_day() -> None:
    contract = reviewed_official_resolution_contract(
        event_id="event-1",
        rules_hash=RULES_HASH,
        source_family="wunderground",
        source_url=SOURCE_URL,
        station_code="KAAA",
        local_timezone="Europe/Berlin",
        statistic="high",
        source_unit="C",
        decimal_places=0,
        finalization_rule="next_day_first_point",
    )

    assert contract.window_start_local == time(0)
    assert contract.window_end_local == time(0)
    assert contract.window_end_day_offset == 1


def test_reviewed_contract_rejects_source_without_window_policy() -> None:
    with pytest.raises(ContractBindingError, match="no independently reviewed"):
        reviewed_official_resolution_contract(
            event_id="event-1",
            rules_hash=RULES_HASH,
            source_family="national",
            source_url=SOURCE_URL,
            station_code="KAAA",
            local_timezone="Europe/Berlin",
            statistic="high",
            source_unit="C",
            decimal_places=0,
            finalization_rule="next_day_first_point",
        )


def _license(
    contract: OfficialResolutionContract,
    config: LicensedJsonFieldMapConfig,
    *,
    adapter_name: str = LICENSED_JSON_ADAPTER_NAME,
    adapter_version: str = LICENSED_JSON_ADAPTER_VERSION,
    expires_at: datetime = datetime(2026, 8, 1, tzinfo=UTC),
) -> OfficialResolutionLicense:
    draft = OfficialResolutionLicense(
        license_id="license-fixture-v1",
        grantee="STRATUS test fixture",
        source_family=contract.source_family,
        endpoint_url=ENDPOINT_URL,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        parser_config=config,
        parser_config_sha256=parser_config_sha256(config),
        contract_bindings=(
            LicensedContractBinding(
                event_id=contract.event_id,
                rules_hash=contract.rules_hash,
                source_url=contract.source_url,
                station_code=contract.station_code,
            ),
        ),
        terms_url="https://licensed.example/terms/v1",
        terms_sha256=TERMS_HASH,
        valid_from=datetime(2026, 7, 1, tzinfo=UTC),
        expires_at=expires_at,
        storage_permitted=True,
        commercial_use_permitted=True,
        max_response_age_seconds=3_600,
    )
    return seal_license_policy(draft)


def _scalar_artifact(
    contract: OfficialResolutionContract,
    *,
    high: object = "31",
    low: object = "18",
    overrides: dict[str, object] | None = None,
) -> dict[str, object]:
    identity: dict[str, object] = {
        "event_id": contract.event_id,
        "rules_hash": contract.rules_hash,
        "source_family": contract.source_family,
        "source_url": contract.source_url,
        "station": contract.station_code,
        "date": TARGET_DATE.isoformat(),
        "unit": contract.source_unit,
        "decimal_places": contract.decimal_places,
        "finalization_rule": contract.finalization_rule,
        "window_start_at": "2026-07-14T00:00:00+02:00",
        "window_end_at": "2026-07-15T00:00:00+02:00",
        "finalization_witness_at": "2026-07-15T00:05:00+02:00",
        "published_at": "2026-07-15T00:10:00+02:00",
    }
    if overrides:
        identity.update(overrides)
    return {
        "schema": "licensed-daily-v1",
        "contract": identity,
        "summary": {"high": high, "low": low},
    }


def _response(
    artifact: dict[str, object] | None = None,
    *,
    body: bytes | None = None,
    request_url: str = ENDPOINT_URL,
    media_type: str = "application/json; charset=utf-8",
    status_code: int = 200,
    tls_verified: bool = True,
    requested_at: datetime = datetime(2026, 7, 14, 22, 20, tzinfo=UTC),
    received_at: datetime = datetime(2026, 7, 14, 22, 21, tzinfo=UTC),
) -> OfficialSourceResponse:
    payload = body
    if payload is None:
        if artifact is None:
            raise AssertionError("test response requires artifact or body")
        payload = json.dumps(artifact, separators=(",", ":")).encode()
    return OfficialSourceResponse(
        acquisition_protocol="licensed-http-v1",
        request_url=request_url,
        requested_at=requested_at,
        received_at=received_at,
        request_method="GET",
        status_code=status_code,
        media_type=media_type,
        body=payload,
        body_sha256=hashlib.sha256(payload).hexdigest(),
        tls_verified=tls_verified,
    )


def _extract(
    contract: OfficialResolutionContract,
    config: LicensedJsonFieldMapConfig,
    artifact: dict[str, object],
    *,
    license_policy: OfficialResolutionLicense | None = None,
    response: OfficialSourceResponse | None = None,
) -> OfficialResolutionRecord:
    sealed = license_policy or _license(contract, config)
    source_response = response or _response(artifact)
    return LicensedJsonFieldMapAdapter(config).extract(
        contract=contract,
        target_date=TARGET_DATE,
        response=source_response,
        license_policy=sealed,
        approved_policy_sha256=sealed.policy_sha256,
        evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
    )


def test_scalar_high_celsius_whole_degree_is_exact_and_deterministic() -> None:
    contract = _contract()
    config = _scalar_config()
    artifact = _scalar_artifact(contract)

    first = _extract(contract, config, artifact)
    second = _extract(contract, config, artifact)

    assert first.source_value == Decimal("31")
    assert first.value_c == Decimal("31.0000")
    assert first.statistic == "high"
    assert first.canonical_content_sha256 == canonical_content_sha256(artifact)
    assert first.record_sha256 == official_resolution_record_sha256(first)
    assert first == second


def test_scalar_low_fahrenheit_tenth_degree_converts_to_celsius() -> None:
    contract = _contract(statistic="low", source_unit="F", decimal_places=1)
    config = _scalar_config(source_unit="F", decimal_places=1)
    artifact = _scalar_artifact(contract, high="91.0", low="68.0")

    result = _extract(contract, config, artifact)

    assert result.source_value == Decimal("68.0")
    assert result.source_unit == "F"
    assert result.value_c == Decimal("20.0000")


def test_json_decimal_lexeme_is_parsed_without_binary_float() -> None:
    contract = _contract(statistic="low", source_unit="F", decimal_places=1)
    config = _scalar_config(source_unit="F", decimal_places=1)
    artifact = _scalar_artifact(contract, high=91.0, low=68.0)

    result = _extract(contract, config, artifact)

    assert result.source_token == "68.0"
    assert result.source_value == Decimal("68.0")
    assert result.value_c == Decimal("20.0000")


def test_parallel_arrays_use_the_unique_target_date_and_anchor_index() -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    config = _parallel_config()
    artifact: dict[str, object] = {
        "schema": "licensed-series-v1",
        "contract": {
            "event_id": contract.event_id,
            "rules_hash": contract.rules_hash,
            "source_family": contract.source_family,
            "source_url": contract.source_url,
            "station": contract.station_code,
            "unit": "C",
            "decimal_places": 0,
            "finalization_rule": "next_day_first_point",
            "window_start_at": "2026-07-14T07:00:00+02:00",
            "window_end_at": "2026-07-15T07:00:00+02:00",
            "finalization_witness_at": "2026-07-15T07:05:00+02:00",
            "published_at": "2026-07-15T07:10:00+02:00",
        },
        "series": {
            "validTimeLocal": [
                "2026-07-13T07:00:00+02:00",
                "2026-07-14T07:00:00+02:00",
                "2026-07-15T07:00:00+02:00",
            ],
            "temperatureMax": ["29", "31", "28"],
            "temperatureMin": ["17", "18", "16"],
        },
    }
    response = _response(
        artifact,
        requested_at=datetime(2026, 7, 15, 5, 20, tzinfo=UTC),
        received_at=datetime(2026, 7, 15, 5, 21, tzinfo=UTC),
    )
    license_policy = _license(contract, config)

    result = LicensedJsonFieldMapAdapter(config).extract(
        contract=contract,
        target_date=TARGET_DATE,
        response=response,
        license_policy=license_policy,
        approved_policy_sha256=license_policy.policy_sha256,
        evaluated_at=datetime(2026, 7, 15, 5, 22, tzinfo=UTC),
    )

    assert result.source_value == Decimal("31")


def test_unmodified_provider_arrays_parse_with_attested_contract_semantics() -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    config = _provider_parallel_config()
    artifact: dict[str, object] = {
        "validTimeLocal": [
            "2026-07-13T07:00:00+02:00",
            "2026-07-14T07:00:00+02:00",
        ],
        "temperatureMax": [29, 31],
        "temperatureMin": [17, 18],
    }
    response = replace(
        _response(
            artifact,
            requested_at=datetime(2026, 7, 15, 13, 20, tzinfo=UTC),
            received_at=datetime(2026, 7, 15, 13, 21, tzinfo=UTC),
        ),
        finalization_witness_at=datetime(2026, 7, 15, 5, 5, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 15, 13, 0, tzinfo=UTC),
    )
    license_policy = _license(contract, config)

    result = LicensedJsonFieldMapAdapter(config).extract(
        contract=contract,
        target_date=TARGET_DATE,
        response=response,
        license_policy=license_policy,
        approved_policy_sha256=license_policy.policy_sha256,
        evaluated_at=datetime(2026, 7, 15, 13, 22, tzinfo=UTC),
    )

    assert result.source_token == "31"
    assert result.source_value == Decimal("31")
    assert result.window_start_at == datetime(2026, 7, 14, 5, tzinfo=UTC)


@pytest.mark.parametrize(
    "config",
    [
        replace(_provider_parallel_config(), station_code_literal="KBBB"),
        replace(_provider_parallel_config(), source_unit_literal="F"),
        replace(_provider_parallel_config(), decimal_places_literal=1),
        replace(_provider_parallel_config(), source_window_start_local=time(0)),
    ],
)
def test_attested_provider_semantics_must_match_contract(
    config: LicensedJsonFieldMapConfig,
) -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    license_policy = _license(contract, config)
    artifact: dict[str, object] = {
        "validTimeLocal": ["2026-07-14T07:00:00+02:00"],
        "temperatureMax": [31],
        "temperatureMin": [18],
    }
    response = replace(
        _response(
            artifact,
            requested_at=datetime(2026, 7, 15, 13, 20, tzinfo=UTC),
            received_at=datetime(2026, 7, 15, 13, 21, tzinfo=UTC),
        ),
        finalization_witness_at=datetime(2026, 7, 15, 5, 5, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 15, 13, 0, tzinfo=UTC),
    )

    with pytest.raises(ContractBindingError):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 15, 13, 22, tzinfo=UTC),
        )


def test_provider_artifact_needs_explicit_publication_and_finality_evidence() -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    config = _provider_parallel_config()
    artifact: dict[str, object] = {
        "validTimeLocal": ["2026-07-14T07:00:00+02:00"],
        "temperatureMax": [31],
        "temperatureMin": [18],
    }
    license_policy = _license(contract, config)
    response = _response(
        artifact,
        requested_at=datetime(2026, 7, 15, 13, 20, tzinfo=UTC),
        received_at=datetime(2026, 7, 15, 13, 21, tzinfo=UTC),
    )

    with pytest.raises(ExtractionError, match="finalization-witness evidence"):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 15, 13, 22, tzinfo=UTC),
        )


def test_provider_date_must_match_the_target_date() -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    config = _provider_parallel_config()
    artifact: dict[str, object] = {
        "validTimeLocal": ["2026-07-13T07:00:00+02:00"],
        "temperatureMax": [31],
        "temperatureMin": [18],
    }
    response = replace(
        _response(
            artifact,
            requested_at=datetime(2026, 7, 15, 13, 20, tzinfo=UTC),
            received_at=datetime(2026, 7, 15, 13, 21, tzinfo=UTC),
        ),
        finalization_witness_at=datetime(2026, 7, 15, 5, 5, tzinfo=UTC),
        source_published_at=datetime(2026, 7, 15, 13, 0, tzinfo=UTC),
    )
    license_policy = _license(contract, config)

    with pytest.raises(ExtractionError, match="target-date anchor"):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 15, 13, 22, tzinfo=UTC),
        )


def test_parallel_arrays_require_one_exact_anchor_and_aligned_lengths() -> None:
    contract = _contract(window_start=time(7), window_end=time(7))
    config = _parallel_config()
    base: dict[str, object] = {
        "schema": "licensed-series-v1",
        "contract": {
            "event_id": contract.event_id,
            "rules_hash": contract.rules_hash,
            "source_family": contract.source_family,
            "source_url": contract.source_url,
            "station": contract.station_code,
            "unit": "C",
            "decimal_places": 0,
            "finalization_rule": "next_day_first_point",
            "window_start_at": "2026-07-14T07:00:00+02:00",
            "window_end_at": "2026-07-15T07:00:00+02:00",
            "finalization_witness_at": "2026-07-15T07:05:00+02:00",
            "published_at": "2026-07-15T07:10:00+02:00",
        },
        "series": {
            "validTimeLocal": ["2026-07-14T08:00:00+02:00"],
            "temperatureMax": ["31"],
            "temperatureMin": ["18"],
        },
    }
    response = _response(
        base,
        requested_at=datetime(2026, 7, 15, 5, 20, tzinfo=UTC),
        received_at=datetime(2026, 7, 15, 5, 21, tzinfo=UTC),
    )
    license_policy = _license(contract, config)

    with pytest.raises(ExtractionError, match="exactly one target-date anchor"):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 15, 5, 22, tzinfo=UTC),
        )


def test_source_window_must_be_explicitly_settlement_equivalent() -> None:
    contract = _contract()
    summary_07_to_07 = _parallel_config()
    license_policy = _license(contract, summary_07_to_07)

    with pytest.raises(ContractBindingError, match="not settlement-equivalent"):
        LicensedJsonFieldMapAdapter(summary_07_to_07).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=_response(_scalar_artifact(contract)),
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("field", "forged_value"),
    [
        ("rules_hash", "c" * 64),
        ("source_family", "similar-but-not-authoritative"),
        ("source_url", "https://similar.example/not-the-contract-source"),
        ("station", "KBBB"),
        ("unit", "F"),
        ("decimal_places", 1),
        ("finalization_rule", "source_published"),
    ],
)
def test_artifact_must_match_every_exact_contract_dimension(
    field: str,
    forged_value: object,
) -> None:
    contract = _contract()
    config = _scalar_config()
    artifact = _scalar_artifact(contract, overrides={field: forged_value})

    with pytest.raises(ContractBindingError):
        _extract(contract, config, artifact)


@pytest.mark.parametrize(
    "body",
    [
        b"{not-json",
        b'{"schema":"one","schema":"two"}',
        b'{"schema":1.5}',
    ],
)
def test_malformed_ambiguous_or_fractional_json_is_rejected(body: bytes) -> None:
    contract = _contract()
    config = _scalar_config()
    license_policy = _license(contract, config)

    with pytest.raises(ExtractionError):
        _extract(
            contract,
            config,
            _scalar_artifact(contract),
            license_policy=license_policy,
            response=_response(body=body),
        )


@pytest.mark.parametrize(
    ("high", "decimal_places"),
    [("31.0", 0), ("31", 1), ("31.00", 1)],
)
def test_temperature_token_must_be_a_string_at_exact_contract_precision(
    high: object,
    decimal_places: int,
) -> None:
    contract = _contract(decimal_places=decimal_places)
    config = _scalar_config(decimal_places=decimal_places)
    low = "18" if decimal_places == 0 else "18.0"

    with pytest.raises(ExtractionError):
        _extract(contract, config, _scalar_artifact(contract, high=high, low=low))


def test_forged_or_unapproved_license_policy_is_rejected() -> None:
    contract = _contract()
    config = _scalar_config()
    sealed = _license(contract, config)
    forged = replace(sealed, endpoint_url="https://attacker.example/official")

    with pytest.raises(LicensePolicyError, match="forged"):
        _extract(
            contract,
            config,
            _scalar_artifact(contract),
            license_policy=forged,
            response=_response(_scalar_artifact(contract), request_url=forged.endpoint_url),
        )
    with pytest.raises(LicensePolicyError, match="externally approved"):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=_response(_scalar_artifact(contract)),
            license_policy=sealed,
            approved_policy_sha256="d" * 64,
            evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
        )


def test_license_policy_uses_its_distinct_protocol() -> None:
    contract = _contract()
    config = _scalar_config()
    sealed = _license(contract, config)

    assert license_policy_payload(sealed)["protocol"] == OFFICIAL_RESOLUTION_LICENSE_PROTOCOL


def test_secret_query_parameters_are_rejected_without_leaking_values() -> None:
    secret = "do-not-log-this-secret"

    with pytest.raises(OfficialResolutionError) as caught:
        replace(
            _contract(),
            source_url=f"https://licensed.example/history?api_key={secret}",
        )

    assert secret not in str(caught.value)

    contract = _contract()
    config = _scalar_config()
    response = _response(
        _scalar_artifact(contract),
        request_url=f"https://licensed.example/official?access_token={secret}",
    )
    with pytest.raises(OfficialResolutionError) as response_error:
        _extract(contract, config, _scalar_artifact(contract), response=response)
    assert secret not in str(response_error.value)

def test_expired_license_is_rejected_even_for_a_pre_expiry_response() -> None:
    contract = _contract()
    config = _scalar_config()
    expired = _license(
        contract,
        config,
        expires_at=datetime(2026, 7, 14, 22, 21, 30, tzinfo=UTC),
    )

    with pytest.raises(LicensePolicyError, match="not active"):
        LicensedJsonFieldMapAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=_response(_scalar_artifact(contract)),
            license_policy=expired,
            approved_policy_sha256=expired.policy_sha256,
            evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
        )


@pytest.mark.parametrize("failure", ["endpoint", "media_type", "status", "tls"])
def test_response_envelope_is_fail_closed(failure: str) -> None:
    contract = _contract()
    config = _scalar_config()
    artifact = _scalar_artifact(contract)
    original = _response(artifact)
    if failure == "endpoint":
        response = replace(
            original,
            request_url="https://licensed.example/v1/official/KBBB",
        )
    elif failure == "media_type":
        response = replace(original, media_type="text/html")
    elif failure == "status":
        response = replace(original, status_code=206)
    else:
        response = replace(original, tls_verified=False)

    with pytest.raises(ResponseEnvelopeError):
        _extract(contract, config, artifact, response=response)


def test_response_raw_hash_is_verified() -> None:
    contract = _contract()
    config = _scalar_config()
    artifact = _scalar_artifact(contract)
    response = replace(_response(artifact), body_sha256="e" * 64)

    with pytest.raises(ResponseEnvelopeError, match="raw-byte hash mismatch"):
        _extract(contract, config, artifact, response=response)


def test_authorized_artifact_does_not_fabricate_http_or_tls_metadata() -> None:
    contract = _contract()
    config = _scalar_config()
    artifact = _scalar_artifact(contract)
    imported = replace(
        _response(artifact),
        acquisition_protocol="authorized-artifact-v1",
        request_url=None,
        request_method="IMPORT",
        status_code=None,
        tls_verified=None,
    )

    result = _extract(contract, config, artifact, response=imported)

    assert result.acquisition_protocol == "authorized-artifact-v1"
    with pytest.raises(ResponseEnvelopeError, match="fabricated HTTP metadata"):
        _extract(
            contract,
            config,
            artifact,
            response=replace(imported, tls_verified=True),
        )


def test_no_lookahead_rejects_future_publication_and_premature_witness() -> None:
    contract = _contract()
    config = _scalar_config()
    future_publication = _scalar_artifact(
        contract,
        overrides={"published_at": "2026-07-15T00:30:00+02:00"},
    )
    premature_witness = _scalar_artifact(
        contract,
        overrides={"finalization_witness_at": "2026-07-14T23:59:00+02:00"},
    )

    with pytest.raises(ExtractionError, match="after the captured request"):
        _extract(contract, config, future_publication)
    with pytest.raises(ExtractionError, match="finalization witness"):
        _extract(contract, config, premature_witness)

    conflicting_response = replace(
        _response(_scalar_artifact(contract)),
        source_published_at=datetime(2026, 7, 14, 22, 11, tzinfo=UTC),
    )
    with pytest.raises(ExtractionError, match="source-publication evidence conflict"):
        _extract(
            contract,
            config,
            _scalar_artifact(contract),
            response=conflicting_response,
        )


def test_wunderground_has_no_default_schema_and_never_accepts_html() -> None:
    contract = _contract(source_family="wunderground")
    config = _scalar_config()
    license_policy = _license(
        contract,
        config,
        adapter_name=WUNDERGROUND_ADAPTER_NAME,
        adapter_version=WUNDERGROUND_ADAPTER_VERSION,
    )
    response = _response(_scalar_artifact(contract))

    with pytest.raises(OfficialResolutionUnavailable, match="licensed JSON endpoint"):
        WundergroundOfficialResolutionAdapter().extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=response,
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
        )

    with pytest.raises(ResponseEnvelopeError, match="structured JSON"):
        WundergroundOfficialResolutionAdapter(config).extract(
            contract=contract,
            target_date=TARGET_DATE,
            response=replace(response, media_type="text/html"),
            license_policy=license_policy,
            approved_policy_sha256=license_policy.policy_sha256,
            evaluated_at=datetime(2026, 7, 14, 22, 22, tzinfo=UTC),
        )


def test_temperature_conversion_is_decimal_and_unit_explicit() -> None:
    assert source_value_to_celsius(Decimal("31.0"), "C") == Decimal("31.0000")
    assert source_value_to_celsius(Decimal("91.0"), "F") == Decimal("32.7778")
