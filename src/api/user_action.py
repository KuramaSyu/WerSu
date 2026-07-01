"""Abstract base for user-action storage.

The repo is intentionally thin: it manages persistence only.
Scheduling, executor dispatch and any business rule live in the
service layer.  The ABC lives in :mod:`src.api` so the service code
can depend on it without importing the concrete Postgres
implementation.
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

    Implementations:
    * :class:`src.db.repos.user.user_action.UserActionPostgresRepo`
    """

    @abstractmethod
    async def get_actions_by_user(self, user_id: str) -> List[UserActionEntity]:
        """Return every action targeting ``user_id``.

        Ordered by ``execute_at`` ascending so consumers can treat the
        first pending entry as the next action to run.

        Args:
            user_id: id of the user whose actions should be returned.

        Returns:
            List[UserActionEntity]: actions for ``user_id``, ordered by
            ``execute_at`` ascending.
        """
        ...

    @abstractmethod
    async def get_actions(
        self,
        filter: FilterUserAction,
    ) -> List[UserActionEntity]:
        """Return every action matching ``filter``.

        Args:
            filter: search filter.  Field semantics follow
                :class:`FilterUserAction`:

                * :obj:`~src.api.undefined.UNDEFINED` -> column ignored.
                * :obj:`None` on nullable columns -> ``IS NULL``.
                * concrete values -> ``<=`` / ``>=`` / ``=`` depending
                  on the column, as documented on the filter dataclass.

        Returns:
            List[UserActionEntity]: matching actions.
        """
        ...

    @abstractmethod
    async def add_action(self, action: UserActionEntity) -> UserActionEntity:
        """Insert ``action`` and return the persisted entity.

        The repository populates any server-side defaults (notably
        ``id``) before returning.

        Args:
            action: entity to insert.  ``id`` may be
                :obj:`~src.api.undefined.UNDEFINED`; any other field
                that is required by the schema must be set.

        Returns:
            UserActionEntity: the persisted entity with server-side
            defaults filled in.
        """
        ...

    @abstractmethod
    async def remove_action(self, action_id: str) -> None:
        """Delete the action with the given id.

        Args:
            action_id: id of the action to delete.

        Raises:
            ValueError: if no action with ``action_id`` exists, so
                callers can distinguish "already gone" from a real
                failure.
        """
        ...

    @abstractmethod
    async def update_action(self, action: UserActionEntity) -> UserActionEntity:
        """Persist changes to an existing action.

        The entity's ``id`` is required; every other field with a
        concrete value replaces the persisted column.
        :obj:`~src.api.undefined.UNDEFINED` fields are ignored;
        :obj:`None` explicitly clears the column.

        Args:
            action: entity carrying the new field values plus the
                ``id`` of the row to update.

        Returns:
            UserActionEntity: the persisted entity after the update.
        """
        ...