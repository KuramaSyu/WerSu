"""Scheduler contract.

The scheduler owns a min-heap of (wakeup_at, process) entries, drives
the loop, and answers introspectable questions via
:meth:`list_events`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, Optional

from src.api.services.background_process.background_process import BackgroundProcessABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC


@dataclass
class ScheduledEvent:
    """One entry in the scheduler's introspection map.

    Attributes:
        process: the registered process that will be called.
        why: the most recent `why` payload the process handed to
            :meth:`SchedulerHandleABC.wake_at`.
    """

    process: BackgroundProcessABC
    why: Dict[str, Any]


class BackgroundSchedulerABC(ABC):
    """Drives a set of :class:`BackgroundProcessABC` instances. Used as main scheduler for processes
    like enabling/disabling users or cleaning attachments"""

    @abstractmethod
    def register(
        self,
        process: BackgroundProcessABC,
        *,
        on_handle: Optional[Callable[[SchedulerHandleABC], None]] = None,
    ) -> None:
        """Add a process. Idempotent; re-registering replaces the entry.

        Args:
            process: the background process to register.
            on_handle: optional callback invoked with the
                :class:`SchedulerHandleABC` once the scheduler binds
                it to ``process`` in :meth:`attach_handles`.  Lets
                external code (e.g. a notifying repo) capture the
                handle without reaching into the process's private
                state.  Called exactly once per registration.
        """

    @abstractmethod
    def unregister(self, process: BackgroundProcessABC) -> None:
        """Remove a process. No-op if it was not registered."""

    @abstractmethod
    def attach_handles(self) -> None:
        """Bind a :class:`SchedulerHandleABC` to every registered process.

        Must be called before :meth:`start`. Calling it twice raises.
        """

    @abstractmethod
    def start(self) -> None:
        """Spawn the loop task.  Returns immediately.

        Runs every registered process once at boot to seed
        :meth:`BackgroundProcessABC.next_wakeup`.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Signal stop, cancel any in-flight sleep, await the loop task."""

    @abstractmethod
    async def list_events(self) -> Dict[datetime, ScheduledEvent]:
        """Snapshot of every currently-scheduled wakeup.

        Keyed by the wakeup time.  ``list_events`` is mostly for
        debug endpoints and operator tooling; it does not affect
        the loop.
        """