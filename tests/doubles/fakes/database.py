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


class _FakeDatabase(DatabaseABC):
    """In-memory :class:`DatabaseABC` used by pure unit tests.

    Only the methods :class:`src.db.repos.note.note.NoteFacadeImpl`
    and the search strategies call are implemented; the rest raise
    to make accidental use loud.  Tests queue the responses they
    expect to see.
    """

    def __init__(self) -> None:
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchrow_responses: list[dict] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetch_responses: list[list[dict]] = []

    async def init_db(self) -> None:
        return None

    async def close(self) -> None:
        return None

    @property
    def pool(self):
        raise NotImplementedError("_FakeDatabase has no asyncpg pool")

    async def execute(self, query: str, *args) -> str:
        raise NotImplementedError("_FakeDatabase.execute is not implemented")

    async def fetch(self, query: str, *args):
        self.fetch_calls.append((query, args))
        if not self.fetch_responses:
            raise AssertionError("_FakeDatabase.fetch called without a queued response")
        return self.fetch_responses.pop(0)

    async def fetchrow(self, query: str, *args):
        self.fetchrow_calls.append((query, args))
        if not self.fetchrow_responses:
            raise AssertionError("_FakeDatabase.fetchrow called without a queued response")
        return self.fetchrow_responses.pop(0)


__all__ = [
    '_FakeDatabase',
]
