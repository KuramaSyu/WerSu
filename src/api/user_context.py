"""Abstract base for the caller identity passed into the service layer.

The :class:`UserContextABC` carries the id of the user making the
current request and exposes the user's type (when known) so callers
can branch on it without re-fetching the user entity.  Implementations
can attach more data (e.g. roles or session info) but the contract only
exposes the id, type and the temporary-user predicate.
"""

from abc import ABC, abstractmethod
from typing import Generic, Literal, TypeVar

from src.api.undefined import UNDEFINED, UndefinedOr


UserTypeT = Literal["human", "temporary", "system"]
"""Allowed values for :attr:`UserContextABC.type`."""


class UserContextABC(ABC):
    """Identity of the caller for a single request.

    Implementations:
    * :class:`~src.db.repos.user.context.RepoUserContext`
    * :class:`~src.db.repos.user.context.UnimplementedUserContext`
    """

    @property
    @abstractmethod
    def user_id(self) -> str:
        """Return the id of the user making the current request."""
        ...

    @property
    @abstractmethod
    def type(self) -> UndefinedOr[UserTypeT]:
        """Return the cached user type, or :obj:`~src.api.undefined.UNDEFINED` when not yet fetched."""
        ...

    @abstractmethod
    async def is_temporary_user(self) -> bool:
        """Return True when the user has been fetched and is flagged ``"temporary"``.
        Currently used for temporary users to access a note share.
        """
        ...


T = TypeVar("T", bound=UserContextABC)


class ContextFactory(ABC, Generic[T]):
    """Abstract factory that materializes :class:`UserContextABC` instances on demand.

    Implementations:
    * :class:`~src.db.repos.user.context.RepoContextFactory`
    """

    @abstractmethod
    async def create(self, user_id: str) -> T:
        """Build a context for ``user_id``.

        Args:
            user_id: id of the user making the current request.

        Returns:
            A concrete :class:`UserContextABC` subtype.
        """
        ...


__all__ = [
    "ContextFactory",
    "UNDEFINED",
    "UserContextABC",
    "UserTypeT",
]


