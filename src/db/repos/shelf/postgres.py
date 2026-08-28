"""Postgres implementation of :class:`ShelfRepoABC`.

Every SQL statement against ``note.shelf`` and ``note.shelf_book``
lives here so the ABC consumers never see raw SQL.

Tables touched:

* ``note.shelf`` -- the shelf row itself.
* ``note.shelf_book`` -- the m2m shelf <-> book (directory) bridge.

The shelf row mirrors the directory row's metadata columns
(slug, display_name, description, image_url, readme_note_id) so
the directory service's README-pointer overlay behaviour can be
reused for shelves with no extra wiring.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set

import asyncpg  # type: ignore[import]

from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    unwrap_undefined_or,
)
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api import LoggingProvider
from src.db.entities.shelf import ShelfEntity
from src.db.table import TableABC


class PostgresShelfRepo(ShelfRepoABC):
    """Postgres implementation of the shelf storage contract.

    Args:
        shelf_table: :class:`TableABC` over ``note.shelf``.
        shelf_book_table: :class:`TableABC` over
            ``note.shelf_book``.
    """

    _SHELF_COLUMNS = (
        "id, slug, display_name, description, image_url, readme_note_id"
    )

    #: How many collision-retry attempts the slug-suffixer
    #: gets before giving up.  ``-2`` through ``-N`` so the
    #: default behaviour handles up to 100 simultaneous users
    #: sharing a slug; bump when you actually need more.
    MAX_SLUG_RETRY = 100

    def __init__(
        self,
        shelf_table: TableABC,
        shelf_book_table: TableABC,
        logging_provider: LoggingProvider,
    ) -> None:
        self._shelf_table = shelf_table
        self._shelf_book_table = shelf_book_table
        self.log = logging_provider(__name__, self)

    @property
    def shelf_table(self) -> TableABC:
        """Return the ``note.shelf`` :class:`TableABC`."""
        return self._shelf_table

    @property
    def shelf_book_table(self) -> TableABC:
        """Return the ``note.shelf_book`` :class:`TableABC`."""
        return self._shelf_book_table

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
        """Insert a shelf row and return the persisted entity.

        Slugs are unique per-deployment.  When a collision
        occurs (e.g. two users with the same username), the
        insert retries with ``<slug>-2``, ``<slug>-3``, ... up
        to :data:`MAX_SLUG_RETRY` attempts before raising.
        """
        import asyncpg  # local import keeps the module top-level clean
        candidate = slug
        for attempt in range(1, self.MAX_SLUG_RETRY + 1):
            try:
                rows = await self._shelf_table.insert(
                    {
                        "slug": candidate,
                        "display_name": self._resolve_undefined_none(display_name),
                        "description": self._resolve_undefined_none(description),
                        "image_url": self._resolve_undefined_none(image_url),
                        "readme_note_id": self._resolve_undefined_none(readme_note_id),
                    },
                    returning=self._SHELF_COLUMNS,
                )
                self.log.debug(f"DEBUG shelf insert attempt={attempt} candidate={candidate!r} rows={rows}")
            except Exception as exc:
                self.log.debug(f"DEBUG shelf EXC attempt={attempt} candidate={candidate!r} exc={type(exc).__name__}: {exc}")
                if isinstance(exc, asyncpg.UniqueViolationError):
                    candidate = f"{slug}-{attempt + 1}"
                    continue
                raise
            if not rows:
                raise RuntimeError("Failed to create shelf")
            return self._row_to_entity(rows[0])
        raise RuntimeError(
            f"Could not insert shelf: slug {slug!r} collided "
            f"with {self.MAX_SLUG_RETRY} attempts"
        )

    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ) -> Optional[ShelfEntity]:
        """Fetch a shelf row by id, optionally with book ids."""
        record = await self._shelf_table.fetch_by_id(str(id))
        if not record:
            return None
        entity = self._row_to_entity(record)
        if include_books:
            entity.book_ids = await self.get_books_of(str(id))
        return entity

    async def fetch_shelves_by_ids(
        self,
        ids: List[str],
        *,
        include_books: bool = False,
    ) -> List[ShelfEntity]:
        """Fetch many shelves in one query, optionally with book ids."""
        if not ids:
            return []
        records = await self._shelf_table.fetch(
            f"""
            SELECT {self._SHELF_COLUMNS}
            FROM {self.shelf_table.name}
            WHERE id = ANY($1)
            """,
            ids,
        )
        entities = [self._row_to_entity(r) for r in records or []]
        if not include_books:
            return entities
        # Bulk-load the book ids for every shelf in one query,
        # then bucket by shelf id.  Avoids N+1 round-trips.
        rows = await self._shelf_book_table.fetch(
            f"""
            SELECT shelf_id, book_id
            FROM {self.shelf_book_table.name}
            WHERE shelf_id = ANY($1)
            """,
            [str(e.id) for e in entities if e.id],
        )
        books_by_shelf: Dict[str, List[str]] = {}
        for row in rows or []:
            shelf_id = str(_row_get(row, "shelf_id"))
            book_id = str(_row_get(row, "book_id"))
            if book_id:
                books_by_shelf.setdefault(shelf_id, []).append(book_id)
        for entity in entities:
            entity.book_ids = sorted(books_by_shelf.get(str(entity.id), []))
        return entities

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
        """Partially update a shelf with UNDEFINED / None semantics."""
        if is_undefined(id):
            raise ValueError("Shelf ID is required for update")

        sets: dict[str, object] = {}
        if not is_undefined(slug):
            sets["slug"] = str(slug)
        if not is_undefined(display_name):
            sets["display_name"] = unwrap_undefined_or(display_name, None)
        if not is_undefined(description):
            sets["description"] = unwrap_undefined_or(description, None)
        if not is_undefined(image_url):
            sets["image_url"] = unwrap_undefined_or(image_url, None)
        if not is_undefined(readme_note_id):
            sets["readme_note_id"] = (
                None if readme_note_id is None else str(readme_note_id)
            )

        if sets:
            await self._shelf_table.update(
                set=sets, where={"id": str(id)}, returning=""
            )

        record = await self._shelf_table.fetch_by_id(str(id))
        if not record:
            return None
        return self._row_to_entity(record)

    async def delete_shelf(self, id: str) -> bool:
        """Delete the shelf row."""
        if is_undefined(id):
            raise ValueError("Shelf ID is required for deletion")
        records = await self._shelf_table.delete({"id": str(id)})
        return bool(records)

    # ---- shelf <-> book bindings ---------------------------------------

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
    ) -> None:
        """Set-match the full book set; cheap when nothing changes."""
        current = set(await self.get_books_of(str(shelf_id)))
        desired = {str(b) for b in book_ids if b}

        for old in current - desired:
            await self._shelf_book_table.delete(
                {"shelf_id": str(shelf_id), "book_id": old}
            )
        for new_book in desired.difference(current):
            await self._shelf_book_table.insert(
                {"shelf_id": str(shelf_id), "book_id": new_book},
                on_conflict="DO NOTHING",
            )

    async def get_books_of(self, shelf_id: str) -> List[str]:
        """Return sorted book ids sitting on ``shelf_id``."""
        records = await self._shelf_book_table.select(
            where={"shelf_id": str(shelf_id)},
            select="book_id",
        )
        return sorted(
            str(_row_get(r, "book_id"))
            for r in records or []
            if _row_get(r, "book_id")
        )

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        """Return sorted shelf ids that contain ``book_id``."""
        records = await self._shelf_book_table.select(
            where={"book_id": str(book_id)},
            select="shelf_id",
        )
        return sorted(
            str(_row_get(r, "shelf_id"))
            for r in records or []
            if _row_get(r, "shelf_id")
        )

    async def add_book(self, shelf_id: str, book_id: str) -> None:
        """Idempotently add ``book_id`` to ``shelf_id``."""
        await self._shelf_book_table.insert(
            {"shelf_id": str(shelf_id), "book_id": str(book_id)},
            on_conflict="DO NOTHING",
        )

    async def remove_book(self, shelf_id: str, book_id: str) -> None:
        """Remove ``book_id`` from ``shelf_id`` (no-op if absent)."""
        await self._shelf_book_table.delete(
            {"shelf_id": str(shelf_id), "book_id": str(book_id)}
        )

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _resolve_undefined_none(value: UndefinedNoneOr[str]) -> Optional[str]:
        """Map a nullable ``UndefinedNoneOr`` into a SQL-friendly value."""
        if is_undefined(value):
            return None
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _row_to_entity(row: object) -> ShelfEntity:
        """Map one ``note.shelf`` record to a :class:`ShelfEntity`."""
        return ShelfEntity(
            id=(
                str(_row_get(row, "id"))
                if _row_get(row, "id") is not None
                else UNDEFINED
            ),
            slug=(
                str(_row_get(row, "slug"))
                if _row_get(row, "slug") is not None
                else None
            ),
            display_name=(
                str(_row_get(row, "display_name"))
                if _row_get(row, "display_name") is not None
                else None
            ),
            description=(
                str(_row_get(row, "description"))
                if _row_get(row, "description") is not None
                else None
            ),
            image_url=(
                str(_row_get(row, "image_url"))
                if _row_get(row, "image_url") is not None
                else None
            ),
            readme_note_id=(
                str(_row_get(row, "readme_note_id"))
                if _row_get(row, "readme_note_id") is not None
                else None
            ),
            book_ids=UNDEFINED,
        )


def _row_get(row: object, key: str) -> object:
    """Read ``key`` from an asyncpg Record or a plain dict."""
    if isinstance(row, asyncpg.Record):
        return row.get(key)  # type: ignore[dict-item]
    if isinstance(row, dict):
        return row.get(key)
    raise TypeError(f"Unsupported row type: {type(row)}")


__all__ = ["PostgresShelfRepo"]