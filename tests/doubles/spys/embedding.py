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


class _RecordingEmbeddingGenerator:
    """Test double that records every ``generate`` invocation.

    Lets unit tests assert that
    :class:`src.services.note.NoteServiceImpl` and the note
    facade actually drive the generator end-to-end on insert and
    update, without loading a real ML model.  Each
    ``generate(text)`` call appends ``text`` to
    :attr:`generate_calls` and increments
    :attr:`generate_count`; the returned tensor is a fixed
    one-element list so :meth:`tensor_to_sequence` and
    :meth:`tensor_to_str_vec` keep working for the context-search
    strategy.
    """

    MODEL_NAME = "recording-fake"

    def __init__(self) -> None:
        self.generate_calls: List[str] = []
        self.generate_count: int = 0

    @property
    def model_name(self) -> str:
        return self.MODEL_NAME

    def generate(self, text: str) -> List[float]:
        self.generate_calls.append(text)
        self.generate_count += 1
        return [0.0]

    def tensor_to_sequence(self, tensor: Any) -> List[float]:
        return [0.0]

    def tensor_to_str_vec(self, tensor: Any) -> str:
        return "[0.0]"


class _RecordingEmbeddingRepo(NoteEmbeddingRepo):
    """Embedding repo backed by a :class:`_RecordingEmbeddingGenerator`.

    Records every ``insert`` and ``update`` call so tests can wait
    for the facade's fire-and-forget background task to complete
    before asserting on the generator.  Each repo call also
    triggers the generator through :meth:`_compose`, matching
    production where the repo encodes ``title + content`` via the
    embedding model.
    """

    def __init__(self) -> None:
        self._generator = _RecordingEmbeddingGenerator()
        self.insert_calls: List[tuple[str, str, str]] = []
        self.update_calls: List[tuple[str, str, str]] = []

    @property
    def embedding_generator(self) -> _RecordingEmbeddingGenerator:
        return self._generator

    @property
    def generate_calls(self) -> List[str]:
        """Texts the backing generator has been asked to embed."""
        return self._generator.generate_calls

    @staticmethod
    def _compose(title: str, content: str) -> str:
        """Mirror the production compose rule (``f"{title}\\n{content}"``)."""
        return f"{title}\n{content}"

    async def insert(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        self.insert_calls.append((note_id, title, content))
        # Drive the generator so tests can assert the generator saw
        # the same input the repo received.  The fake ``generate``
        # is synchronous and returns immediately, so awaiting it is
        # equivalent to a function call here, but we still await it
        # to keep the repo's async signature honest.
        await asyncio.to_thread(self._generator.generate, self._compose(title, content))
        return NoteEmbeddingEntity(
            note_id=note_id, model=self._generator.model_name, embedding=[0.0],
        )

    async def update(self, note_id: str, title: str, content: str) -> NoteEmbeddingEntity:
        self.update_calls.append((note_id, title, content))
        await asyncio.to_thread(self._generator.generate, self._compose(title, content))
        return NoteEmbeddingEntity(
            note_id=note_id, model=self._generator.model_name, embedding=[0.0],
        )

    async def _update(self, set: NoteEmbeddingEntity, where: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return NoteEmbeddingEntity(note_id=where.note_id, model=where.model, embedding=[0.0])

    async def delete(self, embedding: NoteEmbeddingEntity) -> NoteEmbeddingEntity:
        return embedding

    async def select(self, embedding: NoteEmbeddingEntity) -> List[NoteEmbeddingEntity]:
        return [embedding]


__all__ = [
    '_RecordingEmbeddingGenerator',
    '_RecordingEmbeddingRepo',
]
