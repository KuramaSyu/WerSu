from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Self

from asyncpg import Record
from src.api.types import Pagination
from src.api.undefined import UNDEFINED
from src.api.user_context import UserContextABC
from src.ai.embedding_generator import EmbeddingGenerator, EmbeddingGeneratorABC, Models
from src.db.database import Database, DatabaseABC
from src.db.entities import NoteEntity
from src.db.repos.note.permission import NotePermissionRepo, NoteRelationEnum, RelationEnum
from src.db.table import TableABC


class NoteSearchStrategy(ABC):
    """Represents a strategy for searching notes."""

    def __init__(
        self,
        query: str,
        limit: int,
        offset: int,
        db: DatabaseABC,
        user_context: UserContextABC,
        note_permissions: NotePermissionRepo,
    ) -> None:
        self.db = db
        self.query = query
        self.limit = limit
        self.offset = offset
        self.user_context = user_context
        self.note_permissions = note_permissions


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

    async def _get_user_note_ids(self) -> List[str]:
        """Helper method which provides a list of note ids 
        that the user has access to based on their permissions."""
        note_objs = await self.note_permissions.lookup_notes(
            self.user_context, 
            NoteRelationEnum.VIEW
        )
        return [o.object_id for o in note_objs if isinstance(o.object_id, str)]
    
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
        WHERE author_id = $1
        ORDER BY updated_at DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query, self.user_context.user_id)
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
        WHERE 
            author_id = $2
            AND search_vector @@ websearch_to_tsquery('english', $1)
        ORDER BY fts_rank DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query, self.query, self.user_context.user_id)
        if not records:
            raise RuntimeError("Failed to fetch notes by exact title.")
        return [NoteEntity.from_record(record) for record in records]
    

class FuzzyTitleContentSearchStrategy(NoteSearchStrategy):
    """Return notes where the title or content is similar to the query"""
    
    async def search(self) -> list["NoteEntity"]:
        query = f"""
        SELECT id, title, author_id, content, updated_at
        FROM note.content
        WHERE author_id = $2
        ORDER BY similarity(title || ' ' || content, $1) DESC
        LIMIT {self.limit}
        OFFSET {self.offset};
        """
        records = await self.db.fetch(query, self.query, self.user_context.user_id)
        if not records:
            raise RuntimeError("Failed to fetch notes by fuzzy title/content.")
        return [NoteEntity.from_record(record) for record in records]


class ContextNoteSearchStrategy(NoteSearchStrategy):
    """Return notes based on semantic search using embeddings."""
    def __init__(
        self, 
        db: DatabaseABC, 
        query: str, 
        limit: int, 
        offset: int, 
        user_context: UserContextABC, 
        generator: EmbeddingGeneratorABC,
        note_permissions: NotePermissionRepo,
    ) -> None:
        super().__init__(query, limit, offset, db, user_context, note_permissions)
        self.generator = generator

    async def search(self) -> list["NoteEntity"]:
        model = Models.MINI_LM_L6_V2
        note_ids = await self._get_user_note_ids()
        query = f"""
        SELECT id, title, author_id, content, updated_at, (embedding <=> $1::vector) AS similarity
        FROM note.embedding
        JOIN 
            note.content on note.content.id = note.embedding.note_id 
        WHERE note.embedding.model = $2
            AND note.content.id = ANY($3)
        ORDER BY similarity ASC
        LIMIT {self.limit}
        OFFSET {self.offset}
        """
        query_embedding = self.generator.generate(self.query)
        query_embedding_str = self.generator.tensor_to_str_vec(query_embedding)
        start = datetime.now()
        records = await self.db.fetch(
            query, query_embedding_str, 
            model.value, note_ids,
        )

        if not records:
            raise RuntimeError("Failed to fetch notes by context.")
        return [NoteEntity.from_record(record) for record in records]