"""Policy/factory tests for the resource-sharing refactor (Stage 1).

These tests pin the *mapping* layer: which relation a caller must
write, and which ``can_*`` permission the factory asks SpiceDB about.
The schema remains the source of truth; the in-memory repo only
simulates the capability implications the matrix needs.

Small examples::

    factory = GrantPermissionFactory()

    # Writing note#reader requires can_grant_read:
    factory.for_relation(ObjectTypeEnum.NOTE, "note-1", "reader")

    # Writing shelf#delegate_read requires can_assign_delegate_read:
    factory.for_relation(ObjectTypeEnum.SHELF, "shelf-1", "delegate_read")

    # Sharing shelf-1 as writer requires can_grant_write:
    factory.for_access_level(ObjectTypeEnum.SHELF, "shelf-1", ShareLevel.WRITE)

    # Legacy / internal relations raise instead of silently weakening:
    factory.for_relation(ObjectTypeEnum.NOTE, "note-1", "owner")  # ValueError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    SubjectRef,
)
from src.domain.share_policy import (
    GrantPermissionFactory,
    ShareLevel,
)
from tests.doubles import InMemoryPermissionRepo, _DummyUserContext


# Note/directory/shelf share the same access + delegation rules.
_ACCESS_RESOURCES = [ObjectTypeEnum.NOTE, ObjectTypeEnum.DIRECTORY, ObjectTypeEnum.SHELF]

# Rows are the relation the actor holds; the set lists the relations
# that actor may write.  Mirrors docs/permissions.md.
#
# Example: an admin may add readers/writers and delegate those grants,
# but cannot add another admin -- only an owner (or grant_admin /
# delegate_admin) can.
_ACCESS_MATRIX = {
    "reader": set(),
    "writer": set(),
    "admin": {
        "reader", "writer", "grant_read", "grant_write",
        "delegate_read", "delegate_write",
    },
    "owner": {
        "reader", "writer", "admin",
        "grant_read", "grant_write", "grant_admin",
        "delegate_read", "delegate_write", "delegate_admin",
    },
    "grant_read": {"reader"},
    "grant_write": {"writer"},
    "grant_admin": {"admin"},
    "delegate_read": {"reader", "grant_read"},
    "delegate_write": {"writer", "grant_write"},
    "delegate_admin": {"admin", "grant_admin"},
}

# Role membership has its own two-level capability chain.
_ROLE_MATRIX = {
    "admin": {"grant_member", "delegate_member"},
    "grant_member": set(),
    "delegate_member": {"grant_member"},
}


async def _insert_actor_relation(
    repo: InMemoryPermissionRepo,
    resource: ObjectRef,
    relation: str,
    user_id: str,
) -> None:
    """Give ``user_id`` exactly ``relation`` on ``resource``."""
    await repo.insert([
        Relationship(
            resource=resource,
            relation=relation,  # type: ignore[arg-type]
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


@pytest.mark.parametrize("resource_type", _ACCESS_RESOURCES)
@pytest.mark.parametrize("actor_relation", list(_ACCESS_MATRIX))
@pytest.mark.parametrize("target_relation", list(_ACCESS_MATRIX["owner"]))
async def test_access_policy_matrix(
    resource_type: ObjectTypeEnum,
    actor_relation: str,
    target_relation: str,
) -> None:
    repo = InMemoryPermissionRepo()
    actor = _DummyUserContext("actor")
    resource = ObjectRef(resource_type, "resource-1")
    await _insert_actor_relation(repo, resource, actor_relation, actor.user_id)

    check = GrantPermissionFactory().for_relation(
        resource_type, str(resource.object_id), target_relation
    ).set_permission_repo(repo)
    result = await check.check(actor)

    # Cross-stage 1+2: the factory element + in-memory capability map
    # must agree with the documented matrix for every combination.
    expected = target_relation in _ACCESS_MATRIX[actor_relation]
    actual = bool(result)
    assert actual is expected, (
        f"{resource_type} actor={actor_relation} -> {target_relation}: "
        f"expected {expected}, got {actual}"
    )


@pytest.mark.parametrize("actor_relation", list(_ROLE_MATRIX))
@pytest.mark.parametrize("target_relation", ["grant_member", "delegate_member"])
async def test_role_policy_matrix(
    actor_relation: str,
    target_relation: str,
) -> None:
    repo = InMemoryPermissionRepo()
    actor = _DummyUserContext("actor")
    resource = ObjectRef(ObjectTypeEnum.ROLE, "role-1")
    await _insert_actor_relation(repo, resource, actor_relation, actor.user_id)

    check = GrantPermissionFactory().for_relation(
        ObjectTypeEnum.ROLE, "role-1", target_relation
    ).set_permission_repo(repo)
    result = await check.check(actor)

    # Role matrix: admin can delegate; delegate_member can
    # appoint granters; grant_member can only add members.
    expected = target_relation in _ROLE_MATRIX[actor_relation]
    actual = bool(result)
    assert actual is expected, (
        f"role actor={actor_relation} -> {target_relation}: "
        f"expected {expected}, got {actual}"
    )


def test_factory_for_access_level_maps_read_write_admin() -> None:
    """Level -> relation -> capability, without any caller context."""
    factory = GrantPermissionFactory()
    actual = factory.for_access_level(
        ObjectTypeEnum.NOTE, "n1", ShareLevel.READ
    )._capability  # noqa: SLF001
    assert actual == "can_grant_read", (
        f"note level=read: expected can_grant_read, got {actual}"
    )

    actual = factory.for_access_level(
        ObjectTypeEnum.DIRECTORY, "d1", ShareLevel.WRITE
    )._capability  # noqa: SLF001
    assert actual == "can_grant_write", (
        f"directory level=write: expected can_grant_write, got {actual}"
    )

    actual = factory.for_access_level(
        ObjectTypeEnum.SHELF, "s1", ShareLevel.ADMIN
    )._capability  # noqa: SLF001
    assert actual == "can_grant_admin", (
        f"shelf level=admin: expected can_grant_admin, got {actual}"
    )


@pytest.mark.parametrize(
    "resource_type, relation",
    [
        (ObjectTypeEnum.NOTE, "owner"),
        (ObjectTypeEnum.NOTE, "parent_directory"),
        (ObjectTypeEnum.DIRECTORY, "parent"),
        (ObjectTypeEnum.SHELF, "member"),
        (ObjectTypeEnum.USER, "reader"),
        (ObjectTypeEnum.ROLE, "member"),
        (ObjectTypeEnum.ROLE, "reader"),
    ],
)
def test_factory_rejects_unsupported_relations(
    resource_type: ObjectTypeEnum,
    relation: str,
) -> None:
    """Owner, structural relations and unsupported resource/relation pairs fail loudly."""
    with pytest.raises(ValueError):
        GrantPermissionFactory().for_relation(
            resource_type, "id-1", relation
        )


def test_factory_rejects_owner_level() -> None:
    """Owner is a mapping-only level; it is never granted through the API."""
    with pytest.raises(ValueError):
        GrantPermissionFactory().for_access_level(
            ObjectTypeEnum.NOTE, "n1", ShareLevel.OWNER
        )


def test_factory_rejects_unknown_resource() -> None:
    """Attachments are not shareable via PermissionService."""
    with pytest.raises(ValueError):
        GrantPermissionFactory().for_relation(
            ObjectTypeEnum.ATTACHMENT, "a1", "reader"
        )


def test_schema_contains_every_factory_capability() -> None:
    """Cross-stage 1+3: every capability name the factory uses exists in schema."""
    schema = Path("src/db/migrations/schema.zed").read_text()
    assert "edit_permissions" not in schema, (
        "schema must not contain removed legacy permission edit_permissions"
    )
    assert "permission manage" not in schema, (
        "schema must not contain removed legacy permission manage"
    )

    for resource_type in (
        ObjectTypeEnum.NOTE,
        ObjectTypeEnum.DIRECTORY,
        ObjectTypeEnum.SHELF,
        ObjectTypeEnum.ROLE,
    ):
        policy = GrantPermissionFactory().policy_for(resource_type)
        for capability in policy.capability_by_relation.values():
            assert f"permission {capability}" in schema, (
                f"{resource_type}: schema is missing permission {capability}"
            )


def test_removed_enum_members_are_gone() -> None:
    """The legacy computed permissions are deleted, not aliased."""
    from src.api.other.relationship import (
        DirectoryRelationEnum,
        NoteRelationEnum,
        RoleRelationEnum,
        ShelfRelationEnum,
    )

    for enum in (
        NoteRelationEnum,
        DirectoryRelationEnum,
        ShelfRelationEnum,
        RoleRelationEnum,
    ):
        assert not hasattr(enum, "EDIT_PERMISSIONS"), (
            f"{enum.__name__}: EDIT_PERMISSIONS must be removed"
        )
        assert not hasattr(enum, "MANAGE"), (
            f"{enum.__name__}: MANAGE must be removed"
        )


def test_role_resource_has_no_access_levels() -> None:
    """Role membership is not exposed as a read/write/admin share level."""
    policy = GrantPermissionFactory().policy_for(ObjectTypeEnum.ROLE)
    assert policy.supported_levels == frozenset(), (
        f"role supported_levels: expected no levels, got {policy.supported_levels}"
    )
    assert policy.relation_by_level == {}, (
        f"role relation_by_level: expected empty, got {policy.relation_by_level}"
    )