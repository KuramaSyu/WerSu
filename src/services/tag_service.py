"""Concrete :class:`~src.api.services.tag_service.TagServiceABC` implementation.

The service is intentionally thin: it validates the caller-supplied
payload and delegates storage to
:class:`~src.api.repos.tag_repo.TagRepoABC`.  It performs no
permission gating because tags carry no SpiceDB relations -- see the
module docstring on :mod:`src.api.services.tag_service`.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.other.types import LoggingProvider
from src.api.other.user_context import UserContextABC
from src.api.repos.tag_repo import TagRepoABC
from src.api.services.tag_service import TagServiceABC
from src.db.entities.note.tag import TagEntity


class TagServiceImpl(TagServiceABC):
    """Thin service-layer implementation of the tag taxonomy.

    Args:
        tag_repo: storage layer for the ``note.tag`` taxonomy.
        logging_provider: optional logger factory.
    """

    def __init__(
        self,
        tag_repo: TagRepoABC,
        logging_provider: Optional[LoggingProvider] = None,
    ) -> None:
        self._tag_repo = tag_repo
        self._logging_provider = logging_provider
        self.log = (
            logging_provider(__name__, self)
            if logging_provider is not None
            else None
        )

    # ---- create --------------------------------------------------------

    async def create_tag(
        self,
        slug: str,
        display_name: str,
        actor: UserContextABC,
    ) -> TagEntity:
        """Insert a new tag, validating the required fields first."""
        if not slug:
            raise ValueError("slug is required")
        if not display_name:
            raise ValueError("display_name is required")

        # Storage exceptions (notably ``asyncpg.UniqueViolationError``
        # for a duplicate slug) propagate unchanged so the gRPC layer
        # can map them onto the right status code.  This mirrors the
        # note / user services, which do the same.
        return await self._tag_repo.create_tag(
            str(slug), str(display_name),
        )

    # ---- read ----------------------------------------------------------

    async def get_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
    ) -> Optional[TagEntity]:
        """Fetch a single tag by id."""
        if not tag_id:
            raise ValueError("tag_id is required")
        return await self._tag_repo.get_tag_by_id(str(tag_id))

    async def list_tags(
        self,
        actor: UserContextABC,
    ) -> List[TagEntity]:
        """Return every tag, sorted by slug."""
        return await self._tag_repo.list_tags()

    # ---- update --------------------------------------------------------

    async def update_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        """Partially update a tag, validating any provided field."""
        if not tag_id:
            raise ValueError("tag_id is required")
        if slug is not None and not slug:
            raise ValueError("slug must not be empty")
        if display_name is not None and not display_name:
            raise ValueError("display_name must not be empty")

        return await self._tag_repo.update_tag(
            str(tag_id),
            slug=str(slug) if slug is not None else None,
            display_name=(
                str(display_name) if display_name is not None else None
            ),
        )

    # ---- delete --------------------------------------------------------

    async def delete_tag(
        self,
        tag_id: str,
        actor: UserContextABC,
    ) -> bool:
        """Delete a tag row; ``False`` when no row matched."""
        if not tag_id:
            raise ValueError("tag_id is required")
        return await self._tag_repo.delete_tag(str(tag_id))


__all__ = ["TagServiceImpl"]
