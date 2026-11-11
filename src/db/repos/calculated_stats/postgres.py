"""Postgres implementation of :class:`CalculatedStatsRepoABC`.

The repo receives concrete note / directory ids from the service.  It
owns the raw aggregate SQL and the ``calculated_stats`` table itself;
permission resolution is deliberately not part of this layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from asyncpg import Record

from src.api.other.undefined import UNDEFINED, is_undefined
from src.api.repos.calculated_stats_repo import CalculatedStatsRepoABC
from src.db.entities.calculated_stats import (
    CalculatedStatsEntity,
    DirectoryAggregate,
    NoteAggregate,
)
from src.db.table import TableABC


_STATS_COLUMNS = (
    "user_id, total_chars, total_notes, total_chapters, total_books, "
    "calculated_at"
)


class PostgresCalculatedStatsRepo(CalculatedStatsRepoABC):
    """Postgres-backed stats storage + aggregate queries.

    Args:
        stats_table: :class:`TableABC` over ``calculated_stats``.
        note_content_table: :class:`TableABC` over ``note.content``.
            Used to count accessible notes and sum content length.
        directory_table: :class:`TableABC` over ``note.directory``.
            Used together with ``directory_subdirectory_table`` to
            classify accessible directories.
        directory_subdirectory_table: :class:`TableABC` over
            ``note.directory_subdirectory``.
        user_table: :class:`TableABC` over ``auth.user``.  Used by
            :meth:`list_user_ids` when a full recalculation is
            requested.
    """

    def __init__(
        self,
        stats_table: TableABC,
        note_content_table: TableABC,
        directory_table: TableABC,
        directory_subdirectory_table: TableABC,
        user_table: TableABC,
    ) -> None:
        self._stats_table = stats_table
        self._note_content_table = note_content_table
        self._directory_table = directory_table
        self._directory_subdirectory_table = directory_subdirectory_table
        self._user_table = user_table

    # ---- stats row storage --------------------------------------------

    async def list_user_ids(self) -> List[str]:
        """Return the ids of every *human* user.

        System (e.g. the seeded ``public_user``) and temporary share
        users never own a stats row: their wildcard ``view`` lookups
        either match the whole instance or nothing meaningful, so a
        stats row for them would be noise.
        """
        rows = await self._user_table.fetch(
            f"SELECT id FROM {self._user_table.name} "
            f"WHERE type = 'human' ORDER BY id"
        )
        return [str(row["id"]) for row in rows or [] if row["id"] is not None]

    async def get(self, user_id: str) -> Optional[CalculatedStatsEntity]:
        record = await self._stats_table.select_row(
            where={"user_id": str(user_id)},
            select=_STATS_COLUMNS,
        )
        if record is None:
            return None
        return self._from_record(record)

    async def fetch_by_user_ids(
        self, user_ids: List[str]
    ) -> List[CalculatedStatsEntity]:
        if not user_ids:
            return []
        rows = await self._stats_table.fetch(
            f"""
            SELECT {_STATS_COLUMNS}
            FROM {self._stats_table.name}
            WHERE user_id = ANY($1)
            ORDER BY user_id
            """,
            [str(user_id) for user_id in user_ids],
        )
        return [self._from_record(row) for row in rows or []]

    async def upsert(
        self, stats: CalculatedStatsEntity
    ) -> CalculatedStatsEntity:
        if is_undefined(stats.user_id) or not stats.user_id:
            raise ValueError("calculated stats require a concrete user_id")

        row: Dict[str, Any] = {
            "user_id": str(stats.user_id),
            "total_chars": _int_or_zero(stats.total_chars),
            "total_notes": _int_or_zero(stats.total_notes),
            "total_chapters": _int_or_zero(stats.total_chapters),
            "total_books": _int_or_zero(stats.total_books),
        }
        if not is_undefined(stats.calculated_at):
            row["calculated_at"] = stats.calculated_at

        records = await self._stats_table.upsert(
            row, returning=_STATS_COLUMNS
        )
        if not records:
            raise RuntimeError(
                f"failed to upsert calculated stats for user {stats.user_id!r}"
            )
        record = records[0] if isinstance(records, list) else records
        return self._from_record(record)

    # ---- aggregates ----------------------------------------------------

    async def aggregate_notes(self, note_ids: List[str]) -> NoteAggregate:
        if not note_ids:
            return NoteAggregate()
        rows = await self._note_content_table.fetch(
            f"""
            SELECT
                COUNT(*) AS total_notes,
                COALESCE(SUM(LENGTH(content)), 0) AS total_chars
            FROM {self._note_content_table.name}
            WHERE id = ANY($1)
            """,
            [str(note_id) for note_id in note_ids],
        )
        if not rows:
            return NoteAggregate()
        row = rows[0]
        return NoteAggregate(
            total_notes=_int_or_zero(row.get("total_notes")),
            total_chars=_int_or_zero(row.get("total_chars")),
        )

    async def aggregate_directories(
        self, directory_ids: List[str]
    ) -> DirectoryAggregate:
        if not directory_ids:
            return DirectoryAggregate()
        rows = await self._directory_table.fetch(
            f"""
            SELECT
                COUNT(*) FILTER (
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM {self._directory_subdirectory_table.name} s
                        WHERE s.child_directory_id = d.id
                    )
                ) AS total_books,
                COUNT(*) FILTER (
                    WHERE EXISTS (
                        SELECT 1
                        FROM {self._directory_subdirectory_table.name} s
                        WHERE s.child_directory_id = d.id
                    )
                ) AS total_chapters
            FROM {self._directory_table.name} d
            WHERE d.id = ANY($1)
            """,
            [str(directory_id) for directory_id in directory_ids],
        )
        if not rows:
            return DirectoryAggregate()
        row = rows[0]
        return DirectoryAggregate(
            total_books=_int_or_zero(row.get("total_books")),
            total_chapters=_int_or_zero(row.get("total_chapters")),
        )

    # ---- conversion ----------------------------------------------------

    @staticmethod
    def _from_record(record: Record) -> CalculatedStatsEntity:
        data = dict(record)
        return CalculatedStatsEntity(
            user_id=data.get("user_id", UNDEFINED),
            total_chars=data.get("total_chars", UNDEFINED),
            total_notes=data.get("total_notes", UNDEFINED),
            total_chapters=data.get("total_chapters", UNDEFINED),
            total_books=data.get("total_books", UNDEFINED),
            calculated_at=data.get("calculated_at", UNDEFINED),
        )


def _int_or_zero(value: Any) -> int:
    """Coerce a possibly-UNDEFINED / NULL aggregate to a plain int."""
    if is_undefined(value) or value is None:
        return 0
    return int(value)


__all__ = ["PostgresCalculatedStatsRepo"]
