"""Unit tests for :class:`src.grpc_mod.service.GrpcDirectoryService`.

These tests pin the gRPC adapter's behaviour on top of the shared
:class:`~tests.stubs.directory_service._StubDirectoryService` so they
do not require Postgres, SpiceDB, or any other infrastructure.

Coverage:

* :meth:`GrpcDirectoryService.GetDirectories` -- pagination, parent
  filter, permission denial.
* :meth:`GrpcDirectoryService.GetDirectory` -- not-found, permission
  denial, and `user_id` validation.
* :meth:`GrpcDirectoryService.PatchDirectory` -- not-found, patch-through.
* :meth:`GrpcDirectoryService.DeleteDirectory` -- not-found, success.
* :meth:`GrpcDirectoryService.CreateDirectory` -- validation, denial.
* :meth:`GrpcDirectoryService.GetNotesOfDirectory` -- happy path,
  input validation, permission denial.
"""

from __future__ import annotations

from typing import Optional, cast

import grpc
from grpc.aio import ServicerContext

from tests.stubs import _StubDirectoryService, _UserContextFactory, silent_logger
from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.grpc_mod.proto.note_pb2 import (
    AlterDirectoryRequest,
    CreateDirectoryRequest,
    DeleteDirectoryRequest,
    GetDirectoriesRequest,
    GetDirectoryRequest,
    GetNotesOfDirectoryRequest,
)
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.service import GrpcDirectoryService


def _to_grpc() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


def _service(impl: _StubDirectoryService) -> GrpcDirectoryService:
    return GrpcDirectoryService(
        directory_service=impl,
        log=silent_logger,
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )


async def test_get_directories_requires_user_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="")
    result = [
        d async for d in service.GetDirectories(request, cast(ServicerContext, context))
    ]

    assert result == []
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_get_directories_returns_only_user_visible_directories() -> None:
    dir_1 = DirectoryEntity(id="dir-1", slug="one", parent_directory_ids=["parent-a"], relations=[])
    dir_2 = DirectoryEntity(id="dir-2", slug="two", parent_directory_ids=["parent-a"], relations=[])
    dir_3 = DirectoryEntity(id="dir-3", slug="three", parent_directory_ids=["parent-b"], relations=[])

    impl = _StubDirectoryService()
    impl.directories_for_user["user-1"] = [dir_1, dir_2, dir_3]
    service = _service(impl)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="user-1", parent_id="parent-a", limit=1, offset=1)
    result = [
        d async for d in service.GetDirectories(request, cast(ServicerContext, context))
    ]

    assert impl.last_get_directories_user_id == "user-1"
    assert impl.get_directories_parent_id == "parent-a"
    assert impl.get_directories_limit == 1
    assert impl.get_directories_offset == 1
    assert context.code is None
    assert [d.id for d in result] == ["dir-2"]


