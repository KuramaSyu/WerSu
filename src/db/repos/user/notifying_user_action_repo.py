"""Decorator that wakes the scheduler when a new :class:`UserActionEntity` is inserted.

Wraps a :class:`~src.api.repos.user_action_repo.UserActionRepoABC` and,
on :meth:`add_action`, asks the matching
:class:`UserActionListener` to wake its bound background process so
the new row gets picked up without polling.

The decorator keeps the bare Postgres repo as a pure data-access
layer (no scheduler dependency, no business rules).  Tests that don't
care about scheduling can use the bare :class:`UserActionPostgresRepo`.

The decorator is wired with a list of :class:`UserActionListener`
instances.  Each listener carries a :class:`UserActionKind` and the
:class:`~src.api.services.background_process.scheduler_handle.SchedulerHandleABC`
to wake for that kind.  In :mod:`src.main` the processes are
registered *after* the repo, so the listeners are appended to the
list only after
:meth:`~src.api.services.background_process.background_scheduler.BackgroundSchedulerABC.attach_handles`
has run.  Until then the decorator silently skips wakeups.

Only :meth:`add_action` pushes a wakeup.  :meth:`update_action` and
:meth:`remove_action` are called from the background process itself
(and from the service when an action is cancelled before firing); in
both cases the next :meth:`BackgroundProcessABC.next_wakeup` poll
sees the new state on its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from src.api.other.types import LoggingProvider
from src.api.other.undefined import is_undefined
from src.api.repos.user_action_repo import UserActionRepoABC
from src.api.services.background_process.scheduler_handle import SchedulerHandleABC
from src.db.entities.user.user_action import (
    FilterUserAction,
    UserActionEntity,
    UserActionKind,
)
from src.utils import logging_provider as default_logging_provider


# Name used in the scheduler's ``why`` payload for each action kind.
# Mirrors :class:`~src.api.services.background_process.user_action_process`
# so log lines read the same regardless of whether the wakeup came
# from this decorator or from a process calling its own handle.
_KIND_TO_NAME: Dict[UserActionKind, str] = {
    "disable": "DisableUser",
    "enable": "EnableUser",
    "delete": "DeleteUser",
}


class UserActionListener(SchedulerHandleABC):
    """A :class:`SchedulerHandleABC` scoped to a single :class:`UserActionKind`
    The reason, why this is used, is that the :class:`NotifyingUserActionRepo`
    can receive the "handle" without that the actual handle was created yet.
    The handle should be added later on with :meth:`BackgroundSchedulerABC.attach_handles`.

    The :class:`NotifyingUserActionRepo` holds a list of these.

    Note: until buound, :meth:`wake_at` is a no-op.

    Args:
        kind: the :class:`UserActionKind` this listener reacts to.
        handle: the scheduler handle to wake on a matching insert.
            May be ``None`` for late binding via :meth:`bind`.
    """

    def __init__(
        self,
        kind: UserActionKind,
        handle: Optional[SchedulerHandleABC] = None,
    ) -> None:
        self.kind = kind
        self._handle = handle

    def bind(self, handle: SchedulerHandleABC) -> None:
        """Bind the scheduler handle this listener forwards to."""
        self._handle = handle

    async def wake_at(self, when: datetime, why: Dict[str, object]) -> None:
        if self._handle is None:
            return
        await self._handle.wake_at(when, why)


class NotifyingUserActionRepo(UserActionRepoABC):
    """Wraps a :class:`UserActionRepoABC` and wakes a per-kind scheduler handle on insert.

    Args:
        inner: the underlying repo whose methods are delegated to.
        listeners: list of :class:`UserActionListener` to consult on
            :meth:`add_action`.  The list is held by reference, so
            callers may append to it after construction (the common
            case where the scheduler hands out handles only after
            :meth:`BackgroundSchedulerABC.attach_handles`).
        logging_provider: optional logging factory.  Uses the project
            default if omitted.
    """

    def __init__(
        self,
        inner: UserActionRepoABC,
        listeners: List[UserActionListener],
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._inner = inner
        self._listeners = listeners
        self.log = (logging_provider or default_logging_provider)(__name__, self)

    # -- delegation: read-only ------------------------------------------

    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        return await self._inner.get_actions_by_user(user_id)

    async def get_actions(self, filter: FilterUserAction) -> List[UserActionEntity]:
        return await self._inner.get_actions(filter)

    # -- delegation: mutating, with wakeup -------------------------------

    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        saved = await self._inner.add_action(action)
        await self._maybe_wake(saved)
        return saved

    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        return await self._inner.update_action(action)

    async def remove_action(self, action_id: str) -> None:
        await self._inner.remove_action(action_id)

    # -- internals -------------------------------------------------------

    async def _maybe_wake(self, action: UserActionEntity) -> None:
        """Ask the matching listener to wake its handle.

        Skips silently when the action has no kind, no ``execute_at``,
        or no listener has been registered for that kind yet.
        """
        kind = action.action
        if is_undefined(kind):
            self.log.debug("add_action: skipping wakeup (action.kind unset)")
            return
        when = action.execute_at
        if is_undefined(when):
            self.log.debug(f"add_action: skipping wakeup (execute_at unset, kind={kind})")
            return

        listener = self._find_listener(kind)
        if listener is None:
            self.log.debug(
                f"add_action: skipping wakeup (no listener for kind={kind})"
            )
            return

        why: Dict[str, object] = {
            "name": _KIND_TO_NAME[kind],
            "description": f"woken by NotifyingUserActionRepo.add_action (kind={kind})",
            "kind": kind,
        }
        if not is_undefined(action.id):
            why["action_id"] = str(action.id)
        if not is_undefined(action.user_id):
            why["user_id"] = str(action.user_id)

        try:
            await listener.wake_at(when, why)
        except Exception as exc:  # noqa: BLE001
            # The repo's contract is "persist + return"; a scheduler
            # failure must not break the insert.  The scheduler itself
            # will re-derive the next wakeup via next_wakeup() on its
            # own poll, so dropping a push is safe.
            self.log.exception(
                f"add_action: wake_at failed for kind={kind} action_id="
                f"{why.get('action_id', '?')}: {exc!r}"
            )

    def _find_listener(self, kind: UserActionKind) -> Optional[UserActionListener]:
        """Return the first listener whose ``kind`` matches, or ``None``."""
        for listener in self._listeners:
            if listener.kind == kind:
                return listener
        return None


__all__ = ["NotifyingUserActionRepo", "UserActionListener"]