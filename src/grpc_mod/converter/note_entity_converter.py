from google.protobuf.timestamp_pb2 import Timestamp

from db.entities.note.metadata import NoteEntity
from grpc_mod.proto.note_pb2 import Note, NoteEmbedding, NotePermission


def to_grpc_note(note_entity: NoteEntity) -> Note:
    """Converts a NoteEntity to a gRPC Note message."""

    assert note_entity.note_id is not None
    assert note_entity.title is not None
    assert note_entity.content is not None
    assert note_entity.author_id is not None

    updated_at_ts = Timestamp()
    if note_entity.updated_at:
        updated_at_ts.FromDatetime(note_entity.updated_at)

    return Note(
        id=note_entity.note_id,
        title=note_entity.title,
        content=note_entity.content,
        author_id=note_entity.author_id,
        updated_at=updated_at_ts,
        embeddings=[
            NoteEmbedding(model=e.model, embedding=e.embedding)
            for e in note_entity.embeddings
        ],
        permissions=[
            NotePermission(role_id=p.role_id) for p in note_entity.permissions
        ],
    )


