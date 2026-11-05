"""Stub :class:`UserServiceABC` for gRPC adapter tests.

Records the arguments passed to ``search_users`` and returns a fixed
result set: enough to exercise the proto -> adapter -> visitor path
without touching Postgres.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.api.services.user_service import USER_SEARCH_LIMIT, UserServiceABC
from src.db.entities.user.user import UserEntity


class _StubUserService(UserServiceABC):
    """Records ``search_users`` calls and returns a fixed result set."""

    def __init__(self, results: Optional[List[UserEntity]] = None) -> None:
        self.results = results or []
        self.calls: List[tuple[str, str, int]] = []

    async def get_user(
        self,
        user_id: Optional[str] = None,
        discord_id: Optional[int] = None,
    ) -> Optional[UserEntity]:
        return None

    async def create_user(self, user: UserEntity) -> UserEntity:
        return user

    async def update_user(self, user: UserEntity) -> UserEntity:
        return user

    async def search_users(
        self,
        query: str,
        user_ctx: UserContextABC,
        limit: int = USER_SEARCH_LIMIT,
    ) -> List[UserEntity]:
        self.calls.append((query, user_ctx.user_id, limit))
        return self.results


__all__ = [
    '_StubUserService',
]