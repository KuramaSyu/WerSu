"""In-memory :class:`UserRepoABC` fake for unit tests."""

from __future__ import annotations

from typing import List, Optional

from src.db.entities.user.user import UserEntity


class _FakeUserRepo:
    """Minimal user repo stub for sharing-service tests.

    Tracks inserts / deletes / selects so tests can assert on the
    temporary access user's lifecycle.
    """

    def __init__(self, users: Optional[List[UserEntity]] = None) -> None:
        self.inserted: List[UserEntity] = []
        self.deleted: List[str] = []
        self._store: dict[str, UserEntity] = {
            str(user.id): user for user in users or []
        }

    async def insert(self, user: UserEntity) -> UserEntity:
        self.inserted.append(user)
        if user.id is not None:
            self._store[str(user.id)] = user
        return user

    async def select(self, user_id: str) -> Optional[UserEntity]:
        return self._store.get(user_id)

    async def delete(self, user_id: str) -> bool:
        self.deleted.append(user_id)
        self._store.pop(user_id, None)
        return True


__all__ = ["_FakeUserRepo"]