from abc import ABC, abstractmethod
from ..note.embedding import NoteEmbeddingEntity

class NoteEmbeddingRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        metadata: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """inserts metadata
        
        Args:
        -----
        metadata: `NoteEmbeddingEntity`
            the metadata of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        metadata: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """updates metadata
        
        Args:
        -----
        metadata: `NoteEmbeddingEntity`
            the metadata of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    @abstractmethod
    async def delete(
        self,
        metadata: NoteEmbeddingEntity,
    ) -> NoteEmbeddingEntity:
        """delete metadata
        
        Args:
        -----
        metadata: `NoteEmbeddingEntity`
            the metadata of a note

        Returns:
        --------
        `NoteEmbeddingEntity`:
            the updated entity
        """
        ...

    

    