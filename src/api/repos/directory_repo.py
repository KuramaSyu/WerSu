from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Optional, TypeAlias

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.services.directory_service import DirectoryIncludeOptions
from src.db.entities.directory.directory import DirectoryEntity


# ---- type aliases ---------------------------------------------------------

DirectoryHierarchyType: TypeAlias = Literal["note", "directory", "both"]
"""Child/parent query scope for directory hierarchy lookups.

Used by the **read** helpers
(:meth:`DirectoryHelperMixin.get_children_of`,
:meth:`DirectoryHelperMixin.get_parents_of`,
:meth:`DirectoryHelperMixin.get_children_for`,
:meth:`DirectoryHelperMixin.get_parents_for`) where the caller
may be querying "notes, directories, or both" as children /
parents.
"""

DirectoryChildType: TypeAlias = Literal["note", "directory"]
"""Child-kind scope for add/remove/binding operations.

Notes are not allowed as children of a shelf; see
:class:`DirectoryParentType`.
"""

DirectoryParentType: TypeAlias = Literal["directory", "shelf"]
"""Parent-kind scope for add/remove/binding operations.

A parent can be a book (directory) or a shelf.  Shelves are
flat -- they cannot be nested -- so a shelf only ever hosts
books, never notes or other shelves.
"""


# ---- hierarchy helpers ----------------------------------------------------


