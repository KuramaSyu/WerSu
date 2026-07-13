from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence
from datetime import datetime

from asyncpg import Record

from src.db.entities.directory.directory import DirectoryEntity
from src.api.other.visitor import AcceptsVisitor, EntityVisitor
from src.api import Relationship


from .embedding import NoteEmbeddingEntity
from .permission import NotePermissionEntity
from src.api.other.undefined import *


@dataclass
class NoteEntity(AcceptsVisitor):
    """Represents the domain modal of a NoteEntity. Its database representation
    looks differently. No 1:1 translation possible here."""
    note_id: UndefinedOr[str] = UNDEFINED
    title: UndefinedNoneOr[str] = UNDEFINED
    updated_at: UndefinedNoneOr[datetime] = UNDEFINED
    author_id: UndefinedNoneOr[str] = UNDEFINED
    content: UndefinedNoneOr[str] = UNDEFINED
    directory_ids: UndefinedOr[List[str]] = UNDEFINED
    tag_ids: UndefinedOr[List[str]] = UNDEFINED

    # for internal usage only
    embeddings: UndefinedOr[List[NoteEmbeddingEntity]] = UNDEFINED

    # for internal usage only
    permissions: UndefinedOr[List[Relationship]] = UNDEFINED


    def visit(self, visitor: EntityVisitor):
        """Dispatch this note to ``visitor.visit_note``."""
        return visitor.visit_note(self)

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
