"""Postgres implementation of :class:`CombinedNoteRepoABC`.

Pure dispatcher: every concrete SQL statement lives on a
subvariant of :class:`~src.db.repos.note.note_fetch_strategy.NoteFetchStrategyABC`.
This module just picks the strategy that matches the caller's
:class:`~src.api.services.note_service.NoteIncludeOptions` and
delegates the round-trip.
"""

from __future__ import annotations

from typing import List, Optional

from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.services.note_service import NoteIncludeOptions
from src.db.database import Database
from src.db.entities import NoteEntity
from src.db.repos.note.note_fetch_strategy import strategy_for


class CombinedNotePostgresRepo(CombinedNoteRepoABC):
    """Postgres implementation of the combined note + side-table reads.

    Args:
        db: raw :class:`Database` connection used for the JOIN
            statements.  Required because the queries span more
            than one of the table wrappers and we want them in
            one place.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def select_by_id(
        self,
        note_id: str,
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> Optional[NoteEntity]:
        strategy = strategy_for(include)
        return await strategy.fetch_one(self._db, str(note_id))

    async def select_by_ids(
        self,
        note_ids: List[str],
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> List[NoteEntity]:
        strategy = strategy_for(include)
        return await strategy.fetch_many(self._db, list(note_ids))


__all__ = ["CombinedNotePostgresRepo"]