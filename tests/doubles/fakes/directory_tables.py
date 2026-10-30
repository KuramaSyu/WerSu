from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.services.note_service import NoteIncludeOptions
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.tag import TagEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.repos.note_content_repo import NoteContentRepo
from src.db.table import TableABC
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.services.note_service import NoteResponse, NoteServiceABC
from src.services.attachment_facade import Attachment, AttachmentFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class _FakeDirectorySubdirectoryTable(TableABC):
    """In-memory implementation of ``note.directory_subdirectory``.

    Mirrors the SQL semantics the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    uses:

    * rows are ``(directory_id, child_directory_id)`` tuples,
    * inserts are idempotent via the same ``on_conflict`` flag the
      production repo passes, and
    * the ``fetch(clause, *args, select=...)`` parser does a tiny
      ``WHERE directory_id = $1`` match to keep the helper usable
      from the production code path without spinning up Postgres.

    Tests can populate rows up front via the ``rows`` attribute or
    by calling :meth:`add_directory_child`.
    """

    name = "note.directory_subdirectory"

    def __init__(self) -> None:
        # each entry: (directory_id, child_directory_id)
        self.rows: set[tuple[str, str]] = set()
        self._id_counter = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        child_directory_id = where.get("child_directory_id")
        self._id_counter += 1
        self.rows.add((directory_id, child_directory_id))
        return [
            {
                "id": self._id_counter,
                "directory_id": directory_id,
                "child_directory_id": child_directory_id,
            }
        ]

    async def update(self, set, where, returning: str = "*"):
        # Hierarchy rows are immutable in the production schema; the
        # helper exposes ``update`` so the repo's call sites compile,
        # but no test currently needs it.
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        child_directory_id = where.get("child_directory_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (child_directory_id is None or row[1] == child_directory_id)
            )
        }
        after = len(self.rows)
        return [] if before == after else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        directory_id = (where or {}).get("directory_id")
        items = []
        for parent, child in sorted(self.rows):
            if directory_id is not None and parent != directory_id:
                continue
            items.append({"directory_id": parent, "child_directory_id": child})
        return items

    async def select_row(self, where, select: str = "*"):
        return None

    async def delete_by_id(self, *id_values: Any):
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        matches = list(self.rows)
        if args:
            directory_id = args[0]
            matches = [r for r in matches if r[0] == directory_id]
        return [
            {
                "directory_id": parent,
                "child_directory_id": child,
            }
            for parent, child in matches
        ]

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add_directory_child(self, parent_id: str, child_id: str) -> None:
        """Seed a ``directory_id -> child_directory_id`` row."""
        self.rows.add((parent_id, child_id))


class _FakeDirectoryNoteTable(TableABC):
    """In-memory implementation of ``note.directory_note``.

    Mirrors the SQL semantics the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    uses:

    * rows are ``(directory_id, note_id)`` tuples,
    * inserts are idempotent via the same ``on_conflict`` flag the
      production repo passes, and
    * the ``fetch(clause, *args, select=...)`` parser does a tiny
      ``WHERE directory_id = $1`` match to keep the helper usable
      from the production code path without spinning up Postgres.

    Tests can populate rows up front via the ``rows`` attribute or
    by calling :meth:`add_note_child`.
    """

    name = "note.directory_note"

    def __init__(self) -> None:
        # each entry: (directory_id, note_id)
        self.rows: set[tuple[str, str]] = set()
        self._id_counter = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        note_id = where.get("note_id")
        self._id_counter += 1
        self.rows.add((directory_id, note_id))
        return [
            {
                "id": self._id_counter,
                "directory_id": directory_id,
                "note_id": note_id,
            }
        ]

    async def update(self, set, where, returning: str = "*"):
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        note_id = where.get("note_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (note_id is None or row[1] == note_id)
            )
        }
        after = len(self.rows)
        return [] if before == after else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        where = where or {}
        directory_id = where.get("directory_id")
        note_id = where.get("note_id")
        items = []
        for parent, row_note_id in sorted(self.rows):
            if directory_id is not None and parent != directory_id:
                continue
            if note_id is not None and row_note_id != note_id:
                continue
            items.append({"directory_id": parent, "note_id": row_note_id})
        return items

    async def select_row(self, where, select: str = "*"):
        return None

    async def delete_by_id(self, *id_values: Any):
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        matches = list(self.rows)
        if args:
            directory_id = args[0]
            matches = [r for r in matches if r[0] == directory_id]
        return [
            {
                "directory_id": parent,
                "note_id": note_id,
            }
            for parent, note_id in matches
        ]

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add_note_child(self, parent_id: str, note_id: str) -> None:
        """Seed a ``directory_id -> note_id`` row."""
        self.rows.add((parent_id, note_id))


