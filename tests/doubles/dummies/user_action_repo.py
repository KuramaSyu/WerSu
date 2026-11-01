from __future__ import annotations

from typing import List

from src.api.repos.user_action_repo import UserActionRepoABC
from src.db.entities.user.user_action import FilterUserAction, UserActionEntity


class _DummyUserActionRepo(UserActionRepoABC):
    """Inert :class:`UserActionRepoABC` for tests that only wire a context.

    Every read returns an empty result and every write is a no-op, so a
    context built on top of it always reports ``is_disabled() == False``.
    Use this when a test needs a ``RepoContextFactory`` but does not
    exercise user-action behaviour.
    """

    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        return []

    async def get_actions(
        self,
        filter: FilterUserAction,
    ) -> List[UserActionEntity]:
        return []

    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        return action

    async def remove_action(self, action_id: str) -> None:
        return None

    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        return action


__all__ = [
    "_DummyUserActionRepo",
]
