"""gRPC adapter for :class:`src.api.NoteServiceABC`.

Implements the gRPC contract defined in ``grpc/proto/note.proto`` for
note CRUD plus the search endpoint.  Thin layer: translates proto
requests into :class:`~src.db.entities.NoteEntity` arguments, delegates
business/permission logic to the injected
:class:`~src.api.NoteServiceABC`, and converts results back to proto
messages via the injected :class:`ConvertToGrpcVisitor`.
"""

from __future__ import annotations

import traceback
from datetime import datetime

import asyncpg
import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider, NoteServiceABC
from src.api.other.undefined import UNDEFINED
from src.api.other.user_context import ContextFactory, UserContextABC
from src.api.search_filter import NoteSearchFilter, validate_search_filter
from src.db.entities import NoteEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.from_proto import to_search_type
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import (
    AlterNoteRequest,
    DeleteNoteRequest,
    GetNoteRequest,
    GetSearchNotesRequest,
    Note,
    NoteResponse,
    NotesReply,
    PostNoteRequest,
)
from src.grpc_mod.proto.note_pb2_grpc import NoteServiceServicer
from src.utils.grpc_type_helper import grpc_unwrap_oneof, grpc_unwrap_optional


class GrpcNoteService(NoteServiceServicer):
    """
    Implements the gRPC service defined in grpc/proto/note.proto
    """

    def __init__(
        self,
        note_service: NoteServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ):
        self._note_service = note_service
        self.log = log(__name__, self)

        # visitor pattern -> note entiy calls .visit(visitor)
        # -> visitor calls the correct visit_note() method. you
        # can inject whatever visitor you want
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def GetNote(self, request: GetNoteRequest, context: ServicerContext) -> NoteResponse:
        try:
            user_ctx = await self._context.create(request.user_id)
            response = await self._note_service.get_note(request.id, user_ctx, include={
                "include_attachment_ids": True,
                "include_directory_ids": True,
                "include_permissions": False,
                "include_tag_ids": True,
            })
            self.log.debug(f"Fetched note response: {response}")
            if response.note is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Note not found where user with id {request.user_id} has permissions")
                return NoteResponse()
            return response.convert(self._to_grpc)
        except Exception:
            self.log.error(f"Error fetching note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while fetching note")
            return NoteResponse()

    @log_service_call()
    async def PostNote(self, request: PostNoteRequest, context: ServicerContext) -> Note:
        try:
            shelf_id = (request.shelf_id or "").strip()
            directory_ids: list[str] = [d for d in request.directory_ids if d]
            if not directory_ids and not shelf_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details(
                    "one of directory_ids or shelf_id is required so "
                    "the note can be scoped to a directory"
                )
                return Note()

            # directory_ids is preferred when supplied; shelf_id is
            # only consulted when no explicit directory was given
            # (the facade resolves a directory via the
            # default-fleeting rule in that case).
            entity_directory_ids = (
                directory_ids if directory_ids else UNDEFINED
            )
            entity_shelf_ids = (
                [shelf_id] if not directory_ids and shelf_id else UNDEFINED
            )

            user_context = await self._context.create(request.author_id)
            note_entity = await self._note_service.insert_note(
                NoteEntity(
                    note_id=UNDEFINED,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=[],
                    permissions=UNDEFINED,
                    title=request.title,
                    updated_at=datetime.now(),
                    directory_ids=entity_directory_ids,
                    shelf_ids=entity_shelf_ids,
                ),
                user_context,
            )
            return note_entity.convert(self._to_grpc)
        except asyncpg.UniqueViolationError as e:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details(f"Insertion error: {e}")
            return Note()
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return Note()
        except Exception:
            self.log.error(f"Error creating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while creating note")
            return Note()

    @log_service_call()
    async def PatchNote(self, request: AlterNoteRequest, context: ServicerContext) -> Note:
        try:
            self.log.debug(f"Updating note with request: {request}")
            self.log.debug(
                f"{request.WhichOneof('directory_ids_change')=}, "
                f"{request.WhichOneof('tag_ids_change')=}, "
                f"{request.HasField('title')=}, "
                f"{request.HasField('content')=}, "
                f"{request.HasField('author_id')=}, "
                f"{request.id=}"
            )
            author_id = grpc_unwrap_optional(request, "author_id")
            if author_id is UNDEFINED or not author_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("author_id is required")
                return Note()

            user_ctx = await self._context.create(author_id)
            directory_ids = grpc_unwrap_oneof(
                request, "directory_ids_change"
            )
            tag_ids = grpc_unwrap_oneof(
                request, "tag_ids_change"
            )
            title = grpc_unwrap_optional(request, "title")
            content = grpc_unwrap_optional(request, "content")
            note_entity = await self._note_service.update_note(
                NoteEntity(
                    note_id=request.id,
                    author_id=author_id,
                    content=content,
                    embeddings=UNDEFINED,
                    permissions=UNDEFINED,
                    title=title,
                    updated_at=datetime.now(),
                    directory_ids=directory_ids,
                    tag_ids=tag_ids,
                ),
                user_ctx,
            )
            self.log.debug(f"Updated note entity: {note_entity}")
            return note_entity.convert(self._to_grpc)
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return Note()
        except Exception:
            self.log.error(f"Error updating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while updating note")
            return Note()

    @log_service_call()
    async def DeleteNote(self, request: DeleteNoteRequest, context: ServicerContext) -> Note:
        try:
            user_ctx = await self._context.create(request.user_id)
            deleted_note = await self._note_service.delete_note(
                request.id,
                user_ctx,
            )

            if deleted_note is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Note not found where user with id {request.user_id} has permissions")
                return Note()
            return deleted_note.convert(self._to_grpc)
        except Exception:
            self.log.error(f"Error deleting note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting note")
            return Note()

    @log_service_call()
    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ):
        try:
            user_ctx = await self._context.create(request.user_id)
            filter_ = _search_filter_from_proto(request)
            notes = await self._note_service.search_notes(
                to_search_type(request.search_type).name,
                request.query,
                user_ctx,
                limit=request.limit,
                offset=request.offset,
                filter_=filter_,
            )
            return self._to_grpc.visit_notes_reply(notes)
        except ValueError as e:
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(str(e))
            return NotesReply()
        except Exception:
            self.log.error(
                f"Error searching notes: {traceback.format_exc()}"
            )
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while searching notes")
            return NotesReply()


def _search_filter_from_proto(
    request: GetSearchNotesRequest,
) -> NoteSearchFilter:
    """Translate the proto search filter; raises on inconsistent filter_."""
    filter_proto = request.filter

    date_from = (
        filter_proto.date_from.ToDatetime()
        if filter_proto.HasField("date_from")
        else None
    )
    date_until = (
        filter_proto.date_until.ToDatetime()
        if filter_proto.HasField("date_until")
        else None
    )

    out = NoteSearchFilter(
        include_directory_ids=list(filter_proto.include_directory_ids),
        exclude_directory_ids=list(filter_proto.exclude_directory_ids),
        date_from=date_from,
        date_until=date_until,
        include_shelf_ids=list(filter_proto.include_shelf_ids),
        exclude_shelf_ids=list(filter_proto.exclude_shelf_ids),
        include_tag_ids=list(filter_proto.include_tag_ids),
        exclude_tag_ids=list(filter_proto.exclude_tag_ids),
    )
    validate_search_filter(out)
    return out