class _FakeDirectoryTable(TableABC):
    """In-memory implementation of the ``note.directory`` table.

    Stores rows by id and lets the
    :class:`~src.db.repos.directory.postgres.PostgresDirectoryRepo`
    exercise its read paths without a real database.  Mirrors the
    production column set the repo callers depend on.
    """

    name = "note.directory"

    def __init__(self) -> None:
        # id -> record dict
        self._rows: Dict[str, Dict[str, Any]] = {}
        self._next_id = 0

    def get_id_fields(self) -> List[str]:
        return ["id"]

    async def insert(self, where, returning: str = "*", on_conflict: str = ""):
        self._next_id += 1
        new_id = f"dir-{self._next_id}"
        record = {"id": new_id, **where}
        self._rows[new_id] = record
        return [_project(record, returning)]

    async def update(self, set, where, returning: str = "*"):
        target_id = where.get("id")
        existing = self._rows.get(target_id) if target_id else None
        if existing is None:
            return []
        existing.update(set)
        return [_project(existing, returning)]

    async def delete(self, where, returning: str = "*"):
        target_id = where.get("id")
        if target_id is None or target_id not in self._rows:
            return []
        removed = self._rows.pop(target_id)
        return [_project(removed, returning)] if returning else []

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        target_id = (where or {}).get("id")
        if target_id is None:
            return list(self._rows.values())
        return [self._rows[target_id]] if target_id in self._rows else []

    async def select_row(self, where, select: str = "*"):
        results = await self.select(where=where, select=select)
        return results[0] if results else None

    async def delete_by_id(self, *id_values):
        if not id_values:
            return None
        return self._rows.pop(id_values[0], None)

    async def fetch_by_id(self, *id_values, select: str = "*"):
        if not id_values:
            return None
        record = self._rows.get(id_values[0])
        return _project(record, select) if record else None

    async def fetch(self, clause: str, *args, **kwargs):
        # limited support: clause of the form "WHERE id = ANY($1)"
        if clause.strip().startswith("WHERE id = ANY"):
            ids = args[0] if args else []
            return [
                _project(rec, kwargs.get("select", "*"))
                for rec in self._rows.values()
                if rec.get("id") in ids
            ]
        return []

    async def execute(self, sql: str, *args):
        return await self.fetch(sql, *args)

    def add(self, directory_id: str, **fields: Any) -> None:
        """Seed a directory row keyed by ``directory_id``."""
        self._rows[str(directory_id)] = {"id": str(directory_id), **fields}


class _FakeDirectoryTagsTable(TableABC):
    """In-memory implementation of the ``note.directory_tag`` table.

    Retained as a building block for tests that exercise the table
    shape directly.  Production code that touches the
    ``note.directory_tag`` rows now goes through
    :class:`src.api.repos.tag_repo.TagRepoABC`; this fake is still
    here so tests that need a populated association set can
    pre-fill ``rows`` and call :meth:`add_tag`.
    """

    name = "note.directory_tag"

    def __init__(self) -> None:
        # each entry: (directory_id, tag_id)
        self.rows: set[tuple[str, str]] = set()

    def get_id_fields(self) -> List[str]:
        return ["directory_id", "tag_id"]

    def add_tag(self, directory_id: str, tag_id: str) -> None:
        """Seed a ``directory_id -> tag_id`` row."""
        self.rows.add((str(directory_id), str(tag_id)))

    async def insert(
        self,
        where: Dict[str, Any],
        returning: str = "*",
        on_conflict: str = "",
    ) -> Optional[List[Any]]:
        directory_id = str(where.get("directory_id"))
        tag_id = str(where.get("tag_id"))
        if (directory_id, tag_id) not in self.rows:
            self.rows.add((directory_id, tag_id))
            return [{"directory_id": directory_id, "tag_id": tag_id}]
        return [{"directory_id": directory_id, "tag_id": tag_id}]

    async def update(self, set, where, returning: str = "*"):
        return []

    async def delete(
        self,
        where: Dict[str, Any],
        returning: str = "*",
    ) -> Optional[List[Any]]:
        directory_id = where.get("directory_id")
        tag_id = where.get("tag_id")
        before = len(self.rows)
        self.rows = {
            row
            for row in self.rows
            if not (
                (directory_id is None or row[0] == directory_id)
                and (tag_id is None or row[1] == tag_id)
            )
        }
        return [] if before == len(self.rows) else [{"removed": True}]

    async def select(self, where=None, order_by=None, select: str = "*", additional_values=None):
        items = []
        directory_id = (where or {}).get("directory_id")
        for did, tid in sorted(self.rows):
            if directory_id is not None and did != directory_id:
                continue
            items.append({"directory_id": did, "tag_id": tid})
        return items

    async def select_row(self, where, select: str = "*"):
        results = await self.select(where=where, select=select)
        return results[0] if results else None

    async def delete_by_id(self, *id_values: Any):
        if len(id_values) >= 2:
            self.rows.discard((str(id_values[0]), str(id_values[1])))
        return None

    async def fetch_by_id(self, *id_values: Any, select: str = "*"):
        return None

    async def fetch(self, clause: str, *args, **kwargs):
        return []

    async def execute(self, sql: str, *args):
        return []


def _project(record: Dict[str, Any], returning: str) -> Dict[str, Any]:
    """Mirror the ``RETURNING`` clause on inserts / updates."""
    if not returning or returning.strip() == "*":
        return dict(record)
    columns = [c.strip() for c in returning.split(",")]
    return {col: record.get(col) for col in columns}


__all__ = [
    '_FakeDirectorySubdirectoryTable',
    '_FakeDirectoryNoteTable',
    '_FakeDirectoryTable',
    '_FakeDirectoryTagsTable',
    '_project',
]
