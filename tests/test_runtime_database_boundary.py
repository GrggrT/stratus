from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine

from stratus.config import (
    MigrationSettings,
    Settings,
    runtime_database_group_role_name,
    runtime_database_role_name,
)
from stratus.db import session as database_session
from stratus.db.session import (
    RuntimeDatabaseBoundaryError,
    _assert_runtime_checkout,
    _assert_runtime_connection,
    _runtime_server_settings,
    create_engine,
)


class _Cursor:
    def __init__(
        self,
        row: tuple[object, ...] | None,
        *,
        execute_error: Exception | None = None,
        fetch_error: Exception | None = None,
    ) -> None:
        self.row = row
        self.execute_error = execute_error
        self.fetch_error = fetch_error
        self.statement: str | None = None
        self.closed = False
        self.connection: _Connection | None = None
        self.autocommit_at_execute: bool | None = None

    def execute(self, statement: str) -> object:
        self.statement = statement
        assert self.connection is not None
        self.autocommit_at_execute = self.connection.autocommit
        if self.execute_error is not None:
            raise self.execute_error
        return None

    def fetchone(self) -> tuple[object, ...] | None:
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.row

    def close(self) -> None:
        self.closed = True


class _Connection:
    def __init__(self, cursor: _Cursor, *, autocommit: bool = False) -> None:
        self._cursor = cursor
        self._autocommit = autocommit
        self.autocommit_history: list[bool] = []
        cursor.connection = self

    @property
    def autocommit(self) -> bool:
        return self._autocommit

    @autocommit.setter
    def autocommit(self, value: bool) -> None:
        self.autocommit_history.append(value)
        self._autocommit = value

    def cursor(self) -> _Cursor:
        return self._cursor


class _ConnectionRecord:
    def __init__(self) -> None:
        self.invalidated_with: BaseException | None = None

    def invalidate(
        self,
        exception: BaseException | None = None,
        soft: bool = False,
    ) -> None:
        assert soft is False
        self.invalidated_with = exception


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
    assert runtime_database_role_name("stratus") != runtime_database_role_name("stratus_test")
    assert runtime_database_group_role_name("stratus") != runtime_database_group_role_name(
        "stratus_test"
    )
    assert runtime_database_role_name("stratus").startswith("stratus_app_")
    assert runtime_database_group_role_name("stratus").startswith("stratus_runtime_")


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
    accepted_connection = _Connection(accepted)
    _assert_runtime_connection(accepted_connection, object())
    assert accepted.statement == "SELECT public.assert_stratus_runtime_boundary(true)"
    assert accepted.autocommit_at_execute is True
    assert accepted.closed is True
    assert accepted_connection.autocommit is False
    assert accepted_connection.autocommit_history == [True, False]

    rejected = _Cursor((False,))
    rejected_connection = _Connection(rejected)
    with pytest.raises(RuntimeDatabaseBoundaryError, match="rejected"):
        _assert_runtime_connection(rejected_connection, object())
    assert rejected.autocommit_at_execute is True
    assert rejected.closed is True
    assert rejected_connection.autocommit is False
    assert rejected_connection.autocommit_history == [True, False]


@pytest.mark.parametrize(
    ("cursor", "expected_error"),
    [
        (_Cursor(None), RuntimeDatabaseBoundaryError),
        (_Cursor((True, False)), RuntimeDatabaseBoundaryError),
        (_Cursor(None, execute_error=RuntimeError("execute failed")), RuntimeError),
        (_Cursor(None, fetch_error=RuntimeError("fetch failed")), RuntimeError),
    ],
)
def test_runtime_boundary_restores_autocommit_on_every_failure(
    cursor: _Cursor,
    expected_error: type[BaseException],
) -> None:
    connection = _Connection(cursor, autocommit=False)

    with pytest.raises(expected_error):
        _assert_runtime_connection(connection, object())

    assert cursor.closed is True
    assert connection.autocommit is False
    assert connection.autocommit_history == [True, False]


def test_every_runtime_checkout_rechecks_and_invalidates_drift() -> None:
    accepted = _Cursor((True,))
    accepted_record = _ConnectionRecord()
    accepted_connection = _Connection(accepted)
    _assert_runtime_checkout(accepted_connection, accepted_record, object())
    assert accepted_record.invalidated_with is None
    assert accepted_connection.autocommit_history == [True, False]

    rejected = _Cursor((False,))
    rejected_record = _ConnectionRecord()
    rejected_connection = _Connection(rejected)
    with pytest.raises(RuntimeDatabaseBoundaryError, match="rejected") as caught:
        _assert_runtime_checkout(rejected_connection, rejected_record, object())

    assert rejected.closed is True
    assert rejected_connection.autocommit_history == [True, False]
    assert rejected_record.invalidated_with is caught.value


def test_runtime_checkout_normalizes_driver_failure_after_invalidation() -> None:
    driver_error = RuntimeError("driver leaked catalog assertion detail")
    rejected = _Cursor(None, execute_error=driver_error)
    rejected_record = _ConnectionRecord()
    rejected_connection = _Connection(rejected)

    with pytest.raises(
        RuntimeDatabaseBoundaryError,
        match="runtime database boundary assertion failed",
    ) as caught:
        _assert_runtime_checkout(rejected_connection, rejected_record, object())

    assert rejected.closed is True
    assert rejected_connection.autocommit_history == [True, False]
    assert rejected_record.invalidated_with is driver_error
    assert caught.value.__cause__ is driver_error
    assert "catalog assertion detail" not in str(caught.value)


