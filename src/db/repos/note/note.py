from abc import ABC, abstractmethod
from typing import List
from entities import NoteEntity

from db.database import Database


class NoteRepoABC(ABC):

    @property
    def embedding_table(self) -> str:
        return "note.embedding"

    @property
    def content_table(self) -> str:
        return "note.content"
    
    @property
    def permission_table(self) -> str:
        return "note.permission"

    @abstractmethod
    async def insert(
        self,
        note: NoteEntity,
    ) -> NoteEntity:
        """inserts note
        
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
            the note of a note

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
    ) -> NoteEntity:
        """select note
        
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

class NotePostgreRepo(NoteRepoABC):
    def __init__(self, db: Database):
        self._db = db
    
    async def insert(self, note: NoteEntity):
        # insert note itself
        query = f"""
        INSERT INTO {self.content_table}(title, content, updated_at, author_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """
        note_id: int = await self._db.fetch(
            query, 
            note.title, note.content, note.updated_at, note.author_id
        )[0]

        # insert embeddings
        query = f"""
        INSERT INTO {self.embedding_table}(model, embedding)
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
        INSERT INTO {self.permission_table}(note_id, role_id)
        VALUES ($1, $2)
        """
        for permission in note.permissions:
            permission.note_id = note_id
            await self._db.execute(
                query,
                note_id, permission.role_id
            )
        return note




    

    