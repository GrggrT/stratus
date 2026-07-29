from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_RUNTIME_SERVICES = ("worker", "api", "binance-market-stream", "telegram")
_PRODUCTION_DATABASE = "stratus"
_PRODUCTION_APP_ROLE = (
    "stratus_app_"
    + hashlib.md5(  # noqa: S324 - PostgreSQL-compatible identifier, not security
        _PRODUCTION_DATABASE.encode()
    ).hexdigest()
)


def _compose_service(compose: str, service: str) -> str:
    marker = f"\n  {service}:\n"
    start = compose.index(marker) + len(marker)
    following_services = [
        offset
        for offset in (
            compose.find(f"\n  {candidate}:\n", start)
            for candidate in (
                "validate-owner-password",
                "postgres",
                "migrate",
                "provision-runtime-role",
                "worker",
                "binance-market-stream",
                "api",
                "backup",
                "backup-offsite",
                "telegram",
            )
        )
        if offset >= 0
    ]
    end = min(following_services, default=len(compose))
    return compose[start:end]


def test_compose_routes_only_runtime_services_through_application_role() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    for service in _RUNTIME_SERVICES:
        block = _compose_service(compose, service)
        assert 'DATABASE_URL: ""' in block
        assert 'STRATUS_RUNTIME_DATABASE_URL: ""' in block
        assert 'entrypoint: ["/usr/local/bin/stratus-runtime-entrypoint"]' in block
        assert "source: stratus-runtime-password" in block
        assert "provision-runtime-role:" in block
        assert "condition: service_completed_successfully" in block

    migrate = _compose_service(compose, "migrate")
    postgres = _compose_service(compose, "postgres")
    backup = _compose_service(compose, "backup")
    provisioner = _compose_service(compose, "provision-runtime-role")
    validator = _compose_service(compose, "validate-owner-password")
    required_owner = (
        "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD must be set to a strong "
        "non-default owner password}"
    )
    assert required_owner in postgres
    assert required_owner in migrate
    assert required_owner in backup
    assert required_owner in provisioner
    assert "MIGRATION_DATABASE_URL:" in migrate
    assert "PGUSER: stratus" in backup
    assert "SELECT public.assert_stratus_runtime_boundary(false)" in provisioner
    assert "SELECT 'stratus_app_' || md5(current_database())" in provisioner
    assert '--command="\\password $$app_role"' in provisioner
    assert '--command="ALTER ROLE \\"$$app_role\\" LOGIN"' in provisioner
    assert '--username="$$app_role"' in provisioner
    assert "'TEMPORARY'" not in provisioner
    assert "STRATUS_RUNTIME_PASSWORD:" not in provisioner
    assert "stratus_app:${STRATUS_RUNTIME_PASSWORD" not in compose
    assert "${POSTGRES_PASSWORD:-stratus}" not in compose
    assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?" in postgres
    assert "PGPASSWORD: ${POSTGRES_PASSWORD:?" in backup
    assert 'if [ "$${#POSTGRES_PASSWORD}" -lt 24 ]' in validator
    assert "stratus|*[!A-Za-z0-9._~-]*" in validator
    assert "validate-owner-password:" in postgres
    assert "condition: service_completed_successfully" in postgres
    assert (
        "file: ${STRATUS_RUNTIME_PASSWORD_FILE:-"
        "./.secrets/stratus_runtime_password}"
    ) in compose


def test_compose_mounts_release_controlled_postgres_hba_boundary() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    postgres = _compose_service(compose, "postgres")

    assert "hba_file=/etc/postgresql/stratus-pg_hba.conf" in postgres
    assert "password_encryption=scram-sha-256" in postgres
    assert (
        "./ops/postgres/stratus-pg_hba.conf:"
        "/etc/postgresql/stratus-pg_hba.conf:ro"
    ) in postgres


def test_postgres_hba_allows_only_the_production_app_database() -> None:
    hba_path = _ROOT / "ops" / "postgres" / "stratus-pg_hba.conf"
    rules = [
        line.split()
        for raw_line in hba_path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.split("#", maxsplit=1)[0].strip())
    ]
    # PostgreSQL uses the leading slash as the regex marker; unlike sed, the
    # pattern has no closing slash delimiter.
    app_family = r"/^stratus_app_[0-9a-f]{32}$"

    assert rules == [
        ["local", _PRODUCTION_DATABASE, _PRODUCTION_APP_ROLE, "scram-sha-256"],
        ["local", "all", app_family, "reject"],
        [
            "host",
            _PRODUCTION_DATABASE,
            _PRODUCTION_APP_ROLE,
            "0.0.0.0/0",
            "scram-sha-256",
        ],
        ["host", "all", app_family, "0.0.0.0/0", "reject"],
        [
            "host",
            _PRODUCTION_DATABASE,
            _PRODUCTION_APP_ROLE,
            "::/0",
            "scram-sha-256",
        ],
        ["host", "all", app_family, "::/0", "reject"],
        ["local", "all", "all", "scram-sha-256"],
        ["host", "all", "all", "0.0.0.0/0", "scram-sha-256"],
        ["host", "all", "all", "::/0", "scram-sha-256"],
    ]
    assert {rule[-1] for rule in rules} == {"scram-sha-256", "reject"}


