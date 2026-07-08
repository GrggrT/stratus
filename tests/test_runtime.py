import asyncio

from stratus import runtime
from stratus.config import Settings


async def test_probability_pipeline_is_serialized(monkeypatch) -> None:
    active = 0
    max_active = 0
    events: list[str] = []

    async def fake_recompute(_settings: Settings, _sessions=None) -> tuple[int, int]:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        events.append("recompute")
        await asyncio.sleep(0.01)
        return 1, 0

    async def fake_generate(_settings: Settings, _sessions=None) -> tuple[int, int]:
        nonlocal active
        events.append("signals")
        active -= 1
        return 0, 0

    monkeypatch.setattr(runtime, "recompute_probs", fake_recompute)
    monkeypatch.setattr(runtime, "generate_signals", fake_generate)

    await asyncio.gather(
        runtime._probability_pipeline(Settings()),
        runtime._probability_pipeline(Settings()),
    )

    assert max_active == 1
    assert events == ["recompute", "signals", "recompute", "signals"]


async def test_startup_refresh_failure_does_not_escape(monkeypatch) -> None:
    async def fail_refresh(_settings: Settings, _sessions=None) -> None:
        raise RuntimeError("incomplete source data")

    monkeypatch.setattr(runtime, "startup_refresh", fail_refresh)

    await runtime.safe_startup_refresh(Settings())
