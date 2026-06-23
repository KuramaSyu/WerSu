from datetime import datetime
from sqlite3.dbapi2 import Timestamp

from src.api.undefined import UNDEFINED, UndefinedNoneOr, UndefinedOr, unwrap_undefined
from src.db.entities.note.sharing import FilterShareNote, NoteShareEntity
from src.grpc_mod.proto.sharing_pb2 import AccessShareResponse, CreateShareRequest, NoteShare, NullableString, NullableTimestamp, ShareFilter

def to_proto_note_share(share: NoteShareEntity) -> AccessShareResponse:
    """Convert a domain NoteShareEntity into a protobuf AccessShareResponse."""
    return AccessShareResponse(
        share=to_grpc_note_share(share)
    )

def request_to_note_share_entity(request: CreateShareRequest) -> NoteShareEntity:
    """Convert a CreateShareRequest into a NoteShareEntity for service layer consumption."""
    return NoteShareEntity(
        description=from_nullable_string(request, "description"),
        note_id=unwrap_undefined(request.note_id),
        online_since=from_nullable_timestamp(request, "online_since"),
        online_until=from_nullable_timestamp(request, "online_until"),
        permission=unwrap_undefined(request.permission),
    )

def note_share_to_note_share_entity(share: NoteShare) -> NoteShareEntity:
    """Convert a protobuf NoteShare into a domain NoteShareEntity."""
    return NoteShareEntity(
        id=share.id or UNDEFINED,
        description=from_nullable_string(share, "description"),
        note_id=share.note_id or UNDEFINED,
        created_at=from_timestamp_field(share, "created_at"),
        created_by=share.created_by or UNDEFINED,
        online_since=from_nullable_timestamp(share, "online_since"),
        online_until=from_nullable_timestamp(share, "online_until"),
        access_as=unwrap_undefined(share.access_as),  # this is a backend property only
    )


def to_filter_share_note_entity(filter: ShareFilter) -> FilterShareNote:
    """Convert a protobuf ShareFilter into the domain filter entity."""
    return FilterShareNote(
        note_id=filter.note_id if filter.HasField("note_id") else UNDEFINED,
        created_by=filter.created_by if filter.HasField("created_by") else UNDEFINED,
        online_since=from_nullable_timestamp(filter, "online_since"),
        online_until=from_nullable_timestamp(filter, "online_until"),
        access_as=UNDEFINED,  # currently not used for filtering
    )


def to_grpc_note_share(share: NoteShareEntity | None) -> NoteShare:
    """Convert a domain NoteShareEntity into a protobuf NoteShare."""
    if share is None:
        return NoteShare()

    return NoteShare(
        id=unwrap_undefined(share.id),
        description=to_proto_nullable_string(share.description),
        note_id=unwrap_undefined(share.note_id),
        created_at=to_proto_timestamp(share.created_at),
        created_by=unwrap_undefined(share.created_by),
        online_since=to_proto_nullable_timestamp(share.online_since),
        online_until=to_proto_nullable_timestamp(share.online_until),
        access_as=unwrap_undefined(share.access_as),  # this is a backend property only
    )


def from_nullable_string(message, field_name: str) -> UndefinedNoneOr[str]:
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


def from_nullable_timestamp(message, field_name: str) -> UndefinedNoneOr[datetime]:
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


def from_timestamp_field(message, field_name: str) -> UndefinedOr[datetime]:
    """Read an ordinary timestamp field, returning UNDEFINED when omitted."""
    if not message.HasField(field_name):
        return UNDEFINED
    return getattr(message, field_name).ToDatetime()


def to_proto_nullable_string(value: UndefinedNoneOr[str]) -> NullableString | None:
    """Convert a domain nullable string into its protobuf wrapper."""
    if value is UNDEFINED:
        return None
    if value is None:
        return NullableString(null_value=True)
    return NullableString(value=str(value))


def to_proto_nullable_timestamp(value: UndefinedNoneOr[datetime]) -> NullableTimestamp | None:
    """Convert a domain nullable datetime into its protobuf wrapper."""
    if value is UNDEFINED:
        return None
    if value is None:
        return NullableTimestamp(null_value=True)
    return NullableTimestamp(value=to_proto_timestamp(value))


def to_proto_timestamp(value: UndefinedOr[datetime]) -> Timestamp | None:
    """Convert a domain datetime into protobuf Timestamp."""
    if value is UNDEFINED or not isinstance(value, datetime):
        return None

    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp