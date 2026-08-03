"""Tests for the background-process scheduler.

Time is driven by :class:`tests.stubs.background_process.ManualClock`,
which is a plain boolean + parked-future list -- no threading, no
``asyncio.Event``.  Synchronisation with the scheduler is via
:meth:`BackgroundSchedulerImpl.drain`, which fires every due process
once and returns.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from src.services.background_process.background_scheduler import BackgroundSchedulerImpl
from tests.stubs.background_process import (
    FakeBackgroundProcess,
    InMemoryTaskSpawner,
    ManualClock,
)
from tests.stubs.logging import silent_logger


pytestmark = pytest.mark.timeout(10)


def _make_scheduler(clock: ManualClock) -> BackgroundSchedulerImpl:
    return BackgroundSchedulerImpl(
        clock=clock,
        task_spawner=InMemoryTaskSpawner(),
        stop_flag=ManualClock(),
        get_now=lambda: clock.now,
        log=silent_logger,
        run_background_loop=False,
    )


@pytest.fixture
async def scheduler_pair():
    """Yield ``(clock, scheduler)`` and stop the scheduler on teardown."""
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    yield clock, scheduler
    await scheduler.stop()


async def test_register_attaches_handle_and_schedules_wakeup(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=60)]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    events = await scheduler.list_events()
    assert len(events) == 1
    entry = next(iter(events.values()))
    assert entry.process is process
    assert entry.why["name"] == "FakeBackgroundProcess"


async def test_loop_calls_run_when_delay_elapses(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=30), None]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()
    assert process.run_calls == 0

    clock.advance(30)
    await scheduler.drain()
    assert process.run_calls == 1


async def test_wake_at_with_earlier_time_shortens_pending_sleep(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=600), None]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    await process.wake_at(clock.now + timedelta(seconds=5), why={"name": "X"})
    clock.advance(5)
    await scheduler.drain()
    assert process.run_calls == 1


async def test_wake_at_with_later_time_is_noop(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [
        clock.now + timedelta(seconds=60),
        clock.now + timedelta(seconds=120),
        None,
    ]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    await process.wake_at(clock.now + timedelta(seconds=600), why={"name": "X"})
    clock.advance(60)
    await scheduler.drain()
    assert process.run_calls == 1
    clock.advance(60)
    await scheduler.drain()
    assert process.run_calls == 2


async def test_run_exception_does_not_kill_scheduler(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    bad = FakeBackgroundProcess("Bad")
    good = FakeBackgroundProcess("Good")
    bad.next_wakeup_values = [clock.now + timedelta(seconds=10), None]
    good.next_wakeup_values = [clock.now + timedelta(seconds=20), None]

    async def _boom() -> None:
        raise RuntimeError("nope")

    bad.run_side_effect = _boom

    scheduler.register(bad)
    scheduler.register(good)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    clock.advance(10)
    await scheduler.drain()
    assert bad.run_calls == 1
    assert good.run_calls == 0

    clock.advance(10)
    await scheduler.drain()
    assert good.run_calls == 1


async def test_stop_cancels_in_flight_sleep() -> None:
    """Scheduler created and torn down inside the test (no fixture)."""
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=10_000)]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    await scheduler.stop()
    assert process.run_calls == 0


async def test_process_returning_none_next_wakeup_is_parked_until_wake_at(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = []

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()
    assert await scheduler.list_events() == {}

    # After firing, the scheduler will ask the process for the *next*
    # wakeup.  We want None so the process is removed from the heap.
    process.next_wakeup_values = [None]
    await process.wake_at(clock.now + timedelta(seconds=5), why={"name": "manual"})
    clock.advance(5)
    await scheduler.drain()
    assert process.run_calls == 1


async def test_unregister_removes_process(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=5)]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    scheduler.unregister(process)
    clock.advance(5)
    await scheduler.drain()
    assert process.run_calls == 0


async def test_two_processes_share_one_loop(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    a = FakeBackgroundProcess("A")
    b = FakeBackgroundProcess("B")
    a.next_wakeup_values = [clock.now + timedelta(seconds=10), None]
    b.next_wakeup_values = [clock.now + timedelta(seconds=20), None]

    scheduler.register(a)
    scheduler.register(b)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    clock.advance(10)
    await scheduler.drain()
    assert a.run_calls == 1
    assert b.run_calls == 0

    clock.advance(10)
    await scheduler.drain()
    assert b.run_calls == 1


async def test_list_events_reflects_current_heap(scheduler_pair) -> None:
    clock, scheduler = scheduler_pair
    a = FakeBackgroundProcess("A")
    b = FakeBackgroundProcess("B")
    a_at = clock.now + timedelta(seconds=10)
    b_at = clock.now + timedelta(seconds=30)
    a.next_wakeup_values = [a_at]
    b.next_wakeup_values = [b_at]

    scheduler.register(a)
    scheduler.register(b)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    events = await scheduler.list_events()
    assert len(events) == 2
    times = sorted(events.keys())
    assert times[0] == a_at
    assert times[1] == b_at


# -- misconfiguration guards ---------------------------------------------


async def test_start_without_attach_handles_raises() -> None:
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=1)]

    scheduler.register(process)
    with pytest.raises(RuntimeError, match="attach_handles"):
        scheduler.start()


async def test_attach_handles_without_processes_raises() -> None:
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    with pytest.raises(RuntimeError, match="registered"):
        scheduler.attach_handles()


async def test_register_after_attach_handles_is_allowed() -> None:
    """``register().attach().register()`` is permitted; the second
    ``register`` appends a process whose handle is not yet bound."""
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    a = FakeBackgroundProcess("A")
    b = FakeBackgroundProcess("B")
    a.next_wakeup_values = [clock.now + timedelta(seconds=10)]
    b.next_wakeup_values = [clock.now + timedelta(seconds=20)]

    scheduler.register(a)
    scheduler.attach_handles()
    scheduler.register(b)
    scheduler.attach_handles()
    scheduler.start()
    await scheduler.drain()

    events = await scheduler.list_events()
    assert len(events) == 2


async def test_attach_handles_with_no_new_process_raises() -> None:
    """A redundant ``attach_handles()`` (no new process registered
    between calls) is a misconfiguration -- raises."""
    clock = ManualClock()
    scheduler = _make_scheduler(clock)
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=1)]

    scheduler.register(process)
    scheduler.attach_handles()
    with pytest.raises(RuntimeError, match="no new processes"):
        scheduler.attach_handles()