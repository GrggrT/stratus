from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass

from stratus.backtesting.engine import (
    BacktestConfig,
    BacktestResult,
    BacktestStrategy,
    BinanceBacktestEngine,
    PreparedMarketEvent,
)
from stratus.backtesting.events import MarketEvent, canonical_market_event


@dataclass(frozen=True, slots=True)
class BacktestMatrixEntry:
    key: str
    config: BacktestConfig
    strategy_factory: Callable[[], BacktestStrategy]


async def run_backtest_matrix(
    entries: tuple[BacktestMatrixEntry, ...],
    events: AsyncIterable[MarketEvent],
    *,
    queue_size: int = 1,
) -> dict[str, BacktestResult]:
    if not entries:
        raise ValueError("backtest matrix must contain at least one entry")
    keys = [entry.key for entry in entries]
    if any(not key for key in keys) or len(set(keys)) != len(keys):
        raise ValueError("backtest matrix keys must be non-empty and unique")
    if not 1 <= queue_size <= 100:
        raise ValueError("backtest matrix queue_size must be in [1, 100]")

    queues = {
        entry.key: asyncio.Queue[PreparedMarketEvent | _End](maxsize=queue_size)
        for entry in entries
    }
    tasks: dict[str, asyncio.Task[BacktestResult]] = {}
    try:
        async with asyncio.TaskGroup() as group:
            for entry in entries:
                tasks[entry.key] = group.create_task(
                    _run_entry(entry, queues[entry.key]),
                    name=f"backtest-matrix:{entry.key}",
                )
            group.create_task(
                _broadcast(events, tuple(queues.values())),
                name="backtest-matrix:broadcast",
            )
    except BaseExceptionGroup as group:
        leaves = _exception_leaves(group)
        if len(leaves) == 1:
            raise leaves[0] from None
        raise
    return {key: task.result() for key, task in tasks.items()}


async def _run_entry(
    entry: BacktestMatrixEntry,
    queue: asyncio.Queue[PreparedMarketEvent | _End],
) -> BacktestResult:
    return await BinanceBacktestEngine(entry.config, entry.strategy_factory()).run(
        _queue_events(queue)
    )


async def _queue_events(
    queue: asyncio.Queue[PreparedMarketEvent | _End],
) -> AsyncIterator[PreparedMarketEvent]:
    while True:
        value = await queue.get()
        if isinstance(value, _End):
            return
        yield value


async def _broadcast(
    events: AsyncIterable[MarketEvent],
    queues: tuple[asyncio.Queue[PreparedMarketEvent | _End], ...],
) -> None:
    async for event in events:
        prepared = PreparedMarketEvent(
            event=event,
            canonical_bytes=canonical_market_event(event),
        )
        for queue in queues:
            await queue.put(prepared)
    for queue in queues:
        await queue.put(_End())


@dataclass(frozen=True, slots=True)
class _End:
    pass


def _exception_leaves(error: BaseException) -> tuple[BaseException, ...]:
    if isinstance(error, BaseExceptionGroup):
        return tuple(leaf for child in error.exceptions for leaf in _exception_leaves(child))
    return (error,)
