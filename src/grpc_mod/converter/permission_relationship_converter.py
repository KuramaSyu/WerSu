"""Converters between gRPC permission messages and domain relationship models.

This module centralizes translation and validation logic for permission
relationship payloads used by ``GrpcPermissionService``.
"""

from typing import cast

from src.api import (
    ObjectRef,
    ObjectTypeEnum,
    RelationEnum,
    Relationship,
    SubjectRef,
)
from src.grpc_mod.proto.note_pb2 import (
    PermissionObjectType,
    PermissionRelationship,
    PermissionResource,
)


def to_object_ref(object_type: int, object_id: str) -> ObjectRef:
    """Convert a proto object type/id pair to ``ObjectRef``.

    Parameters
    ----------
    object_type : int
        ``PermissionObjectType`` enum value.
    object_id : str
        Resource identifier.

    Returns
    -------
    ObjectRef
        Normalized object reference.

    Raises
    ------
    ValueError
        If input is missing or unsupported.
    """
    if not object_id:
        raise ValueError("object_id is required")

    return ObjectRef(object_type=to_object_type(object_type), object_id=object_id)


def to_object_type(object_type: int) -> ObjectTypeEnum:
    """Convert proto ``PermissionObjectType`` to domain ``ObjectTypeEnum``."""
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE:
        return ObjectTypeEnum.NOTE
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_DIRECTORY:
        return ObjectTypeEnum.DIRECTORY
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_USER:
        return ObjectTypeEnum.USER
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_ATTACHMENT:
        return ObjectTypeEnum.ATTACHMENT
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE:
        return ObjectTypeEnum.ROLE
    if object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF:
        return ObjectTypeEnum.SHELF

    raise ValueError("Unsupported object_type")


def to_relationship(fallback_resource: ObjectRef, relationship: PermissionRelationship) -> Relationship:
    """Convert proto relationship payload to domain ``Relationship``.

    The relationship-level ``resource`` field is optional for compatibility.
    If provided, it must match the fallback request resource.
    """
    if not relationship.relation:
        raise ValueError("relationship.relation is required")
    if relationship.subject.object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_UNSPECIFIED:
        raise ValueError("relationship.subject.object_type is required")
    if not relationship.subject.object_id:
        raise ValueError("relationship.subject.object_id is required")

    relationship_resource = _to_relationship_resource(relationship)
    resource = relationship_resource or fallback_resource

    if relationship_resource is not None:
        if (
            str(relationship_resource.object_type) != str(fallback_resource.object_type)
            or str(relationship_resource.object_id) != str(fallback_resource.object_id)
        ):
            raise ValueError("relationship.resource must match object_type/object_id request fields")

    subject_type = to_object_type(relationship.subject.object_type)
    optional_relation = relationship.subject.optional_relation

    # Role subjects are usersets: ``role:<id>#member`` means "the
    # members of this role", not the role object itself.  This is the
    # optional relation used by role-member lookups/grants.
    if subject_type == ObjectTypeEnum.ROLE:
        normalized_optional = optional_relation or "member"
        if normalized_optional != "member":
            raise ValueError(
                "role subjects only support optional_relation='member'"
            )
    elif optional_relation:
        raise ValueError(
            "optional_relation is only valid for role subjects"
        )
    else:
        normalized_optional = None

    return Relationship(
        resource=resource,
        relation=cast(RelationEnum, relationship.relation),
        subject=SubjectRef(
            object_type=subject_type,
            object_id=relationship.subject.object_id,
            optional_relation=normalized_optional,
        ),
    )


def to_permission_object_type(object_type: ObjectTypeEnum) -> PermissionObjectType.ValueType:
    """Convert domain object type to proto ``PermissionObjectType``."""
    if object_type == ObjectTypeEnum.NOTE:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE
    if object_type == ObjectTypeEnum.DIRECTORY:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_DIRECTORY
    if object_type == ObjectTypeEnum.USER:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_USER
    if object_type == ObjectTypeEnum.ATTACHMENT:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_ATTACHMENT
    if object_type == ObjectTypeEnum.ROLE:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE
    if object_type == ObjectTypeEnum.SHELF:
        return PermissionObjectType.PERMISSION_OBJECT_TYPE_SHELF
    return PermissionObjectType.PERMISSION_OBJECT_TYPE_UNSPECIFIED


def to_permission_resource(resource: ObjectRef) -> PermissionResource:
    """Convert domain ``ObjectRef`` to proto ``PermissionResource``."""
    return PermissionResource(
        object_type=to_permission_object_type(resource.object_type),
        object_id=str(resource.object_id),
    )


def _to_relationship_resource(relationship: PermissionRelationship) -> ObjectRef | None:
    if relationship.resource.object_type == PermissionObjectType.PERMISSION_OBJECT_TYPE_UNSPECIFIED:
        return None
    if not relationship.resource.object_id:
        raise ValueError("relationship.resource.object_id is required when relationship.resource.object_type is set")
    return to_object_ref(relationship.resource.object_type, relationship.resource.object_id)
