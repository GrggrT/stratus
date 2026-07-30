from __future__ import annotations

import pytest

from stratus.services.http_telemetry import (
    RuntimeHttpTelemetry,
    normalize_load_gate_run_id,
)


def test_load_gate_run_id_is_strict_and_normalized() -> None:
    run_id = "ABCDEF0123456789ABCDEF0123456789"

    assert normalize_load_gate_run_id(run_id) == run_id.lower()
    assert normalize_load_gate_run_id(None) is None
    assert normalize_load_gate_run_id("a" * 31) is None
    assert normalize_load_gate_run_id("g" * 32) is None


def test_http_telemetry_separates_tagged_and_foreign_api_load() -> None:
    run_id = "a" * 32
    telemetry = RuntimeHttpTelemetry()

    first = telemetry.begin_api_request(run_id)
    telemetry.record_api_response(status_code=200, attribution_token=first)
    second = telemetry.begin_api_request(run_id)
    telemetry.record_api_response(status_code=503, attribution_token=second)
    foreign = telemetry.begin_api_request(None)
    telemetry.record_api_response(status_code=200, attribution_token=foreign)
    invalid = telemetry.begin_api_request("not-a-run")
    telemetry.record_api_response(status_code=500, attribution_token=invalid)

    snapshot = telemetry.snapshot(run_id)

    assert snapshot.run_started == 2
    assert snapshot.run_completed == 2
    assert snapshot.run_aborted == 0
    assert snapshot.run_5xx == 1
    assert snapshot.total_started == 4
    assert snapshot.total_completed == 4
    assert snapshot.total_aborted == 0
    assert snapshot.total_5xx == 2
    assert snapshot.active_requests == 0
    assert snapshot.untagged_api_started == 2
    assert snapshot.untagged_api_completed == 2
    assert snapshot.untagged_api_5xx == 1


def test_http_telemetry_bounds_untrusted_run_ids() -> None:
    telemetry = RuntimeHttpTelemetry(max_run_ids=1)
    first = "1" * 32
    overflow = "2" * 32

    first_token = telemetry.begin_api_request(first)
    telemetry.record_api_response(status_code=200, attribution_token=first_token)
    overflow_token = telemetry.begin_api_request(overflow)
    telemetry.record_api_response(
        status_code=503,
        attribution_token=overflow_token,
    )

    assert telemetry.snapshot(first).run_started == 1
    overflow_snapshot = telemetry.snapshot(overflow)
    assert overflow_snapshot.run_started == 0
    assert overflow_snapshot.untagged_api_started == 1
    assert overflow_snapshot.untagged_api_completed == 1
    assert overflow_snapshot.untagged_api_5xx == 1


def test_http_telemetry_exposes_foreign_valid_run_and_inflight_work() -> None:
    telemetry = RuntimeHttpTelemetry()
    current = "a" * 32
    foreign = "b" * 32

    current_token = telemetry.begin_api_request(current)
    foreign_token = telemetry.begin_api_request(foreign)
    snapshot = telemetry.snapshot(current)

    assert snapshot.run_started == 1
    assert snapshot.total_started == 2
    assert snapshot.total_completed == 0
    assert snapshot.active_requests == 2

    telemetry.record_api_response(
        status_code=200,
        attribution_token=current_token,
    )
    telemetry.record_api_response(
        status_code=200,
        attribution_token=foreign_token,
    )


def test_http_telemetry_records_aborts_without_leaking_active_capacity() -> None:
    telemetry = RuntimeHttpTelemetry()
    run_id = "a" * 32

    token = telemetry.begin_api_request(run_id)
    telemetry.record_api_abort(attribution_token=token)
    snapshot = telemetry.snapshot(run_id)

    assert snapshot.run_started == 1
    assert snapshot.run_completed == 1
    assert snapshot.run_aborted == 1
    assert snapshot.total_aborted == 1
    assert snapshot.active_requests == 0


def test_http_telemetry_rejects_invalid_configuration_and_snapshot_id() -> None:
    with pytest.raises(ValueError, match="capacity must be positive"):
        RuntimeHttpTelemetry(max_run_ids=0)
    with pytest.raises(ValueError, match="exactly 32 lowercase hex"):
        RuntimeHttpTelemetry().snapshot("invalid")
