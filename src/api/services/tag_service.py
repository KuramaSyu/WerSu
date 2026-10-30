"""Application service for the global tag taxonomy.

Tags are a flat, global taxonomy stored in ``note.tag``.  A tag is
just an ``(id, slug, display_name)`` row; the note / directory
associations live in the ``note.note_tag`` / ``note.directory_tag``
bridge tables and are owned by the note / directory write paths.

SpiceDB does not see tags -- visibility of a tagged note or directory
stays rooted in that entity's own relations.  Because of that this
service performs **no permission gating**: any authenticated caller
may read and manage the taxonomy.  The ``actor`` parameter is kept on
every method so a future permission model can be threaded through
without changing the signature.

Implementations:
* :class:`src.services.tag_service.TagServiceImpl`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.other.user_context import UserContextABC
from src.db.entities.note.tag import TagEntity


class TagServiceError(RuntimeError):
    """Base class for every error raised by the tag service."""


class TagServiceABC(ABC):
    """Application service for tag taxonomy CRUD.

    The service is a thin policy boundary in front of
    :class:`~src.api.repos.tag_repo.TagRepoABC`.  It validates the
    caller-supplied payload and translates the repo's optional /
    boolean results into the shapes the gRPC layer maps onto
    ``NOT_FOUND``.

    Implementations:
    * :class:`src.services.tag_service.TagServiceImpl`
    """

    @abstractmethod
    async def create_tag(
        self,
        slug: str,
        display_name: str,
        actor: UserContextABC,
    ) -> TagEntity:
        """Insert a new tag into the ``note.tag`` taxonomy.

        Args:
            slug: unique machine-readable slug.  Required.
            display_name: human-readable label.  Required.
            actor: caller identity (currently unused for gating;
                kept for symmetry with the other services).

        Returns:
            TagEntity: the persisted tag, including its
            server-assigned id.

        Raises:
            ValueError: ``slug`` or ``display_name`` is empty.
            TagServiceError: the insert failed for any other reason
                (e.g. the slug is already taken).
        """
        ...

    @abstractmethod
    async def get_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
    ) -> Optional[TagEntity]:
        """Fetch a single tag by id.

        Args:
            tag_id: id of the tag to load.  Required.
            actor: caller identity.

        Returns:
            Optional[TagEntity]: the tag, or ``None`` when no row
            matches ``tag_id``.

        Raises:
            ValueError: ``tag_id`` is empty.
        """
        ...

    @abstractmethod
    async def list_tags(
        self,
        actor: UserContextABC,
    ) -> List[TagEntity]:
        """Return every tag in the taxonomy, sorted by slug.

        Args:
            actor: caller identity.

        Returns:
            List[TagEntity]: every persisted tag; ``[]`` when the
            taxonomy is empty.
        """
        ...

    @abstractmethod
    async def update_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        """Partially update a tag row.

        Args:
            tag_id: id of the tag to update.  Required.
            actor: caller identity.
            slug: when provided, overwrite the slug.  ``None``
                means "do not update".
            display_name: when provided, overwrite the display
                name.  ``None`` means "do not update".

        Returns:
            Optional[TagEntity]: the updated tag, or ``None`` when
            no row matched ``tag_id``.

        Raises:
            ValueError: ``tag_id`` is empty, or a provided field is
                blank.
        """
        ...

    @abstractmethod
    async def delete_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
    ) -> bool:
        """Delete a tag row (cascades the join tables).

        Args:
            tag_id: id of the tag to remove.  Required.
            actor: caller identity.

        Returns:
            bool: ``True`` when a row was removed, ``False`` when no
            row matched ``tag_id``.

        Raises:
            ValueError: ``tag_id`` is empty.
        """
        ...


__all__ = ["TagServiceABC", "TagServiceError"]
