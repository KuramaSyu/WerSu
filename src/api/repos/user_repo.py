"""Abstract storage contract for the ``auth.user`` row.

Implementations:
* :class:`src.db.repos.user.user.UserPostgresRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.db.entities.user.user import FilterUser, UserEntity


class UserRepoABC(ABC):
    """Abstract user storage contract."""

    @abstractmethod
    async def insert(self, user: UserEntity) -> UserEntity:
        """Insert a new user and return the created entity with ID."""

    @abstractmethod
    async def update(self, user: UserEntity) -> UserEntity:
        """Update an existing user."""

    @abstractmethod
    async def upsert(self, user: UserEntity) -> UserEntity:
        """Insert or update a user based on discord_id."""

    @abstractmethod
    async def select(self, filter: FilterUser) -> Optional[UserEntity]:
        """Fetch the first user matching every set field of ``filter``.

        Set fields are AND-ed; an empty filter matches no user
        (``None`` is returned).  A single-field filter (``user_id``,
        ``username``) covers the lookup-by-id / lookup-by-username
        cases too.  Usernames are unique among non-null values
        (``auth_user_username_uidx``).
        """

    @abstractmethod
    async def select_by_discord_id(self, discord_id: int) -> Optional[UserEntity]:
        """Select a user by discord_id."""

    @abstractmethod
    async def fetch_users_by_ids(self, ids: List[str]) -> List[UserEntity]:
        """Fetch many users by id in a single query.

        Unknown ids are silently skipped -- the result is the
        intersection of ``ids`` and the rows that exist.  The
        returned entities keep the input order of ``ids`` (after
        de-duplication); this is stable so the service layer can
        rely on it when building batch responses.

        Args:
            ids: user ids to load.  An empty list returns ``[]``
                without issuing a query.

        Returns:
            List[UserEntity]: the matching users, in input order.
        """

    @abstractmethod
    async def search(self, query: str, limit: int = 10) -> List[UserEntity]:
        """Search users by partial id, fuzzy username or exact email.

        The interpretation of ``query`` is shape-based:

        * contains an ``@``: treated as an email and matched exactly
          (no fuzzy fallback: only users with that exact address come
          back).
        * otherwise: matched against the user ``id`` (substring),
          ``username`` and ``display_name`` (trigram similarity + case
          insensitive substring), best match first.

        Args:
            query: free-form search term.
            limit: maximum number of results.  Defaults to 10.

        Returns:
            The matching users, ordered by relevance for non-email
            queries.  An empty/blank query yields an empty list.
        """

    @abstractmethod
    async def delete(self, user_id: str) -> bool:
        """Delete a user by ID."""


__all__ = ["UserRepoABC"]
