"""gRPC adapter for note sharing operations.

This module is intentionally thin: it converts protobuf payloads into the
sharing service entities, delegates all permission/business logic to the
service layer, and converts the result back to protobuf messages.
"""

from datetime import datetime
from typing import AsyncIterator
import traceback

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.timestamp_pb2 import Timestamp
from grpc.aio import ServicerContext

from src.api import LoggingProvider
from src.api.sharing import ShareAccessServiceABC, SharingServiceABC as SharingServiceABC
from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr, unwrap_undefined, unwrap_undefined_or
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.repos.note.note import UnimplementedUserContext, UserContext
from src.grpc_mod.converter.share_converters import note_share_to_note_share_entity, to_filter_share_note_entity, to_grpc_note_share, to_proto_note_share
from src.grpc_mod.proto.note_pb2 import Note
from src.grpc_mod.proto.sharing_pb2 import (
    AccessShareRequest,
    AccessShareResponse,
    CreateShareRequest,
    DeleteSharesRequest,
    GetSharesByIdRequest,
    GetSharesRequest,
    NoteShare,
    NullableString,
    NullableTimestamp,
    ShareFilter,
    UpdateShareRequest,
)
from src.grpc_mod.proto.sharing_pb2_grpc import SharingServiceServicer
from src.grpc_mod.service import log_service_call

from src.grpc_mod.converter import (
    to_grpc_attachment,
    to_grpc_attachment_metadata,
    to_grpc_directory,
    to_grpc_note,
    to_grpc_user,
    to_object_ref,
    to_permission_object_type,
    to_permission_resource,
    to_relationship,
)


class GrpcSharingService(SharingServiceServicer):
    """gRPC adapter for the sharing service."""

    def __init__(self, sharing_service: SharingServiceABC, share_access_service: ShareAccessServiceABC, log: LoggingProvider) -> None:
        self._sharing_service = sharing_service
        self._share_access_service = share_access_service
        self.log = log(__name__, self)

    @log_service_call()
    async def AccessShare(self, request: AccessShareRequest, context: ServicerContext) -> AccessShareResponse:
        """Access a share by its ID and return the associated note."""
        try:
            note_share = await self._share_access_service.access_share(
                request.share_id,
                ctx=UnimplementedUserContext()
                )
            return to_proto_note_share(note_share)
        except Exception as exc:
            self._handle_empty_exception(exc, context)
            return Note()

    @log_service_call()
    async def CreateShare(self, request: CreateShareRequest, context: ServicerContext) -> NoteShare:
        """Create one share using the requester user_id for permission checks."""
        try:
            self._require_user_id(request.user_id)
            if not request.HasField("share"):
                raise ValueError("share is required")

            created = await self._sharing_service.create_share(
                note_share_to_note_share_entity(request.share),
                UserContext(request.user_id),
            )
            return to_grpc_note_share(created)
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
                note_share_to_note_share_entity(request.share),
                UserContext(request.user_id),
            )
            return to_grpc_note_share(updated)
        except Exception as exc:
            return self._handle_unary_exception(exc, context)

    @log_service_call()
    async def GetSharesById(
        self,
        request: AccessShareRequest,
        context: ServicerContext,
    ) -> AsyncIterator[NoteShare]:
        """Stream shares selected by exact IDs."""
        try:
            note = await self._sharing_service.access_share(
                request.share_id,
                UserContext(request.user_id)
            )
            return to_grpc_note(note)
        except Exception as exc:
            self._handle_stream_exception(exc, context)
            return

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
                UserContext(request.user_id),
            )
            for share in shares:
                yield to_grpc_note_share(share)
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
                UserContext(request.user_id),
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



