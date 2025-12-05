from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime

from .embedding import NoteEmbeddingEntity
from .permission import NotePermissionEntity
from api.undefined import *

@dataclass
class NoteEntity:
    """Represents one record of note.metadata"""
    note_id: UndefinedOr[int]
    title: UndefinedNoneOr[str]
    updated_at: UndefinedNoneOr[datetime]
    author_id: UndefinedNoneOr[int]
    content: UndefinedNoneOr[str]
    embeddings: List[NoteEmbeddingEntity]
    permissions: List[NotePermissionEntity]
