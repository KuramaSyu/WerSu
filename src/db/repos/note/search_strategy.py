from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Self

from asyncpg import Record
from src.ai.embedding_generator import EmbeddingGenerator, EmbeddingGeneratorABC, Models
from src.db.database import Database, DatabaseABC
from src.db.entities import NoteEntity
from src.db import TableABC


class NoteSearchStrategy(ABC):
    """Represents a strategy for searching notes."""

    def __init__(
        self,
        db: DatabaseABC,
        query: str,
        limit: int,
        offset: int
    ) -> None:
        self.db = db
        self.query = query
        self.limit = limit
        self.offset = offset


    def set_query(self, query: str) -> Self:
        """Sets the search query.

        Args:
        -----
        query: `str`
            The search query.
        """
        self.query = query
        return self

    def set_limit(self, limit: int) -> Self:
        """Sets the maximum number of results to return.

        Args:
        -----
        limit: `int`
            The maximum number of results.
        """
        self.limit = limit
        return self
    
    def set_offset(self, offset: int) -> Self:
        """Sets the number of results to skip.

        Args:
        -----
        offset: `int`
            The number of results to skip.
        """
        self.offset = offset
        return self
    
    @abstractmethod
    async def search(self) -> list["NoteEntity"]:
        """Searches for notes based on the provided query.

        Args:
        -----
        query: `str`
            The search query.
        limit: `int`
            The maximum number of results to return.
        offset: `int`
            The number of results to skip.

        Returns:
        --------
        `list[NoteEntity]`:
            A list of notes matching the search criteria.
        """
        ...


class DateNoteSearchStrategy(NoteSearchStrategy):
    """Return notes sorted by date (most recent first)."""
    
    async def search(self) -> list["NoteEntity"]:
        query = f"""
        SELECT id, title, author_id, content, updated_at
        FROM note.content
        ORDER BY updated_at DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query)
        if not records:
            return []
        return [NoteEntity.from_record(record) for record in records]


class WebNoteSearchStrategy(NoteSearchStrategy):
    """
    Return notes which match by lexme or similarity in the title and content. 
    Title is also fuzzy searched
    """
    
    async def search(self) -> list["NoteEntity"]:
        query = f"""
        SELECT id, title, author_id, content, updated_at,
            ts_rank(
                to_tsvector('english', title),
                websearch_to_tsquery('english', $1)
            ) AS fts_rank
        FROM note.content
        WHERE search_vector @@ websearch_to_tsquery('english', $1)
        ORDER BY fts_rank DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query, self.query)
        if not records:
            raise RuntimeError("Failed to fetch notes by exact title.")
        return [NoteEntity.from_record(record) for record in records]
    

class FuzzyTitleContentSearchStrategy(NoteSearchStrategy):
    """Return notes where the title or content is similar to the query"""
    
    async def search(self) -> list["NoteEntity"]:
        query = f"""
        SELECT id, title, author_id, content, updated_at
        FROM note.content
        ORDER BY similarity(title || ' ' || content, $1) DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query, self.query)
        if not records:
            raise RuntimeError("Failed to fetch notes by fuzzy title/content.")
        return [NoteEntity.from_record(record) for record in records]


class ContextNoteSearchStrategy(NoteSearchStrategy):
    """Return notes based on semantic search using embeddings."""
    def __init__(self, db: DatabaseABC, query: str, limit: int, offset: int, generator: EmbeddingGeneratorABC) -> None:
        super().__init__(db, query, limit, offset)
        self.generator = generator

    async def search(self) -> list["NoteEntity"]:
        model = Models.MINI_LM_L6_V2
        query = f"""
        SELECT id, title, author_id, content, updated_at, (embedding <=> $1::vector) AS similarity
        FROM note.embedding
        JOIN 
            note.content on note.content.id = note.embedding.note_id 
            AND note.embedding.model = $2
        ORDER BY similarity ASC
        LIMIT {self.limit}
        OFFSET {self.offset}
        """
        query_embedding = self.generator.generate(self.query)
        query_embedding_str = self.generator.tensor_to_str_vec(query_embedding)
        start = datetime.now()
        records = await self.db.fetch(query, query_embedding_str, model.value)
        print(f"Context search records: {records}")
        print(f"Context search took: {datetime.now() - start}")
        if not records:
            raise RuntimeError("Failed to fetch notes by context.")
        return [NoteEntity.from_record(record) for record in records]