"""gRPC adapter for note sharing operations.

This module is intentionally thin: it converts protobuf payloads into the
sharing service entities, delegates all permission/business logic to the
service layer, and converts the result back to protobuf messages via the
injected :class:`ConvertToGrpcVisitor`.
"""

from __future__ import annotations

import traceback
from typing import AsyncIterator

import grpc
from google.protobuf.empty_pb2 import Empty
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.services.sharing import ShareAccessServiceABC, SharingServiceABC as SharingServiceABC
from src.api.other.user_context import ContextFactory, UserContextABC
from src.db.entities.note.sharing import FilterShareNote
from src.db.repos.user import UnimplementedUserContext
from src.grpc_mod.converter.from_proto import (
    grpc_note_share_to_domain,
    grpc_request_to_note_share_entity,
    to_filter_share_note_entity,
)
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.sharing_pb2 import (
    AccessShareRequest,
    AccessShareResponse,
    CreateShareRequest,
    DeleteSharesRequest,
    GetShareUserRequest,
    GetShareUserResponse,
    GetSharesByIdRequest,
    GetSharesRequest,
    NoteShare,
    UpdateShareRequest,
)
from src.grpc_mod.proto.sharing_pb2_grpc import SharingServiceServicer
from src.grpc_mod._log_decorator import log_service_call


class GrpcSharingService(SharingServiceServicer):
    """gRPC adapter for the sharing service."""

    def __init__(
        self,
        sharing_service: SharingServiceABC,
        share_access_service: ShareAccessServiceABC,
        log: LoggingProvider,
        to_grpc: ConvertToGrpcVisitor,
        context_factory: ContextFactory[UserContextABC],
    ) -> None:
        self._sharing_service = sharing_service
        self._share_access_service = share_access_service
        self.log = log(__name__, self)
        self._to_grpc = to_grpc
        self._context = context_factory

    @log_service_call()
    async def AccessShare(self, request: AccessShareRequest, context: ServicerContext) -> AccessShareResponse:
        """Access a share by its ID and return the associated note."""
        try:
            note_share = await self._share_access_service.access_share(
                request.share_id,
                ctx=UnimplementedUserContext()
                )
            return AccessShareResponse(share=note_share.convert(self._to_grpc))
        except Exception as exc:
            self._handle_empty_exception(exc, context)
            return AccessShareResponse()

    @log_service_call()
    async def GetShareUser(
        self,
        request: GetShareUserRequest,
        context: ServicerContext,
    ) -> GetShareUserResponse:
        """Return the temporary user id (and online-until) behind a share."""
        try:
            access_as, online_until = await self._share_access_service.get_share_user(
                request.share_id
            )
            return self._to_grpc.visit_share_user(access_as, online_until)
        except Exception as exc:
            return self._handle_share_user_exception(exc, context)

    @log_service_call()
    async def CreateShare(self, request: CreateShareRequest, context: ServicerContext) -> NoteShare:
        """Create one share using the requester user_id for permission checks."""
        try:
            self._require_user_id(request.user_id)

            created = await self._sharing_service.create_share(
                grpc_request_to_note_share_entity(request),
                await self._context.create(request.user_id),
            )
            return created.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_unary_exception(exc, context)

    @log_service_call()
    async def UpdateShare(self, request: UpdateShareRequest, context: ServicerContext) -> NoteShare:
        """Update one share using the requester user_id for permission checks."""
        try:
            self._require_user_id(request.user_id)
            if not request.HasField("share"):
                raise ValueError("share is required")

            updated = await self._sharing_service.update_share(
                grpc_note_share_to_domain(request.share),
                await self._context.create(request.user_id),
            )
            return updated.convert(self._to_grpc)
        except Exception as exc:
            return self._handle_unary_exception(exc, context)

    @log_service_call()
    async def GetSharesById(
        self,
        request: AccessShareRequest,
        context: ServicerContext,
    ) -> NoteShare:
        """Return the note behind a share for the given share id."""
        try:
            note = await self._sharing_service.access_share(
                request.share_id,
                await self._context.create(request.user_id)
            )
            return note.convert(self._to_grpc)
        except Exception as exc:
            self._handle_unary_exception(exc, context)
            return NoteShare()

    @log_service_call()
    async def GetShares(
        self,
        request: GetSharesRequest,
        context: ServicerContext,
    ) -> AsyncIterator[NoteShare]:
        """Stream shares matching the provided filter."""
        try:
            self._require_user_id(request.user_id)
            filter_entity = (
                to_filter_share_note_entity(request.filter)
                if request.HasField("filter")
                else FilterShareNote()
            )
            shares = await self._sharing_service.get_shares(
                filter_entity,
                await self._context.create(request.user_id),
            )
            for share in shares:
                yield share.convert(self._to_grpc)
        except Exception as exc:
            self._handle_stream_exception(exc, context)
            return

    @log_service_call()
    async def DeleteShares(self, request: DeleteSharesRequest, context: ServicerContext) -> Empty:
        """Delete shares selected by exact IDs."""
        try:
            self._require_user_id(request.user_id)
            await self._sharing_service.delete_shares(
                list(request.share_ids),
                await self._context.create(request.user_id),
            )
            return Empty()
        except Exception as exc:
            self._handle_empty_exception(exc, context)
            return Empty()

    @staticmethod
    def _require_user_id(user_id: str) -> None:
        """Validate that the requester is present for permission checks."""
        if not user_id:
            raise ValueError("user_id is required")

    def _handle_unary_exception(self, exc: Exception, context: ServicerContext) -> NoteShare:
        """Map service exceptions to gRPC status codes for NoteShare responses."""
        self._set_context_error(exc, context)
        return NoteShare()

    def _handle_share_user_exception(
        self,
        exc: Exception,
        context: ServicerContext,
    ) -> GetShareUserResponse:
        """Map service exceptions to gRPC status codes for GetShareUserResponse."""
        self._set_context_error(exc, context)
        return GetShareUserResponse()

    def _handle_empty_exception(self, exc: Exception, context: ServicerContext) -> None:
        """Map service exceptions to gRPC status codes for Empty responses."""
        self._set_context_error(exc, context)

    def _handle_stream_exception(self, exc: Exception, context: ServicerContext) -> None:
        """Map service exceptions to gRPC status codes for streaming responses."""
        self._set_context_error(exc, context)

    def _set_context_error(self, exc: Exception, context: ServicerContext) -> None:
        """Apply the appropriate gRPC status and log unexpected errors."""
        if isinstance(exc, PermissionError):
            context.set_code(grpc.StatusCode.PERMISSION_DENIED)
            context.set_details(str(exc))
            return

        if isinstance(exc, LookupError):
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(str(exc))
            return

        if isinstance(exc, ValueError):
            code = (
                grpc.StatusCode.NOT_FOUND
                if "not found" in str(exc).lower()
                else grpc.StatusCode.INVALID_ARGUMENT
            )
            context.set_code(code)
            context.set_details(str(exc))
            return

        self.log.error(f"Error handling sharing request: {traceback.format_exc()}")
        context.set_code(grpc.StatusCode.INTERNAL)
        context.set_details("Internal server error while handling sharing request")