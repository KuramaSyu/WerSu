"""Tests for the user-action background processes.

Drives :class:`tests.stubs.background_process.ManualClock`-like
fixtures (we use a plain callable for ``get_now`` here since the
processes do not own a clock) and :class:`_FakeUserActionRepo`
to assert which rows the process executes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from src.api.other.undefined import UNDEFINED
from src.db.entities.user.user_action import UserActionEntity, UserActionKind
from src.services.background_process.processes.user_disable_process import (
    UserDisableProcessImpl,
)
from src.services.background_process.processes.user_enable_process import (
    UserEnableProcessImpl,
)
from tests.stubs.logging import silent_logger
from tests.stubs.user_action_repo import _FakeUserActionRepo


def _at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def _action(
    user_id: str,
    kind: UserActionKind,
    execute_at: datetime,
) -> UserActionEntity:
    return UserActionEntity(
        user_id=user_id,
        action=kind,
        execute_at=execute_at,
        executed_at=UNDEFINED,
    )


def _now_at(ts: datetime) -> Callable[[], datetime]:
    return lambda: ts


async def test_disable_process_marks_due_disable_rows_as_executed() -> None:
    now = _at(2026, 1, 1, 12)
    repo = _FakeUserActionRepo([
        _action("alice", "disable", now - timedelta(hours=1)),  # due
        _action("bob", "disable", now + timedelta(hours=1)),    # not due
        _action("carol", "enable", now - timedelta(hours=1)),   # wrong kind
    ])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    await process.run()

    executed = [a for a in repo.all() if a.executed_at is not UNDEFINED and a.executed_at is not None]
    assert len(executed) == 1
    assert str(executed[0].user_id) == "alice"
    assert executed[0].executed_at == now


async def test_disable_process_skips_already_executed_rows() -> None:
    now = _at(2026, 1, 1, 12)
    already = _action("alice", "disable", now - timedelta(hours=1))
    already.executed_at = now - timedelta(hours=2)
    repo = _FakeUserActionRepo([already])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    assert await process.next_wakeup() is None
    await process.run()
    assert str(already.executed_at) == str(now - timedelta(hours=2))


async def test_disable_next_wakeup_returns_earliest_due_pending() -> None:
    now = _at(2026, 1, 1, 12)
    repo = _FakeUserActionRepo([
        _action("alice", "disable", now - timedelta(hours=2)),
        _action("bob", "disable", now - timedelta(hours=1)),
    ])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    next_at = await process.next_wakeup()
    assert next_at == now - timedelta(hours=2)


async def test_disable_next_wakeup_returns_none_when_no_pending() -> None:
    now = _at(2026, 1, 1, 12)
    repo = _FakeUserActionRepo([
        _action("alice", "enable", now - timedelta(hours=1)),  # wrong kind
    ])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    assert await process.next_wakeup() is None


async def test_enable_process_only_touches_enable_rows() -> None:
    now = _at(2026, 1, 1, 12)
    repo = _FakeUserActionRepo([
        _action("alice", "enable", now - timedelta(hours=1)),
        _action("bob", "disable", now - timedelta(hours=1)),
    ])
    process = UserEnableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    await process.run()
    executed = [a for a in repo.all() if a.executed_at is not UNDEFINED and a.executed_at is not None]
    assert len(executed) == 1
    assert str(executed[0].user_id) == "alice"


async def test_process_does_not_execute_future_dated_pending_rows() -> None:
    """Regression: ``execute_at`` is in the future, the process must skip it.

    Pins the time check in :meth:`_UserActionByKindProcessImpl._fetch_due_pending`.
    Without it, a row scheduled for tomorrow would be marked executed
    on first boot and skipped on every later run.
    """
    now = _at(2026, 1, 1, 12)
    future = _action("alice", "disable", now + timedelta(hours=1))
    repo = _FakeUserActionRepo([future])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    await process.run()

    assert future.executed_at is UNDEFINED or future.executed_at is None
    # next_wakeup returns None because no row is due yet.
    assert await process.next_wakeup() is None


async def test_process_handles_naive_execute_at_as_utc() -> None:
    """Naive ``execute_at`` (no tzinfo) is treated as UTC.

    The repo stores naive timestamps; the comparison still works
    against an aware ``get_now()``.
    """
    now = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    naive_past = datetime(2026, 1, 1, 11)  # 1 hour before, no tzinfo
    action = UserActionEntity(
        user_id="alice",
        action="disable",
        execute_at=naive_past,
        executed_at=UNDEFINED,
    )
    repo = _FakeUserActionRepo([action])
    process = UserDisableProcessImpl(
        user_action_repo=repo,
        get_now=_now_at(now),
        log=silent_logger,
    )

    await process.run()

    assert action.executed_at == now