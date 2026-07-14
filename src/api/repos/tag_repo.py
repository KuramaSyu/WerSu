"""Storage contract for the tag taxonomy + association tables.

Tag CRUD lives in three Postgres tables:

* ``note.tag`` -- the tag itself (``id``, ``slug``, ``display_name``).
* ``note.note_tag`` -- note <-> tag bridge.
* ``note.directory_tag`` -- directory <-> tag bridge.

SpiceDB does not see tags; visibility stays rooted in
note/directory relations.

Implementations:
* :class:`src.db.repos.tag.postgres.PostgresTagRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Literal, Optional

from src.db.entities.note.tag import TagEntity


TagSubjectType = Literal["note", "directory"]


class TagRepoABC(ABC):
    """Tag taxonomy CRUD + association writes.

    The repo is the single source of truth for the ``note.tag``
    taxonomy and its two association tables.  It does NOT consult
    SpiceDB; visibility checks belong upstream.

    Across the association methods the entity the tag is being
    attached to is called the "subject" (a note or a directory).
    ``subject_type`` selects the association table; ``subject_id``
    identifies the row the tag is bound to.  ``tag_id`` is the
    tag being attached.

    Implementations:
    * :class:`src.db.repos.tag.postgres.PostgresTagRepo`
    """

    # ---- tag CRUD ------------------------------------------------------

    @abstractmethod
    async def create_tag(
        self,
        slug: str,
        display_name: str,
    ) -> TagEntity:
        """Create a new tag in the ``note.tag`` taxonomy.

        Args:
            slug: unique machine-readable slug.
            display_name: human-readable label.

        Returns:
            :class:`TagEntity`: the persisted tag, including its
            server-assigned id.

        Raises:
            RuntimeError: when the insert silently failed.
            ValueError: ``slug`` or ``display_name`` is empty.
        """
        ...

    @abstractmethod
    async def get_tag_by_id(self, tag_id: str) -> Optional[TagEntity]:
        """Fetch a single tag by id.

        Args:
            tag_id: id of the tag to load.

        Returns:
            Optional[TagEntity]: the tag, or ``None`` when no row
            matches ``tag_id``.
        """
        ...

    @abstractmethod
    async def list_tags(self) -> List[TagEntity]:
        """Return every tag in the ``note.tag`` taxonomy, sorted by slug.

        Returns:
            List[TagEntity]: every persisted tag; ``[]`` when the
            taxonomy is empty.
        """
        ...

    @abstractmethod
    async def update_tag(
        self,
        tag_id: str,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        """Partially update a tag row.

        Args:
            tag_id: id of the tag to update.
            slug: when provided, overwrite the slug.  ``None`` is
                treated as "do not update".
            display_name: when provided, overwrite the display name.
                ``None`` is treated as "do not update".

        Returns:
            Optional[TagEntity]: the updated tag, or ``None`` when
            no row matched ``tag_id``.
        """
        ...

    @abstractmethod
    async def delete_tag(self, tag_id: str) -> bool:
        """Delete a tag row (cascades the join tables).

        Args:
            tag_id: id of the tag to remove.

        Returns:
            bool: ``True`` when exactly one row was removed.
        """
        ...

    # ---- tag associations ---------------------------------------------

    @abstractmethod
    async def list_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
    ) -> Dict[str, List[str]]:
        """Bulk fetch tag ids for every subject id in ``subject_ids``.

        Args:
            subject_type: ``"note"`` or ``"directory"`` -- selects
                the association table.
            subject_ids: ids of the notes or directories to fetch
                tags for.  Order is preserved: every id in the
                input has a key in the result, even when the value
                is ``[]``.

        Returns:
            Dict[str, List[str]]: mapping of subject_id -> tag ids
            (deduplicated, sorted).  Subjects with no tags map to
            ``[]``.

        Raises:
            ValueError: ``subject_type`` is not ``"note"`` /
                ``"directory"`` or ``subject_ids`` is empty.
        """
        ...

    async def list_tags_of(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
    ) -> List[str]:
        """Singular variant of :meth:`list_tags_for`.

        Forwards to :meth:`list_tags_for` with a single-element
        list and returns the matching value (or ``[]`` when the
        subject has no tags).

        Args:
            subject_type: ``"note"`` or ``"directory"``.
            subject_id: id of the note or directory.

        Returns:
            List[str]: tag ids, deduplicated and sorted.
        """
        result = await self.list_tags_for(subject_type, [str(subject_id)])
        return result.get(str(subject_id), [])

    @abstractmethod
    async def assign_tag_to(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        """Assign ``tag_id`` to the entity identified by ``subject_id``.

        Args:
            subject_type: ``"note"`` or ``"directory"`` -- selects
                the association table.
            subject_id: id of the note or directory receiving the
                tag.
            tag_id: id of the tag to attach.

        Raises:
            ValueError: ``subject_type`` is invalid, either id is
                empty, the tag does not exist, or the subject
                (note/directory) does not exist.
            RuntimeError: when the association insert fails for any
                other reason.

        Note:
            Idempotent: when the association already exists, the
            call is a silent no-op.
        """
        ...

    @abstractmethod
    async def replace_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
        tag_ids: List[str],
    ) -> None:
        """Replace the full tag set of every subject in ``subject_ids``.

        Counterpart to :meth:`list_tags_for`.  For each subject the
        current tag set is diffed against ``tag_ids`` -- bindings
        that no longer belong are removed first, then the new
        ones are inserted.  An empty ``tag_ids`` therefore removes
        every existing binding for each subject.

        Args:
            subject_type: ``"note"`` or ``"directory"`` -- selects
                the association table.
            subject_ids: ids of the notes or directories whose tags
                are being rewritten.  Order is irrelevant.
            tag_ids: full list of tag ids to attach to every
                subject.  Falsy ids (e.g. ``""``) are skipped so
                callers don't need to pre-filter.  An empty list
                clears every tag binding.

        Raises:
            ValueError: ``subject_type`` is invalid, ``subject_ids``
                is empty, any tag in ``tag_ids`` does not exist,
                or any subject in ``subject_ids`` does not exist.

        Note:
            Atomicity is per-subject: a failure mid-loop leaves the
            earlier subjects with the new tag set and the later
            ones untouched.  Callers that need all-or-nothing
            semantics should pre-validate the inputs.
        """
        ...

    async def replace_tags_of(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_ids: List[str],
    ) -> None:
        """Singular variant of :meth:`replace_tags_for`.

        Forwards to :meth:`replace_tags_for` with a single-element
        list.

        Args:
            subject_type: ``"note"`` or ``"directory"``.
            subject_id: id of the note or directory whose tags are
                being rewritten.
            tag_ids: full list of tag ids; empty list clears every
                tag binding.
        """
        await self.replace_tags_for(
            subject_type, [str(subject_id)], list(tag_ids),
        )

    @abstractmethod
    async def remove_tag_from(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        """Counterpart to :meth:`assign_tag_to`.

        Removes the ``(subject_type, subject_id, tag_id)``
        association if it exists.  No-op when the association, the
        tag, or the subject is absent -- removing a non-existent
        binding never raises (this is the natural delete
        semantic, in contrast to :meth:`assign_tag_to` which raises
        on missing inputs so a silent ``ON CONFLICT DO NOTHING``
        cannot mask caller bugs).

        Args:
            subject_type: ``"note"`` or ``"directory"`` -- selects
                the association table.
            subject_id: id of the note or directory the tag is
                being detached from.
            tag_id: id of the tag to detach.

        Raises:
            ValueError: ``subject_type`` is invalid or either id is
                empty.
        """
        ...


__all__ = ["TagRepoABC", "TagSubjectType"]
