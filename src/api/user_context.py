"""Abstract base for the caller identity passed into the service layer.

The :class:`UserContextABC` carries the id of the user making the
current request and exposes the user's type (when known) so callers
can branch on it without re-fetching the user entity.  Implementations
can attach more data (e.g. roles or session info) but the contract only
exposes the id, type, ``accessed_as`` (user vs system) and the
temporary-user predicate.
"""

from abc import ABC, abstractmethod
from typing import Literal, TypeVar

from src.api.undefined import UNDEFINED, UndefinedOr


UserTypeT = Literal["human", "temporary", "system"]
"""Allowed values for :attr:`UserContextABC.type`."""


ActorAs = Literal["user", "system"]
"""Whether the actor is acting as the user or the system on their behalf."""


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

    @property
    def accessed_as(self) -> ActorAs:
        """Return whether this context is acting as the user or the system.

        Defaults to ``"user"``.  Override in subclasses when the
        context represents a system actor (e.g. the
        :class:`~src.db.repos.user.context.UnimplementedUserContext`
        which has no real user id and defaults to ``"system"``).
        """
        return "user"

    @abstractmethod
    async def is_temporary_user(self) -> bool:
        """Return True when the user has been fetched and is flagged ``"temporary"``.
        Currently used for temporary users to access a note share.
        """
        ...

    def as_system(self) -> "UserContextABC":
        """Return a new context with the same identity but ``accessed_as == "system"``.

        Used when a service propagates a user context but wants to
        flag downstream actions as system-initiated (e.g. a cron
        job acting in the user's name).  The original context is
        not mutated.
        """
        return _SystemUserContext(self)


class _SystemUserContext(UserContextABC):
    """Decorator that flips ``accessed_as`` to ``"system"`` while preserving the rest."""

    def __init__(self, inner: UserContextABC) -> None:
        self._inner = inner

    @property
    def user_id(self) -> str:
        return self._inner.user_id

    @property
    def type(self) -> UndefinedOr[UserTypeT]:
        return self._inner.type

    @property
    def accessed_as(self) -> ActorAs:
        return "system"

    async def is_temporary_user(self) -> bool:
        return await self._inner.is_temporary_user()


T = TypeVar("T", bound=UserContextABC)


class ContextFactory[T](ABC):
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
    "ActorAs",
    "ContextFactory",
    "UNDEFINED",
    "UserContextABC",
    "UserTypeT",
]