
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List, Optional

from src.api.note_service import NoteIncludeOptions
from src.db.entities.note.metadata import NoteEntity


class CombinedNoteRepoABC(ABC):
    """Combined note-row + side-table reads.

    Implementations:
    * :class:`src.db.repos.note.combined.CombinedNotePostgresRepo`
    """

    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> Optional[NoteEntity]:
        """Fetch one note with optional `directory_ids` + `tag_ids`.

        Args:
            note_id: id of the note to load.
            include: opt-in enrichment flags; see
                :class:`~src.api.note_service.NoteIncludeOptions`.

        Returns:
            Optional[NoteEntity]: the resolved note, or ``None``
            when no row matches ``note_id``.  ``directory_ids`` /
            ``tag_ids`` are populated iff their flag was set;
            ``embeddings`` / ``permissions`` are never populated
            -- call ``NoteFacade.select_by_id`` (or the service)
            for permission enrichment.
        """
        ...

    @abstractmethod
    async def select_by_ids(
        self,
        note_ids: List[str],
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> List[NoteEntity]:
        """Bulk variant of :meth:`select_by_id`.

        Args:
            note_ids: ids to resolve.  Order is preserved in the
                result list.  Empty input is a programming error.
            include: opt-in enrichment flags.

        Raises:
            ValueError: when ``note_ids`` is empty or any id is
                missing.

        Returns:
            List[NoteEntity]: resolved notes in ``note_ids`` order.
        """
        ...
