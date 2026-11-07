"""Postgres implementation of the attachment trash lifecycle repo.

The contract lives in :mod:`src.api.repos.attachment_trash_repo`; this
module only holds the SQL-backed implementation.  An attachment counts
as *linked* when it has a row in either ``note.attachment_note_link``
or ``note.attachment_user_link``.
"""

from __future__ import annotations

from datetime import datetime

from src.api.repos.attachment_trash_repo import AttachmentTrashRepoABC
from src.db.database import DatabaseABC


class PostgresAttachmentTrashRepo(AttachmentTrashRepoABC):
    """Postgres-backed implementation of :class:`AttachmentTrashRepoABC`."""

    def __init__(self, db: DatabaseABC) -> None:
        self._db = db

    async def untrash_linked(self, now: datetime) -> list[str]:
        rows = await self._db.fetch(
            """
            UPDATE note.attachment AS a
            SET trashed_at = NULL,
                updated_at = $1
            WHERE a.trashed_at IS NOT NULL
              AND (
                    EXISTS (
                        SELECT 1 FROM note.attachment_note_link l
                        WHERE l.attachment_key = a.key
                    )
                 OR EXISTS (
                        SELECT 1 FROM note.attachment_user_link l
                        WHERE l.attachment_key = a.key
                    )
              )
            RETURNING a.key
            """,
            now,
        )
        return [str(row["key"]) for row in rows or []]

    async def find_unlinked_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        rows = await self._db.fetch(
            """
            SELECT a.key
            FROM note.attachment AS a
            WHERE a.trashed_at IS NULL
              AND a.created_at < $1
              AND NOT EXISTS (
                    SELECT 1 FROM note.attachment_note_link l
                    WHERE l.attachment_key = a.key
              )
              AND NOT EXISTS (
                    SELECT 1 FROM note.attachment_user_link l
                    WHERE l.attachment_key = a.key
              )
            ORDER BY a.created_at ASC
            LIMIT $2
            """,
            cutoff,
            limit,
        )
        return [str(row["key"]) for row in rows or []]

    async def mark_trashed(self, keys: list[str], at: datetime) -> list[str]:
        if not keys:
            return []
        rows = await self._db.fetch(
            """
            UPDATE note.attachment AS a
            SET trashed_at = $1,
                updated_at = $1
            WHERE a.key = ANY($2::text[])
              AND a.trashed_at IS NULL
            RETURNING a.key
            """,
            at,
            keys,
        )
        return [str(row["key"]) for row in rows or []]

    async def find_trashed_before(
        self, cutoff: datetime, limit: int
    ) -> list[str]:
        rows = await self._db.fetch(
            """
            SELECT a.key
            FROM note.attachment AS a
            WHERE a.trashed_at IS NOT NULL
              AND a.trashed_at < $1
            ORDER BY a.trashed_at ASC
            LIMIT $2
            """,
            cutoff,
            limit,
        )
        return [str(row["key"]) for row in rows or []]

    async def delete_metadata(self, keys: list[str]) -> int:
        if not keys:
            return 0
        rows = await self._db.fetch(
            """
            DELETE FROM note.attachment
            WHERE key = ANY($1::text[])
            RETURNING key
            """,
            keys,
        )
        return len(rows or [])


__all__ = ["PostgresAttachmentTrashRepo"]
