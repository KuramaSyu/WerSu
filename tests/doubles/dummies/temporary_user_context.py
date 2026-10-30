from __future__ import annotations

from tests.doubles.dummies.user_ctx import _DummyUserCtx


class _DummyTemporaryUserContext(_DummyUserCtx):
    """`_UserContext` whose `is_temporary_user()` returns True."""

    async def is_temporary_user(self) -> bool:
        return True


__all__ = [
    '_DummyTemporaryUserContext',
]
