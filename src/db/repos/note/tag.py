"""Postgres implementation of :class:`NoteTagRepoABC`.

Every SQL statement against ``note.note_tag`` lives here so the
:class:`~src.db.repos.note.note.NoteFacadeImpl` stays free of raw SQL.
"""

from __future__ import annotations

from typing import List, Optional

import asyncpg  # type: ignore[import]

from src.api.repos.note_tag_repo import NoteTagRepoABC
from src.db.table import TableABC


class NoteTagPostgresRepo(NoteTagRepoABC):
    """Postgres implementation of the note-tag bridge.

    Args:
        tags_table: ``TableABC`` over ``note.note_tag``.  Required
            -- tag CRUD is the surface this repo exposes.
        db: optional raw :class:`Database` for callers that
            still need to issue hand-written SQL.  Not required by
            the public contract.
    """

    def __init__(
        self,
        tags_table: TableABC,
        db: Optional[object] = None,
    ) -> None:
        self._tags_table = tags_table
        self._db = db

    @property
    def tags_table(self) -> TableABC:
        """Return the ``note.note_tag`` :class:`TableABC`."""
        return self._tags_table

    async def tag_ids_of_note(self, note_id: str) -> List[str]:
        records = await self._tags_table.select(
            where={"note_id": str(note_id)},
            select="tag_id",
        )
        return sorted(
            {
                str(r.get("tag_id"))
                for r in records or []
                if r.get("tag_id")
            }
        )

    async def replace_note_tags(
        self,
        note_id: str,
        tag_ids: List[str],
    ) -> None:
        """Wipe the note's tags and reinsert ``tag_ids``."""
        await self._tags_table.delete({"note_id": str(note_id)})
        for tag_id in tag_ids:
            if not tag_id:
                continue
            await self._tags_table.insert(
                {
                    "note_id": str(note_id),
                    "tag_id": str(tag_id),
                },
                on_conflict="DO NOTHING",
            )


__all__ = ["NoteTagPostgresRepo"]
