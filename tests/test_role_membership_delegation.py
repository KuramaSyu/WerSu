"""Cross-stage 6+4: delegated role membership capabilities."""

from __future__ import annotations

import pytest

from src.api.other.relationship import (
    ObjectRef,
    ObjectTypeEnum,
    Relationship,
    RoleRelationEnum,
    SubjectRef,
)
from src.db.entities.user.role import RoleEntity
from src.services.permissions import PermissionServiceImpl
from src.services.role_service import RoleServiceImpl
from tests.doubles import (
    InMemoryPermissionRepo,
    _DummyUserContext,
)
from tests.doubles.fakespys.role_repo import _FakeSpyRoleRepo
from tests.doubles.stubs.permission_service import (
    _StubPermissionDirectoryRepo,
    _StubPermissionNoteRepo,
)
from tests.doubles.stubs.permission_targets import _StubPermissionRoleRepo


async def _grant_administrator(
    repo: InMemoryPermissionRepo, role_id: str, user_id: str
) -> None:
    await repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.ADMINISTRATOR,
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


async def test_grant_member_delegation_opens_membership_but_not_meta() -> None:
    repo = InMemoryPermissionRepo()
    role_repo = _FakeSpyRoleRepo()
    role = await role_repo.create_role(
        RoleEntity(name="eng"), _DummyUserContext("root")
    )
    role_id = str(role.id)
    await _grant_administrator(repo, role_id, "admin")

    permission_service = PermissionServiceImpl(
        permission_repo=repo,
        note_repo=_StubPermissionNoteRepo(set()),
        directory_repo=_StubPermissionDirectoryRepo(set()),
        role_repo=_StubPermissionRoleRepo({role_id}),
    )
    role_service = RoleServiceImpl(
        role_repo=role_repo,
        permission_repo=repo,
        is_super_admin=lambda ctx: set(),
    )

    # Administrator grants bob the membership capability.
    await permission_service.create_relationship(
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.GRANT_MEMBER,
            subject=SubjectRef(ObjectTypeEnum.USER, "bob"),
        ),
        _DummyUserContext("admin"),
    )

    # Bob can now add members...
    await role_service.add_user_to_role(
        "carol", role_id, _DummyUserContext("bob")
    )
    assert role_repo.add_calls == [("carol", role_id)]

    # ...but cannot change role metadata.
    with pytest.raises(PermissionError):
        await role_service.update_role(
            RoleEntity(id=role_id, name="renamed"),
            _DummyUserContext("bob"),
        )

    # ...and cannot mint another grant_member.
    with pytest.raises(PermissionError):
        await permission_service.create_relationship(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.GRANT_MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, "dave"),
            ),
            _DummyUserContext("bob"),
        )


async def test_delegate_member_can_mint_grant_member_but_not_delegate() -> None:
    repo = InMemoryPermissionRepo()
    role_repo = _FakeSpyRoleRepo()
    role = await role_repo.create_role(
        RoleEntity(name="ops"), _DummyUserContext("root")
    )
    role_id = str(role.id)
    await _grant_administrator(repo, role_id, "admin")

    permission_service = PermissionServiceImpl(
        permission_repo=repo,
        note_repo=_StubPermissionNoteRepo(set()),
        directory_repo=_StubPermissionDirectoryRepo(set()),
        role_repo=_StubPermissionRoleRepo({role_id}),
    )

    await permission_service.create_relationship(
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.DELEGATE_MEMBER,
            subject=SubjectRef(ObjectTypeEnum.USER, "delegate"),
        ),
        _DummyUserContext("admin"),
    )

    await permission_service.create_relationship(
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.GRANT_MEMBER,
            subject=SubjectRef(ObjectTypeEnum.USER, "granter"),
        ),
        _DummyUserContext("delegate"),
    )

    with pytest.raises(PermissionError):
        await permission_service.create_relationship(
            Relationship(
                resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
                relation=RoleRelationEnum.DELEGATE_MEMBER,
                subject=SubjectRef(ObjectTypeEnum.USER, "other-delegate"),
            ),
            _DummyUserContext("delegate"),
        )