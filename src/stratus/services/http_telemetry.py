from __future__ import annotations

import re
from dataclasses import dataclass

_LOAD_GATE_RUN_ID = re.compile(r"^[0-9a-f]{32}$")


def normalize_load_gate_run_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized if _LOAD_GATE_RUN_ID.fullmatch(normalized) else None


@dataclass(frozen=True, slots=True)
class HttpTelemetrySnapshot:
    run_id: str
    run_started: int
    run_completed: int
    run_aborted: int
    run_5xx: int
    total_started: int
    total_completed: int
    total_aborted: int
    total_5xx: int
    active_requests: int
    untagged_api_started: int
    untagged_api_completed: int
    untagged_api_5xx: int


@dataclass(slots=True)
class _RunCounters:
    started: int = 0
    completed: int = 0
    aborted: int = 0
    errors_5xx: int = 0


class RuntimeHttpTelemetry:
    """Small in-process counter set used to prove an uncontaminated load gate."""

    def __init__(self, *, max_run_ids: int = 64) -> None:
        if max_run_ids < 1:
            raise ValueError("HTTP telemetry run capacity must be positive")
        self._max_run_ids = max_run_ids
        self._runs: dict[str, _RunCounters] = {}
        self._total_started = 0
        self._total_completed = 0
        self._total_aborted = 0
        self._total_5xx = 0
        self._untagged_api_started = 0
        self._untagged_api_completed = 0
        self._untagged_api_5xx = 0

    def begin_api_request(self, run_id: str | None) -> str | None:
        """Record request start and return its bounded attribution token."""
        self._total_started += 1
        normalized = normalize_load_gate_run_id(run_id)
        if normalized is not None:
            counters = self._runs.get(normalized)
            if counters is None and len(self._runs) < self._max_run_ids:
                counters = _RunCounters()
                self._runs[normalized] = counters
            if counters is not None:
                counters.started += 1
                return normalized
        self._untagged_api_started += 1
        return None

    def record_api_response(
        self,
        *,
        status_code: int,
        attribution_token: str | None,
    ) -> None:
        self._total_completed += 1
        if status_code >= 500:
            self._total_5xx += 1
        if attribution_token is None:
            self._untagged_api_completed += 1
            if status_code >= 500:
                self._untagged_api_5xx += 1
            return

        counters = self._runs[attribution_token]
        counters.completed += 1
        if status_code >= 500:
            counters.errors_5xx += 1

    def record_api_abort(self, *, attribution_token: str | None) -> None:
        self._total_completed += 1
        self._total_aborted += 1
        if attribution_token is None:
            self._untagged_api_completed += 1
            return
        counters = self._runs[attribution_token]
        counters.completed += 1
        counters.aborted += 1

    def snapshot(self, run_id: str) -> HttpTelemetrySnapshot:
        normalized = normalize_load_gate_run_id(run_id)
        if normalized is None:
            raise ValueError("load-gate run ID must be exactly 32 lowercase hex characters")
        counters = self._runs.get(normalized, _RunCounters())
        return HttpTelemetrySnapshot(
            run_id=normalized,
            run_started=counters.started,
            run_completed=counters.completed,
            run_aborted=counters.aborted,
            run_5xx=counters.errors_5xx,
            total_started=self._total_started,
            total_completed=self._total_completed,
            total_aborted=self._total_aborted,
            total_5xx=self._total_5xx,
            active_requests=self._total_started - self._total_completed,
            untagged_api_started=self._untagged_api_started,
            untagged_api_completed=self._untagged_api_completed,
            untagged_api_5xx=self._untagged_api_5xx,
        )