async def test_get_directories_permission_denied_returns_perm_code() -> None:
    impl = _StubDirectoryService()
    impl.get_directories_deny = True
    service = _service(impl)
    context = _FakeContext()

    request = GetDirectoriesRequest(user_id="user-1")
    result = [
        d async for d in service.GetDirectories(request, cast(ServicerContext, context))
    ]

    assert result == []
    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_get_directory_returns_not_found_when_service_reports_missing() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.GetDirectory(
        GetDirectoryRequest(id="dir-missing", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_get_directory_returns_permission_denied_when_service_raises() -> None:
    impl = _StubDirectoryService()
    impl.get_directory_deny = True
    service = _service(impl)
    context = _FakeContext()

    result = await service.GetDirectory(
        GetDirectoryRequest(id="dir-1", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_get_directory_requires_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.GetDirectory(
        GetDirectoryRequest(id="", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_delete_directory_requires_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.DeleteDirectory(
        DeleteDirectoryRequest(id="", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_delete_directory_returns_not_found_when_service_reports_missing() -> None:
    impl = _StubDirectoryService()
    impl.delete_result = False
    service = _service(impl)
    context = _FakeContext()

    result = await service.DeleteDirectory(
        DeleteDirectoryRequest(id="dir-missing", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert impl.last_delete_id == "dir-missing"
    assert impl.last_delete_user_id == "user-1"
    assert result.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_delete_directory_returns_deleted_id_on_success() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.DeleteDirectory(
        DeleteDirectoryRequest(id="dir-1", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert impl.last_delete_id == "dir-1"
    assert result.id == "dir-1"
    assert context.code is None


async def test_delete_directory_returns_permission_denied_when_service_raises() -> None:
    impl = _StubDirectoryService()
    impl.delete_deny = True
    service = _service(impl)
    context = _FakeContext()

    result = await service.DeleteDirectory(
        DeleteDirectoryRequest(id="dir-1", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_patch_directory_requires_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(id="", user_id="user-1", name="new-name"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_patch_directory_returns_not_found_for_missing_directory() -> None:
    impl = _StubDirectoryService()
    impl.patch_result = None
    service = _service(impl)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(id="dir-missing", user_id="user-1", name="new-name"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.NOT_FOUND


async def test_patch_directory_passes_entity_to_service() -> None:
    impl = _StubDirectoryService()
    impl.patch_result = DirectoryEntity(
        id="dir-1",
        slug="new-name",
        description="new-description",
        parent_directory_ids=["new-parent"],
        relations=[],
    )
    service = _service(impl)
    context = _FakeContext()

    result = await service.PatchDirectory(
        AlterDirectoryRequest(
            id="dir-1",
            user_id="user-1",
            name="new-name",
            description="new-description",
            parent_ids=["new-parent"],
        ),
        cast(ServicerContext, context),
    )

    assert context.code is None
    assert impl.last_patch_entity is not None
    assert impl.last_patch_entity.id == "dir-1"
    assert impl.last_patch_entity.slug == "new-name"
    assert impl.last_patch_entity.description == "new-description"
    assert list(impl.last_patch_entity.parent_directory_ids) == ["new-parent"]
    assert result.id == "dir-1"
    assert result.slug == "new-name"


async def test_create_directory_requires_name() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    result = await service.CreateDirectory(
        CreateDirectoryRequest(name="", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT


async def test_create_directory_permission_denied() -> None:
    impl = _StubDirectoryService()
    impl.create_deny = True
    service = _service(impl)
    context = _FakeContext()

    result = await service.CreateDirectory(
        CreateDirectoryRequest(name="root", user_id="user-1"),
        cast(ServicerContext, context),
    )

    assert result.id == ""
    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_get_notes_of_directory_requires_directory_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    request = GetNotesOfDirectoryRequest(directory_id="", user_id="user-1")
    result = await service.GetNotesOfDirectory(request, cast(ServicerContext, context))

    assert list(result.notes) == []
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "directory_id is required"


async def test_get_notes_of_directory_requires_user_id() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    request = GetNotesOfDirectoryRequest(directory_id="dir-1", user_id="")
    result = await service.GetNotesOfDirectory(request, cast(ServicerContext, context))

    assert list(result.notes) == []
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert context.details == "user_id is required"


async def test_get_notes_of_directory_permission_denied() -> None:
    impl = _StubDirectoryService()
    impl.get_notes_deny = True
    service = _service(impl)
    context = _FakeContext()

    request = GetNotesOfDirectoryRequest(
        directory_id="dir-1", user_id="user-1", limit=10, offset=0
    )
    result = await service.GetNotesOfDirectory(request, cast(ServicerContext, context))

    assert list(result.notes) == []
    assert context.code == grpc.StatusCode.PERMISSION_DENIED


async def test_get_notes_of_directory_yields_paginated_notes() -> None:
    notes = [
        NoteEntity(note_id=f"note-{i}", title=f"note-{i}", author_id="user-1", content="")
        for i in range(3)
    ]
    impl = _StubDirectoryService()
    impl.notes_for_directory["dir-1"] = notes

    service = _service(impl)
    context = _FakeContext()

    request = GetNotesOfDirectoryRequest(
        directory_id="dir-1", user_id="user-1", limit=2, offset=0
    )
    result = await service.GetNotesOfDirectory(request, cast(ServicerContext, context))

    assert impl.last_get_notes_args is not None
    assert impl.last_get_notes_args[0] == "dir-1"
    assert impl.last_get_notes_args[1] == "user-1"
    assert impl.last_get_notes_args[2] == 2
    assert impl.last_get_notes_args[3] == 0
    assert [n.id for n in result.notes] == ["note-0", "note-1"]


async def test_get_notes_of_directory_rejects_negative_offset() -> None:
    impl = _StubDirectoryService()
    service = _service(impl)
    context = _FakeContext()

    request = GetNotesOfDirectoryRequest(
        directory_id="dir-1", user_id="user-1", limit=10, offset=-1
    )
    result = await service.GetNotesOfDirectory(request, cast(ServicerContext, context))

    assert list(result.notes) == []
    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "offset" in (context.details or "")
