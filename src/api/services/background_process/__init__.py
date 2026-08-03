"""Contracts for background processes driven by an in-process scheduler.

A :class:`BackgroundProcessABC` runs at scheduled times without a
human in the loop.  The scheduler asks each registered process when it
wants to be called next via :meth:`BackgroundProcessABC.next_wakeup`
and the process can push an earlier wakeup via
:meth:`SchedulerHandleABC.wake_at`.

The pieces:

* :class:`BackgroundProcessABC` - the work contract.
* :class:`SchedulerHandleABC` - the per-process handle the process
  uses to ask the scheduler to wake it.
* :class:`BackgroundSchedulerABC` - the scheduler that owns the
  processes and drives the loop.
* :class:`AsyncClockABC` / :class:`TaskSpawnerABC` /
  :class:`TaskHandleABC` - seams over :mod:`asyncio` so the scheduler
  is testable without a real running loop.

For the `why` payloads passed to :meth:`SchedulerHandleABC.wake_at`,
the scheduler stores the most recent one per process and exposes it
through :meth:`BackgroundSchedulerABC.list_events`.
"""