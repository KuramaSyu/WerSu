from __future__ import annotations

import uuid
from typing import Dict, List, Optional, Set
from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    unwrap_undefined_or,
)
from src.api.other.user_context import UserContextABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.entities.shelf import ShelfEntity


class _DummyShelfRepo(ShelfRepoABC):
    """No-op :class:`ShelfRepoABC` for callers that don't need shelves.

    Every shelf lookup returns ``None`` / ``[]``; every
    shelf-modifying method is either a no-op or raises
    ``NotImplementedError`` for inserts that have no meaningful
    no-op equivalent.

    Lives in ``tests/stubs/`` because it is purely a test /
    scaffolding convenience -- production code talks to
    :class:`src.db.repos.shelf.postgres.PostgresShelfRepo`.
    """

    async def insert_shelf(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
        user_ctx: Optional[UserContextABC] = None,
    ) -> ShelfEntity:
        del user_ctx  # accepted for ABC parity
        raise NotImplementedError("_DummyShelfRepo does not support inserts")

    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ) -> Optional[ShelfEntity]:
        return None

    async def fetch_shelves_by_ids(
        self,
        ids: List[str],
        *,
        include_books: bool = False,
    ) -> List[ShelfEntity]:
        return []

    async def update_shelf(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
        user_ctx: Optional[UserContextABC] = None,
    ) -> Optional[ShelfEntity]:
        del user_ctx  # accepted for ABC parity
        return None

    async def delete_shelf(
        self,
        id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> bool:
        del user_ctx  # accepted for ABC parity
        return False

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> List[str]:
        del user_ctx  # accepted for ABC parity
        return []

    async def get_books_of(self, shelf_id: str) -> List[str]:
        return []

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        return []

    async def add_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        del user_ctx  # accepted for ABC parity
        return None

    async def remove_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        del user_ctx  # accepted for ABC parity
        return None


__all__ = [
    '_DummyShelfRepo',
]
