"""gRPC adapter for :class:`src.api.NoteServiceABC`.

Implements the gRPC contract defined in ``grpc/proto/note.proto`` for
note CRUD plus the search endpoint.  Thin layer: translates proto
requests into :class:`~src.db.entities.NoteEntity` arguments, delegates
business/permission logic to the injected
:class:`~src.api.NoteServiceABC`, and converts results back to proto
messages via the injected :class:`ConvertToGrpcVisitor`.
"""

from __future__ import annotations

import logging
import traceback
from datetime import datetime
from pprint import pformat
from typing import AsyncIterator

import asyncpg
import grpc
from grpc.aio import ServicerContext

from src.api import LoggingProvider, NoteServiceABC
from src.api.undefined import UNDEFINED
from src.api.user_context import ContextFactory, UserContextABC
from src.db.entities import NoteEntity
from src.grpc_mod._log_decorator import log_service_call
from src.grpc_mod.converter.from_proto import to_search_type
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.note_pb2 import (
    AlterNoteRequest,
    DeleteNoteRequest,
    GetNoteRequest,
    GetSearchNotesRequest,
    MinimalNote,
    Note,
    NoteResponse,
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
        self._svc_logger = logging.getLogger("src.services")

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
            user_ctx = await self._context.create(request.author_id)
            note_entity = await self._note_service.update_note(
                NoteEntity(
                    note_id=request.id,
                    author_id=request.author_id,
                    content=request.content,
                    embeddings=UNDEFINED,
                    permissions=UNDEFINED,
                    title=request.title,
                    updated_at=datetime.now(),
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

    @log_service_call()
    async def SearchNotes(
        self, request: GetSearchNotesRequest, context: ServicerContext
    ) -> AsyncIterator[MinimalNote]:
        user_ctx = await self._context.create(request.user_id)
        notes = await self._note_service.search_notes(
            to_search_type(request.search_type).name,
            request.query,
            user_ctx,
            limit=request.limit,
            offset=request.offset,
        )
        for note in notes:
            grpc_note = self._to_grpc.visit_note_minimal(note)
            self.log.debug(f"[SearchNotes] yielding note: {pformat(grpc_note)}")
            yield grpc_note