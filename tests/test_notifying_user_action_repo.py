"""Tests for :class:`~src.db.repos.user.notifying_user_action_repo.NotifyingUserActionRepo`.

The decorator wraps a :class:`UserActionRepoABC` and, on
:meth:`~src.db.repos.user.notifying_user_action_repo.NotifyingUserActionRepo.add_action`,
asks the matching :class:`UserActionListener` to wake its bound
scheduler handle.  These tests pin:

* the wakeup payload (``when`` = ``execute_at``, ``name`` derived
  from the kind, ``kind`` / ``action_id`` / ``user_id`` extras when
  known);
* per-kind routing (a disable action wakes only the disable listener,
  an enable action only the enable listener);
* the skip cases (no kind, no ``execute_at``, no registered listener);
* that ``update_action`` / ``remove_action`` do *not* wake the
  scheduler (they are called from the background process itself and
  re-poll on their own);
* that a scheduler failure does not break the insert.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.api.other.undefined import UNDEFINED
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity, UserActionKind
from src.db.repos.user.notifying_user_action_repo import (
    NotifyingUserActionRepo,
    UserActionListener,
)
from tests.stubs.user_action_repo import _FakeUserActionRepo


class _RecordingSchedulerHandle(SchedulerHandleABC):
    """Scheduler handle that records every ``wake_at`` call.

    ``fail_next`` lets a single test force the next ``wake_at`` to
    raise so the decorator's failure isolation path is exercised.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: List[tuple[datetime, Dict[str, Any]]] = []
        self.fail_next: Optional[Exception] = None

    async def wake_at(self, when: datetime, why: Dict[str, Any]) -> None:
        if self.fail_next is not None:
            exc = self.fail_next
            self.fail_next = None
            raise exc
        self.calls.append((when, dict(why)))


class _Listeners:
    """Per-kind :class:`UserActionListener` factory for tests.

    Builds one :class:`UserActionListener` per requested kind, each
    wrapping a :class:`_RecordingSchedulerHandle`.  The decorator is
    given the listener list directly; tests read recorded calls off
    :attr:`handles`.
    """

    def __init__(
        self,
        kinds: tuple[UserActionKind, ...] = ("disable", "enable", "delete"),
    ) -> None:
        self.handles: Dict[UserActionKind, _RecordingSchedulerHandle] = {
            kind: _RecordingSchedulerHandle(name=f"handle-{kind}") for kind in kinds
        }
        self.listeners: List[UserActionListener] = [
            UserActionListener(kind=kind, handle=handle)
            for kind, handle in self.handles.items()
        ]


