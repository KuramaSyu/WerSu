"""Storage contract for the ``note.note_tag`` join table.

The note tag table is a thin bridge between ``note.content`` and
``note.tag``.  Storage lives entirely in Postgres; SpiceDB does not
care about tags.

Implementations:
* :class:`src.db.repos.note.tag.NoteTagPostgresRepo`
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List


class NoteTagRepoABC(ABC):
    """CRUD on ``note.note_tag``.

    Implementations:
    * :class:`src.db.repos.note.tag.NoteTagPostgresRepo`
    """

    @abstractmethod
    async def tag_ids_of_note(self, note_id: str) -> List[str]:
        """Return every tag id attached to ``note_id``.

        Args:
            note_id: id of the note whose tags to read.

        Returns:
            List[str]: tag ids, deduplicated and sorted.  ``[]``
            when ``note_id`` has no tags.
        """
        ...

    @abstractmethod
    async def replace_note_tags(
        self,
        note_id: str,
        tag_ids: List[str],
    ) -> None:
        """Replace the full tag set of ``note_id`` with ``tag_ids``.

        Args:
            note_id: id of the note whose tags are being rewritten.
            tag_ids: full list of tag ids; empty list clears every
                tag binding.  Idempotent.  Falsy ids (e.g. ``""``)
                are skipped so callers don't need to pre-filter.
        """
        ...
