"""Stage 4/5/6/8 service tests for resource sharing."""

from __future__ import annotations

from typing import Optional

import pytest

from src.api.other.relationship import (
    DirectoryRelationEnum,
    NoteRelationEnum,
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    RoleRelationEnum,
    ShelfRelationEnum,
    SubjectRef,
)
from src.api.other.user_context import UserContextABC
from src.services.permissions import PermissionServiceImpl
from src.domain.share_policy import GrantPermissionFactory
from tests.doubles import (
    FakePermissionAuditRepo,
    InMemoryPermissionRepo,
    _DummyUserContext,
)
from tests.doubles.stubs.permission_service import (
    _StubPermissionDirectoryRepo,
    _StubPermissionNoteRepo,
)
from tests.doubles.stubs.permission_targets import (
    _StubPermissionRoleRepo,
    _StubPermissionShelfRepo,
)


def _build_service(
    *,
    permission_repo: Optional[InMemoryPermissionRepo] = None,
    note_ids: set[str] | None = None,
    directory_ids: set[str] | None = None,
    shelf_ids: set[str] | None = None,
    role_ids: set[str] | None = None,
    audit_repo: Optional[FakePermissionAuditRepo] = None,
) -> PermissionServiceImpl:
    return PermissionServiceImpl(
        permission_repo=permission_repo or InMemoryPermissionRepo(),
        note_repo=_StubPermissionNoteRepo(note_ids or set()),
        directory_repo=_StubPermissionDirectoryRepo(directory_ids or set()),
        shelf_repo=_StubPermissionShelfRepo(shelf_ids or set()),
        role_repo=_StubPermissionRoleRepo(role_ids or set()),
        grant_factory=GrantPermissionFactory(),
        audit_repo=audit_repo,
    )


async def _grant(
    repo: InMemoryPermissionRepo,
    resource: ObjectRef,
    relation: str,
    user_id: str = "owner",
) -> None:
    await repo.insert([
        Relationship(
            resource=resource,
            relation=relation,  # type: ignore[arg-type]
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


async def test_share_note_with_role_userset() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, note_ids={"note-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    created = await service.create_relationship(
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.ROLE, "role-1"),
        ),
        actor,
    )

    role_rels = [
        rel for rel in created
        if str(rel.subject.object_type) == "role"
    ]
    assert len(role_rels) == 1
    assert role_rels[0].subject.optional_relation == "member"


async def test_role_subject_rejects_non_member_optional_relation() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, note_ids={"note-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    with pytest.raises(ValueError, match="member"):
        await service.create_relationship(
            Relationship(
                resource=resource,
                relation=NoteRelationEnum.READER,
                subject=SubjectRef(
                    ObjectTypeEnum.ROLE,
                    "role-1",
                    optional_relation="admin",
                ),
            ),
            actor,
        )


async def test_share_shelf_with_user() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, shelf_ids={"shelf-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.SHELF, "shelf-1")
    await _grant(repo, resource, "owner")

    created = await service.create_relationship(
        Relationship(
            resource=resource,
            relation=ShelfRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        actor,
    )
    assert any(
        str(rel.relation) == "reader"
        and str(rel.subject.object_id) == "alice"
        for rel in created
    )


async def test_list_role_relationships_uses_has_admin() -> None:
    """Role listing is gated on the new role ``has_admin`` permission."""
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, role_ids={"role-1"})
    admin = _DummyUserContext("role-admin")
    resource = ObjectRef(ObjectTypeEnum.ROLE, "role-1")
    await _grant(repo, resource, RoleRelationEnum.ADMIN, admin.user_id)

    relationships = await service.list_relationships(resource, admin)
    assert any(
        str(rel.relation) == "admin"
        and str(rel.subject.object_id) == admin.user_id
        for rel in relationships
    )

    with pytest.raises(PermissionError):
        await service.list_relationships(resource, _DummyUserContext("outsider"))


async def test_role_membership_capability_can_be_delegated() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, role_ids={"role-1"})
    admin = _DummyUserContext("admin")
    resource = ObjectRef(ObjectTypeEnum.ROLE, "role-1")
    await _grant(repo, resource, "admin", admin.user_id)

    created = await service.create_relationship(
        Relationship(
            resource=resource,
            relation=RoleRelationEnum.GRANT_MEMBER,
            subject=SubjectRef(ObjectTypeEnum.USER, "bob"),
        ),
        admin,
    )
    assert any(
        str(rel.relation) == "grant_member"
        and str(rel.subject.object_id) == "bob"
        for rel in created
    )

    # A grant_member holder cannot mint another grant_member.
    with pytest.raises(PermissionError):
        await service.create_relationship(
            Relationship(
                resource=resource,
                relation=RoleRelationEnum.GRANT_MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, "carol"),
            ),
            _DummyUserContext("bob"),
        )


async def test_owner_is_not_writable() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, note_ids={"note-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    with pytest.raises(ValueError, match="owner"):
        await service.create_relationship(
            Relationship(
                resource=resource,
                relation=NoteRelationEnum.OWNER,
                subject=SubjectRef(ObjectTypeEnum.USER, "mallory"),
            ),
            actor,
        )


async def test_directory_parent_shelf_is_structural() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, directory_ids={"dir-1"})
    actor = _DummyUserContext("owner")
    await _grant(repo, ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"), "owner")

    with pytest.raises(ValueError, match="patch"):
        await service.create_relationship(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.DIRECTORY, "dir-1"),
                relation=DirectoryRelationEnum.PARENT,
                subject=SubjectRef(ObjectTypeEnum.SHELF, "shelf-1"),
            ),
            actor,
        )


