from __future__ import annotations

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.user_context import ActorAs, ContextFactory, UserContextABC, UserTypeT


class _DummyUserContext(UserContextABC):
    """Small user context for service tests.

    Defaults :attr:`type` to :obj:`~src.api.undefined.UNDEFINED`,
    :attr:`accessed_as` to ``"user"``, and :meth:`is_temporary_user`
    to False.  Tests that need to exercise the typed-user code path
    construct a :class:`src.db.repos.user.RepoUserContext` against a
    fake repo instead.
    """

    def __init__(
        self,
        user_id: str = "actor",
        accessed_as: ActorAs = "user",
        disabled: bool = False,
    ) -> None:
        self._user_id = user_id
        self._accessed_as = accessed_as
        self._disabled = disabled

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def type(self) -> UndefinedOr[UserTypeT]:
        return UNDEFINED

    @property
    def accessed_as(self) -> ActorAs:
        return self._accessed_as

    async def is_temporary_user(self) -> bool:
        return False

    async def is_disabled(self) -> bool:
        return self._disabled


__all__ = [
    '_DummyUserContext',
]
