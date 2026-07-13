from abc import ABC, abstractmethod
from dataclasses import replace
from typing import List, Optional

from asyncpg import Record
from src.api.other.undefined import UNDEFINED
from src.db.entities import NoteEntity
from src.db.table import TableABC

from src.utils import asdict
from src.utils.dict_helper import drop_undefined


class NoteContentRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        metadata: NoteEntity,
    ) -> NoteEntity:
        """inserts metadata
        
        Args:
        -----
        metadata: `NoteEntity`
            the metadata of a note

        Returns:
        --------
        `NoteEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        set: NoteEntity,
        where: NoteEntity,
    ) -> NoteEntity:
        """updates metadata
        
        Args:
        -----
        set: `NoteEntity`
            the fields to update
        where: `NoteEntity`
            the conditions to match

        Returns:
        --------
        `NoteEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        metadata: NoteEntity,
    ) -> Optional[List[NoteEntity]]:
        """delete metadata
        
        Args:
        -----
        metadata: `NoteEntity`
            the metadata of a note

        Returns:
        --------
        `NoteEntity`:
            the deleted entity
        """
        ...

    @abstractmethod
    async def select(
        self,
        metadata: NoteEntity,
    ) -> List[NoteEntity]:
        """select metadata
        
        Args:
        -----
        metadata: `NoteEntity`
            the metadata of a note to search for

        Returns:
        --------
        `List[NoteEntity]`:
            the matching entities
        """
        ...

    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
    ) -> NoteEntity:
        """select metadata by ID

        Args:
        -----
        note_id: `str`
            the ID of the note

        Returns:
        --------
        `NoteEntity`:
            the matching entity
        """
        ...

    @abstractmethod
    async def select_by_ids(
        self,
        note_ids: List[str],
    ) -> List[NoteEntity]:
        """Bulk variant of :meth:`select_by_id`.

        Args:
            note_ids: ids to resolve.  Order is preserved in the
                returned list.  Empty input is a programming error.

        Raises:
            ValueError: when `note_ids` is empty or any id is missing.

        Returns:
            List[NoteEntity]: matching notes in `note_ids` order.
            `embeddings` and `permissions` are never populated here -
            callers enrich from the permission repo.
        """
        ...


class NoteContentPostgresRepo(NoteContentRepo):
    """Provides an implementation using Postgres as the backend database"""
    def __init__(self, table: TableABC):
        self._table = table

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        # explicitly declare since the NoteEntity has more fields than the table
        to_insert = drop_undefined({
            "id": metadata.note_id,
            "title": metadata.title,
            "content": metadata.content,
            "updated_at": metadata.updated_at,
            "author_id": metadata.author_id,
        })
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
        to_insert = drop_undefined({
            "id": set.note_id,
            "title": set.title,
            "content": set.content,
            "updated_at": set.updated_at,
            "author_id": set.author_id,
        })
        # re-map note_id -> id
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

        # build dict with all valid fields
        conditions = drop_undefined({
            SQL_ID: metadata.note_id,  # convert note_id -> id for SQL
            "title": metadata.title,
            "content": metadata.content,
            "updated_at": metadata.updated_at,
            "author_id": metadata.author_id,
        })

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
        where = drop_undefined({
            "id": metadata.note_id,
            "title": metadata.title,
            "content": metadata.content,
            "updated_at": metadata.updated_at,
            "author_id": metadata.author_id,
        })

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