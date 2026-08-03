"""Production :class:`AsyncClockABC` over :mod:`asyncio`."""

from __future__ import annotations

import asyncio
from typing import List

from src.api.services.background_process.clock import AsyncClockABC


class AsyncClockAsyncio(AsyncClockABC):
    """Wraps :class:`asyncio.Event` and :func:`asyncio.sleep`.

    :meth:`sleep` is interruptible: when :meth:`set` is called while a
    sleeper is parked, that sleeper is woken immediately and returns
    early.  Pending sleepers track their own deadline so the wakeup
    is precise even if multiple sleepers are queued.
    """

    def __init__(self) -> None:
        self._flag = asyncio.Event()
        self._sleepers: List[asyncio.Future[None]] = []

    async def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds`` (interruptible by :meth:`set`). To enable 
        the interrupt-logic we can't use ``asyncio.sleep`` directly"""
        loop = asyncio.get_running_loop()
        if seconds > 0:
            fut: asyncio.Future[None] = loop.create_future()
            self._sleepers.append(fut)
            loop.call_later(seconds, fut.set_result, None)
            try:
                await fut
            finally:
                if fut in self._sleepers:
                    self._sleepers.remove(fut)
        else:
            # Zero/negative delay: still yield once so the loop can be
            # interrupted by ``set()``.  Without this the scheduler
            # would spin on a never-pausing sleep when its next wakeup
            # is already in the past.
            await asyncio.sleep(0)

    async def wait(self) -> None:
        """Block until :meth:`set` is called; auto-arms for the next call."""
        await self._flag.wait()
        self._flag.clear()

    def set(self) -> None:
        """early await each sleeper ``sleep()`` and await direct waiters ``wait()``"""
        self._flag.set()
        for sleeper in list(self._sleepers):
            if not sleeper.done():
                sleeper.set_result(None)

    def clear(self) -> None:
        """Reset the flag so subsequent :meth:`wait` calls block again."""
        self._flag.clear()

    def is_set(self) -> bool:
        """Whether the flag is currently set."""
        return self._flag.is_set()