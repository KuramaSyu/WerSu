from __future__ import annotations

from src.api.errors import FriendlyLoginError
from src.db.entities.user.user_auth import UserAuthEntity
from typing import Any, Dict, List, Optional, cast


class _MockRaisingAuthService:
    """Stub that raises :class:`FriendlyLoginError` from create_user.

    Mimics the contract the real auth service exposes for the
    collision case without dragging in the rest of the
    UserAuthServiceImpl surface.  The gRPC adapter only needs
    ``create_user`` to be wired up for this test.
    """

    def __init__(self, field: str, value: str) -> None:
        self._field = field
        self._value = value

    async def create_user(self, user: UserAuthEntity) -> UserAuthEntity:
        raise FriendlyLoginError(self._field, self._value)

    async def update_user(self, *args: Any, **kwargs: Any) -> UserAuthEntity:
        raise FriendlyLoginError(self._field, self._value)

    async def get_user(self, *args: Any, **kwargs: Any) -> Any:
        return None


__all__ = [
    '_MockRaisingAuthService',
]