def test_runtime_entrypoint_reads_secret_and_rechecks_boundary() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (
        _ROOT / "scripts" / "stratus-runtime-entrypoint.sh"
    ).read_text(encoding="utf-8")

    assert "COPY scripts/stratus-runtime-entrypoint.sh" in dockerfile
    assert (
        "COPY ops/postgres/stratus-pg_hba.conf "
        "./ops/postgres/stratus-pg_hba.conf"
    ) in dockerfile
    assert "/run/secrets/stratus_runtime_password" in entrypoint
    assert "python -m stratus.runtime_db_boundary" in entrypoint
    assert 'exec "$@"' in entrypoint
    assert "echo \"$runtime_password\"" not in entrypoint


def test_host_api_launch_sanitizes_owner_environment_and_working_directory() -> None:
    script = (
        _ROOT / "scripts" / "Start-StratusDevApi.ps1"
    ).read_text(encoding="utf-8")

    assert "Start-SanitizedRuntimeProcess" in script
    assert "Write-RuntimeSafeEnvironmentFile" in script
    assert "-WorkingDirectory $RuntimeWorkingDirectory" in script
    assert 'Join-Path $LocalRuntimeRoot "STRATUS\\Runtime\\Api"' in script
    assert "DATABASE_URL" in script
    assert "POSTGRES" in script
    assert "STRATUS_TEST_DATABASE_URL" in script
    assert "postgres(?:ql)?(?:\\+asyncpg)?://" in script
    assert '$PostgresPassword = "stratus"' not in script
    assert "^[A-Za-z0-9._~-]{24,}$" in script
    assert "POSTGRES_PASSWORD is required" in script
    snapshot_names_at = script.index("$ownerEnvironmentNames = @(")
    snapshot_at = script.index("$ownerEnvironmentSnapshot = @{}")
    owner_assignment_at = script.index("$env:POSTGRES_PASSWORD = $PostgresPassword")
    outer_finally_at = script.rindex("finally {")
    final_secret_clear_at = script.rindex("$PostgresPassword = $null")
    final_restore_at = script.rindex(
        '[Environment]::SetEnvironmentVariable($name, $value, "Process")'
    )
    assert snapshot_at < owner_assignment_at < outer_finally_at
    assert outer_finally_at < final_secret_clear_at < final_restore_at
    for owner_name in (
        "POSTGRES_PASSWORD",
        "MIGRATION_DATABASE_URL",
        "DATABASE_URL",
        "STRATUS_RUNTIME_DATABASE_URL",
        "STRATUS_TEST_DATABASE_URL",
        "PGPASSWORD",
    ):
        assert f'"{owner_name}"' in script[snapshot_names_at:owner_assignment_at]


def test_owner_and_runtime_credentials_are_documented_separately() -> None:
    example = (_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "POSTGRES_PASSWORD=" in example
    assert "POSTGRES_PASSWORD=stratus" not in example
    assert "MIGRATION_DATABASE_URL=" not in example
    assert "STRATUS_RUNTIME_DATABASE_URL=" in example
    assert "STRATUS_RUNTIME_PASSWORD_FILE=" in example
    assert "Required before starting application services" in example
    assert ".secrets/" in (_ROOT / ".gitignore").read_text(encoding="utf-8")


def test_ci_compose_validation_supplies_strong_test_owner_password() -> None:
    workflow = (_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )
    validate_step = workflow[workflow.index("- name: Validate Compose") :]
    validate_step = validate_step[: validate_step.index("- name: Docker build")]

    assert "POSTGRES_PASSWORD: ci-only-owner-password-000000000000" in validate_step
    assert "docker compose config --quiet" in validate_step


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is unavailable")
def test_compose_fails_closed_without_owner_password(tmp_path: Path) -> None:
    empty_environment = tmp_path / "empty.env"
    empty_environment.write_text("POSTGRES_PASSWORD=\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("POSTGRES_PASSWORD", None)

    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            str(empty_environment),
            "config",
            "--quiet",
        ],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode != 0
    assert "POSTGRES_PASSWORD" in result.stderr
