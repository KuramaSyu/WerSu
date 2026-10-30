from datetime import datetime
import logging
from typing import List, Optional, cast

import grpc
from grpc.aio import ServicerContext

from tests.doubles import _DummyUserContext as UserContext, _StubUserContextFactory
from src.api.other.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.versioning import NoteVersionContent, NoteVersionEntry
from src.api.facades.note_facade import NoteFacadeABC
from src.db.repos.note.versioning import NoteVersionRepoABC
from src.grpc_mod.proto.note_pb2 import (
    GetNoteVersionContentRequest,
    GetNoteVersionsRequest,
    RestoreNoteVersionRequest,
)
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.note_version_service import GrpcNoteVersionService
from src.services.directory_activity_service import DirectoryActivityServiceABC

from tests.doubles.dummies.fake_context import _DummyContext
from tests.doubles.fakespys.grpc_note_version_service import _FakeSpyDirectoryActivityService
from tests.doubles.stubs.grpc_note_version_service import _StubGrpcNoteVersionRepo, _StubGrpcNoteRepo


def _to_grpc() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()








def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.note_version")


async def test_get_note_versions_returns_entries() -> None:
    service = GrpcNoteVersionService(
        note_repo=_StubGrpcNoteRepo(),
        version_repo=_StubGrpcNoteVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeSpyDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_StubUserContextFactory(),
    )
    context = _DummyContext()

    request = GetNoteVersionsRequest(note_id="note-1", limit=5, offset=0, user_id="user-1")
    result = [entry async for entry in service.GetNoteVersions(request, cast(ServicerContext, context))]

    assert context.code is None
    assert len(result) == 1
    assert result[0].note_id == "note-1"
    assert result[0].is_snapshot is True


async def test_get_note_version_content_returns_payload() -> None:
    service = GrpcNoteVersionService(
        note_repo=_StubGrpcNoteRepo(),
        version_repo=_StubGrpcNoteVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeSpyDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_StubUserContextFactory(),
    )
    context = _DummyContext()

    request = GetNoteVersionContentRequest(note_id="note-1", version_index=1, user_id="user-1")
    result = await service.GetNoteVersionContent(request, cast(ServicerContext, context))

    assert context.code is None
    assert result.note_id == "note-1"
    assert result.title == "Title"
    assert result.content == "Content"


async def test_restore_note_version_updates_note() -> None:
    note_repo = _StubGrpcNoteRepo()
    service = GrpcNoteVersionService(
        note_repo=note_repo,
        version_repo=_StubGrpcNoteVersionRepo(),
        log=_log_provider,
        directory_activity_service=_FakeSpyDirectoryActivityService(),
        to_grpc=_to_grpc(),
        context_factory=_StubUserContextFactory(),
    )
    context = _DummyContext()

    request = RestoreNoteVersionRequest(note_id="note-1", version_index=1, user_id="user-1")
    result = await service.RestoreNoteVersion(request, cast(ServicerContext, context))

    assert context.code is None
    assert note_repo.last_updated is not None
    assert note_repo.last_updated.note_id == "note-1"
    assert note_repo.last_updated.title == "Title"
    assert note_repo.last_updated.content == "Content"
    assert result.id == "note-1"
