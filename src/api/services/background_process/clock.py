"""Clock seam over :mod:`asyncio`.

The scheduler depends on this ABC instead of :class:`asyncio.Event`
and :func:`asyncio.sleep` so the loop is testable without a real
running event loop.  In production the impl wraps the real
primitives; in tests a manual-clock fake advances time on demand.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class AsyncClockABC(ABC):
    """Async wait / flag primitive the scheduler depends on."""

    @abstractmethod
    async def sleep(self, seconds: float) -> None:
        """Sleep for ``seconds``. Returns early if :meth:`set` is called."""

    @abstractmethod
    async def wait(self) -> None:
        """Block until :meth:`set` is called."""

    @abstractmethod
    def set(self) -> None:
        """Wake every sleeper and waiter."""

    @abstractmethod
    def clear(self) -> None:
        """Reset the flag so subsequent :meth:`wait` calls block again."""

    @abstractmethod
    def is_set(self) -> bool:
        """Whether the flag is currently raised."""