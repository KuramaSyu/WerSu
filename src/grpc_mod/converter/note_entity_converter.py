from typing import Any, Dict
from google.protobuf.timestamp_pb2 import Timestamp

from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.note import SearchType
from src.db.repos.note.permission import ObjectTypeEnum
from src.grpc_mod.proto.note_pb2 import (
    GetSearchNotesRequest,
    MinimalNote,
    Note,
    NoteEmbedding,
    PermissionObjectType,
    PermissionRelationship,
    PermissionResource,
    PermissionSubject,
)
from src.utils import asdict
from src.utils.dict_helper import drop_except_keys, drop_undefined


def _to_permission_object_type(object_type: str) -> PermissionObjectType.ValueType:
    if object_type == ObjectTypeEnum.NOTE.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE
    if object_type == ObjectTypeEnum.DIRECTORY.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_DIRECTORY
    if object_type == ObjectTypeEnum.USER.value:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_USER
    return PermissionObjectType.PERMISSION_OBJECT_TYPE_UNSPECIFIED



def to_grpc_note(note_entity: NoteEntity | None) -> Note:
    """Converts a NoteEntity to a gRPC Note message."""

    if note_entity is None:
        return Note()
    
    assert note_entity.note_id is not None
    assert note_entity.title is not None
    assert note_entity.content is not None
    assert note_entity.author_id is not None

    # convert updated_at manually since it would be string otherwise
    updated_at_ts = Timestamp()
    if note_entity.updated_at:
        updated_at_ts.FromDatetime(note_entity.updated_at)

    basic_args = drop_undefined(
        drop_except_keys(
            asdict(note_entity), 
            {"note_id", "title", "content", "author_id", "permissions"}
        )
    )
    basic_args["id"] = basic_args.pop("note_id")

    # convert permissions
    assert isinstance(note_entity.permissions, list)
    perms: list[PermissionRelationship] = []
    perms = basic_args.pop("permissions", [])
    converted_perms = []
    for perm in note_entity.permissions:
        converted_perms.append(
            PermissionRelationship(
                relation=str(perm.relation),
                subject=PermissionSubject(
                    object_type=_to_permission_object_type(str(perm.subject.object_type)),
                    object_id=str(perm.subject.object_id),
                ),
                resource=PermissionResource(
                    object_type=_to_permission_object_type(str(perm.resource.object_type)),  # type: ignore
                    object_id=str(perm.resource.object_id),  # type: ignore
                ),
            )
        )
    basic_args["permissions"] = converted_perms

    return Note(
        **basic_args,
        updated_at=updated_at_ts,
        # embeddings disabled and reserved in proto file
        permissions=perms,
    )

def to_grpc_minimal_note(note_entity: NoteEntity) -> MinimalNote:
    """Converts a NoteEntity to a gRPC MinimalNote message."""

    assert note_entity.note_id is not None
    assert note_entity.title is not None
    assert note_entity.content is not None
    assert note_entity.author_id is not None

    basic_args = drop_undefined(
        drop_except_keys(
            asdict(note_entity), 
            {"note_id", "title", "content", "author_id", "updated_at", "permissions"}
        )
    )

    perms = basic_args.pop("permissions", [])
    converted_perms = []
    for perm in perms:
        converted_perms.append(
            PermissionRelationship(
                relation=str(perm.relation),
                subject=PermissionSubject(
                    object_type=_to_permission_object_type(str(perm.subject.object_type)),
                    object_id=str(perm.subject.object_id),
                ),
                resource=PermissionResource(
                    object_type=_to_permission_object_type(str(perm.resource.object_type)),
                    object_id=str(perm.resource.object_id),
                ),
            )
        )
    basic_args["permissions"] = converted_perms
    basic_args["id"] = basic_args.pop("note_id")
    basic_args["stripped_content"] = basic_args.pop("content")

    return MinimalNote(**basic_args)


def to_search_type(proto_value: GetSearchNotesRequest.SearchType.ValueType) ->  SearchType:
    if proto_value == GetSearchNotesRequest.SearchType.Undefined:
        return SearchType.CONTEXT
    elif proto_value == GetSearchNotesRequest.SearchType.NoSearch:
        return SearchType.NO_SEARCH
    elif proto_value == GetSearchNotesRequest.SearchType.FullTextTitle:
        return SearchType.FULL_TEXT_TITLE
    elif proto_value == GetSearchNotesRequest.SearchType.Fuzzy:
        return SearchType.FUZZY
    elif proto_value == GetSearchNotesRequest.SearchType.Context:
        return SearchType.CONTEXT
    else:
        raise ValueError(f"Unknown SearchType value: {proto_value}")

