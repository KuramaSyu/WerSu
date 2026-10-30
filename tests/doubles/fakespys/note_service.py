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


class _FakeSpyNoteService(NoteServiceABC):
    """In-memory :class:`NoteServiceABC` used by directory-service tests.

    Records every call for assertions, implements the small subset
    :class:`DirectoryServiceImpl` actually uses (``delete_note``,
    ``update_note``), and turns every other call into a clear
    ``NotImplementedError`` so accidental fallthroughs surface
    immediately.
    """

    def __init__(self) -> None:
        self.delete_calls: List[str] = []
        self.update_calls: List[NoteEntity] = []

    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> NoteResponse:  # pragma: no cover - unused by directory tests
        raise NotImplementedError

    async def insert_note(
        self, note: NoteEntity, user_ctx: UserContextABC
    ) -> NoteEntity:  # pragma: no cover - unused by directory tests
        raise NotImplementedError

    async def update_note(
        self, note: NoteEntity, user_ctx: UserContextABC
    ) -> NoteEntity:
        self.update_calls.append(note)
        return note

    async def delete_note(
        self, note_id: str, user_ctx: UserContextABC
    ) -> Optional[NoteEntity]:
        self.delete_calls.append(str(note_id))
        return None

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ) -> List[NoteEntity]:  # pragma: no cover - unused
        raise NotImplementedError

    async def get_notes(
        self,
        note_ids: List[str],
        user_ctx: UserContextABC,
        options=None,
    ) -> List[NoteEntity]:  # pragma: no cover - unused
        raise NotImplementedError


__all__ = [
    '_FakeSpyNoteService',
]
