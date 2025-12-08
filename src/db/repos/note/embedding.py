from abc import ABC, abstractmethod

from typing import List

from asyncpg import Record
from db.entities import NoteEmbeddingEntity
from db.table import TableABC

from utils import asdict

class NoteEmbeddingRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        embedding: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """inserts an embedding
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated embedding (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        set: NoteEmbeddingEntity,
        where: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """updates embedding
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        embedding: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """delete embedding
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def select(
        self,
        embedding: NoteEmbeddingEntity,
    ) -> List[NoteEmbeddingEntity]:
        """select embeddings
        
        Args:
        -----
        embedding: `NoteEmbeddingEntity`
            the embedding of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

class NoteEmbeddingPostgresRepo(NoteEmbeddingRepo):
    """Provides an impementation using Postgres as the backend database"""
    def __init__(self, table: TableABC[List[Record]]):
        self._table = table

    async def insert(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        record = await self._table.insert(asdict(embedding))
        if not record:
            raise Exception("Failed to insert embedding")
        return embedding

    async def update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        record = await self._table.update(
            set=asdict(set),
            where=asdict(where)
        )
        if not record:
            raise Exception("Failed to update embedding")
        return set

    async def delete(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        conditions = asdict(embedding)
        if not conditions:
            raise ValueError(f"At least one field must be set to delete an embedding: {embedding}")
        record = await self._table.delete(
            where=conditions
        )
        if not record:
            raise Exception("Failed to delete embedding")
        return embedding
    
    async def select(self, embedding: NoteEmbeddingEntity) -> List[NoteEmbeddingEntity]:
        records = await self._table.select(
            where=asdict(embedding)
        )
        if not records:
            return []
        return [NoteEmbeddingEntity(**record) for record in records]
    