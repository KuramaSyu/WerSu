"""Minimal :class:`UserContextABC` double for unit tests."""

from __future__ import annotations

from src.api.undefined import UNDEFINED, UndefinedOr
from src.api.user_context import ContextFactory, UserContextABC, UserTypeT


class _UserContext(UserContextABC):
    """Small user context for service tests.

    Defaults :attr:`type` to :obj:`~src.api.undefined.UNDEFINED` and
    :meth:`is_temporary_user` to False.  Tests that need to exercise the
    typed-user code path construct a :class:`src.db.repos.user.RepoUserContext`
    against a fake repo instead.
    """

    def __init__(self, user_id: str = "actor") -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def type(self) -> UndefinedOr[UserTypeT]:
        return UNDEFINED

    async def is_temporary_user(self) -> bool:
        return False


class _UserContextFactory(ContextFactory[UserContextABC]):
    """Builds :class:`_UserContext` instances."""

    async def create(self, user_id: str) -> UserContextABC:
        return _UserContext(user_id=user_id)


__all__ = ["_UserContext", "_UserContextFactory"]