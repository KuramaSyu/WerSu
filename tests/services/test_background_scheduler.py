"""Tests for the background-process scheduler.

Time is driven by :class:`tests.stubs.background_process.ManualClock`,
which is a plain boolean + parked-future list -- no threading, no
``asyncio.Event``.  Synchronisation with the scheduler is via
:meth:`BackgroundSchedulerImpl.drain`, which fires every due process
once and returns.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from src.api.other.undefined import UNDEFINED
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.entities.user.user_action import UserActionEntity
from src.db.repos.user.notifying_user_action_repo import NotifyingUserActionRepo, UserActionListener
from src.services.background_process.async_clock import AsyncClockAsyncio
from src.services.background_process.background_scheduler import BackgroundSchedulerImpl
from src.services.background_process.processes import UserDisableProcessImpl
from src.services.background_process.task_spawner import TaskSpawnerAsyncio
from tests.stubs.background_process import FakeBackgroundProcess, InMemoryTaskSpawner, ManualClock
from tests.stubs.logging import silent_logger
from tests.stubs.user_action_repo import _FakeUserActionRepo
import asyncio, pytest


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

async def test_register_with_on_handle_invokes_callback_on_attach(scheduler_pair) -> None:
    """``on_handle`` is called once per process when its handle is bound.

    Pins the contract that lets external code (e.g. a notifying repo)
    capture the scheduler handle without reaching into the process's
    private state.
    """
    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=60)]

    captured: list = []

    def capture(handle) -> None:
        captured.append(handle)

    scheduler.register(process, on_handle=capture)
    scheduler.attach_handles()

    assert len(captured) == 1
    # The handle returned is a real SchedulerHandleABC, not None.

    assert isinstance(captured[0], SchedulerHandleABC)


async def test_on_handle_wires_real_listener_to_real_process(scheduler_pair) -> None:
    """End-to-end: ``on_handle`` binds a real listener to a real process.

    Pins the wiring pattern used in :mod:`src.main`: the real
    :class:`BackgroundSchedulerImpl` binds its real
    :class:`UserDisableProcessImpl` to a
    :class:`~src.db.repos.user.notifying_user_action_repo.UserActionListener`
    via the ``on_handle`` kwarg.  Inserting a due row through the
    notifying repo then wakes the scheduler, which fires the process
    and marks the row as executed.

    All collaborators are real except the storage layer
    (the in-memory :class:`_FakeUserActionRepo`) and the clock
    (a :class:`ManualClock` whose time only advances on test calls).
    """


    clock, scheduler = scheduler_pair
    clock.set_now(datetime(2026, 1, 1, 12, tzinfo=timezone.utc))

    fake_repo = _FakeUserActionRepo()
    listener = UserActionListener(kind="disable")
    notifying_repo = NotifyingUserActionRepo(
        inner=fake_repo,
        listeners=[listener],
    )
    process = UserDisableProcessImpl(
        user_action_repo=notifying_repo,
        get_now=lambda: clock.now,
        log=silent_logger,
    )

    # The whole point of this test: ``listener.bind`` is what
    # ``on_handle`` is wired to.  Before ``attach_handles`` the
    # listener has no handle, so an insert is a no-op for the scheduler.
    scheduler.register(process, on_handle=listener.bind)
    scheduler.attach_handles()
    scheduler.start()

    past = clock.now - timedelta(seconds=1)
    saved = await notifying_repo.add_action(
        UserActionEntity(user_id="alice", action="disable", execute_at=past)
    )

    # No real timers; the scheduler is fired by the listener's push.
    await scheduler.drain()

    # The fake repo's row should now have executed_at set.
    executed = fake_repo.for_user("alice")
    assert len(executed) == 1
    assert executed[0].executed_at is not None and executed[0].executed_at is not UNDEFINED
    assert executed[0].id == saved.id


async def test_on_handle_is_not_invoked_before_attach_handles(scheduler_pair) -> None:
    """``on_handle`` fires inside ``attach_handles``, not at ``register``.

    A late-binding caller can register with ``on_handle`` set and
    still have time to mutate state on the listener object before
    the callback fires.
    """

    clock, scheduler = scheduler_pair
    process = FakeBackgroundProcess()
    process.next_wakeup_values = [clock.now + timedelta(seconds=60)]

    captured: list = []

    def capture(handle) -> None:
        captured.append(handle)

    scheduler.register(process, on_handle=capture)
    assert captured == [], "on_handle must not fire during register"

    scheduler.attach_handles()
    assert len(captured) == 1
    assert isinstance(captured[0], SchedulerHandleABC)


async def test_production_loop_fires_after_real_sleep() -> None:
    """Pin the production loop's deadline wait: ``_run_loop`` parks on
    :meth:`AsyncClockAsyncio.sleep` (interruptible, deadline-aware),
    not on :meth:`AsyncClockAsyncio.wait` (which blocks until
    ``set()`` and never wakes on its own).

    The bug the test pins: the loop used ``_clock.wait()`` for the
    "sleep until the next deadline" branch.  ``wait()`` has no
    timeout, so once the loop parked, only a new ``wake_at`` or
    ``stop()`` could wake it -- the deadline elapsed silently and
    no process ever fired.  ``drain()``-based tests miss this
    because they never start the background loop (they set
    ``run_background_loop=False``).
    """


    # Use naive datetimes for both sides so ``_delay_to_next``'s
    # "naive -> UTC" interpretation produces a positive delta on
    # any host.  An aware ``next_at`` (e.g. ``datetime.now(timezone.utc)``)
    # paired with naive ``now`` would let the loop see ``delay == 0``
    # on a host east of UTC, masking the bug.  Mirrors the
    # production wiring where ``get_now`` is naive and
    # ``UserActionEntity.execute_at`` is read back naive from
    # ``TIMESTAMP WITHOUT TIME ZONE``.
    scheduled_at = datetime.now() + timedelta(milliseconds=200)
    clock = AsyncClockAsyncio()
    stop_flag = AsyncClockAsyncio()
    scheduler = BackgroundSchedulerImpl(
        clock=clock,
        task_spawner=TaskSpawnerAsyncio(),
        stop_flag=stop_flag,
        get_now=lambda: datetime.now(),
        log=silent_logger,
        run_background_loop=True,
    )

    process = FakeBackgroundProcess()
    # First wakeup: 0.2s in the future.  Second: None so the process
    # is removed from the heap after firing.
    process.next_wakeup_values = [scheduled_at, None]

    scheduler.register(process)
    scheduler.attach_handles()
    scheduler.start()

    try:
        # Wait up to 2s for the process to fire.  The deadline is
        # 0.2s, so a healthy loop fires well within the timeout; a
        # broken loop (parked on ``wait()``) would never fire and
        # the deadline check would raise :exc:`AssertionError`.
        deadline = asyncio.get_event_loop().time() + 2.0
        while process.run_calls == 0:
            if asyncio.get_event_loop().time() > deadline:
                raise AssertionError(
                    "process did not fire after the deadline elapsed; "
                    "_run_loop is parked on _clock.wait() instead of "
                    "_clock.sleep(delay)"
                )
            await asyncio.sleep(0.02)
        assert process.run_calls == 1
    finally:
        await scheduler.stop()
