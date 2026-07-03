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
from src.api.note_service import NoteResponse, NoteServiceABC
from src.api.user_context import UserContextABC
from src.db.entities.note.metadata import NoteEntity
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import GetNoteRequest, NoteResponse as GrpcNoteResponse
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
    ) -> NoteResponse:
        self.last_note_id = note_id
        self.last_user_ctx = user_ctx
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