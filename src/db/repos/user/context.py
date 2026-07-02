"""Repository-backed :class:`~src.api.user_context.UserContextABC` implementation.

A :class:`~src.db.repos.user.context.RepoUserContext` is created from a
`user_id` by a :class:`~src.db.repos.user.context.RepoContextFactory`.
The context holds the id, lazily fetches the
:class:`~src.db.entities.user.user.UserEntity` on demand, and caches the
entity's :class:`~src.api.user_context.UserTypeT` so that
:meth:`is_temporary_user` does not re-query the repo per call.
"""

from __future__ import annotations

from typing import Optional

from src.api.undefined import UNDEFINED, UndefinedOr
from src.api.user_context import ContextFactory, UserContextABC, UserTypeT
from src.db.entities.user.user import UserEntity
from src.utils.async_ttl import AsyncTtlCacheInfo, async_ttl

from .user import UserRepoABC


class RepoUserContext(UserContextABC):
    """User context backed by a :class:`~src.db.repos.user.user.UserRepoABC`.

    The user entity is fetched on the first call to
    :meth:`fetch_user` (or :meth:`is_temporary_user`); subsequent calls
    reuse the cached entity.

    Args:
        user_repo: repo used to look up the underlying user.
        user_id: id of the user making the current request.
    """

    def __init__(self, user_repo: UserRepoABC, user_id: str) -> None:
        self._user_repo = user_repo
        self._user_id = user_id
        self._user: Optional[UserEntity] = None

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def type(self) -> UndefinedOr[UserTypeT]:
        return self._type

    async def fetch_user(self) -> Optional[UserEntity]:
        """Lazily fetch and cache the :class:`~src.db.entities.user.user.UserEntity`.

        Returns the cached entity on subsequent calls.  Returns ``None``
        if the repo has no row for `user_id`; in that case the cached
        :attr:`type` stays :obj:`~src.api.undefined.UNDEFINED`.
        """
        if self._user is None:
            self._user = await self._user_repo.select(self._user_id)
        return self._user

    async def is_temporary_user(self) -> bool:
        """Return True iff the resolved user has ``type == "temporary"``."""
        if not self._user:
            await self.fetch_user()
        return  self._user.type == "temporary"


class RepoContextFactory(ContextFactory[UserContextABC]):
    """Factory that produces :class:`~src.db.repos.user.context.RepoUserContext` instances.

    Args:
        user_repo: repo injected into every context the factory builds.
    """

    def __init__(self, user_repo: UserRepoABC) -> None:
        self._user_repo = user_repo

    async def create(self, user_id: str) -> UserContextABC:
        """Build a fresh :class:`~src.db.repos.user.context.RepoUserContext` for `user_id`."""
        return RepoUserContext(self._user_repo, user_id)


class UnimplementedUserContext(UserContextABC):
    """Placeholder context for call sites that have no real user id.

    Used by share-access flows where the caller is anonymous.
    """

    def __init__(self) -> None:
        pass

    @property
    def user_id(self) -> str:
        raise NotImplementedError("User ID is not implemented in this context.")

    @property
    def type(self) -> UndefinedOr[UserTypeT]:
        return UNDEFINED

    async def is_temporary_user(self) -> bool:
        return False


class CachedRepoUserContextFactory(RepoContextFactory):
    """Factory that memoizes the :class:`RepoUserContext` it hands out.

    Each call to :meth:`create` returns the same context instance per
    `user_id` for `ttl_seconds` (default 15 minutes).

    Caching is delegated to :func:`src.utils.async_ttl.async_ttl`
    """

    def __init__(
        self,
        user_repo: UserRepoABC,
        ttl_seconds: float = 15 * 60,
    ) -> None:
        super().__init__(user_repo)
        self._ttl_seconds = ttl_seconds

        @async_ttl(ttl=ttl_seconds)
        async def _cached_create(user_id: str) -> RepoUserContext:
            return RepoUserContext(self._user_repo, user_id)

        self._cached_create = _cached_create

    async def create(self, user_id: str) -> UserContextABC:
        """Return the cached :class:`RepoUserContext` for `user_id`.

        On cache miss the parent factory builds a fresh context, which
        is then memoized for `ttl_seconds` so subsequent calls return
        the exact same instance.

        Args:
            user_id: id of the user making the current request.
        """
        return await self._cached_create(user_id)

    def cache_info(self) -> AsyncTtlCacheInfo:
        """Expose the underlying TTL cache stats for ops and tests."""
        return self._cached_create.cache_info()

    def cache_clear(self) -> None:
        """Drop every cached context."""
        self._cached_create.cache_clear()


__all__ = [
    "CachedRepoUserContextFactory",
    "RepoContextFactory",
    "RepoUserContext",
    "UnimplementedUserContext",
]