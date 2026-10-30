from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from src.api.services.background_process.background_process import BackgroundProcessABC
from src.api.services.background_process.clock import AsyncClockABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.background_process.task import TaskHandleABC, TaskSpawnerABC


class FakeSpyBackgroundProcess(BackgroundProcessABC):
    """Background process that records every call.

    Test code sets :attr:`next_wakeup_values` (a list) to control what
    :meth:`next_wakeup` returns on each successive call.  Once the
    list is exhausted, ``None`` is returned (no more wakeups).  For
    one-shot use, set ``next_wakeup_values = [datetime]``.

    Use :attr:`run_side_effect` to run custom logic inside
    :meth:`run`.  :attr:`run_calls` and :attr:`wake_at_calls` record
    every interaction.
    """

    def __init__(self, name: str = "FakeSpyBackgroundProcess") -> None:
        self.name = name
        self.next_wakeup_values: List[Optional[datetime]] = []
        self.run_side_effect: Optional[Callable[[], Any]] = None
        self.run_calls: int = 0
        self.wake_at_calls: List[tuple[datetime, Dict[str, Any]]] = []
        self._handle: Optional[SchedulerHandleABC] = None

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        if not self.next_wakeup_values:
            return None
        return self.next_wakeup_values.pop(0)

    async def run(self) -> None:
        self.run_calls += 1
        if self.run_side_effect is not None:
            result = self.run_side_effect()
            if asyncio.iscoroutine(result):
                await result

    async def wake_at(self, when: datetime, why: Dict[str, Any]) -> None:
        """Direct call to the handle (bypasses the scheduler)."""
        self.wake_at_calls.append((when, why))
        if self._handle is not None:
            await self._handle.wake_at(when, why)


__all__ = [
    'FakeSpyBackgroundProcess',
]
