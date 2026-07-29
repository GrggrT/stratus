from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import StrEnum


class HealthSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
    BLOCKING = "blocking"


_SEVERITY_RANK = {
    HealthSeverity.INFO: 0,
    HealthSeverity.WARNING: 1,
    HealthSeverity.CRITICAL: 2,
    HealthSeverity.BLOCKING: 3,
}


def parse_health_severity(
    value: object,
    *,
    legacy_default: HealthSeverity = HealthSeverity.WARNING,
) -> HealthSeverity:
    """Read new severity values while keeping legacy issue JSON usable."""

    if isinstance(value, HealthSeverity):
        return value
    if isinstance(value, str):
        try:
            return HealthSeverity(value.strip().lower())
        except ValueError:
            return HealthSeverity.CRITICAL
    return legacy_default if value is None else HealthSeverity.CRITICAL


def health_issue_payload(
    code: str,
    detail: str,
    severity: HealthSeverity,
) -> dict[str, str]:
    if not code.strip():
        raise ValueError("health issue code is required")
    if not detail.strip():
        raise ValueError("health issue detail is required")
    return {
        "code": code.strip(),
        "detail": detail.strip(),
        "severity": severity.value,
    }


def normalize_health_issue(
    issue: Mapping[str, object],
    *,
    default_severity: HealthSeverity = HealthSeverity.WARNING,
    severity_override: HealthSeverity | None = None,
) -> dict[str, str]:
    """Serialize legacy/new issue dictionaries in the common wire shape."""

    code = issue.get("code")
    detail = issue.get("detail")
    if not isinstance(code, str) or not isinstance(detail, str):
        raise ValueError("health issue requires string code and detail")
    severity = severity_override or parse_health_severity(
        issue.get("severity"),
        legacy_default=default_severity,
    )
    return health_issue_payload(code, detail, severity)


def aggregate_health_status(issues: Iterable[Mapping[str, object]]) -> str:
    """Return healthy or the strongest issue severity."""

    strongest: HealthSeverity | None = None
    for issue in issues:
        severity = parse_health_severity(issue.get("severity"))
        if strongest is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[strongest]:
            strongest = severity
    return strongest.value if strongest is not None else "healthy"


def health_status_blocks_readiness(status: str) -> bool:
    """Critical remains non-ready for audits written before four-level health."""

    return status.strip().lower() in {
        HealthSeverity.CRITICAL.value,
        HealthSeverity.BLOCKING.value,
    }


def health_status_requires_kill_switch(status: str) -> bool:
    """Only the explicit strongest level may engage the global stop."""

    return status.strip().lower() == HealthSeverity.BLOCKING.value
