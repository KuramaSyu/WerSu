"""In-memory :class:`ShelfRepoABC` fake for unit tests.

Stores shelf rows + ``shelf <-> book`` bindings in plain dicts so
the rule / directory / user service tests can exercise the
:class:`ShelfRepoABC` surface area without spinning up a Postgres
container.  Mirrors the behaviour of
:class:`src.db.repos.shelf.postgres.PostgresShelfRepo` for the
methods the tests actually call -- the rest fall through to
``raise NotImplementedError`` so any accidental new caller fails
loudly in CI.
"""

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
from src.api.repos.shelf_repo import ShelfRepoABC
from src.db.entities.shelf import ShelfEntity


class InMemoryShelfRepo(ShelfRepoABC):
    """In-memory :class:`ShelfRepoABC` for unit tests.

    Args:
        seeded: optional map of ``{shelf_id: {book_id, ...}}``
            to populate before a test starts -- lets individual
            tests pre-stage shelf <-> book bindings without
            calling :meth:`add_book`.
    """

    def __init__(
        self,
        seeded: Optional[Dict[str, Set[str]]] = None,
    ) -> None:
        self._shelves: Dict[str, ShelfEntity] = {}
        self._books_by_shelf: Dict[str, Set[str]] = {
            str(sid): set(books) for sid, books in (seeded or {}).items()
        }

    # ---- shelf row CRUD -------------------------------------------------

    async def insert_shelf(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> ShelfEntity:
        shelf_id = str(uuid.uuid4())
        entity = ShelfEntity(
            id=shelf_id,
            slug=slug,
            display_name=(
                unwrap_undefined_or(display_name, None)
            ),
            description=(
                unwrap_undefined_or(description, None)
            ),
            image_url=(
                unwrap_undefined_or(image_url, None)
            ),
            readme_note_id=(
                unwrap_undefined_or(readme_note_id, None)
            ),
            book_ids=UNDEFINED,
        )
        self._shelves[shelf_id] = entity
        self._books_by_shelf.setdefault(shelf_id, set())
        return entity

    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ) -> Optional[ShelfEntity]:
        entity = self._shelves.get(str(id))
        if entity is None:
            return None
        if include_books:
            entity.book_ids = sorted(self._books_by_shelf.get(str(id), set()))
        return entity

    async def fetch_shelves_by_ids(
        self,
        ids: List[str],
        *,
        include_books: bool = False,
    ) -> List[ShelfEntity]:
        out: List[ShelfEntity] = []
        for sid in ids:
            entity = self._shelves.get(str(sid))
            if entity is None:
                continue
            if include_books:
                entity.book_ids = sorted(
                    self._books_by_shelf.get(str(sid), set())
                )
            out.append(entity)
        return out

    async def update_shelf(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> Optional[ShelfEntity]:
        if is_undefined(id):
            raise ValueError("Shelf ID is required for update")
        existing = self._shelves.get(str(id))
        if existing is None:
            return None
        if not is_undefined(slug):
            existing.slug = str(slug)
        if not is_undefined(display_name):
            existing.display_name = unwrap_undefined_or(display_name, None)
        if not is_undefined(description):
            existing.description = unwrap_undefined_or(description, None)
        if not is_undefined(image_url):
            existing.image_url = unwrap_undefined_or(image_url, None)
        if not is_undefined(readme_note_id):
            existing.readme_note_id = unwrap_undefined_or(readme_note_id, None)
        return existing

    async def delete_shelf(self, id: str) -> bool:
        if id not in self._shelves:
            return False
        del self._shelves[str(id)]
        self._books_by_shelf.pop(str(id), None)
        return True

    # ---- shelf <-> book bindings ---------------------------------------

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
    ) -> None:
        self._books_by_shelf[str(shelf_id)] = {
            str(b) for b in book_ids if b
        }

    async def get_books_of(self, shelf_id: str) -> List[str]:
        return sorted(self._books_by_shelf.get(str(shelf_id), set()))

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        return sorted(
            sid for sid, books in self._books_by_shelf.items()
            if str(book_id) in books
        )

    async def add_book(self, shelf_id: str, book_id: str) -> None:
        self._books_by_shelf.setdefault(str(shelf_id), set()).add(str(book_id))

    async def remove_book(self, shelf_id: str, book_id: str) -> None:
        bucket = self._books_by_shelf.get(str(shelf_id))
        if bucket is None:
            return
        bucket.discard(str(book_id))


class NoopShelfRepo(ShelfRepoABC):
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
    ) -> ShelfEntity:
        raise NotImplementedError("NoopShelfRepo does not support inserts")

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
    ) -> Optional[ShelfEntity]:
        return None

    async def delete_shelf(self, id: str) -> bool:
        return False

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
    ) -> None:
        return None

    async def get_books_of(self, shelf_id: str) -> List[str]:
        return []

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        return []

    async def add_book(self, shelf_id: str, book_id: str) -> None:
        return None

    async def remove_book(self, shelf_id: str, book_id: str) -> None:
        return None


__all__ = ["InMemoryShelfRepo", "NoopShelfRepo"]
