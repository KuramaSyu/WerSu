from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime

from asyncpg import Record

from src.db.entities.directory.directory import DirectoryEntity
from src.db.repos.note.permission import PartialRelationship, Relationship


from .embedding import NoteEmbeddingEntity
from .permission import NotePermissionEntity
from src.api.undefined import *


@dataclass
class NoteEntity:
    """Represents one record of note.metadata"""
    note_id: UndefinedOr[str] = UNDEFINED
    title: UndefinedNoneOr[str] = UNDEFINED
    updated_at: UndefinedNoneOr[datetime] = UNDEFINED
    author_id: UndefinedNoneOr[str] = UNDEFINED
    content: UndefinedNoneOr[str] = UNDEFINED
    embeddings: UndefinedOr[Sequence[NoteEmbeddingEntity]] = UNDEFINED
    permissions: UndefinedOr[Sequence[Relationship]] = UNDEFINED
    parent_dir_id: UndefinedOr[str] = UNDEFINED

    @staticmethod
    def from_record(record: Record | Dict[str, Any]) -> "NoteEntity":
        return NoteEntity(
            note_id=record.get("id", UNDEFINED),
            title=record.get("title", UNDEFINED),
            updated_at=record.get("updated_at", UNDEFINED),
            author_id=record.get("author_id", UNDEFINED),
            content=record.get("content", UNDEFINED),
            embeddings=[],
            permissions=[]
        )

    def to_grpc_dict(self) -> Dict[str, Any]:
        return {
            "id": self.note_id,
            "title": self.title,
            "updated_at": self.updated_at,
            "author_id": self.author_id,
            "content": self.content,
            "embeddings": self.embeddings,
        }
