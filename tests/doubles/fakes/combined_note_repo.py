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


class _FakeCombinedNoteRepo(CombinedNoteRepoABC):
    """In-memory :class:`CombinedNoteRepoABC` used by pure unit tests.

    Delegates row reads to an optional :class:`_FakeNoteContentRepo`
    so the contents stay in sync; tracks directory / tag /
    attachment id sets in separate dicts so the facade's CRUD
    passes can drive them directly.
    """

    def __init__(
        self,
        content_repo: Optional["_FakeNoteContentRepo"] = None,
    ) -> None:
        self._content_repo = content_repo
        self.directory_ids_by_note: Dict[str, List[str]] = {}
        self.tag_ids_by_note: Dict[str, List[str]] = {}
        self.attachment_ids_by_note: Dict[str, List[str]] = {}

    async def select_by_id(
        self,
        note_id: str,
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> Optional[NoteEntity]:
        if self._content_repo is None:
            raise RuntimeError(
                "_FakeCombinedNoteRepo has no content_repo wired"
            )
        try:
            note = await self._content_repo.select_by_id(note_id)
        except RuntimeError:
            return None
        return self._decorate(note)

    async def select_by_ids(
        self,
        note_ids: List[str],
        *,
        include: Optional["NoteIncludeOptions"] = None,
    ) -> List[NoteEntity]:
        if not note_ids:
            raise ValueError("note_ids must not be empty")
        if self._content_repo is None:
            raise RuntimeError(
                "_FakeCombinedNoteRepo has no content_repo wired"
            )
        try:
            notes = await self._content_repo.select_by_ids(note_ids)
        except ValueError:
            raise
        return [self._decorate(n) for n in notes]

    def _decorate(self, note: NoteEntity) -> NoteEntity:
        if str(note.note_id) in self.directory_ids_by_note:
            note.directory_ids = list(
                self.directory_ids_by_note[str(note.note_id)]
            )
        else:
            note.directory_ids = []
        if str(note.note_id) in self.tag_ids_by_note:
            note.tag_ids = list(self.tag_ids_by_note[str(note.note_id)])
        else:
            note.tag_ids = []
        if str(note.note_id) in self.attachment_ids_by_note:
            note.attachment_ids = list(
                self.attachment_ids_by_note[str(note.note_id)]
            )
        else:
            note.attachment_ids = []
        return note


__all__ = [
    '_FakeCombinedNoteRepo',
]
