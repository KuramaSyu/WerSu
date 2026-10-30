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


class _FakeSpyNoteRepoFacade(NoteFacadeABC):
    """In-memory :class:`NoteRepoFacadeABC` used by unit tests.

    Stores notes by id; ``insert`` mints a sequential id, every other
    CRUD method delegates to the in-memory store.  Tests can drive
    behaviour by populating ``notes_by_id`` ahead of time or by
    reading ``insert_calls`` / ``select_calls`` for assertions.
    """

    def __init__(self) -> None:
        self.notes_by_id: Dict[str, NoteEntity] = {}
        self.select_calls: List[str] = []
        self.insert_calls: List[NoteEntity] = []
        self.update_calls: List[NoteEntity] = []
        self.delete_calls: List[str] = []
        self.search_calls: List[tuple] = []
        self._next_note_id = 0

    async def insert(self, note: NoteEntity, user: UserContextABC) -> NoteEntity:
        self.insert_calls.append(note)
        self._next_note_id += 1
        if note.note_id in (None, UNDEFINED):
            note.note_id = f"note-{self._next_note_id}"
        if note.permissions is UNDEFINED:
            note.permissions = []
        self.notes_by_id[str(note.note_id)] = note
        return note

    async def update(self, note: NoteEntity, ctx: UserContextABC) -> NoteEntity:
        self.update_calls.append(note)
        existing = self.notes_by_id.get(str(note.note_id))
        if existing is not None:
            self.notes_by_id[str(note.note_id)] = note
        return note

    async def delete(self, note_id: str, ctx: UserContextABC) -> Optional[List[NoteEntity]]:
        self.delete_calls.append(str(note_id))
        existing = self.notes_by_id.pop(str(note_id), None)
        return [existing] if existing is not None else None

    async def select_by_id(
        self,
        note_id: str,
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> Optional[NoteEntity]:
        del ctx, include_permissions
        self.select_calls.append(str(note_id))
        return self.notes_by_id.get(str(note_id))

    async def select_by_ids(
        self,
        note_ids: List[str],
        ctx: UserContextABC,
        *,
        include_permissions: bool = True,
    ) -> List[NoteEntity]:
        del ctx, include_permissions
        self.select_calls.append(list(note_ids))
        results: List[NoteEntity] = []
        for nid in note_ids:
            note = self.notes_by_id.get(nid)
            if note is None:
                raise ValueError(
                    f"Notes with ids {nid!r} could not be resolved"
                )
            results.append(note)
        return results

    async def search_notes(
        self,
        search_type: "SearchType",
        query: str,
        ctx: UserContextABC,
        pagination,
    ) -> List[NoteEntity]:
        self.search_calls.append((search_type, query, pagination))
        return []


__all__ = [
    '_FakeSpyNoteRepoFacade',
]
