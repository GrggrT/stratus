from stratus.services.health import (
    HealthSeverity,
    aggregate_health_status,
    health_status_blocks_readiness,
    health_status_requires_kill_switch,
    normalize_health_issue,
)


def test_legacy_issue_defaults_to_warning_and_new_payload_is_stable() -> None:
    normalized = normalize_health_issue(
        {"code": "legacy", "detail": "old audit row"}
    )

    assert normalized == {
        "code": "legacy",
        "detail": "old audit row",
        "severity": "warning",
    }
    assert aggregate_health_status([{"severity": "typo"}]) == "critical"


def test_health_status_uses_strongest_of_four_levels() -> None:
    issues = [
        {"severity": "info"},
        {"severity": "warning"},
        {"severity": "critical"},
        {"severity": "blocking"},
    ]

    assert aggregate_health_status([]) == "healthy"
    assert aggregate_health_status(issues[:1]) == "info"
    assert aggregate_health_status(issues[:2]) == "warning"
    assert aggregate_health_status(issues[:3]) == "critical"
    assert aggregate_health_status(issues) == "blocking"


def test_only_blocking_requires_kill_switch_but_critical_remains_nonready() -> None:
    assert health_status_requires_kill_switch("info") is False
    assert health_status_requires_kill_switch("warning") is False
    assert health_status_requires_kill_switch("critical") is False
    assert health_status_requires_kill_switch("blocking") is True
    assert health_status_blocks_readiness("warning") is False
    assert health_status_blocks_readiness("critical") is True
    assert health_status_blocks_readiness("blocking") is True


def test_explicit_override_can_promote_a_dynamic_issue_to_blocking() -> None:
    normalized = normalize_health_issue(
        {"code": "market_exposure_breach", "detail": "hard limit"},
        default_severity=HealthSeverity.WARNING,
        severity_override=HealthSeverity.BLOCKING,
    )

    assert normalized["severity"] == "blocking"
