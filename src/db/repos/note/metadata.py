from abc import ABC, abstractmethod
from entities import NoteMetadataEntity

class NotesMetadataRepo(ABC):

    @abstractmethod
    async def insert(
        self,
        metadata: NoteMetadataEntity,
    ) -> NoteMetadataEntity:
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
        metadata: NoteMetadataEntity,
    ) -> NoteMetadataEntity:
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
        metadata: NoteMetadataEntity,
    ) -> NoteMetadataEntity:
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

    

    