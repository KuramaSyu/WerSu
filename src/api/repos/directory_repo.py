from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Optional, TypeAlias

from src.api.other.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.api.services.directory_service import DirectoryIncludeOptions
from src.db.entities.directory.directory import DirectoryEntity

DirectoryHierarchyType: TypeAlias = Literal["note", "directory", "both"]
"""Child/parent query scope for directory hierarchy lookups."""

DirectoryChildType: TypeAlias = Literal["note", "directory"]
"""Child relation scope for add/remove operations."""


class DirectoryHelperMixin(ABC):
    """Hierarchy helpers for directories.

    Consumers:
    * :class:`DirectoryRepoABC` low-level Postgres storage.
    * :class:`~src.api.facades.directory_facade.DirectoryFacadeABC`
        higher-level facade composing Postgres + SpiceDB.
    """

    @abstractmethod
    async def set_parent_directories_of(
        self,
        subject_type: DirectoryChildType,
        subject_id: str,
        parent_ids: List[str],
    ) -> None:
        """Replace the entire parent set for a note or directory.

        Args:
            subject_type: the type of the child object whose parents
                are being rewritten.
            subject_id: id of the child directory whose parents
                are being rewritten.
            parent_ids: full list of parent directory ids; an
                empty list removes every parent binding.
                Idempotent.
        """
        ...

    @abstractmethod
    async def get_parent_of(
        self,
        type: DirectoryHierarchyType,
        child_id: str,
    ) -> List[str]:
        """Return the parent directory ids of ``child_id``.

        Args:
            type: defines the type of `child_id` -- ``"note"``
                or ``"directory"`` or ``"both"``.
            child_id: id whose parents to enumerate.

        Returns:
            List[str]: parent directory ids, deduplicated and
            sorted. ``[]`` when there are no parents.
        """
        ...

    @abstractmethod
    async def get_children_of(
        self,
        type: DirectoryHierarchyType,
        directory_id: str,
        depth: int = 1,
    ) -> List[str]:
        """Return the child ids under ``directory_id``.

        Args:
            type: defines the type of children to return --
                ``"note"`` / ``"directory"`` / ``"both"``.
            directory_id: id of the starting directory.
            depth: recursion depth; ``1`` means direct children
                only (``directory_id`` itself is never returned).

        Returns:
            List[str]: matching child ids, deduplicated and
            sorted. ``[]`` when there are none.

        Raises:
            ValueError: ``depth`` is negative.
        """
        ...

    @abstractmethod
    async def get_children_for(
        self,
        child_type: DirectoryChildType,
        directory_ids: List[str],
        depth: int = 1,
    ) -> Dict[str, List[str]]:
        """Return child ids for multiple directories, keyed by input id.

        ``child_type`` describes the type of children to return
        (the entire input batch is treated as one type). With
        ``"note"`` the result lists the note children of every
        input directory; with ``"directory"`` it lists the child
        directories.

        Args:
            child_type: ``"note"`` or ``"directory"``.
            directory_ids: starting directory ids.
            depth: recursion depth; ``1`` means direct children only.

        Returns:
            Dict[str, List[str]]: mapping from each input
            ``directory_id`` to its matching child ids,
            deduplicated and sorted. ``directory_id``s without
            children map to ``[]``. Empty input returns ``{}``.

        Raises:
            ValueError: ``depth`` is negative.
        """
        ...

    @abstractmethod
    async def get_parent_for(
        self,
        child_type: DirectoryChildType,
        child_ids: List[str],
    ) -> Dict[str, List[str]]:
        """Return parent ids for multiple child ids, keyed by input id.

        ``child_type`` describes the type of every id in
        ``child_ids`` (the entire input batch is treated as one
        type). With ``"note"`` the result lists the parent
        directories of every note id; with ``"directory"`` it
        lists the parents of every directory id.

        Args:
            child_type: ``"note"`` or ``"directory"``.
            child_ids: ids of the child objects to inspect.

        Returns:
            Dict[str, List[str]]: mapping from each input
            ``child_id`` to its matching parent ids,
            deduplicated and sorted. ``child_id``s without
            parents map to ``[]``. Empty input returns ``{}``.
        """
        ...

    @abstractmethod
    async def add_child_to_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        """Add a note or child directory to ``directory_id``.

        Args:
            type: the child relation to create.
            directory_id: id of the parent directory.
            child_id: id of the child note or directory.

        Note:
            Idempotent: a no-op when the binding already exists.
        """
        ...

    @abstractmethod
    async def remove_child_from_directory(
        self,
        type: DirectoryChildType,
        directory_id: str,
        child_id: str,
    ) -> None:
        """Remove a note or child directory from ``directory_id``.

        Args:
            type: the child relation to delete.
            directory_id: id of the parent directory.
            child_id: id of the child note or directory.
        """
        ...


class DirectoryRepoABC(DirectoryHelperMixin):
    """Low-level Postgres storage contract for directories.

    Implements:
        * :class:`DirectoryHelperMixin` -- the hierarchy helpers
          (``set_parent_directories_of``, ``get_parent_of``,
          ``get_children_of`` / ``_for``, ``add_child_to_directory``,
          ``remove_child_from_directory``).

    Concrete:
        * :class:`src.db.repos.directory.postgres.PostgresDirectoryRepo`
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
            when no row matches ``id``. Row columns are always
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
            ids: directory ids to load. Empty list returns the
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
    "DirectoryHelperMixin",
    "DirectoryRepoABC",
]