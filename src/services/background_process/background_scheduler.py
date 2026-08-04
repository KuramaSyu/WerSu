"""Scheduler implementation.

Owns a min-heap of (wakeup_at, process) entries.  The loop:

1. Sleeps until the earliest wakeup, via a spawned sleep task the
   loop body owns.  :meth:`wake_at` (which moves an entry earlier)
   cancels the current sleep task; the loop then re-spawns one with
   the new shorter delay.
2. Calls :meth:`BackgroundProcessABC.run` on the process whose
   wakeup fired.  Run-time exceptions are logged and the loop
   continues; one bad process does not kill the scheduler.
3. Re-asks :meth:`BackgroundProcessABC.next_wakeup` and reinserts
   the entry.  ``None`` removes the process from the heap.

:meth:`start` runs every registered process once at boot so they can
do catch-up work before :meth:`next_wakeup` is asked.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.api.other.types import LoggingProvider
from src.api.services.background_process.background_process import BackgroundProcessABC
from src.api.services.background_process.background_scheduler import (
    BackgroundSchedulerABC,
    ScheduledEvent,
)
from src.api.services.background_process.clock import AsyncClockABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.api.services.background_process.task import TaskHandleABC, TaskSpawnerABC


@dataclass(order=True)
class _HeapEntry:
    when: datetime
    seq: int
    process: BackgroundProcessABC = field(compare=False)
    why: Dict[str, Any] = field(compare=False)


class _SchedulerHandleImpl(SchedulerHandleABC):
    """Per-process handle bound to its owner at construction. Used
    to just pass the `wake_at()` functionality of the scheduler to the process without exposing the scheduler itself."""

    def __init__(
        self,
        scheduler: "BackgroundSchedulerImpl",
        owner: BackgroundProcessABC,
    ) -> None:
        self._scheduler = scheduler
        self._owner = owner

    async def wake_at(self, when: datetime, why: Dict[str, Any]) -> None:
        await self._scheduler.schedule(self._owner, when, why)


class BackgroundSchedulerImpl(BackgroundSchedulerABC):
    """Heap-driven background-process scheduler."""

    def __init__(
        self,
        clock: AsyncClockABC,
        task_spawner: TaskSpawnerABC,
        stop_flag: AsyncClockABC,
        get_now: Callable[[], datetime],
        log: LoggingProvider,
        *,
        run_background_loop: bool = True,
    ) -> None:
        self._clock = clock
        self._spawner = task_spawner
        self._stop_flag = stop_flag
        self._get_now = get_now
        self._run_background_loop = run_background_loop
        self.log = log(__name__, self)

        self._heap: List[_HeapEntry] = []
        self._entries_by_process: Dict[int, Optional[_HeapEntry]] = {}
        self._registered: List[BackgroundProcessABC] = []
        self._attached: set[int] = set()  # ids of processes with a handle bound
        self._on_handle: Dict[int, Callable[[SchedulerHandleABC], None]] = {}
        self._seq = 0
        self._handles_attached = False
        self._started = False
        self._loop_task: Optional[TaskHandleABC] = None

    # -- registration ----------------------------------------------------

    def register(
        self,
        process: BackgroundProcessABC,
        *,
        on_handle: Optional[Callable[[SchedulerHandleABC], None]] = None,
    ) -> None:
        if self._started:
            raise RuntimeError("cannot register after start()")
        if id(process) in self._entries_by_process:
            return
        self._entries_by_process[id(process)] = None
        self._registered.append(process)
        if on_handle is not None:
            self._on_handle[id(process)] = on_handle

    def unregister(self, process: BackgroundProcessABC) -> None:
        # Remove a process. this way we dont raise and remove all, not only first, occurences
        self._registered = [p for p in self._registered if p is not process]
        self._attached.discard(id(process))
        self._on_handle.pop(id(process), None)
        entry = self._entries_by_process.pop(id(process), None)
        if entry is None:
            return
        try:
            self._heap.remove(entry)
        except ValueError:
            pass
        heapq.heapify(self._heap)

    # -- lifecycle -------------------------------------------------------

    def attach_handles(self) -> None:
        """Bind a :class:`SchedulerHandleABC` to every registered process.

        Can be called multiple times. Each call binds a handle to any
        process that was registered since the previous attach; processes
        that already have a handle are skipped. Raises if at least one
        new process is not bound by this call -- a redundant call
        (e.g. ``register().attach().attach()`` with no register between
        the attaches) is a misconfiguration.
        """
        if self._started:
            raise RuntimeError("cannot attach_handles after start()")
        if not self._registered:
            raise RuntimeError(
                "attach_handles() called with no registered processes; "
                "register at least one BackgroundProcessABC first"
            )
        newly_attached: List[BackgroundProcessABC] = []
        for process in self._registered:
            if id(process) in self._attached:
                continue
            handle = _SchedulerHandleImpl(self, process)
            process.attach_handle(handle)
            self._attached.add(id(process))
            newly_attached.append(process)
            callback = self._on_handle.get(id(process))
            if callback is not None:
                callback(handle)
        if not newly_attached:
            raise RuntimeError(
                "attach_handles() called but no new processes were bound; "
                "either register a new process before each attach_handles() "
                "call, or stop calling attach_handles() once everything is "
                "bound"
            )
        self._handles_attached = True

    def start(self) -> None:
        if self._started:
            raise RuntimeError("start called twice")
        if not self._handles_attached:
            raise RuntimeError(
                "attach_handles() must be called before start(); "
                "no handles are bound to the registered processes"
            )
        if not self._registered:
            raise RuntimeError(
                "start() called with no registered processes; "
                "register at least one BackgroundProcessABC first"
            )
        self._started = True
        if self._run_background_loop:
            self._loop_task = self._spawner.spawn(self._run_loop())

    async def stop(self) -> None:
        if not self._started:
            return
        self._stop_flag.set()
        # Wake the loop if it is parked on the clock.
        self._clock.set()
        if self._loop_task is not None:
            await self._loop_task.join()
        self._started = False
        self._loop_task = None

    # -- introspection ---------------------------------------------------

    async def list_events(self) -> Dict[datetime, ScheduledEvent]:
        return {
            entry.when: ScheduledEvent(process=entry.process, why=entry.why)
            for entry in self._heap
        }

    async def drain(self) -> None:
        """Run the loop until it would park.

        Drives the same logic as ``_run_loop`` but exits as soon as
        the loop would otherwise block on a clock wait.  Tests use
        this after :meth:`ManualClock.advance` to fire all due
        processes synchronously.

        On the first call after ``start`` (or any time the heap is
        empty while processes are registered), seeds the heap by
        asking every registered process for its first wakeup.
        """
        # Seed the heap if it's empty (no background loop running).
        if not self._heap:
            for process in list(self._registered):
                await self._ask_and_schedule(process)
        while not self._stop_flag.is_set():
            next_at = self._peek_earliest()
            if next_at is None:
                return
            delay = self._delay_to_next(next_at)
            if delay > 0:
                return
            await self._fire_earliest()

    # -- internal: scheduling -------------------------------------------

    async def schedule(
        self,
        process: BackgroundProcessABC,
        when: datetime,
        why: Dict[str, Any],
    ) -> None:
        """Insert or replace ``process``'s pending entry with ``when``.

        Public so :class:`SchedulerHandleABC` implementations can call
        back into the scheduler when a process pushes a wakeup.  If
        the process already has a pending entry at the same or an
        earlier time, this is a no-op.

        Raises:
            RuntimeError: if ``process`` is not registered.
        """
        if id(process) not in self._entries_by_process:
            self.log.debug(
                f"schedule: process {type(process).__name__} not registered"
            )
            raise RuntimeError("process not registered")
        existing = self._entries_by_process[id(process)]
        if existing is not None and existing.when <= when:
            # new when is later or equal -> noop
            self.log.debug(
                f"schedule: {type(process).__name__} noop "
                f"(existing={existing.when}, new={when})"
            )
            return

        if existing is not None:
            try:
                self._heap.remove(existing)
                self.log.debug(
                    f"schedule: replaced entry for {type(process).__name__} "
                    f"(was={existing.when}, now={when})"
                )
            except ValueError:
                pass

        self._seq += 1
        entry = _HeapEntry(
            when=when,
            seq=self._seq,
            process=process,
            why=dict(why),
        )
        heapq.heappush(self._heap, entry)
        self._entries_by_process[id(process)] = entry
        self.log.debug(
            f"schedule: added entry for {type(process).__name__} at {when} "
            f"(why={why.get('name', '?')})"
        )
        await self._maybe_shorten_sleep()

    async def _maybe_shorten_sleep(self) -> None:
        """Wake the loop so it re-evaluates the heap.

        Called from inside :meth:`schedule` whenever a new entry is
        inserted.  Wakes the loop regardless of whether it is parked
        on the clock (waiting for the next deadline) or on the stop
        flag (waiting for stop()).  In the latter case, the loop
        wakes, sees the stop flag is still clear, and re-enters the
        wait -- which is correct because the heap is now non-empty.
        """
        self._clock.set()

    # -- internal: loop --------------------------------------------------

    async def _run_loop(self) -> None:
        # Boot: seed every process's wakeup.
        for process in list(self._registered):
            await self._ask_and_schedule(process)

        while not self._stop_flag.is_set():
            next_at = self._peek_earliest()
            if next_at is None:
                # Nothing scheduled; park on the clock.  wake_at
                # unparks us by calling ``_clock.set`` (via
                # ``_maybe_shorten_sleep``); stop() does the same.
                await self._clock.wait()
                self._clock.clear()
                if self._stop_flag.is_set():
                    return
                continue

            delay = self._delay_to_next(next_at)
            if delay > 0:
                # Park on the clock for the delay.  wake_at or stop()
                # unparks us by calling ``_clock.set``.
                await self._clock.wait()
                self._clock.clear()
                if self._stop_flag.is_set():
                    return
                continue

            # delay == 0: the entry is already due.  Fire immediately.
            await self._fire_earliest()

    async def _fire_earliest(self) -> None:
        """Pop the earliest heap entry, run its process, then re-ask next_wakeup.

        Runs are wrapped so a single process raising does not kill the loop.
        """
        if not self._heap:
            return
        entry = heapq.heappop(self._heap)
        # Mark the entry as consumed; _ask_and_schedule will install a fresh one
        # or leave it as None if next_wakeup returns None.
        self._entries_by_process[id(entry.process)] = None
        try:
            await entry.process.run()
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"background process {type(entry.process).__name__} raised: {exc!r}"
            )
        await self._ask_and_schedule(entry.process)

    async def _ask_and_schedule(self, process: BackgroundProcessABC) -> None:
        """Ask ``process`` for its next wakeup and re-insert it on the heap.

        Returns silently if ``next_wakeup`` raised, returned ``None``,
        or returned a time the heap already has.
        """
        try:
            next_at = await process.next_wakeup()
        except Exception as exc:  # noqa: BLE001
            self.log.exception(
                f"background process {type(process).__name__}.next_wakeup raised: {exc!r}"
            )
            return
        if next_at is None:
            return
        await self.schedule(
            process,
            next_at,
            why={
                "name": type(process).__name__,
                "description": f"scheduled by {type(process).__name__}",
            },
        )

    def _peek_earliest(self) -> Optional[datetime]:
        """Return the wakeup time of the next heap entry, or None."""
        if not self._heap:
            return None
        return self._heap[0].when

    def _delay_to_next(self, next_at: datetime) -> float:
        """Seconds between ``now`` and ``next_at``, clamped to >= 0."""
        now = self._get_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=timezone.utc)
        delta = (next_at - now).total_seconds()
        return max(delta, 0.0)