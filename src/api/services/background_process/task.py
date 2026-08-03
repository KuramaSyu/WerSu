"""Task spawner seam over :mod:`asyncio`.

The scheduler uses this to spawn the loop body and the per-cycle
sleep task.  The sleep task is its own handle so the scheduler can
cancel it on :meth:`stop` and on a shorter :meth:`wake_at`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Coroutine


class TaskHandleABC(ABC):
    """Opaque handle to a coroutine that has been scheduled.
    Used to abstract away an ``asyncio.Task`` so that tests can be implemented easier.

    Returned by :meth:`TaskSpawnerABC.spawn`.  Used by the scheduler
    to cancel a running coroutine (e.g. an in-flight sleep task) and
    to await its completion (e.g. on :meth:`stop`).
    """

    @abstractmethod
    async def join(self) -> None:
        """Await the coroutine's completion.
        join() since this is the asyncio naming convention.

        Re-raises exceptions from the coroutine body.
        """

    @abstractmethod
    def cancel(self) -> None:
        """Request cancellation.  Idempotent."""

    @abstractmethod
    def is_cancelled(self) -> bool:
        """Whether :meth:`cancel` was called and the coroutine has stopped."""


class TaskSpawnerABC(ABC):
    """Starts a coroutine and hands back a :class:`TaskHandleABC`.

    The scheduler uses one of these to launch its loop body and the
    per-cycle sleep task.  Tests substitute an in-memory double so the
    scheduler is exercised without a real running event loop.
    """

    @abstractmethod
    def spawn(self, coro: Coroutine[Any, Any, None]) -> TaskHandleABC:
        """Schedule ``coro`` to run and return a handle to it."""