from __future__ import annotations

from src.api.other.undefined import UNDEFINED, UndefinedOr
from src.api.other.user_context import ActorAs, ContextFactory, UserContextABC, UserTypeT
from tests.doubles.dummies.user_context import _DummyUserContext


class _StubUserContextFactory(ContextFactory[UserContextABC]):
    """Builds :class:`_DummyUserContext` instances."""

    async def create(self, user_id: str) -> UserContextABC:
        return _DummyUserContext(user_id=user_id)


__all__ = [
    '_StubUserContextFactory',
]
