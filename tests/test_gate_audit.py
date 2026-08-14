from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import stratus.jobs.audit_gate as audit_gate_module
from stratus.jobs.audit_gate import _halt_after_repeated_gate_failure, due_checkpoint


def test_gate_checkpoint_schedule() -> None:
    assert due_checkpoint(29, set()) is None
    assert due_checkpoint(30, set()) == 30
    assert due_checkpoint(60, set()) == 30
    assert due_checkpoint(60, {30}) == 60
    assert due_checkpoint(90, {30, 60}) is None


@pytest.mark.asyncio
async def test_repeated_gate_failure_uses_audited_stop_before_halt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []
    session = AsyncMock()

    async def stop(*args: object, **kwargs: object) -> int:
        calls.append(("stop", args, kwargs))
        return 0

    async def execute(statement: object) -> None:
        calls.append(("halt", statement))

    monkeypatch.setattr(audit_gate_module, "set_kill_switch", stop)
    session.execute.side_effect = execute
    now = datetime(2026, 8, 14, tzinfo=UTC)
    await _halt_after_repeated_gate_failure(
        session,
        reason="validation gate failed at day 30 and day 60",
        now=now,
    )

    assert [call[0] for call in calls] == ["stop", "halt"]
    assert calls[0][2] == {
        "actor": "stratus-audit-gate",
        "reason": "validation gate failed at day 30 and day 60",
        "expiration_reason": "validation_gate",
    }
