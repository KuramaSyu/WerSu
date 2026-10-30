from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from src.api.services.background_process.background_process import BackgroundProcessABC
from src.api.services.background_process.clock import AsyncClockABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.background_process.task import TaskHandleABC, TaskSpawnerABC


class ManualClock(AsyncClockABC):
    """Sync-driven test clock.  No threading, no asyncio.Event.

    A single boolean flag plus a list of parked waiters (asyncio
    futures) is the entire state.  :meth:`set` flips the flag and
    resolves every parked future synchronously; :meth:`clear` flips
    it back.  :meth:`wait` parks the calling coroutine on a fresh
    future; :meth:`sleep` is a one-shot wait the test advances.

    Contract:

    * :meth:`set` is sticky -- subsequent :meth:`wait` returns
      immediately until :meth:`clear` is called.
    * :meth:`wait` and :meth:`sleep` do **not** clear.  The scheduler
      (or the test) must call :meth:`clear` after ``wait()`` returns
      so the next ``wait()`` blocks again.
    * :meth:`advance` is the test's "time passed" hook: it moves
      :attr:`now` forward and calls :meth:`set`.  The scheduler's
      wait returns; the scheduler clears the flag before the next
      wait.
    """

    def __init__(self, start: Optional[datetime] = None) -> None:
        self._now: datetime = start or datetime(2026, 1, 1, tzinfo=timezone.utc)
        self._set: bool = False
        self._waiters: List[asyncio.Future[None]] = []

    # -- AsyncClockABC ---------------------------------------------------

    async def sleep(self, seconds: float) -> None:
        """Block until :meth:`set` is called.  Caller must :meth:`clear`."""
        await self._park()

    async def wait(self) -> None:
        """Block until :meth:`set` is called.  Caller must :meth:`clear`."""
        await self._park()

    async def _park(self) -> None:
        """Park on a fresh future; resolve it when the flag is set."""
        if self._set:
            return
        loop = asyncio.get_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        self._waiters.append(fut)
        try:
            await fut
        finally:
            if fut in self._waiters:
                self._waiters.remove(fut)

    def set(self) -> None:
        """Flip the flag and wake every parked waiter."""
        self._set = True
        for fut in list(self._waiters):
            if not fut.done():
                fut.set_result(None)
        self._waiters.clear()

    def clear(self) -> None:
        """Reset the flag so subsequent :meth:`wait` calls block again."""
        self._set = False

    def is_set(self) -> bool:
        return self._set

    # -- test helpers ----------------------------------------------------

    def set_now(self, when: datetime) -> None:
        """Move the clock's idea of ``now`` to ``when``.

        Does not wake sleepers; pair with :meth:`set` if a sleep
        task should resume.
        """
        self._now = when

    def advance(self, seconds: float) -> None:
        """``set_now(now + seconds)`` followed by :meth:`set`."""
        self._now = self._now + timedelta(seconds=seconds)
        self.set()

    @property
    def now(self) -> datetime:
        """Current clock value (for tests asserting on what the scheduler saw)."""
        return self._now


class InMemoryTaskHandle(TaskHandleABC):
    """Wraps an :class:`asyncio.Task`."""

    def __init__(self, task: asyncio.Task[Any]) -> None:
        self._task = task

    async def join(self) -> None:
        await self._task

    def cancel(self) -> None:
        if not self._task.done():
            self._task.cancel()

    def is_cancelled(self) -> bool:
        return self._task.cancelled()


class InMemoryTaskSpawner(TaskSpawnerABC):
    """Spawner that wraps :func:`asyncio.create_task`."""

    def spawn(self, coro: Coroutine[Any, Any, None]) -> TaskHandleABC:
        return InMemoryTaskHandle(asyncio.create_task(coro))


__all__ = [
    'ManualClock',
    'InMemoryTaskHandle',
    'InMemoryTaskSpawner',
]
