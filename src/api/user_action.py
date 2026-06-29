"""Abstract base for user-action storage.

The repo is intentionally thin: it manages persistence only. Scheduling,
executor dispatch, and any kind of business rule live in the service
layer.  The ABC lives in :mod:`src.api` so the service code can depend
on it without importing the concrete Postgres implementation.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from src.db.entities.user.user_action import FilterUserAction, UserActionEntity


class UserActionRepoABC(ABC):
    """Storage contract for ``user_action`` rows.

    Implementations must not perform any permission or business
    validation; they only translate requests into storage operations
    and surface the persisted entity back to the caller.
    """

    @abstractmethod
    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        """Return every action targeting ``user_id``.

        Ordered by ``execute_at`` ascending so consumers can treat the
        first pending entry as the next action to run.
        """
        ...

    @abstractmethod
    async def get_actions(
        self,
        filter: FilterUserAction,
    ) -> List[UserActionEntity]:
        """Return every action matching ``filter``.

        Field semantics follow :class:`FilterUserAction`:

        * ``UNDEFINED`` -> column ignored
        * ``None`` on nullable columns -> ``IS NULL``
        * concrete values -> ``<=`` / ``>=`` / ``=`` depending on the
          column, as documented on the filter dataclass.
        """
        ...

    @abstractmethod
    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        """Insert a new action row and return the persisted entity.

        The repository populates any server-side defaults (notably
        ``id``) before returning.
        """
        ...

    @abstractmethod
    async def remove_action(self, action_id: str) -> None:
        """Delete the action with the given id.

        Raises ``ValueError`` if the id does not exist so callers can
        distinguish "already gone" from a real failure.
        """
        ...

    @abstractmethod
    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        """Persist changes to an existing action.

        The entity's ``id`` is required; every other field with a
        concrete value replaces the persisted column.  ``UNDEFINED``
        fields are ignored, ``None`` explicitly clears the column.
        """
        ...