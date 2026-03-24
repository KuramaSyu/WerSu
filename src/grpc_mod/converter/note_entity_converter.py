from typing import Any, Dict
from google.protobuf.timestamp_pb2 import Timestamp

from src.api.undefined import UNDEFINED
from src.db.entities.note.metadata import NoteEntity
from src.db.repos.note.note import SearchType
from src.grpc_mod.proto.note_pb2 import GetSearchNotesRequest, MinimalNote, Note, NoteEmbedding, NotePermission
from src.utils import asdict
from src.utils.dict_helper import drop_except_keys, drop_undefined



def to_grpc_note(note_entity: NoteEntity | None) -> Note:
    """Converts a NoteEntity to a gRPC Note message."""

    if note_entity is None:
        return Note()
    
    assert note_entity.note_id is not None
    assert note_entity.title is not None
    assert note_entity.content is not None
    assert note_entity.author_id is not None

    updated_at_ts = Timestamp()
    if note_entity.updated_at:
        updated_at_ts.FromDatetime(note_entity.updated_at)

    basic_args = drop_undefined(
        drop_except_keys(
            asdict(note_entity), 
            {"note_id", "title", "content", "author_id"}
        )
    )
    basic_args["id"] = basic_args.pop("note_id")

    # convert permissions
    assert isinstance(note_entity.permissions, list)
    perms: list[NotePermission] = []
    for p in note_entity.permissions:
        perms.append(
            NotePermission(
                relation=str(p.relation),
                subject={
                    "object_type": str(p.subject.object_type),
                    "object_id": str(p.subject.object_id),
                },
            )
        )

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
            {"note_id", "title", "content", "author_id", "updated_at"}
        )
    )
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

