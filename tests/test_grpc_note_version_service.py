from datetime import datetime
import logging
from typing import List, Optional, cast

import grpc
from grpc.aio import ServicerContext

from tests.stubs.user_context import _UserContext as UserContext, _UserContextFactory
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.versioning import NoteVersionContent, NoteVersionEntry
from src.api.facades.note_facade import NoteRepoFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.grpc_mod.proto.note_pb2 import (
    GetNoteVersionContentRequest,
    GetNoteVersionsRequest,
    RestoreNoteVersionRequest,
)
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.service import GrpcNoteVersionService
from src.services.directory_activity_service import DirectoryActivityServiceABC


def _to_grpc() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()

class _FakeDirectoryActivityService(DirectoryActivityServiceABC):
    async def list_directory_activity(
        self,
        directory_id: Optional[str],
        actor: UserContext,
        max_depth: int = 10,
        limit: int = 25,
        offset: int = 0,
    ) -> List[NoteVersionEntry]:
        return []

class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _StubVersionRepo(NoteVersionRepoABC):
    def __init__(self) -> None:
        self.entries = [
            NoteVersionEntry(
                version_id="v-1",
                note_id="note-1",
                version_index=1,
                created_at=datetime(2026, 5, 18, 9, 0, 0),
                author_id="user-1",
                is_snapshot=True,
                snapshot_id="snap-1",
            )
        ]
        self.content = NoteVersionContent(
            note_id="note-1",
            version_index=1,
            created_at=datetime(2026, 5, 18, 9, 0, 0),
            author_id="user-1",
            title="Title",
            content="Content",
        )

    @property
    def max_deltas_per_snapshot(self) -> int:
        return 2

    async def record_initial_snapshot(self, note_id, title, content, author_id, created_at):
        raise NotImplementedError()

    async def append_version(self, note_id, old_title, old_content, new_title, new_content, author_id, created_at):
        raise NotImplementedError()

    async def list_versions(self, note_id: str, limit: int, offset: int) -> List[NoteVersionEntry]:
        return self.entries

    async def get_content_at_version(self, note_id: str, version_index: int) -> NoteVersionContent:
        return self.content


class _StubNoteRepo(NoteRepoFacadeABC):
    def __init__(self) -> None:
        self.last_updated: Optional[NoteEntity] = None

    async def insert(self, note: NoteEntity, user: UserContext):
        raise NotImplementedError()

    async def update(self, note: NoteEntity, ctx: UserContext):
        if note.permissions is UNDEFINED:
            note.permissions = []
        self.last_updated = note
        return note

    async def delete(self, note_id: str, ctx: UserContext):
        raise NotImplementedError()

    async def select_by_id(self, note_id: str, ctx: UserContext):
        raise NotImplementedError()

    async def select_by_ids(self, note_ids, ctx: UserContext):
        raise NotImplementedError()

    async def search_notes(self, search_type, query: str, ctx: UserContext, pagination):
        raise NotImplementedError()


def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.note_version")


async def test_get_note_versions_returns_entries() -> None:
    service = GrpcNoteVersionService(
        note_repo=_StubNoteRepo(),
        version_repo=_StubVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    context = _FakeContext()

    request = GetNoteVersionsRequest(note_id="note-1", limit=5, offset=0, user_id="user-1")
    result = [entry async for entry in service.GetNoteVersions(request, cast(ServicerContext, context))]

    assert context.code is None
    assert len(result) == 1
    assert result[0].note_id == "note-1"
    assert result[0].is_snapshot is True


async def test_get_note_version_content_returns_payload() -> None:
    service = GrpcNoteVersionService(
        note_repo=_StubNoteRepo(),
        version_repo=_StubVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    context = _FakeContext()

    request = GetNoteVersionContentRequest(note_id="note-1", version_index=1, user_id="user-1")
    result = await service.GetNoteVersionContent(request, cast(ServicerContext, context))

    assert context.code is None
    assert result.note_id == "note-1"
    assert result.title == "Title"
    assert result.content == "Content"


async def test_restore_note_version_updates_note() -> None:
    note_repo = _StubNoteRepo()
    service = GrpcNoteVersionService(
        note_repo=note_repo,
        version_repo=_StubVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    context = _FakeContext()

    request = RestoreNoteVersionRequest(note_id="note-1", version_index=1, user_id="user-1")
    result = await service.RestoreNoteVersion(request, cast(ServicerContext, context))

    assert context.code is None
    assert note_repo.last_updated is not None
    assert note_repo.last_updated.note_id == "note-1"
    assert note_repo.last_updated.title == "Title"
    assert note_repo.last_updated.content == "Content"
    assert result.id == "note-1"
