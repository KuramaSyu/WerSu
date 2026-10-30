from __future__ import annotations

from src.api.other.user_context import UserContextABC
from src.api.other.undefined import UNDEFINED
from src.api.other.undefined import UNDEFINED, unwrap_undefined


class _DummyUserCtx(UserContextABC):
    """Minimal :class:`UserContextABC` for tests."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def type(self):  # type: ignore[override]
        return UNDEFINED

    async def is_temporary_user(self) -> bool:
        return False


__all__ = [
    '_DummyUserCtx',
]
