from abc import ABC, abstractmethod
from entities import NoteEntity

class NoteMetadataRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        metadata: NoteEntity,
    ) -> NoteEntity:
        """inserts metadata
        
        Args:
        -----
        metadata: `NoteMetadataEntity`
            the metadata of a note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity (updated ID)
        """
        ...

    @abstractmethod
    async def update(
        self,
        metadata: NoteEntity,
    ) -> NoteEntity:
        """updates metadata
        
        Args:
        -----
        metadata: `NoteMetadataEntity`
            the metadata of a note

        Returns:
        --------
        `NoteMetadataEntity`:
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
        metadata: `NoteMetadataEntity`
            the metadata of a note

        Returns:
        --------
        `NoteMetadataEntity`:
            the updated entity
        """
        ...

    

    