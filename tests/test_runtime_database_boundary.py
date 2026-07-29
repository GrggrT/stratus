from __future__ import annotations

from pathlib import Path

import pytest

from stratus.config import (
    Settings,
    runtime_database_group_role_name,
    runtime_database_role_name,
)
from stratus.db.session import (
    RuntimeDatabaseBoundaryError,
    _assert_runtime_connection,
    create_engine,
)


class _Cursor:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row
        self.statement: str | None = None
        self.closed = False

    def execute(self, statement: str) -> object:
        self.statement = statement
        return None

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


def _write_password(path: Path) -> None:
    path.write_text("runtime-password-for-tests-00000000\n", encoding="utf-8")


def test_runtime_url_uses_database_scoped_role_and_secret_file(tmp_path: Path) -> None:
    password_file = tmp_path / "runtime-password"
    _write_password(password_file)
    settings = Settings(
        _env_file=None,
        stratus_runtime_password_file=password_file,
        stratus_database_host="db.internal",
        stratus_database_name="stratus_test",
    )

    resolved = settings.resolved_database_url()

    assert runtime_database_role_name("stratus_test") in resolved
    assert runtime_database_role_name("stratus") not in resolved
    assert "@db.internal:5432/stratus_test" in resolved


def test_runtime_role_names_are_isolated_by_database() -> None:
    assert runtime_database_role_name("stratus") != runtime_database_role_name(
        "stratus_test"
    )
    assert runtime_database_group_role_name(
        "stratus"
    ) != runtime_database_group_role_name("stratus_test")
    assert runtime_database_role_name("stratus").startswith("stratus_app_")
    assert runtime_database_group_role_name("stratus").startswith(
        "stratus_runtime_"
    )


def test_generic_database_url_environment_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://stratus:owner@localhost:5432/stratus",
    )
    monkeypatch.delenv("STRATUS_RUNTIME_DATABASE_URL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.database_url == ""


def test_runtime_engine_rejects_owner_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STRATUS_TEST_DATABASE_URL", raising=False)
    settings = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://stratus:owner@localhost:5432/stratus",
    )

    with pytest.raises(
        RuntimeDatabaseBoundaryError,
        match="database-scoped stratus_app role",
    ):
        create_engine(settings)


def test_test_database_environment_cannot_bypass_runtime_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_url = "postgresql+asyncpg://stratus:owner@localhost:5432/stratus_test"
    monkeypatch.setenv("STRATUS_TEST_DATABASE_URL", owner_url)
    settings = Settings(_env_file=None, database_url=owner_url)

    with pytest.raises(
        RuntimeDatabaseBoundaryError,
        match="database-scoped stratus_app role",
    ):
        create_engine(settings)


def test_physical_connection_boundary_is_fail_closed() -> None:
    accepted = _Cursor((True,))
    _assert_runtime_connection(_Connection(accepted), object())
    assert accepted.statement == "SELECT public.assert_stratus_runtime_boundary(true)"
    assert accepted.closed is True

    rejected = _Cursor((False,))
    with pytest.raises(RuntimeDatabaseBoundaryError, match="rejected"):
        _assert_runtime_connection(_Connection(rejected), object())
    assert rejected.closed is True
