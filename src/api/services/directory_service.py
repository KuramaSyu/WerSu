from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional, TypedDict

from src.api.other.user_context import UserContextABC
from src.api.repos.permission_repo import DirectoryChild
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity


class DirectoryIncludeOptions(TypedDict, total=False):
    """Per-directory enrichment flags for the directory read paths.

    Every key defaults to `False`.  Each `True` flag costs one
    dedicated SQL statement (see
    :meth:`src.api.directory_repo.DirectoryFacadeABC.fetch_directory`)
    and lands its result on the matching
    :class:`~src.db.entities.directory.directory.DirectoryEntity`
    field:

    * `include_parents` -- populates `directory.parent_directory_ids`
      via ``note.directory_subdirectory`` (every row where
      ``child_directory_id = directory.id``).
    * `include_child_dirs` -- populates
      `directory.child_directory_ids` via
      ``note.directory_subdirectory``
      (``directory_id = directory.id``).
    * `include_child_notes` -- populates
      `directory.child_note_ids` via ``note.directory_note``
      (``directory_id = directory.id``).

    Note:
        The cheaper "row only" read is what happens with an empty
        options dict.  Useful when the caller only needs the
        metadata.  Direct child counts are derived by the caller as
        ``len(directory.child_directory_ids)`` and
        ``len(directory.child_note_ids)`` when the lists were
        fetched.
    """

    include_parents: bool
    include_child_dirs: bool
    include_child_notes: bool


def resolve_directory_include_options(
    options: Optional["DirectoryIncludeOptions"],
) -> "DirectoryIncludeOptions":
    """Return `options` filled with `False` for every flag by default."""
    raw = options or DirectoryIncludeOptions()
    return DirectoryIncludeOptions(
        include_parents=bool(raw.get("include_parents", False)),
        include_child_dirs=bool(raw.get("include_child_dirs", False)),
        include_child_notes=bool(raw.get("include_child_notes", False)),
    )


class DirectoryServiceABC(ABC):
    """Abstract application service for directory operations.

    Implementations:
    * :class:`~src.services.directory.DirectoryServiceImpl`
    """

    @abstractmethod
    async def get_directory_notes(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:
        """Return notes belonging to ``directory_id``, with pagination.

        Permission is checked at the start of the call via the
        permission chain.  The implementation guarantees that a
        :class:`~src.db.entities.note.metadata.NoteEntity` with title
        ``"README.md"`` is present in the directory and is always
        included as the first item of the returned page when
        ``offset == 0``.  When no such note exists yet, one is
        created for the caller before the page is returned.

        Args:
            directory_id: id of the directory to load notes from.
            user_ctx: caller identity used for the directory-level
                permission check and the auto-created README owner
                relation.
            limit: maximum number of notes to return.
            offset: number of notes to skip before returning results.

        Raises:
            PermissionError: when `user_ctx` cannot view `directory_id`.
        """
        ...

    @abstractmethod
    async def get_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> Optional[DirectoryEntity]:
        """Return a single directory by id.

        Args:
            directory_id: id of the directory to load.
            user_ctx: caller identity used for the directory-level
                permission check.
            include: opt-in enrichment flags; see
                :class:`DirectoryIncludeOptions`.

        Raises:
            PermissionError: when `user_ctx` cannot view `directory_id`.

        Returns:
            Optional[DirectoryEntity]: the directory, or `None` when
            no directory with that id is visible to `user_ctx`.
        """
        ...

    @abstractmethod
    async def get_directories(
        self,
        user_ctx: UserContextABC,
        parent_id: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        *,
        include: Optional[DirectoryIncludeOptions] = None,
    ) -> List[DirectoryEntity]:
        """Return all directories visible to `user_ctx`.

        Args:
            user_ctx: caller identity used for the directory-level
                permission check.
            parent_id: optional parent-directory filter.  When set,
                only directories whose parent matches are returned.
            limit: optional maximum number of directories to return.
            offset: optional number of directories to skip before
                returning results.
            include: opt-in enrichment flags; see
                :class:`DirectoryIncludeOptions`.

        Returns:
            List[DirectoryEntity]: the directories visible to
            `user_ctx`, paginated and/or parent-filtered as requested.
        """
        ...

    @abstractmethod
    async def create_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> DirectoryEntity:
        """Create a new directory.

        The caller is automatically added as an ``admin`` of the
        created directory.  Permission is checked via the chain
        against every parent the entity specifies.

        Args:
            entity: directory payload.  `id` is ignored - the repo
                assigns one and returns it on the result.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the caller cannot create a
                directory under any id in
                `entity.parent_directory_ids`.

        Returns:
            DirectoryEntity: the persisted directory with its
            assigned `id`.
        """
        ...

    @abstractmethod
    async def patch_directory(
        self,
        entity: DirectoryEntity,
        user_ctx: UserContextABC,
    ) -> Optional[DirectoryEntity]:
        """Update an existing directory.

        Permission is checked at the start of the call against
        `entity.id`.

        Args:
            entity: directory payload.  Only fields that are not
                :obj:`~src.api.undefined.UNDEFINED` are written.
            user_ctx: caller identity.

        Raises:
            PermissionError: when `user_ctx` cannot write to
                `entity.id`.

        Returns:
            Optional[DirectoryEntity]: the updated directory, or
            `None` when no directory with that id is visible to
            `user_ctx`.
        """
        ...

    @abstractmethod
    async def delete_directory(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> bool:
        """Delete a directory and every exclusively-owned child.

        Walks the subtree rooted at ``directory_id`` via
        :meth:`PermissionRepoABC.resolve_children` and deletes:

        * every sub-directory (recursively, with their own children),
        * every note whose only parent sits inside the subtree,
        * every attachment whose only parent note sits inside the
          subtree.

        Notes or attachments that have an additional parent outside
        the subtree are left alone -- the resolver filters them
        out so the caller never destroys content that is shared with
        a sibling tree.

        Args:
            directory_id: id of the directory to delete.
            user_ctx: caller identity used for every nested
                permission check.

        Raises:
            PermissionError: when `user_ctx` cannot delete
                `directory_id` (or any nested child that the
                resolver returns).

        Returns:
            bool: `True` when the directory row was deleted in the
            underlying repo, `False` when no directory with that
            id exists.
        """
        ...

    @abstractmethod
    async def dry_delete(
        self,
        directory_id: str,
        user_ctx: UserContextABC,
    ) -> List[DirectoryChild]:
        """Resolve every exclusively-owned child without deleting it.

        Mirrors :meth:`delete_directory`'s resolution semantics but
        returns the discovered resources enriched with kind and
        human-readable name instead of mutating anything.  Useful
        for "are you sure?" confirmation prompts in the UI.

        Args:
            directory_id: id of the directory to dry-delete.
            user_ctx: caller identity used for the read-side
                permission check on the root directory.

        Raises:
            PermissionError: when `user_ctx` cannot view
                `directory_id`.

        Returns:
            List[DirectoryChild]: every directory, note and
            attachment that would be deleted by
            :meth:`delete_directory`.  The list is sorted by kind
            (directories first, then notes, then attachments) and
            then by id for deterministic output.
        """
        ...


__all__ = ["DirectoryServiceABC"]
