"""In-memory :class:`UserActionRepoABC` fake for unit tests.

Records every call so tests can assert on how the service reacts to
create / update / delete share events.

Filter semantics mirror the real Postgres repo:

* ``id``, ``user_id``, ``action`` are exact-match when set.
* ``executed_at=None`` selects rows where the column IS NULL
  (pending rows).
* ``executed_at=datetime`` selects rows where the column is NOT NULL
  and ``<=`` the value (executed on or before the given timestamp).
* ``UNDEFINED`` on any field ignores it.
"""

from __future__ import annotations

from typing import Any, List, Optional

from src.api.undefined import UNDEFINED
from src.api.user_action import UserActionRepoABC
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity


class _FakeUserActionRepo(UserActionRepoABC):
    """In-memory :class:`UserActionRepoABC` that records every call."""

    def __init__(self, initial: Optional[List[UserActionEntity]] = None) -> None:
        self._store: dict[str, UserActionEntity] = {}
        self.add_action_calls: List[UserActionEntity] = []
        self.remove_action_calls: List[str] = []
        self.update_action_calls: List[UserActionEntity] = []
        self.get_actions_by_user_calls: List[str] = []
        self.get_actions_calls: List[FilterUserAction] = []
        # Track unique ids so tests can assert on created actions even
        # without a known id up front.
        self._next_id = 0

        for action in initial or []:
            if action.id is UNDEFINED:
                self._next_id += 1
                action.id = f"pre-seeded-{self._next_id}"
            self._store[str(action.id)] = action

    # -- helpers ---------------------------------------------------------

    def all(self) -> List[UserActionEntity]:
        """Snapshot of every stored action (for assertions)."""
        return list(self._store.values())

    def for_user(self, user_id: str) -> List[UserActionEntity]:
        return [
            action for action in self._store.values()
            if str(action.user_id) == user_id
        ]

    # -- UserActionRepoABC -----------------------------------------------

    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        self.get_actions_by_user_calls.append(user_id)
        return [
            action for action in self._store.values()
            if str(action.user_id) == user_id
        ]

    async def get_actions(self, filter: FilterUserAction) -> List[UserActionEntity]:
        self.get_actions_calls.append(filter)

        def matches(action: UserActionEntity) -> bool:
            if filter.id is not UNDEFINED and action.id != filter.id:
                return False
            if filter.user_id is not UNDEFINED and action.user_id != filter.user_id:
                return False
            if filter.action is not UNDEFINED and action.action != filter.action:
                return False
            # ``None`` -> IS NULL, ``UNDEFINED`` -> ignored, datetime -> ``<= value``.
            # ``UNDEFINED`` on the stored action is treated as "not yet set",
            # which the service treats as pending.
            if filter.executed_at is None:
                if action.executed_at is not None and action.executed_at is not UNDEFINED:
                    return False
            elif filter.executed_at is not UNDEFINED:
                # concrete datetime -> action.executed_at must be set and <= filter
                if action.executed_at is None or action.executed_at is UNDEFINED:
                    return False
                if action.executed_at > filter.executed_at:
                    return False
            return True

        return [action for action in self._store.values() if matches(action)]

    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        self.add_action_calls.append(action)
        if action.id in (UNDEFINED, None):
            self._next_id += 1
            action.id = f"action-{self._next_id}"
        self._store[str(action.id)] = action
        return action

    async def remove_action(self, action_id: str) -> None:
        self.remove_action_calls.append(action_id)
        if action_id not in self._store:
            raise ValueError(f"user_action not found: {action_id}")
        del self._store[action_id]

    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        self.update_action_calls.append(action)
        if action.id in (UNDEFINED, None):
            raise ValueError("action.id is required for update")
        if str(action.id) not in self._store:
            raise ValueError(f"user_action not found: {action.id}")
        self._store[str(action.id)] = action
        return action


__all__ = ["_FakeUserActionRepo"]