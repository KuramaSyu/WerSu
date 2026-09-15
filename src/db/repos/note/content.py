"""Postgres implementation of :class:`~src.api.repos.note_content_repo.NoteContentRepo`."""

from __future__ import annotations

from typing import List, Optional

from asyncpg import Record
from src.api.repos.note_content_repo import NoteContentRepo
from src.db.entities import NoteEntity
from src.db.table import TableABC
from src.grpc_mod.converter.postgres_row_converter import (
    PostgresRowConverter,
)

from src.api.other.undefined import UNDEFINED, is_undefined
from src.utils import asdict

# Columns the note.content table actually owns; everything else on NoteEntity
# (tags, shelves, directories, attachments, embeddings, permissions) is
# normalised elsewhere and must not reach this row.
_NOTE_COLUMNS = ("id", "title", "content", "updated_at", "author_id")


def _project_to_columns(entity: NoteEntity, converter: PostgresRowConverter) -> dict:
    """Project a NoteEntity down to the note.content table columns.

    Runs the visitor, then strips ``updated_at`` when the caller left
    it ``UNDEFINED`` -- the visitor would otherwise fill it with
    ``now()``, but note.content wants the DB-side default to apply
    instead.  ``None`` and explicit datetimes pass through.
    """
    row = entity.convert(converter)
    # converter inejcts now() for undefined fields -> clean it in this case.
    # the service would update the timestamp explicitly if we wanted to override it.
    if is_undefined(entity.updated_at):
        row.pop("updated_at", None)
    return {col: row[col] for col in _NOTE_COLUMNS if col in row}


class NoteContentPostgresRepo(NoteContentRepo):
    """Provides an implementation using Postgres as the backend database."""

    def __init__(
        self,
        table: TableABC,
        to_postgres_row: Optional[PostgresRowConverter] = None,
    ) -> None:
        self._table = table
        # Internal knob: a shared PostgresRowConverter instance is injected from main.py.
        self._to_postgres_row = to_postgres_row or PostgresRowConverter()

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        to_insert = _project_to_columns(metadata, self._to_postgres_row)
        records = await self._table.insert(
            to_insert,
            returning="id, title, content, updated_at, author_id"
        )
        if not records:
            raise Exception("Failed to insert metadata")
        record = dict(records[0])
        record['note_id'] = record.pop('id')  # convert SQL id -> note_id for NoteEntity
        return NoteEntity(**record)

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        where_arg = asdict(where)
        to_insert = _project_to_columns(set, self._to_postgres_row)
        # re-map note_id -> id on the WHERE side
        where_arg["id"] = where_arg.pop("note_id", None)

        record = await self._table.update(
            set=to_insert,
            where=where_arg,
            returning="id, title, content, updated_at, author_id"
        )
        if not record:
            raise Exception(f"Failed to update metadata; returned: {record}")
        assert isinstance(record, Record)
        record = dict(record)
        record['note_id'] = record.pop('id')  # convert SQL id -> note_id for NoteEntity
        return NoteEntity(**record)

    async def delete(self, metadata: NoteEntity) -> Optional[List[NoteEntity]]:
        SQL_ID = self._table.get_id_fields()[0]
        ENTITY_ID = "note_id"

        conditions = _project_to_columns(metadata, self._to_postgres_row)
        # re-map SQL id column back to its name
        if SQL_ID != "id" and "id" in conditions:
            conditions[SQL_ID] = conditions.pop("id")

        if not conditions:
            raise ValueError(f"At least one field must be set to delete metadata: {metadata}")
        records = await self._table.delete(
            where=conditions,
            returning="id, title, content, updated_at, author_id"
        )
        if not records:
            raise Exception(f"Failed to delete metadata for conditions: {conditions}; returned: {records}")

        # convert records to note entities with id conversion
        entities = []
        for r in records:
            d = dict(r)
            d[ENTITY_ID] = d.pop(SQL_ID)
            entity = NoteEntity(**d, embeddings=[], permissions=[])
            entities.append(entity)

        return entities

    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        where = _project_to_columns(metadata, self._to_postgres_row)

        records = await self._table.select(
            where=where,
            select="id, title, content, updated_at, author_id"
        )
        if not records:
            return []
        return [NoteEntity.from_record(record) for record in records]

    async def select_by_id(self, note_id: str) -> NoteEntity:
        record = await self._table.fetch_by_id(note_id, select="id, title, content, updated_at, author_id")
        if not record:
            raise RuntimeError(f"Note with ID {note_id} not found")
        # convert Record to NoteEntity (id -> note_id)
        record = dict(record)
        record['note_id'] = record.pop('id')

        # neither embeddings nor permissions are fetched here
        return NoteEntity(**record, embeddings=[], permissions=[])

    async def select_by_ids(self, note_ids: List[str]) -> List[NoteEntity]:
        if not note_ids:
            raise ValueError("note_ids must not be empty")

        # single round-trip; `id = ANY($1::text[])` lets Postgres reuse
        # the primary-key index for fast membership lookups.
        records = await self._table.fetch(
            f"""
            SELECT id, title, content, updated_at, author_id
            FROM {self._table.name}
            WHERE id = ANY($1::text[])
            """,
            list(note_ids),
        )
        if not records:
            raise ValueError(
                f"Notes with ids {note_ids!r} could not be resolved"
            )

        by_id = {str(r["id"]): r for r in records}
        missing = [nid for nid in note_ids if nid not in by_id]
        if missing:
            raise ValueError(
                f"Notes with ids {missing!r} could not be resolved"
            )

        notes: List[NoteEntity] = []
        for nid in note_ids:
            record_dict = dict(by_id[nid])
            record_dict["note_id"] = record_dict.pop("id")
            notes.append(
                NoteEntity(**record_dict, embeddings=[], permissions=[])
            )
        return notes