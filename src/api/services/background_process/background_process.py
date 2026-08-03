"""Background process contract.

A :class:`BackgroundProcessABC` is an observer-style participant: the
scheduler asks it when it wants to be called next and calls
:meth:`run` at that time.  The process can also push an earlier
wakeup via its :class:`SchedulerHandleABC` when something external
changes its mind.

Specialisations (e.g. :class:`~src.api.services.background_process.user_action_process.UserActionProcessABC`)
narrow this contract to a single kind of side-effect so the
`name` of the `why` payload is statically known.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from src.api.services.background_process.scheduler_handle import SchedulerHandleABC


class BackgroundProcessABC(ABC):
    """A unit of work the scheduler runs at scheduled times."""

    @abstractmethod
    async def run(self) -> None:
        """Do one unit of work.  Must be idempotent."""

    @abstractmethod
    async def next_wakeup(self) -> Optional[datetime]:
        """Return when this process wants to be called next.

        ``None`` means: do not call me again until I push a
        wakeup via the handle.
        """

    @abstractmethod
    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        """Receive the scheduler's handle for this process.

        Called exactly once, by the scheduler, before
        :meth:`run` or :meth:`next_wakeup` is ever called.
        """