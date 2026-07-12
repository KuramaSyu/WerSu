"""Postgres implementation of :class:`CombinedNoteRepoABC`.

Owns the three SQL shapes used by :class:`NoteFacade` when a
caller asks for tags / parent directory enrichment.  Each
shape is a dedicated statement so a cheap basic read stays
cheap when the caller doesn't ask for enrichment.

Tables joined:

* ``note.content`` -- the note row.
* ``note.directory_hierarchy`` -- the note->directory edge rows
  with ``child_directory_id IS NULL``.
* ``note.note_tag`` -- the note->tag edge rows.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.api.combined_note_repo import CombinedNoteRepoABC
from src.api.note_service import NoteIncludeOptions, resolve_include_options
from src.api.undefined import UNDEFINED
from src.db.database import Database
from src.db.entities import NoteEntity


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
        include_opts = resolve_include_options(include)
        want_dirs = bool(include_opts.get("include_directory_ids"))
        want_tags = bool(include_opts.get("include_tag_ids"))

        if not want_dirs and not want_tags:
            return await self._select_by_id_row_only(str(note_id))
        if want_dirs and want_tags:
            return await self._select_by_id_with_dirs_and_tags(str(note_id))
        if want_dirs:
            return await self._select_by_id_with_dirs(str(note_id))
        return await self._select_by_id_with_tags(str(note_id))

    async def select_by_ids(
        self,
        note_ids: List[str],
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> List[NoteEntity]:
        include_opts = resolve_include_options(include)
        want_dirs = bool(include_opts.get("include_directory_ids"))
        want_tags = bool(include_opts.get("include_tag_ids"))

        if not want_dirs and not want_tags:
            return await self._select_by_ids_row_only(list(note_ids))
        if want_dirs and want_tags:
            return await self._select_by_ids_with_dirs_and_tags(list(note_ids))
        if want_dirs:
            return await self._select_by_ids_with_dirs(list(note_ids))
        return await self._select_by_ids_with_tags(list(note_ids))

    # ---- dedicated SQL shapes --------------------------------------

    async def _select_by_id_row_only(self, note_id: str) -> Optional[NoteEntity]:
        record = await self._db.fetchrow(
            """
            SELECT id, title, content, updated_at, author_id
            FROM note.content
            WHERE id = $1
            """,
            note_id,
        )
        if not record:
            return None
        d = dict(record)
        d["note_id"] = d.pop("id")
        return NoteEntity(**d, embeddings=[], permissions=UNDEFINED)

    async def _select_by_id_with_dirs(self, note_id: str) -> Optional[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(h.directory_id)
                       FILTER (WHERE h.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS directory_ids
            FROM note.content n
            LEFT JOIN note.directory_hierarchy h
                ON h.note_id = n.id
                AND h.child_directory_id IS NULL
            WHERE n.id = $1
            GROUP BY n.id
            """,
            note_id,
        )
        if not records:
            return None
        d = dict(records[0])
        d["note_id"] = d.pop("id")
        d["directory_ids"] = [
            str(v) for v in (d.get("directory_ids") or []) if v is not None
        ]
        return NoteEntity(**d, embeddings=[], permissions=UNDEFINED)

    async def _select_by_id_with_tags(self, note_id: str) -> Optional[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(nt.tag_id)
                       FILTER (WHERE nt.tag_id IS NOT NULL),
                       '{}'::text[]
                   ) AS tag_ids
            FROM note.content n
            LEFT JOIN note.note_tag nt ON nt.note_id = n.id
            WHERE n.id = $1
            GROUP BY n.id
            """,
            note_id,
        )
        if not records:
            return None
        d = dict(records[0])
        d["note_id"] = d.pop("id")
        d["tag_ids"] = [
            str(v) for v in (d.get("tag_ids") or []) if v is not None
        ]
        return NoteEntity(**d, embeddings=[], permissions=UNDEFINED)

    async def _select_by_id_with_dirs_and_tags(
        self, note_id: str,
    ) -> Optional[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(DISTINCT h.directory_id)
                       FILTER (WHERE h.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS directory_ids,
                   COALESCE(
                       array_agg(DISTINCT nt.tag_id)
                       FILTER (WHERE nt.tag_id IS NOT NULL),
                       '{}'::text[]
                   ) AS tag_ids
            FROM note.content n
            LEFT JOIN note.directory_hierarchy h
                ON h.note_id = n.id
                AND h.child_directory_id IS NULL
            LEFT JOIN note.note_tag nt ON nt.note_id = n.id
            WHERE n.id = $1
            GROUP BY n.id
            """,
            note_id,
        )
        if not records:
            return None
        d = dict(records[0])
        d["note_id"] = d.pop("id")
        d["directory_ids"] = [
            str(v) for v in (d.get("directory_ids") or []) if v is not None
        ]
        d["tag_ids"] = [
            str(v) for v in (d.get("tag_ids") or []) if v is not None
        ]
        return NoteEntity(**d, embeddings=[], permissions=UNDEFINED)

    async def _select_by_ids_row_only(
        self, note_ids: List[str],
    ) -> List[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT id, title, content, updated_at, author_id
            FROM note.content
            WHERE id = ANY($1::text[])
            """,
            note_ids,
        )
        return self._records_to_entities(
            records, note_ids, with_dirs=False, with_tags=False,
        )

    async def _select_by_ids_with_dirs(
        self, note_ids: List[str],
    ) -> List[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(h.directory_id)
                       FILTER (WHERE h.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS directory_ids
            FROM note.content n
            LEFT JOIN note.directory_hierarchy h
                ON h.note_id = n.id
                AND h.child_directory_id IS NULL
            WHERE n.id = ANY($1::text[])
            GROUP BY n.id
            """,
            note_ids,
        )
        return self._records_to_entities(
            records, note_ids, with_dirs=True, with_tags=False,
        )

    async def _select_by_ids_with_tags(
        self, note_ids: List[str],
    ) -> List[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(nt.tag_id)
                       FILTER (WHERE nt.tag_id IS NOT NULL),
                       '{}'::text[]
                   ) AS tag_ids
            FROM note.content n
            LEFT JOIN note.note_tag nt ON nt.note_id = n.id
            WHERE n.id = ANY($1::text[])
            GROUP BY n.id
            """,
            note_ids,
        )
        return self._records_to_entities(
            records, note_ids, with_dirs=False, with_tags=True,
        )

    async def _select_by_ids_with_dirs_and_tags(
        self, note_ids: List[str],
    ) -> List[NoteEntity]:
        records = await self._db.fetch(
            """
            SELECT n.id, n.title, n.content, n.updated_at, n.author_id,
                   COALESCE(
                       array_agg(DISTINCT h.directory_id)
                       FILTER (WHERE h.directory_id IS NOT NULL),
                       '{}'::text[]
                   ) AS directory_ids,
                   COALESCE(
                       array_agg(DISTINCT nt.tag_id)
                       FILTER (WHERE nt.tag_id IS NOT NULL),
                       '{}'::text[]
                   ) AS tag_ids
            FROM note.content n
            LEFT JOIN note.directory_hierarchy h
                ON h.note_id = n.id
                AND h.child_directory_id IS NULL
            LEFT JOIN note.note_tag nt ON nt.note_id = n.id
            WHERE n.id = ANY($1::text[])
            GROUP BY n.id
            """,
            note_ids,
        )
        return self._records_to_entities(
            records, note_ids, with_dirs=True, with_tags=True,
        )

    def _records_to_entities(
        self,
        records: List[Dict[str, Any]],
        note_ids: List[str],
        *,
        with_dirs: bool,
        with_tags: bool,
    ) -> List[NoteEntity]:
        by_id: Dict[str, Dict[str, Any]] = {}
        for row in records:
            row_dict = dict(row)
            row_dict["note_id"] = row_dict.pop("id")
            if with_dirs:
                row_dict["directory_ids"] = [
                    str(v) for v in (row_dict.get("directory_ids") or [])
                    if v is not None
                ]
            if with_tags:
                row_dict["tag_ids"] = [
                    str(v) for v in (row_dict.get("tag_ids") or [])
                    if v is not None
                ]
            by_id[str(row_dict["note_id"])] = row_dict
        missing = [nid for nid in note_ids if nid not in by_id]
        if missing:
            raise ValueError(
                f"Notes with ids {missing!r} could not be resolved"
            )
        return [
            NoteEntity(**by_id[nid], embeddings=[], permissions=UNDEFINED)
            for nid in note_ids
        ]


__all__ = ["CombinedNotePostgresRepo"]
