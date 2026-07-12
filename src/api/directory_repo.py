
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Literal, Optional

from src.api.directory_service import DirectoryIncludeOptions
from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr
from src.db.entities.directory.directory import DirectoryEntity


class DirectoryRepoABC(ABC):
    """Low-level Postgres storage contract for directories.

    Implementations:
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
            server-assigned id populated.  Hierarchy, parent,
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

    @abstractmethod
    async def set_parent_directories(
        self,
        directory_id: str,
        parent_ids: List[str],
    ) -> None:
        """Replace the entire parent set for ``directory_id``.

        Args:
            directory_id: id of the child directory whose parents
                are being rewritten.
            parent_ids: full list of parent directory ids; an
                empty list removes every parent binding.
                Idempotent.
        """
        ...

    @abstractmethod
    async def parent_directory_ids_of(
        self,
        directory_id: str,
    ) -> List[str]:
        """Return every parent directory id of ``directory_id``.

        Args:
            directory_id: id of the directory whose parents to
                enumerate.

        Returns:
            List[str]: parent directory ids, deduplicated and
            sorted.  ``[]`` when ``directory_id`` has no parents.
        """
        ...

    @abstractmethod
    async def direct_child_directory_ids_of(
        self,
        directory_id: str,
    ) -> List[str]:
        """Return every direct child directory of ``directory_id``.

        Args:
            directory_id: id of the directory whose child
                directories to enumerate.

        Returns:
            List[str]: child directory ids, deduplicated and sorted.
            ``[]`` when ``directory_id`` has none.
        """
        ...

    @abstractmethod
    async def list_note_ids(
        self,
        directory_id: str,
    ) -> List[str]:
        """Return every direct child note id of ``directory_id``.

        Args:
            directory_id: id of the directory whose child notes
                to enumerate.

        Returns:
            List[str]: child note ids, deduplicated and sorted.
            ``[]`` when ``directory_id`` has none.
        """
        ...

    @abstractmethod
    async def count_direct_child_directories(
        self,
        directory_id: str,
    ) -> int:
        """Return the count of direct child directories.

        Args:
            directory_id: id of the directory whose child
                directory count to read.

        Returns:
            int: number of direct child directories; ``0`` when
            ``directory_id`` has none.
        """
        ...

    @abstractmethod
    async def bind_note(self, directory_id: str, note_id: str) -> None:
        """Bind ``note_id`` as a direct child of ``directory_id``.

        Args:
            directory_id: id of the parent directory.
            note_id: id of the note to attach.

        Note:
            Idempotent: a no-op when the binding already exists.
        """
        ...

    @abstractmethod
    async def unbind_note(self, directory_id: str, note_id: str) -> None:
        """Remove the direct-child binding for ``note_id``.

        Args:
            directory_id: id of the parent directory.
            note_id: id of the note to detach.
        """
        ...

    @abstractmethod
    async def tag_ids_of_directory(
        self,
        directory_id: str,
    ) -> List[str]:
        """Return every tag id attached to ``directory_id``.

        Args:
            directory_id: id of the directory whose tags to read.

        Returns:
            List[str]: tag ids, deduplicated and sorted.  ``[]``
            when ``directory_id`` has no tags.
        """
        ...

    @abstractmethod
    async def replace_directory_tags(
        self,
        directory_id: str,
        tag_ids: List[str],
    ) -> None:
        """Replace the full tag set of ``directory_id`` with ``tag_ids``.

        Args:
            directory_id: id of the directory whose tags are being
                rewritten.
            tag_ids: full list of tag ids; empty list clears every
                tag binding.  Idempotent.
        """
        ...

    @abstractmethod
    async def get_children(
        self,
        directory_id: str,
        type: Literal["notes", "directories", "both"],
        *,
        descendants: bool = False,
        max_depth: int = 10,
    ) -> List[str]:
        """Return the child ids of ``directory_id`` filtered by ``type``.

        Args:
            directory_id: id of the starting directory.
            type: ``"notes"`` / ``"directories"`` / ``"both"`` --
                selects which side of the XOR constraint to return.
            descendants: when ``True`` (default ``False``), walk
                the full subtree instead of just the immediate
                children.  ``max_depth`` caps the recursion.
            max_depth: recursion cap for the subtree walk;
                ignored when ``descendants=False``.  ``0`` means
                "only the starting directory itself".

        Returns:
            List[str]: matching child ids, deduplicated and sorted.

        Raises:
            ValueError: ``type`` is not one of the allowed values
                or ``max_depth`` is negative.
        """
        ...

    @abstractmethod
    async def get_descendants(
        self,
        root_id: str,
        type: Literal["notes", "directories", "both"],
        *,
        max_depth: int = 10,
    ) -> List[str]:
        """Walk the subtree rooted at ``root_id`` and return ids by type.

        Args:
            root_id: starting directory id.
            type: ``"notes"`` / ``"directories"`` / ``"both"``.
            max_depth: recursion cap; ``0`` returns just the start.

        Returns:
            List[str]: matching descendant ids, deduplicated and
            sorted.

        Raises:
            ValueError: ``type`` invalid or ``max_depth`` negative.
        """
        ...


__all__ = ["DirectoryRepoABC"]
