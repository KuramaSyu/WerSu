"""Recalculation of the ``calculated_stats`` table.

The process runs once at boot (catch-up) and then once per
``interval`` (default: one day) to refresh every human user.  Between
those sweeps it can be woken early by :meth:`notify_user_changed`,
which records the dirty user ids and asks the scheduler to run the
process as soon as possible.  A wakeup run recalculates only the
users that were marked dirty; a scheduled sweep recalculates
everyone.

The :class:`CalculatedStatsNoteCreatedListener` /
:class:`CalculatedStatsNoteUpdatedListener` /
:class:`CalculatedStatsDirectoryCreatedListener` /
:class:`CalculatedStatsDirectoryUpdatedListener` glue the domain
event bus to :meth:`notify_user_changed`, so note and directory
writes invalidate the acting user's stats without a separate
dependency from the note/directory services onto the scheduler.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Optional

from src.api.events.event_context import EventContext
from src.api.events.events import (
    DirectoryCreated,
    DirectoryUpdated,
    NoteCreated,
    NoteUpdated,
)
from src.api.events.listener import Listener
from src.api.other.types import LoggingProvider
from src.api.services.background_process.background_process import (
    BackgroundProcessABC,
)
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.calculated_stats_service import CalculatedStatsServiceABC
from src.db.entities.calculated_stats import CalculatedStatsFilter


class CalculatedStatsProcessImpl(BackgroundProcessABC):
    """Refresh precomputed stats, on a schedule and on demand.

    Args:
        stats_service: service that owns the recalculation logic.
        get_now: clock used for scheduling; injected so tests can pin
            time instead of sleeping.
        log: logging-provider factory.
        interval: delay between full sweeps.  Defaults to one day.
    """

    def __init__(
        self,
        stats_service: CalculatedStatsServiceABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
        *,
        interval: timedelta = timedelta(days=1),
    ) -> None:
        self._stats_service = stats_service
        self._get_now = get_now
        self.log = log(__name__, self)
        self._interval = interval
        self._handle: Optional[SchedulerHandleABC] = None
        self._first_wakeup = True
        self._dirty_user_ids: set[str] = set()

    def attach_handle(self, handle: SchedulerHandleABC) -> None:
        self._handle = handle

    async def next_wakeup(self) -> Optional[datetime]:
        """Run immediately after boot, then once per ``interval``."""
        now = self._get_now()
        if self._first_wakeup:
            self._first_wakeup = False
            return now
        return now + self._interval

    async def notify_user_changed(self, user_id: str) -> None:
        """Mark ``user_id``'s stats dirty and schedule an early run.

        Called from the domain-event listeners whenever a note or
        directory the user touched changes.  The id is remembered
        until :meth:`run` consumes it, so several events arriving
        before the next run collapse into one recalculation.

        This method must never raise into the caller: a scheduler
        failure leaves the id queued and the periodic sweep still
        picks it up.
        """
        if not user_id:
            return
        self._dirty_user_ids.add(str(user_id))
        if self._handle is None:
            # Handle not bound yet (writes during boot): the first
            # ``run`` will still see the queued id.
            return
        try:
            await self._handle.wake_at(
                self._get_now(),
                {
                    "name": type(self).__name__,
                    "description": (
                        f"note/directory changed for user_id={user_id}"
                    ),
                    "user_id": str(user_id),
                },
            )
        except Exception as exc:  # noqa: BLE001 -- never break the writer
            self.log.exception(
                f"CalculatedStatsProcess: wake_at failed for "
                f"user_id={user_id}: {exc!r}"
            )

    async def run(self) -> None:
        """Recalculate the dirty users, or everyone on a scheduled sweep."""
        dirty = sorted(self._dirty_user_ids)
        self._dirty_user_ids.clear()

        try:
            if dirty:
                self.log.info(
                    f"CalculatedStatsProcess: recalculating {len(dirty)} "
                    f"dirty user(s)"
                )
                rows = await self._stats_service.recalculate(
                    CalculatedStatsFilter(user_ids=dirty)
                )
            else:
                self.log.info(
                    "CalculatedStatsProcess: starting full recalculation"
                )
                rows = await self._stats_service.recalculate()
        except Exception as exc:  # noqa: BLE001
            # Put the ids back so the next run retries them instead of
            # waiting for the daily sweep.
            self._dirty_user_ids.update(dirty)
            self.log.exception(
                f"CalculatedStatsProcess: recalculation failed: {exc!r}"
            )
            return
        self.log.info(
            f"CalculatedStatsProcess: recalculated {len(rows)} user(s)"
        )


class _CalculatedStatsUserChangeListener:
    """Mixin that forwards a domain event's actor to the process."""

    def __init__(self, process: CalculatedStatsProcessImpl) -> None:
        self._process = process

    async def _notify(self, actor_id: Optional[str]) -> None:
        await self._process.notify_user_changed(actor_id or "")


class CalculatedStatsNoteCreatedListener(
    _CalculatedStatsUserChangeListener, Listener[NoteCreated]
):
    """Invalidate the actor's stats when a note is created."""

    async def on_event(self, event: NoteCreated, ctx: EventContext) -> None:
        await self._notify(event.actor_id)


class CalculatedStatsNoteUpdatedListener(
    _CalculatedStatsUserChangeListener, Listener[NoteUpdated]
):
    """Invalidate the actor's stats when a note is edited."""

    async def on_event(self, event: NoteUpdated, ctx: EventContext) -> None:
        await self._notify(event.actor_id)


class CalculatedStatsDirectoryCreatedListener(
    _CalculatedStatsUserChangeListener, Listener[DirectoryCreated]
):
    """Invalidate the actor's stats when a directory is created."""

    async def on_event(self, event: DirectoryCreated, ctx: EventContext) -> None:
        await self._notify(event.actor_id)


class CalculatedStatsDirectoryUpdatedListener(
    _CalculatedStatsUserChangeListener, Listener[DirectoryUpdated]
):
    """Invalidate the actor's stats when a directory is edited."""

    async def on_event(self, event: DirectoryUpdated, ctx: EventContext) -> None:
        await self._notify(event.actor_id)


def stats_invalidation_listeners(
    process: CalculatedStatsProcessImpl,
) -> Iterable[Listener[Any]]:
    """Build one listener per supported event for ``process``."""
    return (
        CalculatedStatsNoteCreatedListener(process),
        CalculatedStatsNoteUpdatedListener(process),
        CalculatedStatsDirectoryCreatedListener(process),
        # CalculatedStatsDirectoryUpdatedListener(process),
    )


__all__ = [
    "CalculatedStatsProcessImpl",
    "CalculatedStatsNoteCreatedListener",
    "CalculatedStatsNoteUpdatedListener",
    "CalculatedStatsDirectoryCreatedListener",
    "CalculatedStatsDirectoryUpdatedListener",
    "stats_invalidation_listeners",
]
