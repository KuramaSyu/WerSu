from abc import ABC, abstractmethod
from typing import List
from db.entities import NoteEmbeddingEntity

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
        embedding: NoteEmbeddingEntity,
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

    

    