class DirectoryHelperMixin(ABC):
    """Hierarchy helpers for directories and shelves.

    Consumers:
    * :class:`DirectoryRepoABC` low-level Postgres storage.
    * :class:`~src.api.facades.directory_facade.DirectoryFacadeABC`
        higher-level facade composing Postgres + SpiceDB.

    Naming convention:

    * ``*_parents_*`` -- the parents of a child
      (``child_type -> parent_type``).
    * ``*_children_*`` -- the children of a parent
      (``parent_type -> child_type``).

    Every method takes the type(s) explicitly so callers never
    have to guess what an id refers to.
    """

    @abstractmethod
    async def set_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
    ) -> None:
        """Replace the entire parent set for a note or directory.

        Args:
            child_type: the type of the child whose parents are
                being rewritten (``"note"`` or ``"directory"``).
            child_id: id of the child.
            parent_type: the parent kind (``"directory"`` or
                ``"shelf"``); every id in ``parent_ids`` must be
                of this type.  Mixing directory + shelf parents
                in a single call is **not** supported -- use two
                separate calls.
            parent_ids: full list of parent ids; an empty list
                removes every binding.  Idempotent.
        """
        ...

    @abstractmethod
    async def get_parents_of(
        self,
        child_type: DirectoryChildType,
        child_id: str,
        parent_type: DirectoryParentType,
    ) -> List[str]:
        """Return the parent ids of ``child_id`` (of the given type).

        Args:
            child_type: the type of ``child_id`` (``"note"`` or
                ``"directory"``).
            child_id: id whose parents to enumerate.
            parent_type: the parent kind to filter on
                (``"directory"`` or ``"shelf"``).

        Returns:
            List[str]: parent ids of the requested type,
            deduplicated and sorted.  ``[]`` when there are none.
        """
        ...

    @abstractmethod
    async def get_children_of(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> List[str]:
        """Return the child ids under ``parent_id``.

        Args:
            parent_type: kind of ``parent_id`` (``"directory"`` or
                ``"shelf"``).
            parent_id: id of the starting parent.
            child_type: kind of children to return --
                ``"note"`` / ``"directory"`` / ``"both"``.
            depth: recursion depth; ``1`` means direct children
                only.  ``depth=0`` returns ``[]`` (the parent
                itself is never returned).

        Returns:
            List[str]: matching child ids, deduplicated and
            sorted.  ``[]`` when there are none.

        Raises:
            ValueError: ``depth`` is negative, or
            ``(parent_type, child_type)`` is invalid
            (e.g. ``("shelf", "note")`` -- shelves do not host
            notes directly).
        """
        ...

    @abstractmethod
    async def get_children_for(
        self,
        parent_type: DirectoryParentType,
        parent_ids: List[str],
        child_type: DirectoryHierarchyType,
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        """Return child ids for multiple parents, keyed by input id.

        Args:
            parent_type: kind of every id in ``parent_ids``.
            parent_ids: starting parent ids.
            child_type: kind of children to return.
            depth: recursion depth; ``1`` means direct children only.

        Returns:
            Dict[str, List[str]]: mapping from each input
            ``parent_id`` to its matching child ids,
            deduplicated and sorted.  Parents without children
            map to ``[]``.  Empty input returns ``{}``.

        Raises:
            ValueError: ``depth`` is negative, or the
            ``(parent_type, child_type)`` pair is invalid.
        """
        ...

    @abstractmethod
    async def get_parents_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
        parent_type: DirectoryParentType,
    ) -> Dict[str, List[str]]:
        """Return parent ids for multiple child ids, keyed by input id.

        Args:
            child_type: kind of every id in ``child_ids``.
            child_ids: ids of the child objects to inspect.
            parent_type: parent kind to filter on.

        Returns:
            Dict[str, List[str]]: mapping from each input
            ``child_id`` to its matching parent ids,
            deduplicated and sorted.  ``child_id``s without
            parents map to ``[]``.  Empty input returns ``{}``.
        """
        ...

    @abstractmethod
    async def add_child_to(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Add a note or directory as a child of a directory or shelf.

        Args:
            parent_type: ``"directory"`` or ``"shelf"``.
            parent_id: id of the parent.
            child_type: ``"note"`` or ``"directory"``.
            child_id: id of the child note or directory.

        Note:
            Idempotent: a no-op when the binding already exists.

        Raises:
            ValueError: ``(parent_type, child_type)`` is invalid
            (e.g. ``("shelf", "note")``).
        """
        ...

    @abstractmethod
    async def remove_child_from(
        self,
        parent_type: DirectoryParentType,
        parent_id: str,
        child_type: DirectoryChildType,
        child_id: str,
    ) -> None:
        """Remove a note or directory binding from a directory or shelf.

        Args:
            parent_type: ``"directory"`` or ``"shelf"``.
            parent_id: id of the parent.
            child_type: ``"note"`` or ``"directory"``.
            child_id: id of the child note or directory.
        """
        ...


# ---- directory repo ABC ---------------------------------------------------


class DirectoryRepoABC(DirectoryHelperMixin):
    """Low-level Postgres storage contract for directories.

    Implements:
        * :class:`DirectoryHelperMixin` -- the hierarchy helpers
          (``set_parents_of``, ``get_parents_of`` /
          ``_for``, ``get_children_of`` / ``_for``,
          ``add_child_to``, ``remove_child_from``).

    Concrete:
        * :class:`src.db.repos.directory.postgres.PostgresDirectoryRepo`

    Note:
        Shelf CRUD + shelf<->book bindings live on
        :class:`src.api.repos.shelf_repo.ShelfRepoABC`.  This
        class only covers directory rows and the
        directory/directory + directory/note graphs.
    """

    @abstractmethod
    async def insert_directory(
        self,
        *,
        slug: str,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> DirectoryEntity:
        """Insert a new directory row and return the persisted entity.

        Args:
            slug: machine-readable directory slug (required).
            display_name: optional display name; ``None`` clears it,
                :obj:`~src.api.undefined.UNDEFINED` is treated as
                "not supplied" and defaults to SQL NULL.
            description: optional description; same UNDEFINED / None
                semantics as ``display_name``.
            image_url: optional image URL; same semantics.
            readme_note_id: optional README pointer; same semantics.

        Returns:
            DirectoryEntity: the inserted entity with its
            server-assigned id populated. Hierarchy, parent,
            child and tag bindings are NOT applied here -- callers
            layer those on top.

        Raises:
            RuntimeError: when the underlying database returns no
                row (insert silently failed).
        """
        ...

    @abstractmethod
    async def fetch_directory(
        self,
        id: str,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        """Fetch a single directory by id, optionally hydrated.

        Args:
            id: directory id to load.
            include: opt-in enrichment flags; see
                :class:`~src.api.directory_service.DirectoryIncludeOptions`.
                When omitted (or every flag ``False``) only the row
                is fetched and the list/count fields stay at
                :obj:`~src.api.undefined.UNDEFINED`.

        Returns:
            Optional[DirectoryEntity]: the entity, or ``None``
            when no row matches ``id``.  Row columns are always
            populated; list / count fields are populated iff their
            flag was set.
        """
        ...

    @abstractmethod
    async def fetch_directories_by_ids(
        self,
        ids: List[str],
    ) -> List[DirectoryEntity]:
        """Fetch multiple directory rows in one query (no enrichment).

        Args:
            ids: directory ids to load.  Empty list returns the
                empty list (no query is issued).

        Returns:
            List[DirectoryEntity]: the matching entities without
            hierarchy / parents / children / tags.
        """
        ...

    @abstractmethod
    async def update_directory(
        self,
        id: str,
        *,
        slug: UndefinedOr[str] = UNDEFINED,
        display_name: UndefinedNoneOr[str] = UNDEFINED,
        description: UndefinedNoneOr[str] = UNDEFINED,
        image_url: UndefinedNoneOr[str] = UNDEFINED,
        readme_note_id: UndefinedNoneOr[str] = UNDEFINED,
    ) -> Optional[DirectoryEntity]:
        """Partially update a directory row with UNDEFINED / None semantics.

        Field-level semantics:
        * :obj:`~src.api.undefined.UNDEFINED` -- leave the column
          untouched.
        * ``None`` -- set the column to SQL NULL (only valid on
          ``UndefinedNoneOr`` fields; passing ``None`` for
          ``slug`` raises :exc:`ValueError`).
        * any concrete value -- overwrite the column.

        Args:
            id: directory id to update.
            slug / display_name / description / image_url /
            readme_note_id: per-field updates using the
            UNDEFINED / None / value semantics above.

        Returns:
            Optional[DirectoryEntity]: the updated entity without
            hierarchy / parents / children / tags (callers layer
            those), or ``None`` when no row matched ``id``.

        Raises:
            ValueError: ``id`` is :obj:`~src.api.undefined.UNDEFINED`
                or ``None``, or ``slug`` is ``None``.
        """
        ...

    @abstractmethod
    async def delete_directory(self, id: str) -> bool:
        """Delete the directory row.

        Args:
            id: directory id to remove.

        Returns:
            bool: ``True`` when exactly one row was removed.

        Raises:
            ValueError: ``id`` is :obj:`~src.api.undefined.UNDEFINED`
                or ``None``.
        """
        ...


__all__ = [
    "DirectoryChildType",
    "DirectoryHierarchyType",
    "DirectoryParentType",
    "DirectoryHelperMixin",
    "DirectoryRepoABC",
]