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
            response = await self._note_service.get_note(request.id, user_ctx)
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
                ),
                user_context,
            )
            return note_entity.convert(self._to_grpc)
        except asyncpg.UniqueViolationError as e:
            context.set_code(grpc.StatusCode.ALREADY_EXISTS)
            context.set_details(f"Insertion error: {e}")
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
            author_id = self._unwrap_optional(request, "author_id")
            if author_id is UNDEFINED or not author_id:
                context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
                context.set_details("author_id is required")
                return Note()

            user_ctx = await self._context.create(author_id)
            directory_ids = self._unwrap_oneof(
                request, "directory_ids_change"
            )
            tag_ids = self._unwrap_oneof(
                request, "tag_ids_change"
            )
            title = self._unwrap_optional(request, "title")
            content = self._unwrap_optional(request, "content")
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
        except Exception:
            self.log.error(f"Error updating note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while updating note")
            return Note()

    @log_service_call()
    async def DeleteNote(self, request: DeleteNoteRequest, context: ServicerContext) -> Note:
        try:
            user_ctx = await self._context.create(request.author_id)
            deleted_note = await self._note_service.delete_note(
                request.id,
                user_ctx,
            )

            if deleted_note is None:
                context.set_code(grpc.StatusCode.NOT_FOUND)
                context.set_details(f"Note not found where user with id {request.author_id} has permissions")
                return Note()
            return deleted_note.convert(self._to_grpc)
        except Exception:
            self.log.error(f"Error deleting note: {traceback.format_exc()}")
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("Internal server error while deleting note")
            return Note()

    @staticmethod
    def _unwrap_oneof(
        request: AlterNoteRequest,
        oneof_name: str,
    ) -> object:
        """Translate a `IdsOrUndefined` `oneof` arm value or UNDEFINED"""
        which = request.WhichOneof(oneof_name)
        if which is None:
            return UNDEFINED
        return list(getattr(request, which).ids)

    @staticmethod
    def _unwrap_optional(
        request: AlterNoteRequest,
        oneof_name: str,
    ) -> object:
        """Translate a proto3 `optional` scalar into the API sentinel.

        Args:
            request: the incoming :class:`AlterNoteRequest`.
            oneof_name: name of the implicit oneof backing the
                ``optional`` field on ``request``.

        Returns:
            ``UNDEFINED`` when the caller did not set the field, the
            field's value otherwise (including the empty string when
            the caller explicitly cleared it).
        """
        if request.HasField(oneof_name) == False:
            return UNDEFINED
        return getattr(request, oneof_name)

    @log_service_call()
    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ):
        user_ctx = await self._context.create(request.user_id)
        notes = await self._note_service.search_notes(
            to_search_type(request.search_type).name,
            request.query,
            user_ctx,
            limit=request.limit,
            offset=request.offset,
        )
        return self._to_grpc.visit_notes_reply(notes)