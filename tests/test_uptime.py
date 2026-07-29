from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from stratus.config import Settings
from stratus.services.uptime import (
    CollectorSLOState,
    JobOutcome,
    MaintenanceWindow,
    calculate_collector_uptime,
)


def test_collector_uptime_passes_complete_mature_window() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    interval = timedelta(minutes=10)
    window = timedelta(hours=1)
    outcomes = [
        JobOutcome("collector", now - window + interval * index, "success")
        for index in range(1, 7)
    ]

    result = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window - timedelta(minutes=1),
        window=window,
        intervals={"collector": interval},
    )

    assert result.mature is True
    assert result.passed is True
    assert result.metrics[0].uptime == 1.0
    assert result.metrics[0].max_gap == interval


def test_collector_uptime_detects_missed_run_and_gap() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    interval = timedelta(minutes=10)
    window = timedelta(hours=1)
    outcomes = [
        JobOutcome("collector", now - window + interval * index, "success")
        for index in (1, 2, 5, 6)
    ]

    result = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window,
        window=window,
        intervals={"collector": interval},
    )

    assert result.passed is False
    assert result.metrics[0].uptime == 4 / 6
    assert result.metrics[0].max_gap == timedelta(minutes=30)


def test_collector_uptime_requires_full_window_before_pass() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    result = calculate_collector_uptime(
        [],
        now=now,
        monitoring_started=now - timedelta(hours=12),
        intervals={"collector": timedelta(hours=1)},
    )

    assert result.mature is False
    assert result.passed is False
    assert result.coverage == timedelta(hours=12)
    assert result.state is CollectorSLOState.FAILING


def test_short_startup_grace_is_explicit_but_does_not_claim_slo_pass() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    result = calculate_collector_uptime(
        [],
        now=now,
        monitoring_started=now - timedelta(hours=1),
        intervals={"collector": timedelta(minutes=10)},
    )

    assert result.mature is False
    assert result.passed is False
    assert result.state is CollectorSLOState.STARTUP_GRACE
    assert result.startup_grace_remaining == timedelta(hours=5)
    assert result.metrics[0].passed is False


def test_immature_healthy_window_is_collecting_not_passing() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    interval = timedelta(hours=1)
    outcomes = [
        JobOutcome("collector", now - timedelta(hours=12) + interval * index, "success")
        for index in range(1, 13)
    ]
    result = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - timedelta(hours=12),
        intervals={"collector": interval},
    )

    assert result.state is CollectorSLOState.COLLECTING
    assert result.passed is False
    assert result.metrics[0].passed is True


def test_planned_maintenance_is_excluded_but_remains_visible() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    window = timedelta(hours=1)
    interval = timedelta(minutes=10)
    maintenance = MaintenanceWindow(
        starts_at=now - timedelta(minutes=45),
        ends_at=now - timedelta(minutes=25),
        reason="database migration",
    )
    outcomes = [
        JobOutcome("collector", now - timedelta(minutes=50), "success"),
        JobOutcome("collector", now - timedelta(minutes=20), "success"),
        JobOutcome("collector", now - timedelta(minutes=10), "success"),
        JobOutcome("collector", now, "success"),
    ]

    raw = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window,
        window=window,
        intervals={"collector": interval},
    )
    planned = calculate_collector_uptime(
        outcomes,
        now=now,
        monitoring_started=now - window,
        window=window,
        intervals={"collector": interval},
        maintenance_windows=[maintenance],
    )

    assert raw.state is CollectorSLOState.FAILING
    assert planned.state is CollectorSLOState.PLANNED_MAINTENANCE
    assert planned.passed is True
    assert planned.excluded_maintenance == timedelta(minutes=20)
    assert planned.maintenance_reasons == ("database migration",)


def test_outage_beyond_planned_window_still_fails() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    maintenance = MaintenanceWindow(
        starts_at=now - timedelta(minutes=45),
        ends_at=now - timedelta(minutes=25),
        reason="database migration",
    )
    result = calculate_collector_uptime(
        [
            JobOutcome("collector", now - timedelta(minutes=50), "success"),
            JobOutcome("collector", now, "success"),
        ],
        now=now,
        monitoring_started=now - timedelta(hours=1),
        window=timedelta(hours=1),
        intervals={"collector": timedelta(minutes=10)},
        maintenance_windows=[maintenance],
    )

    assert result.state is CollectorSLOState.FAILING
    assert result.passed is False
    assert result.excluded_maintenance == timedelta(minutes=20)


def test_unbounded_maintenance_window_is_rejected() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    with pytest.raises(ValueError, match="cannot exceed two hours"):
        MaintenanceWindow(
            starts_at=now - timedelta(hours=3),
            ends_at=now,
            reason="too broad",
        )


def test_settings_require_complete_bounded_maintenance_window() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    settings = Settings(
        _env_file=None,
        health_maintenance_starts_at=now,
        health_maintenance_ends_at=now + timedelta(minutes=30),
        health_maintenance_reason="schema migration",
    )
    assert settings.health_maintenance_reason == "schema migration"

    with pytest.raises(ValidationError, match="configured together"):
        Settings(
            _env_file=None,
            health_maintenance_starts_at=now,
        )


def test_empty_compose_maintenance_values_disable_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HEALTH_MAINTENANCE_STARTS_AT", "")
    monkeypatch.setenv("HEALTH_MAINTENANCE_ENDS_AT", "")
    monkeypatch.setenv("HEALTH_MAINTENANCE_REASON", "")

    settings = Settings(_env_file=None)

    assert settings.health_maintenance_starts_at is None
    assert settings.health_maintenance_ends_at is None
    assert settings.health_maintenance_reason is None


def test_multiple_windows_cannot_chain_into_an_unbounded_exclusion() -> None:
    now = datetime(2026, 7, 8, 12, tzinfo=UTC)
    windows = [
        MaintenanceWindow(
            starts_at=now - timedelta(hours=4),
            ends_at=now - timedelta(hours=2, minutes=30),
            reason="first",
        ),
        MaintenanceWindow(
            starts_at=now - timedelta(hours=2),
            ends_at=now - timedelta(minutes=30),
            reason="second",
        ),
    ]

    with pytest.raises(ValueError, match="per SLO window"):
        calculate_collector_uptime(
            [],
            now=now,
            monitoring_started=now - timedelta(hours=6),
            window=timedelta(hours=6),
            intervals={"collector": timedelta(minutes=10)},
            maintenance_windows=windows,
        )
