from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from .embedding import NoteEmbeddingEntity
from .permission import NotePermissionEntity

@dataclass
class NoteEntity:
    """Represents one record of note.metadata"""
    note_id: Optional[int]
    title: Optional[str]
    updated_at: Optional[datetime]
    author_id: Optional[int]
    content: Optional[str]
    embeddings: List[NoteEmbeddingEntity]
    permissions: List[NotePermissionEntity]
