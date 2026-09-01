"""Fast unit tests for :class:`src.grpc_mod.service.GrpcNoteService`.

`GetNote` is the only RPC whose wire shape changed in the visitor
refactor: it now returns the proto :class:`NoteResponse` (which wraps
the resolved :class:`Note` plus an attachment id -> JWT map for
temporary users).  These tests pin that contract end-to-end against
the gRPC servicer.

Wire shape asserted:

* :meth:`GetNote` returns a proto :class:`NoteResponse` with the
  resolved note forwarded through
  :meth:`ConvertToGrpcVisitor.visit_note_response`.
* A miss at the service layer sets ``NOT_FOUND`` and returns an
  empty :class:`NoteResponse` (no inner :class:`Note`, empty
  ``id_token_map``).
* For a temporary-user call, the JWT map minted by the service is
  forwarded verbatim into the proto ``id_token_map``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, cast

import grpc
from grpc.aio import ServicerContext

from tests.stubs.user_context import _UserContextFactory
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import UserContextABC
from src.api.services.note_service import NoteIncludeOptions, NoteResponse, NoteServiceABC
from src.db.entities.note.metadata import NoteEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import (
    AlterNoteRequest,
    GetNoteRequest,
    IdsOrUndefined,
    NoteResponse as GrpcNoteResponse,
    PostNoteRequest,
)
from src.grpc_mod.service import GrpcNoteService


class _FakeContext:
    def __init__(self) -> None:
        self.code: Optional[grpc.StatusCode] = None
        self.details: Optional[str] = None

    def set_code(self, code: grpc.StatusCode) -> None:
        self.code = code

    def set_details(self, details: str) -> None:
        self.details = details


class _StubNoteService(NoteServiceABC):
    """`NoteServiceABC` stub that returns a preset :class:`NoteResponse`."""

    def __init__(self, response: NoteResponse) -> None:
        self._response = response
        self.last_note_id: Optional[str] = None
        self.last_user_ctx: Optional[UserContextABC] = None

    async def get_note(
        self,
        note_id: str,
        user_ctx: UserContextABC,
        *,
        include: Optional[NoteIncludeOptions] = None,
    ) -> NoteResponse:
        self.last_note_id = note_id
        self.last_user_ctx = user_ctx
        self.last_include = include
        return self._response

    async def insert_note(self, note, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def update_note(self, note, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover - unused
        raise NotImplementedError

    async def search_notes(
        self,
        search_type: str,
        query: str,
        user_ctx: UserContextABC,
        limit: int,
        offset: int,
    ):  # pragma: no cover - unused
        raise NotImplementedError

    async def get_notes(
        self,
        note_ids,
        user_ctx,
        options=None,
    ):  # pragma: no cover - unused
        raise NotImplementedError


def _log_provider(*_args, **_kwargs):
    return logging.getLogger("test.grpc.note")


def _to_grpc() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()


def _make_service(response: NoteResponse) -> tuple[GrpcNoteService, _StubNoteService]:
    note_service = _StubNoteService(response)
    service = GrpcNoteService(
        note_service=note_service,
        log=_log_provider,
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    return service, note_service


async def test_get_note_returns_note_response_with_resolved_note() -> None:
    """`GetNote` returns a `NoteResponse` wrapping the resolved `Note`."""
    entity = NoteEntity(
        note_id="note-1",
        title="hello",
        content="world",
        author_id="user-1",
        updated_at=datetime(2026, 7, 3, 12, 0, 0),
        permissions=[],
    )
    service, stub = _make_service(NoteResponse(note=entity))
    context = _FakeContext()

    request = GetNoteRequest(id="note-1", user_id="user-1")
    proto = await service.GetNote(request, cast(ServicerContext, context))

    assert isinstance(proto, GrpcNoteResponse)
    assert context.code is None
    assert proto.note.id == "note-1"
    assert proto.note.title == "hello"
    assert proto.note.content == "world"
    assert proto.note.author_id == "user-1"
    assert dict(proto.id_token_map) == {}
    assert stub.last_note_id == "note-1"
    assert stub.last_user_ctx is not None
    assert stub.last_user_ctx.user_id == "user-1"


async def test_get_note_returns_not_found_with_empty_response_on_miss() -> None:
    """A `None` note from the service yields `NOT_FOUND` + empty `NoteResponse`."""
    service, stub = _make_service(NoteResponse(note=None))
    context = _FakeContext()

    request = GetNoteRequest(id="ghost", user_id="user-1")
    proto = await service.GetNote(request, cast(ServicerContext, context))

    assert isinstance(proto, GrpcNoteResponse)
    assert proto.note.id == ""
    assert dict(proto.id_token_map) == {}
    assert context.code == grpc.StatusCode.NOT_FOUND
    assert context.details == (
        "Note not found where user with id user-1 has permissions"
    )
    assert stub.last_note_id == "ghost"


async def test_get_note_forwards_temporary_user_jwt_map_to_proto() -> None:
    """For a temp-user call, `id_token_map` flows into the proto map verbatim."""
    entity = NoteEntity(
        note_id="note-1",
        title="t",
        content="c",
        author_id="user-1",
        permissions=[],
    )
    service, _stub = _make_service(
        NoteResponse(
            note=entity,
            id_token_map={"att-a": "jwt:user-1:att-a", "att-b": "jwt:user-1:att-b"},
        )
    )
    context = _FakeContext()

    request = GetNoteRequest(id="note-1", user_id="user-1")
    proto = await service.GetNote(request, cast(ServicerContext, context))

    assert isinstance(proto, GrpcNoteResponse)
    assert context.code is None
    assert proto.note.id == "note-1"
    assert dict(proto.id_token_map) == {
        "att-a": "jwt:user-1:att-a",
        "att-b": "jwt:user-1:att-b",
    }


# ---------------------------------------------------------------------------
# PostNote: directory_ids is preferred over shelf_id; one of both is required.
# ---------------------------------------------------------------------------


class _CapturingNoteService(NoteServiceABC):
    """Records the entity handed to `insert_note` and echoes a fake id."""

    def __init__(self) -> None:
        self.last_note = None
        self.last_user_ctx = None

    async def insert_note(self, note, user_ctx):
        self.last_note = note
        self.last_user_ctx = user_ctx
        # the repo would normally assign an id; fake one so the
        # grpc visitor can serialize the response without raising.
        if note.note_id is None or note.note_id is UNDEFINED:
            note.note_id = "note-fake-id"
        return note

    async def update_note(self, note, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_note(self, note_id, user_ctx, *, include=None):  # pragma: no cover
        raise NotImplementedError

    async def search_notes(  # pragma: no cover
        self, search_type, query, user_ctx, limit, offset,
    ):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):  # pragma: no cover
        raise NotImplementedError


def _make_capturing_service() -> tuple[GrpcNoteService, _CapturingNoteService]:
    note_service = _CapturingNoteService()
    service = GrpcNoteService(
        note_service=note_service,
        log=_log_provider,
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    return service, note_service


async def test_post_note_with_directory_ids_forwards_them_and_skips_shelf() -> None:
    """directory_ids takes priority: shelf_id is ignored when set."""
    service, stub = _make_capturing_service()
    context = _FakeContext()

    request = PostNoteRequest(
        title="hello",
        content="world",
        author_id="user-1",
        user_id="user-1",
        shelf_id="shelf-should-be-ignored",
        directory_ids=["dir-a", "dir-b"],
    )
    proto = await service.PostNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert proto.id == "" or proto.title == "hello"  # proto echo via visitor
    assert stub.last_note is not None
    # directory_ids flowed through verbatim
    assert list(stub.last_note.directory_ids) == ["dir-a", "dir-b"]
    # shelf_ids left UNDEFINED so the facade does not consult a shelf
    assert stub.last_note.shelf_ids is UNDEFINED


async def test_post_note_with_shelf_id_only_passes_shelf_to_facade() -> None:
    """Only shelf_id: directory_ids stays UNDEFINED, shelf_ids = [shelf_id]."""
    service, stub = _make_capturing_service()
    context = _FakeContext()

    request = PostNoteRequest(
        title="hello",
        content=None,
        author_id="user-1",
        user_id="user-1",
        shelf_id="shelf-only",
    )
    await service.PostNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert stub.last_note.directory_ids is UNDEFINED
    assert list(stub.last_note.shelf_ids) == ["shelf-only"]


async def test_post_note_without_directory_or_shelf_returns_invalid_argument() -> None:
    """Both empty -> INVALID_ARGUMENT, no entity forwarded."""
    service, stub = _make_capturing_service()
    context = _FakeContext()

    request = PostNoteRequest(
        title="hello",
        content=None,
        author_id="user-1",
        user_id="user-1",
    )
    proto = await service.PostNote(request, cast(ServicerContext, context))

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "directory_ids or shelf_id" in (context.details or "")
    assert stub.last_note is None
    assert proto.id == ""


async def test_post_note_with_blank_strings_is_treated_as_unset() -> None:
    """Blank shelf_id + empty directory_ids -> INVALID_ARGUMENT."""
    service, stub = _make_capturing_service()
    context = _FakeContext()

    request = PostNoteRequest(
        title="hello",
        content=None,
        author_id="user-1",
        user_id="user-1",
        shelf_id="   ",
    )
    await service.PostNote(request, cast(ServicerContext, context))

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert stub.last_note is None


# ---------------------------------------------------------------------------
# PatchNote: oneof/optional fields flow through to the service layer.
# ---------------------------------------------------------------------------


class _UpdateNoteStub(NoteServiceABC):
    """`NoteServiceABC` stub that records the entity handed to `update_note`."""

    def __init__(self) -> None:
        self.last_note = None
        self.last_user_ctx = None

    async def update_note(self, note, user_ctx):
        self.last_note = note
        self.last_user_ctx = user_ctx
        return note

    async def insert_note(self, note, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def delete_note(self, note_id, user_ctx):  # pragma: no cover
        raise NotImplementedError

    async def get_note(self, note_id, user_ctx, *, include=None):  # pragma: no cover
        raise NotImplementedError

    async def search_notes(  # pragma: no cover
        self, search_type, query, user_ctx, limit, offset,
    ):
        raise NotImplementedError

    async def get_notes(self, note_ids, user_ctx, options=None):  # pragma: no cover
        raise NotImplementedError


def _make_update_service() -> tuple[GrpcNoteService, _UpdateNoteStub]:
    stub = _UpdateNoteStub()
    service = GrpcNoteService(
        note_service=stub,
        log=_log_provider,
        to_grpc=_to_grpc(),
        context_factory=_UserContextFactory(),
    )
    return service, stub


async def test_patch_note_directory_ids_change_forwards_to_service() -> None:
    """A set `directory_ids_change` flows verbatim into `update_note`."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(
        id="note-1",
        author_id="user-1",
        directory_ids=IdsOrUndefined(ids=["dir-a", "dir-b"]),
    )
    proto = await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert list(stub.last_note.directory_ids) == ["dir-a", "dir-b"]
    # the other ids-shaped field stays UNDEFINED because not set
    assert stub.last_note.tag_ids is UNDEFINED