def _entity(**overrides: Any) -> UserActionEntity:
    """Build a disable action with sensible defaults."""
    defaults: Dict[str, Any] = {
        "user_id": "user-1",
        "action": "disable",
        "execute_at": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return UserActionEntity(**defaults)


# -- happy path ---------------------------------------------------------


async def test_add_action_wakes_disable_listener_with_execute_at() -> None:
    """A disable insert pushes one wakeup to the disable listener."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners(kinds=("disable", "enable"))
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity())

    disable = listeners.handles["disable"]
    enable = listeners.handles["enable"]
    assert len(disable.calls) == 1
    assert disable.calls[0][0] == saved.execute_at
    assert enable.calls == []


async def test_wakeup_payload_contains_name_kind_and_ids() -> None:
    """The ``why`` payload maps kind to ``name`` and carries entity fields."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity())

    when, why = listeners.handles["disable"].calls[0]
    assert when == saved.execute_at
    assert why["name"] == "DisableUser"
    assert why["kind"] == "disable"
    assert why["action_id"] == str(saved.id)
    assert why["user_id"] == "user-1"


async def test_enable_action_only_wakes_enable_listener() -> None:
    """Per-kind routing: an enable insert must not wake the disable listener."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    await repo.add_action(_entity(action="enable"))

    assert listeners.handles["disable"].calls == []
    assert len(listeners.handles["enable"].calls) == 1


async def test_delete_kind_resolves_to_delete_user_name() -> None:
    """The name map covers every kind defined on :class:`UserActionKind`."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    await repo.add_action(_entity(action="delete"))

    _, why = listeners.handles["delete"].calls[0]
    assert why["name"] == "DeleteUser"
    assert why["kind"] == "delete"


async def test_inner_repo_persists_and_returns_saved_entity() -> None:
    """The decorator forwards the insert and returns the persisted entity."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity())

    assert inner.add_action_calls and inner.add_action_calls[0] is saved
    assert saved.id is not UNDEFINED
    assert len(inner.all()) == 1


# -- skip cases ---------------------------------------------------------


async def test_add_action_skips_wakeup_when_kind_is_undefined() -> None:
    """No kind -> no listener lookup -> no wakeup, insert still succeeds."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity(action=UNDEFINED))

    assert listeners.handles["disable"].calls == []
    assert listeners.handles["enable"].calls == []
    assert saved.id is not UNDEFINED


async def test_add_action_skips_wakeup_when_kind_is_none() -> None:
    """``None`` is treated the same as ``UNDEFINED`` for the kind."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity(action=None))

    assert listeners.handles["disable"].calls == []
    assert saved.id is not UNDEFINED


async def test_add_action_skips_wakeup_when_execute_at_is_missing() -> None:
    """``execute_at`` is the wakeup time; without it there is nothing to schedule."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity(execute_at=UNDEFINED))

    assert listeners.handles["disable"].calls == []
    assert saved.id is not UNDEFINED


async def test_add_action_skips_wakeup_when_no_listener_registered() -> None:
    """A kind with no registered listener is silently skipped (boot-time)."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners(kinds=())  # empty registry
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    saved = await repo.add_action(_entity(action="disable"))

    assert listeners.handles == {}
    assert saved.id is not UNDEFINED


# -- late-binding via mutable list --------------------------------------


async def test_listener_list_is_mutable_after_construction() -> None:
    """The decorator holds the list by reference, so late appends are seen."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners(kinds=("disable",))
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    # Pre-condition: an enable insert has no listener yet.
    saved = await repo.add_action(_entity(action="enable"))
    assert listeners.handles["disable"].calls == []
    assert saved.id is not UNDEFINED

    # Late append: an enable listener becomes visible immediately.
    enable_handle = _RecordingSchedulerHandle(name="late-enable")
    listeners.listeners.append(
        UserActionListener(kind="enable", handle=enable_handle)
    )
    await repo.add_action(_entity(action="enable"))
    assert len(enable_handle.calls) == 1


async def test_unbound_listener_skips_wakeups_until_bound() -> None:
    """A listener built without a handle is inert until :meth:`bind`.

    This is the wiring pattern in :mod:`src.main`: the listeners are
    created up-front so the wiring is visible at the construction
    site, and :meth:`bind` is called later after
    :meth:`attach_handles` hands out the per-process handles.
    """
    inner = _FakeUserActionRepo()
    listeners: List[UserActionListener] = [UserActionListener(kind="disable")]
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners)

    # Listener has no handle yet -> wakeup is skipped, insert succeeds.
    saved = await repo.add_action(_entity())
    assert saved.id is not UNDEFINED

    # Bind a handle and confirm the same listener now wakes it.
    handle = _RecordingSchedulerHandle(name="bound-disable")
    listeners[0].bind(handle)
    await repo.add_action(_entity())
    assert len(handle.calls) == 1


# -- non-mutating delegation --------------------------------------------


async def test_get_actions_delegates_to_inner() -> None:
    """Read-only methods must not touch the scheduler."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    flt = FilterUserAction(action="disable")
    result = await repo.get_actions(flt)

    assert inner.get_actions_calls == [flt]
    assert result == []


async def test_get_actions_by_user_delegates_to_inner() -> None:
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    result = await repo.get_actions_by_user("user-1")

    assert inner.get_actions_by_user_calls == ["user-1"]
    assert result == []


# -- mutating methods that must NOT wake --------------------------------


async def test_update_action_does_not_wake_scheduler() -> None:
    """``update_action`` is called from the background process itself."""
    seeded = _entity()
    inner = _FakeUserActionRepo(initial=[seeded])
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    stored = inner.all()[0]
    stored.executed_at = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    await repo.update_action(stored)

    assert listeners.handles["disable"].calls == []
    assert listeners.handles["enable"].calls == []


async def test_remove_action_does_not_wake_scheduler() -> None:
    """``remove_action`` cancels an action; the next poll sees the row gone."""
    seeded = _entity()
    inner = _FakeUserActionRepo(initial=[seeded])
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)

    stored = inner.all()[0]
    await repo.remove_action(str(stored.id))

    assert listeners.handles["disable"].calls == []
    assert listeners.handles["enable"].calls == []


# -- failure isolation --------------------------------------------------


async def test_scheduler_failure_does_not_break_insert() -> None:
    """A raising handle must not propagate: the insert still returns."""
    inner = _FakeUserActionRepo()
    listeners = _Listeners()
    repo = NotifyingUserActionRepo(inner=inner, listeners=listeners.listeners)
    listeners.handles["disable"].fail_next = RuntimeError("scheduler down")

    saved = await repo.add_action(_entity())

    assert saved.id is not UNDEFINED
    # The handle raised before appending, so it recorded nothing --
    # but the insert still succeeded.
    assert listeners.handles["disable"].calls == []
    assert len(inner.all()) == 1