from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Sequence
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_HBA_CONTAINER_PATH = "/etc/postgresql/stratus-pg_hba.conf"
_POSTGRES_IMAGE = (
    "postgres:16-alpine@sha256:"
    "20edbde7749f822887a1a022ad526fde0a47d6b2be9a8364433605cf65099416"
)
_DATABASE = "stratus"
_OWNER = "stratus"
_OWNER_PASSWORD = "isolated-owner-password-000000000000"
_APP_PASSWORD = "isolated-runtime-password-0000000000"
_SAFETY_PASSWORD = "isolated-safety-password-00000000000"
_APP_ROLE = (
    "stratus_app_"
    + hashlib.md5(  # noqa: S324 - PostgreSQL role scheme
        _DATABASE.encode()
    ).hexdigest()
)
_SAFETY_APP_ROLE = (
    "stratus_safety_app_"
    + hashlib.md5(  # noqa: S324
        _DATABASE.encode()
    ).hexdigest()
)
_SIBLING_DATABASE = "stratus_sibling"
_SIBLING_ROLE = (
    "stratus_app_"
    + hashlib.md5(  # noqa: S324 - PostgreSQL role scheme
        _SIBLING_DATABASE.encode()
    ).hexdigest()
)
_SIBLING_SAFETY_ROLE = (
    "stratus_safety_app_"
    + hashlib.md5(  # noqa: S324
        _SIBLING_DATABASE.encode()
    ).hexdigest()
)


def _run(
    arguments: Sequence[str],
    *,
    environment: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["docker", *arguments],
        cwd=_ROOT,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"docker command failed with exit {result.returncode}: {result.stderr}"
        )
    return result


