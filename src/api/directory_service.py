"""Abstract application service for directory reads and writes.

The :class:`DirectoryServiceABC` is the contract every directory
service implementation must satisfy.  It sits between the gRPC
adapter and the lower-level repositories
(:class:`~src.db.repos.directory.directory.DirectoryRepo` and
:class:`~src.db.repos.note.note.NoteFacade`), and centralises
permission checks via the permission chain in
:mod:`src.domain.permission_chain`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.permission_repo import DirectoryChild
from src.api.user_context import UserContextABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity


class DirectoryServiceABC(ABC):
    """Abstract application service for directory operations.

    Implementations:
    * :class:`~src.services.directory.DirectoryService`
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
    ) -> Optional[DirectoryEntity]:
        """Return a single directory by id.

        Args:
            directory_id: id of the directory to load.
            user_ctx: caller identity used for the directory-level
                permission check.

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
        against the parent directory when the entity specifies one.

        Args:
            entity: directory payload.  `id` is ignored - the repo
                assigns one and returns it on the result.
            user_ctx: caller identity.

        Raises:
            PermissionError: when the caller cannot create a
                directory under `entity.parent_id`.

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
