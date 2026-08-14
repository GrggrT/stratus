from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from stratus.config import Settings
from stratus.jobs import maintain_execution_cost_evidence as maintenance
from stratus.repositories.execution_cost_ledger import (
    MarkoutSealRun,
    ObligationFinalizationRun,
)


class _FakeSession:
    def __init__(self, writes: list[str]) -> None:
        self.writes = writes


class _FakeTransaction:
    def __init__(self, factory: _FakeFactory) -> None:
        self._factory = factory
        self._writes: list[str] = []

    async def __aenter__(self) -> _FakeSession:
        self._factory.events.append("begin")
        return _FakeSession(self._writes)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: Any,
    ) -> None:
        if exc_type is None:
            self._factory.durable.extend(self._writes)
            self._factory.events.append("commit")
        else:
            self._factory.events.append("rollback")


class _FakeFactory:
    def __init__(self) -> None:
        self.durable: list[str] = []
        self.events: list[str] = []

    def begin(self) -> _FakeTransaction:
        return _FakeTransaction(self)


async def test_composite_uses_one_clock_and_two_committed_phases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _FakeFactory()
    observed_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)
    clocks = 0

    async def fake_clock(_session: _FakeSession) -> datetime:
        nonlocal clocks
        clocks += 1
        return observed_at

    async def fake_markouts(
        session: _FakeSession,
        *,
        now: datetime,
    ) -> MarkoutSealRun:
        assert now == observed_at
        session.writes.append("mark")
        return MarkoutSealRun(
            obligations_claimed=1,
            marks_observed=1,
            obligation_ids=(41,),
        )

    async def fake_finalize(
        session: _FakeSession,
        *,
        now: datetime,
    ) -> ObligationFinalizationRun:
        assert now == observed_at
        session.writes.append("seal")
        return ObligationFinalizationRun(
            obligations_claimed=1,
            obligations_sealed=1,
            obligation_ids=(52,),
        )

    monkeypatch.setattr(maintenance, "execution_cost_clock_timestamp", fake_clock)
    monkeypatch.setattr(maintenance, "seal_due_execution_markouts", fake_markouts)
    monkeypatch.setattr(maintenance, "finalize_execution_cost_obligations", fake_finalize)
    log_messages: list[str] = []

    def capture_info(message: str, *args: object) -> None:
        log_messages.append(message % args)

    monkeypatch.setattr(maintenance.logger, "info", capture_info)

    result = await maintenance.maintain_execution_cost_evidence(
        Settings(_env_file=None),
        factory,  # type: ignore[arg-type]
    )

    assert clocks == 1
    assert factory.events == ["begin", "commit", "begin", "commit"]
    assert factory.durable == ["mark", "seal"]
    assert result.observed_at == observed_at
    assert len(log_messages) == 1
    assert "mark_ids=41" in log_messages[0]
    assert "finalize_ids=52" in log_messages[0]


async def test_finalizer_failure_cannot_rollback_committed_marks(monkeypatch) -> None:
    factory = _FakeFactory()
    observed_at = datetime(2026, 7, 29, 12, 0, tzinfo=UTC)

    async def fake_clock(_session: _FakeSession) -> datetime:
        return observed_at

    async def fake_markouts(
        session: _FakeSession,
        *,
        now: datetime,
    ) -> MarkoutSealRun:
        assert now == observed_at
        session.writes.append("mark")
        return MarkoutSealRun(obligations_claimed=1, marks_observed=1)

    async def failing_finalize(
        session: _FakeSession,
        *,
        now: datetime,
    ) -> ObligationFinalizationRun:
        assert now == observed_at
        session.writes.append("uncommitted-seal")
        raise RuntimeError("forced phase-b failure")

    monkeypatch.setattr(maintenance, "execution_cost_clock_timestamp", fake_clock)
    monkeypatch.setattr(maintenance, "seal_due_execution_markouts", fake_markouts)
    monkeypatch.setattr(maintenance, "finalize_execution_cost_obligations", failing_finalize)

    with pytest.raises(RuntimeError, match="forced phase-b"):
        await maintenance.maintain_execution_cost_evidence(
            Settings(_env_file=None),
            factory,  # type: ignore[arg-type]
        )

    assert factory.events == ["begin", "commit", "begin", "rollback"]
    assert factory.durable == ["mark"]
