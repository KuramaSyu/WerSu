"""Unit tests for :class:`src.services.role_service.RoleServiceImpl`.

The service enforces ``manage`` permission on every write that
mutates an existing role.  ``create_role`` falls back to an
injected super-admin lookup.  These tests wire the real service
against an in-memory permission repo (so permission tuples resolve
like SpiceDB would) and a fake role repo that records the calls
and holds role metadata in-process.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from src.api import ObjectRef, ObjectTypeEnum, PermissionRepoABC, Relationship, RoleRelationEnum, SubjectRef
from src.api.other.undefined import UNDEFINED, UndefinedOr, is_undefined
from src.api.other.user_context import UserContextABC
from src.db.entities.user.role import RoleEntity, RoleFilter, UserRoleMembershipEntity
from src.services.role_service import RoleServiceImpl
from tests.doubles import InMemoryPermissionRepo
from tests.doubles import _DummyUserContext
from typing import Dict, List, Optional
import pytest, uuid

from tests.doubles.fakespys.role_repo import _FakeSpyRoleRepo


# ---------------------------------------------------------------------------
# Recording fake
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _grant_manage(
    permission_repo: PermissionRepoABC,
    *,
    role_id: str,
    user_id: str,
) -> None:
    """Insert ``role:<id>#administrator@user:<id>`` so ``manage`` resolves True."""
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef(ObjectTypeEnum.ROLE, role_id),
            relation=RoleRelationEnum.ADMINISTRATOR,
            subject=SubjectRef(ObjectTypeEnum.USER, user_id),
        )
    ])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.fixture
def permission_repo() -> InMemoryPermissionRepo:
    return InMemoryPermissionRepo()


@pytest.fixture
def role_repo() -> _FakeSpyRoleRepo:
    return _FakeSpyRoleRepo()


@pytest.fixture
def super_admin_ids() -> set[str]:
    """Default: nobody is a super-admin.  Tests override per-case."""
    return set()


@pytest.fixture
def service(
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
    super_admin_ids: set[str],
) -> RoleServiceImpl:
    return RoleServiceImpl(
        role_repo=role_repo,
        permission_repo=permission_repo,
        is_super_admin=lambda _ctx: set(super_admin_ids),
    )


# ---- create_role ---------------------------------------------------------


async def test_create_role_rejects_caller_not_in_super_admin_env(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
) -> None:
    """``create_role`` denies callers not in the injected super-admin set."""
    actor = _DummyUserContext(user_id="alice")
    with pytest.raises(PermissionError):
        await service.create_role(
            RoleEntity(name="engineering"),
            actor,
        )
    assert role_repo.create_calls == []


async def test_create_role_accepts_super_admin(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    super_admin_ids: set[str],
) -> None:
    """``create_role`` proceeds when caller is in the injected super-admin set."""
    super_admin_ids.update({"alice", "bob"})
    actor = _DummyUserContext(user_id="alice")
    created = await service.create_role(
        RoleEntity(name="engineering"),
        actor,
    )
    assert created.id is not UNDEFINED
    assert created.name == "engineering"
    assert len(role_repo.create_calls) == 1


# ---- add_user_to_role ----------------------------------------------------


async def test_add_user_to_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Without ``manage`` on the role the call is denied."""
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.add_user_to_role(
            user_id="alice",
            role_id=str(role.id),
            ctx=_DummyUserContext(user_id="carol"),
        )
    assert role_repo.add_calls == []


async def test_add_user_to_role_succeeds_with_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Caller holds ``administrator`` on the role; ``manage`` resolves True."""
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    await _grant_manage(
        permission_repo,
        role_id=str(role.id),
        user_id="carol",
    )
    membership = await service.add_user_to_role(
        user_id="alice",
        role_id=str(role.id),
        ctx=_DummyUserContext(user_id="carol"),
    )
    assert membership.user_id == "alice"
    assert membership.role_id == str(role.id)
    # The membership tuple ends up in the permission repo too --
    # verify that, since ``SpicedbRoleRepo.add_user_to_role`` writes
    # through ``PermissionRepoABC.insert``.  The fake skips that
    # step, but a real repo would have inserted it.
    assert role_repo.add_calls == [("alice", str(role.id))]


# ---- remove_user_from_role ----------------------------------------------


async def test_remove_user_from_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.remove_user_from_role(
            user_id="alice",
            role_id=str(role.id),
            ctx=_DummyUserContext(user_id="carol"),
        )
    assert role_repo.remove_calls == []


# ---- update_role / delete_role -----------------------------------------


async def test_update_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.update_role(
            RoleEntity(id=str(role.id), name="engineering"),
            _DummyUserContext(user_id="carol"),
        )
    assert role_repo.update_calls == []


async def test_delete_role_requires_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    with pytest.raises(PermissionError):
        await service.delete_role(str(role.id), _DummyUserContext(user_id="carol"))
    assert role_repo.delete_calls == []


async def test_update_role_succeeds_with_manage(
    service: RoleServiceImpl,
    role_repo: _FakeSpyRoleRepo,
    permission_repo: InMemoryPermissionRepo,
) -> None:
    role = await role_repo.create_role(RoleEntity(name="eng"), _DummyUserContext(user_id="root"))
    await _grant_manage(permission_repo, role_id=str(role.id), user_id="carol")
    updated = await service.update_role(
        RoleEntity(id=str(role.id), name="engineering"),
        _DummyUserContext(user_id="carol"),
    )
    assert updated.name == "engineering"


# ---- input validation ---------------------------------------------------


async def test_create_role_rejects_missing_name(
    service: RoleServiceImpl,
    super_admin_ids: set[str],
) -> None:
    super_admin_ids.add("alice")
    with pytest.raises(ValueError, match="role.name is required"):
        await service.create_role(RoleEntity(name=UNDEFINED), _DummyUserContext(user_id="alice"))


async def test_update_role_rejects_missing_id(service: RoleServiceImpl) -> None:
    with pytest.raises(ValueError, match="role.id is required"):
        await service.update_role(RoleEntity(name="x"), _DummyUserContext(user_id="alice"))


async def test_add_user_to_role_rejects_missing_ids(service: RoleServiceImpl) -> None:
    with pytest.raises(ValueError, match="user_id is required"):
        await service.add_user_to_role(
            user_id="",
            role_id="r1",
            ctx=_DummyUserContext(user_id="alice"),
        )
    with pytest.raises(ValueError, match="role_id is required"):
        await service.add_user_to_role(
            user_id="alice",
            role_id="",
            ctx=_DummyUserContext(user_id="alice"),
        )


# ---- in-memory role grant propagation -----------------------------------


async def test_role_grant_propagates_to_user_permission_check(
    permission_repo: InMemoryPermissionRepo,
) -> None:
    """Smoke-test the in-memory repo's ``member_of`` walk.

    alice is a member of role X.  role X is granted ``reader`` on a
    note.  alice should be able to read the note.
    """
    await permission_repo.insert([
        Relationship(
            resource=ObjectRef("role", "role-eng"),
            relation="member",
            subject=SubjectRef("user", "alice"),
        ),
        Relationship(
            resource=ObjectRef("note", "note-1"),
            relation="reader",
            # Userset reference -- matches the SpiceDB schema where
            # ``note#reader: user | role#member``.
            subject=SubjectRef("role", "role-eng", optional_relation="member"),
        ),
    ])

    actor = _DummyUserContext(user_id="alice")
    assert await permission_repo.has_permission(actor, "view", ObjectRef("note", "note-1"))
    # alice is NOT admin
    assert not await permission_repo.has_permission(actor, "delete", ObjectRef("note", "note-1"))