def test_runtime_database_defaults_preserve_long_worker_queries() -> None:
    settings = Settings(_env_file=None)

    assert settings.stratus_database_application_name == "stratus-runtime"
    assert settings.stratus_database_statement_timeout_ms == 0
    assert settings.stratus_database_lock_timeout_ms == 0
    assert settings.stratus_database_idle_transaction_timeout_ms == 0
    assert settings.stratus_database_pool_size == 5
    assert settings.stratus_database_max_overflow == 10
    assert settings.stratus_database_pool_timeout_seconds == 30.0
    assert _runtime_server_settings(settings) == {
        "application_name": "stratus-runtime",
        "statement_timeout": "0ms",
        "lock_timeout": "0ms",
        "idle_in_transaction_session_timeout": "0ms",
    }
    assert _runtime_server_settings(settings, read_only=True) == {
        "application_name": "stratus-runtime",
        "statement_timeout": "0ms",
        "lock_timeout": "0ms",
        "idle_in_transaction_session_timeout": "0ms",
        "default_transaction_read_only": "on",
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("stratus_database_statement_timeout_ms", -1),
        ("stratus_database_lock_timeout_ms", -1),
        ("stratus_database_idle_transaction_timeout_ms", -1),
        ("stratus_database_pool_size", 0),
        ("stratus_database_max_overflow", -1),
        ("stratus_database_pool_timeout_seconds", 0),
    ],
)
def test_runtime_database_timeout_configuration_fails_closed(
    field: str,
    value: int,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_runtime_engine_installs_server_side_timeout_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_name = "stratus"
    role = runtime_database_role_name(database_name)
    settings = Settings(
        _env_file=None,
        database_url=(f"postgresql+asyncpg://{role}:runtime-password@localhost/{database_name}"),
        stratus_database_statement_timeout_ms=2_400,
        stratus_database_lock_timeout_ms=800,
        stratus_database_idle_transaction_timeout_ms=4_500,
        stratus_database_application_name="stratus-api",
        stratus_database_pool_size=4,
        stratus_database_max_overflow=0,
        stratus_database_pool_timeout_seconds=0.25,
    )
    captured: dict[str, Any] = {}
    sync_engine = object()

    class _Engine:
        def __init__(self) -> None:
            self.sync_engine = sync_engine

    fake_engine = _Engine()

    def fake_create_async_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
        captured["database_url"] = database_url
        captured.update(kwargs)
        return cast(AsyncEngine, fake_engine)

    def fake_listen(target: object, name: str, listener: object) -> None:
        captured.setdefault("listeners", []).append((target, name, listener))

    monkeypatch.setattr(
        database_session,
        "create_async_engine",
        fake_create_async_engine,
    )
    monkeypatch.setattr(database_session.event, "listen", fake_listen)

    engine = create_engine(settings)

    assert engine is fake_engine
    assert captured["database_url"] == settings.database_url
    assert captured["pool_pre_ping"] is True
    assert captured["pool_size"] == 4
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 0.25
    assert captured["connect_args"] == {
        "server_settings": {
            "application_name": "stratus-api",
            "statement_timeout": "2400ms",
            "lock_timeout": "800ms",
            "idle_in_transaction_session_timeout": "4500ms",
        }
    }
    assert captured["listeners"] == [
        (sync_engine, "connect", _assert_runtime_connection),
        (sync_engine, "checkout", _assert_runtime_checkout),
    ]

    captured.clear()
    read_engine = create_engine(
        settings,
        pool_size=3,
        max_overflow=0,
        read_only=True,
    )
    assert read_engine is fake_engine
    assert captured["listeners"] == [
        (sync_engine, "connect", _assert_runtime_connection),
        (sync_engine, "checkout", _assert_runtime_checkout),
    ]
    assert captured["pool_size"] == 3
    assert captured["max_overflow"] == 0
    assert captured["connect_args"]["server_settings"]["default_transaction_read_only"] == "on"


def test_migration_settings_remain_separate_from_runtime_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "MIGRATION_DATABASE_URL",
        "postgresql+asyncpg://owner:secret@localhost/stratus",
    )
    monkeypatch.setenv("STRATUS_DATABASE_STATEMENT_TIMEOUT_MS", "200")

    migration_settings = MigrationSettings(_env_file=None)

    assert migration_settings.model_dump() == {
        "migration_database_url": ("postgresql+asyncpg://owner:secret@localhost/stratus")
    }


def test_compose_applies_strict_database_budget_only_to_api() -> None:
    compose = (Path(__file__).parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
    worker = compose.split("\n  worker:\n", maxsplit=1)[1].split(
        "\n  binance-market-stream:\n",
        maxsplit=1,
    )[0]
    api = compose.split("\n  api:\n", maxsplit=1)[1].split(
        "\n  backup:\n",
        maxsplit=1,
    )[0]

    assert "STRATUS_DATABASE_STATEMENT_TIMEOUT_MS:" not in worker
    assert "STRATUS_DATABASE_APPLICATION_NAME: stratus-api" in api
    assert 'STRATUS_DATABASE_STATEMENT_TIMEOUT_MS: "2500"' in api
    assert 'STRATUS_DATABASE_LOCK_TIMEOUT_MS: "1000"' in api
    assert 'STRATUS_DATABASE_IDLE_TRANSACTION_TIMEOUT_MS: "5000"' in api
    assert 'STRATUS_DATABASE_POOL_SIZE: "4"' in api
    assert 'STRATUS_DATABASE_MAX_OVERFLOW: "0"' in api
    assert 'STRATUS_DATABASE_POOL_TIMEOUT_SECONDS: "0.25"' in api
    assert "STRATUS_API_DATABASE_" not in api
