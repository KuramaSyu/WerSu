from abc import ABC, abstractmethod
from typing import List, Optional

import asyncpg

from db.entities import NoteEntity
from db import Database
from db.entities.note.embedding import NoteEmbeddingEntity
from db.repos.note.content import NoteContentRepo

from db.repos.note.permission import NotePermissionRepo
from db.table import TableABC


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
        all 3 relations used for this
        
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
        """select note
        
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

class NotePostgreRepoFacade(NoteRepoFacadeABC):
    def __init__(
        self, 
        db: Database,
        content_repo: NoteContentRepo,
        embedding_repo: NoteEmbeddingEntity,
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

        # insert embeddings
        query = f"""
        INSERT INTO {self.embedding_table_name}(model, embedding)
        VALUES ($1, $2)
        """
        for embedding in note.embeddings:
            embedding.note_id = note_id
            await self._db.execute(
                query,
                note_id, embedding
            )
        
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
        record = await self._






    

    