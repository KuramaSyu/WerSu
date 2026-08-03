"""Per-process handle the scheduler hands to a :class:`BackgroundProcessABC`.

The handle is the only thing the process uses to talk to the scheduler
beyond the pull side (:meth:`BackgroundProcessABC.next_wakeup`).  It
is bound to the owning process at construction time so a process can
never accidentally wake a different one.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict


class SchedulerHandleABC(ABC):
    """Handle a process uses to ask the scheduler to wake it. Used
    to just pass the `wake_at()` functionality of the scheduler to the process
    without exposing the scheduler itself."""

    @abstractmethod
    async def wake_at(self, when: datetime, why: Dict[str, Any]) -> None:
        """Schedule the next call to :meth:`BackgroundProcessABC.run` at ``when``.

        Earlier than the currently-scheduled time, this wins.
        Later than or equal to the currently-scheduled time, this is
        a no-op.

        Args:
            when: the desired wakeup time.
            why: a free-form payload.  Must contain at minimum:

                * ``name``: short identifier (e.g. ``"DisableUser"``).
                * ``description``: human-readable prose for debugging.

                Extras are allowed and encouraged for diagnosis
                (``user_id``, ``share_id``, ``action_id`` ...).
        """