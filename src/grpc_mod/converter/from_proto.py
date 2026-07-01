"""Proto-to-entity converters for the sharing service.

These helpers turn inbound gRPC messages into the domain entities that
the sharing service layer expects.  They live next to the visitor
(:mod:`src.grpc_mod.converter.grpc_visitor`) so the gRPC adapters and
the conversion helpers share a single home.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Literal

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr, unwrap_undefined
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.db.repos.note.note import SearchType
from src.grpc_mod.proto.note_pb2 import GetSearchNotesRequest
from src.grpc_mod.proto.sharing_pb2 import (
    CreateShareRequest,
    NoteShare,
    NullableString,
    NullableTimestamp,
    ShareFilter,
)


def grpc_permission_to_domain(
    permission: Any,
) -> UndefinedOr[Literal["read", "write"]]:
    """Convert a gRPC permission into the domain representation.

    Returns :data:`UNDEFINED` for `SHARE_PERMISSION_UNSPECIFIED` so that
    update requests can omit the field to leave the existing permission
    unchanged. Any other unknown enum value is rejected.
    """
    from src.grpc_mod.proto.sharing_pb2 import (
        SHARE_PERMISSION_READ,
        SHARE_PERMISSION_UNSPECIFIED,
        SHARE_PERMISSION_WRITE,
    )

    if permission == SHARE_PERMISSION_UNSPECIFIED:
        return UNDEFINED
    if permission == SHARE_PERMISSION_READ:
        return "read"
    if permission == SHARE_PERMISSION_WRITE:
        return "write"
    raise ValueError(f"Invalid permission for a share: {permission}")


def from_nullable_string(message: Any, field_name: str) -> UndefinedNoneOr[str]:
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


def from_nullable_timestamp(
    message: Any, field_name: str
) -> UndefinedNoneOr[_dt.datetime]:
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


def from_timestamp_field(
    message: Any, field_name: str
) -> UndefinedOr[_dt.datetime]:
    """Read an ordinary timestamp field, returning UNDEFINED when omitted."""
    if not message.HasField(field_name):
        return UNDEFINED
    return getattr(message, field_name).ToDatetime()


def grpc_request_to_note_share_entity(
    request: CreateShareRequest,
) -> NoteShareEntity:
    """Convert a CreateShareRequest into a NoteShareEntity for service layer consumption."""
    return NoteShareEntity(
        description=from_nullable_string(request, "description"),
        note_id=unwrap_undefined(request.note_id),
        online_since=from_nullable_timestamp(request, "online_since"),
        online_until=from_nullable_timestamp(request, "online_until"),
        permission=grpc_permission_to_domain(request.permission),
        created_by=unwrap_undefined(request.user_id),
    )


def grpc_note_share_to_domain(share: NoteShare) -> NoteShareEntity:
    """Convert a protobuf NoteShare into a domain NoteShareEntity."""
    return NoteShareEntity(
        id=share.id or UNDEFINED,
        description=from_nullable_string(share, "description"),
        note_id=share.note_id or UNDEFINED,
        created_at=from_timestamp_field(share, "created_at"),
        created_by=share.created_by or UNDEFINED,
        online_since=from_nullable_timestamp(share, "online_since"),
        online_until=from_nullable_timestamp(share, "online_until"),
        access_as=unwrap_undefined(share.access_as),
        permission=grpc_permission_to_domain(share.permission),
    )


def to_filter_share_note_entity(filter: ShareFilter) -> FilterShareNote:
    """Convert a protobuf ShareFilter into the domain filter entity."""
    return FilterShareNote(
        note_id=filter.note_id if filter.HasField("note_id") else UNDEFINED,
        created_by=filter.created_by if filter.HasField("created_by") else UNDEFINED,
        online_since=from_nullable_timestamp(filter, "online_since"),
        online_until=from_nullable_timestamp(filter, "online_until"),
        access_as=UNDEFINED,
    )


def to_search_type(
    proto_value: "GetSearchNotesRequest.SearchType.ValueType",
) -> SearchType:
    """Translate a proto `SearchType` enum value onto its domain enum.

    `Undefined` and `Context` both fall back to :data:`SearchType.CONTEXT`
    to mirror the legacy converter behaviour.

    Raises:
        ValueError: if the proto value is not a known `SearchType` enum.
    """
    if proto_value == GetSearchNotesRequest.SearchType.NoSearch:
        return SearchType.NO_SEARCH
    if proto_value == GetSearchNotesRequest.SearchType.FullTextTitle:
        return SearchType.FULL_TEXT_TITLE
    if proto_value == GetSearchNotesRequest.SearchType.Fuzzy:
        return SearchType.FUZZY
    if proto_value in (
        GetSearchNotesRequest.SearchType.Undefined,
        GetSearchNotesRequest.SearchType.Context,
    ):
        return SearchType.CONTEXT
    raise ValueError(f"Unknown SearchType value: {proto_value}")