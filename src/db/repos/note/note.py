from abc import ABC, abstractmethod
from dataclasses import replace
from enum import Enum
from typing import List, Optional, Type
import typing

import asyncpg

from src.api.types import LoggingProvider, Pagination
from src.api.user_context import UserContextABC
from src.db.entities import NoteEntity
from src.db.database import Database
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.repos.note.content import NoteContentRepo

from src.db.repos.note.permission import NotePermissionRepo
from src.db.table import TableABC
from src.api.undefined import UNDEFINED
from src.db.entities.note.permission import NotePermissionEntity
from src.db.repos.note.embedding import NoteEmbeddingRepo


class SearchType(Enum):
    NO_SEARCH = 1
    FULL_TEXT_TITLE = 2
    FUZZY = 3
    CONTEXT = 4


class UserContext(UserContextABC):
    def __init__(self, user_id: str):
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        return self._user_id


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
        ctx: UserContext,
    ) -> NoteEntity:
        """updates note (content only)
        
        Args:
        -----
        note: `NoteEntity`
            the note

        Returns:
        --------
        `NoteEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        note_id: str,
        ctx: UserContext,
    ) -> Optional[List[NoteEntity]]:
        """delete note
        
        Args:
        -----
        note_id: `str`
            the ID of the note to delete

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
        """
        ...


    @abstractmethod
    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContext,
    ) -> Optional[NoteEntity]:
        """select a whole note by its ID
        
        Args:
        -----
        note_id: `str`
            the ID of the note

            
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
        ctx: UserContext,
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
        logging_provider: LoggingProvider,
    ):
        self._db = db
        self._content_repo = content_repo
        self._embedding_repo = embedding_repo
        self._permission_repo = permission_repo
        self.log = logging_provider(__name__, self)

    
    async def insert(self, note: NoteEntity):
        # insert note itself
        query = f"""
        INSERT INTO {self.content_table_name}(title, content, updated_at, author_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """
        note_id: str = (await self._db.fetchrow(
            query, 
            note.title, note.content, note.updated_at, note.author_id
        ))["id"] 
        self.log.debug(f"Inserted note with ID: {note_id}")

        # insert embeddings
        assert note.embeddings == [] or note.embeddings is UNDEFINED
        note.embeddings = []
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
        if isinstance(note.permissions, list):
            for permission in note.permissions:
                permission.note_id = note_id
                await self._db.execute(
                    query,
                    note_id, permission.role_id
                )
        else:
            note.permissions = []  # to ensure it's the same value as the SQL return
        note.note_id = note_id
        return note
    
    async def update(self, note: NoteEntity, ctx: UserContext) -> NoteEntity:
        # update content
        note_entity = await self._content_repo.update(
            set=replace(note, embeddings=UNDEFINED, permissions=UNDEFINED, note_id=UNDEFINED),
            where=NoteEntity(note_id=note.note_id)
        )

        # add removed embeddings and permissions
        note_entity.embeddings = note.embeddings or []
        note_entity.permissions = note.permissions or []
        return note_entity

    async def delete(self, note_id: str, ctx: UserContext) -> Optional[List[NoteEntity]]:
        return await self._content_repo.delete(NoteEntity(note_id=note_id, author_id=ctx.user_id))
    
    async def select_by_id(self, note_id: str, ctx: UserContext) -> Optional[NoteEntity]:
        record = await self._content_repo.select_by_id(note_id)
        if not record:
            return None
        
        # fetch embeddings
        embeddings = await self._embedding_repo.select(
            NoteEmbeddingEntity(
                note_id=note_id,
                model=UNDEFINED,
                embedding=UNDEFINED,
            )
        )
        record.embeddings = embeddings

        # fetch permissions
        select_permissions = getattr(self._permission_repo, "select", None)
        if callable(select_permissions):
            select_permissions = typing.cast(
                typing.Callable[[NotePermissionEntity], typing.Awaitable[List[NotePermissionEntity]]],
                select_permissions,
            )
            permissions = await select_permissions(
                NotePermissionEntity(
                    note_id=note_id,
                    role_id=UNDEFINED,
                )
            )
        else:
            permissions = []
        record.permissions = permissions
        return record

    async def search_notes(
        self, 
        search_type: SearchType,
        query: str, 
        ctx: UserContext,
        pagination: Pagination
    ) -> List[NoteEntity]:

        from src.db.repos.note.search_strategy import (
            ContextNoteSearchStrategy,
            DateNoteSearchStrategy,
            FuzzyTitleContentSearchStrategy,
            WebNoteSearchStrategy,
        )

        # these parameters are common to all strategies __init__ fn
        common_init_parameters = {
            "db": self._db,
            "query": query,
            "limit": pagination.limit,
            "offset": pagination.offset,
            "user_id": ctx.user_id,
        }
        if search_type == SearchType.NO_SEARCH:
            strategy = DateNoteSearchStrategy(**common_init_parameters)
        elif search_type == SearchType.FULL_TEXT_TITLE:
            strategy = WebNoteSearchStrategy(**common_init_parameters)
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





    

    