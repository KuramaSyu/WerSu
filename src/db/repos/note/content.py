from abc import ABC, abstractmethod
from typing import List

from asyncpg import Record
from src.db.entities import NoteEntity
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
    ) -> NoteEntity:
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
        note_id: int,
    ) -> NoteEntity:
        """select metadata by ID
        
        Args:
        -----
        note_id: `int`
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
        record = await self._table.insert(asdict(metadata))
        if not record:
            raise Exception("Failed to insert metadata")
        return metadata

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        where_arg = asdict(where)
        where_arg["id"] = where_arg.pop("note_id", None)
        record = await self._table.update(
            set=asdict(set),
            where=where_arg,
            returning="*"
        )
        if not record:
            raise Exception(f"Failed to update metadata; returned: {record}")
        assert isinstance(record, Record)
        record = dict(record)
        record['note_id'] = record.pop('id')  # convert SQL id -> note_id for NoteEntity
        return NoteEntity(**record)

    async def delete(self, metadata: NoteEntity) -> NoteEntity:
        conditions = asdict(metadata)
        if not conditions:
            raise ValueError(f"At least one field must be set to delete metadata: {metadata}")
        record = await self._table.delete(
            where=conditions
        )
        if not record:
            raise Exception("Failed to delete metadata")
        return metadata
    
    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        records = await self._table.select(
            where=asdict(metadata)
        )
        if not records:
            return []
        return [NoteEntity(**record) for record in records]

    async def select_by_id(self, note_id: int) -> NoteEntity:
        record = await self._table.fetch_by_id(note_id)
        if not record:
            raise Exception(f"Note with ID {note_id} not found")
        # convert Record to NoteEntity (id -> note_id)
        record = dict(record)
        record['note_id'] = record.pop('id')

        # neither embeddings nor permissions are fetched here
        return NoteEntity(**record, embeddings=[], permissions=[])