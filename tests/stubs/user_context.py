"""Minimal :class:`UserContextABC` double for unit tests."""

from __future__ import annotations

from src.api.user_context import UserContextABC


class _UserContext(UserContextABC):
    """Small user context for service tests."""

    def __init__(self, user_id: str = "actor") -> None:
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


__all__ = ["_UserContext"]