async def test_replace_preserves_owner_and_never_removes_it() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, note_ids={"note-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await repo.insert([
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.OWNER,
            subject=SubjectRef(ObjectTypeEnum.USER, actor.user_id),
        ),
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
    ])

    updated = await service.replace_relationships(
        resource,
        [
            Relationship(
                resource=resource,
                relation=NoteRelationEnum.WRITER,
                subject=SubjectRef(ObjectTypeEnum.USER, "bob"),
            )
        ],
        actor,
    )

    by_relation = {str(rel.relation) for rel in updated}
    assert "owner" in by_relation
    assert "writer" in by_relation
    assert "reader" not in by_relation


async def test_replace_rejects_desired_owner_tuple() -> None:
    repo = InMemoryPermissionRepo()
    service = _build_service(permission_repo=repo, note_ids={"note-1"})
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    with pytest.raises(ValueError, match="owner"):
        await service.replace_relationships(
            resource,
            [
                Relationship(
                    resource=resource,
                    relation=NoteRelationEnum.OWNER,
                    subject=SubjectRef(ObjectTypeEnum.USER, "mallory"),
                )
            ],
            actor,
        )


async def test_audit_rows_for_create_delete_and_replace() -> None:
    repo = InMemoryPermissionRepo()
    audit = FakePermissionAuditRepo()
    service = _build_service(
        permission_repo=repo, note_ids={"note-1"}, audit_repo=audit
    )
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    await service.create_relationship(
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        actor,
    )
    await service.delete_relationship(
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        actor,
    )
    await service.replace_relationships(
        resource,
        [
            Relationship(
                resource=resource,
                relation=NoteRelationEnum.WRITER,
                subject=SubjectRef(ObjectTypeEnum.ROLE, "role-1"),
            )
        ],
        actor,
    )

    actions = [entry.action for entry in audit.entries]
    assert actions == ["grant", "revoke", "grant"]
    role_entry = audit.entries[-1]
    assert role_entry.subject_type == "role"
    assert role_entry.subject_relation == "member"
    assert role_entry.actor_id == "owner"


async def test_audit_failure_does_not_fail_mutation() -> None:
    repo = InMemoryPermissionRepo()
    audit = FakePermissionAuditRepo(fail=True)
    service = _build_service(
        permission_repo=repo, note_ids={"note-1"}, audit_repo=audit
    )
    actor = _DummyUserContext("owner")
    resource = ObjectRef(ObjectTypeEnum.NOTE, "note-1")
    await _grant(repo, resource, "owner")

    created = await service.create_relationship(
        Relationship(
            resource=resource,
            relation=NoteRelationEnum.READER,
            subject=SubjectRef(ObjectTypeEnum.USER, "alice"),
        ),
        actor,
    )
    assert any(str(rel.subject.object_id) == "alice" for rel in created)
    assert audit.record_calls

async def test_grpc_mutation_writes_audit_row() -> None:
    """Cross-stage 9+4+8: gRPC -> service -> audit."""
    from src.grpc_mod.permission_service import GrpcPermissionService
    from src.grpc_mod.proto.note_pb2 import (
        CreatePermissionRequest,
        PermissionObjectType,
        PermissionRelationship,
        PermissionSubject,
    )
    from tests.doubles import _StubUserContextFactory

    repo = InMemoryPermissionRepo()
    audit = FakePermissionAuditRepo()
    service = _build_service(
        permission_repo=repo, note_ids={"note-1"}, audit_repo=audit
    )
    actor = _DummyUserContext("owner")
    await _grant(repo, ObjectRef(ObjectTypeEnum.NOTE, "note-1"), "owner")

    grpc_service = GrpcPermissionService(
        permission_service=service,
        log=lambda name, instance: None,
        to_grpc=object(),  # type: ignore[arg-type]
        context_factory=_StubUserContextFactory(),
    )
    response = await grpc_service.CreatePermission(
        CreatePermissionRequest(
            object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_NOTE,
            object_id="note-1",
            user_id="owner",
            relationship=PermissionRelationship(
                relation="reader",
                subject=PermissionSubject(
                    object_type=PermissionObjectType.PERMISSION_OBJECT_TYPE_ROLE,
                    object_id="role-1",
                ),
            ),
        ),
        None,
    )

    reader_response = next(
        rel for rel in response.relationships if rel.relation == "reader"
    )
    assert reader_response.subject.optional_relation == "member"
    assert len(audit.entries) == 1
    assert audit.entries[0].action == "grant"
    assert audit.entries[0].subject_relation == "member"
