"""Postgres implementation of :class:`ShelfRepoABC`.

Every SQL statement against ``note.shelf`` and ``note.shelf_book``
lives here so the ABC consumers never see raw SQL.  The repo is a
pure storage adapter -- SpiceDB edges are layered on by
:class:`~src.db.repos.shelf.spicedb_decorator.SpicedbShelfRepoDecorator`
which wraps an instance of this class at the composition root.

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

from src.api.other.undefined import (
    UNDEFINED,
    UndefinedNoneOr,
    UndefinedOr,
    is_undefined,
    resolve_undefined_none,
    unwrap_undefined_or,
)
from src.api.other.user_context import UserContextABC
from src.api.repos.shelf_repo import ShelfRepoABC
from src.api import LoggingProvider
from src.db.entities.shelf import ShelfEntity
from src.db.table import TableABC
from src.utils import row_get


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
        user_ctx: Optional[UserContextABC] = None,
    ) -> ShelfEntity:
        """Insert a shelf row and return the persisted entity.

        Slugs are unique per-deployment.  When a collision
        occurs (e.g. two users with the same username), the
        insert retries with ``<slug>-2``, ``<slug>-3``, ... up
        to :data:`MAX_SLUG_RETRY` attempts before raising.

        ``user_ctx`` is accepted for ABC compliance; this
        storage adapter does not write SpiceDB edges -- the
        :class:`~src.db.repos.shelf.spicedb_decorator.SpicedbShelfRepoDecorator`
        handles auth.  When the decorator is in the call
        chain, ``user_ctx`` is consumed there before this
        method sees it.
        """
        import asyncpg  # local import keeps the module top-level clean
        candidate = slug
        for attempt in range(1, self.MAX_SLUG_RETRY + 1):
            try:
                rows = await self._shelf_table.insert(
                    {
                        "slug": candidate,
                        "display_name": resolve_undefined_none(display_name),
                        "description": resolve_undefined_none(description),
                        "image_url": resolve_undefined_none(image_url),
                        "readme_note_id": resolve_undefined_none(readme_note_id),
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
        user_ctx: Optional[UserContextABC] = None,
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

    async def delete_shelf(
        self,
        id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> bool:
        """Delete the shelf row.

        ``user_ctx`` is accepted for ABC compliance; this
        storage adapter does not write SpiceDB edges.
        """
        if is_undefined(id):
            raise ValueError("Shelf ID is required for deletion")
        records = await self._shelf_table.delete({"id": str(id)})
        return bool(records)

    # ---- shelf <-> book bindings ---------------------------------------

    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> List[str]:
        """Set-match the full book set; cheap when nothing changes.

        Returns the list of *newly added* book ids so callers
        -- typically the
        :class:`~src.db.repos.shelf.spicedb_decorator.SpicedbShelfRepoDecorator`
        -- can scope auth grants to the diff.
        """
        current = set(await self.get_books_of(str(shelf_id)))
        desired = {str(b) for b in book_ids if b}

        for old in current - desired:
            await self._shelf_book_table.delete(
                {"shelf_id": str(shelf_id), "book_id": old}
            )
        newly_added: List[str] = []
        for new_book in desired.difference(current):
            await self._shelf_book_table.insert(
                {"shelf_id": str(shelf_id), "book_id": new_book},
                on_conflict="DO NOTHING",
            )
            newly_added.append(new_book)
        return newly_added

    async def get_books_of(self, shelf_id: str) -> List[str]:
        """Return sorted book ids sitting on ``shelf_id``."""
        records = await self._shelf_book_table.select(
            where={"shelf_id": str(shelf_id)},
            select="book_id",
        )
        return sorted(
            str(row_get(r, "book_id"))
            for r in records or []
            if row_get(r, "book_id")
        )

    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        """Return sorted shelf ids that contain ``book_id``."""
        records = await self._shelf_book_table.select(
            where={"book_id": str(book_id)},
            select="shelf_id",
        )
        return sorted(
            str(row_get(r, "shelf_id"))
            for r in records or []
            if row_get(r, "shelf_id")
        )

    async def add_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        """Idempotently add ``book_id`` to ``shelf_id``.

        ``user_ctx`` is accepted for ABC compliance; this
        storage adapter does not write SpiceDB edges.
        """
        await self._shelf_book_table.insert(
            {"shelf_id": str(shelf_id), "book_id": str(book_id)},
            on_conflict="DO NOTHING",
        )

    async def remove_book(
        self,
        shelf_id: str,
        book_id: str,
        *,
        user_ctx: Optional[UserContextABC] = None,
    ) -> None:
        """Remove ``book_id`` from ``shelf_id`` (no-op if absent).

        ``user_ctx`` is accepted for ABC compliance; this
        storage adapter does not write SpiceDB edges.
        """
        await self._shelf_book_table.delete(
            {"shelf_id": str(shelf_id), "book_id": str(book_id)}
        )

    # ---- helpers -------------------------------------------------------

    @staticmethod
    def _row_to_entity(row: object) -> ShelfEntity:
        """Map one ``note.shelf`` record to a :class:`ShelfEntity`."""
        return ShelfEntity(
            id=(
                str(row_get(row, "id"))
                if row_get(row, "id") is not None
                else UNDEFINED
            ),
            slug=(
                str(row_get(row, "slug"))
                if row_get(row, "slug") is not None
                else None
            ),
            display_name=(
                str(row_get(row, "display_name"))
                if row_get(row, "display_name") is not None
                else None
            ),
            description=(
                str(row_get(row, "description"))
                if row_get(row, "description") is not None
                else None
            ),
            image_url=(
                str(row_get(row, "image_url"))
                if row_get(row, "image_url") is not None
                else None
            ),
            readme_note_id=(
                str(row_get(row, "readme_note_id"))
                if row_get(row, "readme_note_id") is not None
                else None
            ),
            book_ids=UNDEFINED,
        )


__all__ = ["PostgresShelfRepo"]