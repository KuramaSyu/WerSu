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
from src.db.repos.note.note import UserContext
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

    def __init__(self, sharing_service: SharingServiceABC, share_access_serivce: ShareAccessServiceABC, log: LoggingProvider) -> None:
        self._sharing_service = sharing_service
        self._share_access_service = share_access_serivce
        self.log = log(__name__, self)

    @log_service_call()
    async def AccessShare(self, request: AccessShareRequest, context: ServicerContext) -> AccessShareResponse:
        """Access a share by its ID and return the associated note."""
        try:
            self._require_user_id(request.user_id)
            note = await self._sharing_service.access_share(
                request.share.id,
                share=request_to_note_share_entity(request),
                )
            return to_grpc_note(note)
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
            return _to_grpc_note_share(created)
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
            return _to_grpc_note_share(updated)
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
                _to_filter_share_note_entity(request.filter)
                if request.HasField("filter")
                else FilterShareNote()
            )
            shares = await self._sharing_service.get_shares(
                filter_entity,
                UserContext(request.user_id),
            )
            for share in shares:
                yield _to_grpc_note_share(share)
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


def request_to_note_share_entity(request: CreateShareRequest) -> NoteShareEntity:
    """Convert a CreateShareRequest into a NoteShareEntity for service layer consumption."""
    return NoteShareEntity(
        description=_from_nullable_string(request, "description"),
        note_id=unwrap_undefined(request.note_id),
        online_since=_from_nullable_timestamp(request, "online_since"),
        online_until=_from_nullable_timestamp(request, "online_until"),
        permission=unwrap_undefined(request.permission),
    )

def note_share_to_note_share_entity(share: NoteShare) -> NoteShareEntity:
    """Convert a protobuf NoteShare into a domain NoteShareEntity."""
    return NoteShareEntity(
        id=share.id or UNDEFINED,
        description=_from_nullable_string(share, "description"),
        note_id=share.note_id or UNDEFINED,
        created_at=_from_timestamp_field(share, "created_at"),
        created_by=share.created_by or UNDEFINED,
        online_since=_from_nullable_timestamp(share, "online_since"),
        online_until=_from_nullable_timestamp(share, "online_until"),
        access_as=unwrap_undefined(share.access_as),  # this is a backend property only
    )


def _to_filter_share_note_entity(filter: ShareFilter) -> FilterShareNote:
    """Convert a protobuf ShareFilter into the domain filter entity."""
    return FilterShareNote(
        note_id=filter.note_id if filter.HasField("note_id") else UNDEFINED,
        created_by=filter.created_by if filter.HasField("created_by") else UNDEFINED,
        online_since=_from_nullable_timestamp(filter, "online_since"),
        online_until=_from_nullable_timestamp(filter, "online_until"),
        access_as=UNDEFINED,  # currently not used for filtering
    )


def _to_grpc_note_share(share: NoteShareEntity | None) -> NoteShare:
    """Convert a domain NoteShareEntity into a protobuf NoteShare."""
    if share is None:
        return NoteShare()

    return NoteShare(
        id=unwrap_undefined(share.id),
        description=_to_nullable_string(share.description),
        note_id=unwrap_undefined(share.note_id),
        created_at=_to_timestamp(share.created_at),
        created_by=unwrap_undefined(share.created_by),
        online_since=_to_nullable_timestamp(share.online_since),
        online_until=_to_nullable_timestamp(share.online_until),
        access_as=unwrap_undefined(share.access_as),  # this is a backend property only
    )


def _from_nullable_string(message, field_name: str) -> UndefinedNoneOr[str]:
    """Read a nullable string wrapper, preserving omitted and null states."""
    if not message.HasField(field_name):
        return UNDEFINED

    wrapped: NullableString = getattr(message, field_name)
    kind = wrapped.WhichOneof("kind")
    if kind == "null_value":
        return None
    if kind == "value":
        return wrapped.value
    return UNDEFINED


def _from_nullable_timestamp(message, field_name: str) -> UndefinedNoneOr[datetime]:
    """Read a nullable timestamp wrapper, preserving omitted and null states."""
    if not message.HasField(field_name):
        return UNDEFINED

    wrapped: NullableTimestamp = getattr(message, field_name)
    kind = wrapped.WhichOneof("kind")
    if kind == "null_value":
        return None
    if kind == "value":
        return wrapped.value.ToDatetime()
    return UNDEFINED


def _from_timestamp_field(message, field_name: str) -> UndefinedOr[datetime]:
    """Read an ordinary timestamp field, returning UNDEFINED when omitted."""
    if not message.HasField(field_name):
        return UNDEFINED
    return getattr(message, field_name).ToDatetime()


def _to_nullable_string(value: UndefinedNoneOr[str]) -> NullableString | None:
    """Convert a domain nullable string into its protobuf wrapper."""
    if value is UNDEFINED:
        return None
    if value is None:
        return NullableString(null_value=True)
    return NullableString(value=str(value))


def _to_nullable_timestamp(value: UndefinedNoneOr[datetime]) -> NullableTimestamp | None:
    """Convert a domain nullable datetime into its protobuf wrapper."""
    if value is UNDEFINED:
        return None
    if value is None:
        return NullableTimestamp(null_value=True)
    return NullableTimestamp(value=_to_timestamp(value))


def _to_timestamp(value: UndefinedOr[datetime]) -> Timestamp | None:
    """Convert a domain datetime into protobuf Timestamp."""
    if value is UNDEFINED or not isinstance(value, datetime):
        return None

    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp
