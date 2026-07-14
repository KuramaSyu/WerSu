"""Postgres implementation of :class:`TagRepoABC`.

Every SQL statement against ``note.tag`` and its two association
tables lives here so :class:`TagRepoABC` consumers never see raw
SQL.

Tables touched:

* ``note.tag`` -- the tag taxonomy row.
* ``note.note_tag`` -- note <-> tag bridge.
* ``note.directory_tag`` -- directory <-> tag bridge.

The two association tables mirror the parent rows: deleting a
note / directory or a tag cascades the link row away (see
:mod:`src.db.migrations.20260712-tags-and-directory-slug`).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.db.database import Database
from src.db.entities.note.tag import TagEntity
from src.db.table import TableABC


class PostgresTagRepo(TagRepoABC):
    """Postgres implementation of the tag taxonomy + association writes.

    Args:
        tag_table: :class:`TableABC` over ``note.tag``.
        note_tag_table: :class:`TableABC` over ``note.note_tag``.
        directory_tag_table: :class:`TableABC` over
            ``note.directory_tag``.
        db: raw :class:`Database` connection used for the
            subject / existence checks and the bulk fetch.
            Required because the bulk ``list_tags_for`` query
            spans the association table with a single
            ``GROUP BY``, which the table wrapper does not expose.
    """

    def __init__(
        self,
        tag_table: TableABC,
        note_tag_table: TableABC,
        directory_tag_table: TableABC,
        db: Database,
    ) -> None:
        self._tag_table = tag_table
        self._note_tag_table = note_tag_table
        self._directory_tag_table = directory_tag_table
        self._db = db

    # ---- tag CRUD ------------------------------------------------------

    async def create_tag(
        self,
        slug: str,
        display_name: str,
    ) -> TagEntity:
        if not slug:
            raise ValueError("slug is required")
        if not display_name:
            raise ValueError("display_name is required")
        rows = await self._tag_table.insert(
            {"slug": str(slug), "display_name": str(display_name)},
            returning="id, slug, display_name",
        )
        if not rows:
            raise RuntimeError("Failed to create tag")
        return self._row_to_entity(rows[0])

    async def get_tag_by_id(self, tag_id: str) -> Optional[TagEntity]:
        record = await self._tag_table.fetch_by_id(str(tag_id))
        if not record:
            return None
        return self._row_to_entity(record)

    async def list_tags(self) -> List[TagEntity]:
        records = await self._db.fetch(
            """
            SELECT id, slug, display_name
            FROM note.tag
            ORDER BY slug ASC
            """
        )
        return [self._row_to_entity(r) for r in records or []]

    async def update_tag(
        self,
        tag_id: str,
        *,
        slug: Optional[str] = None,
        display_name: Optional[str] = None,
    ) -> Optional[TagEntity]:
        updates: Dict[str, object] = {}
        if slug is not None:
            updates["slug"] = str(slug)
        if display_name is not None:
            updates["display_name"] = str(display_name)
        if not updates:
            return await self.get_tag_by_id(tag_id)

        set_clause = ", ".join(f"{col} = ${i + 2}" for i, col in enumerate(updates.keys()))
        values = list(updates.values())
        record = await self._db.fetchrow(
            f"""
            UPDATE note.tag
            SET {set_clause}
            WHERE id = $1
            RETURNING id, slug, display_name
            """,
            str(tag_id),
            *values,
        )
        if not record:
            return None
        return self._row_to_entity(record)

    async def delete_tag(self, tag_id: str) -> bool:
        if not tag_id:
            raise ValueError("tag_id is required")
        return bool(
            await self._tag_table.delete({"id": str(tag_id)})
        )

    # ---- tag associations ---------------------------------------------

    async def list_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
    ) -> Dict[str, List[str]]:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")

        cleaned_ids = [str(i) for i in subject_ids if i]
        if not cleaned_ids:
            raise ValueError("subject_ids is required")

        if subject_type == "note":
            records = await self._db.fetch(
                """
                SELECT note_id, tag_id
                FROM note.note_tag
                WHERE note_id = ANY($1::text[])
                """,
                cleaned_ids,
            )
            subject_key = "note_id"
        else:
            records = await self._db.fetch(
                """
                SELECT directory_id, tag_id
                FROM note.directory_tag
                WHERE directory_id = ANY($1::text[])
                """,
                cleaned_ids,
            )
            subject_key = "directory_id"

        result: Dict[str, Dict[str, None]] = {oid: {} for oid in cleaned_ids}
        for r in records or []:
            subject_id = r.get(subject_key)
            tag_id = r.get("tag_id")
            if not subject_id or not tag_id:
                continue
            result.setdefault(str(subject_id), {})[str(tag_id)] = None

        return {
            subject_id: sorted(tags.keys()) for subject_id, tags in result.items()
        }

    async def assign_tag_to(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")

        # Verify the tag itself exists.  Without this, the ON CONFLICT
        # DO NOTHING on the join table would silently swallow a bad
        # tag_id.
        tag = await self.get_tag_by_id(str(tag_id))
        if not tag:
            raise ValueError(f"Tag {tag_id!r} does not exist")

        if subject_type == "note":
            subject_column = "note_id"
            subject_table = "note.content"
            join_table = self._note_tag_table
        else:
            subject_column = "directory_id"
            subject_table = "note.directory"
            join_table = self._directory_tag_table

        # Verify the subject exists.  Same rationale: silent
        # no-op on a non-existent subject would mask caller bugs.
        subject_record = await self._db.fetchrow(
            f"SELECT id FROM {subject_table} WHERE id = $1",
            str(subject_id),
        )
        if not subject_record:
            raise ValueError(
                f"{subject_type.capitalize()} {subject_id!r} does not exist"
            )

        rows = await join_table.insert(
            {subject_column: str(subject_id), "tag_id": str(tag_id)},
            on_conflict="DO NOTHING",
        )
        if rows is None:
            raise RuntimeError(
                f"Failed to assign tag {tag_id!r} to "
                f"{subject_type} {subject_id!r}"
            )

    async def replace_tags_for(
        self,
        subject_type: TagSubjectType,
        subject_ids: List[str],
        tag_ids: List[str],
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_ids:
            raise ValueError("subject_ids is required")

        cleaned_subject_ids = [str(i) for i in subject_ids if i]
        if not cleaned_subject_ids:
            raise ValueError("subject_ids is required")

        cleaned_tag_ids = [str(t) for t in tag_ids if t]

        if subject_type == "note":
            subject_column = "note_id"
            subject_table = "note.content"
            join_table = self._note_tag_table
        else:
            subject_column = "directory_id"
            subject_table = "note.directory"
            join_table = self._directory_tag_table

        # Verify every tag exists up front so a bad tag_id fails the
        # call before we start mutating state.  Same rationale as
        # :meth:`assign_tag_to` -- the join-table insert uses
        # ``ON CONFLICT DO NOTHING`` and would otherwise silently
        # swallow a bad tag_id.
        for tag_id in cleaned_tag_ids:
            tag = await self.get_tag_by_id(tag_id)
            if not tag:
                raise ValueError(f"Tag {tag_id!r} does not exist")

        # Verify every subject exists so the rewrite does not silently
        # succeed against a non-existent row.
        for subject_id in cleaned_subject_ids:
            subject_record = await self._db.fetchrow(
                f"SELECT id FROM {subject_table} WHERE id = $1",
                str(subject_id),
            )
            if not subject_record:
                raise ValueError(
                    f"{subject_type.capitalize()} {subject_id!r} does not exist"
                )

        # Per-subject set-difference rewrite.  Read the current tag
        # set, drop the bindings that are no longer wanted, and
        # insert the new ones.  Mirrors
        # :meth:`PostgresDirectoryRepo.set_parent_directories` --
        # bindings that already match are skipped via
        # ``ON CONFLICT DO NOTHING``; bindings that no longer belong
        # are deleted before the new set is inserted.
        for subject_id in cleaned_subject_ids:
            current = set(
                await self.list_tags_of(subject_type, str(subject_id))
            )
            desired = set(cleaned_tag_ids)

            for old in current - desired:
                await join_table.delete(
                    {subject_column: str(subject_id), "tag_id": str(old)}
                )

            for new_tag in desired - current:
                await join_table.insert(
                    {
                        subject_column: str(subject_id),
                        "tag_id": str(new_tag),
                    },
                    on_conflict="DO NOTHING",
                )

    async def remove_tag_from(
        self,
        subject_type: TagSubjectType,
        subject_id: str,
        tag_id: str,
    ) -> None:
        if subject_type not in ("note", "directory"):
            raise ValueError(
                f"subject_type must be 'note' or 'directory', got {subject_type!r}"
            )
        if not subject_id:
            raise ValueError("subject_id is required")
        if not tag_id:
            raise ValueError("tag_id is required")

        if subject_type == "note":
            subject_column = "note_id"
            join_table = self._note_tag_table
        else:
            subject_column = "directory_id"
            join_table = self._directory_tag_table

        # No-op when the binding is absent -- deleting a non-existent
        # row is a no-op, which is the natural counterpart to
        # ``assign_tag_to``'s idempotent insert.
        await join_table.delete(
            {subject_column: str(subject_id), "tag_id": str(tag_id)}
        )

    # ---- helpers ------------------------------------------------------

    @staticmethod
    def _row_to_entity(row: object) -> TagEntity:
        """Map one ``note.tag`` record to a :class:`TagEntity`.

        Handles both ``asyncpg.Record`` (production) and plain
        ``dict`` (in-memory fakes) via :meth:`TagEntity.from_record`.
        """
        return TagEntity.from_record(row)  # type: ignore[arg-type]


__all__ = ["PostgresTagRepo"]