def _client_psql(
    network: str,
    server: str,
    *,
    user: str,
    password: str,
    database: str,
    sql: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    return _run(
        [
            "run",
            "--rm",
            "--network",
            network,
            "--env",
            "PGPASSWORD",
            _POSTGRES_IMAGE,
            "psql",
            "-XAtq",
            "--set=ON_ERROR_STOP=1",
            "--host",
            server,
            "--username",
            user,
            "--dbname",
            database,
            "--command",
            sql,
        ],
        environment=environment,
        check=check,
    )


def _server_psql(
    server: str,
    *,
    host: str | None,
    user: str,
    password: str,
    database: str,
    sql: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PGPASSWORD"] = password
    arguments = [
        "exec",
        "--env",
        "PGPASSWORD",
        server,
        "psql",
        "-XAtq",
        "--set=ON_ERROR_STOP=1",
    ]
    if host is not None:
        arguments.extend(("--host", host))
    arguments.extend(
        (
            "--username",
            user,
            "--dbname",
            database,
            "--command",
            sql,
        )
    )
    return _run(arguments, environment=environment, check=check)


def _assert_hba_rejection(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode != 0
    assert "pg_hba.conf rejects connection" in result.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI is unavailable")
def test_release_hba_enforces_database_scoped_runtime_logins() -> None:
    """Exercise the mounted production HBA in an isolated PostgreSQL cluster."""

    suffix = uuid.uuid4().hex[:12]
    network = f"stratus-hba-{suffix}"
    server = f"stratus-hba-db-{suffix}"
    hba_path = (_ROOT / "ops" / "postgres" / "stratus-pg_hba.conf").resolve()
    server_environment = os.environ.copy()
    server_environment.update(
        {
            "POSTGRES_DB": _DATABASE,
            "POSTGRES_USER": _OWNER,
            "POSTGRES_PASSWORD": _OWNER_PASSWORD,
        }
    )

    _run(["network", "create", network])
    try:
        _run(
            [
                "run",
                "--detach",
                "--name",
                server,
                "--network",
                network,
                "--env",
                "POSTGRES_DB",
                "--env",
                "POSTGRES_USER",
                "--env",
                "POSTGRES_PASSWORD",
                "--mount",
                f"type=bind,source={hba_path},target={_HBA_CONTAINER_PATH},readonly",
                _POSTGRES_IMAGE,
                "postgres",
                "-c",
                f"hba_file={_HBA_CONTAINER_PATH}",
                "-c",
                "password_encryption=scram-sha-256",
            ],
            environment=server_environment,
        )

        deadline = time.monotonic() + 60
        owner_target: subprocess.CompletedProcess[str] | None = None
        while time.monotonic() < deadline:
            logs = _run(["logs", server], check=False)
            rendered_logs = f"{logs.stdout}\n{logs.stderr}"
            # pg_isready can observe the image's temporary bootstrap server
            # just before it is shut down.  Wait for the official entrypoint's
            # hand-off marker, then require a real network-authenticated query.
            if "PostgreSQL init process complete; ready for start up." in rendered_logs:
                probe = _client_psql(
                    network,
                    server,
                    user=_OWNER,
                    password=_OWNER_PASSWORD,
                    database=_DATABASE,
                    sql="SELECT current_database()",
                    check=False,
                )
                if probe.returncode == 0 and probe.stdout.strip() == _DATABASE:
                    owner_target = probe
                    break
            time.sleep(0.5)
        else:
            logs = _run(["logs", server], check=False)
            raise AssertionError(f"isolated PostgreSQL did not become ready: {logs.stderr}")

        # The owner still crosses databases through the final SCRAM rule.
        assert owner_target is not None
        assert owner_target.stdout.strip() == _DATABASE
        _client_psql(
            network,
            server,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_DATABASE,
            sql=f'CREATE DATABASE "{_SIBLING_DATABASE}"',
        )
        _client_psql(
            network,
            server,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_DATABASE,
            sql=(
                f"CREATE ROLE \"{_APP_ROLE}\" LOGIN PASSWORD '{_APP_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
                "NOBYPASSRLS; "
                f"CREATE ROLE \"{_SIBLING_ROLE}\" LOGIN PASSWORD '{_APP_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
                "NOBYPASSRLS; "
                f'CREATE ROLE "{_SAFETY_APP_ROLE}" LOGIN '
                f"PASSWORD '{_SAFETY_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
                "NOBYPASSRLS; "
                f'CREATE ROLE "{_SIBLING_SAFETY_ROLE}" LOGIN '
                f"PASSWORD '{_SAFETY_PASSWORD}' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION "
                "NOBYPASSRLS; "
                f'GRANT CONNECT ON DATABASE "{_DATABASE}" TO "{_APP_ROLE}"; '
                f'GRANT CONNECT ON DATABASE "{_SIBLING_DATABASE}" TO "{_SIBLING_ROLE}"; '
                f'GRANT CONNECT ON DATABASE "{_DATABASE}" TO "{_SAFETY_APP_ROLE}"; '
                f'GRANT CONNECT ON DATABASE "{_SIBLING_DATABASE}" '
                f'TO "{_SIBLING_SAFETY_ROLE}"; '
                f'REVOKE TEMPORARY ON DATABASE "{_DATABASE}" FROM PUBLIC'
            ),
        )
        owner_sibling = _client_psql(
            network,
            server,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_SIBLING_DATABASE,
            sql="SELECT current_database()",
        )
        assert owner_sibling.stdout.strip() == _SIBLING_DATABASE
        owner_local = _server_psql(
            server,
            host=None,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert owner_local.stdout.strip() == _DATABASE

        configured_hba = _client_psql(
            network,
            server,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_DATABASE,
            sql="SHOW hba_file",
        )
        assert configured_hba.stdout.strip() == _HBA_CONTAINER_PATH
        invalid_rules = _client_psql(
            network,
            server,
            user=_OWNER,
            password=_OWNER_PASSWORD,
            database=_DATABASE,
            sql="SELECT count(*) FROM pg_hba_file_rules WHERE error IS NOT NULL",
        )
        assert invalid_rules.stdout.strip() == "0"

        app_target = _client_psql(
            network,
            server,
            user=_APP_ROLE,
            password=_APP_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert app_target.stdout.strip() == _DATABASE
        safety_target = _client_psql(
            network,
            server,
            user=_SAFETY_APP_ROLE,
            password=_SAFETY_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert safety_target.stdout.strip() == _DATABASE

        # Both cross-database directions are rejected before PUBLIC CONNECT or
        # the general owner SCRAM rule can admit them.
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_SAFETY_APP_ROLE,
                password=_SAFETY_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_SAFETY_APP_ROLE,
                password=_SAFETY_PASSWORD,
                database=_SIBLING_DATABASE,
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_SIBLING_SAFETY_ROLE,
                password=_SAFETY_PASSWORD,
                database=_DATABASE,
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                database="template1",
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                database=_SIBLING_DATABASE,
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _client_psql(
                network,
                server,
                user=_SIBLING_ROLE,
                password=_APP_PASSWORD,
                database=_DATABASE,
                sql="SELECT 1",
                check=False,
            )
        )

        # Unix-socket and IPv6 rules use the same exact-allow/family-reject
        # boundary.  These checks run inside the isolated server container.
        local_target = _server_psql(
            server,
            host=None,
            user=_APP_ROLE,
            password=_APP_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert local_target.stdout.strip() == _DATABASE
        local_safety_target = _server_psql(
            server,
            host=None,
            user=_SAFETY_APP_ROLE,
            password=_SAFETY_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert local_safety_target.stdout.strip() == _DATABASE
        _assert_hba_rejection(
            _server_psql(
                server,
                host=None,
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _server_psql(
                server,
                host=None,
                user=_SAFETY_APP_ROLE,
                password=_SAFETY_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )
        ipv6_target = _server_psql(
            server,
            host="::1",
            user=_APP_ROLE,
            password=_APP_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert ipv6_target.stdout.strip() == _DATABASE
        ipv6_safety_target = _server_psql(
            server,
            host="::1",
            user=_SAFETY_APP_ROLE,
            password=_SAFETY_PASSWORD,
            database=_DATABASE,
            sql="SELECT current_database()",
        )
        assert ipv6_safety_target.stdout.strip() == _DATABASE
        _assert_hba_rejection(
            _server_psql(
                server,
                host="::1",
                user=_APP_ROLE,
                password=_APP_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )
        _assert_hba_rejection(
            _server_psql(
                server,
                host="::1",
                user=_SAFETY_APP_ROLE,
                password=_SAFETY_PASSWORD,
                database="postgres",
                sql="SELECT 1",
                check=False,
            )
        )

        no_temp = _client_psql(
            network,
            server,
            user=_APP_ROLE,
            password=_APP_PASSWORD,
            database=_DATABASE,
            sql="CREATE TEMP TABLE stratus_hba_escape(id integer)",
            check=False,
        )
        assert no_temp.returncode != 0
        assert "permission denied to create temporary tables" in no_temp.stderr
    finally:
        _run(["rm", "--force", server], check=False)
        _run(["network", "rm", network], check=False)
