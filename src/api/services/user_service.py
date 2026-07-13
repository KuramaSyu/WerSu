"""Abstract application service for user lifecycle and bootstrap directories.

Implementations:
* :class:`src.services.user_service.UserServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.user.user import UserEntity


class UserServiceABC(ABC):
    """Abstract application service for the user entity.

    Implementations:
    * :class:`src.services.user_service.UserServiceImpl`
    """

    @abstractmethod
    async def get_user(
        self,
        user_id: Optional[str] = None,
        discord_id: Optional[int] = None,
    ) -> Optional[UserEntity]:
        """Look up a user by id or Discord id.

        Args:
            user_id: id of the user to load.
            discord_id: Discord id of the user to load.

        Returns:
            Optional[UserEntity]: the matching user, or ``None`` if
            neither argument is supplied or no row matches.
        """

    @abstractmethod
    async def create_user(self, user: UserEntity) -> UserEntity:
        """Create a user and bootstrap their default zettelkasten directories.

        For ``human`` users the call also creates the default
        directories (fleeting / literature / permanent) with admin
        relations for the new user.  ``temporary`` and ``system``
        users skip the directory bootstrap.

        Args:
            user: the user to create.  ``user.id`` may be
                :obj:`~src.api.undefined.UNDEFINED` -- the repo
                assigns one.

        Returns:
            UserEntity: the persisted user with its server-assigned
            id populated.
        """
        ...


__all__ = ["UserServiceABC"]