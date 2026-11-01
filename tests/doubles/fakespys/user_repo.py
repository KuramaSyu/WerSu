from __future__ import annotations

from typing import List, Optional
from src.api.other.undefined import UNDEFINED
from src.db.entities.user.user import FilterUser, UserEntity


class _FakeSpyUserRepo:
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

    async def select(self, filter: FilterUser) -> Optional[UserEntity]:
        if filter.is_empty():
            return None
        for user in self._store.values():
            if (
                filter.user_id is not UNDEFINED
                and str(user.id) != str(filter.user_id)
            ):
                continue
            if (
                filter.username is not UNDEFINED
                and user.username != filter.username
            ):
                continue
            return user
        return None

    async def delete(self, user_id: str) -> bool:
        self.deleted.append(user_id)
        self._store.pop(user_id, None)
        return True


__all__ = [
    '_FakeSpyUserRepo',
]