async def test_patch_note_tag_ids_change_forwards_to_service() -> None:
    """A set `tag_ids_change` flows verbatim into `update_note`."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(
        id="note-1",
        author_id="user-1",
        tag_ids=IdsOrUndefined(ids=["tag-a", "tag-b"]),
    )
    await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert list(stub.last_note.tag_ids) == ["tag-a", "tag-b"]
    assert stub.last_note.directory_ids is UNDEFINED


async def test_patch_note_empty_ids_change_replaces_with_empty_list() -> None:
    """An explicit empty list replaces the field with [] (not UNDEFINED)."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(
        id="note-1",
        author_id="user-1",
        directory_ids=IdsOrUndefined(ids=[]),
    )
    await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert list(stub.last_note.directory_ids) == []
    assert stub.last_note.directory_ids is not UNDEFINED


async def test_patch_note_omitted_ids_change_leaves_field_undefined() -> None:
    """Without the oneof arm, the entity field stays UNDEFINED."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(id="note-1", author_id="user-1")
    await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert stub.last_note.directory_ids is UNDEFINED
    assert stub.last_note.tag_ids is UNDEFINED


async def test_patch_note_optional_title_forwards_to_service() -> None:
    """A set `title` flows into `update_note`; unset stays UNDEFINED."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(
        id="note-1",
        author_id="user-1",
        title="new title",
    )
    await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert stub.last_note.title == "new title"
    assert stub.last_note.content is UNDEFINED


async def test_patch_note_optional_content_forwards_to_service() -> None:
    """A set `content` flows into `update_note`; unset stays UNDEFINED."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(
        id="note-1",
        author_id="user-1",
        content="new body",
    )
    await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code is None
    assert stub.last_note is not None
    assert stub.last_note.content == "new body"
    assert stub.last_note.title is UNDEFINED


async def test_patch_note_missing_author_id_returns_invalid_argument() -> None:
    """`author_id` is required; absent -> INVALID_ARGUMENT, no update."""
    service, stub = _make_update_service()
    context = _FakeContext()

    request = AlterNoteRequest(id="note-1", user_id="user-1")
    proto = await service.PatchNote(request, cast(ServicerContext, context))

    assert context.code == grpc.StatusCode.INVALID_ARGUMENT
    assert "author_id is required" in (context.details or "")
    assert stub.last_note is None
    assert proto.id == ""