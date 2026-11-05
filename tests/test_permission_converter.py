"""Stage 9 converter tests for shelf/role and userset qualifiers."""

from __future__ import annotations

import pytest

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.grpc_mod.converter.permission_relationship_converter import (
    to_object_ref,
    to_object_type,
    to_permission_object_type,
    to_relationship,
)
from src.grpc_mod.proto.note_pb2 import (
    PermissionObjectType,
    PermissionRelationship,
    PermissionResource,
    PermissionSubject,
)


def _proto_relationship(
    *,
    relation: str,
    subject_type: int,
    subject_id: str,
    optional_relation: str = "",
) -> PermissionRelationship:
    return PermissionRelationship(
        relation=relation,
        subject=PermissionSubject(
            object_type=subject_type,
            object_id=subject_id,
            optional_relation=optional_relation,
        ),
    )


def test_to_object_type_maps_shelf_and_role() -> None:
    assert to_object_type(
        PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF
    ) == ObjectTypeEnum.SHELF
    assert to_object_type(
        PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE
    ) == ObjectTypeEnum.ROLE


def test_to_object_ref_accepts_shelf() -> None:
    ref = to_object_ref(
        PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF, "shelf-1"
    )
    assert ref.object_type == ObjectTypeEnum.SHELF
    assert ref.object_id == "shelf-1"


def test_to_relationship_defaults_role_userset_to_member() -> None:
    fallback = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    rel = to_relationship(
        fallback,
        _proto_relationship(
            relation="reader",
            subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE,
            subject_id="role-1",
        ),
    )
    assert rel.subject.optional_relation == "member"


def test_to_relationship_accepts_member_role_userset() -> None:
    fallback = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    rel = to_relationship(
        fallback,
        _proto_relationship(
            relation="reader",
            subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE,
            subject_id="role-1",
            optional_relation="member",
        ),
    )
    assert rel.subject.optional_relation == "member"


def test_to_relationship_rejects_non_member_role_userset() -> None:
    fallback = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    with pytest.raises(ValueError, match="member"):
        to_relationship(
            fallback,
            _proto_relationship(
                relation="reader",
                subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE,
                subject_id="role-1",
                optional_relation="admin",
            ),
        )


def test_to_relationship_rejects_user_userset() -> None:
    fallback = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    with pytest.raises(ValueError, match="role subjects"):
        to_relationship(
            fallback,
            _proto_relationship(
                relation="reader",
                subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_USER,
                subject_id="alice",
                optional_relation="member",
            ),
        )


def test_to_relationship_rejects_relationship_resource_mismatch() -> None:
    fallback = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    with pytest.raises(ValueError, match="match"):
        to_relationship(
            fallback,
            PermissionRelationship(
                relation="reader",
                subject=PermissionSubject(
                    object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_USER,
                    object_id="alice",
                ),
                resource=PermissionResource(
                    object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF,
                    object_id="shelf-1",
                ),
            ),
        )


def test_to_permission_object_type_maps_shelf_and_role() -> None:
    assert to_permission_object_type(
        ObjectTypeEnum.SHELF
    ) == PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF
    assert to_permission_object_type(
        ObjectTypeEnum.ROLE
    ) == PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE


def test_response_preserves_optional_relation() -> None:
    from src.grpc_mod.permission_service import GrpcPermissionService

    service = GrpcPermissionService(
        permission_service=object(),  # type: ignore[arg-type]
        log=lambda name, instance: None,
        to_grpc=object(),  # type: ignore[arg-type]
        context_factory=object(),  # type: ignore[arg-type]
    )
    response = service._to_permissions_response(  # noqa: SLF001
        ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
        [
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.NOTE, "note-1"),
                relation="reader",
                subject=SubjectRef(
                    ObjectTypeEnum.ROLE, "role-1", optional_relation="member"
                ),
            )
        ],
    )
    assert response.relationships[0].subject.optional_relation == "member"

class _ContextFactory:
    async def create(self, user_id: str):
        from tests.doubles import _DummyUserContext

        return _DummyUserContext(user_id)


async def test_grpc_create_delete_shelf_role_roundtrip() -> None:
    """Stage 9+4: gRPC payload for shelf/role reaches the service intact."""
    from src.grpc_mod.permission_service import GrpcPermissionService
    from src.grpc_mod.proto.note_pb2 import (
        CreatePermissionRequest,
        DeletePermissionRequest,
    )

    from tests.doubles import _FakeSpyPermissionService

    fake = _FakeSpyPermissionService()
    grpc_service = GrpcPermissionService(
        permission_service=fake,
        log=lambda name, instance: None,
        to_grpc=object(),
        context_factory=_ContextFactory(),
    )

    request = CreatePermissionRequest(
        object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF,
        object_id="shelf-1",
        relationship=_proto_relationship(
            relation="reader",
            subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE,
            subject_id="role-1",
            optional_relation="member",
        ),
        user_id="actor",
    )
    response = await grpc_service.CreatePermission(request, None)

    relationship, actor = fake.create_calls[-1]
    assert relationship.resource.object_type == ObjectTypeEnum.SHELF
    assert relationship.subject.optional_relation == "member"
    assert actor.user_id == "actor"
    assert response.relationships[0].subject.optional_relation == "member"

    delete_request = DeletePermissionRequest(
        object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE,
        object_id="note-1",
        relationship=_proto_relationship(
            relation="reader",
            subject_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_USER,
            subject_id="alice",
        ),
        user_id="actor",
    )
    await grpc_service.DeletePermission(delete_request, None)
    relationship, _ = fake.delete_calls[-1]
    assert relationship.subject.optional_relation is None
