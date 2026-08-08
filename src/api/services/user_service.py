"""Abstract application service for user lifecycle and bootstrap directories.

Implementations:
* :class:`src.services.user_service.UserServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.db.entities.user.user import UserEntity


@dataclass(eq=True, frozen=True)
class UserFilter:
    """Filter for "find me a user with these properties".

    Every field is :data:`UndefinedOr` so callers can leave fields
    unset.  Set fields are AND-ed -- the filter matches a user only
    when every set field equals the user's value for that field.

    The dataclass is frozen so it can be hashed (callers use it as
    a cache key in the auth layer) and ``eq=True`` is the dataclass
    default -- two filters with the same set fields compare equal.

    Attributes:
        user_id: target user's id.  :obj:`UNDEFINED` (the default)
            means "don't filter on user_id".
        email: target user's email.  :obj:`UNDEFINED` means "don't
            filter on email".
        discord_id: target user's Discord id.  :obj:`UNDEFINED`
            means "don't filter on discord_id".
    """

    user_id: UndefinedOr[str] = UNDEFINED
    email: UndefinedOr[str] = UNDEFINED
    discord_id: UndefinedOr[int] = UNDEFINED

    def is_empty(self) -> bool:
        """Return ``True`` when no field is set.

        Useful for callers that want to short-circuit an empty
        filter (``select_by_filter`` returns ``None`` for an empty
        filter by design).
        """
        return (
            is_undefined(self.user_id)
            and is_undefined(self.email)
            and is_undefined(self.discord_id)
        )

    def set_fields(self) -> List[str]:
        """Return the names of the fields that are currently set.

        Stable order so callers can compare two filters'
        :meth:`set_fields` results.
        """
        out: List[str] = []
        if not is_undefined(self.user_id):
            out.append("user_id")
        if not is_undefined(self.email):
            out.append("email")
        if not is_undefined(self.discord_id):
            out.append("discord_id")
        return out


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

    @abstractmethod
    async def update_user(self, user: UserEntity) -> UserEntity:
        """Persist partial updates to an existing user.

        Only fields whose value is not
        :obj:`~src.api.undefined.UNDEFINED` are written;
        ``None`` explicitly clears the underlying column.  No
        directory side effects -- this is the raw update path used
        by gRPC adapters that own their own validation
        (e.g. avatar-only writes from the auth service).

        Args:
            user: the user carrying ``id`` and the fields to
                update.  ``id`` must be set; the rest may be
                :obj:`~src.api.undefined.UNDEFINED` to leave the
                column untouched.

        Returns:
            UserEntity: the persisted user after the update.

        Raises:
            ValueError: when ``user.id`` is missing or the row no
                longer exists.
        """
        ...


__all__ = ["UserFilter", "UserServiceABC"]