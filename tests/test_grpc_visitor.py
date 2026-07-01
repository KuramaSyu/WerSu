"""Unit tests for the entity visitor pattern and :class:`ConvertToGrpcVisitor`.

What this file covers:

* :class:`AcceptsVisitor` and :class:`EntityVisitor` enforce the
  abstract contract (cannot be instantiated; subclasses must implement
  the dispatch methods).
* Each supported entity dispatches itself to the matching `visit_*`
  handler via :meth:`AcceptsVisitor.visit`.
* :class:`ConvertToGrpcVisitor` produces the same proto message the
  matching `to_grpc_*` free function would, for every entity type.

Wire shape asserted: every entity's `visit` call returns the exact
proto type the gRPC servicer consumes.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.db.entities.directory.directory import DirectoryEntity
from src.db.entities.note.metadata import NoteEntity
from src.db.entities.note.sharing import NoteShareEntity
from src.db.entities.user.user import UserEntity
from src.api.visitor import AcceptsVisitor, EntityVisitor
from src.db.repos.attachments.attachments import Attachment
from src.grpc_mod.converter.grpc_visitor import ConvertToGrpcVisitor
from src.grpc_mod.proto.attachments_pb2 import Attachment as GrpcAttachment
from src.grpc_mod.proto.note_pb2 import Directory, Note
from src.grpc_mod.proto.sharing_pb2 import NoteShare
from src.grpc_mod.proto.user_pb2 import User
from tests.stubs.visitor import StubVisitor, make_relationship


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_relationship = make_relationship


def _visitor() -> ConvertToGrpcVisitor:
    return ConvertToGrpcVisitor()


def _stub() -> StubVisitor:
    return StubVisitor()


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


def test_accepts_visitor_cannot_be_instantiated_directly() -> None:
    """`AcceptsVisitor` is abstract; calling `visit` raises before dispatch."""
    with pytest.raises(TypeError):
        AcceptsVisitor()  # type: ignore[abstract]


def test_entity_visitor_cannot_be_instantiated_directly() -> None:
    """`EntityVisitor` is abstract; subclasses must implement the `visit_*` methods."""
    with pytest.raises(TypeError):
        EntityVisitor()  # type: ignore[abstract]


def test_partial_visitor_must_implement_all_visit_methods() -> None:
    """A subclass that forgets one `visit_*` method stays abstract."""

    class _HalfVisitor(EntityVisitor):
        def visit_note(self, entity):  # type: ignore[override]
            return None
        def visit_note_minimal(self, entity): ...  # type: ignore[override]
        def visit_directory(self, entity): ...  # type: ignore[override]
        def visit_user(self, entity): ...  # type: ignore[override]
        def visit_note_share(self, entity): ...  # type: ignore[override]
        def visit_attachment(self, entity): ...  # type: ignore[override]
        # visit_attachment_metadata is intentionally omitted

    with pytest.raises(TypeError):
        _HalfVisitor()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Entity dispatch
# ---------------------------------------------------------------------------


def test_note_entity_dispatches_to_visit_note() -> None:
    """`NoteEntity.visit` routes to `visitor.visit_note` with itself."""
    stub = _stub()
    entity = NoteEntity(note_id="n1", title="t", content="c", author_id="a1")

    result = entity.convert(stub)

    assert stub.notes == [entity]
    assert result is entity


def test_directory_entity_dispatches_to_visit_directory() -> None:
    """`DirectoryEntity.visit` routes to `visitor.visit_directory` with itself."""
    stub = _stub()
    entity = DirectoryEntity(id="d1", name="docs")

    result = entity.convert(stub)

    assert stub.directories == [entity]
    assert result is entity


def test_user_entity_dispatches_to_visit_user() -> None:
    """`UserEntity.visit` routes to `visitor.visit_user` with itself."""
    stub = _stub()
    entity = UserEntity(id="u1", discord_id=1, username="alice", email="a@b.c")

    result = entity.convert(stub)

    assert stub.users == [entity]
    assert result is entity


def test_note_share_entity_dispatches_to_visit_note_share() -> None:
    """`NoteShareEntity.visit` routes to `visitor.visit_note_share` with itself."""
    stub = _stub()
    entity = NoteShareEntity(id="s1", note_id="n1", access_as="u1")

    result = entity.convert(stub)

    assert stub.note_shares == [entity]
    assert result is entity


def test_attachment_dispatches_to_visit_attachment() -> None:
    """`Attachment.visit` routes to `visitor.visit_attachment` with itself."""
    stub = _stub()
    entity = Attachment(key="k1", filename="hello.txt", content=b"hi")

    result = entity.convert(stub)

    assert stub.attachments == [entity]
    assert result is entity


def test_convert_is_an_alias_for_visit() -> None:
    """`AcceptsVisitor.convert` is an alias for `AcceptsVisitor.visit`."""
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
        permissions=[],
    )

    assert entity.convert(_visitor()) == entity.visit(_visitor())


# ---------------------------------------------------------------------------
# ConvertToGrpcVisitor -- per-entity
# ---------------------------------------------------------------------------


def test_visit_note_returns_note_with_basic_fields() -> None:
    """`visit_note` maps the entity scalar fields onto the proto."""
    updated_at = datetime(2026, 7, 1, 12, 0, 0)
    entity = NoteEntity(
        note_id="n1",
        title="hello",
        content="world",
        author_id="a1",
        updated_at=updated_at,
        permissions=[],
    )

    proto: Note = entity.convert(_visitor())

    assert isinstance(proto, Note)
    assert proto.id == "n1"
    assert proto.title == "hello"
    assert proto.content == "world"
    assert proto.author_id == "a1"
    assert proto.updated_at.ToDatetime() == updated_at


def test_visit_note_converts_permissions_to_permission_relationships() -> None:
    """`visit_note` translates each `Relationship` into a proto permission."""
    entity = NoteEntity(
        note_id="n1",
        title="t",
        content="c",
        author_id="a1",
        permissions=[_relationship("n1", "alice")],
    )

    proto: Note = entity.convert(_visitor())

    assert len(proto.permissions) == 1
    perm = proto.permissions[0]
    assert perm.relation == "writer"
    assert perm.subject.object_id == "alice"
    assert perm.resource.object_id == "n1"


def test_visit_directory_returns_directory_with_all_fields() -> None:
    """`visit_directory` maps every entity field onto the proto."""
    entity = DirectoryEntity(
        id="d1",
        name="docs",
        display_name="Documents",
        description="shared docs",
        image_url="https://example.com/img.png",
        parent_id="d0",
        relations=[_relationship("d1", "alice")],
    )

    proto: Directory = entity.convert(_visitor())

    assert isinstance(proto, Directory)
    assert proto.id == "d1"
    assert proto.name == "docs"
    assert proto.display_name == "Documents"
    assert proto.description == "shared docs"
    assert proto.image_url == "https://example.com/img.png"
    assert proto.parent_id == "d0"
    assert len(proto.relationships) == 1
    assert proto.relationships[0].relation == "writer"


def test_visit_directory_omits_parent_id_when_undefined() -> None:
    """`visit_directory` skips `parent_id` when the entity leaves it `UNDEFINED`."""
    entity = DirectoryEntity(id="d1", name="docs")

    proto: Directory = entity.convert(_visitor())

    assert proto.id == "d1"
    assert proto.name == "docs"
    assert proto.parent_id == ""


def test_visit_user_returns_user_with_basic_fields() -> None:
    """`visit_user` maps the entity scalar fields onto the proto."""
    entity = UserEntity(
        id="u1",
        discord_id=42,
        avatar="avatar.png",
        username="alice",
        discriminator="0001",
        email="alice@example.com",
    )

    proto: User = entity.convert(_visitor())

    assert isinstance(proto, User)
    assert proto.id == "u1"
    assert proto.discord_id == 42
    assert proto.avatar == "avatar.png"
    assert proto.username == "alice"
    assert proto.discriminator == "0001"
    assert proto.email == "alice@example.com"


def test_visit_user_treats_none_discriminator_as_empty_string() -> None:
    """`visit_user` falls back to `""` when `discriminator` is `None`."""
    entity = UserEntity(
        id="u1",
        discord_id=1,
        avatar="a",
        username="alice",
        discriminator=None,
        email="a@b.c",
    )

    proto: User = entity.convert(_visitor())

    assert proto.discriminator == ""


def test_visit_note_share_returns_note_share_with_basic_fields() -> None:
    """`visit_note_share` maps the entity scalar fields onto the proto."""
    created_at = datetime(2026, 7, 1, 12, 0, 0)
    entity = NoteShareEntity(
        id="s1",
        note_id="n1",
        created_at=created_at,
        created_by="alice",
        access_as="temp-user",
        permission="read",
    )

    proto: NoteShare = entity.convert(_visitor())

    assert isinstance(proto, NoteShare)
    assert proto.id == "s1"
    assert proto.note_id == "n1"
    assert proto.created_at.ToDatetime() == created_at
    assert proto.created_by == "alice"
    assert proto.access_as == "temp-user"
    assert proto.permission == 1  # SHARE_PERMISSION_READ == 1


def test_visit_note_share_translates_permission_literal() -> None:
    """`visit_note_share` maps `"write"` onto `SHARE_PERMISSION_WRITE` (2)."""
    entity = NoteShareEntity(
        id="s1",
        note_id="n1",
        created_by="alice",
        access_as="temp-user",
        permission="write",
    )

    proto: NoteShare = entity.convert(_visitor())

    assert proto.permission == 2  # SHARE_PERMISSION_WRITE


def test_visit_attachment_returns_attachment_with_metadata_and_content() -> None:
    """`visit_attachment` wraps the metadata and forwards the raw content bytes."""
    entity = Attachment(
        key="k1",
        filename="hello.txt",
        filepath="uploads/hello.txt",
        content_type="text/plain",
        size=5,
        content=b"hello",
        checksum="2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
    )

    proto: GrpcAttachment = entity.convert(_visitor())

    assert isinstance(proto, GrpcAttachment)
    assert proto.content == b"hello"
    assert proto.metadata.key == "k1"
    assert proto.metadata.filename == "hello.txt"
    assert proto.metadata.content_type == "text/plain"
    assert proto.metadata.size == 5
    assert proto.metadata.sha256 == entity.checksum


# ---------------------------------------------------------------------------
# End-to-end: visit() goes through the visitor and back out
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("entity", "expected_type"),
    [
        (NoteEntity(note_id="n1", title="t", content="c", author_id="a1", permissions=[]), Note),
        (DirectoryEntity(id="d1", name="docs"), Directory),
        (UserEntity(id="u1", discord_id=1, avatar="a.png", username="alice", email="a@b.c"), User),
        (NoteShareEntity(id="s1", note_id="n1", created_by="alice", access_as="temp", permission="read"), NoteShare),
        (Attachment(key="k1", content=b"x", size=1, filepath="uploads/k1", filename="x.bin", content_type="application/octet-stream"), GrpcAttachment),
    ],
    ids=["note", "directory", "user", "note_share", "attachment"],
)
def test_entity_visit_returns_expected_proto_type(entity, expected_type) -> None:
    """Each entity's `visit` call lands on the matching proto type."""
    result = entity.convert(_visitor())
    assert isinstance(result, expected_type)


@pytest.mark.parametrize(
    ("entity", "expected_type"),
    [
        (NoteEntity(note_id="n1", title="t", content="c", author_id="a1", permissions=[]), Note),
        (DirectoryEntity(id="d1", name="docs"), Directory),
        (UserEntity(id="u1", discord_id=1, avatar="a.png", username="alice", email="a@b.c"), User),
        (NoteShareEntity(id="s1", note_id="n1", created_by="alice", access_as="temp", permission="read"), NoteShare),
        (Attachment(key="k1", content=b"x", size=1, filepath="uploads/k1", filename="x.bin", content_type="application/octet-stream"), GrpcAttachment),
    ],
    ids=["note", "directory", "user", "note_share", "attachment"],
)
def test_visitor_is_reusable_across_entities(entity, expected_type) -> None:
    """A single :class:`ConvertToGrpcVisitor` instance handles many entities in sequence."""
    visitor = _visitor()

    first = entity.convert(visitor)
    second = entity.convert(visitor)

    assert isinstance(first, expected_type)
    assert isinstance(second, expected_type)