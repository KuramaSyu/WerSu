"""Storage contract for the ``note`` SQL table (the note's core columns)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.db.entities.note.metadata import NoteEntity


class NoteContentRepo(ABC):
    """CRUD over the ``note`` table -- the five core columns only.

    Relation fields (``directory_ids``, ``tag_ids``, ``attachment_ids``,
    ``shelf_ids``, ``embeddings``, ``permissions``) live in side tables
    and are handled by the facades, never by this repo.

    Implementations:
    * :class:`src.db.repos.note.content.NoteContentPostgresRepo`
    """

    @abstractmethod
    async def insert(
        self,
        metadata: NoteEntity,
    ) -> NoteEntity:
        """Insert a note row and return the entity with its assigned id."""
        ...

    @abstractmethod
    async def update(
        self,
        set: NoteEntity,
        where: NoteEntity,
    ) -> NoteEntity:
        """Update a note row by matching ``where`` and applying ``set``."""
        ...

    @abstractmethod
    async def delete(
        self,
        metadata: NoteEntity,
    ) -> Optional[List[NoteEntity]]:
        """Delete notes matching ``metadata``; returns the deleted entities."""
        ...

    @abstractmethod
    async def select(
        self,
        metadata: NoteEntity,
    ) -> List[NoteEntity]:
        """Select notes matching the non-UNDEFINED fields of ``metadata``."""
        ...

    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
    ) -> NoteEntity:
        """Resolve a single note by id; raises when no row matches."""
        ...

    @abstractmethod
    async def select_by_ids(
        self,
        note_ids: List[str],
    ) -> List[NoteEntity]:
        """Bulk-resolve notes by id; preserves ``note_ids`` order.

        Args:
            note_ids: ids to resolve.  Order is preserved in the
                returned list.  Empty input is a programming error.

        Raises:
            ValueError: when `note_ids` is empty or any id is missing.

        Returns:
            List[NoteEntity]: matching notes in `note_ids` order.
            `embeddings` and `permissions` are never populated here -
            callers enrich from the permission repo.
        """
        ...
