from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple
from src.api.repos.combined_note_repo import CombinedNoteRepoABC
from src.api.repos.tag_repo import TagRepoABC, TagSubjectType
from src.api.services.directory_service import DirectoryIncludeOptions
from src.api.services.note_service import NoteIncludeOptions
from src.api.other.undefined import UNDEFINED, is_undefined, unwrap_undefined_or
from src.api.other.user_context import UserContextABC
from src.db.database import DatabaseABC
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.embedding import NoteEmbeddingEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.tag import TagEntity
from src.db.entities.note.versioning import NoteVersionEntry
from src.api.facades.directory_facade import DirectoryFacadeABC
from src.api.repos.directory_repo import (
    DirectoryChildType,
    DirectoryHierarchyType,
    DirectoryParentType,
)
from src.api.repos.permission_repo import PermissionRepoABC
from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.api.repos.note_content_repo import NoteContentRepo
from src.db.table import TableABC
from src.db.repos.note.embedding import NoteEmbeddingRepo
from src.api.facades.note_facade import NoteFacadeABC, SearchType
from src.api.services.note_service import NoteResponse, NoteServiceABC
from src.services.attachment_facade import Attachment, AttachmentFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC


class _StubEmbeddingGenerator:
    """Lightweight embedding generator to avoid heavy ML model loads."""

    @property
    def model_name(self) -> str:
        return "fake"

    def generate(self, text: str) -> Any:
        return [0.0]

    def tensor_to_sequence(self, tensor: Any) -> List[float]:
        return [0.0]

    def tensor_to_str_vec(self, tensor: Any) -> str:
        """Mirror the real generator's API for the ``ContextNoteSearchStrategy``.

        ``tensor_to_str_vec`` formats a tensor as a Postgres
        ``[a,b,c]``-style vector literal -- only the context search
        path uses it; the fakes return a matching string so the
        strategy call site stays untouched.
        """
        return "[0.0]"


class _StubEmbeddingRepo(NoteEmbeddingRepo):
    """Stub embedding repo used by tests that don't need real ML embeddings."""

    def __init__(self) -> None:
        self._generator = _StubEmbeddingGenerator()

    @property
    def embedding_generator(self) -> _StubEmbeddingGenerator:
        return self._generator

    async def insert(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=note_id, model="fake", embedding=[0.0])

    async def update(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=note_id, model="fake", embedding=[0.0])

    async def _update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=where.note_id, model=where.model, embedding=[0.0])

    async def delete(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return embedding

    async def select(self, embedding: NoteEmbeddingEntity) -> List[NoteEmbeddingEntity]:
        return [embedding]


__all__ = [
    '_StubEmbeddingGenerator',
    '_StubEmbeddingRepo',
]
