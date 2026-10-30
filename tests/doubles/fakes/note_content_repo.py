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


class _FakeNoteContentRepo(NoteContentRepo):
    """In-memory NoteContentRepo used by pure unit tests."""

    def __init__(self, facade: Optional["_FakeNoteRepoFacade"] = None) -> None:
        self._store: dict[str, NoteEntity] = {}
        self._facade = facade
        self._next_id = 0

    def _lookup(self, note_id: str) -> Optional[NoteEntity]:
        existing = self._store.get(str(note_id))
        if existing is not None:
            return existing
        if self._facade is not None:
            return self._facade.notes_by_id.get(str(note_id))
        return None

    def seed(self, note: NoteEntity) -> None:
        """Insert `note` into the in-memory store keyed by its `note_id`."""
        self._store[str(note.note_id)] = note

    def contains(self, note_id: str) -> bool:
        """True when a note with ``note_id`` is stored."""
        return str(note_id) in self._store

    async def insert(self, metadata: NoteEntity) -> NoteEntity:
        # Mirror production: when the caller doesn't supply an id,
        # mint one so the facade can carry on with embedding / tags /
        # permission writes.  Otherwise keep what the caller passed.
        if is_undefined(metadata.note_id) or metadata.note_id is None:
            self._next_id += 1
            new_id = f"note-{self._next_id:04d}"
            metadata = NoteEntity(
                note_id=new_id,
                title=metadata.title,
                updated_at=metadata.updated_at,
                author_id=metadata.author_id,
                content=metadata.content,
                directory_ids=unwrap_undefined_or(metadata.directory_ids, []),
                tag_ids=unwrap_undefined_or(metadata.tag_ids, []),
                embeddings=unwrap_undefined_or(metadata.embeddings, []),
                permissions=unwrap_undefined_or(metadata.permissions, []),
            )
        self._store[str(metadata.note_id)] = metadata
        return metadata

    async def update(self, set: NoteEntity, where: NoteEntity) -> NoteEntity:
        existing = self._lookup(str(where.note_id))
        if existing is None:
            raise RuntimeError(f"note {where.note_id!r} not found")
        merged = NoteEntity(
            note_id=existing.note_id,
            title=set.title if set.title is not None else existing.title,
            content=set.content if set.content is not None else existing.content,
            updated_at=set.updated_at if set.updated_at is not None else existing.updated_at,
            author_id=set.author_id if set.author_id is not None else existing.author_id,
            embeddings=existing.embeddings,
            permissions=existing.permissions,
        )
        self._store[str(merged.note_id)] = merged
        return merged

    async def delete(self, metadata: NoteEntity) -> List[NoteEntity]:
        existing = self._store.pop(str(metadata.note_id), None)
        if existing is None and self._facade is not None:
            existing = self._facade.notes_by_id.pop(str(metadata.note_id), None)
        return [existing] if existing is not None else []

    async def select(self, metadata: NoteEntity) -> List[NoteEntity]:
        return [n for n in self._store.values() if n.note_id == metadata.note_id]

    async def select_by_id(self, note_id: str) -> NoteEntity:
        existing = self._lookup(note_id)
        if existing is None:
            raise RuntimeError(f"note {note_id!r} not found")
        return existing

    async def select_by_ids(self, note_ids: List[str]) -> List[NoteEntity]:
        if not note_ids:
            raise ValueError("note_ids must not be empty")
        missing = [nid for nid in note_ids if self._lookup(nid) is None]
        if missing:
            raise ValueError(
                f"Notes with ids {missing!r} could not be resolved"
            )
        return [self._lookup(str(nid)) for nid in note_ids]  # type: ignore[arg-type]  # noqa: F821


__all__ = [
    '_FakeNoteContentRepo',
]
