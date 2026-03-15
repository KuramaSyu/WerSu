from abc import ABC, abstractmethod
from dataclasses import replace
from typing import List, Optional

from asyncpg import Record
from src.api.undefined import UNDEFINED
from src.db.entities import NoteEntity
from src.db.repos.note import permission
from src.db.table import TableABC

from src.utils import asdict


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


class NoteContentPostgresRepo(NoteContentRepo):
    """Provides an implementation using Postgres as the backend database"""
    def __init__(self, table: TableABC[List[Record]]):
        self._table = table

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        records = await self._table.insert(
            asdict(metadata),
            returning="id, title, content, updated_at, author_id"
        )
        if not records:
            raise Exception("Failed to insert metadata")
        record = dict(records[0])
        record['note_id'] = record.pop('id')  # convert SQL id -> note_id for NoteEntity
        return NoteEntity(**record)

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        where_arg = asdict(where)
        where_arg["id"] = where_arg.pop("note_id", None)
        record = await self._table.update(
            set=asdict(set),
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
        # remove permissions and embeddings, since these are not in the content table
        conditions = asdict(
            replace(metadata, permissions=UNDEFINED, embeddings=UNDEFINED)
        )

        # map entities note_id to table id
        conditions[SQL_ID] = conditions.pop(ENTITY_ID, None)

        if not conditions:
            raise ValueError(f"At least one field must be set to delete metadata: {metadata}")
        records = await self._table.delete(
            where=conditions,
            returning="id, title, content, updated_at, author_id"
        )
        if not records:
            raise Exception("Failed to delete metadata")
        
        # convert records to note entities with id conversion
        entities = []
        for r in records:
            d = dict(r)
            d[ENTITY_ID] = d.pop(SQL_ID)
            entity = NoteEntity(**d, embeddings=[], permissions=[])
            entities.append(entity)

        return entities
    
    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        records = await self._table.select(
            where=asdict(metadata),
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