from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.db.entities.shelf import ShelfEntity


class ShelfRepoABC(ABC):
    """Storage contract for shelves.

    The :class:`ShelfRepoABC` is the cross-layer contract the
    shelf service depends on; the concrete Postgres implementation
    lives under :mod:`src.db.repos.shelf.postgres`.  The repo
    deliberately does not consult SpiceDB; permission checks belong
    upstream in the service / facade layer.

    Tables touched by the Postgres implementation:

    * ``note.shelf`` -- the shelf row itself.
    * ``note.shelf_book`` -- the m2m shelf <-> book (directory) bridge.

    Implementations:
    * :class:`src.db.repos.shelf.postgres.PostgresShelfRepo`
    """

    # ---- shelf row CRUD -------------------------------------------------

    @abstractmethod
    async def insert_shelf(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> ShelfEntity:
        """Insert a new shelf row and return the persisted entity.

        Args:
            slug: machine-readable shelf slug (required; unique).
            display_name: optional display name; ``None`` clears
                it, :obj:`~src.api.undefined.UNDEFINED` is treated
                as "not supplied" and defaults to SQL NULL.
            description: optional description; same UNDEFINED / None
                semantics as ``display_name``.
            image_url: optional image URL; same semantics.
            readme_note_id: optional README pointer; same semantics.

        Returns:
            ShelfEntity: the inserted entity with its
            server-assigned id populated. Book bindings are NOT
            applied here -- callers layer those on top via
            :meth:`add_book` / :meth:`set_books_of`.

        Raises:
            RuntimeError: when the underlying database returns no
                row (insert silently failed).
        """
        ...

    @abstractmethod
    async def fetch_shelf(
        self,
        id: str,
        *,
        include_books: bool = False,
    ) -> Optional[ShelfEntity]:
        """Fetch a single shelf by id.

        Args:
            id: shelf id to load.
            include_books: when ``True`` populates the entity's
                :attr:`ShelfEntity.book_ids` from
                ``note.shelf_book`` in the same query.

        Returns:
            Optional[ShelfEntity]: the entity, or ``None`` when no
            row matches. ``book_ids`` is populated iff
            ``include_books=True``.
        """
        ...

    @abstractmethod
    async def fetch_shelves_by_ids(
        self,
        ids: List[str],
        *,
        include_books: bool = False,
    ) -> List[ShelfEntity]:
        """Fetch multiple shelves by id (one query).

        Args:
            ids: shelf ids to load. Empty list returns ``[]``
                (no query issued).
            include_books: when ``True`` populates each entity's
                :attr:`book_ids`.

        Returns:
            List[ShelfEntity]: matching shelves in input order.
        """
        ...

    @abstractmethod
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
        """Partially update a shelf row.

        Field-level semantics:
        * :obj:`~src.api.undefined.UNDEFINED` -- leave alone.
        * ``None`` -- set the column to SQL NULL.
        * concrete value -- overwrite the column.

        Args:
            id: shelf id to update (required).
            slug / display_name / description / image_url /
            readme_note_id: per-field updates using the
            UNDEFINED / None / value semantics above.

        Returns:
            Optional[ShelfEntity]: the updated entity without
            ``book_ids`` (callers layer that on top), or
            ``None`` when no row matched ``id``.

        Raises:
            ValueError: ``id`` is UNDEFINED / None, or ``slug``
                is ``None``.
        """
        ...

    @abstractmethod
    async def delete_shelf(self, id: str) -> bool:
        """Delete the shelf row (cascades ``note.shelf_book``).

        Args:
            id: shelf id to remove.

        Returns:
            bool: ``True`` when exactly one row was removed.

        Raises:
            ValueError: ``id`` is UNDEFINED / None.
        """
        ...

    # ---- shelf <-> book bindings ---------------------------------------

    @abstractmethod
    async def set_books_of(
        self,
        shelf_id: str,
        book_ids: List[str],
    ) -> None:
        """Replace the full book set of ``shelf_id`` with ``book_ids``.

        Args:
            shelf_id: id of the shelf to mutate.
            book_ids: full list of book (directory) ids that
                should sit on the shelf after this call; an
                empty list removes every binding. Idempotent.

        Note:
            The Postgres implementation uses a set-match approach:
            current bindings minus the desired set are deleted,
            and the new ones are inserted (with ``ON CONFLICT DO
            NOTHING``) so a no-op call is cheap and concurrency-safe.
        """
        ...

    @abstractmethod
    async def get_books_of(self, shelf_id: str) -> List[str]:
        """Return the book ids sitting on ``shelf_id``.

        Args:
            shelf_id: id of the shelf to inspect.

        Returns:
            List[str]: the book ids, sorted; ``[]`` when the
            shelf is empty.
        """
        ...

    @abstractmethod
    async def get_shelves_of_book(self, book_id: str) -> List[str]:
        """Return the shelf ids that contain ``book_id``.

        Args:
            book_id: id of the book (directory) to inspect.

        Returns:
            List[str]: the shelf ids, sorted; ``[]`` when the
            book sits on no shelf.
        """
        ...

    @abstractmethod
    async def add_book(
        self,
        shelf_id: str,
        book_id: str,
    ) -> None:
        """Add ``book_id`` to ``shelf_id``.

        Idempotent: a no-op when the binding already exists.
        """
        ...

    @abstractmethod
    async def remove_book(
        self,
        shelf_id: str,
        book_id: str,
    ) -> None:
        """Remove ``book_id`` from ``shelf_id``.

        A no-op when the binding does not exist.
        """
        ...

__all__ = ["ShelfRepoABC"]