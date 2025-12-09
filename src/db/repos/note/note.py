from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Type
import typing

import asyncpg

from ai.embedding_generator import EmbeddingGenerator, Models
from api.types import Pagination
from db.entities import NoteEntity
from db import Database
from db.entities.note.embedding import NoteEmbeddingEntity
from db.repos.note.content import NoteContentRepo

from db.repos.note.permission import NotePermissionRepo
from db.repos.note.search_strategy import ContextNoteSearchStrategy, FuzzyTitleContentSearchStrategy, NoteSearchStrategy, TitleLexemeNoteSearchStrategy
from db.table import TableABC
from api.undefined import UNDEFINED
from db.entities.note.permission import NotePermissionEntity
from db.repos.note.embedding import NoteEmbeddingRepo


class SearchType(Enum):
    NO_SEARCH = 1
    FULL_TEXT_TITLE = 2
    FUZZY = 3
    CONTEXT = 4



class NoteRepoFacadeABC(ABC):
    """Represents the ABC for note-operations which operate over multiple relations"""
    @property
    def embedding_table_name(self) -> str:
        return "note.embedding"

    @property
    def content_table_name(self) -> str:
        return "note.content"
    
    @property
    def permission_table_name(self) -> str:
        return "note.permission"

    @abstractmethod
    async def insert(
        self,
        note: NoteEntity,
    ) -> NoteEntity:
        """inserts a full note into 
        all 3 relations used for this.
        The embedding will be generated automatically.
        Added embeddings will be ignored.
        
        Args:
        -----
        note: `NoteMetadataEntity`
            the note of a note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        note: NoteEntity,
    ) -> NoteEntity:
        """updates note
        
        Args:
        -----
        note: `NoteMetadataEntity`
            the note of a note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        note: NoteEntity,
    ) -> NoteEntity:
        """delete note
        
        Args:
        -----
        note: `NoteMetadataEntity`
            the note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
        """
        ...


    @abstractmethod
    async def select(
        self,
        note: NoteEntity,
    ) -> Optional[NoteEntity]:
        """select a whole note by its ID
        
        Args:
        -----
        note: `NoteMetadataEntity`
            the note

            
        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
            
        """
        ...

    @abstractmethod
    async def search_notes(
        self, 
        search_type: SearchType,
        query: str, 
        pagination: Pagination
    ) -> List[NoteEntity]:
        """search notes according to the search type
        
        Args:
        -----
        search_type: `SearchType`
            the type of search to perform
        query: `str`
            the search query
        pagination: `Pagination`
            pagination parameters (limit, offset)

        Returns:
        --------
        `List[MinimalNote]`:
            the list of matching minimal notes
        """
        ...


class NoteRepoFacade(NoteRepoFacadeABC):
    def __init__(
        self, 
        db: Database,
        content_repo: NoteContentRepo,
        embedding_repo: NoteEmbeddingRepo,
        permission_repo: NotePermissionRepo,
    ):
        self._db = db
        self._content_repo = content_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo

    
    async def insert(self, note: NoteEntity):
        # insert note itself
        query = f"""
        INSERT INTO {self.content_table_name}(title, content, updated_at, author_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """
        note_id: int = (await self._db.fetchrow(
            query, 
            note.title, note.content, note.updated_at, note.author_id
        ))["id"] 
        print(f"Inserted note with ID: {note_id}")

        # insert embeddings
        assert note.embeddings == []
        query = f"""
        INSERT INTO {self.embedding_table_name}(note_id, model, embedding)
        VALUES ($1, $2, $3)
        """
        if note.content:
            embedding = await self._embedding_repo.insert(
                note_id,
                note.title if note.title else "",
                note.content
            )
            note.embeddings.append(embedding)
        # insert permissions
        query = f"""
        INSERT INTO {self.permission_table_name}(note_id, role_id)
        VALUES ($1, $2)
        """
        for permission in note.permissions:
            permission.note_id = note_id
            await self._db.execute(
                query,
                note_id, permission.role_id
            )
        note.note_id = note_id
        return note
    
    async def update(self, note):
        raise NotImplementedError("Not implemented yet")
    
    async def delete(self, note):
        raise NotImplementedError("Not implemented yet")
    
    async def select(self, note: NoteEntity) -> Optional[NoteEntity]:
        assert note.note_id
        record = await self._content_repo.select_by_id(note.note_id)
        if not record:
            return None
        
        # fetch embeddings
        embeddings = await self._embedding_repo.select(
            NoteEmbeddingEntity(
                note_id=note.note_id,
                model=UNDEFINED,
                embedding=UNDEFINED,
            )
        )
        record.embeddings = embeddings

        # fetch permissions
        permissions = await self._permission_repo.select(
            NotePermissionEntity(
                note_id=note.note_id,
                role_id=UNDEFINED,
            )
        )
        record.permissions = permissions
        return record

    async def search_notes(
        self, 
        search_type: SearchType,
        query: str, 
        pagination: Pagination
    ) -> List[NoteEntity]:

        # these parameters are common to all strategies __init__ fn
        common_init_parameters = {
            "db": self._db,
            "query": query,
            "limit": pagination.limit,
            "offset": pagination.offset,
        }
        strategy: NoteSearchStrategy
        if search_type == SearchType.NO_SEARCH:
            strategy = ContextNoteSearchStrategy(
                **common_init_parameters, 
                generator=self._embedding_repo.embedding_generator
            )
        elif search_type == SearchType.FULL_TEXT_TITLE:
            strategy = TitleLexemeNoteSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.FUZZY:
            strategy = FuzzyTitleContentSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.CONTEXT:
            strategy = ContextNoteSearchStrategy(
                **common_init_parameters, 
                generator=self._embedding_repo.embedding_generator
            )
        else: 
            raise ValueError(f"Unknown SearchType: {search_type}")

        note_entities = await strategy.search()
        return note_entities





    

    